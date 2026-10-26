"""The Rust gate keeps serving through resource errors, and releases fixed answers on time."""
import asyncio
import json
import os
import resource
import shutil
import statistics
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
            words = [json.loads(line)["result"] for line in logpath.read_text().splitlines()]
            assert "accept_failed" in words
        finally:
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
