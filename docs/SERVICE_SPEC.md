# Gate service (v1 software)

The device as a network service: clients send requests to the device, the device forwards them to the AI server (the prover), checks each answer's proof against its own encoding of the request, and releases either the decoded output or an error. There are two error strings: `bad_request`, which depends only on the client's own request, and `unavailable` for every failure that involves the server. Code: `pge/service.py`, `pge/protocol.py`. Laptop prover: `prover/server.py`. Tests: `tests/test_service.py`, `tests/test_protocol.py`.

This is the v1 software, still run on the v0 OS until the v1 image exists. Compared with v0 (commit `f8e27e0`): fixed-size binary frames instead of JSON, answers released in arrival order, log records with fixed words only, and resource limits on the verifier. `--release fixed` (19 Oct) sends every admitted answer at admission + deadline, which hides the server's timing; the failure channel stays open (section 4, step 6). `--release immediate` restores v0's release behaviour for comparisons.

## 1. Connections

```
client --(outside port, device listens)--> device --(device connects out)--> server
```

- The gate listens only on the address given with `--listen`, which must be the outside interface's own address, not `0.0.0.0`. It opens the connection to the server itself, so the gate has no listener on the server side. That connection still carries hostile data.
- These are claims about the gate's own sockets. On the v0 OS the system also runs SSH and mDNS, and nothing yet forces every path from the server to the outside through the gate (see `THREAT_MODEL.md` section 4).
- If the server connection drops, every pending request fails at once, and the device reconnects with a growing delay.

## 2. Framing

Every message is a frame: a 4-byte big-endian length, then that many bytes of payload. For one pinned circuit every payload has one size, except the server's answer (section 3). With `n` input values and `m` output values in total:

| Direction | Payload size |
|---|---|
| client to device | `9 + 8n` bytes (41 for the tiny bundle) |
| device to server | `9 + 8n` bytes |
| server to device | `9 + 32m` bytes plus the proof, at most 64 KiB of proof (3,177 bytes for the tiny bundle) |
| device to client | `10 + 8m` bytes, for every reply (34 for the tiny bundle) |

A frame of any other size, an unknown type byte, a counter of 2^53 or more, or a number that isn't finite is a protocol violation (section 5). A server frame that has started must arrive in full within the deadline, counted from its first byte, or it is a violation too. Re-encoding an accepted payload gives the same bytes, so no message has a second accepted form (checked on fuzzed inputs in `tests/test_protocol.py`, not proven). Different float64 inputs can still quantise to the same `x`; that is the binding's concern (`BINDING_SPEC.md`).

## 3. Messages

All integers are big-endian. Numbers are IEEE-754 float64. Field elements are the 32 little-endian bytes EZKL uses.

| From | Layout |
|---|---|
| client | type `1` (1 byte), id (8 bytes), x (`n` float64, inputs in graph order, flattened) |
| device to server | type `2`, seq (8 bytes), x (`n` float64) |
| server | type `3`, seq (8 bytes), outputs (`m` field elements), proof (the raw EZKL proof bytes) |
| device to client | type `4`, id (8 bytes), status (1 byte: `0` ok, `1` bad_request, `2` unavailable), y (`m` float64; all zero bytes unless ok) |

- `id` is the client's, below 2^53. On one connection ids must be strictly increasing, so an id is never reused.
- `seq` is the device's own counter, strictly increasing over the whole run, starting at 1. The server never sees client ids, and clients never see `seq`.
- Every reply has the same size, so its length says nothing about the answer.
- The number of output values is fixed by the frame, so the server can't choose it (in v0 it could, and that count reached the log). The device checks the proof has exactly the pinned length (`--proof-bytes`; EZKL itself accepts bytes appended to a valid proof). A proof of another length is a REJECT for that request, as in v0, not a protocol violation, except an empty proof or one over 64 KiB, which doesn't fit the frame and is a violation. The pin itself can't exceed 64 KiB.
- The device builds the proof object EZKL verifies from its own instances and the raw proof bytes; nothing else from the server reaches the verifier.

## 4. Request lifecycle

1. The device parses the client frame. A request of the wrong size is a protocol violation; otherwise `x` has the circuit's shape, and the device checks its encoding (`pge/binding.py`) and, if the bundle has one, the input domain (`pge/domain.py`). Any failure is answered `bad_request` at once and nothing is forwarded. This answer depends only on `x`, so it reveals nothing about the server.
2. Otherwise the device assigns the next `seq`, records the request as pending with a deadline, and forwards `(seq, x)`.
3. The **first** server message for a pending `seq` is final. The device checks the proof length, builds the instances from its own `x` and the server's outputs, verifies, and removes the `seq` from the pending table:
   - ACCEPT: the answer is `ok` with `y` decoded from the verified output instances at their pinned scales;
   - REJECT or ERROR: the answer is `unavailable`.
4. If the deadline passes first, the request is removed and answered `unavailable`. A check still running is left to finish (the verifier has its own deadline) and its result is dropped. The deadline is absolute: it is checked before a check starts, when the result is decided, and again when a held `ok` leaves (step 5), so a late timer can't let an overdue answer out. The verifier gets only the time that is left.
5. **Release in arrival order** (default, `--release arrival`): every admitted request's answer, `ok` or `unavailable`, is sent only after the answers to all requests admitted before it. So the server can't choose the order in which admitted answers leave. Requests are admitted in order with the same deadline, so the earlier requests' deadlines are no later than an answer's own. Timers can run late, so a held `ok` is checked again when it leaves: past its own deadline or after a halt it is sent as `unavailable` instead (logged `deadline` with reason `held`, or `halted`). Held answers count towards `max_pending`. Answers that are never admitted (`bad_request`, `busy`, halted) are sent at once, so they can overtake held ones; `busy` and halted depend on the server through load and failures (section 5). Global order couples clients: a silent server delays every later answer, from any client, up to the silent request's deadline. Per-client order would remove that coupling but let the server order answers across clients; which matters more is open. With `--release immediate` each answer leaves as soon as it is decided (v0).
6. **Fixed release** (`--release fixed`): every admitted answer, `ok` or `unavailable`, leaves at admission + deadline, whatever the server did and whenever the check finished. So the deadline is also the release time and has to cover proving plus checking; an answer not decided by then is `unavailable`. Release times increase with admission, so the order is fixed too. A held `ok` after a halt is sent as `unavailable` at its release time. What still varies: whether each answer is `ok` (the failure channel: the position of one failure in a block of N requests carries log2 N bits), `busy` and halted answers (never admitted, sent at once), and the release timer's jitter.
7. Messages for a `seq` that is not pending (late, duplicate, never issued) are dropped and counted. They never produce a client answer.

So the device makes exactly one decision per request and sends at most one answer for it; each `seq` is consumed once. The answer reaches the client as long as its connection can still receive: a client that closes its sending side (or goes idle) still gets the answers to the requests it already sent. A client closed for a protocol violation doesn't.

## 5. Failures and policy

- **Client protocol violation** (bad frame or message, non-increasing id): the device closes that client's connection. Its pending requests still run, but their answers are dropped.
- **Idle or slow clients:** a client that sends nothing, or doesn't finish a frame, for `idle` seconds (default 60) stops being read; its outstanding answers are still sent, then the connection is closed. At most `max_clients` connections (default 64) are served; more are closed at once.
- **Server protocol violation** (a frame or message that doesn't match section 2-3): the device drops the server connection and answers every pending request `unavailable`. A broken message can't be tied to one request, so all of them fail.
- **Stalled server:** if more than 1 MiB of requests is waiting for a server that has stopped reading, the device drops the connection, with the same effect.
- **Failure count:** every `unavailable` caused by the server (REJECT, ERROR, deadline, disconnect, no server connection) adds one to the failure count. If `halt_after` is set and the count reaches it, the device halts: every pending request is answered `unavailable` at once (with fixed release, at its release time), held `ok` answers become `unavailable`, and every later request is answered `unavailable` too, until it is restarted. The failure is counted before its answer is released. A disconnect fails every pending request, so it adds one failure for each. The default is not to halt (open question 2 for Yogev, halt vs continue); every failure is logged either way.
- **Load:** at most `max_pending` admitted requests are not yet answered (pending or held); more are answered `unavailable`. This answer depends on other clients' load.
- Checks run one at a time. A request that expires while waiting for its turn is never checked.
- **Verifier limits** (`pge/cli.py`, `pge/proc.py`): the EZKL CLI runs with Rayon set to one thread (`RAYON_NUM_THREADS=1`, a setting, not an OS limit), in its own process group, with an address-space limit of 256 MiB (Linux; on the Pi, 112, 144 and 160 MiB failed on some runs and not others, 256 MiB passed 231 of 231), CPU seconds up to the deadline, a file-size limit of 0, at most 64 open files and no core dumps. The file-size limit stops a file growing; it doesn't stop truncating, deleting or creating empty files, which needs permissions or read-only mounts (v1 image). Limits are per process, not for the process tree. A fresh single-threaded process sets the limits and then execs the CLI. A CLI that hits a limit is ERROR. A larger circuit needs a larger memory limit (`CLI_MEMORY`).
- **Bad configuration:** a deadline or idle time that isn't a number of seconds in (0, 3600], capacities outside [1, 4096], non-positive `halt_after`, a `--proof-bytes` outside [1, 65536] (the frame's proof limit), and an unknown `--release` are refused at start-up.

## 6. Replay

- A server message is matched only to a pending `seq`, so an old answer can't be delivered twice or to another request.
- The proof binds the encoded `x` and `y` only, with no nonce (see `BINDING_SPEC.md` section 8). A proof the server made earlier for the same encoded `x` is still a valid answer to a new request with that `x`. With one pinned, deterministic model the released `y` is the same either way, so this doesn't let the server choose the output. A nonce in the proved relation would make each proof fresh, but not the inference: an old witness can be re-proved with a new nonce (see `BINDING_SPEC.md` section 8). Which property matters is open question 3.

## 7. Logs

JSON lines, one per event, on stderr or appended to the file given with `--log` (for a separate log partition on the image). A record holds only:

- `result`: a fixed word (`accept`, `reject`, `error`, `deadline`, `disconnect`, `no_server`, `busy`, `halted`, `bad_request`, `stale_answer`, `duplicate_answer`, `server_violation`, `server_stalled`, `client_violation`, `client_idle`, `client_refused`, `connect_failed`, `check_crashed`);
- `reason`: a fixed word from `REASONS` in `pge/service.py` (`request`, `proof_length`, `binding`, `proof`, `verifier`, `verifier_failed`, `reply`, `closed`, `violation`, `lost`, `protocol`, `ids`, `held`); anything else is logged as `other`;
- for an admitted request: the device's `seq`, sha256 of the encoded request (the client's input, not the server's) and `ms` from admission to release. The record is written when the answer leaves, so with arrival release records come in `seq` order; for `duplicate_answer`, the device's `seq`; for `halted`, the failure count.

No error text is logged: in v0 it could carry server-chosen values (the number of output values, the proof length). The proof is not hashed into the log either: a proof is randomized, so the server can pick among valid proofs, and a logged proof hash would give it a channel into the log whose rate depends on how many proofs it can afford to make, up to the digest width. A hash of the decoded `y` alone has no such freedom for one pinned deterministic model, but it would put the output into the log. Commitments to answers belong in v2's hash chain, which authenticates the log but doesn't close channels in its contents. The number of events, their timing, `ms`, the result and reason words and the failure count still follow the server, and `x_sha256` is a linkable tag for the client's input (guessable for small input sets), so who can read the log matters (`THREAT_MODEL.md` section 6). A log that fails to write never stops an answer (it is counted as `log_error`).

## 8. Not yet

Writing outgoing packets itself, the separate log partition and a separate unprivileged user for the verifier (both on the v1 image), alerts with a response beyond `halt_after`, signed updates.
