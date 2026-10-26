"""The failure-cap reference model and the bound computed from it."""
import itertools
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "observer"))
from cap_bound import brute_force, count_patterns  # noqa: E402
from cap_model import START, check, step  # noqa: E402


@pytest.mark.parametrize("window,failures,cooldown", [(w, f, c) for w, f, c in itertools.product([1, 2, 3, 4], [0, 1, 2], [1, 3]) if f < w])
def test_exact_count_matches_brute_force(window, failures, cooldown):
    for n in (1, 4, 9, 12):
        assert count_patterns(window, failures, cooldown, n) == brute_force(window, failures, cooldown, n)


def test_trip_and_cooldown_trace():
    s, out = START, []
    for _ in range(6):
        s, o = step(s, 4, 1, 2, True)
        out.append(o)
    assert out == ["pass", "trip", "capped", "capped", "pass", "trip"]


def test_the_cap_bounds_the_channel_below_one_bit_per_answer():
    n = 256
    assert count_patterns(64, 1, 64, n) < 2 ** (n // 4)
    assert count_patterns(8, 7, 1, n) < 2 ** n


@pytest.mark.parametrize("args", [(4, 1, 0), (4, 4, 1), (0, 0, 1), (4097, 0, 1), (4, -1, 1), (4.0, 1, 1)])
def test_bad_cap_settings_are_refused(args):
    with pytest.raises(ValueError):
        check(*args)
