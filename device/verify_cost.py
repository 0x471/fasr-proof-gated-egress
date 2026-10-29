"""What one endpoint check costs on this machine: CLI threads, the limit trampoline, bundle hashing,
and how reliably the CLI runs under each address-space limit.

Usage from the repo root on the device (Linux; the memory sweep needs RLIMIT_AS):
  python3 -I device/verify_cost.py --cli ~/p01-v0/ezkl --srs ~/p01-v0/kzg.srs [--runs 20] [--sweep 20]
Prints one line per measurement: medians in ms, and accept counts per limit.
"""
from __future__ import annotations

import argparse
import collections
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pge.cli as cli  # noqa: E402
from pge.bundle import Bundle, Pins  # noqa: E402
from pge.proc import OK, run_bounded  # noqa: E402
from pge.verdict import Verdict, canonical_proof, read_proof  # noqa: E402

TINY = ROOT / "tests" / "fixtures" / "tiny"


def median_ms(f, n: int) -> float:
    ms = []
    for _ in range(n):
        t = time.perf_counter()
        f()
        ms.append((time.perf_counter() - t) * 1000)
    return statistics.median(ms)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cli", required=True)
    ap.add_argument("--srs", required=True)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--sweep", type=int, default=0, help="runs per memory limit (0: skip the sweep)")
    ap.add_argument("--limits", default="96,112,128,144,160,192,224,256,320", help="MiB, comma-separated")
    a = ap.parse_args(argv)

    sums = dict(line.split("  ")[::-1] for line in (ROOT / "device" / "tiny.sha256").read_text().splitlines() if line)
    pins = Pins(settings=sums["settings.json"], vk=sums["vk.key"], srs=sums["kzg.srs"])
    bundle = Bundle.load(TINY / "settings.json", TINY / "vk.key", a.srs, pins)
    proof = TINY / "proof.json"
    argv_cli = [a.cli, "verify", "--settings-path", str(bundle.settings), "--vk-path", str(bundle.vk),
                "--srs-path", str(bundle.srs), "--proof-path", str(proof)]
    base = {"PATH": os.defpath, "LC_ALL": "C", "HOME": os.environ.get("HOME", "/")}

    def raw(env):
        r = subprocess.run(argv_cli, env=env, capture_output=True)
        assert r.returncode == 0, r.stdout[-200:]

    def bounded(limits=None):
        status, rc, out = run_bounded(argv_cli, 10, 1 << 16, env=cli._env(), limits=limits)
        assert status == OK and rc == 0, (status, rc, out[-200:])

    def endpoint():
        v, d = cli.verify_with_cli(bundle, proof, a.cli, 10)
        assert v is Verdict.ACCEPT, d

    raw(base)
    rows = [
        ("raw CLI, default threads", lambda: raw(base)),
        ("raw CLI, RAYON_NUM_THREADS=1", lambda: raw(dict(base, RAYON_NUM_THREADS="1"))),
        ("run_bounded, no limits", bounded),
        ("run_bounded, v1 limits", lambda: bounded(cli.cli_limits(10))),
        ("bundle.check", bundle.check),
        ("read + canonical proof", lambda: canonical_proof(read_proof(proof))),
        ("verify_with_cli (endpoint path)", endpoint),
    ]
    for name, f in rows:
        print(f"{name:32s} median {median_ms(f, a.runs):7.1f} ms (n={a.runs})", flush=True)

    for mib in (int(m) for m in a.limits.split(",")) if a.sweep else ():
        cli.CLI_MEMORY = mib << 20
        c = collections.Counter()
        for _ in range(a.sweep):
            v, d = cli.verify_with_cli(bundle, proof, a.cli, 10)
            c["accept" if v is Verdict.ACCEPT else d] += 1
        print(f"memory limit {mib:4d} MiB: {dict(c)}", flush=True)


if __name__ == "__main__":
    main()
