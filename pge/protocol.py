"""Wire format of the v0 gate service: length-prefixed JSON frames. See docs/SERVICE_SPEC.md."""
from __future__ import annotations

import asyncio
import json
import math
import struct

CLIENT_MAX = 4096
REPLY_MAX = 4096
SERVER_MAX = 65536
MAX_ID = 1 << 53
ERRORS = ("bad_request", "unavailable")
_HEX = frozenset("0123456789abcdef")


class ProtocolError(ValueError):
    pass


def _object(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ProtocolError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _constant(name):
    raise ProtocolError(f"{name} is not allowed")


def loads(data: bytes):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ProtocolError("frame is not utf-8") from e
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as e:
        raise ProtocolError("frame is not json") from e


def dumps(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).encode()


def frame(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader, limit: int) -> bytes | None:
    """Return the next payload, None on a clean end of stream, or raise ProtocolError."""
    try:
        head = await reader.readexactly(4)
    except asyncio.IncompleteReadError as e:
        if not e.partial:
            return None
        raise ProtocolError("truncated frame header") from e
    n = struct.unpack(">I", head)[0]
    if not 0 < n <= limit:
        raise ProtocolError(f"frame length {n} outside 1..{limit}")
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        raise ProtocolError("truncated frame") from e


def _keys(obj, keys: tuple[str, ...], what: str) -> None:
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ProtocolError(f"{what} must be an object with exactly the keys {', '.join(keys)}")


def _counter(v, what: str) -> int:
    if type(v) is not int or not 0 <= v < MAX_ID:
        raise ProtocolError(f"{what} must be an integer in [0, 2^53)")
    return v


def _tensors(v, what: str, check) -> list[list]:
    if not isinstance(v, list) or not v or not all(isinstance(t, list) and t for t in v):
        raise ProtocolError(f"{what} must be a non-empty list of non-empty lists")
    for t in v:
        for e in t:
            check(e, what)
    return v


def _number(e, what: str) -> None:
    if type(e) not in (int, float) or (type(e) is float and not math.isfinite(e)):
        raise ProtocolError(f"{what} must hold finite numbers")


def _felt(e, what: str) -> None:
    if not isinstance(e, str) or len(e) != 64 or not set(e) <= _HEX:
        raise ProtocolError(f"{what} must hold 64-character lower-case hex strings")


def parse_client(data: bytes) -> tuple[int, list[list[float]]]:
    msg = loads(data)
    _keys(msg, ("id", "x"), "request")
    return _counter(msg["id"], "id"), _tensors(msg["x"], "x", _number)


def parse_server(data: bytes) -> tuple[int, list[list[str]], bytes]:
    msg = loads(data)
    _keys(msg, ("seq", "outputs", "proof"), "answer")
    seq = _counter(msg["seq"], "seq")
    outputs = _tensors(msg["outputs"], "outputs", _felt)
    proof = msg["proof"]
    if not isinstance(proof, str) or not proof or len(proof) % 2 or not set(proof) <= _HEX:
        raise ProtocolError("proof must be non-empty lower-case hex")
    return seq, outputs, bytes.fromhex(proof)


def server_request(seq: int, x) -> bytes:
    return dumps({"seq": seq, "x": x})


def client_ok(cid: int, y) -> bytes:
    return dumps({"id": cid, "ok": True, "y": y})


def client_error(cid: int, error: str) -> bytes:
    if error not in ERRORS:
        raise ValueError(error)
    return dumps({"id": cid, "ok": False, "error": error})
