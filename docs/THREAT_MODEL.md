# Threat model

Living document: started in W2, updated after each version and each adversarial-testing session, final in W7. Status as of 19 Oct 2026: v0 measured on the Pi, the v1 software (including fixed release) measured on the Pi on the v0 OS; the v1 and v2 images are not built yet.

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
| A2 The served model acting as a rogue agent | The same as A1, from inside the inference stack, and patient. Gigurtsis's loss-of-control matrix lists weight exfiltration (technique 5.4.1) as a step a rogue agent takes, and for closed-weight models a prerequisite for replication. In scope: a model that compromises the inference stack or controls timing and failures. Not in scope: secret-bearing content that the approved model itself computes; the gate must release that, since it is a correct answer (see 4.1) | Its own weights out, a few bits at a time |
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
- **Complete mediation:** every path from the AI server to the outside crosses the gate. In v0 this is not enforced: the stock OS has SSH, mDNS, Wi-Fi and its own outbound traffic, and nothing stops the server from having another route out. The v1 image (two wired ports, nothing forwarded, no other services) is the first version where this can be claimed, and it has to be tested from both sides.
- **Logs:** where the log goes and who reads it is not yet defined. If an outsider can read it, the log is an output channel (section 6).
- The proof binds the encoded request and the outputs (docs/BINDING_SPEC.md), with one pinned model and fixed parameters.
- Physical access is out of scope (Yogev, 14 Oct): no power, EM, acoustic or thermal channels, no tampering.
- What ordinary, correct outputs reveal about the model is out of scope (see 4.1).

### 4.1 Extraction through correct answers: out of scope, to confirm

A proof shows that `y` came from the pinned model on this `x`. It does nothing against an attacker who learns the model from many correct answers. For the tiny affine + ReLU test model, a handful of chosen requests that keep the ReLUs active can recover the quantised weights by linear algebra, and every proof verifies. The plan puts this out of scope; it is on the agenda for the mentor call (20 Oct) to confirm, or to add a gate policy such as query-rate limits.

For real LLMs, any freedom the proof doesn't pin (sampling seed, temperature, unproven layers) becomes a channel with valid proofs. The test model is deterministic; who chooses the seed is an open question for the mentor.

## 5. v0 attack surface (measured on the Pi, 11, 13 and 16 Oct)

- Raspberry Pi OS Lite (Debian 13), 633 installed packages, 13 to 15 running system services (the count depends on what has run since boot: 15 on 11 Oct, 13 on 16 Oct).
- Listening: sshd (TCP 22) and avahi (mDNS on UDP 5353 plus two random UDP ports), on IPv4 and IPv6. The gate adds its own listener on link 1.
- Verifier: EZKL's official 36 MB CLI (it also contains the prover and setup code), run by `pge/cli.py`.
- Updates: files copied over SSH.

`device/measure.sh` records the same local items for every version (packages, running services, processes, listening sockets, hashes of the boot config and of any files passed to it). A tool that fails makes the run invalid instead of reporting zero. It is a local snapshot only: what is reachable is measured separately, from outside. From the laptop on 16 Oct: TCP 22 (ssh) and 8700 (the gate), UDP 5353 (mDNS). The first v1 development image (16 Oct, no gate running yet): 0 of 65,535 TCP ports open, the top 100 UDP ports closed, no IPv6.

## 6. Side channels a compromised server controls

Status as of 19 Oct in the last column. "Open" means the channel is not closed by any software version yet.

| Channel | How A1/A2 uses it | Planned defence (version) | v0 |
|---|---|---|---|
| Timing | Delay each answer by a chosen amount | Fixed release time with a verification budget (`--release fixed`, 19 Oct) | **Reduced, not closed (K8):** v0 and arrival order leak 16/16 bits on the Pi; under fixed release the 1 s-code decoder fails, but a 0.66 ms shift follows the server's delays (a held-out decoder gets 25-28 % wrong, n = 128); waking early and busy-waiting (`--spin-ms 5`) removes the mean shift; a 512-answer pre-registered repeat was inconclusive (held-out decoder 44 % wrong at best, p = 0.041), so any residue is small but not shown to be gone. Costs every answer the full budget (4 s here) |
| Ordering | Answer concurrent requests in a chosen order | Release in arrival order (v1); time slots (v2) | Closed in the v1 software for the order admitted answers are written on the gate, failures included (`SERVICE_SPEC.md` section 4). Not covered: `busy` and halted answers leave at once, log timing, and the order separate TCP connections are seen in. v0 released answers as checks finished |
| Whether an answer arrives | Fail on purpose: bad proofs, drops, resets, missed deadlines | One generic error; failure threshold and response (v1, v2). With timing fixed, still up to 1 bit per request, and a sparse failure's position carries log2 N bits per block of N | **Open, shown on the Pi (K32):** 4 failures in 64 answers carried 16 bits under every release policy. One server-independent error string; failures are counted, and `halt_after` stops all releases (pending and held ones included), off by default |
| Sizes and packet metadata | Response length, TCP segmentation, header fields | The device builds every outgoing packet itself; fixed-size responses (v1, v2) | Partly: in the v1 software every reply frame has one size and the server can't choose the number of output values; the OS's TCP stack (segmentation, headers) is still stock |
| Answer content | Pick among valid answers or encodings | One pinned model, the device's own encoding, canonical values, `y` decoded from verified values, proof never released | **Closed for the test bundle:** binding and service tests |
| Verification time | Proofs that are slow to verify or crash the verifier | Fixed proof size; verification budget; separate worker with a deadline | Partly: CLI killed at the deadline (`pge/cli.py`). With arrival or immediate release the answer time still depends on it; with fixed release it doesn't, except for the residual shift (K8), and a check that runs out of budget is `unavailable` |
| Device outputs | Smuggle data into logs, alerts or errors | Logs hold hashes and results only, exported one way; one generic error text | Partly: clients only see two error strings. In the v1 software log records hold fixed words, the device's `seq`, the client's request hash, `ms` and (for a halt) the failure count (no error text, no proof hash), to stderr or a file for a log partition; the number and timing of log events still follow the server. Who reads the log is undefined |
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
| Malformed or oversized frames | Parser | Fixed-size binary frames (v1 software); re-encoding an accepted frame gives the same bytes (fuzzed, including split streams, in `tests/test_protocol.py`) |
| Exploit the verifier or the network stack | Device software | The verifier runs with Rayon set to one thread and per-process limits (memory on Linux, CPU, no file growth, few files; v1 software); the minimal image and a separate verifier user come with the v1 image |
| Swap the bundle on disk | Bundle | Pins checked at load and before every check; same-user swap between check and use still possible |
| Change the device's software | Boot chain | Not addressed in v0; secure boot in v2 |
| Persuade an operator to approve a bad update or model (Gigurtsis 5.2.2, 5.6.8) | Update process | Not addressed in v0; separate keys and 2-of-3 update signing in v2 |
| Learn the device's policy by probing (Gigurtsis 5.1.4) | Policy | Generic errors; failure alerts planned |

## 8. Mapping to MITRE ATLAS

ATLAS release 2026.09 (`mitre-atlas/atlas-data`, `dist/v6/ATLAS-2026.09.yaml`: 16 tactics, 208 techniques, 40 mitigations, 73 case studies). ATLAS describes adversaries attacking AI systems, mostly from outside or through the supply chain. Our main attacker is the inference host itself (A1, A2) trying to get data out through a device that only releases verified answers. **We found no ATLAS technique for leaking through the timing, order or failure pattern of valid outputs from a compromised inference host** (searched names and descriptions for covert, side channel, timing, latency, steganography). The closest are the generic Exfiltration via Cyber Means, which defers to ATT&CK, and the goal AI Intellectual Property Theft. Gigurtsis's loss-of-control matrix (technique 5.4.1) is the closer fit and is cited per row in `ATTACKS.md`.

| ATLAS | Name | Our rows | Note |
|---|---|---|---|
| AML.T0048.004 | AI Intellectual Property Theft (Impact) | the goal of A1/A2 (section 1) | the asset is the weights |
| AML.T0025 | Exfiltration via Cyber Means | K8, K18, K28, K19, section 6 | generic; our channels are a gap in ATLAS |
| AML.T0024.002 | Extract AI Model | K26 | out of scope |
| AML.T0018 | Manipulate AI Model | K3 | a proof under another circuit's key is rejected (pinned vk) |
| AML.T0010.001 / .003 | AI Supply Chain Compromise: AI Software / Model | section 7 (substituted verifier binary, swapped bundle) | digest pins; v1 bakes both into the image |
| AML.T0029 | Denial of AI Service | K16, K17, K23 | partly by design (halt, busy) |
| AML.T0034.001 | Resource-Intensive Queries | K15, K29 | analogous: crafted proofs rather than queries |

No dedicated technique found in this release (by the search above) for: request binding and proof-format attacks on the verifier (K1-K7, K30, K31, K33), release timing, order and failure position, and packet metadata. This is a statement about the taxonomy's granularity, not proof that nobody has studied them.

ATLAS mitigations we use: AML.M0014 Verify AI Artifacts (bundle pins), AML.M0013 Code Signing (v2 signed updates, secure boot), AML.M0036 Limit AI Workload Resource Consumption (verifier limits, deadlines), AML.M0004 Limit AI Service Query Volume and Rate (`max_pending`, `max_clients`), AML.M0024 AI Telemetry Logging (fixed-word logs; here the log is itself a channel, section 6), AML.M0035 AI Red Team (W6 sessions).

## 9. References

- Project plan, side-channel table and three versions (shared with mentor and RM).
- Gigurtsis, *From Discovery to Persistence: A Loss-of-Control Threat Matrix for Agentic AI* (2026), https://github.com/bgigurtsis/AgenticLoCThreatMatrix
- Rinberg et al., *Verifying LLM Inference to Detect Model Weight Exfiltration* (2025), https://arxiv.org/abs/2511.02620
- Lampson, *A Note on the Confinement Problem* (1973); Murdoch and Lewis, *Embedding Covert Channels into TCP/IP* (2005).
- MITRE ATLAS, release 2026.09, https://atlas.mitre.org/ (data: https://github.com/mitre-atlas/atlas-data)
