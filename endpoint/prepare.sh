#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CARGO_HOME="$ROOT/.deps/cargo"
python3 "$ROOT/endpoint/prepare.py"
