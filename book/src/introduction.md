# Proof-gated egress

> **In short**
> - A small computer (a Raspberry Pi) sits between an AI server and the outside world. It lets an answer out only if a cryptographic proof shows the approved model produced it from that exact question.
> - That stops a hacked server from changing **what** an answer says. It doesn't stop it from signalling through **when** answers leave, **in what order**, or **which ones fail**.
> - We measured those three signals on the real device, one client at a time. Release rules stopped the ordering and timing codes we tried (timing at a cost in speed, and with a small trace left); deliberate failures still get through.

This work was supported by ERA's Frontier AI Security Residency. It is work in progress (October 2026), not production security.

## Why this matters

The most valuable thing an AI lab owns is its model's weights. A hacked server, or a model acting against its owners, could try to smuggle weights out a little at a time, hidden inside ordinary answers. If every answer has to pass through a checkpoint that verifies a proof, the server loses the freedom to hide data in the content of its answers. The checkpoint is small and simple, so it is much easier to secure than the datacenter behind it.

```text
                          +---------+
  user  ---- question --> |  gate   | ---- question ---->  AI server
                          |  (Pi)   |                     (may be hacked)
        <--- answer ----- |         | <--- answer + proof
          or "unavailable"+---------+
```

We call the Pi running the checking software **the gate**.

## What we found

| Way to smuggle data out | Basic gate | Best gate so far | What it costs |
|---|---|---|---|
| Change what an answer says | Blocked by the proof | Blocked | Each check takes about 0.24 s on the Pi |
| Reorder answers | Leaks | **The ordering code we tried no longer gets through**: answers leave in the order questions arrived | A slow answer delays the ones behind it |
| Delay answers | Leaks | **Much reduced**: every answer leaves at a set time; a small trace remains | Answers take 4 s instead of 0.8 s |
| Fail chosen answers on purpose | Leaks | **Still leaks** | Open problem |

These are results for one tiny, deterministic test model and the encoders we tried, not limits on what an attacker could do. Details and numbers are in [What a hacked server can still leak](leakage.md).

## Status

| Version | What it is | Status |
|---|---|---|
| v0 | The checking software on the Pi's standard operating system | Done and measured |
| v1 | Stricter software on a stripped-down system with nothing else running | Software done and measured; the stripped system boots, the software isn't on it yet |
| v2 | Fixed release times, secure boot, signed updates | Fixed release done; the rest planned |

## Where to start

- **5 minutes:** this page, then [What a hacked server can still leak](leakage.md).
- **Assessing the security:** [How hard the gate is to attack](hardening.md), then the [Attack catalogue](attacks.md) and [Threat model](threat-model.md).
- **Checking the numbers:** [Measurements](results.md) and [Reproduce the results](reproduce.md).
- **New to the terms:** the [Glossary](glossary.md).

The code is at [github.com/0x471/fasr-proof-gated-egress](https://github.com/0x471/fasr-proof-gated-egress).
