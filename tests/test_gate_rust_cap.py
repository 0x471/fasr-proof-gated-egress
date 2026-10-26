"""The Rust gate's failure cap against the reference model (observer/cap_model.py), over TCP."""
import asyncio
import json
import random
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT, TINY, TINY_X
from pge.protocol import Wire, frame, read_frame
from test_gate_rust import ezkl, free_port, rust_bin  # noqa: F401
from test_service import FakeProver
from prover.server import Server

sys.path.insert(0, str(ROOT / "observer"))
from cap_model import START, step  # noqa: E402


class Patterned(FakeProver):
    """Answers with the pinned proof, but breaks the output on the calls marked in `fail`."""

    def __init__(self, spec, fail):
        super().__init__(spec)
        self.fail = list(fail)

    def prove(self, x):
        outputs, raw = super().prove(x)
        if self.fail[len(self.calls) - 1]:
            first = outputs[0][0]
            outputs = [[("1" if first[0] == "0" else "0") + first[1:]] + outputs[0][1:]] + outputs[1:]
        return outputs, raw


def expected(fail, window, failures, cooldown):
    state, ok, outcomes = START, [], []
    for failed in fail:
        state, outcome = step(state, window, failures, cooldown, failed)
        ok.append(outcome == "pass" and not failed)
        outcomes.append(outcome)
    return ok, outcomes


async def run_gate(binary, bundle, work, fail, cap):
    work.mkdir()
    for name, source in (("settings.json", bundle.settings), ("vk.key", bundle.vk), ("kzg.srs", bundle.srs)):
        shutil.copy(source, work / name)
    server = Server(Patterned(bundle.spec, fail))
    upstream = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port, logpath = free_port(), work / "gate.log"
    args = [str(binary), "--listen", f"127.0.0.1:{port}", "--server", f"127.0.0.1:{upstream.sockets[0].getsockname()[1]}",
            "--bundle", str(work), "--pins", str(ROOT / "device/tiny.sha256"), "--domain", str(TINY / "domain.json"),
            "--proof-bytes", "3072", "--deadline", "5", "--log", str(logpath), *cap]
    proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
    wire, ok = Wire.for_spec(bundle.spec), []
    try:
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        for cid in range(1, len(fail) + 1):
            writer.write(frame(wire.encode_request(cid, [TINY_X])))
            await writer.drain()
            reply = wire.parse_reply(await asyncio.wait_for(read_frame(reader, 65536), 10))
            assert reply["id"] == cid
            ok.append(reply["ok"])
        writer.close()
        assert proc.returncode is None
        records = [json.loads(line) for line in logpath.read_text().splitlines()]
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        upstream.close()
        await upstream.wait_closed()
        server.pool.shutdown(wait=True)
    return ok, records


@pytest.mark.parametrize("window,failures,cooldown,seed", [(4, 1, 3, 1), (6, 2, 2, 2), (3, 0, 4, 3)])
def test_cap_matches_reference_model(rust_bin, tiny_bundle, tmp_path, window, failures, cooldown, seed):  # noqa: F811
    rng = random.Random(seed)
    fail = [rng.random() < 0.4 for _ in range(30)]
    cap = ["--cap-window", str(window), "--cap-failures", str(failures), "--cap-cooldown", str(cooldown)]
    ok, records = asyncio.run(run_gate(rust_bin, tiny_bundle, tmp_path / "rs", fail, cap))
    want, outcomes = expected(fail, window, failures, cooldown)
    assert ok == want
    results = [r["result"] for r in records]
    assert results.count("capped") == outcomes.count("capped") > 0
    assert results.count("cap_tripped") == outcomes.count("trip") > 0


def test_without_cap_every_failure_shows(rust_bin, tiny_bundle, tmp_path):  # noqa: F811
    fail = [True, False] * 6
    ok, records = asyncio.run(run_gate(rust_bin, tiny_bundle, tmp_path / "rs", fail, []))
    assert ok == [not f for f in fail]
    assert not any(r["result"] in ("capped", "cap_tripped") for r in records)


@pytest.mark.parametrize("flags", [["--cap-window", "4", "--cap-cooldown", "0"],
                                   ["--cap-window", "4", "--cap-failures", "4"],
                                   ["--cap-window", "0"],
                                   ["--cap-failures", "1"],
                                   ["--cap-window", "4", "--cap-cooldown", "-1"]])
def test_invalid_cap_flags_are_refused(rust_bin, tmp_path, flags):  # noqa: F811
    args = [str(rust_bin), "--listen", "127.0.0.1:1", "--server", "127.0.0.1:2", "--bundle", str(tmp_path),
            "--pins", str(ROOT / "device/tiny.sha256"), "--proof-bytes", "3072", *flags]
    done = subprocess.run(args, capture_output=True, timeout=10)
    assert done.returncode == 2 and b"cap" in done.stderr
