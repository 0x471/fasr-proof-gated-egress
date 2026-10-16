import json
import sys
import time

import pytest

from conftest import TINY
from pge.verdict import Verdict
from pge.worker import verify_in_worker


def test_real_worker(ezkl, tiny_bundle, tmp_path):
    assert verify_in_worker(tiny_bundle, TINY / "proof.json", 60) == (Verdict.ACCEPT, "")
    bad = tmp_path / "bad.json"
    bad.write_text("bad\n")
    assert verify_in_worker(tiny_bundle, bad, 60)[0] == Verdict.REJECT


def test_sigterm_is_not_enough_to_survive(tiny_bundle):
    hang = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    start = time.monotonic()
    v, detail = verify_in_worker(tiny_bundle, TINY / "proof.json", 1, cmd=[sys.executable, "-c", hang])
    assert v == Verdict.ERROR and "timed out" in detail
    assert time.monotonic() - start < 10


@pytest.mark.parametrize("code", [
    "import os, signal; print('[\"accept\", \"\"]', flush=True); os.kill(os.getpid(), signal.SIGKILL)",
    "import sys; print('[\"accept\", \"\"]'); sys.exit(3)",
    "print('accept')",
    "print('[\"maybe\", \"\"]')",
    "pass",
])
def test_bad_worker_is_error(tiny_bundle, code):
    assert verify_in_worker(tiny_bundle, TINY / "proof.json", 30, cmd=[sys.executable, "-c", code])[0] == Verdict.ERROR


def test_unstartable_worker_is_error(tiny_bundle, tmp_path):
    assert verify_in_worker(tiny_bundle, TINY / "proof.json", 30, cmd=[str(tmp_path / "nope")])[0] == Verdict.ERROR


def test_changed_bundle_is_error_before_starting(tiny_bundle):
    tiny_bundle.vk.chmod(0o600)
    tiny_bundle.vk.write_bytes(b"changed")
    v, detail = verify_in_worker(tiny_bundle, TINY / "proof.json", 30, cmd=[sys.executable, "-c", "1/0"])
    assert v == Verdict.ERROR and detail.startswith("bundle check failed")


def test_hostile_import_path_is_ignored(ezkl, tiny_bundle, tiny_proof, tmp_path, monkeypatch):
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "ezkl.py").write_text("__version__ = '23.0.5'\ndef verify(*a, **k):\n    return True\n")
    monkeypatch.setenv("PYTHONPATH", str(hostile))
    bad = json.loads(json.dumps(tiny_proof))
    bad["instances"][0][-3] = bad["instances"][0][1]
    p = tmp_path / "tampered.json"
    p.write_text(json.dumps(bad))
    assert verify_in_worker(tiny_bundle, p, 60)[0] == Verdict.REJECT


@pytest.mark.parametrize("out", [
    '{"accept": "not a tuple", "detail": 42}',
    '["accept", {"unexpected": 42}]',
    '["accept", 1]',
    '["accept"]',
    '["accept", "", ""]',
    '["reject", ""]\n["accept", ""]',
    '["accept", ""]\n["accept", ""]',
    '',
])
def test_answer_must_be_one_two_string_line(tiny_bundle, out):
    code = f"import sys; sys.stdout.write({out!r} + chr(10))" if out else "pass"
    assert verify_in_worker(tiny_bundle, TINY / "proof.json", 30, cmd=[sys.executable, "-c", code])[0] == Verdict.ERROR


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "5", None])
def test_bad_timeout_is_error(tiny_bundle, timeout):
    v, detail = verify_in_worker(tiny_bundle, TINY / "proof.json", timeout, cmd=[sys.executable, "-c", "1/0"])
    assert v == Verdict.ERROR and detail.startswith("bad timeout")


def test_huge_output_is_error(tiny_bundle):
    # One well-formed answer line, just too long: only the size cap can refuse it.
    code = "import json, sys; print(json.dumps(['accept', 'x' * 70000]))"
    assert verify_in_worker(tiny_bundle, TINY / "proof.json", 30, cmd=[sys.executable, "-c", code])[0] == Verdict.ERROR


def test_deadline_kills_the_whole_process_group(tiny_bundle, tmp_path):
    import os
    pidfile = tmp_path / "pid"
    code = ("import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open({str(pidfile)!r}, 'w').write(str(c.pid)); time.sleep(60)")
    assert verify_in_worker(tiny_bundle, TINY / "proof.json", 2, cmd=[sys.executable, "-c", code])[0] == Verdict.ERROR
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"grandchild {pid} survived the deadline")
