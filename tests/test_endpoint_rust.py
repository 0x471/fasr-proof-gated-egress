"""Black-box compatibility checks. Both endpoints use real verification and TCP."""
import asyncio
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ROOT, TINY, TINY_X
from pge.protocol import Wire, frame, read_frame
from pge.service import Endpoint, Policy, load_domain
from test_service import FakeProver, OTHER_X, py_verifier
from prover.server import MODES, Prover, Server


@pytest.fixture(scope="session")
def ezkl():
    try:
        import ezkl as mod
    except ImportError:
        if os.environ.get("PGE_RUST_REFERENCE_CLI"):
            return None
        raise
    assert mod.__version__ == "23.0.5"
    return mod


@pytest.fixture(scope="module")
def rust_bin():
    path = Path(os.environ.get("PGE_RUST_BIN", ROOT / "endpoint/target/release/pge-endpoint"))
    if not path.is_file():
        if os.environ.get("PGE_RUST_BIN"):
            pytest.fail(f"missing configured Rust binary: {path}")
        pytest.skip("build endpoint/ or set PGE_RUST_BIN")
    return path.resolve()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def exercise(rust, binary, bundle, ezkl, work, mode="honest", release="arrival",
                   case="normal", prover=None, deadline=1.0, halt=None, capacity=32):
    work.mkdir()
    for name, source in (("settings.json", bundle.settings), ("vk.key", bundle.vk), ("kzg.srs", bundle.srs)):
        shutil.copy(source, work / name)
    records, connected = [], asyncio.Event()
    selected = prover or FakeProver(bundle.spec)
    if case == "padded":
        selected.raw += b"\x00"
    server = Server(selected, mode=mode, delay=1.3, bits="10", slot=0.03, batch=2)

    async def handle(reader, writer):
        connected.set()
        if case == "disconnect":
            await read_frame(reader, 65536)
            await read_frame(reader, 65536)
            writer.close()
            return
        await server.handle(reader, writer)

    upstream = await asyncio.start_server(handle, "127.0.0.1", 0)
    sport = upstream.sockets[0].getsockname()[1]
    if case == "no_server":
        upstream.close()
        await upstream.wait_closed()
    port, wire = free_port(), Wire.for_spec(bundle.spec)
    proc, listener, link, endpoint = None, None, None, None
    logpath = work / "endpoint.log"
    clients = []
    try:
        if rust:
            args = [str(binary), "--listen", f"127.0.0.1:{port}", "--server", f"127.0.0.1:{sport}",
                    "--bundle", str(work), "--pins", str(ROOT / "device/tiny.sha256"), "--domain", str(TINY / "domain.json"),
                    "--proof-bytes", "3072", "--deadline", str(deadline), "--release", release,
                    "--spin-ms", "5", "--max-pending", str(capacity), "--log", str(logpath)]
            if halt is not None:
                args += ["--halt-after", str(halt)]
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        else:
            verifier = py_verifier(bundle, ezkl)
            if os.environ.get("PGE_RUST_REFERENCE_CLI"):
                from pge.cli import verify_bytes_with_cli
                verifier = lambda data, budget: verify_bytes_with_cli(bundle, data, os.environ["PGE_RUST_REFERENCE_CLI"], budget)
            endpoint = Endpoint(bundle.spec, verifier, domain=load_domain(TINY / "domain.json"),
                        policy=Policy(deadline=deadline, release=release, spin=0.005, proof_bytes=3072,
                                      max_pending=capacity, halt_after=halt), log=records.append)
            listener = await asyncio.start_server(endpoint.serve_client, "127.0.0.1", port)
            link = asyncio.create_task(endpoint.link("127.0.0.1", sport))
        if case == "no_server":
            await asyncio.sleep(0.15)
        else:
            await asyncio.wait_for(connected.wait(), 10)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        clients.append(writer)
        before = time.monotonic()
        if case == "bad_frame":
            writer.write(frame(b"garbage"))
            replies = [await asyncio.wait_for(read_frame(reader, 65536), 5)]
        elif case == "idle":
            replies = [await asyncio.wait_for(read_frame(reader, 65536), 65)]
        else:
            xs = [TINY_X], (OTHER_X if mode == "replay" else [TINY_X])
            if case == "bad_request":
                xs = ([[1e30, 0, 0, 0]], [[-3.25, 7., 1e-6, -0.0]])
            for cid, x in enumerate(xs, 1):
                writer.write(frame(wire.encode_request(cid, x)))
            await writer.drain()
            if case == "half_close":
                writer.write_eof()
            replies = [await asyncio.wait_for(read_frame(reader, 65536), deadline + 5) for _ in xs]
            if release == "fixed" and case != "bad_request" and capacity > 1:
                assert time.monotonic() - before >= deadline
            if case == "half_close":
                assert await asyncio.wait_for(read_frame(reader, 65536), 5) is None
            elif case == "ids":
                writer.write(frame(wire.encode_request(2, [TINY_X])))
                assert await asyncio.wait_for(read_frame(reader, 65536), 5) is None
            else:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(read_frame(reader, 65536), 0.1)
        if mode == "delay":
            await asyncio.sleep(2.7)
        await asyncio.sleep(0.03)
        if rust:
            assert proc.returncode is None
            records = [json.loads(line) for line in logpath.read_text().splitlines()]
        clean = [{k: v for k, v in r.items() if k != "ms"} for r in records]
        return replies, clean
    finally:
        for writer in clients:
            writer.close()
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if link:
            link.cancel()
            await asyncio.gather(link, return_exceptions=True)
        if listener:
            listener.close()
            await listener.wait_closed()
        upstream.close()
        await upstream.wait_closed()
        server.pool.shutdown(wait=True)
        if endpoint:
            endpoint._pool.shutdown(wait=True)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("release", ["arrival", "fixed"])
def test_modes_match_python(rust_bin, tiny_bundle, ezkl, tmp_path, mode, release):
    async def go():
        py = await exercise(False, rust_bin, tiny_bundle, ezkl, tmp_path / "py", mode, release)
        rs = await exercise(True, rust_bin, tiny_bundle, ezkl, tmp_path / "rs", mode, release)
        assert rs == py
        replies = [Wire.for_spec(tiny_bundle.spec).parse_reply(raw) for raw in rs[0]]
        if mode in ("honest", "duplicate", "order_bits", "timing_bits"):
            assert all(r["ok"] for r in replies)
        if mode in ("tamper_output", "drop", "wrong_seq", "garbage", "oversize", "delay"):
            assert not any(r["ok"] for r in replies)
    asyncio.run(go())


@pytest.mark.parametrize("case", ["bad_frame", "bad_request", "half_close", "ids", "padded", "disconnect", "no_server"])
@pytest.mark.parametrize("release", ["arrival", "fixed"])
def test_client_cases_match_python(rust_bin, tiny_bundle, ezkl, tmp_path, case, release):
    async def go():
        py = await exercise(False, rust_bin, tiny_bundle, ezkl, tmp_path / "py", case=case, release=release)
        rs = await exercise(True, rust_bin, tiny_bundle, ezkl, tmp_path / "rs", case=case, release=release)
        assert rs == py
    asyncio.run(go())


def test_idle_clients_match_python(rust_bin, tiny_bundle, ezkl, tmp_path):
    async def go():
        py, rs = await asyncio.gather(
            exercise(False, rust_bin, tiny_bundle, ezkl, tmp_path / "py", case="idle"),
            exercise(True, rust_bin, tiny_bundle, ezkl, tmp_path / "rs", case="idle"))
        assert rs == py == ([None], [{"result": "client_idle"}])
    asyncio.run(go())


@pytest.mark.parametrize("release", ["arrival", "fixed"])
@pytest.mark.parametrize("halt,capacity", [(1, 32), (None, 1)])
def test_halt_and_busy_match_python(rust_bin, tiny_bundle, ezkl, tmp_path, release, halt, capacity):
    async def go():
        kw = dict(mode="drop", release=release, halt=halt, capacity=capacity)
        py = await exercise(False, rust_bin, tiny_bundle, ezkl, tmp_path / "py", **kw)
        rs = await exercise(True, rust_bin, tiny_bundle, ezkl, tmp_path / "rs", **kw)
        assert rs == py
    asyncio.run(go())


def test_real_prover_matches_python(rust_bin, tiny_bundle, ezkl, srs_path, tmp_path):
    if ezkl is None:
        pytest.skip("real proving requires the EZKL Python wheel; CLI reference only")
    prover = Prover(srs_path)
    async def go():
        py = await exercise(False, rust_bin, tiny_bundle, ezkl, tmp_path / "py", prover=prover, deadline=30)
        rs = await exercise(True, rust_bin, tiny_bundle, ezkl, tmp_path / "rs", prover=prover, deadline=30)
        assert rs == py
        assert all(Wire.for_spec(tiny_bundle.spec).parse_reply(raw)["ok"] for raw in rs[0])
    asyncio.run(go())


def child_command(binary, bundle, proof_bytes=3072):
    return [str(binary), "--verify-child", "2", str(proof_bytes), bundle.pins.settings, bundle.pins.vk, bundle.pins.srs]


def job(bundle, proof):
    chunks = [bundle.settings.read_bytes(), bundle.vk.read_bytes(), bundle.srs.read_bytes(), proof]
    return b"".join(struct.pack(">I", len(c)) + c for c in chunks)


def test_real_child_verifies_without_runtime_files(rust_bin, tiny_bundle, tiny_proof, tmp_path):
    unavailable_tmp = tmp_path / "not-a-directory"
    unavailable_tmp.write_bytes(b"not writable scratch")
    env = {"PATH": os.defpath, "RAYON_NUM_THREADS": "1", "TMPDIR": str(unavailable_tmp), "LC_ALL": "C"}
    for tampered in (False, True):
        proof = json.loads(json.dumps(tiny_proof))
        if tampered:
            proof["instances"][0][-3] = "00" * 32
        result = subprocess.run(child_command(rust_bin, tiny_bundle), input=job(tiny_bundle, json.dumps(proof).encode()),
                                capture_output=True, env=env, timeout=5)
        assert (result.returncode, result.stdout, result.stderr) == (
            (1, b"reject\n", b"") if tampered else (0, b"accept\n", b""))


@pytest.mark.parametrize("kind", ["truncated", "tail", "oversize", "changed_bundle", "bad_proof"])
def test_worker_input_failures_are_fail_closed(rust_bin, tiny_bundle, tiny_proof, kind):
    raw = job(tiny_bundle, json.dumps(tiny_proof).encode())
    expected = (2, b"")
    if kind == "truncated":
        raw = raw[:-1]
    elif kind == "tail":
        raw += b"x"
    elif kind == "oversize":
        raw = struct.pack(">I", (1 << 20) + 1)
    elif kind == "changed_bundle":
        raw = raw[:4] + bytes([raw[4] ^ 1]) + raw[5:]
    else:
        raw = job(tiny_bundle, b"garbage")
        expected = (1, b"reject\n")
    result = subprocess.run(child_command(rust_bin, tiny_bundle), input=raw, capture_output=True, timeout=5)
    assert (result.returncode, result.stdout) == expected


@pytest.mark.parametrize("index", [0, 1, 2])
def test_bad_pinned_model_is_error(rust_bin, tiny_bundle, tiny_proof, index):
    chunks = [tiny_bundle.settings.read_bytes(), tiny_bundle.vk.read_bytes(), tiny_bundle.srs.read_bytes()]
    chunks[index] = b"\x00"
    pins = [hashlib.sha256(c).hexdigest() for c in chunks]
    raw = b"".join(struct.pack(">I", len(c)) + c for c in chunks + [json.dumps(tiny_proof).encode()])
    command = [str(rust_bin), "--verify-child", "2", "3072", *pins]
    result = subprocess.run(command, input=raw, capture_output=True, timeout=5)
    assert result.returncode == 2
    assert result.stdout in (b"", b"error\n")
    assert not result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Linux prlimit and address-space policy")
def test_live_child_has_all_required_limits(rust_bin, tiny_bundle):
    import resource
    proc = subprocess.Popen(child_command(rust_bin, tiny_bundle), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    expected = {resource.RLIMIT_CORE: 0, resource.RLIMIT_CPU: 2, resource.RLIMIT_FSIZE: 0,
                resource.RLIMIT_NOFILE: 64, resource.RLIMIT_AS: 256 << 20}
    try:
        end = time.monotonic() + 3
        while time.monotonic() < end:
            actual = {r: resource.prlimit(proc.pid, r) for r in expected}
            if actual == {r: (v, v) for r, v in expected.items()}:
                break
            time.sleep(0.01)
        assert actual == {r: (v, v) for r, v in expected.items()}
    finally:
        proc.kill()
        proc.communicate()


def test_non_utf8_arguments_are_errors_not_panics(rust_bin):
    result = subprocess.run([bytes(rust_bin), b"--bundle", b"\xff"], capture_output=True, timeout=5)
    assert result.returncode == 2
    assert b"panicked" not in result.stderr
