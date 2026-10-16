"""v0 gate service: forward requests, check every answer's proof, release y or one generic error.

See docs/SERVICE_SPEC.md. This is the baseline: it releases as soon as a check finishes, so
timing, ordering and failure channels stay open on purpose until v1 and v2.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pge.binding import BindingError, CircuitSpec, build_instances, decode_outputs, encode_inputs
from pge.domain import AffineReluDomain
from pge.protocol import (CLIENT_MAX, REPLY_MAX, SERVER_MAX, ProtocolError, client_error, client_ok, frame,
                          parse_client, parse_server, read_frame, server_request)
from pge.verdict import Verdict

# Takes the proof JSON the device built and a timeout in seconds.
Verifier = Callable[[bytes, float], "tuple[Verdict, str]"]


@dataclass(frozen=True)
class Policy:
    deadline: float = 10.0
    max_pending: int = 32
    halt_after: int | None = None


@dataclass
class _Pending:
    seq: int
    x: list
    reply: Callable[[bytes], None]
    cid: int
    started: float
    due: float
    x_sha256: str
    timer: asyncio.TimerHandle | None = None
    checking: bool = False


@dataclass
class Gate:
    spec: CircuitSpec
    verifier: Verifier
    domain: AffineReluDomain | None = None
    policy: Policy = Policy()
    log: Callable[[dict], None] = lambda rec: print(json.dumps(rec), file=sys.stderr, flush=True)
    seq: int = 0
    failures: int = 0
    halted: bool = False
    counts: Counter = field(default_factory=Counter)
    pending: dict[int, _Pending] = field(default_factory=dict)
    _server: asyncio.StreamWriter | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _pool: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=1))

    def _record(self, result: str, p: _Pending | None, detail: str = "", x_sha256: str = "") -> None:
        self.counts[result] += 1
        rec = {"result": result, "detail": detail[:200]}
        if p is not None:
            rec.update(seq=p.seq, x_sha256=p.x_sha256, ms=round((time.monotonic() - p.started) * 1000, 1))
        elif x_sha256:
            rec["x_sha256"] = x_sha256
        self.log(rec)

    def _fail(self, p: _Pending, result: str, detail: str) -> None:
        self.failures += 1
        if self.policy.halt_after is not None and self.failures >= self.policy.halt_after and not self.halted:
            self.halted = True
            self.log({"result": "halted", "detail": f"{self.failures} failures"})
        self._record(result, p, detail)
        p.reply(client_error(p.cid, "unavailable"))

    def _finish(self, seq: int) -> _Pending | None:
        p = self.pending.pop(seq, None)
        if p is not None and p.timer is not None:
            p.timer.cancel()
        return p

    def submit(self, cid: int, x: list, reply: Callable[[bytes], None]) -> None:
        try:
            encoded = encode_inputs(self.spec, x)
            if self.domain is not None:
                self.domain.check(self.spec, x)
        except (BindingError, TypeError) as e:
            self._record("bad_request", None, str(e))
            reply(client_error(cid, "bad_request"))
            return
        x_sha256 = hashlib.sha256(json.dumps(encoded).encode()).hexdigest()
        if self.halted:
            self._record("halted", None, x_sha256=x_sha256)
            reply(client_error(cid, "unavailable"))
            return
        if len(self.pending) >= self.policy.max_pending:
            self._record("busy", None, x_sha256=x_sha256)
            reply(client_error(cid, "unavailable"))
            return
        loop = asyncio.get_running_loop()
        self.seq += 1
        now = time.monotonic()
        p = _Pending(seq=self.seq, x=x, reply=reply, cid=cid, started=now, due=now + self.policy.deadline,
                     x_sha256=x_sha256)
        if self._server is None or self._server.is_closing():
            self._fail(p, "no_server", "no server connection")
            return
        p.timer = loop.call_later(self.policy.deadline, self._expire, p.seq)
        self.pending[p.seq] = p
        self._server.write(frame(server_request(p.seq, x)))

    def _expire(self, seq: int) -> None:
        p = self._finish(seq)
        if p is not None:
            self._fail(p, "deadline", f"no checked answer within {self.policy.deadline} s")

    def on_answer(self, seq: int, outputs: list, proof: bytes) -> None:
        p = self.pending.get(seq)
        if p is None:
            self.counts["stale_answer"] += 1
            self.log({"result": "stale_answer", "seq": seq})
            return
        if p.checking:
            self.counts["duplicate_answer"] += 1
            self.log({"result": "duplicate_answer", "seq": seq})
            return
        p.checking = True
        asyncio.get_running_loop().create_task(self._check(p, outputs, proof))

    async def _check(self, p: _Pending, outputs: list, proof: bytes) -> None:
        try:
            instances = build_instances(self.spec, p.x, outputs)
            y = decode_outputs(self.spec, outputs)
        except BindingError as e:
            verdict, detail = Verdict.REJECT, f"binding: {e}"
        else:
            data = json.dumps({"instances": instances, "proof": list(proof)}).encode()
            async with self._lock:
                if p.seq not in self.pending:
                    return
                budget = max(p.due - time.monotonic(), 0.001)
                try:
                    verdict, detail = await asyncio.get_running_loop().run_in_executor(
                        self._pool, self.verifier, data, budget)
                except Exception as e:
                    verdict, detail = Verdict.ERROR, f"verifier raised {type(e).__name__}"
        if self._finish(p.seq) is None:
            self.counts["late_check"] += 1
            return
        if verdict is Verdict.ACCEPT:
            msg = client_ok(p.cid, y)
            if len(msg) > REPLY_MAX:
                self._fail(p, "error", "reply too large")
                return
            self._record("accept", p)
            p.reply(msg)
        else:
            self._fail(p, verdict.value, detail)

    def fail_all(self, detail: str) -> None:
        for seq in sorted(self.pending):
            p = self._finish(seq)
            self._fail(p, "disconnect", detail)

    async def serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def reply(msg: bytes) -> None:
            if not writer.is_closing():
                writer.write(frame(msg))

        last = -1
        try:
            while True:
                try:
                    data = await read_frame(reader, CLIENT_MAX)
                    if data is None:
                        break
                    cid, x = parse_client(data)
                except ProtocolError as e:
                    self._record("client_violation", None, str(e))
                    break
                if cid <= last:
                    self._record("client_violation", None, "ids must be strictly increasing")
                    break
                last = cid
                self.submit(cid, x, reply)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def attach_server(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Use this connection to the server until it ends or breaks the protocol."""
        self._server = writer
        detail = "server closed the connection"
        try:
            while True:
                data = await read_frame(reader, SERVER_MAX)
                if data is None:
                    break
                seq, outputs, proof = parse_server(data)
                self.on_answer(seq, outputs, proof)
        except ProtocolError as e:
            self._record("server_violation", None, str(e))
            detail = f"server violation: {e}"
        except (ConnectionError, OSError) as e:
            detail = f"server connection lost: {type(e).__name__}"
        finally:
            if self._server is writer:
                self._server = None
            writer.close()
            self.fail_all(detail)

    async def link(self, host: str, port: int) -> None:
        delay = 0.5
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except OSError as e:
                self.log({"result": "connect_failed", "detail": type(e).__name__})
            else:
                delay = 0.5
                await self.attach_server(reader, writer)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)


def load_domain(path: str | Path) -> AffineReluDomain:
    d = json.loads(Path(path).read_text())
    if not isinstance(d, dict) or set(d) != {"wq", "bq", "bound"}:
        raise ValueError("domain file must have exactly wq, bq and bound")
    ints = lambda v: isinstance(v, list) and all(type(e) is int for e in v)
    if not (isinstance(d["wq"], list) and d["wq"] and all(ints(r) and len(r) == len(d["bq"]) for r in d["wq"])
            and ints(d["bq"]) and d["bq"] and type(d["bound"]) is int and d["bound"] > 0):
        raise ValueError("domain file has the wrong types or shapes")
    return AffineReluDomain(wq=tuple(map(tuple, d["wq"])), bq=tuple(d["bq"]), bound=d["bound"])


def _addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    return host, int(port)


def main(argv: list[str] | None = None) -> None:
    from pge.bundle import Bundle, Pins
    from pge.cli import verify_bytes_with_cli

    ap = argparse.ArgumentParser(description="v0 gate service")
    ap.add_argument("--listen", required=True, help="outside address, host:port")
    ap.add_argument("--server", required=True, help="AI server address, host:port")
    ap.add_argument("--bundle", required=True, help="directory with settings.json, vk.key, kzg.srs")
    ap.add_argument("--pins", required=True, help="sha256sum-style file for the three bundle files")
    ap.add_argument("--cli", required=True, help="pinned EZKL CLI binary")
    ap.add_argument("--domain", help="input domain file (wq, bq, bound)")
    ap.add_argument("--deadline", type=float, default=10.0)
    ap.add_argument("--max-pending", type=int, default=32)
    ap.add_argument("--halt-after", type=int)
    a = ap.parse_args(argv)

    sums = {}
    for line in Path(a.pins).read_text().splitlines():
        digest, _, name = line.partition("  ")
        sums[name] = digest
    pins = Pins(settings=sums["settings.json"], vk=sums["vk.key"], srs=sums["kzg.srs"])
    d = Path(a.bundle)
    bundle = Bundle.load(d / "settings.json", d / "vk.key", d / "kzg.srs", pins)
    gate = Gate(spec=bundle.spec, verifier=lambda data, t: verify_bytes_with_cli(bundle, data, a.cli, t),
                domain=load_domain(a.domain) if a.domain else None,
                policy=Policy(deadline=a.deadline, max_pending=a.max_pending, halt_after=a.halt_after))

    async def run() -> None:
        host, port = _addr(a.listen)
        srv = await asyncio.start_server(gate.serve_client, host, port)
        shost, sport = _addr(a.server)
        await asyncio.gather(srv.serve_forever(), gate.link(shost, sport))

    asyncio.run(run())


if __name__ == "__main__":
    main()
