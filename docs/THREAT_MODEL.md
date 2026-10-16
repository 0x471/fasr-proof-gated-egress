# Threat model v0

Living document: started in W2, updated after each version and each adversarial-testing session, final in W7. v0 is the baseline, so most rows below say what is still open.

## 1. What the device protects

| Asset | Why it matters |
|---|---|
| Model weights (and other secrets) on the AI server | The thing an attacker wants out. The device can't see them; it can only limit what leaves |
| The pinned bundle on the device (settings, vk, SRS) and the domain file | Swapping any of them changes what counts as a valid answer |
| The device's code and configuration | Whoever controls them controls what is released |
| The release decision and its timing | Content is pinned by the proof; when, whether and how an answer is released is the remaining channel |
| The device's own outputs (logs, errors, alerts) | A second way out if they carry server-chosen data |

## 2. Attackers

| Attacker | Controls | Wants |
|---|---|---|
| A1 Compromised AI server | Everything on the server side of the device: outputs, proofs, timing, which requests fail, the TCP connection | Move secret bits to someone outside, through answers that still pass the gate |
| A2 The served model acting as a rogue agent | The same as A1, from inside the inference stack, and patient. Gigurtsis's loss-of-control matrix lists weight exfiltration (technique 5.4.1) as a step a rogue agent takes, and for closed-weight models a prerequisite for replication | Its own weights out, a few bits at a time |
| A3 Outside receiver | One or more ordinary client connections | Decode what A1 or A2 hides; probe the device (invalid requests, timing of errors) |
| A4 Network attacker | Either link, if not physically protected | Inject, drop or reorder frames; impersonate the server or a client |
| A5 Holder of a signing key (from v2) | An update, boot or model-approval key | Install a device that releases more |

A1 and A3 working together is the main case: the server encodes, the receiver decodes.

## 3. Trust boundaries

```
outside clients (A3)  |  device  |  AI server (A1, A2)
        ----- link 1 -----   ----- link 2 -----
```

- The device trusts nothing it receives on either link.
- The device opens link 2 itself and listens only on link 1.
- v0 runs on stock Raspberry Pi OS, so the device's own software is trusted without being minimal (see section 5).

## 4. Assumptions

- The proof system is sound and EZKL 23.0.5's verifier implements it correctly. Not tested here; checked by others.
- The SRS file is the pinned one, and the ceremony behind it was sound. A pin proves byte identity only.
- The pinned vk and settings were built from an honest, complete circuit for the claimed model, on a build machine that wasn't compromised. The device can check that a proof matches the vk, not that the vk constrains what we think it does.
- The EZKL CLI binary matches its pinned release digest; that trusts zkonduit's release process.
- The proof binds the encoded request and the outputs (docs/BINDING_SPEC.md), with one pinned model and fixed parameters.
- Physical access is out of scope (Yogev, 14 Oct): no power, EM, acoustic or thermal channels, no tampering.
- What ordinary, correct outputs reveal about the model is out of scope (see 4.1).

### 4.1 Extraction through correct answers: out of scope, to confirm

A proof shows that `y` came from the pinned model on this `x`. It does nothing against an attacker who learns the model from many correct answers. For the tiny affine + ReLU test model, a handful of chosen requests that keep the ReLUs active can recover the quantised weights by linear algebra, and every proof verifies. The plan puts this out of scope; it is on the agenda for the mentor call (20 Oct) to confirm, or to add a gate policy such as query-rate limits.

For real LLMs, any freedom the proof doesn't pin (sampling seed, temperature, unproven layers) becomes a channel with valid proofs. The test model is deterministic; who chooses the seed is an open question for the mentor.

## 5. v0 attack surface (measured on the Pi, 11 and 13 Oct)

- Raspberry Pi OS Lite (Debian 13), 633 installed packages, 15 running system services.
- Listening: sshd (TCP 22) and avahi (mDNS on UDP 5353 plus two random UDP ports), on IPv4 and IPv6. The gate adds its own listener on link 1.
- Verifier: EZKL's official 36 MB CLI (it also contains the prover and setup code), run by `pge/cli.py`.
- Updates: files copied over SSH.

`device/measure.sh` records the same items for every version, with hashes of the image, config and tools.

## 6. Side channels a compromised server controls

Status for v0 in the last column. "Open" means the channel is not closed in this version.

| Channel | How A1/A2 uses it | Planned defence (version) | v0 |
|---|---|---|---|
| Timing | Delay each answer by a chosen amount | Fixed release time with a verification budget (v2) | **Open, shown:** `test_v0_leaks_bits_through_timing` recovers 7 of 7 bits with 0.5 s slots |
| Ordering | Answer concurrent requests in a chosen order | Release in arrival order (v1); time slots (v2) | Open: answers are released as checks finish |
| Whether an answer arrives | Fail on purpose: bad proofs, drops, resets, missed deadlines | One generic error; failure threshold and response (v1, v2). With timing fixed, still up to 1 bit per request | Partly: one generic error; failures are counted and `halt_after` can stop the device, off by default |
| Sizes and packet metadata | Response length, TCP segmentation, header fields | The device builds every outgoing packet itself; fixed-size responses (v1, v2) | Partly: the device writes its own frames, but their length depends on `y`, and the OS's TCP stack is stock |
| Answer content | Pick among valid answers or encodings | One pinned model, the device's own encoding, canonical values, `y` decoded from verified values, proof never released | **Closed for the test bundle:** binding and service tests |
| Verification time | Proofs that are slow to verify or crash the verifier | Fixed proof size; verification budget; separate worker with a deadline | Partly: CLI killed at the deadline (`pge/cli.py`), but answer time still depends on it |
| Device outputs | Smuggle data into logs, alerts or errors | Logs hold hashes and results only, exported one way; one generic error text | Partly: clients only see two error strings; logs go to stderr and include verifier detail text |
| Busy answers | Hold requests open to fill `max_pending`, so other clients get `unavailable` | Fixed slots (v2) | Open: depends on load |

## 7. Other attacks to test

| Attack | Target | Current coverage |
|---|---|---|
| Replay an old answer | Pending table | One answer per `seq`; tested (`test_late_answer_is_dropped_and_answered_once`, `test_replayed_answer_only_passes_for_the_same_request`) |
| Proof for another request or model | Binding | Tested: other request, other circuit, tampered output; EZKL also refuses wrong instance layouts (`test_instance_layout_is_enforced_by_ezkl`) |
| Bytes appended to a valid proof | Proof format | EZKL accepts them (`test_trailing_proof_bytes_still_verify`), so the gate pins the exact proof length (`Policy.proof_bytes`); tested |
| Inject, replay or reorder frames on either link | Links (A4) | No transport security in v0; replay is limited to one answer per `seq` |
| A substituted verifier binary that accepts everything | Supply chain | Digest pin on download; v1 bakes the binary into the image |
| Make the device unreliable until operators bypass it | Availability | Failures are logged and counted; nothing yet makes a bypass visible |
| Malformed or oversized frames | Parser | Strict framing and parsing, tested; fuzzing planned for v1 |
| Exploit the verifier or the network stack | Device software | Not addressed in v0 (stock OS, 36 MB CLI); v1 minimal image |
| Swap the bundle on disk | Bundle | Pins checked at load and before every check; same-user swap between check and use still possible |
| Change the device's software | Boot chain | Not addressed in v0; secure boot in v2 |
| Persuade an operator to approve a bad update or model (Gigurtsis 5.2.2, 5.6.8) | Update process | Not addressed in v0; separate keys and 2-of-3 update signing in v2 |
| Learn the device's policy by probing (Gigurtsis 5.1.4) | Policy | Generic errors; failure alerts planned |

## 8. References

- Project plan, side-channel table and three versions (shared with mentor and RM).
- Gigurtsis, *From Discovery to Persistence: A Loss-of-Control Threat Matrix for Agentic AI* (2026), https://github.com/bgigurtsis/AgenticLoCThreatMatrix
- Rinberg et al., *Verifying LLM Inference to Detect Model Weight Exfiltration* (2025), https://arxiv.org/abs/2511.02620
- Lampson, *A Note on the Confinement Problem* (1973); Murdoch and Lewis, *Embedding Covert Channels into TCP/IP* (2005).
