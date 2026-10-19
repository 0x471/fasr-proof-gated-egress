# How it works

## The setting

```text
                          +----------+
  client  -- request x -->|          |-- x ------------>  AI server
  (outside)               |  device  |                   (prover; may be
          <-- y or error -|          |<-- y, proof -----  compromised)
                          +----------+
```

The device has two sides. It listens only on the outside. It opens the connection to the AI server itself, so it has no listener on the server side, but that connection still carries hostile data. On the planned v1 image the two sides are separate wired ports with nothing forwarded between them.

For each request the device:

1. **Parses and checks the request** itself: fixed size, finite numbers, inside the circuit's input domain. A bad request gets `bad_request` at once and is never forwarded, so this answer depends only on the client.
2. **Forwards** `(seq, x)` to the server, where `seq` is the device's own counter. The server never sees client ids.
3. **Checks the first answer** for that `seq`: the proof must have exactly the pinned length; the device rebuilds the proof's public values from **its own** encoding of `x` plus the server's claimed outputs, and verifies with the pinned verification key and setup. The verifier runs as a separate process with a deadline and resource limits.
4. **Releases** either the output decoded from the verified values, or one generic error (`unavailable`). No text from the server ever reaches the client, and every reply has the same size.

Each request gets exactly one decision and at most one answer. Late, duplicate or unknown answers from the server are dropped and counted.

The proof system is [EZKL](https://github.com/zkonduit/ezkl) 23.0.5 (Halo2 with KZG), used as a stand-in prover on two tiny pinned test circuits (`y = ReLU(xW + b)`, 4 inputs and 3 outputs, and a two-input variant). The device side would be the same for a production proof system; the binding rules here are specific to EZKL.

## Why binding matters

A valid proof only says "some input produced some output under this key". The device must make sure it is **this** request's input, **this** model and **this** output encoding. Each of these was found to be breakable when left to the server or to defaults:

- changing only the output scale in the settings leaves the proof valid but decodes 0.9666 as 7918.6, so the settings are pinned by hash;
- EZKL accepts a valid proof with extra bytes appended, so the proof length is pinned;
- Python's `round()` rounds half to even while EZKL rounds half away from zero, so the device reimplements EZKL's encoding exactly and tests it against EZKL.

The full rules and test vectors are in [Request binding](binding-spec.md).

## Release policies

The order and timing of answers are the server's main levers once content is fixed. The gate has three policies (`--release`):

| Policy | When an answer leaves | What it closes | What it costs |
|---|---|---|---|
| `immediate` (v0) | As soon as it is decided | Nothing | Nothing |
| `arrival` (v1 default) | After every earlier request's answer | Ordering: the order is the order requests arrived | A silent request delays later answers up to its deadline |
| `fixed` | At admission + deadline, for every admitted request | Ordering and coarse timing | Every answer waits the full budget; an answer not verified in time is `unavailable` |

No release policy closes the **failure** channel: whether each answer is `ok` or `unavailable` is still the server's choice, because the server can always send a bad proof. The [Results](results.md) show how much each policy leaks on the real device.

## Verifier limits

The verifier is EZKL's official command-line program (there is no Python package for the Pi's OS). The gate starts it through a small single-threaded launcher that sets resource limits before it runs: Rayon's thread pool set to one thread (a setting, not an OS limit), 256 MiB of address space, CPU time up to the deadline, no file growth (truncation and deletion need file permissions on the image), 64 open files, no core dumps. The limits are per process, not for the process tree. The whole process group is killed at the deadline or when its output passes 64 KiB. A verifier that hits a limit is an error, never an accept.

## Logs

The gate logs one line per event with fixed words only (a result, a reason from a fixed list, the device's `seq`, a hash of the client's request and the elapsed time), never text from the server. The number and timing of log events still follow the server, so who may read the log matters; see the [Threat model](threat-model.md).
