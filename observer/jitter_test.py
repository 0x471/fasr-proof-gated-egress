"""Does the residual answer-time jitter follow the server's planted bits?

Usage: python3 observer/jitter_test.py TRACE.jsonl BITS [--control HONEST.jsonl] [--perms 20000]
Splits answer times by the planted bit (1 = the server delayed), reports the difference of
means and a two-sided permutation p-value. With --control, the same labels are applied to an
honest run, where any difference is noise. Also tries the best single threshold on the times
as a decoder and reports its bit errors (tuned on the same data, so optimistic). With --heldout,
it fits the threshold on one half and tests on the other, both ways, against a refitting null.
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


def fit_threshold(ms: list[float], labels: str) -> tuple[int, float, str]:
    """Best single threshold and polarity on this data: (errors, threshold, value above it)."""
    best = (len(ms) + 1, 0.0, "1")
    for t in sorted(set(ms)):
        for above in "10":
            other = "0" if above == "1" else "1"
            e = sum((above if m > t else other) != b for m, b in zip(ms, labels))
            if e < best[0]:
                best = (e, t, above)
    return best


def heldout_errors(ms: list[float], labels: str) -> list[float]:
    """Fit on one half and count errors on the other, both ways round (fractions)."""
    h = len(ms) // 2
    out = []
    for tr, te in ((slice(0, h), slice(h, 2 * h)), (slice(h, 2 * h), slice(0, h))):
        _, t, above = fit_threshold(ms[tr], labels[tr])
        other = "0" if above == "1" else "1"
        out.append(sum((above if m > t else other) != b for m, b in zip(ms[te], labels[te])) / h)
    return out


def heldout_report(name: str, ms: list[float], labels: str, perms: int, seed: int = 3) -> None:
    """Held-out decoder errors, with a null that refits on shuffled labels and keeps the better half."""
    n = min(len(ms), len(labels))
    ms, labels = ms[:n], labels[:n]
    errs = heldout_errors(ms, labels)
    rng, lab, hits = random.Random(seed), list(labels), 0
    for _ in range(perms):
        rng.shuffle(lab)
        hits += min(heldout_errors(ms, "".join(lab))) <= min(errs)
    print(f"{name}: held-out errors {errs[0]:.2f} / {errs[1]:.2f}; null P(better half <= {min(errs):.2f}) = "
          f"{(hits + 1) / (perms + 1):.4f} ({perms} shuffles, refit each time)")


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
    ap.add_argument("--heldout", type=int, default=0, metavar="SHUFFLES",
                    help="also run the held-out decoder with this many null shuffles (e.g. 2000)")
    a = ap.parse_args(argv)
    report("signal ", load_ms(a.trace), a.bits, a.perms)
    if a.control:
        report("control", load_ms(a.control), a.bits, a.perms)
    if a.heldout:
        heldout_report("signal ", load_ms(a.trace), a.bits, a.heldout)
        if a.control:
            heldout_report("control", load_ms(a.control), a.bits, a.heldout)


if __name__ == "__main__":
    main()
