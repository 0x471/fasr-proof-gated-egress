"""Outside observer: send requests to the gate one at a time and record each answer and its time.

Usage: python observer/client.py HOST PORT X_JSON [--n N] > trace.jsonl
X_JSON is one request's x, e.g. '[[0.5, -0.2, 0.9, 0.1]]'. Each output line is
{"id", "elapsed_ms", "answer"}; elapsed_ms runs from sending the request to reading its answer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time


async def trace(host: str, port: int, xs: list) -> list[dict]:
    reader, writer = await asyncio.open_connection(host, port)
    rows = []
    try:
        for cid, x in enumerate(xs):
            body = json.dumps({"id": cid, "x": x}).encode()
            start = time.monotonic()
            writer.write(struct.pack(">I", len(body)) + body)
            await writer.drain()
            n = struct.unpack(">I", await reader.readexactly(4))[0]
            answer = json.loads(await reader.readexactly(n))
            rows.append({"id": cid, "elapsed_ms": round((time.monotonic() - start) * 1000, 1), "answer": answer})
    finally:
        writer.close()
    return rows


def decode_bits(rows: list[dict]) -> str:
    """Split answer times at the widest gap: a 1 is a slow answer. For known-bit experiments only."""
    ms = [r["elapsed_ms"] for r in rows]
    s = sorted(ms)
    gaps = [(s[i + 1] - s[i], (s[i + 1] + s[i]) / 2) for i in range(len(s) - 1)]
    threshold = max(gaps)[1] if gaps else float("inf")
    return "".join("1" if m > threshold else "0" for m in ms)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("port", type=int)
    ap.add_argument("x", help="one request's x as JSON")
    ap.add_argument("--n", type=int, default=1)
    a = ap.parse_args(argv)
    for row in asyncio.run(trace(a.host, a.port, [json.loads(a.x)] * a.n)):
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    sys.exit(main())
