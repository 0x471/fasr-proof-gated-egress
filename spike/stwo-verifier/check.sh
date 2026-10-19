#!/bin/sh
# Build the pinned verifier, check the fixtures' hashes, then run every case.
set -eu
cd "$(dirname "$0")"
cargo build --release --locked
(cd fixtures && shasum -a 256 -c SHA256SUMS)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 -I cases.py target/release/pge-stwo-verify fixtures "$work"
