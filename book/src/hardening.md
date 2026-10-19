# How hard the gate is to attack

> **In short**
> - The gate is built in layers: check only what it computed itself, accept only one exact message format, contain the proof checker, and run on a system with almost nothing else on it.
> - Of the 32 attacks tried so far, 22 are stopped, 2 are allowed on purpose, 6 still work or are only partly stopped, and 2 are out of scope.
> - Two reviews of the code found three real bugs, all fixed, each now covered by a test.

## The layers

**1. Check only what the gate computed itself.** The gate never trusts the server's account of the question or the model. It encodes the question itself, pins the approved model's files by hash, fixes the proof length, and reads the output only from the proven values. See [How the gate works](how-it-works.md#why-the-gate-encodes-the-question-itself).

**2. Accept one exact message format.** Every message between the user, the gate and the server has one fixed size and layout for the model in use. Anything else is rejected: oversized or truncated messages, wrong types, numbers that aren't finite. A fuzz test throws about 10,000 random and mutated messages at each parser.

**3. Contain the proof checker.** The checker is a separate program with hard limits: 256 MiB of memory, CPU time up to the deadline, no file growth, 64 open files, no crash dumps. It is killed if it runs past its deadline or prints too much. A checker that hits a limit counts as a failed check, never as a pass.

**4. Say nothing the server chose.** Users only ever see the proven output or one fixed error. The gate's log holds fixed words only, never text from the server.

**5. Run on a minimal system (v1, in progress).** The standard Pi system has ssh and other services listening. A stripped-down system built for the gate boots with no open network ports at all; the gate software goes onto it next.

**6. Lock the device (v2, planned).** Secure boot, so the Pi starts only a system we signed; signed updates that can't roll back to an older version.

## Attacks tried so far

| Outcome | Count | Examples |
|---|---|---|
| Stopped | 22 | Edited answers, a proof for a different question, a proof from another model, padded proofs, garbage or oversized messages, answering twice or late, malicious proofs that try to exhaust the checker |
| Allowed on purpose | 2 | The same question gets the same answer again (the server has no choice there); a gate set to stop after failures can be stopped by the server |
| Still works or partly stopped | 6 | Timing (much reduced), failing on purpose, never answering, keeping the gate busy, other services on the standard system, probing the gate's rules |
| Out of scope | 2 | Learning the model from many correct answers; physical attacks |

Every attack has a row in the [Attack catalogue](attacks.md) with its result and the test that covers it.

## Bugs found by review

Two independent reviews of the code (run with AI tools) found three real bugs, all in the code that holds answers back until it is their turn:

- an answer that was already checked could still leave after the gate had decided to stop answering;
- under a stalled event loop, a checked answer could leave after its own deadline;
- answers waiting their turn didn't count towards the gate's capacity limit, so the queue could grow without bound.

Each now has a test that fails on the old code (catalogue rows K30 and K31).

## What isn't hardened yet

The stripped system doesn't run the gate yet, nothing yet forces all of the server's traffic through the gate, and there is no secure boot or signed update path. See [Limitations and open questions](limitations.md).
