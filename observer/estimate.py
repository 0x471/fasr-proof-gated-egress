"""Estimate how much a planted bit tells an observer about answer times: mutual information in bits.

Usage: python3 observer/estimate.py TRACE.jsonl BITS [--bins 4] [--control HONEST.jsonl]
       python3 observer/estimate.py --selftest

Answer times are cut into equal-count bins; I(bit; bin) is estimated with the plug-in formula
and the Miller-Madow bias correction, and compared with a shuffled-label null (the same
estimate after randomly reassigning the bits) and a bootstrap interval. The bits are uniform
and independent by construction, so I(bit; bin) is what one answer reveals to an observer who
sees only that answer's time, under this binning. It is an estimate of this channel as used,
not the channel's capacity: a cleverer encoder or observer could do better.
"""
from __future__ import annotations

import argparse
import json
import math
import random


def entropy(counts: list[int]) -> float:
    n = sum(counts)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def mi_plugin(xs: list[int], ys: list[int], kx: int, ky: int) -> float:
    n = len(xs)
    joint = [[0] * ky for _ in range(kx)]
    for x, y in zip(xs, ys):
        joint[x][y] += 1
    px = [sum(r) for r in joint]
    py = [sum(joint[i][j] for i in range(kx)) for j in range(ky)]
    hxy = entropy([c for r in joint for c in r])
    mi = entropy(px) + entropy(py) - hxy
    # Miller-Madow: each entropy is biased low by (nonzero cells - 1) / (2 n ln 2).
    nz = lambda cs: sum(1 for c in cs if c)
    corr = ((nz(px) - 1) + (nz(py) - 1) - (nz([c for r in joint for c in r]) - 1)) / (2 * n * math.log(2))
    return mi + corr


def binned(ms: list[float], bins: int) -> list[int]:
    order = sorted(range(len(ms)), key=lambda i: ms[i])
    out = [0] * len(ms)
    for rank, i in enumerate(order):
        out[i] = rank * bins // len(ms)
    return out


def estimate(bits: list[int], ms: list[float], bins: int, perms: int = 2000, boots: int = 1000, seed: int = 1) -> dict:
    ys = binned(ms, bins)
    mi = mi_plugin(bits, ys, 2, bins)
    rng = random.Random(seed)
    lab = list(bits)
    null = []
    for _ in range(perms):
        rng.shuffle(lab)
        null.append(mi_plugin(lab, ys, 2, bins))
    p = (sum(v >= mi for v in null) + 1) / (perms + 1)
    boot = []
    n = len(bits)
    for _ in range(boots):
        idx = [rng.randrange(n) for _ in range(n)]
        boot.append(mi_plugin([bits[i] for i in idx], binned([ms[i] for i in idx], bins), 2, bins))
    boot.sort()
    null.sort()
    return {"n": n, "bins": bins, "mi": mi, "ci95": (boot[int(0.025 * boots)], boot[int(0.975 * boots) - 1]),
            "null95": null[int(0.95 * perms) - 1], "p": p}


def h2(p: float) -> float:
    return 0.0 if p in (0, 1) else -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def selftest() -> None:
    """Synthetic channels with known mutual information for a uniform bit."""
    rng = random.Random(7)
    print(f"{'channel':38s} {'n':>5s} {'true':>6s} {'est':>6s} {'95% CI':>15s} {'null95':>7s} {'p':>7s}")
    cases = []
    for n in (128, 512):
        cases += [
            (f"no signal (time ignores bit), n={n}", n, 0.0, lambda b: rng.gauss(0, 1)),
            (f"perfect (1 s apart), n={n}", n, 1.0, lambda b: b * 1000 + rng.gauss(0, 1)),
            (f"BSC p=0.25 via two levels, n={n}", n, 1 - h2(0.25), lambda b: (b if rng.random() > 0.25 else 1 - b) + rng.gauss(0, 0.01)),
            (f"Gaussian shift d=0.5 sd, n={n}", n, None, lambda b: b * 0.5 + rng.gauss(0, 1)),
        ]
    for name, n, true, f in cases:
        bits = [rng.randrange(2) for _ in range(n)]
        ms = [f(b) for b in bits]
        bins = 2 if true is not None and true > 0 and "BSC" in name else 4
        r = estimate(bits, ms, bins, perms=500, boots=300)
        t = "?" if true is None else f"{true:.3f}"
        print(f"{name:38s} {n:5d} {t:>6s} {r['mi']:6.3f} [{r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}] {r['null95']:7.3f} {r['p']:7.4f}")
    # Binary-input Gaussian channel with shift d=0.5 sd: the binned estimate should sit below the
    # exact value, about 0.045 bit (numerical integration below), because binning loses information.
    d, s = 0.5, 1.0
    pdf = lambda x, m: math.exp(-((x - m) / s) ** 2 / 2) / (s * math.sqrt(2 * math.pi))
    xs = [-8 + i * 0.001 for i in range(16001 + int(d / 0.001))]
    exact = 0.0
    for x in xs:
        p0, p1 = pdf(x, 0), pdf(x, d)
        pm = (p0 + p1) / 2
        for p in (p0, p1):
            if p > 0:
                exact += 0.5 * p * math.log2(p / pm) * 0.001
    print(f"exact I for the Gaussian shift d=0.5 sd: {exact:.3f} bit")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace", nargs="?")
    ap.add_argument("bits", nargs="?")
    ap.add_argument("--bins", type=int, default=4)
    ap.add_argument("--control")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest()
        return
    bits = [int(c) for c in a.bits]
    for name, path in (("signal", a.trace), ("control", a.control)):
        if not path:
            continue
        ms = [json.loads(line)["elapsed_ms"] for line in open(path) if line.strip()]
        n = min(len(ms), len(bits))
        r = estimate(bits[:n], ms[:n], a.bins)
        print(f"{name}: n={r['n']} bins={r['bins']} I={r['mi']:.4f} bit/answer, 95% CI [{r['ci95'][0]:.4f}, "
              f"{r['ci95'][1]:.4f}], null 95th pct {r['null95']:.4f}, p={r['p']:.4f}")


if __name__ == "__main__":
    main()
