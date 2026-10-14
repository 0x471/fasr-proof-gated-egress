import json
import random

import onnx
import pytest
from onnx import numpy_helper

from conftest import TINY, TINY_PINS, TINY_X, run
from pge.binding import BindingError, CircuitSpec
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
    # the fixture proof's output is exactly the first pre-activation
    assert dom.preactivations([4096, -1638, 7373, 819])[0] == 64869479


def test_known_requests(dom, spec):
    assert dom.check(spec, [TINY_X]) == [4096, -1638, 7373, 819]
    with pytest.raises(BindingError):
        dom.check(spec, [[-3.25, 7.0, 1e-6, -0.0]])


def test_unit_box_is_inside(dom):
    rng = random.Random(1)
    for _ in range(500):
        assert dom.admits([rng.randint(-8192, 8192) for _ in range(4)])


def _crossing(dom, j, rest):
    """Find n0 with z_j(n0) inside the bound and z_j(n0 + step) just outside."""
    w = dom.wq[0][j]
    step = 1 if w > 0 else -1
    base = sum(rest[i - 1] * dom.wq[i][j] for i in range(1, 4)) + dom.bq[j]
    n0 = (N - base) // abs(w) * step
    while abs(base + (n0 + step) * w) <= N:
        n0 += step
    return [n0] + rest, [n0 + step] + rest


def _witness_ok(ezkl, d, n):
    (d / "input.json").write_text(json.dumps({"input_data": [[v / 8192 for v in n]]}))
    try:
        run(ezkl.gen_witness, str(d / "input.json"), str(d / "network.ezkl"), str(d / "witness.json"))
        return True
    except Exception:
        return False


def test_policy_matches_witness_generation(ezkl, workdir, dom):
    d = workdir / "tiny"
    rng = random.Random(2)
    cases = [[rng.randint(-20000, 20000) for _ in range(4)] for _ in range(80)]
    for j in range(3):
        inside, outside = _crossing(dom, j, [0, 0, 0])
        cases += [inside, outside]
    for n in cases:
        assert dom.admits(n) == _witness_ok(ezkl, d, n), n


def test_input_bound_on_its_own():
    d = AffineReluDomain(wq=((0,),), bq=(0,), bound=10)
    assert d.admits([10]) and d.admits([-10])
    assert not d.admits([11]) and not d.admits([-11])
    assert not d.admits([1, 2])
