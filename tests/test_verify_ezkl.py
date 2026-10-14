import json

import pytest

from conftest import MULTI_X, TINY, TINY_X, run
from pge.binding import BindingError, CircuitSpec, build_instances, decode_outputs, split_instances
from pge.bundle import Bundle, Pins
from pge.verdict import Verdict, verify


def _verify(ezkl, bundle, proof, tmp_path):
    p = tmp_path / "candidate.json"
    p.write_text(json.dumps(proof))
    return bundle.verify(ezkl, p)[0]


def test_tiny_accepts_matching_request(ezkl, tiny_bundle, tiny_proof, tmp_path):
    _, outs = split_instances(tiny_bundle.spec, tiny_proof["instances"])
    cand = dict(tiny_proof, instances=build_instances(tiny_bundle.spec, [TINY_X], outs))
    assert _verify(ezkl, tiny_bundle, cand, tmp_path) == Verdict.ACCEPT


def test_tiny_rejects_other_encoded_request(ezkl, tiny_bundle, tiny_proof, tmp_path):
    _, outs = split_instances(tiny_bundle.spec, tiny_proof["instances"])
    cand = dict(tiny_proof, instances=build_instances(tiny_bundle.spec, [[0.6, -0.2, 0.9, 0.1]], outs))
    assert _verify(ezkl, tiny_bundle, cand, tmp_path) == Verdict.REJECT


def test_tiny_accepts_same_encoding_class(ezkl, tiny_bundle, tiny_proof, tmp_path):
    # 0.50001 quantizes like 0.5, so the proof also covers it; binding is to the encoded request.
    _, outs = split_instances(tiny_bundle.spec, tiny_proof["instances"])
    cand = dict(tiny_proof, instances=build_instances(tiny_bundle.spec, [[0.50001, -0.2, 0.9, 0.1]], outs))
    assert _verify(ezkl, tiny_bundle, cand, tmp_path) == Verdict.ACCEPT


def test_tiny_rejects_swapped_outputs(ezkl, tiny_bundle, tiny_proof, tmp_path):
    _, outs = split_instances(tiny_bundle.spec, tiny_proof["instances"])
    forged = [[outs[0][1], outs[0][0], outs[0][2]]]
    cand = dict(tiny_proof, instances=build_instances(tiny_bundle.spec, [TINY_X], forged))
    assert _verify(ezkl, tiny_bundle, cand, tmp_path) == Verdict.REJECT


def test_multi_accepts_and_rejects(ezkl, multi_bundle, multi_proof, tmp_path):
    spec = multi_bundle.spec
    _, outs = split_instances(spec, multi_proof["instances"])
    good = dict(multi_proof, instances=build_instances(spec, MULTI_X, outs))
    assert _verify(ezkl, multi_bundle, good, tmp_path) == Verdict.ACCEPT
    swapped = dict(multi_proof, instances=build_instances(spec, MULTI_X[::-1], outs))
    assert _verify(ezkl, multi_bundle, swapped, tmp_path) == Verdict.REJECT


def test_pretty_fields_not_authenticated(ezkl, tiny_bundle, tiny_proof, tmp_path):
    # EZKL itself ignores these, so the device drops them and never reads or releases them.
    cand = json.loads(json.dumps(tiny_proof))
    cand["pretty_public_inputs"]["rescaled_outputs"] = [["999"]]
    cand["timestamp"] = 0
    cand["hex_proof"] = cand["hex_proof"][:-2] + ("00" if cand["hex_proof"][-2:] != "00" else "01")
    p = tmp_path / "candidate.json"
    p.write_text(json.dumps(cand))
    b = tiny_bundle
    assert run(ezkl.verify, str(p), str(b.settings), str(b.vk), srs_path=str(b.srs)) is True


def test_bundle_pins_enforced(srs_path):
    from conftest import TINY, TINY_PINS
    for bad in (Pins("00" * 32, TINY_PINS.vk, TINY_PINS.srs),
                Pins(TINY_PINS.settings, "00" * 32, TINY_PINS.srs),
                Pins(TINY_PINS.settings, TINY_PINS.vk, "00" * 32)):
        with pytest.raises(BindingError):
            Bundle.load(TINY / "settings.json", TINY / "vk.key", srs_path, bad)


def test_scale_only_settings_change_still_verifies(ezkl, tiny_bundle, tmp_path):
    # Why settings are pinned: the same proof verifies under these settings, but decodes differently.
    s = json.loads(tiny_bundle.settings.read_text())
    s["model_output_scales"] = [13]
    forged = tmp_path / "settings.json"
    forged.write_text(json.dumps(s))
    assert verify(ezkl, TINY / "proof.json", forged, tiny_bundle.vk, tiny_bundle.srs)[0] == Verdict.ACCEPT
    _, outs = split_instances(tiny_bundle.spec, json.loads((TINY / "proof.json").read_text())["instances"])
    assert decode_outputs(CircuitSpec.parse(s, "forged"), outs) != decode_outputs(tiny_bundle.spec, outs)
