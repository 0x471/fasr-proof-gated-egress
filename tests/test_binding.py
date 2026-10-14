import copy
import json
import math

import pytest

from conftest import MULTI, MULTI_PINS, MULTI_X, TINY, TINY_PINS, TINY_X
from pge.binding import (
    Q, BindingError, CircuitSpec, _round_half_away, build_instances, check_outputs, decode_outputs,
    encode_inputs, felt_to_hex, felt_to_signed, hex_to_felt, int_to_felt, quantize, split_instances,
)


@pytest.fixture(scope="module")
def tiny():
    return CircuitSpec.from_settings(TINY / "settings.json", TINY_PINS.settings)


@pytest.fixture(scope="module")
def multi():
    return CircuitSpec.from_settings(MULTI / "settings.json", MULTI_PINS.settings)


@pytest.fixture()
def settings():
    return json.loads((TINY / "settings.json").read_text())


def test_tiny_spec(tiny):
    assert tiny.input_scales == (13,)
    assert tiny.output_scales == (26,)
    assert tiny.input_lens == (4,) and tiny.output_lens == (3,)


def test_multi_spec(multi):
    assert multi.input_scales == (13, 13)
    assert multi.output_scales == (13, 26)
    assert multi.input_lens == (2, 2) and multi.output_lens == (2, 2)


def test_pin_is_required():
    with pytest.raises(TypeError):
        CircuitSpec.from_settings(TINY / "settings.json")
    with pytest.raises(BindingError):
        CircuitSpec.from_settings(TINY / "settings.json", "00" * 32)


@pytest.mark.parametrize("mutate", [
    lambda s: s["run_args"].update(input_visibility="Private"),
    lambda s: s["run_args"].update(output_visibility="Private"),
    lambda s: s["run_args"].update(output_visibility="Hashed"),
    lambda s: s["run_args"].update(param_visibility="Public"),
    lambda s: s.update(version="23.0.4"),
    lambda s: s.pop("version"),
    lambda s: s.pop("input_types"),
    lambda s: s.update(input_types=["F16"]),
    lambda s: s.update(input_types=["F32", "F32"]),
    lambda s: s.update(model_input_scales=[13.9]),
    lambda s: s.update(model_input_scales=[True]),
    lambda s: s.update(model_input_scales=["13"]),
    lambda s: s.update(model_output_scales=[99]),
    lambda s: s.update(model_instance_shapes=[[-1, -4], [1, 3]]),
    lambda s: s.update(model_instance_shapes=[[1, 4]]),
    lambda s: s.update(model_instance_shapes=[[1, 4], [1, 3], [1, 1]]),
    lambda s: s.update(model_instance_shapes=[[1, 1 << 20], [1, 3]]),
    lambda s: s.update(model_instance_shapes=[[256, 257], [1, 3]]),
    lambda s: s.update(model_input_scales=[-1]),
    lambda s: s.update(model_output_scales=[-1]),
    lambda s: s.pop("run_args"),
])
def test_bad_settings_rejected(settings, mutate):
    s = copy.deepcopy(settings)
    mutate(s)
    with pytest.raises(BindingError):
        CircuitSpec.parse(s, "test")


def test_largest_tensor_accepted(settings):
    s = copy.deepcopy(settings)
    s["model_instance_shapes"] = [[256, 256], [1, 3]]
    s["model_input_scales"], s["model_output_scales"] = [0], [0]
    spec = CircuitSpec.parse(s, "test")
    assert spec.input_lens == (65536,) and spec.input_scales == (0,)


def test_unpinned_scale_change_changes_decoding(tiny, tiny_proof):
    # Why the settings pin is mandatory: the proof still verifies with these settings, but y changes.
    s = json.loads((TINY / "settings.json").read_text())
    s["model_output_scales"] = [13]
    forged = CircuitSpec.parse(s, "forged")
    _, outs = split_instances(tiny, tiny_proof["instances"])
    assert decode_outputs(tiny, outs)[0][0] != decode_outputs(forged, outs)[0][0]


def test_tiny_vectors(tiny, tiny_proof):
    assert [quantize(x, 13) for x in TINY_X] == [4096, -1638, 7373, 819]
    ins, outs = split_instances(tiny, tiny_proof["instances"])
    assert encode_inputs(tiny, [TINY_X]) == ins
    assert tiny_proof["instances"][0][0] == "0010" + "00" * 30
    assert [felt_to_signed(hex_to_felt(h)) for h in outs[0]] == [64869479, 0, 0]
    assert decode_outputs(tiny, outs) == [[0.9666305631399155, 0.0, 0.0]]
    assert build_instances(tiny, [TINY_X], outs) == tiny_proof["instances"]


def test_multi_tensor_order_and_scales(multi, multi_proof):
    ins, outs = split_instances(multi, multi_proof["instances"])
    assert encode_inputs(multi, MULTI_X) == ins
    assert encode_inputs(multi, MULTI_X[::-1]) != ins
    assert build_instances(multi, MULTI_X, outs) == multi_proof["instances"]
    assert decode_outputs(multi, outs) == [[-1.0, 0.625], [-0.1875, -0.0625]]


def test_negative_wraps_into_field():
    assert int_to_felt(-1638) == Q - 1638
    assert felt_to_signed(Q - 1638) == -1638


def test_same_encoding_class(tiny):
    # Binding is to the quantized request: 0.5 and 0.50001 encode identically at scale 13.
    assert encode_inputs(tiny, [[0.50001, -0.2, 0.9, 0.1]]) == encode_inputs(tiny, [TINY_X])


@pytest.mark.parametrize("v,expected", [
    (0.5, 1), (-0.5, -1), (1.5, 2), (2.5, 3), (-2.5, -3), (0.49999999999999994, 0),
    (0.0, 0), (-0.0, 0), (1.4999999999999998, 1), (4503599627370495.5, 4503599627370496),
])
def test_round_half_away(v, expected):
    assert _round_half_away(v) == expected


@pytest.mark.parametrize("x,f32,f64", [
    (0.5 / 8192, 1, 1), (-0.5 / 8192, -1, -1), (2.5 / 8192, 3, 3), ((0.5 - 1e-9) / 8192, 1, 0),
])
def test_input_type_conversion_first(x, f32, f64):
    assert quantize(x, 13, "F32") == f32
    assert quantize(x, 13, "F64") == f64


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 1e39, 10 ** 400, 2.0 ** 50, True, "0.5", None])
def test_bad_inputs_rejected(bad):
    with pytest.raises(BindingError):
        quantize(bad, 13, "F32")


def test_wrong_input_counts_rejected(tiny):
    with pytest.raises(BindingError):
        encode_inputs(tiny, [TINY_X[:3]])
    with pytest.raises(BindingError):
        encode_inputs(tiny, [TINY_X, TINY_X])


BAD_FELTS = [
    "0x" + "00" * 31,
    "0010" + "00" * 29,
    "0010" + "00" * 31,
    "0010" + "00" * 29 + "0A",
    "zz" + "00" * 31,
    Q.to_bytes(32, "little").hex(),
    (Q + 4096).to_bytes(32, "little").hex(),
]


@pytest.mark.parametrize("h", BAD_FELTS)
def test_bad_felts_rejected_everywhere(tiny, tiny_proof, h):
    _, outs = split_instances(tiny, tiny_proof["instances"])
    bad = [[h] + outs[0][1:]]
    with pytest.raises(BindingError):
        hex_to_felt(h)
    with pytest.raises(BindingError):
        check_outputs(tiny, bad)
    with pytest.raises(BindingError):
        build_instances(tiny, [TINY_X], bad)
    with pytest.raises(BindingError):
        decode_outputs(tiny, bad)


def test_felt_roundtrip():
    for v in [0, 1, 4096, Q - 1, Q // 2, Q // 2 + 1]:
        assert hex_to_felt(felt_to_hex(v)) == v


def test_wrong_output_shapes_rejected(tiny, tiny_proof):
    _, outs = split_instances(tiny, tiny_proof["instances"])
    with pytest.raises(BindingError):
        check_outputs(tiny, [outs[0][:2]])
    with pytest.raises(BindingError):
        check_outputs(tiny, [outs[0], outs[0]])
    with pytest.raises(BindingError):
        split_instances(tiny, [tiny_proof["instances"][0][:-1]])
