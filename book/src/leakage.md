# What a hacked server can still leak

> **In short**
> - A proof fixes what an answer says, but a hacked server still controls **when** it answers, **in what order**, and **which answers fail**. Each of these can carry hidden data.
> - On the real device, with one client at a time: releasing answers in arrival order stopped the ordering code we tried, and releasing every answer at a fixed time stopped the timing code we tried, at the cost of speed. A small leftover timing trace is described below.
> - Deliberate failures leaked under every release rule we tried. This is the main open problem.

## Why a proof isn't enough

Think of the endpoint as a strict customs officer who checks that every parcel's contents match the paperwork. A smuggler who can't change the contents can still send a message by choosing **when** parcels arrive, **in what order**, or **which ones get rejected**. Anyone watching from outside who knows the code can read the message back.

## How we tested it

We made the server hide a known message (16 bits) in its behaviour, and had an observer outside the endpoint try to read it back with a fixed decoder. If the observer recovers all 16 bits, that channel is open. If the decoder fails, it didn't recover that message in that run; a different encoder or a smarter observer might still get something through, which is why the leftover trace was tested separately (below).

We did this on the real Raspberry Pi for three ways of signalling and three release rules, with a 4-second budget for the fixed-time rule, one client at a time.

## Results

{{#include results.md:matrix}}

What this means:

- **Order is easy to protect.** Sending answers back in the order the questions arrived removes the server's choice: the observer decoded the same bits whatever the server planted.
- **Timing costs speed to protect.** The endpoint aims to send every answer 4 seconds after its question, so a delay chosen by the server no longer shows in a simple way. But every user now waits the whole budget, even when the answer was ready much sooner (the last row above).
- **A small timing trace was left.** {{#include results.md:no-spin-trace}} {{#include results.md:spin-verdict}}
- **Failures stay open.** The server can always make an answer fail by sending a bad proof, and which answers fail is itself the message: choosing one failure in every 16 answers sends 4 bits per 16 answers. {{#include results.md:failure-leak}}

## Closing the failure channel

No release rule can stop deliberate failures, because a failure looks like a real fault. What the endpoint can do is **limit how many failures it tolerates**, and count every failure, including answers the server lets miss their deadline:

- Over a window of N answers, if the observer only sees which answers failed and the endpoint allows at most F failures, the server can choose among at most C(N,0) + C(N,1) + ... + C(N,F) patterns, so it can send at most log2 of that many bits per window.
- Stopping the endpoint at the first failure in a window of N answers leaves N + 1 outcomes (no failure, or a failure at one of N positions), so at most log2(N + 1) bits per window, but only if someone decides when the endpoint restarts; and it lets the server switch the endpoint off whenever it wants.

These bounds hold only for that observation (failures only) and that policy; timing, logs and restarts need their own accounting. Measuring the trade-off, leakage against how often an honest endpoint would be stopped by real faults, is the next step.

## What this doesn't show

- These are the rates **one particular** hiding method achieved, not the most any method could achieve.
- One device, one network, one client at a time (the ordering test used several questions at once on one connection). With several clients, the fixed-time rule and its spin interact in ways not yet measured.
- Packet-level signals (packet sizes, network timing details) haven't been measured yet.

All numbers and their conditions are in [Measurements](results.md).
