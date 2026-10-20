#!/usr/bin/env bash
# Renders book/diagrams/*.mmd to book/src/diagrams/*.svg (mdBook has no mermaid support).
# Needs Node and a Chrome or Chromium; set PUPPETEER_EXECUTABLE_PATH if it isn't found.
set -euo pipefail
cd "$(dirname "$0")"
for f in diagrams/*.mmd; do
  n="$(basename "$f" .mmd)"
  npx -y @mermaid-js/mermaid-cli@11.4.2 -q -c diagrams/mermaid.json -b white -i "$f" -o "src/diagrams/$n.svg"
done
