# Known-attacks catalogue

Every attack tried against the device, by us or by testers: what it targets, how far it got, and the test that now covers it. Living document; final in W7.

**Level:** shown on the Pi; shown in tests (laptop and CI); proposed (not yet tried).
**Result:** blocked; leaked (with a measured rate); open (no defence yet); out of scope.
**LoC:** the closest technique in Gigurtsis's loss-of-control threat matrix (`github.com/bgigurtsis/AgenticLoCThreatMatrix`), where one fits.

Pi results: 16 Oct 2026, v0 (stock Raspberry Pi OS), gate on the Pi, AI server on the laptop.

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
| K8 | Delay answers to encode bits (LoC 5.4.1) | v0 | shown on the Pi | **leaked: 16 of 16 bits at 1 s slots (0.69 bit/s); honest answers vary by 81 ms.** Open until the fixed release time (v2) | `test_v0_leaks_bits_through_timing`, `test_observer_recovers_the_timing_bits` |
| K9 | Answer twice | v0 | shown on the Pi | blocked: one answer, the duplicate is counted | `test_duplicate_answer_gives_one_reply` |
| K10 | Answer late, after the deadline | v0 | shown in tests | blocked: the client already got `unavailable`; the late answer is dropped | `test_late_answer_is_dropped_and_answered_once` |
| K11 | Never answer | v0 | shown in tests | `unavailable` at the deadline; still 1 bit per request (answer or not) | `test_silent_server_hits_the_deadline` |
| K12 | Answer for a seq the device never issued | v0 | shown on the Pi | dropped, counted, and the seq is not logged | `test_answer_for_unissued_seq_is_dropped`, `test_unissued_seq_is_not_logged` |
| K13 | Send garbage or an oversized frame | v0 | shown on the Pi | link dropped, pending requests answered `unavailable` | `test_bad_answers_are_blocked[garbage, oversize]` |
| K14 | Start a frame and never finish it; stop reading requests | v0 | shown in tests | link dropped | `test_stalled_server_frame_is_a_violation`, `SERVER_BUFFER_MAX` |
| K15 | Make verification slow, hang it, or flood its output | v0, v1 | shown in tests | killed at the deadline or at 64 KiB of output; `unavailable`. v1 adds CPU-seconds, address-space (Linux), file-size and open-file limits on the verifier (K29) | `test_cli.py::test_deadline_kills_the_whole_process_group`, `test_continuous_writer_is_stopped_at_the_limit` |
| K16 | Fail on purpose to trigger a halt (if `halt_after` is set) | v0 | shown in tests | works by design: a halted device stops all releases. A denial-of-service lever; the policy choice is open question 2 | `test_halt_answers_pending_requests_at_once` |
| K17 | Hold requests open so other clients get `unavailable` | v0 | proposed | open: `busy` depends on load until fixed slots (v2) | `test_too_many_pending_is_unavailable` (mechanism only) |
| K18 | Answer concurrent requests in a chosen order (LoC 5.4.1) | v1 (software) | shown in tests | blocked: answers leave in arrival order, failures included; with `--release immediate` (v0) the server's order goes through | `test_answers_leave_in_arrival_order`, `test_failures_are_released_in_order_too`, `test_held_answer_waits_only_for_the_earlier_deadline` |
| K28 | Vary the size of what leaves: the number of output values, the reply length | v1 (software) | shown in tests | blocked: fixed-size binary frames; extra output values are read as proof bytes and the proof fails; every reply has one size | `test_log_records_hold_no_server_text`, `test_sizes_are_fixed_by_the_circuit` |

## The device's own outputs

| # | Attack | Version | Level | Result | Covered by |
|---|---|---|---|---|---|
| K19 | Put server-chosen values into the device's log (seq numbers, error text, output counts, proof lengths) | v1 (software) | shown in tests | blocked: records hold fixed words, the device's seq and the client's request hash only (v0 logged error text that could carry the output count and proof length); the number and timing of log events still follow the server; who reads the log is undefined | `test_unissued_seq_is_not_logged`, `test_server_violation_detail_is_fixed_text`, `test_log_records_hold_no_server_text`, `test_proof_length_reason_hides_the_length` |
| K20 | Get server text into the client's error | v0 | shown in tests | blocked: two fixed error strings | `test_only_two_error_strings` |

## The client side and the device itself

| # | Attack | Version | Level | Result | Covered by |
|---|---|---|---|---|---|
| K21 | Send requests the circuit can't prove, so the server fails on demand | v0 | shown on the Pi | blocked: `bad_request` in under 1 ms, never forwarded | `test_bad_requests_never_reach_the_server` |
| K22 | Malformed client frames (wrong size or type, NaN or infinity, oversize, ids of 2^53 or more, non-increasing ids) | v1 (software) | shown in tests | connection closed; every accepted frame has one encoding (fuzzed) | `test_client_violation_closes_the_connection`, `test_ids_must_increase`, `test_fuzzed_input_is_refused_or_canonical` |
| K23 | Idle or slow clients holding connections | v0 | shown in tests | closed after `idle`; at most `max_clients` | `test_idle_client_is_closed`, `test_too_many_clients_are_refused` |
| K24 | Reach the device through other services (LoC 5.2.4) | v0 | shown on the Pi | **open in v0:** TCP 22 (sshd) and UDP 5353 (avahi) reachable; v1 removes them | README measurements (16 Oct nmap and `ss` on the Pi), `device/measure.sh` |
| K25 | Learn the device's policy by probing (LoC 5.1.4) | v0 | proposed | partly: generic errors; failure alerts planned | none yet |
| K29 | Exhaust the verifier with a crafted proof: memory, CPU, disk, file handles | v1 (software) | shown in tests | blocked: the CLI runs with one thread, 256 MiB of address space (Linux), CPU seconds up to the deadline, no file writes and 64 open files, set before exec by a single-threaded process; a CLI over a limit is ERROR. Checked with the real EZKL CLI on Linux (19 Oct): below 128 MiB it fails closed | `test_proc.py` (limit tests), `test_a_cli_that_writes_a_file_is_stopped`, `test_cli_limits` |

## Out of scope

| # | Attack | Why |
|---|---|---|
| K26 | Learn the model from many correct answers (extraction) | Every proof verifies; the plan puts it out of scope, to confirm with the mentor (`THREAT_MODEL.md` 4.1) |
| K27 | Physical attacks and physical side channels | Out of scope (mentor, 14 Oct) |
