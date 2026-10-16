"""Run a child with a deadline and a live output cap, in its own process group.

Output is read from a pipe as it is produced, so a child that writes too much is killed
at the limit instead of filling a file first. At the deadline or the limit the whole
process group is killed (SIGKILL) before the child is reaped.

Limit: processes the child leaves behind after a normal exit are not killed.
"""
from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time

OK = "ok"
TIMEOUT = "timeout"
TOO_LONG = "too_long"
NO_START = "no_start"


def _kill(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        p.kill()


def run_bounded(argv: list[str], timeout: float, max_output: int, env: dict | None = None,
                stderr: bool = True) -> tuple[str, int | None, bytes]:
    """Return (status, exit code or None, output). stderr=True merges stderr into the output."""
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT if stderr else subprocess.DEVNULL,
                             env=env, start_new_session=True)
    except OSError as e:
        return NO_START, None, (e.strerror or type(e).__name__).encode()
    end = time.monotonic() + timeout
    out = bytearray()
    status = OK
    fd = p.stdout.fileno()
    with selectors.DefaultSelector() as sel:
        sel.register(fd, selectors.EVENT_READ)
        while True:
            left = end - time.monotonic()
            if left <= 0:
                status = TIMEOUT
                break
            if not sel.select(left):
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            out += chunk
            if len(out) > max_output:
                status = TOO_LONG
                break
    if status == OK:
        try:
            rc = p.wait(timeout=max(end - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            status = TIMEOUT
    if status != OK:
        # The group still has its leader until we reap it, so its id can't be reused yet.
        _kill(p)
        rc = p.wait()
    p.stdout.close()
    return status, rc, bytes(out)
