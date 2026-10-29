# How the endpoint works

> **In short**
> - The endpoint checks each answer's proof against the question **it** received, never against what the server says the question was.
> - It sends back either the proven answer or a fixed error, never anything the server wrote, and it decides itself when and in what order answers leave.
> - This page is the whole system on one page: every part with a diagram, the code that implements it and the evidence behind it. Every number here comes from [Measurements](results.md), which is the reference.

## The pieces

Two machines and two network links. Today the laptop plays both the AI server and the outside observer, and the Pi runs the endpoint. The endpoint listens only on the outside link and dials out to the server itself, so it has no listener for the server to connect to.

![The laptop runs the observer and the AI server; the Pi runs the endpoint service, which reads a pinned bundle, runs the EZKL CLI in a limited child process per check, and writes fixed-word logs](diagrams/architecture.svg)

| Piece | File | Job |
|---|---|---|
| Endpoint service | `pge/service.py` | Admits questions, forwards them, checks answers, decides when replies leave |
| Message format | `pge/protocol.py` | Fixed-size binary frames; strict parsing |
| Binding | `pge/binding.py`, `pge/bundle.py`, `pge/domain.py` | Encodes questions like EZKL, pins the model, decodes proven outputs, refuses impossible questions |
| Checker | `pge/cli.py`, `pge/proc.py`, `pge/verdict.py` | Runs EZKL's command-line tool under limits and turns its output into a verdict |
| Server | `prover/server.py` | Proves each question with EZKL; can misbehave on purpose (tamper, replay, delay, hide bits) |
| Observer | `observer/client.py` | Sends questions, records each reply and its arrival time, decodes hidden bits |

Today both links share one Ethernet cable, so the separation between the server side and the outside side is only by address. The v1 image gives the Pi a second wired port so the two sides are physically separate and nothing is forwarded between them.

## The life of one question

The server never gets to say what the question was. The endpoint rebuilds that part of the proof itself, so the server only supplies outputs and a proof, and they count only if the proof over the endpoint's question and those outputs verifies.

![Sequence: the user sends a question; the endpoint parses and encodes it; a malformed message closes the connection and an impossible question gets bad_request, neither reaching the server; otherwise the endpoint forwards it, takes the first answer for that sequence number, verifies it with the EZKL CLI, and replies with the proven y or unavailable](diagrams/question.svg)

| Step | Code | Evidence |
|---|---|---|
| Read and parse the question | `serve_client` and `parse_request` | `test_client_violation_closes_the_connection` |
| Encode, check, admit or refuse | `submit` | `test_bad_requests_never_reach_the_server` |
| Accept the first answer only | `on_answer` | `test_duplicate_answer_gives_one_reply`, `test_late_answer_is_dropped_and_answered_once` |
| Build instances, verify, decide | `_check` | `test_proof_for_another_request_is_blocked` |
| Decide when the reply leaves | `_release` and `_pump` | [When answers leave](#when-answers-leave) |

- **Malformed frames:** a bad frame from a user closes that user's connection; a bad frame from the server drops the server link and fails every pending question, because a broken message can't be tied to one question.
- **Deadline:** checked before a check starts and again when the result comes back, so a late result can't slip out.
- **One decision per question:** late, duplicate and unknown answers from the server are counted and dropped, never sent to a user.

## How a proof is tied to the question

A valid EZKL proof only says "this circuit turned these public input values into these public output values". Binding makes it a proof about **this** question, **this** model and **this** reading of the output, by never taking any of the three from the server.

![The endpoint encodes the question itself and checks the server's claimed outputs are canonical field elements; together they form the public instances; EZKL verifies the 3,072-byte proof against them with the pinned key, settings and setup file; valid proofs are decoded and released, invalid ones give unavailable](diagrams/binding.svg)

**The five rules**

1. **One pinned model.** The endpoint loads one bundle: `settings.json`, the verification key `vk.key` and the setup file `kzg.srs`, each pinned by SHA-256 and re-checked before every verify. The server can't pick another model or key.
2. **The endpoint encodes the question.** It computes the input values exactly as EZKL does, so a proof made for any other encoded question fails.
3. **Outputs are read only from verified values.** The server's outputs are released only if the proof over the endpoint's inputs and those exact outputs verifies. The endpoint never uses an answer the server states, and never reads the proof file's unbound fields (`pretty_*`, `timestamp`, `hex_proof`).
4. **The proof length is pinned.** EZKL accepts a valid proof with extra bytes appended, which would be a free channel, so any length other than 3,072 bytes is rejected.
5. **Impossible questions are refused up front.** A question that would overflow EZKL's range checks can't be proved, so it gets `bad_request` before it reaches the server; otherwise anyone could make the server fail on demand. The limits come from a domain file, which is trusted configuration: it is not among the bundle's pinned hashes.

**What went wrong without each rule**

| Pitfall | What happened | Rule |
|---|---|---|
| Settings not pinned | Changing only the output scale from 26 to 13 kept the proof valid but decoded 0.9666 as 7918.6 | 1 |
| Python rounding | Python's `round()` rounds half to even: 2.5/8192 encodes as 2, EZKL gives 3 | 2 |
| Server-supplied answer | The server could state any answer next to a valid proof | 3 |
| Padded proofs | EZKL still accepts a valid proof with bytes appended | 4 |
| Out-of-domain questions | A question no proof can exist for makes the server fail on demand | 5 |

**Worked example** (the tiny test model, y = ReLU(xW + b), 4 inputs and 3 outputs): x = [0.5, -0.2, 0.9, 0.1] encodes to inputs [4096, -1638, 7373, 819] at scale 13; the proven outputs are [64869479, 0, 0] at scale 26, which decode to y = [0.9666305631399155, 0, 0].

**Limits**

- It binds the **encoded** value, not the raw number: 0.5 and 0.50001 both encode to 4096, so one proof serves both.
- **Replay:** the proof has no nonce. The endpoint answers each pending question once and drops duplicates, so nothing is delivered twice, but an old proof for the same encoded question still verifies for a new request. For one deterministic model that gives the server no choice of output; it isn't proof of fresh computation.
- The rules are specific to [EZKL](https://github.com/zkonduit/ezkl) 23.0.5 and these test models. The test model is tiny (4 inputs, 3 outputs) and deterministic because the research question is about the endpoint, not the prover. A production proof system would take the same place in the design, but it needs its own binding rules (what exactly the proof states, how inputs and outputs are encoded, any randomness) and its own review.

Full rules and test vectors: [Request binding specification](binding-spec.md).

## The checker

The cryptography is done by EZKL's official command-line tool (23.0.5 for 64-bit ARM, download checked against its published digest; Raspberry Pi OS has no EZKL Python package). The endpoint has to trust its verdict, since it can't tell if the tool wrongly says yes, but it contains the tool's failures: one short-lived process per check, boxed in by limits, whose only way to say yes is a strict exit code and log line.

![The endpoint service starts a fresh single-threaded launcher with a minimal environment; the launcher sets resource limits and replaces itself with the EZKL CLI; output comes back through a pipe capped at 64 KiB; a watchdog kills the whole process group at the deadline](diagrams/checker.svg)

| Limit | Value | Why |
|---|---|---|
| Address space (Linux only) | 256 MiB | Stops a proof that makes the verifier allocate without bound |
| CPU time | up to the deadline | A second stop for a busy loop, besides the wall-clock deadline |
| File size | 0 | Verify writes no files; a CLI that tries is killed (this doesn't stop it deleting files) |
| Open files | 64 | Bounds file-handle exhaustion |
| Core dumps | off | No memory dumps on disk |
| Threads | Rayon set to 1 (a setting, not an OS limit) | Less memory, steadier timing |
| Output | 64 KiB, then SIGKILL | A flooding child can't exhaust the endpoint |
| Wall time | the time left until the deadline | Then SIGKILL of the whole process group |

- **Why a launcher and not `preexec_fn`:** the endpoint is multithreaded, and running Python code between fork and exec in a threaded program can deadlock. A fresh single-threaded interpreter sets the limits and then replaces itself with the CLI.
- **Verdicts** (`classify_cli`): ACCEPT needs exit 0, no error line and exactly one "verified: true"; REJECT needs exit 1, one "verified: false" and exactly one `[halo2]` error; everything else is ERROR. Only ACCEPT releases an answer, and ERROR counts as a server failure, never as a pass.
- **Memory:** {{#include results.md:memory}}
- **What it isn't:** a sandbox. Limits are per process; a separate user and a read-only system come with the v1 image.

**What a check costs on the Pi** (medians of 20, 19 October):

{{#include results.md:verify-cost}}

## The message format

Every message is a 4-byte length followed by its payload. Questions and replies have one fixed size for the pinned model, and an answer is a fixed part plus a proof that must have the pinned length, so nothing about a reply's size is the server's choice and a parser only has to accept one shape.

| Message | From, to | Payload (test model) | Contents |
|---|---|---|---|
| Question | user to endpoint | 41 bytes (45 with the length) | type 1, id (8 bytes), 4 float64 inputs |
| Forwarded question | endpoint to server | 41 bytes | type 2, seq (8 bytes), the same inputs |
| Answer | server to endpoint | 105 bytes + proof | type 3, seq, 3 output field elements of 32 bytes, then the proof (up to 64 KiB on the wire, must be exactly 3,072 bytes) |
| Reply | endpoint to user | 34 bytes (38 with the length), success or error alike | type 4, id, status (0 ok, 1 bad_request, 2 unavailable), 3 float64 outputs (all zero unless ok) |

- **Rejected:** a frame of the wrong size, an unknown type byte, an id or seq of 2^53 or more, a NaN or infinite number, a frame that doesn't arrive in full within the deadline, and ids that don't strictly increase on one connection.
- **Tested by:** a fuzz test of 10,500 random and mutated messages per parser (each must be refused, or re-encode to exactly the same bytes), frames split at random points, and boundary tests (a 65,536-byte proof field accepted, 65,537 refused).

Details: [Endpoint service specification](service-spec.md).

## When answers leave

Once the proof fixes what an answer says, the server's remaining levers are when and in what order replies leave, so the endpoint decides that itself. Every admitted question goes through the same states; the release rule only changes when a decided answer leaves.

![States of one question: pending, checking, decided; then sent straight away under the immediate rule, or held until every earlier answer is sent (and, under the fixed rule, until its own deadline); a halt sends held answers as unavailable](diagrams/lifecycle.svg)

| Rule | When an answer leaves | Stops | Costs |
|---|---|---|---|
| Immediate (v0) | as soon as it is decided | nothing | nothing |
| Arrival (v1 default) | after every earlier question's answer | the server choosing the order | a slow answer holds up the ones behind it, up to its deadline |
| Fixed | in arrival order, and not before admission + deadline (4 s in the tests) | order, and the timing codes we tried | every answer waits the whole budget; a check not done by then is `unavailable` |
| Fixed + spin 5 ms | as fixed, but the timer wakes 5 ms early and busy-waits until the release time | no average sub-millisecond shift was detected; whether a weaker trace remains is not settled | a little CPU; no callback blocks the loop for more than the spin (K35) |

- **Halt** (`--halt-after N`, off by default): after N server-caused failures the endpoint stops every successful answer: pending and held ones become `unavailable`, and so do later requests. Error replies keep flowing. It caps damage but lets the server switch the endpoint off, and where the halting failure fell, and the timing of the errors, still leak.
- **Capacity:** at most `max_pending` (default 32) admitted questions wait at once, held answers included; beyond that the endpoint answers `unavailable` straight away.

Bugs found by review in this part, all fixed with tests that fail on the old code:

| Row | Bug | Fix |
|---|---|---|
| K30 | A held success could leave after a halt, or after its own deadline | re-checked when it leaves |
| K31 | Held answers didn't count towards `max_pending`, so the queue could grow without bound | counted |
| K33 | With spin, a later answer could overtake an earlier one, letting the server order two error replies | one ordered queue; only the next answer may spin |
| K35 | With spin, a disconnect or halt that decided many answers at once chained one busy-wait per answer, stalling the endpoint for tens of ms | releases only schedule the pump, which busy-waits at most once per run |

## From v0 to v2

The same endpoint is built three times, each version removing a way in, and measured the same way each time. Today v0 is measured, the v1 software runs on v0's operating system, and the v1 image boots but doesn't carry the endpoint yet.

![v0 baseline: stock Raspberry Pi OS with ssh and mDNS listening; v1 stripped: a Buildroot image with only the endpoint, read-only, two ports, no remote login; v2 hardened: secure boot, signed versioned updates, fixed time slots and a hash-chained log](diagrams/ladder.svg)

|  | v0 (measured) | v1 (in progress) | v2 (planned) |
|---|---|---|---|
| Operating system | Raspberry Pi OS Lite (Debian 13) | Buildroot 2025.02.18 LTS: kernel 6.6, BusyBox init, glibc 2.41, Python 3.12 | as v1, inside a signed boot image |
| Network | one port | two wired ports, static addresses, nothing forwarded | as v1 |
| Updates | copy files over ssh | swap the SD card | signed, versioned packages with a rollback rule |
| Debug access | ssh, HDMI | serial console only | as v1 |

What each one exposes, measured:

{{#include results.md:attack-surface}}

- **How the v1 image is built** (`image/build.sh`, on a Linux VM because Buildroot doesn't build on macOS): download the Buildroot release and check its SHA-256, start from `raspberrypi4_64_defconfig`, add glibc (the EZKL CLI needs it) and Python 3 (`image/v1.fragment`), check every added option survived, build `sdcard.img`. Defaults still to fix: an empty root password on the console, DHCP instead of fixed addresses, and crond.
- **What v1 still needs:** the endpoint and the pinned bundle inside the image, a read-only system, a separate unprivileged user for the checker, a small log partition, the second port, and a serial console for debugging.

**Secure boot (v2, planned):**

![The hash of our public key is burned once into the chip's one-time-programmable memory; the boot ROM and EEPROM bootloader check the signature of the boot image holding the kernel, system and endpoint; a valid image boots, a modified or unsigned one doesn't](diagrams/secure-boot.svg)

- The key hash in the chip is **permanent**, so this is done on a spare Pi, not the development one.
- Updates: versioned packages signed with update keys (in the style of The Update Framework), a rule that refuses an older version, and an install that survives power loss at every step. Boot signing, update signing and model approval use separate keys.
- **Known gap:** the Pi has no battery-backed clock, so after a reboot it can't tell how old its version is, and an attacker who blocks updates can keep it on an old one.
- This part is planned from Raspberry Pi's documentation; nothing about secure boot has been run yet.

More: [How hard the endpoint is to attack](hardening.md).

## Who attacks it

The main attacker is the AI server itself (hacked, or a model working against its owners) together with an accomplice outside who reads the signals. The endpoint's job is to give that pair as little to work with as possible.

![Inside and untrusted: A1 a hacked server and A2 a patient rogue model, both sending to the endpoint, the only trusted part; outside: A3 an accomplice reading replies; also A4 a network attacker on either link and A5 a holder of a signing key from v2](diagrams/threats.svg)

- **Protected:** the weights on the server, and everything that decides what gets out: the pinned bundle, the endpoint's code and settings, when replies are released, and the endpoint's own logs.
- **Assumed:** every path from the server to the outside goes through the endpoint (not yet enforced on v0); the proof system is sound; physical attacks are out of scope.

Signals the server controls even with valid proofs:

| Signal | Defence | Status |
|---|---|---|
| What an answer says | proof + binding | blocked for the test model |
| Order of replies | arrival-order release | the ordering code we tried no longer gets through (one connection) |
| Timing | fixed release time, plus spin | much reduced; see below |
| Which answers fail | a cap on failures per window (in the Rust endpoint; not yet measured on the Pi) | open |
| Reply size | fixed-size replies | blocked at the message level; TCP packets are still the stock OS's |
| Logs | fixed words only | the number and timing of events still follow the server |
| Keeping the endpoint busy | fixed time slots (planned) | open |

{{#include ../../docs/ATTACKS.md:summary}} Every row is in the [Attack catalogue](attacks.md); the full model is in [Threat model](threat-model.md).

## Measuring what still leaks

We made the server hide a known message in its behaviour and had an outside observer try to read it back. If the observer recovers the planted bits, that channel is open; if a fixed decoder fails, it didn't recover that message in that run (a smarter one might still).

![Planted bits, seeded at random, drive a server encoder that delays, reorders or fails answers on purpose; answers with valid proofs pass the endpoint under one release rule; the observer records each reply and its arrival time; a decoder reads the bits back and they are compared with the planted bits, with an honest server as the control](diagrams/leakage.svg)

**Three encoders** (`prover/server.py`):

- `timing_bits`: delay an answer by 1 s for each 1 bit.
- `order_bits`: take 3 questions at once and answer them in an order whose permutation index carries 2 bits.
- `fail_position`: in each block of 16 answers, make exactly one fail (a tampered output); its position carries 4 bits.

**The statistics in plain words:**

- **Permutation test:** shuffle the planted labels thousands of times and see how often a difference as big as the real one appears by chance. That fraction is the p-value.
- **Held-out decoder:** fit a simple threshold on half the answers and score it on the other half, both ways round, so the decoder never sees the answers it's scored on. Its null refits the decoder on shuffled labels each time.
- **Mutual information:** how many bits per answer the binned arrival time tells about the hidden bit, with a shuffled-label baseline. It describes this one statistic, not a limit on what leaks.
- **Honest control:** the same analysis on a run where the server hides nothing; any "signal" there is noise.

**Results on the Pi** (19 October, 4 s budget, one client at a time, 16 bits per cell):

{{#include results.md:matrix}}

**The leftover timing trace.** {{#include results.md:no-spin-trace}} The spin was re-tested on 20 October under a protocol written before the data (`docs/EXPERIMENT_PROTOCOL.md`; 512 answers, the corrected spin code, microsecond timestamps). {{#include results.md:spin-verdict}}

**What it doesn't show:** a limit on what a cleverer attacker could do, several clients at once, or packet-level signals. All numbers and caveats: [Measurements](results.md) and [What a hacked server can still leak](leakage.md).
