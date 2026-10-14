#!/usr/bin/env bash
# Runs on the device, inside a run directory holding proof.json, settings.json, vk.key and kzg.srs.
# Fetches the pinned EZKL CLI, checks accept and reject cases, then times verification.
# Args: run_dir cache_base ezkl_version archive archive_sha256 runs timeout_s pins
set -euo pipefail
RUN="$1"; BASE="$2"; VER="$3"; TGZ="$4"; SHA="$5"; RUNS="$6"; TMO="$7"; PINS="$8"
cd "$RUN"

echo "== pinned inputs (Pi)"
echo "$PINS" | sha256sum -c -

CACHE="$HOME/$BASE/cache/$TGZ"
if [ ! -f "$CACHE" ]; then
  timeout 300 curl -sSL --fail -o "$CACHE.part" "https://github.com/zkonduit/ezkl/releases/download/v$VER/$TGZ" </dev/null
  mv "$CACHE.part" "$CACHE"
fi
echo "$SHA  $CACHE" | sha256sum -c -
tar -xzf "$CACHE" -C "$RUN" ezkl </dev/null
got="$(./ezkl --version </dev/null | awk '{print $2}')"
[ "$got" = "$VER" ] || { echo "FAIL: ezkl version $got, expected $VER"; exit 1; }

python3 - <<'PY'
import json
p = json.load(open("proof.json")); p["instances"][0][-3] = p["instances"][0][1]
json.dump(p, open("tampered_output.json", "w"))
q = json.load(open("proof.json")); q["instances"][0][0] = q["instances"][0][1]
json.dump(q, open("tampered_input.json", "w"))
PY
printf 'bad\n' > garbage.json

verdict() {
  local out rc
  set +e
  out="$(timeout "$TMO" ./ezkl verify --settings-path settings.json --vk-path vk.key --srs-path kzg.srs --proof-path "$1" </dev/null 2>&1)"
  rc=$?
  set -e
  if [ $rc -eq 0 ]; then echo ACCEPT
  elif [ $rc -eq 1 ] && { [[ "$out" == *"[halo2]"* ]] || [[ "$out" == *"failed to load the proof"* ]]; }; then echo REJECT
  else echo "ERROR(rc=$rc)"
  fi
}

fail=0
check() {
  local got
  got="$(verdict "$3")"
  if [ "$got" = "$2" ]; then echo "PASS  $1: $got"; else echo "FAIL  $1: got $got, want $2"; fail=1; fi
}
check "valid proof" ACCEPT proof.json
check "tampered output" REJECT tampered_output.json
check "tampered input" REJECT tampered_input.json
check "garbage file" REJECT garbage.json
[ $fail -eq 0 ] || exit 1

echo "== timing $RUNS runs of the whole CLI (start-up + load + verify)"
: > timings_ms.txt
i=0
while [ $i -lt "$RUNS" ]; do
  s=$(date +%s%N)
  v="$(verdict proof.json)"
  e=$(date +%s%N)
  [ "$v" = ACCEPT ] || { echo "FAIL: run $i gave $v"; exit 1; }
  echo $(( (e - s) / 1000000 )) >> timings_ms.txt
  i=$((i + 1))
done
n=$(wc -l < timings_ms.txt)
[ "$n" -eq "$RUNS" ] || { echo "FAIL: $n samples, expected $RUNS"; exit 1; }
sort -n timings_ms.txt | awk '{a[NR]=$1} END{print "ms: min", a[1], " median", a[int((NR+1)/2)], " max", a[NR], " (n=" NR ")"}'
echo "machine: $(uname -m) | $(vcgencmd measure_temp 2>/dev/null || true) | $(vcgencmd get_throttled 2>/dev/null || true)"
echo "raw timings: $RUN/timings_ms.txt"
echo "all checks passed"
