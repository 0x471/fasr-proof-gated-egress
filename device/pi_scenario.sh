#!/usr/bin/env bash
# One endpoint scenario on the Pi: the endpoint runs on the Pi, the prover and the observer on this machine.
# Usage from the repo root: device/pi_scenario.sh NAME RELEASE N BATCH -- prover/server.py args...
#   e.g. device/pi_scenario.sh order_v1 arrival 24 3 -- --mode order_bits --batch 3 --bits 1101
# Needs: ssh p01 with the repo at ~/p01-v1 and a bundle in ~/p01-v1/bundle, the EZKL CLI at
# ~/p01-v0/ezkl, this machine on 192.168.2.1 and the Pi on 192.168.2.2. Writes OUT/NAME.* files.
# DEADLINE (seconds, default 10) is the endpoint's deadline, and with fixed release the release time.
# SPIN_MS (default 0): with fixed release, wake this many ms early and busy-wait until the release time.
# CLIENTS (default 1): with more than 1, that many connections each send one request per round (N rounds).
# ENDPOINT (default python): rust runs the static binary BIN (default: the aarch64 musl build in endpoint/target) as
# ~/p01-rust/pge-endpoint on the Pi instead of the Python endpoint and the EZKL CLI. Immediate release is
# Python only. CAP="W F C" turns on the Rust endpoint's failure cap.
# The observer runs with /usr/bin/python3: on macOS a Homebrew Python may lack Local Network access.
# The checkout must be clean: the manifest names its commit, taken before the run, and the hashes
# of what the Pi ran (endpoint code, bundle, pins, domain, EZKL CLI, Python). A run is "ok" only if the
# client finished, every row arrived and both endpoint logs were copied back.
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="${OUT:-runs}"
DEADLINE="${DEADLINE:-10}"
SPIN_MS="${SPIN_MS:-0}"
CLIENTS="${CLIENTS:-1}"
ENDPOINT="${ENDPOINT:-python}"
BIN="${BIN:-endpoint/target/aarch64-unknown-linux-musl/release/pge-endpoint}"
CAP="${CAP:-}"
NAME=$1 RELEASE=$2 N=$3 BATCH=$4; shift 5
# These go into a command run by the Pi's shell, so only plain values are allowed.
[[ "$NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "bad NAME"; exit 2; }
[[ "$RELEASE" =~ ^(immediate|arrival|fixed)$ ]] || { echo "bad RELEASE"; exit 2; }
[[ "$N" =~ ^[1-9][0-9]{0,4}$ && "$BATCH" =~ ^[1-9][0-9]{0,3}$ && "$CLIENTS" =~ ^[1-9][0-9]?$ ]] || { echo "bad N, BATCH or CLIENTS"; exit 2; }
num='^(0|[1-9][0-9]*)(\.[0-9]+)?$'
[[ "$DEADLINE" =~ $num && "$SPIN_MS" =~ $num ]] || { echo "bad DEADLINE or SPIN_MS"; exit 2; }
[[ "$ENDPOINT" =~ ^(python|rust)$ ]] || { echo "bad ENDPOINT"; exit 2; }
[[ -z "$CAP" || ( "$ENDPOINT" == rust && "$CAP" =~ ^[1-9][0-9]{0,3}\ [0-9]{1,4}\ [1-9][0-9]{0,3}$ ) ]] || { echo "bad CAP"; exit 2; }
[[ "$ENDPOINT" == python || "$RELEASE" != immediate ]] || { echo "the Rust endpoint has no immediate release"; exit 2; }
[[ -z "$(git status --porcelain)" ]] || { echo "$NAME: the checkout has changes; commit them first"; exit 2; }
COMMIT="$(git rev-parse HEAD)"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$OUT"
X='[[0.5, -0.2, 0.9, 0.1]]'
cleanup() {
  ssh -n p01 "pkill -f '[f]rom pge.service import main'; pkill -x pge-endpoint; true"
  pkill -f "[p]rover/server.py --listen 192.168.2.1" ; sleep 0.5; pkill -9 -f "[p]rover/server.py --listen 192.168.2.1"; true
}
cleanup
trap cleanup EXIT
# The endpoint on the Pi must run the same code as this checkout, or the manifest's commit is wrong.
# Sort by file name in byte order: the two machines' locales order "__init__.py" differently.
if [[ $ENDPOINT == python ]]; then
  here="$(cd pge && shasum -a 256 *.py | LC_ALL=C sort -k2 | shasum -a 256 | cut -c1-16)"
  there="$(ssh -n p01 'cd ~/p01-v1/pge && sha256sum *.py | LC_ALL=C sort -k2 | sha256sum | cut -c1-16')"
  [[ "$here" == "$there" ]] || { echo "$NAME: the Pi's pge differs from this checkout; redeploy: git archive HEAD | ssh p01 'cd ~/p01-v1 && tar -x'"; exit 1; }
  RUNTIME="/home/bob/p01-v0/ezkl \"\$(readlink -f \"\$(command -v python3)\")\" && python3 --version"
else
  # BIN should be built from this commit (CI artifact or a local build); the manifest records its hash.
  [[ -f "$BIN" ]] || { echo "$NAME: no binary at $BIN"; exit 1; }
  here="$(shasum -a 256 "$BIN" | cut -d' ' -f1)"
  there="$(ssh -n p01 'sha256sum ~/p01-rust/pge-endpoint' | cut -d' ' -f1)"
  [[ "$here" == "$there" ]] || { echo "$NAME: the Pi's pge-endpoint differs from $BIN; redeploy: scp $BIN p01:p01-rust/pge-endpoint"; exit 1; }
  RUNTIME="/home/bob/p01-rust/pge-endpoint"
fi
# Everything else the endpoint's verdicts depend on. The endpoint checks the bundle against the pins itself.
pi_files="$(ssh -n p01 "cd ~/p01-v1 && sha256sum bundle/* device/tiny.sha256 tests/fixtures/tiny/domain.json $RUNTIME")" \
  || { echo "$NAME: could not hash the Pi's files"; exit 1; }
for f in device/tiny.sha256 tests/fixtures/tiny/domain.json; do
  [[ "$pi_files" == *"$(shasum -a 256 "$f" | cut -d' ' -f1)  $f"* ]] || { echo "$NAME: the Pi's $f differs from this checkout"; exit 1; }
done
ssh -n p01 "ss -ltn | grep -q ':8700 '" && { echo "$NAME: port 8700 still busy"; exit 1; }
.venv/bin/python prover/server.py --listen 192.168.2.1:8701 "$@" 2>"$OUT/$NAME.server.log" &
for i in $(seq 120); do grep -q listening "$OUT/$NAME.server.log" && break; sleep 0.5; done
grep -q listening "$OUT/$NAME.server.log" || { echo "$NAME: server did not start"; cleanup; exit 1; }
ARGS="--listen 192.168.2.2:8700 --server 192.168.2.1:8701 --bundle bundle --pins device/tiny.sha256 --domain tests/fixtures/tiny/domain.json --proof-bytes 3072 --deadline $DEADLINE --spin-ms $SPIN_MS --release $RELEASE --log /home/bob/endpoint_$NAME.log"
if [[ $ENDPOINT == python ]]; then
  LAUNCH="python3 -I -c 'import sys; sys.path.insert(0, \".\"); from pge.service import main; main()' $ARGS --cli /home/bob/p01-v0/ezkl"
else
  read -r CW CF CC <<< "${CAP:-}"
  LAUNCH="/home/bob/p01-rust/pge-endpoint $ARGS${CAP:+ --cap-window $CW --cap-failures $CF --cap-cooldown $CC}"
fi
ssh -n p01 "cd ~/p01-v1 || exit 1; rm -f ~/endpoint_$NAME.log ~/endpoint_$NAME.err; nohup $LAUNCH </dev/null >/home/bob/endpoint_$NAME.err 2>&1 &"
ok=0
for i in $(seq 40); do
  if ssh -n p01 "ss -ltn | grep -q '192.168.2.2:8700 '" && lsof -nP -iTCP:8701 -sTCP:ESTABLISHED 2>/dev/null | grep -q 192.168.2.2; then ok=1; break; fi
  sleep 0.25
done
[ $ok = 1 ] || { echo "$NAME: endpoint not up"; ssh -n p01 "cat ~/endpoint_$NAME.err" | tail -3; cleanup; exit 1; }
if [ "$CLIENTS" -gt 1 ]; then B="--clients $CLIENTS"; elif [ "$BATCH" -gt 1 ]; then B="--batch $BATCH"; else B=""; fi
WANT=$(( CLIENTS > 1 ? N * CLIENTS : N ))
# Each answer can take up to the deadline plus a check, so the client's limit grows with N.
limit=$(awk -v n="$N" -v d="$DEADLINE" 'BEGIN { printf "%d", n * (d + 3) + 120 }')
timeout "$limit" /usr/bin/python3 observer/client.py 192.168.2.2 8700 "$X" --n "$N" $B > "$OUT/$NAME.trace.jsonl" 2>"$OUT/$NAME.client.err"
rc=$?
rows=$(wc -l < "$OUT/$NAME.trace.jsonl" | tr -d ' ')
sleep 0.5
logs=0
ssh -n p01 "cat ~/endpoint_$NAME.log" > "$OUT/$NAME.endpoint.jsonl" && [[ -s "$OUT/$NAME.endpoint.jsonl" ]] || logs=1
ssh -n p01 "cat ~/endpoint_$NAME.err" > "$OUT/$NAME.endpoint.err" || logs=1
status=ok
[[ $rc == 0 && $rows == "$WANT" && $logs == 0 ]] || status=failed
ENDPOINT="$ENDPOINT" CAP="$CAP" NAME="$NAME" RELEASE="$RELEASE" N="$N" BATCH="$BATCH" CLIENTS="$CLIENTS" DEADLINE="$DEADLINE" SPIN_MS="$SPIN_MS" \
COMMIT="$COMMIT" HERE="$here" STARTED="$STARTED" RC="$rc" ROWS="$rows" LOGS="$logs" STATUS="$status" PI_FILES="$pi_files" \
  /usr/bin/python3 -I -c '
import json, os, sys
e = os.environ
lines = e["PI_FILES"].splitlines()
files = dict(reversed(l.split("  ", 1)) for l in lines if "  " in l)
m = {"name": e["NAME"], "endpoint": e["ENDPOINT"], "cap": e["CAP"], "release": e["RELEASE"], "n": int(e["N"]), "batch": int(e["BATCH"]),
     "clients": int(e["CLIENTS"]), "deadline": float(e["DEADLINE"]), "spin_ms": float(e["SPIN_MS"]),
     "commit": e["COMMIT"], "pge_sha256": e["HERE"], "pi_sha256": files,
     "pi_python": [l for l in lines if l.startswith("Python ")], "started": e["STARTED"],
     "client_exit": int(e["RC"]), "rows": int(e["ROWS"]), "logs_copied": e["LOGS"] == "0",
     "status": e["STATUS"], "server_args": sys.argv[1:]}
print(json.dumps(m))' "$@" > "$OUT/$NAME.manifest.json" || status=failed
echo "$NAME: client exit $rc rows $rows/$WANT logs $([[ $logs == 0 ]] && echo copied || echo missing) $status"
[[ $status == ok ]]
