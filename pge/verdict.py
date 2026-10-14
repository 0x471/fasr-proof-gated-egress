"""Turn an EZKL verify call into ACCEPT, REJECT or ERROR.

REJECT means the proof was checked and is invalid or malformed. ERROR means the check
itself did not happen properly (missing files, crashes, unexpected results). The device
releases output only on ACCEPT; tests must treat ERROR as a failure, not as a rejection.
"""
from __future__ import annotations

import asyncio
import enum
import inspect
import re
import tempfile
from pathlib import Path

# EZKL proofs for the test bundles are about 18 KB.
MAX_PROOF_BYTES = 1 << 20


class Verdict(enum.Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    ERROR = "error"


def _run(fn, *a, **k):
    async def _c():
        r = fn(*a, **k)
        return await r if inspect.isawaitable(r) else r
    return asyncio.run(_c())


_PREFIX = "Failed to run verify: "
_LOAD_PROOF = _PREFIX + "[pfsys] failed to load the proof: "
# serde_json reports parse and data errors with a position; I/O errors have none.
_PARSE_ERROR = re.compile(r" at line \d+ column \d+$")


def classify_exception(msg: str) -> Verdict:
    """REJECT only for anchored categories that mean the proof itself was bad.

    A [halo2] failure is only meaningful if settings, vk and SRS are the pinned ones,
    see Bundle.verify. Everything else, including I/O errors while loading, is ERROR.
    """
    if msg.startswith(_PREFIX + "[halo2] "):
        return Verdict.REJECT
    if msg.startswith(_LOAD_PROOF) and _PARSE_ERROR.search(msg):
        return Verdict.REJECT
    return Verdict.ERROR


def verify(ezkl, proof: str | Path, settings: str | Path, vk: str | Path, srs: str | Path) -> tuple[Verdict, str]:
    for p in (settings, vk, srs):
        if not Path(p).is_file():
            return Verdict.ERROR, f"missing file {p}"
    # Read the proof once and verify a private copy, so a read failure is never taken
    # for a malformed proof and the file can't change between the read and the check.
    try:
        with open(proof, "rb") as f:
            data = f.read(MAX_PROOF_BYTES + 1)
    except OSError as e:
        return Verdict.ERROR, f"cannot read proof: {e.strerror or type(e).__name__}"
    if len(data) > MAX_PROOF_BYTES:
        return Verdict.REJECT, "proof too large"
    with tempfile.TemporaryDirectory(prefix="pge-proof-") as d:
        snapshot = Path(d) / "proof.json"
        snapshot.write_bytes(data)
        try:
            r = _run(ezkl.verify, str(snapshot), str(settings), str(vk), srs_path=str(srs))
        except Exception as e:
            lines = str(e).splitlines()
            msg = lines[0] if lines else type(e).__name__
            return classify_exception(msg), msg[:200]
    if r is True:
        return Verdict.ACCEPT, ""
    if r is False:
        return Verdict.REJECT, "verify returned False"
    return Verdict.ERROR, f"unexpected verify result {r!r}"
