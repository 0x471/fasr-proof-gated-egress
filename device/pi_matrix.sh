#!/usr/bin/env bash
# Release policies x leakage workloads on the Pi endpoint (device/pi_scenario.sh for each cell).
# Usage from the repo root: OUT=runs/NAME device/pi_matrix.sh
set -uo pipefail
cd "$(dirname "$0")/.."
export OUT="${OUT:-runs/matrix}" DEADLINE="${DEADLINE:-4}"
BITS=1101001110100111
failed=0
run() { device/pi_scenario.sh "$@" || failed=$((failed + 1)); }
# The Rust endpoint (ENDPOINT=rust) has no immediate release.
releases="immediate arrival fixed"; [[ "${ENDPOINT:-python}" == rust ]] && releases="arrival fixed"
for r in $releases; do
  run "honest_$r" "$r" 16 1 -- --mode honest
  run "timing_$r" "$r" 16 1 -- --mode timing_bits --bits "$BITS" --slot 1.0
  run "order_$r" "$r" 24 3 -- --mode order_bits --batch 3 --bits "$BITS"
  run "failpos_$r" "$r" 64 1 -- --mode fail_position --batch 16 --bits "$BITS"
done
if [[ $failed == 0 ]]; then echo MATRIX DONE; else echo "MATRIX INCOMPLETE: $failed cell(s) failed" >&2; exit 1; fi
