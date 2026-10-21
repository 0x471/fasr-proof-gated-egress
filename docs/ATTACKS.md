# Known-attacks catalogue

Every scenario catalogued against the device, by us, by reviewers or by testers: what it targets, how far it got, and its evidence (a test, a Pi run, or none yet). Living document; final in W7.

<!-- ANCHOR: summary -->
The catalogue has 35 scenarios: 24 stopped, 2 allowed on purpose, 7 that still work, are only partly stopped or are untested, and 2 out of scope. Some were shown on the Pi, some only in tests, and some are only proposed; the counts describe coverage, not a security score.
<!-- ANCHOR_END: summary -->

<!-- ANCHOR: outcomes -->
| Outcome | Scenarios | Examples |
|---|---|---|
| Stopped | 24 | Edited answers, a proof for a different question, a proof from another model, padded proofs, garbage or oversized messages, answering twice or late, malicious proofs that try to exhaust the checker, using a disconnect to stall the gate |
| Allowed on purpose | 2 | The same question gets the same answer again (the server has no choice there); a gate set to stop after failures can be stopped by the server |
| Still works, partly stopped or untested | 7 | Timing (much reduced), failing on purpose, never answering, keeping the gate busy, loading the gate to delay releases, other services on the standard system, probing the gate's rules |
| Out of scope | 2 | Learning the model from many correct answers; physical attacks |
<!-- ANCHOR_END: outcomes -->

**Level:** shown on the Pi; shown in tests (laptop and CI); proposed (not yet tried).
**Result:** blocked; leaked (with a measured rate); open (no defence yet); out of scope.
**LoC:** the closest technique in Gigurtsis's loss-of-control threat matrix (`github.com/bgigurtsis/AgenticLoCThreatMatrix`), where one fits.

Pi results: 16 Oct 2026 (v0 software) and 19 Oct 2026 (v1 software), both on the stock Raspberry Pi OS, gate on the Pi, AI server and observer on the laptop; each row gives its date where it matters.

## Answer content

| # | Attack | Version | Level | Result | Covered by |
|---|---|---|---|---|---|
| K1 | Change an output value and keep the proof | v0 | shown on the Pi | blocked: REJECT | `test_bad_answers_are_blocked[tamper_output]` |
| K2 | Send a valid proof made for a different request | v0 | shown on the Pi (replay mode, other x) | blocked: REJECT | `test_proof_for_another_request_is_blocked` |
| K3 | Send a proof made with another circuit's key | v0 | shown in tests | blocked: REJECT | `test_proof_from_another_circuit_is_blocked` |
| K4 | Reuse an earlier valid answer for the same request | v0 | shown on the Pi | accepted by design: the same encoded x gives the same y, so the server gains no choice (`SERVICE_SPEC.md` section 6) | `test_replayed_answer_only_passes_for_the_same_request` |
| K5 | Append bytes to a valid proof (EZKL still accepts it) | v0 | shown in tests | blocked by pinning the proof length | `test_trailing_proof_bytes_still_verify`, `test_padded_proof_is_blocked_when_the_length_is_pinned` |
| K6 | Change the settings' output scale so the same proof decodes differently | v0 | shown in tests | blocked by pinning settings, vk and SRS | `test_scale_only_settings_change_still_verifies`, `test_bundle_pins_enforced` |
| K7 | Release extra fields from EZKL's proof file (`pretty_*`, `hex_proof`, `timestamp`) | v0 | shown in tests | blocked: only `instances` and `proof` are read, y is decoded from verified values | `test_pretty_fields_not_authenticated` |

## Delivery: whether, when and how often an answer arrives

| # | Attack | Version | Level | Result | Covered by |
|---|---|---|---|---|---|
| K8 | Delay answers to encode bits (LoC 5.4.1) | v0 | shown on the Pi | **Leaked on v0 and in arrival order. Under fixed release the 1 s code was not read back, but a sub-millisecond trace still followed the server without spin; with spin a pre-registered test was inconclusive.** Numbers: the book's Measurements page (release policies; what fixed release leaves) and `docs/results/2026-10-20_spin5_512.md` | `test_v0_leaks_bits_through_timing`, `test_fixed_release_hides_the_timing_bits`, `observer/jitter_test.py`, `test_observer_recovers_the_timing_bits` |
| K9 | Answer twice | v0 | shown on the Pi | blocked: one answer, the duplicate is counted | `test_duplicate_answer_gives_one_reply` |
| K10 | Answer late, after the deadline | v0 | shown in tests | blocked: the client already got `unavailable`; the late answer is dropped | `test_late_answer_is_dropped_and_answered_once` |
| K11 | Never answer | v0 | shown in tests | `unavailable` at the deadline; still 1 bit per request (answer or not) | `test_silent_server_hits_the_deadline` |
| K12 | Answer for a seq the device never issued | v0 | shown on the Pi | dropped, counted, and the seq is not logged | `test_answer_for_unissued_seq_is_dropped`, `test_unissued_seq_is_not_logged` |
| K13 | Send garbage or an oversized frame | v0 | shown on the Pi | link dropped, pending requests answered `unavailable` | `test_bad_answers_are_blocked[garbage, oversize]` |
| K14 | Start a frame and never finish it; stop reading requests | v0 | shown in tests | link dropped | `test_stalled_server_frame_is_a_violation`, `SERVER_BUFFER_MAX` |
| K15 | Make verification slow, hang it, or flood its output | v0, v1 | shown in tests | killed at the deadline or once its output passes 64 KiB (checked per read, so up to one more read is kept); `unavailable`. v1 adds CPU-seconds, address-space (Linux), file-size and open-file limits on the verifier (K29) | `test_cli.py::test_deadline_kills_the_whole_process_group`, `test_continuous_writer_is_stopped_at_the_limit` |
| K16 | Fail on purpose to trigger a halt (if `halt_after` is set) | v0 | shown in tests | works by design: a halted device stops all releases, held successes included. A denial-of-service lever; the policy choice is open question 2 | `test_halt_answers_pending_requests_at_once`, `test_halt_revokes_held_successes` |
| K17 | Hold requests open so other clients get `unavailable` | v0 | proposed | open: `busy` depends on load until fixed slots (v2); fixed release holds every answer until its deadline, so pending plus held fills `max_pending` sooner | `test_too_many_pending_is_unavailable` (mechanism only) |
| K18 | Answer concurrent requests in a chosen order (LoC 5.4.1) | v1 (software) | shown in tests | blocked for admitted answers on the gate: they leave in arrival order, failures included; `busy`/halted answers leave at once, and the log keeps event timing; with `--release immediate` (v0) the server's order goes through | `test_answers_leave_in_arrival_order`, `test_failures_are_released_in_order_too`, `test_held_answer_waits_only_for_the_earlier_deadline`, `test_held_answers_go_to_their_own_clients`, `test_order_bits_leak_only_without_arrival_order`. Measured 19 Oct (`observer/order_experiment.py`, real prover, localhost): v0 leaks floor(log2(k!)) bits per batch of k with no errors; with arrival order the decoded bits are constant (Measurements, release policies) |
| K28 | Vary the size of what leaves: the number of output values, the reply length | v1 (software) | shown in tests | blocked: fixed-size binary frames; extra output values are read as proof bytes and the proof fails; every reply has one size | `test_log_records_hold_no_server_text`, `test_sizes_are_fixed_by_the_circuit` |

## The device's own outputs

| # | Attack | Version | Level | Result | Covered by |
|---|---|---|---|---|---|
| K19 | Put server-chosen values into the device's log (seq numbers, error text, output counts, proof lengths) | v1 (software) | shown in tests | blocked: records hold fixed words, the device's seq, the client's request hash, `ms` and the halt failure count (v0 logged error text that could carry the output count and proof length); the number and timing of log events still follow the server; who reads the log is undefined | `test_unissued_seq_is_not_logged`, `test_server_violation_detail_is_fixed_text`, `test_log_records_hold_no_server_text`, `test_proof_length_reason_hides_the_length` |
| K20 | Get server text into the client's error | v0 | shown in tests | blocked: two fixed error strings | `test_only_two_error_strings` |

## The client side and the device itself

| # | Attack | Version | Level | Result | Covered by |
|---|---|---|---|---|---|
| K21 | Send requests the circuit can't prove, so the server fails on demand | v0 | shown on the Pi | blocked: `bad_request` at once, never forwarded | `test_bad_requests_never_reach_the_server` |
| K22 | Malformed client frames (wrong size or type, NaN or infinity, oversize, ids of 2^53 or more, non-increasing ids) | v1 (software) | shown in tests | connection closed; re-encoding an accepted frame gives the same bytes (fuzzed) | `test_client_violation_closes_the_connection`, `test_ids_must_increase`, `test_fuzzed_input_is_refused_or_canonical` |
| K23 | Idle or slow clients holding connections | v0 | shown in tests | closed after `idle`; at most `max_clients` | `test_idle_client_is_closed`, `test_too_many_clients_are_refused` |
| K24 | Reach the device through other services (LoC 5.2.4) | v0 | shown on the Pi | **open in v0:** TCP 22 (sshd) and UDP 5353 (avahi) reachable; v1 removes them | `book/src/results.md` (16 Oct nmap and `ss` on the Pi), `device/measure.sh` |
| K25 | Learn the device's policy by probing (LoC 5.1.4) | v0 | proposed | partly: generic errors; failure alerts planned | none yet |
| K29 | Exhaust the verifier with a crafted proof: memory, CPU, disk, file handles | v1 (software) | shown in tests | blocked: the CLI runs with Rayon set to one thread, 256 MiB of address space (Linux), CPU seconds up to the deadline, a file-size limit of 0 (no growth; truncation and deletion need the v1 image's permissions) and 64 open files, per process, set before exec by a single-threaded process; a CLI over a limit is ERROR. Checked with the real EZKL CLI (19 Oct): under a too-small limit it aborts and the gate answers ERROR. On the Pi the outcome near the limit varies by run, so 256 MiB is used (Measurements, memory limit) | `test_proc.py` (limit tests), `test_a_cli_that_writes_a_file_is_stopped`, `test_cli_limits` |
| K30 | Get a held success out after a halt or past its deadline: stall the lowest `seq`, then trigger the halt or let timers run late (found by review, 19 Oct) | v1 (software) | shown in tests | blocked: a held `ok` is checked again when it leaves and sent as `unavailable` after a halt (both release rules) or, in arrival order, past its own deadline; the failure is counted before release. Fixed release sends an `ok` decided before its deadline at its release time even if the loop runs late, by design | `test_halt_revokes_held_successes`, `test_halt_on_the_earliest_request_revokes_later_held_successes`, `test_held_success_past_its_deadline_is_not_released` |
| K31 | Grow the held queue past `max_pending`: keep the lowest `seq` silent and fail later ones fast (found by review, 19 Oct) | v1 (software) | shown in tests | blocked: held answers count towards `max_pending` | `test_held_answers_count_towards_max_pending` |
| K32 | Fail one chosen answer in each block of N, so its position carries log2 N bits (sparse failures) | v1 (software) | shown on the Pi | **leaked under every release policy, immediate, arrival and fixed alike (19 Oct, 4 s budget, N = 16; Measurements, release policies).** Open: a rare failure still carries many bits; halting reveals the halting failure's position | `test_failure_position_leaks_under_every_release_policy`; `device/pi_matrix.sh` |
| K33 | With fixed release and spin, answer a later request so it is due inside the spin window while an earlier one stays silent, so the later reply overtakes the earlier deadline error and the order of two `unavailable` replies follows the server (found by review, 19 Oct) | v1 (software) | shown in tests | blocked: fixed release sends held answers in admission order, each not before its own deadline; only the next answer in order may busy-wait | `test_fixed_release_keeps_admission_order_whatever_the_server_does`, `test_spin_never_blocks_the_loop_for_long` |
| K34 | Load the gate on purpose (answer many requests at once, flood stale answers) so releases leave late or checks run past their deadline: timing and failures the server controls through load (found by review, 19 Oct) | v1 (software) | proposed | open: not measured; all leakage runs so far used one client at a time. Each spin blocks the loop for at most `spin` (K35), but load can still delay the loop and the verifier | none yet; planned: the concurrent-client matrix |
| K35 | Disconnect, or trigger a halt, while many answers are due a few ms apart, so that releasing them chains one busy-wait per answer and stalls the gate's event loop for tens of milliseconds, delaying every other client (found by review, 20 Oct; reproduced in tests with 20 answers and a 5 ms spin) | v1 (software) | shown in tests | blocked: releases only schedule the pump, which runs in its own callback and busy-waits at most once per run, so no callback blocks the loop for more than `spin` | `test_failing_many_answers_at_once_never_spins_for_long` |

## Out of scope

| # | Attack | Why |
|---|---|---|
| K26 | Learn the model from many correct answers (extraction) | Every proof verifies; the plan puts it out of scope, to confirm with the mentor (`THREAT_MODEL.md` 4.1) |
| K27 | Physical attacks and physical side channels | Out of scope (mentor, 14 Oct) |
