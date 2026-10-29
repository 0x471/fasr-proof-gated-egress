"""Upper bound on what the cap-processed status sequence can carry under the failure cap.

What it counts: the ok/unavailable status of the replies to n admitted requests, in the order
they leave the endpoint, starting from an empty cap. Under the cap (observer/cap_model.py, the
reference the Rust endpoint's cap is tested against) only some of the 2^n such sequences can occur,
whatever the server does; log2 of their number bounds the bits that sequence can carry. Counted
exactly by dynamic programming over the cap's states.

What it doesn't count: replies that never pass the cap (busy and halted answers at admission),
when replies leave, ids and interleaving across clients, missing replies and the endpoint's logs.
Under fixed release with halt_after off, busy answers don't depend on the server (every admitted
request holds its slot until its fixed release time); under arrival release the server also
controls timing and, by answering slowly, which later requests are busy.

    python observer/cap_bound.py --window 64 --failures 1 --cooldown 64 --n 4096
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cap_model import START, check, step  # noqa: E402


def count_patterns(window: int, failures: int, cooldown: int, n: int) -> int:
    """Number of distinct ok/unavailable sequences of length n the cap allows."""
    check(window, failures, cooldown)
    states = {START: 1}
    for _ in range(n):
        nxt: dict = defaultdict(int)
        for s, k in states.items():
            if s[1]:
                # In a cooldown every reply is unavailable, so the server's choice is invisible.
                nxt[step(s, window, failures, cooldown, False)[0]] += k
                continue
            for failed in (False, True):
                nxt[step(s, window, failures, cooldown, failed)[0]] += k
        states = nxt
    return sum(states.values())


def brute_force(window: int, failures: int, cooldown: int, n: int) -> int:
    """The same count by trying every failure pattern the server could choose (small n only)."""
    seen = set()
    for choice in product((False, True), repeat=n):
        s, out = START, []
        for failed in choice:
            s, o = step(s, window, failures, cooldown, failed)
            out.append(o == "pass" and not failed)
        seen.add(tuple(out))
    return len(seen)


def bits(window: int, failures: int, cooldown: int, n: int) -> float:
    return math.log2(count_patterns(window, failures, cooldown, n))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=int, required=True)
    ap.add_argument("--failures", type=int, required=True)
    ap.add_argument("--cooldown", type=int, required=True)
    ap.add_argument("--n", type=int, default=1024, help="number of replies")
    a = ap.parse_args(argv)
    b = bits(a.window, a.failures, a.cooldown, a.n)
    print(f"at most {b:.2f} bits in the statuses of {a.n} admitted replies "
          f"({b / a.n:.4f} bit per admitted reply; no cap: 1). Not counted: busy replies, "
          "timing, ids, missing replies, logs (see the module docstring)")


if __name__ == "__main__":
    main()
