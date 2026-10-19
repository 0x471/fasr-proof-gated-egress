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


def test_plugin_estimate_matches_a_hand_computed_table():
    import math
    from estimate import mi_plugin
    # Joint counts [[3, 1], [1, 3]], n = 8: plug-in MI = 2 - H(3/8, 1/8, 1/8, 3/8), and the
    # Miller-Madow terms add ((2-1) + (2-1) - (4-1)) / (2 n ln 2).
    xs, ys = [0, 0, 0, 0, 1, 1, 1, 1], [0, 0, 0, 1, 0, 1, 1, 1]
    hxy = -(2 * (3 / 8) * math.log2(3 / 8) + 2 * (1 / 8) * math.log2(1 / 8))
    want = 2 - hxy - 1 / (16 * math.log(2))
    assert abs(mi_plugin(xs, ys, 2, 2) - want) < 1e-12


def test_equal_times_carry_no_information():
    # Found by review: ties used to be split by row order, so constant times read as 1 bit.
    out = estimate([0] * 64 + [1] * 64, [4000.0] * 128, 4, perms=200, boots=100)
    assert abs(out["mi"]) < 1e-12 and out["p"] > 0.5


def test_coarsely_rounded_times_without_signal_are_not_significant():
    bits, ms = _channel(256, lambda b, r: round(r.gauss(4000, 1), 1), 14)
    assert estimate(bits, ms, 4, perms=500, boots=100)["p"] > 0.05
