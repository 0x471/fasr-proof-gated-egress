"""Wire format of the gate service (v1): length-prefixed binary frames of fixed size. See docs/SERVICE_SPEC.md.

For one pinned circuit every message type has one size, except the server's answer, whose
proof length the gate checks against its pin. Numbers are big-endian; field elements are
the 32 little-endian bytes EZKL uses.
"""
from __future__ import annotations

import asyncio
import math
import struct
from dataclasses import dataclass

REQUEST, FORWARD, ANSWER, REPLY = 1, 2, 3, 4
OK, BAD_REQUEST, UNAVAILABLE = 0, 1, 2
ERRORS = ("bad_request", "unavailable")
_STATUS = {"bad_request": BAD_REQUEST, "unavailable": UNAVAILABLE}
MAX_ID = 1 << 53
FELT_BYTES = 32
# Upper bound for a proof in one answer; the gate pins the exact length (Policy.proof_bytes).
PROOF_MAX = 1 << 16
_HEAD = struct.Struct(">BQ")


class ProtocolError(ValueError):
    pass


def frame(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader, limit: int, body_timeout: float | None = None) -> bytes | None:
    """Return the next payload, None on a clean end of stream, or raise ProtocolError.

    body_timeout bounds how long a started frame may take to arrive in full, from its first byte.
    """
    try:
        first = await reader.readexactly(1)
    except asyncio.IncompleteReadError:
        return None
    loop = asyncio.get_running_loop()
    end = None if body_timeout is None else loop.time() + body_timeout
    left = lambda: None if end is None else max(0.0, end - loop.time())
    try:
        head = first + await asyncio.wait_for(reader.readexactly(3), left())
    except asyncio.IncompleteReadError as e:
        raise ProtocolError("truncated frame header") from e
    except asyncio.TimeoutError as e:
        raise ProtocolError("frame not completed in time") from e
    n = struct.unpack(">I", head)[0]
    if not 0 < n <= limit:
        raise ProtocolError(f"frame length {n} outside 1..{limit}")
    try:
        return await asyncio.wait_for(reader.readexactly(n), left())
    except asyncio.IncompleteReadError as e:
        raise ProtocolError("truncated frame") from e
    except asyncio.TimeoutError as e:
        raise ProtocolError("frame not completed in time") from e


def _head(data: bytes, kind: int, size: int, what: str) -> int:
    if len(data) != size:
        raise ProtocolError(f"{what} must be {size} bytes, got {len(data)}")
    t, n = _HEAD.unpack_from(data)
    if t != kind:
        raise ProtocolError(f"{what} has type {t}, expected {kind}")
    if n >= MAX_ID:
        raise ProtocolError(f"{what} counter must be below 2^53")
    return n


def _split(flat: list, lens: tuple[int, ...]) -> list[list]:
    out, i = [], 0
    for n in lens:
        out.append(flat[i:i + n])
        i += n
    return out


def _floats(data: bytes, offset: int, n: int, what: str) -> list[float]:
    vals = list(struct.unpack_from(f">{n}d", data, offset))
    if not all(math.isfinite(v) for v in vals):
        raise ProtocolError(f"{what} must hold finite numbers")
    return vals


def _flat(tensors, lens: tuple[int, ...], what: str) -> list[float]:
    if len(tensors) != len(lens) or any(len(t) != n for t, n in zip(tensors, lens)):
        raise ValueError(f"{what} has the wrong shape")
    return [float(v) for t in tensors for v in t]


@dataclass(frozen=True)
class Wire:
    """Message sizes and codecs for one pinned circuit."""
    input_lens: tuple[int, ...]
    output_lens: tuple[int, ...]

    @classmethod
    def for_spec(cls, spec) -> "Wire":
        return cls(tuple(spec.input_lens), tuple(math.prod(sh) for sh in spec.output_shapes))

    @property
    def n_in(self) -> int:
        return sum(self.input_lens)

    @property
    def n_out(self) -> int:
        return sum(self.output_lens)

    @property
    def request_size(self) -> int:
        return _HEAD.size + 8 * self.n_in

    @property
    def answer_fixed(self) -> int:
        return _HEAD.size + FELT_BYTES * self.n_out

    @property
    def answer_max(self) -> int:
        return self.answer_fixed + PROOF_MAX

    @property
    def reply_size(self) -> int:
        return _HEAD.size + 1 + 8 * self.n_out

    # client -> device, and device -> server: (counter, x)
    def _encode_x(self, kind: int, n: int, x) -> bytes:
        return _HEAD.pack(kind, n) + struct.pack(f">{self.n_in}d", *_flat(x, self.input_lens, "x"))

    def _parse_x(self, data: bytes, kind: int, what: str) -> tuple[int, list[list[float]]]:
        n = _head(data, kind, self.request_size, what)
        return n, _split(_floats(data, _HEAD.size, self.n_in, "x"), self.input_lens)

    def encode_request(self, cid: int, x) -> bytes:
        return self._encode_x(REQUEST, cid, x)

    def parse_request(self, data: bytes) -> tuple[int, list[list[float]]]:
        return self._parse_x(data, REQUEST, "request")

    def encode_forward(self, seq: int, x) -> bytes:
        return self._encode_x(FORWARD, seq, x)

    def parse_forward(self, data: bytes) -> tuple[int, list[list[float]]]:
        return self._parse_x(data, FORWARD, "forwarded request")

    # server -> device: (seq, outputs as 64-character hex field elements, raw proof)
    def encode_answer(self, seq: int, outputs, proof: bytes) -> bytes:
        felts = b"".join(bytes.fromhex(h) for t in outputs for h in t)
        return _HEAD.pack(ANSWER, seq) + felts + proof

    def parse_answer(self, data: bytes) -> tuple[int, list[list[str]], bytes]:
        if not self.answer_fixed < len(data) <= self.answer_max:
            raise ProtocolError(f"answer must be {self.answer_fixed + 1}..{self.answer_max} bytes, got {len(data)}")
        seq = _head(data[:_HEAD.size], ANSWER, _HEAD.size, "answer")
        body = data[_HEAD.size:self.answer_fixed]
        felts = [body[i:i + FELT_BYTES].hex() for i in range(0, len(body), FELT_BYTES)]
        return seq, _split(felts, self.output_lens), data[self.answer_fixed:]

    # device -> client: one size for every reply
    def reply_ok(self, cid: int, y) -> bytes:
        return _HEAD.pack(REPLY, cid) + bytes([OK]) + struct.pack(f">{self.n_out}d", *_flat(y, self.output_lens, "y"))

    def reply_error(self, cid: int, error: str) -> bytes:
        if error not in ERRORS:
            raise ValueError(error)
        return _HEAD.pack(REPLY, cid) + bytes([_STATUS[error]]) + bytes(8 * self.n_out)

    def parse_reply(self, data: bytes) -> dict:
        cid = _head(data, REPLY, self.reply_size, "reply")
        status = data[_HEAD.size]
        if status == OK:
            return {"id": cid, "ok": True, "y": _split(_floats(data, _HEAD.size + 1, self.n_out, "y"), self.output_lens)}
        for name, code in _STATUS.items():
            if status == code and not any(data[_HEAD.size + 1:]):
                return {"id": cid, "ok": False, "error": name}
        raise ProtocolError("reply has an unknown status or a non-zero error body")
