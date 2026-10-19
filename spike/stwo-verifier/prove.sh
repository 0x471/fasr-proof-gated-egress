#!/usr/bin/env bash
# Rebuild StarkWare's prover at the pinned commit and regenerate the fixtures.
# Proving is deterministic, so the result must match fixtures/SHA256SUMS. Needs about 15 GB of RAM.
# Usage: spike/stwo-verifier/prove.sh [WORKDIR]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:-$HERE/.build}"
# The tag sharp-7.4. A branch of the same name points elsewhere, so fetch the commit itself.
REV=b0b74007223b28f8e4d778a64fc72021ff817186

mkdir -p "$WORK"
if [ ! -d "$WORK/stwo-cairo" ]; then
  git init -q "$WORK/stwo-cairo"
  git -C "$WORK/stwo-cairo" fetch -q --depth 1 https://github.com/starkware-libs/stwo-cairo.git "$REV"
  git -C "$WORK/stwo-cairo" checkout -q FETCH_HEAD
fi
got="$(git -C "$WORK/stwo-cairo" rev-parse HEAD)"
[ "$got" = "$REV" ] || { echo "stwo-cairo checkout is $got, expected $REV" >&2; exit 1; }
SRC="$WORK/stwo-cairo/stwo_cairo_prover"
(cd "$SRC" && cargo build -q --release --locked --bin run_and_prove >&2)
BIN="$SRC/target/release/run_and_prove"
DATA="$SRC/test_data"
"$BIN" --program "$DATA/test_prove_verify_ret_opcode/compiled.json" \
  --proof_path "$HERE/fixtures/ret_opcode.bin" --proof-format binary >/dev/null
"$BIN" --program "$DATA/test_prove_verify_all_opcode_components/compiled.json" \
  --proof_path "$HERE/fixtures/weak_all_opcode.bin" --proof-format binary \
  --params_json "$HERE/weak_params.json" >/dev/null
(cd "$HERE/fixtures" && shasum -a 256 -c SHA256SUMS)
