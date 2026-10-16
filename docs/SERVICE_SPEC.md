# v0 gate service

The v0 device as a network service: clients send requests to the device, the device forwards them to the AI server (the prover), checks each answer's proof against its own encoding of the request, and releases either the decoded output or one generic error. Code: `pge/service.py`, `pge/protocol.py`. Laptop prover: `prover/server.py`. Tests: `tests/test_service.py`, `tests/test_protocol.py`.

v0 is the baseline. It releases as soon as a check finishes, so it does not close the timing, ordering or failure channels; that is v1 and v2 work and the W4 experiment.

## 1. Connections

```
client --(outside port, device listens)--> device --(device connects out)--> server
```

- The device listens only on the outside address. It opens the connection to the server itself, so the server side has no listener on the device. That connection still carries hostile data.
- If the server connection drops, every pending request fails at once, and the device reconnects with a growing delay.

## 2. Framing

Every message is a frame: a 4-byte big-endian length, then that many bytes of UTF-8 JSON.

| Direction | Max frame |
|---|---|
| client to device | 4 KiB |
| device to client | 4 KiB |
| server to device | 64 KiB |

A frame over the limit, an empty frame, invalid UTF-8 or invalid JSON is a protocol violation (section 5). The JSON parser refuses duplicate keys, `NaN`, `Infinity` and nesting deeper than the schema. Every object must have exactly the listed keys.

## 3. Messages

| From | Message |
|---|---|
| client | `{"id": <int>, "x": [[<number>, ...], ...]}` |
| device to server | `{"seq": <int>, "x": [[<number>, ...], ...]}` |
| server | `{"seq": <int>, "outputs": [["<64 hex>", ...], ...], "proof": "<hex>"}` |
| device to client, success | `{"id": <int>, "ok": true, "y": [[<number>, ...], ...]}` |
| device to client, failure | `{"id": <int>, "ok": false, "error": "bad_request" \| "unavailable"}` |

- `id` is the client's, an integer in [0, 2^53). On one connection ids must be strictly increasing, so an id is never reused.
- `seq` is the device's own counter, strictly increasing over the whole run, starting at 1. The server never sees client ids, and clients never see `seq`.
- `x` holds one list per input tensor, in graph order. Numbers must be finite; booleans are not numbers.
- `outputs` holds one list per output tensor: canonical field elements (64 lower-case hex characters, little-endian, below the field modulus).
- `proof` is the raw EZKL proof bytes as lower-case hex (3072 bytes for the test bundles). The device builds the proof object EZKL verifies from its own instances and these bytes; the server's own JSON proof file never reaches the device.

## 4. Request lifecycle

1. The device parses the client frame and checks `x` against the pinned bundle: tensor count and lengths, encoding (`pge/binding.py`) and, if the bundle has one, the input domain (`pge/domain.py`). Any failure is answered `bad_request` and nothing is forwarded. This answer depends only on `x`, so it reveals nothing about the server.
2. Otherwise the device assigns the next `seq`, records the request as pending with a deadline, and forwards `{"seq", "x"}`.
3. The **first** server message for a pending `seq` is final. The device builds the instances from its own `x` and the server's `outputs`, verifies, and removes the `seq` from the pending table before answering:
   - ACCEPT: answer `ok: true` with `y` decoded from the verified output instances at their pinned scales;
   - REJECT or ERROR: answer `unavailable`.
4. If the deadline passes first, the request is removed and answered `unavailable`. A check still running is left to finish (the verifier has its own deadline) and its result is dropped.
5. Messages for a `seq` that is not pending (late, duplicate, never issued) are dropped and counted. They never produce a client answer.

So each client request gets exactly one answer, and each `seq` is consumed once.

## 5. Failures and policy

- **Client protocol violation** (bad frame, wrong keys, non-increasing id): the device closes that client's connection. Its pending requests still run, but their answers are dropped.
- **Server protocol violation** (bad frame or a message that doesn't match the schema): the device drops the server connection and answers every pending request `unavailable`. A broken message can't be tied to one request, so all of them fail.
- **Failure count:** every `unavailable` caused by the server (REJECT, ERROR, deadline, disconnect, no server connection) adds one to the failure count. If `halt_after` is set and the count reaches it, the device stops forwarding and answers every later request `unavailable` until it is restarted. The default is not to halt, which is open question 2 for Yogev (halt vs continue); v0 logs every failure either way.
- **Load:** at most `max_pending` requests are in flight; more are answered `unavailable`. In v0 this answer depends on other clients' load.
- Checks run one at a time.

## 6. Replay

- A server message is matched only to a pending `seq`, so an old answer can't be delivered twice or to another request.
- The proof binds the encoded `x` and `y` only, with no nonce (see `BINDING_SPEC.md` section 8). A proof the server made earlier for the same encoded `x` is still a valid answer to a new request with that `x`. With one pinned, deterministic model the released `y` is the same either way, so this doesn't let the server choose the output. Proving that each answer was freshly computed would need a nonce in the proved relation: open question 3.

## 7. Logs

One JSON line per finished request on stderr: `seq`, sha256 of the encoded request, result, the verdict detail, and the elapsed time. The detail stays in the log; clients only ever see the two error strings.

## 8. Not in v0

Fixed release time, arrival-order release, fixed-size responses, writing outgoing packets itself, a separate log partition, alerts with a response beyond `halt_after`, signed updates. See the plan's three versions.
