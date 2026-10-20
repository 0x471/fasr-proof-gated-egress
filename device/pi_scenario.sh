#!/usr/bin/env bash
# One gate scenario on the Pi: the gate runs on the Pi, the prover and the observer on this machine.
# Usage from the repo root: device/pi_scenario.sh NAME RELEASE N BATCH -- prover/server.py args...
#   e.g. device/pi_scenario.sh order_v1 arrival 24 3 -- --mode order_bits --batch 3 --bits 1101
# Needs: ssh p01 with the repo at ~/p01-v1 and a bundle in ~/p01-v1/bundle, the EZKL CLI at
# ~/p01-v0/ezkl, this machine on 192.168.2.1 and the Pi on 192.168.2.2. Writes OUT/NAME.* files.
# DEADLINE (seconds, default 10) is the gate's deadline, and with fixed release the release time.
# SPIN_MS (default 0): with fixed release, wake this many ms early and busy-wait until the release time.
# The observer runs with /usr/bin/python3: on macOS a Homebrew Python may lack Local Network access.
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="${OUT:-runs}"
DEADLINE="${DEADLINE:-10}"
SPIN_MS="${SPIN_MS:-0}"
mkdir -p "$OUT"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
NAME=$1 RELEASE=$2 N=$3 BATCH=$4; shift 5
# These go into a command run by the Pi's shell, so only plain values are allowed.
[[ "$NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "bad NAME"; exit 2; }
[[ "$RELEASE" =~ ^(immediate|arrival|fixed)$ ]] || { echo "bad RELEASE"; exit 2; }
[[ "$N" =~ ^[1-9][0-9]{0,4}$ && "$BATCH" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "bad N or BATCH"; exit 2; }
[[ "$DEADLINE" =~ ^[0-9]+(\.[0-9]+)?$ && "$SPIN_MS" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "bad DEADLINE or SPIN_MS"; exit 2; }
X='[[0.5, -0.2, 0.9, 0.1]]'
cleanup() {
  ssh -n p01 "pkill -f '[f]rom pge.service import main'; true"
  pkill -f "[p]rover/server.py --listen 192.168.2.1" ; sleep 0.5; pkill -9 -f "[p]rover/server.py --listen 192.168.2.1"; true
}
cleanup
trap cleanup EXIT
# The gate on the Pi must run the same code as this checkout, or the manifest's commit is wrong.
# Sort by file name in byte order: the two machines' locales order "__init__.py" differently.
here="$(cd pge && shasum -a 256 *.py | LC_ALL=C sort -k2 | shasum -a 256 | cut -c1-16)"
there="$(ssh -n p01 'cd ~/p01-v1/pge && sha256sum *.py | LC_ALL=C sort -k2 | sha256sum | cut -c1-16')"
[[ "$here" == "$there" ]] || { echo "$NAME: the Pi's pge differs from this checkout; redeploy: git archive HEAD | ssh p01 'cd ~/p01-v1 && tar -x'"; exit 1; }
ssh -n p01 "ss -ltn | grep -q ':8700 '" && { echo "$NAME: port 8700 still busy"; exit 1; }
.venv/bin/python prover/server.py --listen 192.168.2.1:8701 "$@" 2>"$OUT/$NAME.server.log" &
for i in $(seq 120); do grep -q listening "$OUT/$NAME.server.log" && break; sleep 0.5; done
grep -q listening "$OUT/$NAME.server.log" || { echo "$NAME: server did not start"; cleanup; exit 1; }
ssh -n p01 "cd ~/p01-v1 || exit 1; rm -f ~/gate_$NAME.log ~/gate_$NAME.err; nohup python3 -I -c 'import sys; sys.path.insert(0, \".\"); from pge.service import main; main()' --listen 192.168.2.2:8700 --server 192.168.2.1:8701 --bundle bundle --pins device/tiny.sha256 --cli /home/bob/p01-v0/ezkl --domain tests/fixtures/tiny/domain.json --proof-bytes 3072 --deadline $DEADLINE --spin-ms $SPIN_MS --release $RELEASE --log /home/bob/gate_$NAME.log </dev/null >/home/bob/gate_$NAME.err 2>&1 &"
ok=0
for i in $(seq 40); do
  if ssh -n p01 "ss -ltn | grep -q '192.168.2.2:8700 '" && lsof -nP -iTCP:8701 -sTCP:ESTABLISHED 2>/dev/null | grep -q 192.168.2.2; then ok=1; break; fi
  sleep 0.25
done
[ $ok = 1 ] || { echo "$NAME: gate not up"; ssh -n p01 "cat ~/gate_$NAME.err" | tail -3; cleanup; exit 1; }
if [ "$BATCH" -gt 1 ]; then B="--batch $BATCH"; else B=""; fi
timeout 600 /usr/bin/python3 observer/client.py 192.168.2.2 8700 "$X" --n "$N" $B > "$OUT/$NAME.trace.jsonl" 2>"$OUT/$NAME.client.err"
rc=$?
rows=$(wc -l < "$OUT/$NAME.trace.jsonl" | tr -d ' ')
sleep 0.5
ssh -n p01 "cat ~/gate_$NAME.log" > "$OUT/$NAME.gate.jsonl"
ssh -n p01 "cat ~/gate_$NAME.err" > "$OUT/$NAME.gate.err"
status=ok
[[ $rc == 0 && $rows == "$N" ]] || status=failed
printf '{"name": "%s", "release": "%s", "n": %s, "batch": %s, "deadline": %s, "spin_ms": %s, "commit": "%s", "pge_sha256": "%s", "started": "%s", "client_exit": %s, "rows": %s, "status": "%s", "server_args": "%s"}\n' \
  "$NAME" "$RELEASE" "$N" "$BATCH" "$DEADLINE" "$SPIN_MS" "$(git rev-parse --short HEAD)" "$here" "$STARTED" "$rc" "$rows" "$status" "$*" > "$OUT/$NAME.manifest.json"
echo "$NAME: client exit $rc rows $rows/$N $status"
[[ $status == ok ]]
