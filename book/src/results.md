# Measurements

> **In short**
> - Checking one proof on the Pi takes about 0.1 s with the proof tool alone and 0.24 s with all the gate's safety limits.
> - A 256 MiB memory cap is safe for the checker; smaller caps fail unpredictably.
> - The leakage experiment: order and timing can be closed (timing at the cost of 4 s per answer), deliberate failures can't yet. A plain explanation is in [What a hacked server can still leak](leakage.md).

Everything here was measured on one Raspberry Pi 4 (2 GB, Raspberry Pi OS Lite, 64-bit) with the AI server (Python EZKL prover) and the observer on a laptop (Apple M4 Pro) over a direct Ethernet link, unless stated otherwise. Dates are 2026.

**How to read the leakage numbers.** The server hides known bits and an observer tries to read them back. "16/16" means every planted bit was recovered; "chance" means about half were wrong. These are achieved rates for one encoder each. They show that a channel is open, or that one encoder no longer works; they are not capacity bounds.

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

Longer runs the same day: 192 MiB 200/200; 256 MiB 200/200 and 10/10 (231 of 231 in all); 160 MiB 4/10 in a later sweep. The outcome near the limit varies from run to run and doesn't improve steadily with more memory (address-space layout randomisation is on). At 256 MiB, 0 failures in 231 runs means below about 1.3 % at 95 % confidence; not proven zero. A spurious error would count as a server failure, so the margin matters.

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

The core experiment (19 Oct): the same workload under each of the gate's three release policies, with a 4 s budget for every policy, 16 planted bits per channel (`device/pi_matrix.sh`, summarised by `observer/analyze_matrix.py`).

| Channel the server uses | immediate (v0) | arrival (v1) | fixed |
|---|---|---|---|
| Timing: delay an answer by 1 s for a 1 | **16/16 leaked** | **16/16 leaked** | chance (12/16 wrong); answer times within 2 ms |
| Ordering: answer 3 concurrent requests in a chosen order | **16/16 leaked** | none (always the same order) | none |
| Failure position: fail one chosen answer in each 16 | **16/16 leaked** | **16/16 leaked** | **16/16 leaked** |
| No signal (honest server, timing decoder) | 8/16 wrong | 10/16 wrong | 7/16 wrong |
| Honest answer time, median | 0.81 s | 0.83 s | 4.0 s |

- **Ordering** is closed by releasing in request order. On one connection that holds by construction; a separate localhost run showed v0 leaking floor(log2 k!) bits per batch of k concurrent requests (k = 2, 3, 4) with no errors.
- **Timing** is closed at this resolution only by a fixed release time, and that costs latency: every answer waits the whole budget, about 5x the honest median here.
- **Failures** stay open under every policy: 4 failures in 64 answers (6 %) carried 16 bits. A rare failure can carry many bits, because its position is the message.

### What fixed release leaves

A longer run (128 random bits, plus 128 honest requests as a control) asked whether the 2 ms of remaining jitter still follows the server:

| Run | Bit-1 minus bit-0 answer time | Permutation test |
|---|---|---|
| Server encoding bits | -0.66 ms (-0.73 and -0.60 in each half) | p < 0.0001 |
| Honest server, same labels | +0.19 ms | p = 0.13 |
| Server encoding bits, with a 5 ms spin before release | +0.08 ms | p = 0.31 |
| Honest server, with the spin | +0.09 ms | p = 0.20 |

A threshold decoder tuned on one half of the run and tested on the other gets 25 % and 28 % of bits wrong. Read as a binary symmetric channel (which assumes independent errors), that is about **0.14-0.19 bit per answer**, about 0.04 bit/s at a 4 s budget, against 0.69 bit/s for v0's timing channel. So fixed release cuts timing leakage by roughly 15-20x, not to zero. One hypothesis was that the server's delay changes how long the device idles before the release timer fires, which shifts its wake-up time. Waking 5 ms early and busy-waiting until the release time (`--spin-ms 5`) removes the average shift: the spin run looks like its honest control. A held-out decoder still got 34 % wrong on one half and 44 % on the other (the controls: 39-59 %), and only about 1 % of random relabellings do as well as 34 %, so a weaker residue may remain. A larger run is needed to settle it.

A second, independent measure (`observer/estimate.py`): the mutual information between the hidden bit and the answer time, with answer times cut into 4 equal-count bins, a small-sample bias correction, a shuffled-label null and a bootstrap interval. It was first checked on synthetic channels with known values (`tests/test_estimate.py`). Binning can only lose information, so it tends to undercount.

| Run (n = 128) | Mutual information, bit per answer | 95 % interval | p |
|---|---|---|---|
| Fixed release, server encoding bits | 0.20 | 0.09 to 0.35 | 0.0005 |
| Fixed release, honest server | about 0 | up to 0.06 | 0.75 |
| Fixed release with spin, server encoding bits | 0.016 | up to 0.12 | 0.13 |
| Fixed release with spin, honest server | about 0 | up to 0.06 | 0.41 |

The no-spin value agrees with the held-out decoder (0.14-0.19). With spin nothing is detected, but 128 answers can't rule out up to about 0.12 bit per answer.

## What these results don't show

- One device, one network, one sequential client (ordering used concurrent requests on one connection). Several clients, other loads and packet-level observation (sizes, TCP behaviour) are not measured yet.
- Every leakage cell is one run of 16 bits, except the 128-bit residual test.
- The release budget (4 s) was chosen by hand; the trade-off between budget, leakage and lost answers is not measured yet.
- Nothing here is a bound on what a cleverer encoder could achieve.
