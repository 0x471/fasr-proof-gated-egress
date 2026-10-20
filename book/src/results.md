# Measurements

> **In short**
> - Checking one proof on the Pi takes about 0.1 s with the proof tool alone and 0.24 s with all the gate's safety limits.
> - At a 256 MiB memory cap every recorded check passed (231 of 231); smaller caps fail unpredictably.
> - The leakage experiment, one client at a time: releasing in arrival order stopped the ordering code we tried; a fixed release time stopped the 1 s timing code but left a small timing trace, at the cost of 4 s per answer; deliberate failures leaked under every policy. A plain explanation is in [What a hacked server can still leak](leakage.md).

Everything here was measured on one Raspberry Pi 4 (2 GB, Raspberry Pi OS Lite, 64-bit) with the AI server (Python EZKL prover) and the observer on a laptop (Apple M4 Pro) over a direct Ethernet link, unless stated otherwise. Dates are 2026.

**How to read the leakage numbers.** The server hides known bits and an observer tries to read them back with a fixed decoder. "16/16" means every planted bit was recovered. When the decoder fails, the table says how many bits it got wrong; that shows this encoder and decoder no longer work, not that nothing leaks. These are achieved rates for one encoder each, from one sequential client unless stated; they are not capacity bounds.

## Checking one proof

Whole EZKL CLI process per check (start-up, loading, verify), tiny circuit:

| Date | Runs | Result |
|---|---|---|
| 13 Oct | 20 | 99-103 ms |
| 16 Oct | 20 | min 101, median 103, max 106 ms |
| 19 Oct | 20 | min 101, median 103, max 280 ms (one cold run just after boot) |

What the full gate path costs (19 Oct, medians of 20, `device/verify_cost.py`):

| Path | ms |
|---|---|
| Raw CLI, default threads (4 cores) | 97-98 |
| Raw CLI, one thread (`RAYON_NUM_THREADS=1`) | 163 |
| With the gate's resource limits (single-threaded launcher) | 207 |
| Re-checking the bundle's hashes (includes a 4 MB setup file) | 28 |
| Reading and canonicalising the proof | 2 |
| Full gate check | 239 |

A raw proof for the test model is 3,072 bytes. One thread costs 1.7x; the launcher's Python start-up is about 45 ms. Both could be cut (a static launcher; hashing the bundle once when it lives in a read-only image). On the laptop, proving takes about 0.5-0.8 s and an in-process verify 8-14 ms.

**The memory limit is not a clean floor.** Address-space limit vs. how often a valid proof is accepted, in one sweep of 20 runs per limit (19 Oct; the rest abort, which the gate treats as an error):

| Limit | Accepted |
|---|---|
| 96 MiB | 0/20 |
| 112 MiB | 18/20 |
| 128 MiB | 20/20 |
| 144 MiB | 11/20 |
| 160 MiB | 9/20 |
| 192 MiB | 20/20 |
| 224 MiB | 20/20 |
| 256 MiB (the gate's setting) | 20/20 |
| 320 MiB | 20/20 |

Other runs the same day at 256 MiB: one first check, then 200/200 and 10/10, so 231 of 231 in all with the sweep; at 192 MiB 200/200; at 160 MiB 4/10 in a later sweep. The outcome near the limit varies from run to run and doesn't improve steadily with more memory (address-space layout randomisation is on). At 256 MiB, 0 failures in 231 runs means below about 1.3 % at 95 % confidence if the runs are independent; not proven zero, and only for this circuit on this Pi. A spurious error would count as a server failure, so the margin matters. The memory cap is enforced on Linux only.

## Attack surface

| Version | Seen from outside | Notes |
|---|---|---|
| v0 (stock OS, 16 Oct) | TCP 22 (ssh) and 8700 (the gate); UDP 5353 (mDNS) | 633 packages, 13 running services |
| v1 development image (16 Oct) | 0 of 65,535 TCP ports open; top 100 UDP ports closed; no IPv6 | **No gate was running on this image yet**, so this shows a quiet base system, not a working hardened gate |

## The gate end to end

| Date | Software | Result |
|---|---|---|
| 16 Oct | v0 (JSON, immediate release) | about 0.9-1.05 s per answer (mostly proving on the laptop); `y` matches the float model within quantisation |
| 19 Oct | v1 (binary, arrival order, limits, fixed-word logs) | 5/5 ok, 0.79-0.89 s per answer |

Misbehaving server against the v1 gate on the Pi (19 Oct):

| Server behaviour | What the gate did |
|---|---|
| Changes an output value | proof rejected; client gets `unavailable` |
| Replays an earlier answer for the same request | accepted, by design: the same request gives the same output, so the server has no choice there |
| Answers twice | one answer to the client; the duplicate is dropped and counted |
| Sends garbage or an oversized frame | server link dropped; the pending request fails, the next gets `unavailable` in under 1 ms |

The first timing test (16 Oct, v0): the server delayed an answer by 1 s for each 1 bit; an observer decoded 16 of 16 bits, 0.69 bit/s, while honest answers varied by 81 ms.

## Release policies on the Pi

The core experiment (19 Oct): the same workload under each of the gate's three release policies, with a 4 s budget for every policy, 16 planted bits per channel, one run per cell (`device/pi_matrix.sh`, summarised by `observer/analyze_matrix.py`).

| Channel the server uses | immediate (v0) | arrival (v1) | fixed |
|---|---|---|---|
| Timing: delay an answer by 1 s for a 1 | **16/16 recovered** | **16/16 recovered** | not recovered (12/16 wrong); answer times within 2 ms |
| Ordering: answer 3 concurrent requests in a chosen order | **16/16 recovered** | decoded bits constant | decoded bits constant |
| Failure position: fail one chosen answer in each 16 | **16/16 recovered** | **16/16 recovered** | **16/16 recovered** |
| No signal (honest server, timing decoder) | 8/16 wrong | 10/16 wrong | 7/16 wrong |
| Honest answer time, median | 0.81 s | 0.83 s | 4.0 s |

- **Ordering:** with arrival or fixed release the decoded bits were the same whatever the server planted, so the server's choice of order never reached the observer. On one connection that holds by construction: the gate sends answers in admission order and the client's ids increase. A separate localhost run showed v0 leaking floor(log2 k!) bits per batch of k concurrent requests (k = 2, 3, 4) with no errors.
- **Timing:** the 1 s code wasn't recovered under fixed release, but a smaller timing trace remains (below). The cost is latency: every answer waits the whole budget, about 5 times the honest median in this sequential run.
- **Failures** were recovered under every policy: 4 failures in 64 answers (6.25 %) carried 16 bits, 0.25 bit per request, with this particular encoder.

### What fixed release leaves

A longer run (128 random bits, plus 128 honest requests as a control) asked whether the 2 ms of remaining jitter still follows the server:

| Run | Bit-1 minus bit-0 answer time | Permutation test |
|---|---|---|
| Server encoding bits | -0.66 ms (-0.73 and -0.60 in each half) | p < 0.0001 |
| Honest server, same labels | +0.19 ms | p = 0.13 |
| Server encoding bits, with a 5 ms spin before release (old spin code) | +0.08 ms | p = 0.31 |
| Honest server, with the spin (old spin code) | +0.09 ms | p = 0.20 |
| Server encoding bits, spin, fixed code, n = 512 (20 Oct) | -0.06 ms | p = 0.071 |
| Honest server, spin, fixed code, n = 512 (20 Oct) | -0.02 ms | p = 0.48 |

**Without spin**, the association is clear. A threshold decoder fitted on one half and tested on the other got 25 % and 28 % of the bits wrong (`observer/jitter_test.py --heldout`). If the channel were a symmetric binary channel with independent errors, that would correspond to about 0.14-0.19 bit per answer; it is a model-based figure, not a measured throughput, and the two error kinds were not symmetric in one half.

**With a 5 ms spin** (wake early and busy-wait until the release time, `--spin-ms 5`) the average shift is gone, but not every trace of the signal: the held-out decoder still got only 34 % wrong on one half (44 % on the other; honest controls 39-59 %), and a null that refits the decoder on shuffled labels does that well only 0.75 % of the time. That run used the spin code from before the K33 fix and 0.1 ms timestamps, so it was repeated.

**The repeat (20 Oct)** followed a protocol written before the data (`docs/EXPERIMENT_PROTOCOL.md`): 512 seeded random bits (249 ones), the fixed spin code, microsecond timestamps, and an honest run of 512 as the control. Both runs completed 512 of 512 answers, every one accepted, all leaving 4000.96-4003.84 ms after they were sent. The primary test is the held-out decoder: it got 46 % and 44 % wrong, and the refitting null does that well 4.1 % of the time (p = 0.041; honest control 51 % and 52 %, p = 0.77). The protocol counts a residue as shown only below p = 0.01 and as not detected only above 0.05, so the result is **inconclusive**. Any residue left is small: the best decoder gets about 56 % right, against 72-75 % without spin. Idle wake-up latency is one hypothesis for a residue; it hasn't been isolated.

Deviation from the protocol: the first attempt at both runs stopped at 149 of 512 answers because the observer's time limit was fixed at 600 s. The limit now grows with the run's length (commit `cd0086c`, which changes only `device/pi_scenario.sh`; the gate's code hash on the Pi was the same), and both runs were repeated in full; the partial runs were not analysed for the result.

A second measure on the same data (`observer/estimate.py`): the mutual information between the hidden bit and the answer time, with times cut into 4 bins at their quantiles, a small-sample bias correction, a shuffled-label null and a bootstrap interval, checked first on synthetic channels with known values (`tests/test_estimate.py`). It describes this one binned statistic; it is not a lower or upper bound on what the timing reveals.

| Run (n = 128) | Mutual information, bit per answer (4 bins) | p |
|---|---|---|
| Fixed release, server encoding bits | 0.17 | 0.0005 |
| Fixed release, honest server | about 0 | 0.68 |
| Fixed release with spin, server encoding bits | 0.05 | 0.012 |
| Fixed release with spin, honest server | about 0 | 0.73 |
| Spin, fixed code, server encoding bits (n = 512) | 0.006 (95 % interval -0.003 to 0.026) | 0.069 |
| Spin, fixed code, honest server (n = 512) | about 0 | 0.94 |

With 2 or 8 bins the no-spin result holds (p = 0.0005) and the old spin result doesn't reach significance (p = 0.46 and 0.09). Caveats on the 128-answer runs: the observer recorded times rounded to 0.1 ms (only 19-30 distinct values per 128 answers), and the spin runs used an earlier version of the spin code that a review later found could let a later answer overtake an earlier one when several were in flight. The 512-answer repeat fixes both; at 4 bins and n = 512 it is not significant either (p = 0.069).

## What these results don't show

- One device, one network, one sequential client (ordering used concurrent requests on one connection). Several clients, other loads and packet-level observation (sizes, TCP behaviour) are not measured yet.
- Every leakage cell is one run of 16 bits, except the 128-bit residual tests and the 512-bit spin repeat.
- The release budget (4 s) was chosen by hand; the trade-off between budget, leakage and lost answers is not measured yet.
- Nothing here is a bound on what a cleverer encoder could achieve.
