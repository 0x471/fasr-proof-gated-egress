import json
import random
import struct

import pytest

from conftest import MULTI, MULTI_PINS, MULTI_X, TINY, TINY_PINS, TINY_X, run
from pge.binding import CircuitSpec, decode_outputs, encode_inputs, felt_to_hex, int_to_felt, quantize


def _f32_bits(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _values(seed=0):
    rng = random.Random(seed)
    vals = [0.0, -0.0, 0.5 / 8192, -0.5 / 8192, 1.5 / 8192, 2.5 / 8192, -2.5 / 8192,
            (0.5 - 1e-9) / 8192, (0.5 + 1e-9) / 8192, 1.0, -1.0, 0.2, -0.2,
            _f32_bits(1), _f32_bits(0x80000001), _f32_bits(0x00800000), _f32_bits(0x7F7FFFFF) / 2 ** 100]
    for _ in range(600):
        k = rng.randint(-200000, 200000)
        vals.append((k + 0.5) / 8192)
        vals.append(rng.uniform(-50, 50))
        vals.append((k + 0.5) / 8192 + rng.choice([-1, 1]) * 1e-12)
        x = _f32_bits(rng.getrandbits(32))
        if x == x and abs(x) < 1e6:
            vals.append(x)
    return vals


def test_quantize_matches_ezkl(ezkl):
    bad = [(x, quantize(x, 13), ezkl.felt_to_int(ezkl.float_to_felt(x, 13, ezkl.PyInputType.F32)))
           for x in _values()]
    bad = [b for b in bad if b[1] != b[2]]
    assert not bad, bad[:10]


def test_hex_matches_ezkl(ezkl):
    for x in [0.5, -0.2, 0.9, 0.1, -123.456, 1e-4]:
        assert felt_to_hex(int_to_felt(quantize(x, 13))) == ezkl.float_to_felt(x, 13, ezkl.PyInputType.F32)


def test_decode_matches_ezkl(ezkl, multi_proof):
    spec = CircuitSpec.from_settings(MULTI / "settings.json", MULTI_PINS.settings)
    outs = [multi_proof["instances"][0][4:6], multi_proof["instances"][0][6:8]]
    mine = decode_outputs(spec, outs)
    for tensor, scale, got in zip(outs, spec.output_scales, mine):
        assert got == [ezkl.felt_to_float(h, scale) for h in tensor]


@pytest.mark.parametrize("x", [
    [TINY_X],
    [[0.5 / 8192, -0.5 / 8192, 2.5 / 8192, (0.5 - 1e-9) / 8192]],
    [[-0.75, 0.3, 1e-6, -0.0]],
    [[_f32_bits(1), _f32_bits(0x80000001), _f32_bits(0x00800000), 0.0]],
])
def test_tiny_encoding_matches_witness(ezkl, workdir, x):
    spec = CircuitSpec.from_settings(TINY / "settings.json", TINY_PINS.settings)
    d = workdir / "tiny"
    (d / "input.json").write_text(json.dumps({"input_data": x}))
    run(ezkl.gen_witness, str(d / "input.json"), str(d / "network.ezkl"), str(d / "witness.json"))
    assert encode_inputs(spec, x) == json.loads((d / "witness.json").read_text())["inputs"]


def test_multi_encoding_matches_witness(ezkl, workdir):
    spec = CircuitSpec.from_settings(MULTI / "settings.json", MULTI_PINS.settings)
    d = workdir / "multi"
    (d / "input.json").write_text(json.dumps({"input_data": MULTI_X}))
    run(ezkl.gen_witness, str(d / "input.json"), str(d / "network.ezkl"), str(d / "witness.json"))
    assert encode_inputs(spec, MULTI_X) == json.loads((d / "witness.json").read_text())["inputs"]
