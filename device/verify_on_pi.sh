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
PINS="$(cat "$ROOT/device/tiny.sha256")"

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

"${SSH[@]}" "bash -s -- $(printf '%q ' "$RUN_DIR" "$REMOTE_BASE" "$EZKL_VERSION" "$EZKL_TGZ" "$EZKL_TGZ_SHA" "$RUNS" "$VERIFY_TIMEOUT" "$PINS")" < "$ROOT/device/check_verifier.sh"
