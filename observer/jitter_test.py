"""Does the residual answer-time jitter follow the server's planted bits?

Usage: python3 observer/jitter_test.py TRACE.jsonl BITS [--control HONEST.jsonl] [--perms 20000]
Splits answer times by the planted bit (1 = the server delayed), reports the difference of
means and a two-sided permutation p-value. With --control, the same labels are applied to an
honest run, where any difference is noise. Also tries the best single threshold on the times
as a decoder and reports its bit errors (tuned on the same data, so optimistic).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics


def load_ms(path: str) -> list[float]:
    return [json.loads(line)["elapsed_ms"] for line in open(path) if line.strip()]


def mean_diff(ms: list[float], labels: str) -> float:
    one = [m for m, b in zip(ms, labels) if b == "1"]
    zero = [m for m, b in zip(ms, labels) if b == "0"]
    return statistics.mean(one) - statistics.mean(zero)


def perm_p(ms: list[float], labels: str, perms: int, seed: int = 1) -> float:
    d = abs(mean_diff(ms, labels))
    rng, lab, hits = random.Random(seed), list(labels), 0
    for _ in range(perms):
        rng.shuffle(lab)
        hits += abs(mean_diff(ms, "".join(lab))) >= d
    return (hits + 1) / (perms + 1)


def best_threshold_errors(ms: list[float], labels: str) -> int:
    best = len(ms)
    for t in sorted(ms):
        for above in "01":
            guess = "".join(above if m > t else ("1" if above == "0" else "0") for m in ms)
            best = min(best, sum(a != b for a, b in zip(guess, labels)))
    return best


def report(name: str, ms: list[float], labels: str, perms: int) -> None:
    n = min(len(ms), len(labels))
    ms, labels = ms[:n], labels[:n]
    print(f"{name}: n={n} mean(1)-mean(0)={mean_diff(ms, labels):+.3f} ms  p={perm_p(ms, labels, perms):.4f}  "
          f"spread={max(ms) - min(ms):.1f} ms  best-threshold errors={best_threshold_errors(ms, labels)}/{n}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("bits")
    ap.add_argument("--control")
    ap.add_argument("--perms", type=int, default=20000)
    a = ap.parse_args(argv)
    report("signal ", load_ms(a.trace), a.bits, a.perms)
    if a.control:
        report("control", load_ms(a.control), a.bits, a.perms)


if __name__ == "__main__":
    main()
