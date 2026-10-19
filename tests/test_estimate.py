import random
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "observer"))
from estimate import estimate  # noqa: E402


def _channel(n, f, seed):
    rng = random.Random(seed)
    bits = [rng.randrange(2) for _ in range(n)]
    return bits, [f(b, rng) for b in bits]


def test_no_signal_is_not_significant():
    bits, ms = _channel(512, lambda b, r: r.gauss(0, 1), 11)
    out = estimate(bits, ms, 4, perms=500, boots=200)
    assert out["p"] > 0.05 and out["mi"] < out["null95"]


def test_binary_symmetric_channel_is_recovered():
    # Each bit flips with probability 0.25: I = 1 - H(0.25), about 0.189 bit.
    bits, ms = _channel(512, lambda b, r: (b if r.random() > 0.25 else 1 - b) + r.gauss(0, 0.01), 12)
    out = estimate(bits, ms, 2, perms=500, boots=200)
    assert abs(out["mi"] - 0.189) < 0.06 and out["ci95"][0] < 0.189 < out["ci95"][1] and out["p"] < 0.01


def test_a_clear_signal_is_found():
    bits, ms = _channel(128, lambda b, r: b * 1000 + r.gauss(0, 1), 13)
    out = estimate(bits, ms, 4, perms=500, boots=200)
    assert out["mi"] > 0.7 and out["p"] < 0.01
