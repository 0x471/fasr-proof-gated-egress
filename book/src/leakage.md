# What a hacked server can still leak

> **In short**
> - A proof fixes what an answer says, but a hacked server still controls **when** it answers, **in what order**, and **which answers fail**. Each of these can carry hidden data.
> - On the real device, with one client at a time: releasing answers in arrival order stopped the ordering code we tried, and releasing every answer at a fixed time stopped the timing code we tried, at the cost of speed. A small timing trace remained.
> - Deliberate failures leaked under every release rule we tried. This is the main open problem.

## Why a proof isn't enough

Think of the gate as a strict customs officer who checks that every parcel's contents match the paperwork. A smuggler who can't change the contents can still send a message by choosing **when** parcels arrive, **in what order**, or **which ones get rejected**. Anyone watching from outside who knows the code can read the message back.

## How we tested it

We made the server hide a known message (16 bits) in its behaviour, and had an observer outside the gate try to read it back with a fixed decoder. If the observer recovers all 16 bits, that channel is open. If the decoder fails, that particular way of signalling no longer works; a different encoder or a smarter observer might still get something through, which is why the leftover trace was tested separately (below).

We did this on the real Raspberry Pi for three ways of signalling and three release rules, with a 4-second budget for the fixed-time rule, one client at a time.

## Results

| The server signals by | Immediate release | Arrival order | Fixed time |
|---|---|---|---|
| Delaying answers | Read back (16 of 16 bits) | Read back (16 of 16) | Not read back (12 of 16 wrong) |
| Reordering answers | Read back (16 of 16) | Not read back (same bits whatever was planted) | Not read back |
| Failing chosen answers | Read back (16 of 16) | Read back (16 of 16) | Read back (16 of 16) |
| **Time per answer** | 0.8 s | 0.8 s | **4.0 s** |

What this means:

- **Order is easy to protect.** Sending answers back in the order the questions arrived removes the server's choice: the observer decoded the same bits whatever the server planted.
- **Timing costs speed to protect.** The gate aims to send every answer 4 seconds after its question, so a delay chosen by the server no longer shows in a simple way. But every user now waits 4 seconds, even when the answer was ready in under one.
- **A small timing trace was left.** With the fixed time, answers still left about 0.66 ms earlier or later depending on what the server did, enough for a decoder to get about three bits in four right. Waking a few milliseconds early and waiting precisely removed the average difference. A 512-answer repeat with a protocol fixed in advance was inconclusive: the best decoder got about 56 % right (p = 0.041, short of the 0.01 the protocol required to call it a leak). So any trace left is small, but not shown to be gone.
- **Failures stay open.** The server can always make an answer fail by sending a bad proof, and which answers fail is itself the message: choosing one failure in every 16 answers sent 4 bits per 16 answers. Here only 4 of 64 answers failed, yet all 16 bits got through.

## Closing the failure channel

No release rule can stop deliberate failures, because a failure looks like a real fault. What the gate can do is **limit how many failures it tolerates**, and count every failure, including answers the server lets miss their deadline:

- Over a window of N answers, if the observer only sees which answers failed and the gate allows at most F failures, the server can choose among at most C(N,0) + C(N,1) + ... + C(N,F) patterns, so it can send at most log2 of that many bits per window.
- Stopping the gate at the first failure in a window of N answers leaves N + 1 outcomes (no failure, or a failure at one of N positions), so at most log2(N + 1) bits per window, but only if someone decides when the gate restarts; and it lets the server switch the gate off whenever it wants.

These bounds hold only for that observation (failures only) and that policy; timing, logs and restarts need their own accounting. Measuring the trade-off, leakage against how often an honest gate would be stopped by real faults, is the next step.

## What this doesn't show

- These are the rates **one particular** hiding method achieved, not the most any method could achieve.
- One device, one network, one client at a time (the ordering test used several questions at once on one connection). With several clients, the fixed-time rule and its spin interact in ways not yet measured.
- Packet-level signals (packet sizes, network timing details) haven't been measured yet.

All numbers and their conditions are in [Measurements](results.md).
