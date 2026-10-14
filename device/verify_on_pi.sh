#!/usr/bin/env bash
# Verify the pinned tiny proof on a Raspberry Pi with EZKL's official aarch64 CLI and time it.
# Usage from the repo root, with the Pi reachable as `ssh p01`:
#   ./device/verify_on_pi.sh            (RUNS=20 by default)
# The remote login shell must be bash: arguments are quoted with printf %q.
set -euo pipefail

HOST="${HOST:-p01}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ART="$ROOT/tests/fixtures/tiny"
SRS="${PGE_SRS:-$ROOT/prover/artifacts/kzg.srs}"
RUNS="${RUNS:-20}"
VERIFY_TIMEOUT="${VERIFY_TIMEOUT:-60}"
REMOTE_BASE="p01-v0"
EZKL_VERSION="23.0.5"
EZKL_TGZ="ezkl-linux-aarch64.tar.gz"
EZKL_TGZ_SHA="384af10d6d6c24379789ba29559c7deacf1b4e287a89ad48e13949939d9dffa6"
PINS="90807800a1c3b248a452e1732c45ee5099f38b737356f5542c0584ec9c3ebb45  kzg.srs
34ab48a8ac0946be4faf10d85699ac729adb20ec4c6251da1d1d0d4ea18a4d26  vk.key
b4a95fc7c572816034708c4e5c079f0f91f82eb9681cfa069565aacc9887aefd  settings.json"

[[ "$RUNS" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "RUNS must be an integer from 1 to 9999"; exit 2; }
[[ "$VERIFY_TIMEOUT" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "VERIFY_TIMEOUT must be 1 to 9999 seconds"; exit 2; }
[[ -f "$SRS" ]] || { echo "missing SRS at $SRS (set PGE_SRS)"; exit 2; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp "$ART"/{proof.json,settings.json,vk.key} "$STAGE"/
cp "$SRS" "$STAGE/kzg.srs"
echo "== pinned inputs (local)"
(cd "$STAGE" && echo "$PINS" | shasum -a 256 -c -)

SSH=(ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 "$HOST")
RUN_DIR="$("${SSH[@]}" "mkdir -p ~/$REMOTE_BASE/cache && mktemp -d ~/$REMOTE_BASE/run.XXXXXX")"
echo "== remote run directory: $RUN_DIR"
scp -q -o ConnectTimeout=10 "$STAGE"/{proof.json,settings.json,vk.key,kzg.srs} "$HOST:$RUN_DIR/"

"${SSH[@]}" "bash -s -- $(printf '%q ' "$RUN_DIR" "$REMOTE_BASE" "$EZKL_VERSION" "$EZKL_TGZ" "$EZKL_TGZ_SHA" "$RUNS" "$VERIFY_TIMEOUT" "$PINS")" <<'REMOTE'
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
REMOTE
