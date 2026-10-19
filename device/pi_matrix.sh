#!/usr/bin/env bash
# Release policies x leakage workloads on the Pi gate (device/pi_scenario.sh for each cell).
# Usage from the repo root: OUT=runs/NAME device/pi_matrix.sh
set -uo pipefail
cd "$(dirname "$0")/.."
export OUT="${OUT:-runs/matrix}" DEADLINE="${DEADLINE:-4}"
BITS=1101001110100111
for r in immediate arrival fixed; do
  device/pi_scenario.sh "honest_$r" "$r" 16 1 -- --mode honest
  device/pi_scenario.sh "timing_$r" "$r" 16 1 -- --mode timing_bits --bits "$BITS" --slot 1.0
  device/pi_scenario.sh "order_$r" "$r" 24 3 -- --mode order_bits --batch 3 --bits "$BITS"
  device/pi_scenario.sh "failpos_$r" "$r" 64 1 -- --mode fail_position --batch 16 --bits "$BITS"
done
echo MATRIX DONE
