"""The v0 gate against honest and misbehaving servers, over real sockets."""
import asyncio
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

import pytest

from conftest import ROOT, TINY, TINY_X
from pge.binding import split_instances
from pge.protocol import frame, loads, read_frame
from pge.service import REASONS, Gate, Policy, file_log, load_domain
from pge.verdict import Verdict

sys.path.insert(0, str(ROOT / "prover"))
from server import Server  # noqa: E402

Y = [[0.9666305631399155, 0.0, 0.0]]
OTHER_X = [[0.6, -0.2, 0.9, 0.1]]


class FakeProver:
    """Answers every request with the pinned tiny proof, which is only valid for TINY_X."""

    def __init__(self, spec):
        proof = json.loads((TINY / "proof.json").read_text())
        self.outputs = split_instances(spec, proof["instances"])[1]
        self.raw = bytes(proof["proof"])
        self.calls = []

    def prove(self, x):
        self.calls.append(x)
        return self.outputs, self.raw


def py_verifier(bundle, ezkl):
    def verify(data, timeout):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "proof.json"
            p.write_bytes(data)
            return bundle.verify(ezkl, p)
    return verify


class Client:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer

    def send(self, cid, x):
        self.w.write(frame(json.dumps({"id": cid, "x": x}).encode()))

    def send_raw(self, payload: bytes):
        self.w.write(payload)

    async def recv(self, timeout=10.0):
        data = await asyncio.wait_for(read_frame(self.r, 1 << 16), timeout)
        return None if data is None else loads(data)

    async def nothing_within(self, seconds):
        try:
            data = await asyncio.wait_for(read_frame(self.r, 1 << 16), seconds)
        except asyncio.TimeoutError:
            return True
        return data is None


class Stack:
    def __init__(self, gate, server, tasks, servers):
        self.gate, self.server, self.tasks, self.servers = gate, server, tasks, servers

    async def client(self):
        port = self.servers[1].sockets[0].getsockname()[1]
        return Client(*await asyncio.open_connection("127.0.0.1", port))

    def results(self):
        return [r["result"] for r in self.records]


def run_stack(bundle, verifier, test, *, server=None, policy=Policy(deadline=5), domain=None, link=True):
    async def go():
        records = []
        gate = Gate(spec=bundle.spec, verifier=verifier, domain=domain, policy=policy, log=records.append)
        srv = server or Server(FakeProver(bundle.spec))
        s1 = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
        s2 = await asyncio.start_server(gate.serve_client, "127.0.0.1", 0)
        tasks = []
        if link:
            tasks.append(asyncio.create_task(gate.link("127.0.0.1", s1.sockets[0].getsockname()[1])))
            for _ in range(200):
                if gate._server is not None:
                    break
                await asyncio.sleep(0.01)
            assert gate._server is not None
        st = Stack(gate, srv, tasks, (s1, s2))
        st.records = records
        try:
            return await test(st)
        finally:
            for t in tasks:
                t.cancel()
            for s in (s1, s2):
                s.close()
    return asyncio.run(go())


@pytest.fixture()
def verifier(ezkl, tiny_bundle):
    return py_verifier(tiny_bundle, ezkl)


def server(bundle, mode, **kw):
    return Server(FakeProver(bundle.spec), mode=mode, **kw)


def test_honest_answer_is_released(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": True, "y": Y}
        assert st.server.requests == [{"seq": 1, "x": [TINY_X]}]
        assert st.results() == ["accept"]
    run_stack(tiny_bundle, verifier, t)


def test_proof_for_another_request_is_blocked(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, OTHER_X)
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert st.results() == ["reject"]
    run_stack(tiny_bundle, verifier, t)


@pytest.mark.parametrize("mode", ["tamper_output", "garbage", "oversize"])
def test_bad_answers_are_blocked(tiny_bundle, verifier, mode):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert await c.nothing_within(0.5)
        want = "reject" if mode == "tamper_output" else "disconnect"
        assert want in st.results()
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, mode))


def test_server_violation_fails_pending_and_reconnects(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert st.results()[:2] == ["server_violation", "disconnect"]
        for _ in range(300):
            if st.gate._server is not None:
                break
            await asyncio.sleep(0.01)
        assert st.gate._server is not None
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "garbage"))


def test_silent_server_hits_the_deadline(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        start = time.monotonic()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert 0.9 < time.monotonic() - start < 3
        assert st.results() == ["deadline"]
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "drop"), policy=Policy(deadline=1))


def test_late_answer_is_dropped_and_answered_once(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert await c.nothing_within(1.5)
        assert st.gate.counts["stale_answer"] == 1
        assert st.results() == ["deadline", "stale_answer"]
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "delay", delay=1.2), policy=Policy(deadline=0.5))


def test_duplicate_answer_gives_one_reply(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is True
        assert await c.nothing_within(0.5)
        assert st.gate.counts["duplicate_answer"] + st.gate.counts["stale_answer"] == 1
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "duplicate"))


def test_answer_for_unissued_seq_is_dropped(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert st.gate.counts["stale_answer"] == 1 and "deadline" in st.results()
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "wrong_seq"), policy=Policy(deadline=1))


def test_replayed_answer_only_passes_for_the_same_request(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is True
        c.send(2, [TINY_X])
        assert await c.recv() == {"id": 2, "ok": True, "y": Y}
        c.send(3, OTHER_X)
        assert (await c.recv())["ok"] is False
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "replay"))


def test_proof_from_another_circuit_is_blocked(tiny_bundle, verifier):
    class OtherKey(FakeProver):
        def prove(self, x):
            other = json.loads((ROOT / "tests" / "fixtures" / "multi" / "proof.json").read_text())
            return self.outputs, bytes(other["proof"])

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert st.results() == ["reject"]
    run_stack(tiny_bundle, verifier, t, server=Server(OtherKey(tiny_bundle.spec)))


def test_padded_proof_is_blocked_when_the_length_is_pinned(tiny_bundle, verifier):
    class Padded(FakeProver):
        def prove(self, x):
            return self.outputs, self.raw + b"\x00"

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert st.results() == ["reject"]
    run_stack(tiny_bundle, verifier, t, server=Server(Padded(tiny_bundle.spec)), policy=Policy(deadline=5, proof_bytes=3072))


def test_bad_requests_never_reach_the_server(tiny_bundle, verifier):
    m = json.loads((TINY / "settings.json").read_text())["run_args"]
    assert (m["decomp_base"], m["decomp_legs"]) == (16384, 2)
    domain = load_domain(TINY / "domain.json")

    async def t(st):
        c = await st.client()
        for cid, x in enumerate(([[0.5, -0.2, 0.9]], [TINY_X, TINY_X], [[1e30, 0, 0, 0]], [[-3.25, 7.0, 1e-6, -0.0]])):
            c.send(cid, x)
            assert await c.recv() == {"id": cid, "ok": False, "error": "bad_request"}
        assert st.server.requests == [] and st.results() == ["bad_request"] * 4
    run_stack(tiny_bundle, verifier, t, domain=domain)


@pytest.mark.parametrize("payload", [
    frame(b'{"id": 1, "x": [[1, 2, 3, 4]], "id": 2}'),
    frame(b'{"id": 1, "x": [[1, 2, 3, 4]], "seq": 5}'),
    frame(b"nope"),
    struct.pack(">I", 1 << 20),
    frame(b'{"id": 1, "x": [[NaN, 0, 0, 0]]}'),
])
def test_client_violation_closes_the_connection(tiny_bundle, verifier, payload):
    async def t(st):
        c = await st.client()
        c.send_raw(payload)
        assert await c.recv() is None
        assert st.results() == ["client_violation"] and st.server.requests == []
    run_stack(tiny_bundle, verifier, t)


def test_ids_must_increase(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(5, [TINY_X])
        assert (await c.recv())["id"] == 5
        c.send(5, [TINY_X])
        assert await c.recv() is None
        assert "client_violation" in st.results() and len(st.server.requests) == 1
    run_stack(tiny_bundle, verifier, t)


def test_halt_after_failures(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        for cid in (1, 2):
            c.send(cid, [TINY_X])
            assert (await c.recv())["ok"] is False
        assert st.gate.halted
        c.send(3, [TINY_X])
        assert await c.recv(timeout=0.3) == {"id": 3, "ok": False, "error": "unavailable"}
        assert len(st.server.requests) == 2
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "drop"),
              policy=Policy(deadline=0.3, halt_after=2))


def test_too_many_pending_is_unavailable(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        c.send(2, [TINY_X])
        assert await c.recv(timeout=0.5) == {"id": 2, "ok": False, "error": "unavailable"}
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert st.results() == ["busy", "deadline"] and not st.gate.halted
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "drop"),
              policy=Policy(deadline=1, max_pending=1, halt_after=2))


def test_no_server_is_unavailable(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert st.results() == ["no_server"]
    run_stack(tiny_bundle, verifier, t, link=False)


def test_each_request_answered_exactly_once(tiny_bundle, verifier):
    async def t(st):
        a, b = await st.client(), await st.client()
        for cid in range(5):
            a.send(cid, [TINY_X])
            b.send(cid, OTHER_X)
        got_a = [await a.recv() for _ in range(5)]
        got_b = [await b.recv() for _ in range(5)]
        assert sorted(m["id"] for m in got_a) == list(range(5)) and all(m["ok"] for m in got_a)
        assert sorted(m["id"] for m in got_b) == list(range(5)) and not any(m["ok"] for m in got_b)
        assert await a.nothing_within(0.3) and await b.nothing_within(0.1)
        assert [r["seq"] for r in st.server.requests] == list(range(1, 11))
    run_stack(tiny_bundle, verifier, t)


def test_verifier_crash_is_unavailable(tiny_bundle):
    def boom(data, timeout):
        raise RuntimeError("native crash stand-in")

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert st.results() == ["error"]
    run_stack(tiny_bundle, boom, t)


def test_check_finishing_after_the_deadline_is_dropped(tiny_bundle):
    def slow(data, timeout):
        time.sleep(1.0)
        return Verdict.ACCEPT, ""

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert await c.nothing_within(1.5)
        assert st.results() == ["deadline"] and st.gate.counts["late_check"] == 1
    run_stack(tiny_bundle, slow, t, policy=Policy(deadline=0.3))


def test_v0_leaks_bits_through_timing(tiny_bundle, verifier):
    # The baseline releases as soon as a check passes, so the server's delays reach the client.
    bits = "1011001"

    async def t(st):
        c = await st.client()
        seen = ""
        for cid in range(len(bits)):
            start = time.monotonic()
            c.send(cid, [TINY_X])
            assert (await c.recv())["ok"] is True
            seen += "1" if time.monotonic() - start > 0.25 else "0"
        assert seen == bits
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "timing_bits", bits=bits, slot=0.5))


def test_real_prover_end_to_end(ezkl, tiny_bundle, verifier, srs_path):
    from server import Prover

    prover = Prover(srs_path)

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv(timeout=60) == {"id": 1, "ok": True, "y": Y}
        c.send(2, [[0.1, 0.2, 0.3, 0.4]])
        m = await c.recv(timeout=60)
        assert m["ok"] is True and m["id"] == 2
    run_stack(tiny_bundle, verifier, t, server=Server(prover), policy=Policy(deadline=60))


def test_cli_verifier_in_the_gate(real_cli_path, tiny_bundle):
    from pge.cli import verify_bytes_with_cli

    def verifier(data, timeout):
        return verify_bytes_with_cli(tiny_bundle, data, real_cli_path, timeout)

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv(timeout=30) == {"id": 1, "ok": True, "y": Y}
        c.send(2, OTHER_X)
        assert (await c.recv(timeout=30))["ok"] is False
        assert st.results() == ["accept", "reject"]
    run_stack(tiny_bundle, verifier, t, policy=Policy(deadline=30))


def test_service_main_with_the_real_cli(real_cli_path, tiny_bundle, srs_path, tmp_path):
    import shutil
    import socket
    import subprocess

    for name in ("settings.json", "vk.key"):
        shutil.copy(TINY / name, tmp_path / name)
    shutil.copy(srs_path, tmp_path / "kzg.srs")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    async def go():
        srv = Server(FakeProver(tiny_bundle.spec))
        s1 = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
        sport = s1.sockets[0].getsockname()[1]
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); from pge.service import main; main()",
            "--listen", f"127.0.0.1:{port}",
            "--server", f"127.0.0.1:{sport}", "--bundle", str(tmp_path), "--pins", str(ROOT / "device" / "tiny.sha256"),
            "--cli", str(real_cli_path), "--domain", str(TINY / "domain.json"), "--deadline", "30", "--proof-bytes", "3072",
            cwd=str(ROOT), stderr=asyncio.subprocess.PIPE)
        try:
            for _ in range(200):
                try:
                    c = Client(*await asyncio.open_connection("127.0.0.1", port))
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            else:
                pytest.fail("service did not start")
            # The first requests may arrive before the service has connected to the server.
            for cid in range(50):
                c.send(cid, [TINY_X])
                m = await c.recv(timeout=30)
                if srv.requests:
                    break
                assert m == {"id": cid, "ok": False, "error": "unavailable"}
                await asyncio.sleep(0.1)
            assert m == {"id": cid, "ok": True, "y": Y}
            c.send(cid + 1, [[-3.25, 7.0, 1e-6, -0.0]])
            assert await c.recv() == {"id": cid + 1, "ok": False, "error": "bad_request"}
        finally:
            proc.kill()
            await proc.wait()
            s1.close()
    asyncio.run(go())


@pytest.fixture()
def real_cli_path():
    from conftest import REQUIRE
    path = os.environ.get("PGE_EZKL_CLI")
    if not path or not Path(path).is_file():
        if REQUIRE:
            pytest.fail("PGE_EZKL_CLI not set")
        pytest.skip("set PGE_EZKL_CLI to EZKL's 23.0.5 CLI binary")
    return Path(path)


def test_verdict_values_match_log_results():
    assert {v.value for v in Verdict} == {"accept", "reject", "error"}


# Regression tests from the 16 Oct review.

def test_broken_log_still_answers(tiny_bundle, verifier):
    def bad_log(rec):
        raise OSError(28, "No space left on device")

    async def go():
        gate = Gate(spec=tiny_bundle.spec, verifier=verifier, log=bad_log, policy=Policy(deadline=5))
        srv = Server(FakeProver(tiny_bundle.spec))
        s1 = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
        s2 = await asyncio.start_server(gate.serve_client, "127.0.0.1", 0)
        link = asyncio.create_task(gate.link("127.0.0.1", s1.sockets[0].getsockname()[1]))
        while gate._server is None:
            await asyncio.sleep(0.01)
        c = Client(*await asyncio.open_connection("127.0.0.1", s2.sockets[0].getsockname()[1]))
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": True, "y": Y}
        c.send(2, OTHER_X)
        assert await c.recv() == {"id": 2, "ok": False, "error": "unavailable"}
        assert gate.counts["log_error"] >= 2 and not gate.pending
        link.cancel()
        s1.close()
        s2.close()
    asyncio.run(go())


def test_overdue_result_is_not_released_when_the_timer_is_late(tiny_bundle):
    def slow_accept(data, timeout):
        time.sleep(0.5)
        return Verdict.ACCEPT, ""

    async def t(st):
        st.gate._expire = lambda seq: None  # stands in for a deadline timer that runs late
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert st.results() == ["deadline"]
    run_stack(tiny_bundle, slow_accept, t, policy=Policy(deadline=0.2))


def test_half_closed_client_still_gets_its_answers(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        c.send(2, OTHER_X)
        c.w.write_eof()
        got = [await c.recv(), await c.recv()]
        assert got == [{"id": 1, "ok": True, "y": Y}, {"id": 2, "ok": False, "error": "unavailable"}]
        assert await c.recv() is None
    run_stack(tiny_bundle, verifier, t)


def test_unissued_seq_is_not_logged(tiny_bundle, verifier):
    async def go():
        records = []
        gate = Gate(spec=tiny_bundle.spec, verifier=verifier, log=records.append)
        gate.on_answer(1234567890123456, [], b"x")
        assert records == [{"result": "stale_answer"}] and gate.counts["stale_answer"] == 1
    asyncio.run(go())


def test_server_violation_detail_is_fixed_text(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert [r for r in st.records if r["result"] == "server_violation"] == [{"result": "server_violation"}]
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "oversize"))


@pytest.mark.parametrize("kw", [
    {"deadline": float("inf")}, {"deadline": float("nan")}, {"deadline": 0}, {"deadline": -1}, {"deadline": True},
    {"max_pending": 0}, {"max_pending": 1.5}, {"halt_after": 0}, {"proof_bytes": 0}, {"proof_bytes": 1 << 21},
    {"max_clients": 0}, {"idle": 0},
])
def test_bad_policy_is_refused(kw):
    with pytest.raises(ValueError):
        Policy(**kw)


def test_expired_request_waiting_for_the_lock_is_never_verified(tiny_bundle):
    calls = []

    def slow(data, timeout):
        calls.append(timeout)
        time.sleep(0.6)
        return Verdict.REJECT, "first"

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        c.send(2, [TINY_X])
        got = sorted([(await c.recv())["id"], (await c.recv())["id"]])
        assert got == [1, 2]
        await asyncio.sleep(0.3)
        assert len(calls) == 1
    run_stack(tiny_bundle, slow, t, policy=Policy(deadline=0.4))


def test_verifier_budget_is_the_remaining_time(tiny_bundle):
    budgets = []

    def slow(data, timeout):
        budgets.append(timeout)
        time.sleep(0.3)
        return Verdict.ACCEPT, ""

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        c.send(2, [TINY_X])
        assert [(await c.recv())["ok"], (await c.recv())["ok"]] == [True, True]
        assert budgets[0] <= 2.0 and budgets[1] < budgets[0] - 0.2
    run_stack(tiny_bundle, slow, t, policy=Policy(deadline=2))


def test_failures_are_cumulative(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        for cid, x, ok in ((1, OTHER_X, False), (2, [TINY_X], True), (3, OTHER_X, False)):
            c.send(cid, x)
            assert (await c.recv())["ok"] is ok
        assert st.gate.halted
    run_stack(tiny_bundle, verifier, t, policy=Policy(deadline=5, halt_after=2))


def test_halt_answers_pending_requests_at_once(tiny_bundle):
    state = {"n": 0}

    def verify(data, timeout):
        state["n"] += 1
        time.sleep(0.2 if state["n"] == 1 else 1.5)
        return (Verdict.REJECT, "first") if state["n"] == 1 else (Verdict.ACCEPT, "")

    async def t(st):
        c = await st.client()
        c.send(1, OTHER_X)
        c.send(2, [TINY_X])
        first = await c.recv()
        start = time.monotonic()
        second = await c.recv()
        assert first == {"id": 1, "ok": False, "error": "unavailable"}
        assert second == {"id": 2, "ok": False, "error": "unavailable"} and time.monotonic() - start < 0.5
        assert st.gate.halted and "accept" not in st.results() and "halted" in st.results()
    run_stack(tiny_bundle, verify, t, policy=Policy(deadline=5, halt_after=1))


def test_idle_client_is_closed(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        assert await c.recv(timeout=5) is None
        c2 = await st.client()
        c2.send_raw(b"\x00\x00")
        assert await c2.recv(timeout=5) is None
        assert st.results() == ["client_idle", "client_idle"]
    run_stack(tiny_bundle, verifier, t, policy=Policy(deadline=5, idle=0.3))


def test_too_many_clients_are_refused(tiny_bundle, verifier):
    async def t(st):
        a = await st.client()
        a.send(1, [TINY_X])
        assert (await a.recv())["ok"] is True
        b = await st.client()
        assert await b.recv(timeout=5) is None
        assert "client_refused" in st.results()
    run_stack(tiny_bundle, verifier, t, policy=Policy(deadline=5, max_clients=1))


def test_stalled_server_frame_is_a_violation(tiny_bundle, verifier):
    class Stall(Server):
        async def _send(self, writer, seq, outputs, proof):
            writer.write(b"\x00\x00\x10\x00{")
            await writer.drain()

    async def t(st):
        c = await st.client()
        start = time.monotonic()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert time.monotonic() - start < 3
        for _ in range(100):
            if "server_violation" in st.results():
                break
            await asyncio.sleep(0.02)
        assert "server_violation" in st.results()
    run_stack(tiny_bundle, verifier, t, server=Stall(FakeProver(tiny_bundle.spec)), policy=Policy(deadline=1))


def test_bad_verifier_result_is_unavailable(tiny_bundle):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert await c.recv() == {"id": 1, "ok": False, "error": "unavailable"}
        assert st.results() == ["error"]
    run_stack(tiny_bundle, lambda data, timeout: ("accept", ""), t)


def test_observer_recovers_the_timing_bits(tiny_bundle, verifier):
    sys.path.insert(0, str(ROOT / "observer"))
    from client import decode_bits, trace

    bits = "1101001"

    async def t(st):
        port = st.servers[1].sockets[0].getsockname()[1]
        rows = await trace("127.0.0.1", port, [[TINY_X]] * len(bits))
        assert [r["id"] for r in rows] == list(range(len(bits))) and all(r["answer"]["ok"] for r in rows)
        assert decode_bits(rows) == bits
    run_stack(tiny_bundle, verifier, t, server=server(tiny_bundle, "timing_bits", bits=bits, slot=0.5))


# Logs carry fixed words only (v1).

def test_log_records_hold_no_server_text(tiny_bundle, verifier):
    class Counted(FakeProver):
        def prove(self, x):
            return [self.outputs[0] * 7], self.raw  # the count of output values is server-chosen

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert st.records[0]["result"] == "reject" and st.records[0]["reason"] == "binding"
        for rec in st.records:
            assert set(rec) <= {"result", "reason", "seq", "x_sha256", "ms", "failures"}
            assert "reason" not in rec or rec["reason"] in REASONS
    run_stack(tiny_bundle, verifier, t, server=Server(Counted(tiny_bundle.spec)))


def test_proof_length_reason_hides_the_length(tiny_bundle, verifier):
    class Padded(FakeProver):
        def prove(self, x):
            return self.outputs, self.raw + b"\x00" * 123

    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        assert (await c.recv())["ok"] is False
        assert st.records[0]["reason"] == "proof_length" and "123" not in json.dumps(st.records)
    run_stack(tiny_bundle, verifier, t, server=Server(Padded(tiny_bundle.spec)), policy=Policy(deadline=5, proof_bytes=3072))


def test_unknown_reason_is_logged_as_other(tiny_bundle, verifier):
    records = []
    gate = Gate(spec=tiny_bundle.spec, verifier=verifier, log=records.append)
    gate._record("error", None, "server said 0xdeadbeef")
    assert records == [{"result": "error", "reason": "other"}]


def test_file_log_appends_lines(tmp_path):
    path = tmp_path / "gate.log"
    log = file_log(path)
    log({"result": "accept", "seq": 1})
    log({"result": "reject", "reason": "proof"})
    assert [json.loads(l) for l in path.read_text().splitlines()] == [
        {"result": "accept", "seq": 1}, {"result": "reject", "reason": "proof"}]


# Arrival-order release (v1).

class Reversed(Server):
    """Collects n requests, then answers them in reverse order."""

    def __init__(self, prover, n=2, skip=()):
        super().__init__(prover)
        self.n, self.skip = n, set(skip)

    async def handle(self, reader, writer):
        got = []
        try:
            while len(got) < self.n:
                data = await read_frame(reader, 4096)
                if data is None:
                    return
                got.append(loads(data))
            for msg in reversed(got):
                if msg["seq"] in self.skip:
                    continue
                outputs, proof = self.prover.prove(msg["x"])
                await self._send(writer, msg["seq"], outputs, proof)
            await asyncio.sleep(30)
        finally:
            writer.close()


@pytest.mark.parametrize("release, first", [("arrival", 1), ("immediate", 2)])
def test_answers_leave_in_arrival_order(tiny_bundle, verifier, release, first):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        c.send(2, [TINY_X])
        got = [await c.recv(), await c.recv()]
        assert [m["id"] for m in got][0] == first and all(m["ok"] for m in got)
    run_stack(tiny_bundle, verifier, t, server=Reversed(FakeProver(tiny_bundle.spec)),
              policy=Policy(deadline=5, release=release))


def test_held_answer_waits_only_for_the_earlier_deadline(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        start = time.monotonic()
        c.send(1, [TINY_X])
        c.send(2, [TINY_X])
        first, second = await c.recv(), await c.recv()
        assert first == {"id": 1, "ok": False, "error": "unavailable"} and second == {"id": 2, "ok": True, "y": Y}
        assert 0.9 < time.monotonic() - start < 3
        assert st.results() == ["accept", "deadline"]
    run_stack(tiny_bundle, verifier, t, server=Reversed(FakeProver(tiny_bundle.spec), skip={1}),
              policy=Policy(deadline=1))


def test_failures_are_released_in_order_too(tiny_bundle, verifier):
    async def t(st):
        c = await st.client()
        c.send(1, [TINY_X])
        c.send(2, OTHER_X)
        got = [await c.recv(), await c.recv()]
        assert got == [{"id": 1, "ok": True, "y": Y}, {"id": 2, "ok": False, "error": "unavailable"}]
    run_stack(tiny_bundle, verifier, t, server=Reversed(FakeProver(tiny_bundle.spec)))


def test_bad_release_policy_is_refused():
    with pytest.raises(ValueError):
        Policy(release="random")
