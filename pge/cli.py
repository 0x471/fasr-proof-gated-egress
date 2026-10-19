"""Verify with EZKL's official command-line program.

Pi OS has no EZKL Python wheel, so the device runs the pinned CLI binary instead of the
Python package. The proof is checked and reduced to its canonical form in memory first,
as in verdict.verify. The CLI then runs with a minimal environment in its own process
group, which is killed (SIGKILL) at the deadline or as soon as its output passes 64 KiB
(pge/proc.py). It runs with one thread and resource limits (cli_limits): address space
on Linux, CPU seconds up to the deadline, no file writes, few open files. The verdict is
anchored on the exit code and the CLI's log lines, as in device/check_verifier.sh.

Limits: the bundle check runs before the deadline starts, and processes the CLI leaves
behind after a normal exit are not killed. CLI_MEMORY fits the test bundles (the tiny
bundle needs about 128 MiB with one thread); a larger circuit needs a larger value.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
from pathlib import Path

from pge.binding import BindingError
from pge.bundle import Bundle
from pge.proc import NO_START, TIMEOUT, TOO_LONG, Limits, memory_limit_supported, run_bounded
from pge.verdict import MAX_PROOF_BYTES, Verdict, canonical_proof, read_proof

MAX_OUTPUT_BYTES = 1 << 16
# Twice what the tiny bundle needs with one thread (about 128 MiB, measured on Linux 16 and 19 Oct).
CLI_MEMORY = 256 << 20
CLI_OPEN_FILES = 64
# Verify writes no files; a CLI that tries to is stopped.
CLI_FILE_SIZE: int | None = 0

_ERR = re.compile(r"\[E\] \[[^\]]*, ezkl\] - (.*)")
_VERIFIED = re.compile(r"\[\*\] \[[^\]]*, ezkl::execute\] - verified: (true|false)")


def classify_cli(rc: int, out: bytes) -> tuple[Verdict, str]:
    """ACCEPT needs exit 0, no error line and one "verified: true" line. REJECT needs exit 1,
    one "verified: false" line and exactly one error line, a [halo2] one. Anything else is ERROR."""
    if len(out) > MAX_OUTPUT_BYTES:
        return Verdict.ERROR, "cli output too long"
    try:
        lines = out.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return Verdict.ERROR, "cli output is not utf-8"
    errs = [m.group(1) for line in lines if (m := _ERR.fullmatch(line))]
    verified = [m.group(1) for line in lines if (m := _VERIFIED.fullmatch(line))]
    if rc == 0 and not errs and verified == ["true"]:
        return Verdict.ACCEPT, ""
    if rc == 1 and len(errs) == 1 and errs[0].startswith("[halo2] ") and verified == ["false"]:
        return Verdict.REJECT, errs[0][:200]
    return Verdict.ERROR, f"cli exit {rc}" + (f": {errs[0][:200]}" if errs else "")


def _env() -> dict[str, str]:
    # One thread: rayon's thread stacks and malloc arenas are what push the address space up.
    env = {"PATH": os.defpath, "LC_ALL": "C", "RAYON_NUM_THREADS": "1"}
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    return env


def cli_limits(timeout: float) -> Limits:
    return Limits(memory=CLI_MEMORY if memory_limit_supported() else None, cpu=max(1, math.ceil(timeout)),
                  file_size=CLI_FILE_SIZE, open_files=CLI_OPEN_FILES)


def _run(argv: list[str], timeout: float) -> tuple[Verdict, str]:
    status, rc, out = run_bounded(argv, timeout, MAX_OUTPUT_BYTES, env=_env(), limits=cli_limits(timeout))
    if status == NO_START:
        return Verdict.ERROR, f"cannot start cli: {out.decode(errors='replace')}"
    if status == TIMEOUT:
        return Verdict.ERROR, f"verify timed out after {timeout} s"
    if status == TOO_LONG:
        return Verdict.ERROR, "cli output too long"
    return classify_cli(rc, out)


def verify_with_cli(bundle: Bundle, proof: str | Path, cli: str | Path, timeout: float) -> tuple[Verdict, str]:
    try:
        data = read_proof(proof)
    except OSError as e:
        return Verdict.ERROR, f"cannot read proof: {e.strerror or type(e).__name__}"
    except BindingError as e:
        return Verdict.REJECT, str(e)
    return verify_bytes_with_cli(bundle, data, cli, timeout)


def verify_bytes_with_cli(bundle: Bundle, data: bytes, cli: str | Path, timeout: float) -> tuple[Verdict, str]:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < math.inf:
        return Verdict.ERROR, f"bad timeout {timeout!r}"
    try:
        bundle.check()
    except (OSError, BindingError) as e:
        return Verdict.ERROR, f"bundle check failed: {e}"
    if len(data) > MAX_PROOF_BYTES:
        return Verdict.REJECT, "proof too large"
    try:
        canonical = canonical_proof(data)
    except BindingError as e:
        return Verdict.REJECT, f"malformed proof: {e}"
    try:
        with tempfile.TemporaryDirectory(prefix="pge-cli-") as d:
            snapshot = Path(d) / "proof.json"
            snapshot.write_bytes(canonical)
            argv = [str(cli), "verify", "--settings-path", str(bundle.settings), "--vk-path", str(bundle.vk),
                    "--srs-path", str(bundle.srs), "--proof-path", str(snapshot)]
            return _run(argv, timeout)
    except OSError as e:
        return Verdict.ERROR, f"cannot stage proof: {e.strerror or type(e).__name__}"
