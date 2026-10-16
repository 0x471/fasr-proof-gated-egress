"""Run one verify in a child process with a deadline.

The in-process adapter cannot stop a hung verifier or survive a native crash. Here the
child runs in its own process group, which is killed (SIGKILL) at the deadline, and a
timeout, crash, non-zero exit, output over 64 KiB or anything but exactly one
well-formed answer line is ERROR.

Limits: the bundle check runs before the deadline starts, and processes the child leaves
behind after a normal exit are not killed.
"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from pge.bundle import Bundle
from pge.verdict import Verdict

ROOT = Path(__file__).resolve().parent.parent
MAX_OUTPUT_BYTES = 1 << 16
_CHILD = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
          "from pge.worker import main; sys.exit(main(sys.argv[1:]))")


def verify_in_worker(bundle: Bundle, proof: str | Path, timeout: float,
                     cmd: list[str] | None = None) -> tuple[Verdict, str]:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < math.inf:
        return Verdict.ERROR, f"bad timeout {timeout!r}"
    try:
        bundle.check()
    except Exception as e:
        return Verdict.ERROR, f"bundle check failed: {e}"
    argv = (cmd or [sys.executable, "-I", "-c", _CHILD]) + [
        str(proof), str(bundle.settings), str(bundle.vk), str(bundle.srs)]
    with tempfile.TemporaryFile() as out:
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except OSError as e:
            return Verdict.ERROR, f"cannot start worker: {e.strerror or type(e).__name__}"
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                p.kill()
            p.wait()
            return Verdict.ERROR, f"verify timed out after {timeout} s"
        out.seek(0)
        data = out.read(MAX_OUTPUT_BYTES + 1)
    if rc != 0:
        return Verdict.ERROR, f"worker exited with {rc}"
    if len(data) > MAX_OUTPUT_BYTES:
        return Verdict.ERROR, "worker output too long"
    lines = data.splitlines()
    try:
        answer = json.loads(lines[0]) if len(lines) == 1 else None
        if not (isinstance(answer, list) and len(answer) == 2 and all(isinstance(a, str) for a in answer)):
            return Verdict.ERROR, "worker must answer with one line: [verdict, detail]"
        return Verdict(answer[0]), answer[1]
    except (ValueError, RecursionError):
        return Verdict.ERROR, "unreadable worker answer"


def main(args: list[str]) -> int:
    import ezkl

    from pge.verdict import verify

    verdict, detail = verify(ezkl, *args)
    print(json.dumps([verdict.value, detail]), flush=True)
    return 0
