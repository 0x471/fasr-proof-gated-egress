# How hard the endpoint is to attack

> **In short**
> - The endpoint is built in layers: check only what it computed itself, accept only one exact message format, contain the proof checker, and run on a system with almost nothing else on it.
> - {{#include ../../docs/ATTACKS.md:summary}}
> - Code reviews found five real bugs in the code that holds answers back until their turn; all fixed, each now covered by a test that fails on the old code.

## The layers

**1. Check only what the endpoint computed itself.** The endpoint never trusts the server's account of the question or the model. It encodes the question itself, pins the approved model's files by hash, fixes the proof length, and reads the output only from the proven values. See [How the endpoint works](tour.md#how-a-proof-is-tied-to-the-question).

**2. Accept one exact message format.** Questions, forwarded questions and replies have one fixed size and layout for the model in use. The server's answer is a fixed part plus the proof, which may be up to 64 KiB on the wire and must then be exactly the pinned length. Anything else is rejected: oversized or truncated messages, wrong types, numbers that aren't finite. A fuzz test throws 10,500 random and mutated messages at each parser.

**3. Contain the proof checker.** The checker is a separate program with hard limits: 256 MiB of memory (enforced on Linux, which the Pi runs), CPU time up to the deadline, no file growth, 64 open files, no crash dumps. It is killed if it runs past its deadline or prints too much. A checker that hits a limit counts as a failed check, never as a pass.

**4. Say nothing the server chose.** Users only ever see the proven output or a fixed error (`unavailable`, or `bad_request` for a well-formed question the model can't answer); a malformed message just closes the connection. The endpoint's log uses a fixed vocabulary, never text from the server; it still records a counter, a hash of the question and the time taken, and which events happen and when still follow the server.

**5. Run on a minimal system (v1, in progress).** The standard Pi system has ssh and other services listening. A stripped-down system built for the endpoint boots with no open network ports at all; the endpoint software goes onto it next.

**6. Lock the device (v2, planned).** Secure boot, so the Pi starts only a system we signed; signed updates that can't roll back to an older version.

## Catalogued scenarios

{{#include ../../docs/ATTACKS.md:outcomes}}

Every scenario has a row in the [Attack catalogue](attacks.md) with its result and its evidence, or says that there is none yet.

## Bugs found by review

Independent reviews of the code (run with AI tools) found five real bugs, all in the code that holds answers back until it is their turn:

- an answer that was already checked could still leave after the endpoint had decided to stop answering;
- in arrival order, under a stalled event loop, a checked answer could leave after its own deadline;
- answers waiting their turn didn't count towards the endpoint's capacity limit, so the queue could grow without bound;
- with fixed-time release and the spin option, a later answer could overtake an earlier one whose deadline had nearly passed, so the server could choose the order of two failures again;
- with spin, a disconnect or halt that decided many answers at once chained one busy-wait per answer, stalling the endpoint for tens of milliseconds.

Each now has a test that fails on the old code (catalogue rows K30, K31, K33 and K35).

## What isn't hardened yet

The stripped system doesn't run the endpoint yet, nothing yet forces all of the server's traffic through the endpoint, and there is no secure boot or signed update path. See [Limitations and open questions](limitations.md).
