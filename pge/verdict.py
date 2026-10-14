"""Turn an EZKL verify call into ACCEPT, REJECT or ERROR.

REJECT means the proof was checked and is invalid or malformed. ERROR means the check
itself did not happen properly (missing files, crashes, unexpected results). The device
releases output only on ACCEPT; tests must treat ERROR as a failure, not as a rejection.
"""
from __future__ import annotations

import asyncio
import enum
import inspect
import json
import tempfile
from pathlib import Path

from pge.binding import BindingError, felt_to_hex, hex_to_felt

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


def read_proof(path: str | Path) -> bytes:
    """Read at most MAX_PROOF_BYTES. OSError means the read failed, BindingError a too-large proof."""
    with open(path, "rb") as f:
        data = f.read(MAX_PROOF_BYTES + 1)
    if len(data) > MAX_PROOF_BYTES:
        raise BindingError("proof too large")
    return data


def canonical_proof(data: bytes) -> bytes:
    """Re-encode the only two fields EZKL needs, or raise BindingError if the proof is malformed.

    Doing this in memory means a load error from EZKL can never stand for a bad proof:
    EZKL's messages don't say whether a load failed on the data or on I/O.
    """
    try:
        obj = json.loads(data)
    except (ValueError, RecursionError) as e:
        raise BindingError("proof is not JSON") from e
    if not isinstance(obj, dict):
        raise BindingError("proof is not a JSON object")
    proof, instances = obj.get("proof"), obj.get("instances")
    if not isinstance(proof, list) or not all(type(v) is int and 0 <= v <= 255 for v in proof):
        raise BindingError("proof bytes must be a list of integers in [0, 255]")
    if not isinstance(instances, list) or not all(isinstance(col, list) for col in instances):
        raise BindingError("instances must be a list of lists")
    cols = [[felt_to_hex(hex_to_felt(h)) for h in col] for col in instances]
    return json.dumps({"instances": cols, "proof": proof}).encode()


def classify_exception(msg: str) -> Verdict:
    """REJECT only when the verification itself failed; everything else is ERROR.

    A [halo2] failure is only meaningful if settings, vk and SRS are the pinned ones,
    see Bundle.verify. Proof load errors are ERROR because canonical_proof has already
    checked the proof's shape.
    """
    if msg.startswith("Failed to run verify: [halo2] "):
        return Verdict.REJECT
    return Verdict.ERROR


def _call(ezkl, proof: Path, settings, vk, srs) -> tuple[Verdict, str]:
    try:
        r = _run(ezkl.verify, str(proof), str(settings), str(vk), srs_path=str(srs))
    except Exception as e:
        lines = str(e).splitlines()
        msg = lines[0] if lines else type(e).__name__
        return classify_exception(msg), msg[:200]
    if r is True:
        return Verdict.ACCEPT, ""
    if r is False:
        return Verdict.REJECT, "verify returned False"
    return Verdict.ERROR, f"unexpected verify result {r!r}"


def verify(ezkl, proof: str | Path, settings: str | Path, vk: str | Path, srs: str | Path) -> tuple[Verdict, str]:
    for p in (settings, vk, srs):
        if not Path(p).is_file():
            return Verdict.ERROR, f"missing file {p}"
    # Read the proof once and verify a private canonical copy, so a read failure is never
    # taken for a malformed proof and the file can't change between the read and the check.
    try:
        data = read_proof(proof)
    except OSError as e:
        return Verdict.ERROR, f"cannot read proof: {e.strerror or type(e).__name__}"
    except BindingError as e:
        return Verdict.REJECT, str(e)
    try:
        canonical = canonical_proof(data)
    except BindingError as e:
        return Verdict.REJECT, f"malformed proof: {e}"
    try:
        with tempfile.TemporaryDirectory(prefix="pge-proof-") as d:
            snapshot = Path(d) / "proof.json"
            snapshot.write_bytes(canonical)
            result = _call(ezkl, snapshot, settings, vk, srs)
    except OSError as e:
        return Verdict.ERROR, f"cannot stage proof: {e.strerror or type(e).__name__}"
    return result
