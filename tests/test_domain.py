import json
import random

import onnx
import pytest
from onnx import numpy_helper

from conftest import TINY, TINY_PINS, TINY_X, run
from pge.binding import BindingError, CircuitSpec, felt_to_hex, int_to_felt, quantize, quantize_inputs
from pge.domain import AffineReluDomain

N = 16384 ** 2 - 1


@pytest.fixture(scope="module")
def spec():
    return CircuitSpec.from_settings(TINY / "settings.json", TINY_PINS.settings)


@pytest.fixture(scope="module")
def dom():
    m = onnx.load(TINY / "model.onnx")
    init = {t.name: numpy_helper.to_array(t).astype(float) for t in m.graph.initializer}
    s = json.loads((TINY / "settings.json").read_text())["run_args"]
    return AffineReluDomain.from_float(init["W"].tolist(), init["b"].tolist(), param_scale=13, out_scale=26,
                                       decomp_base=s["decomp_base"], decomp_legs=s["decomp_legs"])


def test_constants(dom):
    assert dom.bound == N
    assert dom.wq == ((2244, -3772, -7521), (-7921, 5133, 6763), (1747, 3760, 715), (7128, 5175, -8147))
    assert dom.bq == (23984994, -31300542, 15411916)
    assert dom.preactivations([4096, -1638, 7373, 819])[0] == 64869479


def test_known_requests(dom, spec):
    assert dom.check(spec, [TINY_X]) == [4096, -1638, 7373, 819]
    with pytest.raises(BindingError):
        dom.check(spec, [[-3.25, 7.0, 1e-6, -0.0]])


def test_unit_box_is_inside(dom):
    rng = random.Random(1)
    for _ in range(500):
        assert dom.admits([rng.randint(-8192, 8192) for _ in range(4)])


# (j, z_j, n): pre-activation j sits exactly on or just past the bound, every other
# constraint holds with room to spare. |n_i| < 2**24, so n / 8192 is an exact F32 request.
FACETS = [
    (0, N, [-28275, -28877, 18700, 6523]),
    (0, N + 1, [-21030, -30814, 17952, 2273]),
    (0, -N, [47718, 47162, -17629, 683]),
    (0, -N - 1, [39294, 47039, -17897, 3264]),
    (1, N, [-2775, 25794, 10812, 22457]),
    (1, N + 1, [-5987, 26813, 5783, 22759]),
    (1, -N, [6178, -14364, -10556, -19403]),
    (1, -N - 1, [8165, -14303, -8456, -19541]),
    (2, N, [-34242, -16780, 13557, -12186]),
    (2, N + 1, [-34411, -15726, 15654, -10971]),
    (2, -N, [44062, 30299, -15590, 17948]),
    (2, -N - 1, [40848, 27282, -16508, 18330]),
]

# Input coordinate 2 on and just past its bound, pre-activations small. N is not an
# F32 request at scale 13, so these go to EZKL as field elements.
INPUT_FACETS = [
    ([142579886, 9307149, 268435455, -100337831], True),
    ([142579887, 9307149, 268435456, -100337832], False),
    ([-142569193, -9292891, -268435455, 100343580], True),
    ([-142569194, -9292891, -268435456, 100343580], False),
]


def _witness_ok(ezkl, d, n, as_felts=False):
    vals = [felt_to_hex(int_to_felt(v)) for v in n] if as_felts else [v / 8192 for v in n]
    (d / "input.json").write_text(json.dumps({"input_data": [vals]}))
    try:
        run(ezkl.gen_witness, str(d / "input.json"), str(d / "network.ezkl"), str(d / "witness.json"))
        return True
    except Exception:
        return False


def test_policy_matches_witness_generation(ezkl, workdir, dom):
    d = workdir / "tiny"
    rng = random.Random(2)
    for n in [[rng.randint(-20000, 20000) for _ in range(4)] for _ in range(80)]:
        assert dom.admits(n) == _witness_ok(ezkl, d, n), n


@pytest.mark.parametrize("j,zj,n", FACETS)
def test_preactivation_facets(ezkl, workdir, dom, spec, j, zj, n):
    z = dom.preactivations(n)
    assert z[j] == zj
    assert all(abs(z[k]) <= N // 2 for k in range(3) if k != j) and all(abs(v) <= N // 2 for v in n)
    inside = abs(zj) == N
    assert dom.admits(n) == inside
    assert _witness_ok(ezkl, workdir / "tiny", n) == inside
    assert quantize_inputs(spec, [[v / 8192 for v in n]]) == [n]


@pytest.mark.parametrize("n,inside", INPUT_FACETS)
def test_input_facets(ezkl, workdir, dom, n, inside):
    assert all(abs(z) <= N // 2 for z in dom.preactivations(n))
    assert dom.admits(n) == inside
    assert _witness_ok(ezkl, workdir / "tiny", n, as_felts=True) == inside


def test_bound_is_not_an_f32_request():
    assert quantize(N / 8192, 13) == N + 1


def test_input_bound_on_its_own():
    d = AffineReluDomain(wq=((0,),), bq=(0,), bound=10)
    assert d.admits([10]) and d.admits([-10])
    assert not d.admits([11]) and not d.admits([-11])
    assert not d.admits([1, 2])
