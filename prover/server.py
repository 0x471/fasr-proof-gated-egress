"""Laptop AI server for the endpoint: prove each request with EZKL, honestly or not.

The device connects here. Each forwarded request (seq, x) is proved with the pinned tiny
bundle and answered with (seq, outputs, proof) in the binary format (docs/SERVICE_SPEC.md). --mode makes the server
misbehave, for the service tests and as the start of the W4 malicious-prover harness:

  honest           prove and answer
  drop             never answer
  delay            answer after --delay seconds
  tamper_output    change the first output value
  replay           answer with the previous request's outputs and proof
  duplicate        answer twice
  wrong_seq        answer with a seq that was never issued
  oversize         send a frame over the device's limit
  garbage          send a frame that isn't a well-formed answer
  timing_bits      hide --bits in the answer times: delay by --slot seconds for a 1
  order_bits       hide --bits in the order of each --batch answers: floor(log2(batch!)) bits
                   per batch, as the index of a permutation
  fail_position    hide --bits in which answer of each --batch fails (tampered output, so the
                   endpoint rejects it): log2(batch) bits per batch, one failure per batch

Usage: python prover/server.py --listen 127.0.0.1:8701 [--mode honest]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import math
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pge.binding import CircuitSpec, split_instances  # noqa: E402
from pge.protocol import ProtocolError, Wire, frame, read_frame  # noqa: E402

TINY = ROOT / "tests" / "fixtures" / "tiny"
TINY_SETTINGS_SHA256 = "b4a95fc7c572816034708c4e5c079f0f91f82eb9681cfa069565aacc9887aefd"
TINY_VK_SHA256 = "34ab48a8ac0946be4faf10d85699ac729adb20ec4c6251da1d1d0d4ea18a4d26"
MODES = ("honest", "drop", "delay", "tamper_output", "replay", "duplicate", "wrong_seq", "oversize", "garbage",
         "timing_bits", "order_bits", "fail_position")


def bits_per_batch(k: int) -> int:
    return math.factorial(k).bit_length() - 1


def permutation(v: int, k: int) -> list[int]:
    """The v-th permutation of range(k) in lexicographic order (factorial number system)."""
    items, out = list(range(k)), []
    for i in range(k, 0, -1):
        idx, v = divmod(v, math.factorial(i - 1))
        out.append(items.pop(idx))
    return out


def _run(fn, *a, **k):
    async def _c():
        r = fn(*a, **k)
        return await r if inspect.isawaitable(r) else r
    return asyncio.run(_c())


class Prover:
    """Keys come from setup on the pinned compiled circuit; setup is deterministic, so the vk must match its pin."""

    def __init__(self, srs: str | Path, fixture: Path = TINY):
        import ezkl

        self.ezkl = ezkl
        self.dir = tempfile.TemporaryDirectory(prefix="pge-prover-")
        self.work = Path(self.dir.name)
        self.network = fixture / "network.ezkl"
        self.srs = Path(srs)
        self.spec = CircuitSpec.from_settings(fixture / "settings.json", TINY_SETTINGS_SHA256)
        vk, self.pk = self.work / "vk.key", self.work / "pk.key"
        if _run(ezkl.setup, str(self.network), str(vk), str(self.pk), srs_path=str(self.srs)) is not True:
            raise RuntimeError("setup failed")
        if hashlib.sha256(vk.read_bytes()).hexdigest() != TINY_VK_SHA256:
            raise RuntimeError("setup produced a vk that doesn't match the pin")

    def prove(self, x: list) -> tuple[list[list[str]], bytes]:
        inp, wit, out = self.work / "input.json", self.work / "witness.json", self.work / "proof.json"
        for p in (wit, out):
            p.unlink(missing_ok=True)
        inp.write_text(json.dumps({"input_data": x}))
        _run(self.ezkl.gen_witness, str(inp), str(self.network), str(wit))
        _run(self.ezkl.prove, str(wit), str(self.network), str(self.pk), str(out), srs_path=str(self.srs))
        proof = json.loads(out.read_text())
        _, outputs = split_instances(self.spec, proof["instances"])
        return outputs, bytes(proof["proof"])


class Server:
    def __init__(self, prover, mode: str = "honest", delay: float = 0.0, bits: str = "", slot: float = 0.2,
                 batch: int = 2):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if set(bits) - {"0", "1"}:
            raise ValueError("bits must be a string of 0 and 1")
        if type(batch) is not int or not 2 <= batch <= (1024 if mode == "fail_position" else 8):
            raise ValueError("batch must be an integer in [2, 8], or a power of 2 up to 1024 for fail_position")
        if mode == "fail_position" and batch & (batch - 1):
            raise ValueError("batch must be a power of 2 for fail_position")
        self.prover, self.mode, self.delay, self.bits, self.slot = prover, mode, delay, bits, slot
        self.batch = batch
        self.held: list[tuple[int, list, bytes]] = []
        self.used = 0
        self.wire = Wire.for_spec(prover.spec)
        self.previous: tuple[list, bytes] | None = None
        self.sent = 0
        self.requests: list[dict] = []
        self.pool = ThreadPoolExecutor(max_workers=1)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await read_frame(reader, self.wire.request_size)
                if data is None:
                    break
                seq, x = self.wire.parse_forward(data)
                self.requests.append({"seq": seq, "x": x})
                try:
                    outputs, proof = await loop.run_in_executor(self.pool, self.prover.prove, x)
                except Exception as e:
                    print(f"seq {seq}: cannot prove: {type(e).__name__}", file=sys.stderr)
                    continue
                if self.mode == "order_bits":
                    self.held.append((seq, outputs, proof))
                    if len(self.held) == self.batch:
                        for i in self._order():
                            await self._send(writer, *self.held[i])
                        self.held = []
                    continue
                await self._send(writer, seq, outputs, proof)
                self.previous = (outputs, proof)
        except (ProtocolError, ConnectionError):
            pass
        finally:
            writer.close()

    def _order(self) -> list[int]:
        m = bits_per_batch(self.batch)
        chunk = self.bits[self.used:self.used + m].ljust(m, "0")
        self.used += m
        return permutation(int(chunk, 2) if m else 0, self.batch)

    async def _send(self, writer, seq: int, outputs: list, proof: bytes) -> None:
        m = self.mode
        if m == "drop":
            return
        if m == "delay":
            await asyncio.sleep(self.delay)
        if m == "timing_bits" and self.sent < len(self.bits) and self.bits[self.sent] == "1":
            await asyncio.sleep(self.slot)
        if m == "fail_position":
            k = self.batch.bit_length() - 1
            block, pos = divmod(self.sent, self.batch)
            chunk = self.bits[block * k:(block + 1) * k].ljust(k, "0")
            if pos == int(chunk, 2):
                m = "tamper_output"
        if m == "tamper_output":
            first = outputs[0][0]
            outputs = [[("1" if first[0] == "0" else "0") + first[1:]] + outputs[0][1:]] + outputs[1:]
        if m == "replay" and self.previous is not None:
            outputs, proof = self.previous
        if m == "wrong_seq":
            seq += 1000
        if m == "oversize":
            payload = b" " * (self.wire.answer_max + 1)
        elif m == "garbage":
            payload = b"not an answer"
        else:
            payload = self.wire.encode_answer(seq, outputs, proof)
        writer.write(frame(payload))
        if m == "duplicate":
            writer.write(frame(payload))
        self.sent += 1
        await writer.drain()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", default="127.0.0.1:8701")
    ap.add_argument("--srs", default=str(ROOT / "prover" / "artifacts" / "kzg.srs"))
    ap.add_argument("--mode", default="honest", choices=MODES)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--bits", default="")
    ap.add_argument("--slot", type=float, default=0.2)
    ap.add_argument("--batch", type=int, default=2)
    a = ap.parse_args(argv)
    server = Server(Prover(a.srs), a.mode, a.delay, a.bits, a.slot, a.batch)
    host, _, port = a.listen.rpartition(":")

    async def run() -> None:
        srv = await asyncio.start_server(server.handle, host, int(port))
        print(f"listening on {a.listen}, mode {a.mode}", file=sys.stderr)
        await srv.serve_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
