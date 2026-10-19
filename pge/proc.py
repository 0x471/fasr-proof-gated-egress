"""Run a child with a deadline and a live output cap, in its own process group.

Output is read from a pipe as it is produced, so a child that writes too much is killed
at the limit instead of filling a file first. At the deadline or the limit the whole
process group is killed (SIGKILL) before the child is reaped.

With Limits, a fresh single-threaded Python process sets the resource limits and then
execs the child. preexec_fn would instead run Python in a fork of a threaded program
(the service), which can deadlock. If a limit can't be set the child never starts.

Limit: processes the child leaves behind after a normal exit are not killed.
"""
from __future__ import annotations

import os
import resource
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

OK = "ok"
TIMEOUT = "timeout"
TOO_LONG = "too_long"
NO_START = "no_start"

_NO_START = b"pge-limits: no start\n"
# Python ignores SIGPIPE and SIGXFSZ, and an ignored signal survives exec, so restore both.
_TRAMPOLINE = (
    "import os, resource, signal, sys\n"
    "try:\n"
    "    for item in sys.argv[1].split(','):\n"
    "        if item:\n"
    "            r, v = map(int, item.split('='))\n"
    "            resource.setrlimit(r, (v, v))\n"
    "    signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n"
    "    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)\n"
    "    os.execv(sys.argv[2], sys.argv[2:])\n"
    "except BaseException:\n"
    f"    os.write(1, {_NO_START!r})\n"
    "    os._exit(127)\n"
)


@dataclass(frozen=True)
class Limits:
    """Per-child resource limits; None leaves one unchanged. Core dumps are always off.

    memory is address space in bytes (RLIMIT_AS). macOS refuses to set it, so asking for
    it there means the child never starts.
    """
    memory: int | None = None
    cpu: int | None = None
    file_size: int | None = None
    open_files: int | None = None

    def spec(self) -> str:
        pairs = [(resource.RLIMIT_CORE, 0)]
        for r, v in ((resource.RLIMIT_AS, self.memory), (resource.RLIMIT_CPU, self.cpu),
                     (resource.RLIMIT_FSIZE, self.file_size), (resource.RLIMIT_NOFILE, self.open_files)):
            if v is not None:
                if type(v) is not int or v < 0:
                    raise ValueError("limits must be non-negative integers or None")
                pairs.append((r, v))
        return ",".join(f"{r}={v}" for r, v in pairs)


def memory_limit_supported() -> bool:
    return sys.platform.startswith("linux")


def _kill(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        p.kill()


def run_bounded(argv: list[str], timeout: float, max_output: int, env: dict | None = None,
                stderr: bool = True, limits: Limits | None = None) -> tuple[str, int | None, bytes]:
    """Return (status, exit code or None, output). stderr=True merges stderr into the output."""
    if limits is not None:
        try:
            spec = limits.spec()
        except ValueError as e:
            return NO_START, None, str(e).encode()
        status, rc, out = run_bounded([sys.executable, "-I", "-S", "-c", _TRAMPOLINE, spec, *argv],
                                      timeout, max_output, env, stderr)
        if status == OK and rc == 127 and out == _NO_START:
            return NO_START, None, b"cannot set limits or start the child"
        return status, rc, out
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
