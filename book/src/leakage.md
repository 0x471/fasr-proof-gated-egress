# What a hacked server can still leak

> **In short**
> - A proof fixes what an answer says, but a hacked server still controls **when** it answers, **in what order**, and **which answers fail**. Each of these can carry hidden data.
> - On the real device, releasing answers in arrival order closes the order channel, and releasing every answer at a fixed time almost closes the timing channel, at the cost of speed.
> - Deliberate failures still leak under every release rule we tried. This is the main open problem.

## Why a proof isn't enough

Think of the gate as a strict customs officer who checks that every parcel's contents match the paperwork. A smuggler who can't change the contents can still send a message by choosing **when** parcels arrive, **in what order**, or **which ones get rejected**. Anyone watching from outside who knows the code can read the message back.

## How we tested it

We made the server hide a known message (16 bits) in its behaviour, and had an observer outside the gate try to read it back. If the observer recovers all 16 bits, the channel is wide open. If it gets about half of them wrong, it is doing no better than guessing, and that way of signalling no longer works.

We did this on the real Raspberry Pi for three ways of signalling and three release rules, with a 4-second budget for the fixed-time rule.

## Results

| The server signals by | Immediate release | Arrival order | Fixed time |
|---|---|---|---|
| Delaying answers | Leaks (16 of 16 bits) | Leaks (16 of 16) | Guessing (12 of 16 wrong) |
| Reordering answers | Leaks (16 of 16) | Closed | Closed |
| Failing chosen answers | Leaks (16 of 16) | Leaks (16 of 16) | Leaks (16 of 16) |
| **Time per answer** | 0.8 s | 0.8 s | **4.0 s** |

What this means:

- **Order is easy to close.** Sending answers back in the order the questions arrived removes the server's choice.
- **Timing costs speed to close.** If every answer leaves exactly 4 seconds after its question, a delay chosen by the server no longer shows. But every user now waits 4 seconds, even when the answer was ready in under one.
- **A tiny timing trace was left.** With the fixed time, answers still left about 0.66 ms earlier or later depending on what the server did: enough for roughly 0.15 to 0.2 bits per answer. Measured per second, that is about 15 to 20 times less than without the fixed time. The likely cause is the Pi waking up from idle at slightly different speeds. A fix (waking a few milliseconds early and waiting precisely) is in testing and looks promising.
- **Failures stay open.** The server can always make an answer fail by sending a bad proof, and which answers fail is itself the message: choosing one failure in every 16 answers sends 4 bits. Here only 4 of 64 answers failed, yet all 16 bits got through.

## Closing the failure channel

No release rule can stop deliberate failures, because a failure is indistinguishable from a real fault. What can be done is to **limit how many failures the gate tolerates**:

- If at most F of every N answers may fail, the server can only choose among a limited number of failure patterns, which caps how many bits it can send per N answers.
- Stopping the gate after the first failure caps the leak at the position of that one failure, but it also lets the server switch the gate off whenever it wants.

Working out and measuring that trade-off, leakage against how often an honest gate would be stopped by real faults, is the next step.

## What this doesn't show

- These are the rates **one particular** hiding method achieved, not the most any method could achieve.
- One device, one network, one user at a time (the ordering test used several questions at once on one connection).
- Packet-level signals (packet sizes, network timing details) haven't been measured yet.

All numbers and their conditions are in [Measurements](results.md).
