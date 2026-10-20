# Experiment protocol: residual timing under fixed release with spin

Written before collecting the data (20 Oct 2026). Any analysis not listed here is exploratory and is reported as such.

## Question
With fixed release and a 5 ms spin, using the gate code after the K33 fix, does answer time still carry the server's planted bits, for the `timing_bits` encoder with 1 s slots and one sequential client?

## Setup
- Raspberry Pi 4 (stock OS), gate at the commit recorded in each run's manifest; prover and observer on the laptop over the direct Ethernet link.
- Gate: `--release fixed --deadline 4 --spin-ms 5`. Observer times recorded to the microsecond (`observer/client.py`).
- Bits: 512, `random.Random(20261020)`, each `rng.choice('01')`.
- Runs: `timing_spin5_512` (server `--mode timing_bits --bits B --slot 1.0`, N = 512) and `honest_spin5_512` (server `--mode honest`, N = 512, analysed with the same labels). Both via `device/pi_scenario.sh` with `DEADLINE=4 SPIN_MS=5`.
- A run counts only if its manifest says `"status": "ok"` (512 rows, client exit 0). A failed run is repeated in full, never patched.

## Primary analysis
Held-out threshold decoder (`observer/jitter_test.py --heldout 2000`): fit a threshold and polarity on the first 256 answers, count errors on the last 256, and the other way round. Statistic: the smaller of the two error rates. Null: the same statistic after shuffling the labels and refitting, 2000 times; p = (count of null values at or below the observed one + 1) / 2001.

Decision rule:
- **Residue shown** if the signal run has p < 0.01 and the honest run has p > 0.05.
- **No residue detected** if the signal run has p > 0.05: report it as "not detected by this decoder at n = 512", not as "no leakage".
- Anything else is **inconclusive**.

## Secondary analyses (reported, not used for the decision)
- Mean difference (bit 1 minus bit 0) and its two-sided permutation p, 20000 shuffles (`jitter_test.py --perms 20000`).
- Mutual information with **4 bins only** (`observer/estimate.py --bins 4`), with its shuffled-label p and bootstrap interval.

## Reporting
Every number above for both runs, the manifests, the commit, and any deviation from this protocol, in `book/src/results.md` and `docs/ATTACKS.md` K8.

## Commands
```sh
B=$(python3 -c "import random; r=random.Random(20261020); print(''.join(r.choice('01') for _ in range(512)))")
export OUT=runs/spin5_512 DEADLINE=4 SPIN_MS=5
device/pi_scenario.sh timing_spin5_512 fixed 512 1 -- --mode timing_bits --bits $B --slot 1.0
device/pi_scenario.sh honest_spin5_512 fixed 512 1 -- --mode honest
python3 observer/jitter_test.py runs/spin5_512/timing_spin5_512.trace.jsonl $B --control runs/spin5_512/honest_spin5_512.trace.jsonl --perms 20000 --heldout 2000
python3 observer/estimate.py runs/spin5_512/timing_spin5_512.trace.jsonl $B --bins 4 --control runs/spin5_512/honest_spin5_512.trace.jsonl
```
Each scenario takes about 35 minutes (512 answers at 4 s).
