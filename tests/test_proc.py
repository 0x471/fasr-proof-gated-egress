import os
import sys
import time

import pytest

from pge.proc import NO_START, OK, TIMEOUT, TOO_LONG, run_bounded

PY = sys.executable


def _gone(pid):
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_normal_exit_returns_code_and_output():
    assert run_bounded([PY, "-c", "print('hi'); raise SystemExit(3)"], 30, 100) == (OK, 3, b"hi\n")


def test_continuous_writer_is_stopped_at_the_limit(tmp_path):
    # Writes 8 MiB in 64 KiB chunks; must be killed just past 64 KiB, long before it finishes.
    marker = tmp_path / "done"
    code = (f"import sys\nfor _ in range(128):\n    sys.stdout.buffer.write(b'x' * 65536)\n    sys.stdout.flush()\n"
            f"open({str(marker)!r}, 'w').write('finished')")
    start = time.monotonic()
    status, rc, out = run_bounded([PY, "-c", code], 30, 65536)
    assert status == TOO_LONG and rc is not None and rc < 0
    assert 65536 < len(out) <= 2 * 65536
    assert not marker.exists() and time.monotonic() - start < 10


def test_deadline_kills_the_group(tmp_path):
    pidfile = tmp_path / "pid"
    code = ("import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open({str(pidfile)!r}, 'w').write(str(c.pid)); time.sleep(60)")
    status, _, _ = run_bounded([PY, "-c", code], 2, 1000)
    assert status == TIMEOUT and _gone(int(pidfile.read_text()))


def test_closed_stdout_still_needs_an_exit_before_the_deadline():
    code = "import os, time; os.close(1); time.sleep(60)"
    start = time.monotonic()
    assert run_bounded([PY, "-c", code], 1, 1000)[0] == TIMEOUT
    assert time.monotonic() - start < 10


def test_stderr_merge_and_drop():
    code = "import sys; sys.stderr.write('e'); sys.stdout.write('o')"
    assert sorted(run_bounded([PY, "-c", code], 30, 100)[2]) == sorted(b"eo")
    assert run_bounded([PY, "-c", code], 30, 100, stderr=False)[2] == b"o"


def test_unstartable(tmp_path):
    assert run_bounded([str(tmp_path / "nope")], 30, 100)[0] == NO_START


@pytest.mark.parametrize("killer", ["import os, signal; os.kill(os.getpid(), signal.SIGKILL)"])
def test_killed_child_reports_the_signal(killer):
    status, rc, _ = run_bounded([PY, "-c", killer], 30, 100)
    assert status == OK and rc == -9
