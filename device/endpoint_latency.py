"""What the endpoint itself adds to each answer, on this machine, without a real prover.

Starts a local stub server that answers every request at once with the pinned tiny proof, starts
one endpoint (the Rust binary or the Python service with the EZKL CLI) on localhost, and times
sequential requests from send to reply. Proving and the network are out of the picture, so the
times are the endpoint's own: forwarding, checking the proof and releasing the answer.

Usage from the repo root on the device:
  python3 -I device/endpoint_latency.py rust --bin ~/p01-rust/pge-endpoint [--n 30]
  python3 -I device/endpoint_latency.py python --cli ~/p01-v0/ezkl [--n 30]
Prints one JSON line.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pge.binding import CircuitSpec, split_instances  # noqa: E402
from pge.protocol import Wire, frame, read_frame  # noqa: E402
from prover.server import Server  # noqa: E402

TINY = ROOT / "tests" / "fixtures" / "tiny"
X = [[0.5, -0.2, 0.9, 0.1]]


class Fixed:
    """Answers every request with the pinned tiny proof, which is valid only for X."""

    def __init__(self, spec: CircuitSpec):
        self.spec = spec
        proof = json.loads((TINY / "proof.json").read_text())
        self.outputs = split_instances(spec, proof["instances"])[1]
        self.raw = bytes(proof["proof"])

    def prove(self, x):
        return self.outputs, self.raw


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=("rust", "python"))
    ap.add_argument("--bin")
    ap.add_argument("--cli")
    ap.add_argument("--bundle", default="bundle")
    ap.add_argument("--n", type=int, default=30)
    a = ap.parse_args()
    pins = dict(line.split()[::-1] for line in (ROOT / "device/tiny.sha256").read_text().splitlines() if line.strip())
    spec = CircuitSpec.from_bytes((Path(a.bundle) / "settings.json").read_bytes(), pins["settings.json"])
    connected = asyncio.Event()
    server = Server(Fixed(spec))

    async def handle(reader, writer):
        connected.set()
        await server.handle(reader, writer)

    upstream = await asyncio.start_server(handle, "127.0.0.1", 0)
    sport = upstream.sockets[0].getsockname()[1]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    common = ["--listen", f"127.0.0.1:{port}", "--server", f"127.0.0.1:{sport}", "--bundle", a.bundle,
              "--pins", str(ROOT / "device/tiny.sha256"), "--domain", str(TINY / "domain.json"),
              "--proof-bytes", "3072", "--deadline", "10", "--release", "arrival"]
    if a.kind == "rust":
        cmd = [a.bin, *common]
    else:
        cmd = [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
               "from pge.service import main; main()", *common, "--cli", a.cli]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(connected.wait(), 30)
        await asyncio.sleep(1.0)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        wire = Wire.for_spec(spec)
        times, ok = [], 0
        for i in range(a.n):
            t = time.perf_counter()
            writer.write(frame(Wire(tuple(len(r) for r in X), ()).encode_request(i, X)))
            await writer.drain()
            raw = await asyncio.wait_for(read_frame(reader, 65536), 30)
            times.append((time.perf_counter() - t) * 1000)
            ok += bool(wire.parse_reply(raw)["ok"])
        writer.close()
    finally:
        proc.terminate()
        await proc.wait()
        upstream.close()
    print(json.dumps({"endpoint": a.kind, "n": a.n, "ok": ok, "min_ms": round(min(times), 1),
                      "median_ms": round(statistics.median(times), 1), "max_ms": round(max(times), 1)}))


if __name__ == "__main__":
    asyncio.run(main())
