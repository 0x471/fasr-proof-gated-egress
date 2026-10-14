#!/usr/bin/env bash
# Runs on the device, inside a run directory holding proof.json, settings.json, vk.key, kzg.srs
# and the pge package (for the proof shape check).
# Fetches the pinned EZKL CLI, checks accept and reject cases, then times verification.
# Args: run_dir cache_base ezkl_version archive archive_sha256 runs timeout_s pins
# Needs GNU coreutils (timeout, sha256sum, date +%N) and python3.
set -euo pipefail
RUN="$1"; BASE="$2"; VER="$3"; TGZ="$4"; SHA="$5"; RUNS="$6"; TMO="$7"; PINS="$8"
KILL_AFTER=5
cd "$RUN"

echo "== pinned inputs (device)"
sha256sum -c - <<<"$PINS"
recheck() {
  sha256sum -c --quiet - <<<"$PINS" >/dev/null 2>&1 || { echo "FAIL: pinned inputs changed during the run"; exit 1; }
}

CACHE="$HOME/$BASE/cache/$TGZ"
if [ ! -f "$CACHE" ]; then
  timeout -k "$KILL_AFTER" 300 curl -sSL --fail -o "$CACHE.part" "https://github.com/zkonduit/ezkl/releases/download/v$VER/$TGZ" </dev/null
  mv "$CACHE.part" "$CACHE"
fi
echo "$SHA  $CACHE" | sha256sum -c -
tar -xzf "$CACHE" -C "$RUN" ezkl </dev/null
got="$(timeout -k "$KILL_AFTER" "$TMO" ./ezkl --version </dev/null | awk '{print $2}')" || { echo "FAIL: ezkl --version failed"; exit 1; }
[ "$got" = "$VER" ] || { echo "FAIL: ezkl version $got, expected $VER"; exit 1; }

python3 - <<'PY'
import json
p = json.load(open("proof.json")); p["instances"][0][-3] = p["instances"][0][1]
json.dump(p, open("tampered_output.json", "w"))
q = json.load(open("proof.json")); q["instances"][0][0] = q["instances"][0][1]
json.dump(q, open("tampered_input.json", "w"))
PY
printf 'bad\n' > garbage.json

# Writes the canonical form of the proof in $1 to candidate.json. Exit 3 means the proof
# is malformed, so it is rejected without running the CLI; any other failure is ERROR.
canonicalize() {
  python3 -I - "$1" <<'PY'
import os
import sys
sys.path.insert(0, os.getcwd())
from pge.binding import BindingError
from pge.verdict import canonical_proof, read_proof
try:
    data = read_proof(sys.argv[1])
except OSError:
    sys.exit(2)
except BindingError:
    sys.exit(3)
try:
    out = canonical_proof(data)
except BindingError:
    sys.exit(3)
with open("candidate.json", "wb") as f:
    f.write(out)
PY
}

# Sets RC and leaves the CLI's output (stdout and stderr) in verify_out.txt.
run_verify() {
  set +e
  timeout -k "$KILL_AFTER" "$TMO" ./ezkl verify --settings-path settings.json --vk-path vk.key \
    --srs-path kzg.srs --proof-path "$1" </dev/null >verify_out.txt 2>&1
  RC=$?
  set -e
}

# The CLI logs "[E] [time, ezkl] - <error>" and, only when verification itself ran,
# "[*] [time, ezkl::execute] - verified: true|false". REJECT needs a [halo2] error after
# "verified: false"; anything else, including load errors and signals, is ERROR.
classify() {
  local out err
  out="$(head -c 65536 verify_out.txt)"
  err="$(sed -n 's/^\[E\] \[[^]]*, ezkl\] - //p' <<<"$out")"
  if [ "$RC" -eq 0 ] && [ -z "$err" ] && grep -qx '\[\*\] \[[^]]*, ezkl::execute\] - verified: true' <<<"$out"; then
    echo ACCEPT
  elif [ "$RC" -eq 1 ] && [ -n "$err" ] && [[ "$err" != *$'\n'* ]] && [[ "$err" == "[halo2] "* ]] &&
      grep -qx '\[\*\] \[[^]]*, ezkl::execute\] - verified: false' <<<"$out"; then
    echo REJECT
  else
    echo "ERROR(rc=$RC)"
  fi
}

fail=0
check() {
  local got c
  recheck
  set +e
  canonicalize "$3"
  c=$?
  set -e
  if [ $c -eq 3 ]; then
    got=REJECT
  elif [ $c -ne 0 ]; then
    got="ERROR(prepare=$c)"
  else
    run_verify candidate.json
    got="$(classify)"
  fi
  if [ "$got" = "$2" ]; then echo "PASS  $1: $got"; else echo "FAIL  $1: got $got, want $2"; fail=1; fi
}
check "valid proof" ACCEPT proof.json
check "tampered output" REJECT tampered_output.json
check "tampered input" REJECT tampered_input.json
check "garbage file" REJECT garbage.json
recheck
[ $fail -eq 0 ] || exit 1

echo "== timing $RUNS runs of the whole CLI (start-up + load + verify)"
canonicalize proof.json || { echo "FAIL: cannot prepare proof.json"; exit 1; }
: > timings_ms.txt
i=0
while [ $i -lt "$RUNS" ]; do
  s=$(date +%s%N)
  run_verify candidate.json
  e=$(date +%s%N)
  v="$(classify)"
  [ "$v" = ACCEPT ] || { echo "FAIL: run $i gave $v"; exit 1; }
  echo $(( (e - s) / 1000000 )) >> timings_ms.txt
  i=$((i + 1))
done
recheck
n=$(wc -l < timings_ms.txt)
[ "$n" -eq "$RUNS" ] || { echo "FAIL: $n samples, expected $RUNS"; exit 1; }
sort -n timings_ms.txt | awk '{a[NR]=$1} END{print "ms: min", a[1], " median", a[int((NR+1)/2)], " max", a[NR], " (n=" NR ")"}'
echo "machine: $(uname -m) | $(vcgencmd measure_temp 2>/dev/null || true) | $(vcgencmd get_throttled 2>/dev/null || true)"
echo "raw timings: $RUN/timings_ms.txt"
echo "all checks passed"
