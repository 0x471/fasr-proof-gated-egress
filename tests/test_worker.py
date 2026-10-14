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
    "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
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
