"""Summarise a device/pi_matrix.sh run: bits decoded per release policy and channel, plus availability.

Usage: python3 observer/analyze_matrix.py runs/NAME [--bits 1101001110100111]
Each cell: bits sent, bit errors, ok answers, latency median and spread, and the decoded bits
(a constant string means the encoder's choice didn't reach the observer at all). The honest rows
also run the timing decoder as a no-signal control (it should agree with the bits only by chance).
Decoded bits from a known-bit encoder are an achieved rate for that encoder, not a capacity bound.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import decode_bits, decode_failure_position, decode_order_bits  # noqa: E402

POLICIES = ("immediate", "arrival", "fixed")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def errors(got: str, bits: str) -> int:
    return sum(a != b for a, b in zip(got, bits)) + abs(len(got) - len(bits))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--bits", default="1101001110100111")
    a = ap.parse_args(argv)
    run, bits = Path(a.run), a.bits
    decoders = {
        "timing": lambda rows: decode_bits(rows),
        "order": lambda rows: decode_order_bits(rows, 3),
        "failpos": lambda rows: decode_failure_position(rows, 16),
        "honest": lambda rows: decode_bits(rows),
    }
    print(f"{'cell':20s} {'sent':>4s} {'errors':>6s} {'ok':>7s} {'median ms':>9s} {'spread ms':>9s}  decoded")
    missing = 0
    for policy in POLICIES:
        for workload, decode in decoders.items():
            rows = load(run / f"{workload}_{policy}.trace.jsonl")
            if not rows:
                print(f"{workload + '_' + policy:20s} missing")
                missing += 1
                continue
            ms = [r["elapsed_ms"] for r in rows]
            ok = sum(r["answer"]["ok"] for r in rows)
            got = decode(rows)
            want = bits[:len(got)] if workload == "honest" else bits
            const = " (constant)" if len(set(got)) == 1 else ""
            print(f"{workload + '_' + policy:20s} {len(want):4d} {errors(got, want):6d} {ok:3d}/{len(rows):<3d} "
                  f"{statistics.median(ms):9.1f} {max(ms) - min(ms):9.1f}  {got}{const}")
    if missing:
        sys.exit(f"{missing} cell(s) missing")


if __name__ == "__main__":
    main()
