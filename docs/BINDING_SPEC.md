# Request binding

What the device checks so that a valid proof is a proof for the request the device itself received, and what it releases. Code: `pge/binding.py`, `pge/bundle.py`, `pge/domain.py`, `pge/verdict.py`. Tests: `tests/`.

Everything here is for EZKL 23.0.5 and the pinned test bundles. It is not a general claim about every EZKL model.

## 1. The bundle is pinned

The device picks the single bundle for each request (settings, verification key, SRS) and refuses to start if any file's sha256 differs from its pin. The server never chooses between bundles: picking between two valid models with different outputs would let it choose the output.

| Bundle | File | sha256 |
|---|---|---|
| tiny | settings.json | `b4a95fc7c572816034708c4e5c079f0f91f82eb9681cfa069565aacc9887aefd` |
| tiny | vk.key | `34ab48a8ac0946be4faf10d85699ac729adb20ec4c6251da1d1d0d4ea18a4d26` |
| multi | settings.json | `89f83ab3aace396b42aced55d3b482d33cb8a196f2d00259ddfa20fea92a3121` |
| multi | vk.key | `0bd4cec6c17051d478ac3cfd3bff3ca5f4d98184673c99949bdb838b208411c7` |
| both | kzg.srs (logrows 15) | `90807800a1c3b248a452e1732c45ee5099f38b737356f5542c0584ec9c3ebb45` |

Why the settings pin is mandatory: changing only `model_output_scales` from `[26]` to `[13]` leaves the proof valid under the same vk, but the decoded output changes from 0.9666 to 7918.6 (`test_unpinned_scale_change_changes_decoding`).

Why pins name one build, not "the circuit": EZKL fills `required_range_checks` from a hash set, so its order can change between runs. That order changes the circuit layout and therefore the vk. Reversing only `[[-1,1],[0,16383]]` gives vk `d3c4f45c...` instead of `34ab48a8...`. Keygen itself is deterministic for a fixed ordered circuit, and the settings timestamp is not part of the vk. A regenerated bundle is a new build that needs a new pin.

A pin proves byte identity. It says nothing about whether the SRS ceremony was sound.

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

`pge/domain.py` derives these from the model's float weights and checks requests against them. The tests compare its decisions with EZKL's real witness generation on random requests and on the exact boundary of each pre-activation. Every request in `[-1, 1]^4` is inside. The domain of other models has to be derived from their own compiled operations.

## 5. Output decoding

- Each output tensor has its own scale (tiny: 26; multi: 13 and 26). Decode as a signed integer divided by `2^scale`.
- Release only values decoded from the same instances that were verified. Never release the server's claimed y, the proof file's `pretty_*` fields or the proof itself: changing those fields does not invalidate the proof.

## 6. Verify results

`pge/verdict.py` maps a verify call to ACCEPT, REJECT (invalid or malformed proof) or ERROR (missing files, crashes, timeouts, unexpected results). Only ACCEPT releases output. Tests treat ERROR as a failure, never as a rejection.

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

- Replay: the proof binds only the encoded x and y, with no request id or nonce. The device's pending table must consume each request id once and ignore late or duplicate answers. Whether a nonce must be bound into the proved relation depends on whether fresh computation has to be proven.
- The domain policy covers the affine + ReLU shape only.
