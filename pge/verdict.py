"""Turn an EZKL verify call into ACCEPT, REJECT or ERROR.

REJECT means the proof was checked and is invalid or malformed. ERROR means the check
itself did not happen properly (missing files, crashes, unexpected results). The device
releases output only on ACCEPT; tests must treat ERROR as a failure, not as a rejection.
"""
from __future__ import annotations

import asyncio
import enum
import inspect
from pathlib import Path


class Verdict(enum.Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    ERROR = "error"


def _run(fn, *a, **k):
    async def _c():
        r = fn(*a, **k)
        return await r if inspect.isawaitable(r) else r
    return asyncio.run(_c())


def classify_exception(msg: str) -> Verdict:
    if "[halo2]" in msg or "failed to load the proof" in msg:
        return Verdict.REJECT
    return Verdict.ERROR


def verify(ezkl, proof: str | Path, settings: str | Path, vk: str | Path, srs: str | Path) -> tuple[Verdict, str]:
    for p in (proof, settings, vk, srs):
        if not Path(p).is_file():
            return Verdict.ERROR, f"missing file {p}"
    try:
        r = _run(ezkl.verify, str(proof), str(settings), str(vk), srs_path=str(srs))
    except Exception as e:
        msg = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
        return classify_exception(msg), msg
    if r is True:
        return Verdict.ACCEPT, ""
    if r is False:
        return Verdict.REJECT, "verify returned False"
    return Verdict.ERROR, f"unexpected verify result {r!r}"
