# Request binding

What the device checks so that a valid proof is a proof for the request the device itself received, and what it releases. Code: `pge/binding.py`, `pge/bundle.py`, `pge/domain.py`, `pge/verdict.py`, `pge/worker.py`. Tests: `tests/`.

Everything here is for EZKL 23.0.5 and the pinned test bundles. It is not a general claim about every EZKL model.

## 1. The bundle is pinned

Required of the device service (`pge/service.py` serves exactly one bundle): it picks the single bundle for each request (settings, verification key, SRS) and refuses to start if any file's sha256 differs from its pin. The server never chooses between bundles: picking between two valid models with different outputs would let it choose the output. `Bundle.load` implements the check.

| Bundle | File | sha256 |
|---|---|---|
| tiny | settings.json | `b4a95fc7c572816034708c4e5c079f0f91f82eb9681cfa069565aacc9887aefd` |
| tiny | vk.key | `34ab48a8ac0946be4faf10d85699ac729adb20ec4c6251da1d1d0d4ea18a4d26` |
| multi | settings.json | `89f83ab3aace396b42aced55d3b482d33cb8a196f2d00259ddfa20fea92a3121` |
| multi | vk.key | `0bd4cec6c17051d478ac3cfd3bff3ca5f4d98184673c99949bdb838b208411c7` |
| both | kzg.srs (logrows 15) | `90807800a1c3b248a452e1732c45ee5099f38b737356f5542c0584ec9c3ebb45` |

Why the settings pin is mandatory: changing only `model_output_scales` from `[26]` to `[13]` leaves the proof valid under the same vk, but the decoded output changes from 0.9666 to 7918.6 (`test_scale_only_settings_change_still_verifies`).

Why pins name one build, not "the circuit": EZKL fills `required_range_checks` from a hash set, so its order can change between runs. That order changes the circuit layout and therefore the vk. Reversing only `[[-1,1],[0,16383]]` gives vk `d3c4f45c...` instead of `34ab48a8...`. Keygen itself is deterministic for a fixed ordered circuit, and the settings timestamp is not part of the vk. A regenerated bundle is a new build that needs a new pin.

A pin proves byte identity. It says nothing about whether the SRS ceremony was sound.

`Bundle.load` reads each file once, checks it and keeps a private read-only copy of exactly those bytes; `Bundle.verify` re-checks the copies before every call and returns ERROR if they changed. This matters because a wrong but well-formed SRS makes a valid proof fail with a genuine `[halo2]` error, which would otherwise look like a rejected proof. The copies are protected only from other users: the service's own user can still change them between the re-check and EZKL's read.

## 2. Settings are validated strictly

`CircuitSpec.parse` rejects: a version other than 23.0.5; inputs or outputs that are not public; parameters that are not fixed; missing or empty fields; `input_types` that don't match the inputs one to one; types other than F32/F64; scales that are not plain integers in [0, 30] (no bools, floats or strings); non-positive dimensions; tensors over 65536 values; the wrong number of instance shapes.

## 3. Input encoding

- Field: BN254 scalar field, `q = 21888242871839275222246405745257275088548364400416034343698204186575808495617`.
- Convert to the declared input type first (F32 here), then multiply by `2^scale` and round half away from zero (Rust `f64::round`). Python's `round()` (half to even) and `floor(v + 0.5)` are both wrong.

| x | Python round(x*8192) | EZKL, F32 |
|---|---|---|
| 0.5/8192 | 0 | 1 |
| -0.5/8192 | 0 | -1 |
| 2.5/8192 | 2 | 3 |
| (0.5-1e-9)/8192 | 0 | 1 (0 if the type were F64) |

- A negative integer v is encoded as `q - |v|`.
- Instances are 32-byte little-endian, lower-case hex, no prefix. Only that canonical form is accepted (64 characters, value < q), for inputs and outputs alike.
- Inputs must be finite numbers that fit the input type; the scaled value must stay below 2^63. EZKL itself allows i128 and maps NaN to 0. The narrower range is deliberate and still far wider than any circuit domain below.
- All input tensors come first in graph order, then all output tensors, in one instance column (checked with a two-input, two-output bundle whose inputs are declared out of alphabetical order).

Binding is to the encoded request, not the raw float: 0.5 and 0.50001 both encode as 4096 at scale 13, so a proof for one is a proof for the other (`test_tiny_accepts_same_encoding_class`). If raw request identity matters, the device has to record it separately.

## 4. Input domain

A request with no possible proof should be refused before it reaches the server, otherwise out-of-domain requests let anyone make the server fail on demand.

For the tiny bundle (`y = ReLU(xW + b)`, 4 inputs, 3 outputs) EZKL decomposes the quantized inputs and every pre-activation in base 16384 with 2 digits, so with `N = 16384^2 - 1 = 268435455`:

```
n_i = round_half_away(8192 * F32(x_i))
z_j = sum_i n_i * Wq[i][j] + bq[j]
admissible  <=>  |n_i| <= N for all i  and  |z_j| <= N for all j

Wq = [[ 2244, -3772, -7521],      (scale 13)
      [-7921,  5133,  6763],
      [ 1747,  3760,   715],
      [ 7128,  5175, -8147]]
bq = [23984994, -31300542, 15411916]   (scale 26, quantized directly)
```

`pge/domain.py` derives these from the model's F32 weights and checks requests against them. The tests compare its decisions with EZKL's real witness generation on:

- random requests;
- each pre-activation at exactly `+N` and `-N` (admitted) and at `+(N+1)` and `-(N+1)` (refused), with every other constraint well inside. These inputs are small, so they are exact F32 requests;
- input coordinate 2 at `+N`, `-N`, `+(N+1)` and `-(N+1)` with small pre-activations. These are sent to EZKL as field elements, because `N` itself is not an F32 request at scale 13: `F32(N/8192)` quantizes to `N + 1`.

Every request in `[-1, 1]^4` is inside. `from_float` only accepts finite F32 parameters and is only valid for this graph's shape (MatMul + Add + ReLU, each constant used once, no rescaling before the output). EZKL requantizes a shared bias differently, so the domain of other models has to be derived from their own compiled operations.

## 5. Output decoding

- Each output tensor has its own scale (tiny: 26; multi: 13 and 26). Decode as a signed integer divided by `2^scale`.
- Release only values decoded from the same instances that were verified. Never release the server's claimed y or the proof itself, and never read the proof file's `pretty_*`, `timestamp` or `hex_proof` fields: changing them does not invalidate the proof, because EZKL verifies only the `proof` byte list. EZKL also accepts a valid `proof` list with bytes appended (`test_trailing_proof_bytes_still_verify`), so the endpoint pins the exact proof length (`Policy.proof_bytes`, 3072 for the test bundles). It does enforce the instance layout: extra, missing, shorter or longer columns are REJECT.

## 6. Verify results

`pge/verdict.py` maps a verify call to ACCEPT, REJECT (invalid or malformed proof) or ERROR (the check did not happen properly). Only ACCEPT releases output. Tests treat ERROR as a failure, never as a rejection.

- The proof is read once, up to 1 MiB. A read failure is ERROR; a larger proof is REJECT.
- The device checks the proof's shape in memory: a JSON object whose `proof` is a list of integers in [0, 255] and whose `instances` are lists of canonical field elements. Anything else is REJECT without calling EZKL. It then writes a private copy holding only those two fields and verifies that, so the other fields are never read.
- After that, only a `[halo2] ...` error is REJECT. Everything else is ERROR, including any error while loading the proof, settings, vk or SRS. EZKL's load messages can't tell bad data from failed I/O: serde can attach `at line L column C` to a real I/O error. The device script also requires the CLI's `verified: false` line for a `[halo2]` rejection, and `verified: true` plus exit code 0 for ACCEPT.
- A `[halo2]` failure only says the proof is bad if settings, vk and SRS were the pinned ones, so use `Bundle.verify`, which re-checks them.
- `verify` itself has no deadline (it can block on opening a slow file) and cannot survive a native crash. `pge/worker.py` runs it in a child process that is killed (SIGKILL) at the deadline; a timeout, crash, non-zero exit, or anything but one `[verdict, detail]` line of two strings is ERROR. The child runs in its own process group, killed as a whole at the deadline, and output over 64 KiB is ERROR. Limit: the bundle check runs before the deadline starts. The device script uses `timeout -k 5`, so a verifier that ignores SIGTERM is killed 5 s later.
- `pge/cli.py` is the same check with EZKL's official CLI, which the Pi needs because there is no Python package for Pi OS. ACCEPT needs exit 0, no error line and one `verified: true`; REJECT needs exit 1, one `verified: false` and exactly one error line, a `[halo2] ...` one. Anything else is ERROR, including duplicated or contradictory `verified` lines, output over 64 KiB and output that isn't UTF-8. The CLI runs with a minimal environment (so `RUST_LOG` can't change its output) in its own process group, killed with SIGKILL at the deadline or as soon as its output passes 64 KiB (`pge/proc.py` reads it from a pipe). `device/check_verifier.sh` uses the same `classify_cli`, so the endpoint and the device checks can't disagree on a verdict.

## 7. Test vectors

```
tiny:  x = [0.5, -0.2, 0.9, 0.1]
       inputs  = [4096, -1638, 7373, 819]                 (scale 13)
       outputs = [64869479, 0, 0]                          (scale 26)
       decoded = [0.9666305631399155, 0, 0]
multi: inputs  z = [0.25, -0.5], a = [-0.75, 0.125]      (scale 13)
       d = a - z = [-1.0, 0.625]                           (scale 13)
       p = z * a = [-0.1875, -0.0625]                      (scale 26)
```

## 8. Open

- Replay: the proof binds only the encoded x and y, with no request id or nonce. The device's pending table must consume each request id once and ignore late or duplicate answers. Binding a nonce into the proved relation would stop an old proof being replayed, but it would not by itself prove the inference was freshly computed.
- The domain policy covers the affine + ReLU shape only.
