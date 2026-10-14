import json

import pytest

from conftest import TINY, TINY_X
from pge.binding import build_instances, split_instances
from pge.verdict import Verdict, classify_exception, verify


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
    p.write_text("{}")
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
    assert classify_exception("Failed to run verify: [pfsys] failed to load the proof: expected value") == Verdict.REJECT
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
