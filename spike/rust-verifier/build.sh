#!/usr/bin/env bash
# Build the spike verifier as an example inside EZKL's own checkout, so it uses EZKL's
# Cargo.lock, patches and toolchain. Prints the binary path.
# Usage: spike/rust-verifier/build.sh [WORKDIR]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:-$HERE/.build}"
TAG=v23.0.5
REV=534ff3e6c13d4c2c1aca2358910d62dac466d3cc

mkdir -p "$WORK"
[ -d "$WORK/ezkl" ] || git clone -q --depth 1 --branch "$TAG" https://github.com/zkonduit/ezkl.git "$WORK/ezkl"
got="$(git -C "$WORK/ezkl" rev-parse HEAD)"
[ "$got" = "$REV" ] || { echo "ezkl checkout is $got, expected $REV" >&2; exit 1; }
cp "$HERE/pge_verify.rs" "$WORK/ezkl/examples/pge_verify.rs"
(cd "$WORK/ezkl" && cargo build -q --release --locked --no-default-features --example pge_verify >&2)
echo "$WORK/ezkl/target/release/examples/pge_verify"
