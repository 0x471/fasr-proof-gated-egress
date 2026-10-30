#!/usr/bin/env bash
# Runs after the board's post-build script: removes services the endpoint doesn't need.
set -euo pipefail
rm -f "$TARGET_DIR/etc/init.d/S50crond"
