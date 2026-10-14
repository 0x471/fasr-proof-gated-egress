import json
import os
from pathlib import Path

import pytest

from conftest import TINY, TINY_X
from pge.binding import build_instances, split_instances
from pge.verdict import MAX_PROOF_BYTES, Verdict, classify_exception, verify


class FakeEzkl:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def verify(self, *a, **k):
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture()
def files(tmp_path):
    p = tmp_path / "proof.json"
    p.write_bytes((TINY / "proof.json").read_bytes())
    return p, TINY / "settings.json", TINY / "vk.key", TINY / "vk.key"


@pytest.mark.parametrize("fake,expected", [
    (FakeEzkl(result=True), Verdict.ACCEPT),
    (FakeEzkl(result=False), Verdict.REJECT),
    (FakeEzkl(result="unexpected"), Verdict.ERROR),
    (FakeEzkl(result=None), Verdict.ERROR),
    (FakeEzkl(exc=RuntimeError("Failed to run verify: [halo2] The constraint system is not satisfied")), Verdict.REJECT),
    (FakeEzkl(exc=OSError("disk full")), Verdict.ERROR),
    (FakeEzkl(exc=MemoryError()), Verdict.ERROR),
])
def test_classification(files, fake, expected):
    assert verify(fake, *files)[0] == expected


def test_missing_file_is_error(files):
    p, s, vk, srs = files
    assert verify(FakeEzkl(result=True), p.parent / "nope.json", s, vk, srs)[0] == Verdict.ERROR


def test_messages():
    assert classify_exception("Failed to run verify: [halo2] Multi-opening proof was invalid") == Verdict.REJECT
    assert classify_exception("something else broke") == Verdict.ERROR


def _write(tmp_path, obj):
    p = tmp_path / "candidate.json"
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)
    return p


def test_real_verdicts(ezkl, tiny_bundle, tiny_proof, tmp_path):
    b = tiny_bundle
    _, outs = split_instances(b.spec, tiny_proof["instances"])
    good = dict(tiny_proof, instances=build_instances(b.spec, [TINY_X], outs))
    assert verify(ezkl, _write(tmp_path, good), b.settings, b.vk, b.srs)[0] == Verdict.ACCEPT
    other = dict(tiny_proof, instances=build_instances(b.spec, [[0.6, -0.2, 0.9, 0.1]], outs))
    assert verify(ezkl, _write(tmp_path, other), b.settings, b.vk, b.srs)[0] == Verdict.REJECT
    assert verify(ezkl, _write(tmp_path, "bad\n"), b.settings, b.vk, b.srs)[0] == Verdict.REJECT


@pytest.mark.parametrize("missing", [1, 2, 3])
def test_each_missing_prerequisite_is_error(files, missing):
    args = list(files)
    args[missing] = args[0].parent / "nope"
    assert verify(FakeEzkl(result=True), *args)[0] == Verdict.ERROR


@pytest.mark.parametrize("msg", [
    "Failed to run verify: [pfsys] failed to load the proof: Permission denied (os error 13)",
    "Failed to run verify: [pfsys] failed to load the proof: expected value at line 1 column 1",
    "Failed to run verify: [pfsys] failed to load the proof: No such file or directory (os error 2)",
    "Failed to run verify: [srs] failed to load srs from /tmp/x/[halo2].srs",
    "Failed to run verify: [srs] failed to read srs failed to fill whole buffer",
    "Failed to run verify: [pfsys] failed to load vk from file: unexpected version byte",
    "Failed to run verify: [io] expected value at line 1 column 1",
    "Failed to run verify: [io] No such file or directory (os error 2)",
    "something [halo2] The constraint system is not satisfied",
    "",
])
def test_infrastructure_messages_are_error(msg):
    assert classify_exception(msg) == Verdict.ERROR


def test_verifies_a_private_copy(files):
    seen = []

    class Recorder:
        def verify(self, proof, *a, **k):
            seen.append((proof, open(proof).read()))
            return True

    assert verify(Recorder(), *files)[0] == Verdict.ACCEPT
    original = json.loads(files[0].read_text())
    assert seen[0][0] != str(files[0])
    assert json.loads(seen[0][1]) == {"instances": original["instances"], "proof": original["proof"]}


def test_oversized_proof_is_rejected_unread(files, tmp_path):
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (MAX_PROOF_BYTES + 1))
    assert verify(FakeEzkl(result=True), big, *files[1:])[0] == Verdict.REJECT


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_files_are_error(ezkl, tiny_bundle, tiny_proof, tmp_path):
    b = tiny_bundle
    p = _write(tmp_path, "bad\n")
    p.chmod(0)
    # an unreadable SRS whose name looks like a halo2 error
    srs = tmp_path / "[halo2].srs"
    srs.write_bytes(Path(b.srs).read_bytes())
    srs.chmod(0)
    proof = tmp_path / "good.json"
    proof.write_text(json.dumps(tiny_proof))
    try:
        assert verify(ezkl, p, b.settings, b.vk, b.srs)[0] == Verdict.ERROR
        assert verify(ezkl, proof, b.settings, b.vk, srs) == (
            Verdict.ERROR, f"Failed to run verify: [srs] failed to load srs from {srs}"[:200])
    finally:
        p.chmod(0o600)
        srs.chmod(0o600)


def test_real_infrastructure_errors(ezkl, tiny_bundle, tiny_proof, tmp_path):
    b = tiny_bundle
    proof = _write(tmp_path, json.dumps(tiny_proof))
    bad = tmp_path / "bad"
    bad.write_bytes(b"bad")
    assert verify(ezkl, proof, bad, b.vk, b.srs)[0] == Verdict.ERROR
    assert verify(ezkl, proof, b.settings, bad, b.srs)[0] == Verdict.ERROR
    assert verify(ezkl, proof, b.settings, b.vk, bad)[0] == Verdict.ERROR


class MustNotRun:
    def verify(self, *a, **k):
        raise AssertionError("EZKL must not see a malformed proof")


def _with(fields):
    good = json.loads((TINY / "proof.json").read_text())
    return json.dumps({**good, **fields})


@pytest.mark.parametrize("content", [
    b"bad\n", b"", b"\xff\xfe", b"[]", b"null", b"[" * 100000, b"{}",
    _with({"proof": None}).encode(),
    _with({"proof": [256]}).encode(),
    _with({"proof": [-1]}).encode(),
    _with({"proof": [True]}).encode(),
    _with({"proof": [1.0]}).encode(),
    _with({"proof": "0x00"}).encode(),
    _with({"instances": None}).encode(),
    _with({"instances": ["00" * 32]}).encode(),
    _with({"instances": [["ff" * 32]]}).encode(),
    _with({"instances": [["00" * 31]]}).encode(),
    _with({"instances": [["0X" + "0" * 62]]}).encode(),
    _with({"instances": [[0]]}).encode(),
])
def test_malformed_proof_rejected_before_ezkl(files, content):
    files[0].write_bytes(content)
    verdict, detail = verify(MustNotRun(), *files)
    assert verdict == Verdict.REJECT, detail


def test_extra_fields_are_dropped(files):
    files[0].write_text(_with({"pretty_public_inputs": "x", "hex_proof": 5, "extra": [1]}))
    seen = []

    class Recorder:
        def verify(self, proof, *a, **k):
            seen.append(json.loads(open(proof).read()))
            return True

    assert verify(Recorder(), *files)[0] == Verdict.ACCEPT
    assert set(seen[0]) == {"instances", "proof"}


def test_load_error_after_shape_check_is_error(files):
    # serde can attach a position to a real I/O error, so a position proves nothing
    msg = "Failed to run verify: [pfsys] failed to load the proof: Input/output error (os error 5) at line 1 column 14"
    assert verify(FakeEzkl(exc=RuntimeError(msg)), *files)[0] == Verdict.ERROR


class _Boom:
    def __init__(self, *a, **k):
        raise OSError(28, "No space left on device")


class _BadCleanup:
    def __init__(self, *a, **k):
        import tempfile
        self._d = tempfile.mkdtemp()

    def __enter__(self):
        return self._d

    def __exit__(self, *exc):
        import shutil
        shutil.rmtree(self._d)
        raise OSError(16, "Device or resource busy")


@pytest.mark.parametrize("make_dir", [_Boom, _BadCleanup])
def test_staging_failure_is_error(files, monkeypatch, make_dir):
    monkeypatch.setattr("pge.verdict.tempfile.TemporaryDirectory", make_dir)
    assert verify(FakeEzkl(result=True), *files)[0] == Verdict.ERROR


def test_snapshot_write_failure_is_error(files, monkeypatch):
    def fail(self, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Path, "write_bytes", fail)
    assert verify(FakeEzkl(result=True), *files)[0] == Verdict.ERROR
