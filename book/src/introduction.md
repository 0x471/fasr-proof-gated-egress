# Proof-gated egress

A small, off-the-shelf device (a Raspberry Pi 4) that sits between an AI server and the outside world and releases an answer only if it carries a valid zero-knowledge proof that the approved model produced it from that exact request. This book collects the design, the threat model, every attack tried so far and what was measured.

> **Work in progress (October 2026).** Nothing here is production security. Every claim is limited to what the named test or measurement shows, and the measurements are achieved rates for specific attacks, not upper bounds.

This work was supported by ERA's Frontier AI Security Residency.

## The problem

A compromised AI server, or the model it serves, may try to get its weights out. The obvious route out is the answers themselves. If every answer must come with a proof that it is the approved model's output on the request it was asked, a device at the boundary can check the proof and block anything else. Securing the boundary then means securing one small device instead of a datacenter.

A valid proof removes the server's choice over **what** an answer says. It says nothing about **when** the answer arrives, **whether** it arrives, in **what order** answers arrive, or what the packets look like. A server that can't change the content can still signal through those. Measuring and closing those channels is half of this project; hardening the device itself is the other half.

## What has been shown so far

| Question | Answer so far | Where |
|---|---|---|
| Can a Pi check a proof per answer? | Yes: about 100 ms per check with EZKL's official CLI, 239 ms with the full gate path and resource limits | [Results](results.md) |
| Does the device bind each proof to its own request? | Yes, for the test circuits: it encodes the request itself, pins the model's settings, key and setup, and decodes the output only from verified values | [Request binding](binding-spec.md) |
| Can a compromised server leak through the order of answers? | Yes, unless answers leave in request order; then no | [Results](results.md#release-policies-on-the-pi) |
| Through answer timing? | Yes, fully, unless every answer leaves at a fixed time; then a small residue remains (about 0.15-0.2 bit per answer) and every answer waits the whole budget | [Results](results.md#release-policies-on-the-pi) |
| Through deliberate failures? | Yes, under every release policy tried: one failure per 16 answers carried 4 bits | [Results](results.md#release-policies-on-the-pi) |
| What else is open? | The stripped and hardened device images, signed updates, secure boot, packet-level channels, a real capacity estimate | [Limitations](limitations.md) |

## Versions of the device

| Version | What it is | Status |
|---|---|---|
| v0 baseline | The verifier as a normal program on standard Raspberry Pi OS | Measured on the Pi |
| v1 stripped | A minimal Buildroot system that runs only the gate; two wired ports; fixed-size binary messages; resource limits on the verifier | Software done and measured on the Pi (on the v0 OS); the image is in progress |
| v2 hardened | Fixed release time, signed and rollback-protected updates, secure boot | Fixed release done in software; the rest planned |

## How to read this book

- [How it works](how-it-works.md): the device, the request path and the three release policies.
- [Results](results.md): every measurement, with its conditions and what it does not show.
- [Threat model](threat-model.md) and [Known-attacks catalogue](attacks.md): the security analysis; every attack has a result and the test that covers it.
- [Gate service](service-spec.md) and [Request binding](binding-spec.md): the exact behaviour, as specified for the code.
- [Reproduce the results](reproduce.md): commands for the tests and the Pi experiments.

The code is at [github.com/0x471/fasr-proof-gated-egress](https://github.com/0x471/fasr-proof-gated-egress).
