import asyncio
import struct

import pytest

from pge.protocol import (CLIENT_MAX, ProtocolError, client_error, frame, loads, parse_client, parse_server,
                          read_frame)

FELT = "00" * 32


def _read(data: bytes, limit: int = CLIENT_MAX):
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(data)
        r.feed_eof()
        return await read_frame(r, limit)
    return asyncio.run(go())


def test_frame_round_trip():
    assert _read(frame(b'{"a":1}')) == b'{"a":1}'
    assert _read(b"") is None


@pytest.mark.parametrize("data", [
    b"\x00\x00",
    struct.pack(">I", 0),
    struct.pack(">I", CLIENT_MAX + 1) + b" " * (CLIENT_MAX + 1),
    struct.pack(">I", 10) + b"short",
])
def test_bad_frames(data):
    with pytest.raises(ProtocolError):
        _read(data)


@pytest.mark.parametrize("raw", [
    b'{"id": 1, "id": 2, "x": [[1]]}',
    b'{"id": 1, "x": [[NaN]]}',
    b'{"id": 1, "x": [[Infinity]]}',
    b'{"id": 1, "x": [[1e400]]}',
    b'{"id": 1, "x": [[true]]}',
    b'{"id": 1, "x": [["1"]]}',
    b'{"id": 1, "x": [[]]}',
    b'{"id": 1, "x": []}',
    b'{"id": 1, "x": [1]}',
    b'{"id": -1, "x": [[1]]}',
    b'{"id": 9007199254740992, "x": [[1]]}',
    b'{"id": 1.0, "x": [[1]]}',
    b'{"id": true, "x": [[1]]}',
    b'{"id": 1, "x": [[1]], "extra": 0}',
    b'{"x": [[1]]}',
    b'[1]',
    b'\xff',
    b'{"id": 1, "x": ' + b"[" * 5000 + b"]" * 5000 + b"}",
])
def test_bad_client_messages(raw):
    with pytest.raises(ProtocolError):
        parse_client(raw)


def test_good_client_message():
    assert parse_client(b'{"id": 0, "x": [[0.5, -1, 2e-3]]}') == (0, [[0.5, -1, 0.002]])


@pytest.mark.parametrize("raw", [
    '{"seq": 1, "outputs": [["%s"]], "proof": "0"}' % FELT,
    '{"seq": 1, "outputs": [["%s"]], "proof": "AB"}' % FELT,
    '{"seq": 1, "outputs": [["%s"]], "proof": ""}' % FELT,
    '{"seq": 1, "outputs": [["%s"]], "proof": [1, 2]}' % FELT,
    '{"seq": 1, "outputs": [["%s"]], "proof": "0x00"}' % FELT,
    '{"seq": 1, "outputs": [["%s"]], "proof": "00"}' % FELT.upper().replace("0", "A"),
    '{"seq": 1, "outputs": [["%s"]], "proof": "00"}' % FELT[:-2],
    '{"seq": 1, "outputs": [[1]], "proof": "00"}',
    '{"seq": 1, "outputs": [["%s"]], "proof": "00", "instances": []}' % FELT,
])
def test_bad_server_messages(raw):
    with pytest.raises(ProtocolError):
        parse_server(raw.encode())


def test_good_server_message():
    assert parse_server(('{"seq": 7, "outputs": [["%s"]], "proof": "00ff"}' % FELT).encode()) == (7, [[FELT]], b"\x00\xff")


def test_only_two_error_strings():
    assert loads(client_error(3, "unavailable")) == {"id": 3, "ok": False, "error": "unavailable"}
    with pytest.raises(ValueError):
        client_error(3, "rejected: [halo2] constraint system not satisfied")


@pytest.mark.parametrize("seq", ["true", "1.0", "-1", "9007199254740992", '"1"', "null", "[1]", "{}"])
def test_bad_server_seq(seq):
    with pytest.raises(ProtocolError):
        parse_server(('{"seq": %s, "outputs": [["%s"]], "proof": "00"}' % (seq, FELT)).encode())


def test_frame_must_complete_in_time():
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(b"\x00\x00\x00\x05ab")
        return await read_frame(r, 100, body_timeout=0.2)
    with pytest.raises(ProtocolError):
        asyncio.run(go())
