"""The Rust gate keeps serving through resource errors, and releases fixed answers on time."""
import asyncio
import json
import os
import resource
import shutil
import socket
import statistics
import subprocess
import time

import pytest

from conftest import ROOT, TINY, TINY_X
from pge.protocol import Wire, frame, read_frame
from test_gate_rust import ezkl, free_port, rust_bin  # noqa: F401
from test_service import FakeProver
from prover.server import Server


async def start(binary, bundle, work, extra=(), nofile=None):
    work.mkdir()
    for name, source in (("settings.json", bundle.settings), ("vk.key", bundle.vk), ("kzg.srs", bundle.srs)):
        shutil.copy(source, work / name)
    server = Server(FakeProver(bundle.spec))
    upstream = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port, logpath = free_port(), work / "gate.log"
    args = [str(binary), "--listen", f"127.0.0.1:{port}", "--server", f"127.0.0.1:{upstream.sockets[0].getsockname()[1]}",
            "--bundle", str(work), "--pins", str(ROOT / "device/tiny.sha256"), "--domain", str(TINY / "domain.json"),
            "--proof-bytes", "3072", "--log", str(logpath), *extra]
    limit = (lambda: resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))) if nofile else None
    proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE, preexec_fn=limit)
    for _ in range(100):
        try:
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            break
        except OSError:
            await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    return proc, port, logpath, upstream, server


async def stop(proc, upstream, server):
    if proc.returncode is None:
        proc.kill()
        await proc.wait()
    upstream.close()
    await upstream.wait_closed()
    server.pool.shutdown(wait=True)


async def log_records(logpath, done, timeout=10):
    """The gate's log records once done(records) holds, reading only complete lines.

    Getting a reply doesn't mean its log record has been written yet."""
    end = time.monotonic() + timeout
    while True:
        text = logpath.read_text() if logpath.exists() else ""
        records = [json.loads(line) for line in text.split("\n")[:-1] if line]
        if done(records) or time.monotonic() > end:
            return records
        await asyncio.sleep(0.05)


async def log_words(logpath, done, timeout=10):
    records = await log_records(logpath, lambda r: done([x["result"] for x in r]), timeout)
    return [x["result"] for x in records]


async def ask(port, wire, cid=1, timeout=10):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    sent = time.monotonic()
    writer.write(frame(wire.encode_request(cid, [TINY_X])))
    await writer.drain()
    reply = wire.parse_reply(await asyncio.wait_for(read_frame(reader, 65536), timeout))
    took = time.monotonic() - sent
    writer.close()
    return reply, took


def test_running_out_of_descriptors_does_not_stop_the_gate(rust_bin, tiny_bundle, tmp_path):  # noqa: F811
    async def go():
        proc, port, logpath, upstream, server = await start(rust_bin, tiny_bundle, tmp_path / "rs", nofile=24)
        wire, held = Wire.for_spec(tiny_bundle.spec), []
        try:
            # More connections than the gate has descriptors for: accept() starts failing.
            for _ in range(40):
                try:
                    held.append((await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2))[1])
                except (OSError, asyncio.TimeoutError):
                    break
            await asyncio.sleep(0.5)
            assert proc.returncode is None, "gate exited when accept() failed"
            for w in held:
                w.close()
            await asyncio.sleep(1.5)
            reply, _ = await ask(port, wire)
            assert reply["ok"] and proc.returncode is None
            words = await log_words(logpath, lambda w: "accept" in w)
            assert "accept_failed" in words
        finally:
            await stop(proc, upstream, server)
    asyncio.run(go())


def test_requests_on_open_connections_are_answered_while_descriptors_run_out(rust_bin, tiny_bundle, tmp_path):  # noqa: F811
    async def go():
        proc, port, logpath, upstream, server = await start(rust_bin, tiny_bundle, tmp_path / "rs", nofile=24)
        wire, held = Wire.for_spec(tiny_bundle.spec), []
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            for _ in range(40):
                try:
                    held.append((await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2))[1])
                except (OSError, asyncio.TimeoutError):
                    break
            await log_words(logpath, lambda w: "accept_failed" in w)
            # Pressure stays on: the existing client still gets an answer, ok or not.
            writer.write(frame(wire.encode_request(1, [TINY_X])))
            await writer.drain()
            data = await asyncio.wait_for(read_frame(reader, 65536), 15)
            assert data is not None, "connection closed without a reply: the gate exited"
            assert wire.parse_reply(data)["id"] == 1
            assert proc.returncode is None, "gate exited under descriptor pressure"
        finally:
            for w in held:
                w.close()
            await stop(proc, upstream, server)
    asyncio.run(go())


def cpu_seconds(pid):
    """CPU time used by a process so far, from ps ([[dd-]hh:]mm:ss[.ff], Linux and macOS)."""
    out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    days, _, rest = out.rpartition("-")
    total = 0.0
    for part in rest.split(":"):
        total = total * 60 + float(part)
    return total + (int(days) * 86400 if days else 0)


def test_a_client_that_never_reads_does_not_spin_the_gate(rust_bin, tiny_bundle, tmp_path):  # noqa: F811
    async def go():
        extra = ["--idle", "1", "--deadline", "1"]
        proc, port, logpath, upstream, server = await start(rust_bin, tiny_bundle, tmp_path / "rs", extra)
        wire = Wire.for_spec(tiny_bundle.spec)
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.connect(("127.0.0.1", port))
        sock.setblocking(False)
        bad = frame(wire.encode_request(1, [[1e30, 0.0, 0.0, 0.0]]))
        try:
            # Requests the gate answers at once with bad_request; the replies are never read,
            # so the gate's output buffer for this client fills and it stops reading.
            cid, blocked_since = 1, None
            while cid < 40000:
                try:
                    sock.send(frame(wire.encode_request(cid, [[1e30, 0.0, 0.0, 0.0]])))
                    cid, blocked_since = cid + 1, None
                except BlockingIOError:
                    blocked_since = blocked_since or time.monotonic()
                    if time.monotonic() - blocked_since > 0.5:
                        break
                    await asyncio.sleep(0.01)
                except (BrokenPipeError, ConnectionResetError):
                    break
            assert cid > 2000 and len(bad) > 0
            await asyncio.sleep(1.5)  # past the idle time, inside the drain time
            before = cpu_seconds(proc.pid)
            await asyncio.sleep(3)
            used = cpu_seconds(proc.pid) - before
            assert proc.returncode is None
            assert used < 0.5, f"gate used {used:.2f} s of CPU in 3 s with an unread client"
            # The gate gives up on the client after the idle time plus the drain time.
            closed = False
            for _ in range(100):
                try:
                    sock.send(bad)
                except BlockingIOError:
                    pass
                except (BrokenPipeError, ConnectionResetError):
                    closed = True
                    break
                await asyncio.sleep(0.05)
            assert closed, "the gate never closed the client that stopped reading"
            reply, _ = await ask(port, wire)
            assert reply["ok"]
        finally:
            sock.close()
            await stop(proc, upstream, server)
    asyncio.run(go())


@pytest.mark.skipif(not os.environ.get("PGE_TIMING"), reason="timing check; set PGE_TIMING=1 on a quiet machine")
def test_fixed_release_with_spin_is_on_time(rust_bin, tiny_bundle, tmp_path):  # noqa: F811
    async def go():
        deadline = 0.5
        extra = ["--release", "fixed", "--deadline", str(deadline), "--spin-ms", "5"]
        proc, port, logpath, upstream, server = await start(rust_bin, tiny_bundle, tmp_path / "rs", extra)
        wire, late = Wire.for_spec(tiny_bundle.spec), []
        try:
            for cid in range(30):
                reply, took = await ask(port, wire, cid + 1)
                assert reply["ok"]
                late.append((took - deadline) * 1000)
        finally:
            await stop(proc, upstream, server)
        return sorted(late)
    late = asyncio.run(go())
    # Measured from the client, so admission comes after sending: never early means late >= 0.
    assert late[0] >= 0
    assert statistics.median(late) < 1.0 and late[int(0.9 * len(late))] < 1.5, late
