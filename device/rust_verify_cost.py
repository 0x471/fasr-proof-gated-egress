"""What one Rust gate check costs on this machine, and that it rejects what it should.

Runs the gate binary's verifier child (`pge-gate --verify-child`, the process the gate starts for
every proof) on the pinned tiny proof, with RAYON_NUM_THREADS=1 as the gate sets it and with the
default thread count. Also checks one valid proof, a changed output, a changed input, a garbage
proof and a truncated job. Uses only the standard library, so it runs on the stock Pi.

Usage from the repo root on the device:
  python3 -I device/rust_verify_cost.py --bin ~/p01-rust/pge-gate [--bundle bundle] [--runs 20]
Prints one JSON line per measurement.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import struct
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TINY = ROOT / "tests" / "fixtures" / "tiny"


def pins(path: Path) -> dict[str, str]:
    return {name: digest for digest, name in (line.split() for line in path.read_text().splitlines() if line.strip())}


def job(bundle: Path, proof: bytes) -> bytes:
    chunks = [(bundle / n).read_bytes() for n in ("settings.json", "vk.key", "kzg.srs")] + [proof]
    return b"".join(struct.pack(">I", len(c)) + c for c in chunks)


def run(cmd: list[str], data: bytes, threads: str | None) -> tuple[int, bytes, float]:
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    if threads:
        env["RAYON_NUM_THREADS"] = threads
    t = time.perf_counter()
    r = subprocess.run(cmd, input=data, capture_output=True, env=env, timeout=30)
    return r.returncode, r.stdout, (time.perf_counter() - t) * 1000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--bundle", default="bundle")
    ap.add_argument("--runs", type=int, default=20)
    a = ap.parse_args()
    p = pins(ROOT / "device" / "tiny.sha256")
    cmd = [a.bin, "--verify-child", "2", "3072", p["settings.json"], p["vk.key"], p["kzg.srs"]]
    bundle = Path(a.bundle)
    proof = json.loads((TINY / "proof.json").read_text())

    def changed(index: int) -> bytes:
        q = json.loads(json.dumps(proof))
        q["instances"][0][index] = "00" * 32
        return json.dumps(q).encode()

    valid = job(bundle, json.dumps(proof).encode())
    cases = {
        "valid": (valid, (0, b"accept\n")),
        "changed_output": (job(bundle, changed(-3)), (1, b"reject\n")),
        "changed_input": (job(bundle, changed(0)), (1, b"reject\n")),
        "garbage_proof": (job(bundle, b"garbage"), (1, b"reject\n")),
        "truncated_job": (valid[:-1], (2, b"")),
    }
    for name, (data, want) in cases.items():
        rc, out, ms = run(cmd, data, "1")
        print(json.dumps({"case": name, "exit": rc, "stdout": out.decode(errors="replace").strip(),
                          "expected": (rc, out) == want, "ms": round(ms, 1)}), flush=True)
    for threads in ("1", None):
        times = []
        for _ in range(a.runs):
            rc, out, ms = run(cmd, valid, threads)
            if (rc, out) != (0, b"accept\n"):
                raise SystemExit(f"valid proof not accepted: {rc} {out!r}")
            times.append(ms)
        print(json.dumps({"measure": "verify_child", "threads": threads or "default", "runs": a.runs,
                          "min_ms": round(min(times), 1), "median_ms": round(statistics.median(times), 1),
                          "max_ms": round(max(times), 1), "cores": os.cpu_count()}), flush=True)


if __name__ == "__main__":
    main()
