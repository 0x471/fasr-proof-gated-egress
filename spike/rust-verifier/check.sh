#!/usr/bin/env bash
# Check the spike verifier against the pinned tiny bundle: exit 0 valid, 1 invalid, 2 could not check.
# Usage: spike/rust-verifier/check.sh BINARY SRS
set -euo pipefail
BIN="$1"; SRS="$2"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$ROOT/tests/fixtures/tiny"
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT

python3 - "$T/proof.json" "$W" <<'PY'
import json, sys
p = json.load(open(sys.argv[1])); w = sys.argv[2]
def save(name, obj): json.dump(obj, open(f"{w}/{name}.json", "w"))
save("valid", p)
q = json.loads(json.dumps(p)); q["instances"][0][-3] = q["instances"][0][1]; save("tampered_output", q)
q = json.loads(json.dumps(p)); q["instances"][0][0] = q["instances"][0][1]; save("tampered_input", q)
save("truncated", dict(p, proof=p["proof"][:-32]))
save("padded", dict(p, proof=p["proof"] + [0]))
open(f"{w}/garbage.json", "w").write("bad\n")
PY

fail=0
check() {
  local rc=0
  "$BIN" "$T/settings.json" "$T/vk.key" "$SRS" "$W/$1.json" >/dev/null 2>&1 || rc=$?
  if [ "$rc" = "$2" ]; then echo "PASS  $1: exit $rc"; else echo "FAIL  $1: exit $rc, want $2"; fail=1; fi
}
check valid 0
check tampered_output 1
check tampered_input 1
check truncated 1
check padded 0
check garbage 2
[ $fail -eq 0 ] && echo "all checks passed"
exit $fail
