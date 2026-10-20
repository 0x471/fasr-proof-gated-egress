"""Outside observer: send requests to the gate one at a time and record each answer and its time.

Usage: python observer/client.py HOST PORT X_JSON [--n N] [--batch K] > trace.jsonl
X_JSON is one request's x, e.g. '[[0.5, -0.2, 0.9, 0.1]]'. Each output line is
{"id", "elapsed_ms", "answer"}, printed as each answer arrives; elapsed_ms (to the microsecond) runs
from sending the request to reading its answer.
With --batch K, K requests are sent at once and their answers read in arrival order, so the
rows show the order in which the gate released them. With --clients C, C connections each send one
request per round (client 0 first, --gap-ms apart, so the gate admits them in client order); each
row also has "client", "round" and "recv_ms" (receive time since the start), and rows are in the
order answers arrived across all connections.
Requests and answers use the gate's binary format (pge/protocol.py); the observer needs no
bundle, so it sizes requests from x and answers from their frame length (y comes back flat).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pge.protocol import Wire, frame  # noqa: E402


async def trace(host: str, port: int, xs: list, on_row=None) -> list[dict]:
    reader, writer = await asyncio.open_connection(host, port)
    rows = []
    try:
        for cid, x in enumerate(xs):
            body = Wire(tuple(len(t) for t in x), ()).encode_request(cid, x)
            start = time.monotonic()
            writer.write(frame(body))
            await writer.drain()
            n = struct.unpack(">I", await reader.readexactly(4))[0]
            if n < 10 or (n - 10) % 8:
                raise ValueError(f"reply of {n} bytes is not a gate reply")
            answer = Wire((), ((n - 10) // 8,)).parse_reply(await reader.readexactly(n))
            rows.append({"id": cid, "elapsed_ms": round((time.monotonic() - start) * 1000, 3), "answer": answer})
            if on_row:
                on_row(rows[-1])
    finally:
        writer.close()
    return rows


async def trace_batches(host: str, port: int, xs: list, batch: int, on_row=None) -> list[dict]:
    """Send len(xs) requests in groups of batch, each group at once; rows are in arrival order."""
    reader, writer = await asyncio.open_connection(host, port)
    rows = []
    try:
        for base in range(0, len(xs), batch):
            start = time.monotonic()
            for cid in range(base, min(base + batch, len(xs))):
                writer.write(frame(Wire(tuple(len(t) for t in xs[cid]), ()).encode_request(cid, xs[cid])))
            await writer.drain()
            for _ in range(base, min(base + batch, len(xs))):
                n = struct.unpack(">I", await reader.readexactly(4))[0]
                if n < 10 or (n - 10) % 8:
                    raise ValueError(f"reply of {n} bytes is not a gate reply")
                answer = Wire((), ((n - 10) // 8,)).parse_reply(await reader.readexactly(n))
                rows.append({"id": answer["id"], "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
                             "answer": answer})
                if on_row:
                    on_row(rows[-1])
    finally:
        writer.close()
    return rows


async def _read_reply(reader: asyncio.StreamReader) -> dict:
    n = struct.unpack(">I", await reader.readexactly(4))[0]
    if n < 10 or (n - 10) % 8:
        raise ValueError(f"reply of {n} bytes is not a gate reply")
    return Wire((), ((n - 10) // 8,)).parse_reply(await reader.readexactly(n))


async def trace_clients(host: str, port: int, x: list, clients: int, rounds: int, gap_ms: float = 2.0,
                        on_row=None) -> list[dict]:
    """Several connections, one request each per round; rows in arrival order across connections.

    Every connection has a reader waiting before anything is sent, so each answer is timed and
    emitted as soon as it is read, and one silent connection doesn't hold back the others' rows.
    Answers that arrive together are read in the order the loop wakes their readers, which need
    not be the order on the wire; a packet capture settles close cases. "id" is the client index,
    so the batch decoders read the order across connections.
    """
    conns = [await asyncio.open_connection(host, port) for _ in range(clients)]
    body = lambda rid: frame(Wire(tuple(len(t) for t in x), ()).encode_request(rid, x))
    rows = []
    sent = [[0.0] * rounds for _ in range(clients)]
    arrived = [0] * rounds
    done = [asyncio.Event() for _ in range(rounds)]
    t0 = time.monotonic()

    async def read(c):
        for r in range(rounds):
            answer = await _read_reply(conns[c][0])
            t = time.monotonic()
            if answer["id"] != r:
                raise ValueError(f"client {c} round {r}: answer for id {answer['id']}")
            rows.append({"id": c, "client": c, "round": r, "elapsed_ms": round((t - sent[c][r]) * 1000, 3),
                         "recv_ms": round((t - t0) * 1000, 3), "answer": answer})
            if on_row:
                on_row(rows[-1])
            arrived[r] += 1
            if arrived[r] == clients:
                done[r].set()

    readers = [asyncio.create_task(read(c)) for c in range(clients)]
    try:
        for r in range(rounds):
            for c, (_, w) in enumerate(conns):
                sent[c][r] = time.monotonic()
                w.write(body(r))
                await w.drain()
                if c < clients - 1:
                    await asyncio.sleep(gap_ms / 1000)
            waiting = asyncio.create_task(done[r].wait())
            while not done[r].is_set():
                await asyncio.wait([waiting, *(t for t in readers if not t.done())],
                                   return_when=asyncio.FIRST_COMPLETED)
                for t in readers:
                    if t.done() and not t.cancelled() and t.exception() is not None:
                        waiting.cancel()
                        raise t.exception()
    finally:
        for t in readers:
            t.cancel()
        for _, w in conns:
            w.close()
    return rows


def order_index(order: list[int]) -> int:
    """Lexicographic index of a permutation of range(len(order))."""
    items, v = sorted(order), 0
    for i, e in enumerate(order):
        idx = items.index(e)
        v += idx * math.factorial(len(order) - 1 - i)
        items.pop(idx)
    return v


def decode_order_bits(rows: list[dict], batch: int) -> str:
    """Read floor(log2(batch!)) bits from the order of each full batch of answers.

    A batch whose ids aren't exactly batch consecutive ids, or whose order is a permutation the
    encoder never sends, gives m "?" so later batches stay aligned.
    """
    m = math.factorial(batch).bit_length() - 1
    out = ""
    for i in range(0, len(rows) - batch + 1, batch):
        ids = [r["id"] for r in rows[i:i + batch]]
        if sorted(ids) != list(range(min(ids), min(ids) + batch)):
            out += "?" * m
            continue
        v = order_index([cid - min(ids) for cid in ids])
        out += format(v, f"0{m}b") if v < 1 << m else "?" * m
    return out


def decode_failure_position(rows: list[dict], batch: int) -> str:
    """Read log2(batch) bits per full batch from which answer failed; "?" when not exactly one did."""
    if batch < 2 or batch & (batch - 1):
        raise ValueError("batch must be a power of 2, as the encoder requires")
    k = batch.bit_length() - 1
    out = ""
    for i in range(0, len(rows) - batch + 1, batch):
        failed = [j for j, r in enumerate(rows[i:i + batch]) if not r["answer"]["ok"]]
        out += format(failed[0], f"0{k}b") if len(failed) == 1 else "?" * k
    return out


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
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--clients", type=int, default=1, help="connections, one request each per round (--n rounds)")
    ap.add_argument("--gap-ms", type=float, default=2.0)
    a = ap.parse_args(argv)
    xs = [json.loads(a.x)] * a.n
    emit = lambda row: print(json.dumps(row), flush=True)
    if a.clients > 1:
        asyncio.run(trace_clients(a.host, a.port, json.loads(a.x), a.clients, a.n, a.gap_ms, emit))
    elif a.batch > 1:
        asyncio.run(trace_batches(a.host, a.port, xs, a.batch, emit))
    else:
        asyncio.run(trace(a.host, a.port, xs, emit))


if __name__ == "__main__":
    sys.exit(main())
