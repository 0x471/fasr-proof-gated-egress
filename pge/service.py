"""Gate service: forward requests, check every answer's proof, release y or one generic error.

See docs/SERVICE_SPEC.md. v1 releases answers in arrival order over fixed-size binary frames
and logs fixed words only; the timing and failure channels stay open until v2 (fixed release
time). Policy(release="immediate") gives v0's release behaviour for comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import struct
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pge.binding import BindingError, CircuitSpec, build_instances, decode_outputs, encode_inputs
from pge.domain import AffineReluDomain
from pge.protocol import ProtocolError, Wire, frame, read_frame
from pge.verdict import MAX_PROOF_BYTES, Verdict

# Takes the proof JSON the device built and a timeout in seconds.
Verifier = Callable[[bytes, float], "tuple[Verdict, str]"]

# Requests queued to a server that stopped reading; past this the link is dropped.
SERVER_BUFFER_MAX = 1 << 20

# Every reason a log record can give. Anything else is logged as "other".
RELEASES = ("arrival", "immediate")

REASONS = frozenset({"request", "proof_length", "binding", "proof", "verifier", "verifier_failed",
                     "reply", "closed", "violation", "lost", "protocol", "ids", "held"})


def _number(v, lo: float, hi: float) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and lo < v <= hi


def _count(v, lo: int, hi: int) -> bool:
    return type(v) is int and lo <= v <= hi


@dataclass(frozen=True)
class Policy:
    deadline: float = 10.0
    max_pending: int = 32
    halt_after: int | None = None
    # EZKL accepts a valid proof with bytes appended, so the length must be pinned to stop the server choosing it.
    proof_bytes: int | None = None
    max_clients: int = 64
    idle: float = 60.0
    # "arrival": answers leave in the order requests were admitted, so the server can't choose
    # the order. "immediate" (v0) releases each answer as soon as it is decided.
    release: str = "arrival"

    def __post_init__(self):
        if not _number(self.deadline, 0, 3600):
            raise ValueError("deadline must be a number of seconds in (0, 3600]")
        if not _number(self.idle, 0, 3600):
            raise ValueError("idle must be a number of seconds in (0, 3600]")
        if not _count(self.max_pending, 1, 4096) or not _count(self.max_clients, 1, 4096):
            raise ValueError("max_pending and max_clients must be integers in [1, 4096]")
        if self.halt_after is not None and not _count(self.halt_after, 1, 1 << 30):
            raise ValueError("halt_after must be a positive integer or None")
        if self.proof_bytes is not None and not _count(self.proof_bytes, 1, MAX_PROOF_BYTES):
            raise ValueError("proof_bytes must be a positive integer or None")
        if self.release not in RELEASES:
            raise ValueError(f"release must be one of {', '.join(RELEASES)}")


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


def _stderr_log(rec: dict) -> None:
    print(json.dumps(rec), file=sys.stderr, flush=True)


def file_log(path: str | Path) -> Callable[[dict], None]:
    """Append one JSON line per record, written through at once (for a separate log partition)."""
    f = open(path, "a", buffering=1, encoding="utf-8")

    def log(rec: dict) -> None:
        f.write(json.dumps(rec) + "\n")

    return log


@dataclass
class Gate:
    spec: CircuitSpec
    verifier: Verifier
    domain: AffineReluDomain | None = None
    policy: Policy = Policy()
    log: Callable[[dict], None] = _stderr_log
    seq: int = 0
    failures: int = 0
    halted: bool = False
    clients: int = 0
    counts: Counter = field(default_factory=Counter)
    pending: dict[int, _Pending] = field(default_factory=dict)
    # Decided answers waiting for every earlier seq to be released (arrival order).
    _held: dict[int, tuple[_Pending, bytes, str, str]] = field(default_factory=dict)
    _next_release: int = 1
    _server: asyncio.StreamWriter | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _pool: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=1))
    _tasks: set = field(default_factory=set)

    def __post_init__(self):
        self.wire = Wire.for_spec(self.spec)

    def _log(self, rec: dict) -> None:
        # A broken log must never stop a request from being answered.
        try:
            self.log(rec)
        except Exception:
            self.counts["log_error"] += 1

    def _record(self, result: str, p: _Pending | None, reason: str = "", x_sha256: str = "") -> None:
        # Only fixed words reach the log: error text can carry server-chosen values.
        self.counts[result] += 1
        rec = {"result": result}
        if reason:
            rec["reason"] = reason if reason in REASONS else "other"
        if p is not None:
            rec.update(seq=p.seq, x_sha256=p.x_sha256, ms=round((time.monotonic() - p.started) * 1000, 1))
        elif x_sha256:
            rec["x_sha256"] = x_sha256
        self._log(rec)

    @staticmethod
    def _send(reply: Callable[[bytes], None], msg: bytes) -> None:
        try:
            reply(msg)
        except Exception:
            pass

    def _release(self, p: _Pending, msg: bytes, result: str, reason: str = "") -> None:
        """Send and log the answer for an admitted request, in seq order unless release is immediate.

        A held success is checked again when it leaves: after a halt or past its own deadline
        it becomes unavailable.
        """
        if self.policy.release == "immediate":
            self._send(p.reply, msg)
            self._record(result, p, reason)
            return
        self._held[p.seq] = (p, msg, result, reason)
        while self._next_release in self._held:
            p, msg, result, reason = self._held.pop(self._next_release)
            self._next_release += 1
            late = result == "accept" and not self.halted and time.monotonic() >= p.due
            if result == "accept" and (self.halted or late):
                msg = self.wire.reply_error(p.cid, "unavailable")
                result, reason = ("deadline", "held") if late else ("halted", "")
            self._send(p.reply, msg)
            self._record(result, p, reason)
            if late:
                self._count_failure()

    def _count_failure(self) -> None:
        self.failures += 1
        if self.policy.halt_after is not None and self.failures >= self.policy.halt_after and not self.halted:
            self.halted = True
            self._log({"result": "halted", "failures": self.failures})
            for seq in sorted(self.pending):
                self._fail(self._finish(seq), "halted", count=False)

    def _fail(self, p: _Pending, result: str, reason: str = "", count: bool = True) -> None:
        """Answer unavailable. Failures the server caused count towards halt_after, before release."""
        if count:
            self._count_failure()
        self._release(p, self.wire.reply_error(p.cid, "unavailable"), result, reason)

    def _finish(self, seq: int) -> _Pending | None:
        p = self.pending.pop(seq, None)
        if p is not None and p.timer is not None:
            p.timer.cancel()
        return p

    def submit(self, cid: int, x: list, reply: Callable[[bytes], None]) -> None:
        """Answer exactly once through reply, now or later."""
        try:
            encoded = encode_inputs(self.spec, x)
            if self.domain is not None:
                self.domain.check(self.spec, x)
        except (BindingError, TypeError) as e:
            self._send(reply, self.wire.reply_error(cid, "bad_request"))
            self._record("bad_request", None, "request")
            return
        x_sha256 = hashlib.sha256(json.dumps(encoded).encode()).hexdigest()
        if self.halted or len(self.pending) + len(self._held) >= self.policy.max_pending:
            self._send(reply, self.wire.reply_error(cid, "unavailable"))
            self._record("halted" if self.halted else "busy", None, x_sha256=x_sha256)
            return
        loop = asyncio.get_running_loop()
        self.seq += 1
        now = time.monotonic()
        p = _Pending(seq=self.seq, x=x, reply=reply, cid=cid, started=now, due=now + self.policy.deadline,
                     x_sha256=x_sha256)
        server = self._server
        if server is None or server.is_closing():
            self._fail(p, "no_server")
            return
        p.timer = loop.call_later(self.policy.deadline, self._expire, p.seq)
        self.pending[p.seq] = p
        server.write(frame(self.wire.encode_forward(p.seq, x)))
        if server.transport.get_write_buffer_size() > SERVER_BUFFER_MAX:
            self._record("server_stalled", None)
            server.close()

    def _expire(self, seq: int) -> None:
        p = self._finish(seq)
        if p is not None:
            self._fail(p, "deadline")

    def _expired(self, p: _Pending) -> bool:
        # The timer can run late if the loop stalls, so the deadline is also checked directly.
        if time.monotonic() < p.due:
            return False
        if self._finish(p.seq) is not None:
            self._fail(p, "deadline")
        return True

    def on_answer(self, seq: int, outputs: list, proof: bytes) -> None:
        p = self.pending.get(seq)
        if p is None:
            # The seq is server-chosen, so it is counted but not logged.
            self.counts["stale_answer"] += 1
            self._log({"result": "stale_answer"})
            return
        if p.checking:
            self.counts["duplicate_answer"] += 1
            self._log({"result": "duplicate_answer", "seq": seq})
            return
        p.checking = True
        task = asyncio.get_running_loop().create_task(self._check(p, outputs, proof))
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.counts["check_crashed"] += 1
            self._log({"result": "check_crashed"})

    async def _check(self, p: _Pending, outputs: list, proof: bytes) -> None:
        if self.policy.proof_bytes is not None and len(proof) != self.policy.proof_bytes:
            verdict, reason = Verdict.REJECT, "proof_length"
        else:
            try:
                instances = build_instances(self.spec, p.x, outputs)
                y = decode_outputs(self.spec, outputs)
            except BindingError:
                verdict, reason = Verdict.REJECT, "binding"
            else:
                verdict, reason = None, ""
        if verdict is None:
            data = json.dumps({"instances": instances, "proof": list(proof)}).encode()
            async with self._lock:
                if p.seq not in self.pending or self._expired(p):
                    return
                try:
                    result = await asyncio.get_running_loop().run_in_executor(
                        self._pool, self.verifier, data, p.due - time.monotonic())
                    verdict, detail = result
                    if not isinstance(verdict, Verdict) or not isinstance(detail, str):
                        raise TypeError("verifier must return (Verdict, str)")
                    reason = "proof" if verdict is Verdict.REJECT else "verifier"
                except Exception:
                    verdict, reason = Verdict.ERROR, "verifier_failed"
        if p.seq not in self.pending:
            self.counts["late_check"] += 1
            return
        if self._expired(p):
            return
        self._finish(p.seq)
        if verdict is not Verdict.ACCEPT:
            self._fail(p, verdict.value, reason)
        else:
            try:
                msg = self.wire.reply_ok(p.cid, y)
            except (ValueError, struct.error):
                self._fail(p, "error", "reply")
                return
            self._release(p, msg, "accept")

    def fail_all(self, reason: str) -> None:
        for seq in sorted(self.pending):
            p = self._finish(seq)
            if p is not None:
                self._fail(p, "disconnect", reason)

    async def serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client. After it stops sending (end of stream or idle), its pending answers are still sent."""
        if self.clients >= self.policy.max_clients:
            self._record("client_refused", None)
            writer.close()
            return
        self.clients += 1
        outstanding = 0
        drained = asyncio.Event()
        drained.set()

        def reply(msg: bytes) -> None:
            nonlocal outstanding
            outstanding -= 1
            if outstanding == 0:
                drained.set()
            if not writer.is_closing():
                writer.write(frame(msg))

        last = -1
        violated = False
        try:
            while True:
                try:
                    data = await asyncio.wait_for(read_frame(reader, self.wire.request_size), self.policy.idle)
                    if data is None:
                        break
                    cid, x = self.wire.parse_request(data)
                except asyncio.TimeoutError:
                    self._record("client_idle", None)
                    break
                except ProtocolError as e:
                    self._record("client_violation", None, "protocol")
                    violated = True
                    break
                if cid <= last:
                    self._record("client_violation", None, "ids")
                    violated = True
                    break
                last = cid
                outstanding += 1
                drained.clear()
                self.submit(cid, x, reply)
                await writer.drain()
            if not violated:
                await asyncio.wait_for(drained.wait(), self.policy.deadline + 1)
                await writer.drain()
        except (ConnectionError, OSError, asyncio.TimeoutError):
            pass
        finally:
            self.clients -= 1
            writer.close()

    async def attach_server(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Use this connection to the server until it ends or breaks the protocol."""
        self._server = writer
        reason = "closed"
        try:
            while True:
                data = await read_frame(reader, self.wire.answer_max, body_timeout=self.policy.deadline)
                if data is None:
                    break
                seq, outputs, proof = self.wire.parse_answer(data)
                self.on_answer(seq, outputs, proof)
        except ProtocolError:
            self._record("server_violation", None)
            reason = "violation"
        except (ConnectionError, OSError):
            reason = "lost"
        finally:
            if self._server is writer:
                self._server = None
            writer.close()
            self.fail_all(reason)

    async def link(self, host: str, port: int) -> None:
        delay = 0.5
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except OSError:
                self._log({"result": "connect_failed"})
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

    ap = argparse.ArgumentParser(description="gate service")
    ap.add_argument("--listen", required=True, help="outside address, host:port")
    ap.add_argument("--server", required=True, help="AI server address, host:port")
    ap.add_argument("--bundle", required=True, help="directory with settings.json, vk.key, kzg.srs")
    ap.add_argument("--pins", required=True, help="sha256sum-style file for the three bundle files")
    ap.add_argument("--cli", required=True, help="pinned EZKL CLI binary")
    ap.add_argument("--domain", help="input domain file (wq, bq, bound)")
    ap.add_argument("--deadline", type=float, default=10.0)
    ap.add_argument("--max-pending", type=int, default=32)
    ap.add_argument("--halt-after", type=int)
    ap.add_argument("--proof-bytes", type=int, required=True, help="exact raw proof length (3072 for the test bundles)")
    ap.add_argument("--log", help="append JSON log lines to this file instead of stderr")
    ap.add_argument("--release", default="arrival", choices=RELEASES,
                    help="arrival: answers leave in request order (default); immediate: as decided (v0)")
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
                log=file_log(a.log) if a.log else _stderr_log,
                policy=Policy(deadline=a.deadline, max_pending=a.max_pending, halt_after=a.halt_after,
                              proof_bytes=a.proof_bytes, release=a.release))

    async def run() -> None:
        host, port = _addr(a.listen)
        srv = await asyncio.start_server(gate.serve_client, host, port)
        shost, sport = _addr(a.server)
        await asyncio.gather(srv.serve_forever(), gate.link(shost, sport))

    asyncio.run(run())


if __name__ == "__main__":
    main()
