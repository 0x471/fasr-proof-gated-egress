import json
import stat

import pytest

from conftest import TINY, TINY_PINS, TINY_X
from pge.binding import BindingError, build_instances, split_instances
from pge.bundle import Bundle, Pins
from pge.verdict import Verdict


def _last_char_changed(h):
    return h[:-1] + ("0" if h[-1] != "0" else "1")


@pytest.mark.parametrize("field", ["settings", "vk", "srs"])
def test_pin_differing_only_at_the_end(srs_path, field):
    pins = Pins(**{**TINY_PINS.__dict__, field: _last_char_changed(getattr(TINY_PINS, field))})
    with pytest.raises(BindingError):
        Bundle.load(TINY / "settings.json", TINY / "vk.key", srs_path, pins)


def _good(bundle, tiny_proof, tmp_path):
    _, outs = split_instances(bundle.spec, tiny_proof["instances"])
    p = tmp_path / "candidate.json"
    p.write_text(json.dumps(dict(tiny_proof, instances=build_instances(bundle.spec, [TINY_X], outs))))
    return p


def test_copies_are_private_and_read_only(tiny_bundle):
    for p in (tiny_bundle.settings, tiny_bundle.vk, tiny_bundle.srs):
        assert stat.S_IMODE(p.stat().st_mode) == 0o400
        assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700


def test_later_change_to_source_files_has_no_effect(ezkl, workdir, srs_path, tiny_proof, tmp_path):
    d = workdir / "tiny"
    srs = tmp_path / "kzg.srs"
    srs.write_bytes(srs_path.read_bytes())
    b = Bundle.load(d / "settings.json", d / "vk.key", srs, TINY_PINS)
    for p in (d / "settings.json", d / "vk.key", srs):
        p.write_bytes(b"changed")
    assert b.verify(ezkl, _good(b, tiny_proof, tmp_path))[0] == Verdict.ACCEPT


@pytest.mark.parametrize("field", ["settings", "vk", "srs"])
def test_changed_copy_is_error_not_reject(ezkl, tiny_bundle, tiny_proof, tmp_path, field):
    b = tiny_bundle
    p = getattr(b, field)
    data = bytearray(p.read_bytes())
    data[-1] ^= 1
    p.chmod(0o600)
    p.write_bytes(bytes(data))
    verdict, detail = b.verify(ezkl, _good(b, tiny_proof, tmp_path))
    assert verdict == Verdict.ERROR and detail.startswith("bundle check failed")
