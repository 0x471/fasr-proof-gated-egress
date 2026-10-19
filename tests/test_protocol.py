import asyncio
import random
import struct

import pytest

from pge.protocol import (ANSWER, FORWARD, PROOF_MAX, REPLY, REQUEST, ProtocolError, Wire, frame, read_frame)

FELT = "00" * 32
W = Wire(input_lens=(4,), output_lens=(3,))
X = [[0.5, -0.2, 0.9, 0.1]]
Y = [[0.25, 0.0, -1.5]]


def _read(data: bytes, limit: int = W.request_size):
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(data)
        r.feed_eof()
        return await read_frame(r, limit)
    return asyncio.run(go())


def test_frame_round_trip():
    assert _read(frame(b"\x01\x02")) == b"\x01\x02"
    assert _read(b"") is None


@pytest.mark.parametrize("data", [
    b"\x00\x00",
    struct.pack(">I", 0),
    struct.pack(">I", W.request_size + 1) + b" " * (W.request_size + 1),
    struct.pack(">I", 10) + b"short",
])
def test_bad_frames(data):
    with pytest.raises(ProtocolError):
        _read(data)


def test_sizes_are_fixed_by_the_circuit():
    assert W.request_size == 1 + 8 + 8 * 4
    assert W.reply_size == 1 + 8 + 1 + 8 * 3
    assert len(W.reply_ok(1, Y)) == len(W.reply_error(1, "bad_request")) == len(W.reply_error(1, "unavailable"))


def test_request_and_forward_round_trip():
    assert W.parse_request(W.encode_request(7, X)) == (7, X)
    assert W.parse_forward(W.encode_forward(9, X)) == (9, X)


def test_reply_round_trip():
    assert W.parse_reply(W.reply_ok(3, Y)) == {"id": 3, "ok": True, "y": Y}
    assert W.parse_reply(W.reply_error(3, "unavailable")) == {"id": 3, "ok": False, "error": "unavailable"}
    assert W.parse_reply(W.reply_error(4, "bad_request")) == {"id": 4, "ok": False, "error": "bad_request"}


def test_answer_round_trip():
    outputs = [[FELT, "01" + "00" * 31, "ff" * 31 + "0f"]]
    assert W.parse_answer(W.encode_answer(5, outputs, b"\x00\xff" * 4)) == (5, outputs, b"\x00\xff" * 4)


def _req(kind, n, vals):
    return struct.pack(">BQ", kind, n) + struct.pack(f">{len(vals)}d", *vals)


@pytest.mark.parametrize("raw", [
    _req(REQUEST, 1, [1, 2, 3]),
    _req(REQUEST, 1, [1, 2, 3, 4, 5]),
    _req(REQUEST, 1, [1, 2, 3, 4]) + b"\x00",
    _req(FORWARD, 1, [1, 2, 3, 4]),
    _req(REQUEST, 1 << 53, [1, 2, 3, 4]),
    _req(REQUEST, (1 << 64) - 1, [1, 2, 3, 4]),
    _req(REQUEST, 1, [float("nan"), 0, 0, 0]),
    _req(REQUEST, 1, [0, float("inf"), 0, 0]),
    _req(REQUEST, 1, [0, 0, float("-inf"), 0]),
    b"",
    b"\x01",
])
def test_bad_requests(raw):
    with pytest.raises(ProtocolError):
        W.parse_request(raw)


def test_negative_zero_survives():
    _, x = W.parse_request(W.encode_request(1, [[-0.0, 0, 0, 0]]))
    assert struct.pack(">d", x[0][0]) == struct.pack(">d", -0.0)


@pytest.mark.parametrize("raw", [
    struct.pack(">BQ", ANSWER, 1) + bytes(32 * 3),
    struct.pack(">BQ", ANSWER, 1) + bytes(32 * 3 + PROOF_MAX + 1),
    struct.pack(">BQ", REPLY, 1) + bytes(32 * 3 + 8),
    struct.pack(">BQ", ANSWER, 1 << 53) + bytes(32 * 3 + 8),
    b"\x03",
])
def test_bad_answers(raw):
    with pytest.raises(ProtocolError):
        W.parse_answer(raw)


@pytest.mark.parametrize("raw", [
    struct.pack(">BQ", REPLY, 1) + bytes([3]) + bytes(24),
    struct.pack(">BQ", REPLY, 1) + bytes([2]) + b"\x01" + bytes(23),
    struct.pack(">BQ", REQUEST, 1) + bytes([0]) + bytes(24),
    struct.pack(">BQ", REPLY, 1) + bytes([0]) + bytes(23),
])
def test_bad_replies(raw):
    with pytest.raises(ProtocolError):
        W.parse_reply(raw)


def test_only_two_error_strings():
    with pytest.raises(ValueError):
        W.reply_error(3, "rejected: [halo2] constraint system not satisfied")


def test_wrong_shapes_cannot_be_encoded():
    with pytest.raises(ValueError):
        W.encode_request(1, [[1, 2, 3]])
    with pytest.raises(ValueError):
        W.reply_ok(1, [[1, 2]])


def test_frame_must_complete_in_time():
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(b"\x00\x00\x00\x05ab")
        return await read_frame(r, 100, body_timeout=0.2)
    with pytest.raises(ProtocolError):
        asyncio.run(go())


# Fuzzing: a parser either refuses with ProtocolError or accepts, and an accepted message
# re-encodes to exactly the same bytes, so no message has a second accepted form.

def _valid():
    return [W.encode_request(11, X), W.encode_forward(12, X),
            W.encode_answer(13, [[FELT] * 3], bytes(range(64))),
            W.reply_ok(14, Y), W.reply_error(15, "unavailable")]


def _reencode(kind_parser, out, raw):
    name = kind_parser.__name__
    if name == "parse_request":
        return W.encode_request(*out)
    if name == "parse_forward":
        return W.encode_forward(*out)
    if name == "parse_answer":
        return W.encode_answer(*out)
    if out["ok"]:
        return W.reply_ok(out["id"], out["y"])
    return W.reply_error(out["id"], out["error"])


def _candidates(rng):
    for _ in range(3000):
        yield bytes(rng.getrandbits(8) for _ in range(rng.choice([0, 1, 9, W.request_size, W.reply_size,
                                                                   W.answer_fixed + 1, rng.randrange(200)])))
    for base in _valid():
        for _ in range(1500):
            b = bytearray(base)
            for _ in range(rng.randrange(1, 4)):
                op = rng.randrange(3)
                if op == 0 and b:
                    i = rng.randrange(len(b))
                    b[i] ^= 1 << rng.randrange(8)
                elif op == 1:
                    b.insert(rng.randrange(len(b) + 1), rng.getrandbits(8))
                elif b:
                    del b[rng.randrange(len(b))]
            yield bytes(b)


@pytest.mark.parametrize("parser", [W.parse_request, W.parse_forward, W.parse_answer, W.parse_reply],
                         ids=lambda p: p.__name__)
def test_fuzzed_input_is_refused_or_canonical(parser):
    rng = random.Random(20261019)
    accepted = 0
    for raw in _candidates(rng):
        try:
            out = parser(raw)
        except ProtocolError:
            continue
        accepted += 1
        assert _reencode(parser, out, raw) == raw
    assert accepted > 0


def test_proof_tail_bound_is_64_kib():
    assert PROOF_MAX == 65536
    head = struct.pack(">BQ", ANSWER, 1) + bytes(32 * 3)
    assert len(W.parse_answer(head + bytes(65536))[2]) == 65536
    with pytest.raises(ProtocolError):
        W.parse_answer(head + bytes(65537))


def test_split_frame_must_complete_within_one_deadline():
    async def go():
        loop = asyncio.get_running_loop()
        r = asyncio.StreamReader()
        r.feed_data(b"\x00")
        loop.call_later(0.15, r.feed_data, b"\x00\x00\x02")
        loop.call_later(0.3, r.feed_data, b"ab")
        return await read_frame(r, 100, body_timeout=0.25)
    with pytest.raises(ProtocolError):
        asyncio.run(go())


def test_frames_survive_any_chunking():
    rng = random.Random(20261020)
    for _ in range(300):
        payloads = [rng.choice(_valid()) for _ in range(rng.randrange(1, 5))]
        stream = b"".join(frame(p) for p in payloads)

        cuts = sorted(rng.sample(range(1, len(stream)), min(len(stream) - 1, rng.randrange(1, 60))))

        async def feed(r):
            for a, b in zip([0] + cuts, cuts + [len(stream)]):
                r.feed_data(stream[a:b])
                await asyncio.sleep(0)
            r.feed_eof()

        async def go():
            r = asyncio.StreamReader()
            feeder = asyncio.create_task(feed(r))
            got = []
            while (p := await read_frame(r, W.answer_max, body_timeout=1)) is not None:
                got.append(p)
            await feeder
            return got
        assert asyncio.run(go()) == payloads
