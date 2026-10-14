#!/usr/bin/env bash
# Verify the pinned tiny proof on a Raspberry Pi with EZKL's official aarch64 CLI and time it.
# Usage from the repo root, with the Pi reachable as `ssh p01`:
#   ./device/verify_on_pi.sh            (RUNS=20 by default)
# The remote login shell must be bash: arguments are quoted with printf %q.
# HOST=local runs the same checks on this Linux machine; CI uses it with
# EZKL_TGZ=ezkl-linux-gnu.tar.gz and that archive's EZKL_TGZ_SHA.
set -euo pipefail

HOST="${HOST:-p01}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ART="$ROOT/tests/fixtures/tiny"
SRS="${PGE_SRS:-$ROOT/prover/artifacts/kzg.srs}"
RUNS="${RUNS:-20}"
VERIFY_TIMEOUT="${VERIFY_TIMEOUT:-60}"
REMOTE_BASE="p01-v0"
EZKL_VERSION="23.0.5"
EZKL_TGZ="${EZKL_TGZ:-ezkl-linux-aarch64.tar.gz}"
EZKL_TGZ_SHA="${EZKL_TGZ_SHA:-384af10d6d6c24379789ba29559c7deacf1b4e287a89ad48e13949939d9dffa6}"
PINS="$(cat "$ROOT/device/tiny.sha256")"

[[ "$RUNS" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "RUNS must be an integer from 1 to 9999"; exit 2; }
[[ "$VERIFY_TIMEOUT" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "VERIFY_TIMEOUT must be 1 to 9999 seconds"; exit 2; }
[[ "$EZKL_TGZ" =~ ^ezkl-linux-[a-z0-9]+\.tar\.gz$ ]] || { echo "EZKL_TGZ must name a linux release archive"; exit 2; }
[[ "$EZKL_TGZ_SHA" =~ ^[0-9a-f]{64}$ ]] || { echo "EZKL_TGZ_SHA must be a sha256 hex digest"; exit 2; }
[[ -f "$SRS" ]] || { echo "missing SRS at $SRS (set PGE_SRS)"; exit 2; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp "$ART"/{proof.json,settings.json,vk.key} "$STAGE"/
cp "$SRS" "$STAGE/kzg.srs"
mkdir "$STAGE/pge"
cp "$ROOT"/pge/{__init__,binding,verdict}.py "$STAGE/pge"/
echo "== pinned inputs (local)"
if command -v sha256sum >/dev/null; then SUM=(sha256sum); else SUM=(shasum -a 256); fi
(cd "$STAGE" && "${SUM[@]}" -c - <<<"$PINS")
ARGS=("$REMOTE_BASE" "$EZKL_VERSION" "$EZKL_TGZ" "$EZKL_TGZ_SHA" "$RUNS" "$VERIFY_TIMEOUT" "$PINS")

if [ "$HOST" = local ]; then
  mkdir -p "$HOME/$REMOTE_BASE/cache"
  RUN_DIR="$(mktemp -d "$HOME/$REMOTE_BASE/run.XXXXXX")"
  echo "== local run directory: $RUN_DIR"
  cp -R "$STAGE"/{proof.json,settings.json,vk.key,kzg.srs,pge} "$RUN_DIR"/
  rc=0
  bash "$ROOT/device/check_verifier.sh" "$RUN_DIR" "${ARGS[@]}" || rc=$?
  exit $rc
fi

SSH=(ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 "$HOST")
RUN_DIR="$("${SSH[@]}" "mkdir -p ~/$REMOTE_BASE/cache && mktemp -d ~/$REMOTE_BASE/run.XXXXXX")"
echo "== remote run directory: $RUN_DIR"
scp -q -r -o ConnectTimeout=10 "$STAGE"/{proof.json,settings.json,vk.key,kzg.srs,pge} "$HOST:$RUN_DIR/"

"${SSH[@]}" "bash -s -- $(printf '%q ' "$RUN_DIR" "${ARGS[@]}")" < "$ROOT/device/check_verifier.sh"
