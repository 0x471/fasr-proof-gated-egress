import asyncio
import hashlib
import inspect
import json
import os
import shutil
from pathlib import Path

import pytest

from pge.bundle import Bundle, Pins

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
TINY = FIX / "tiny"
MULTI = FIX / "multi"

SRS_SHA256 = "90807800a1c3b248a452e1732c45ee5099f38b737356f5542c0584ec9c3ebb45"
TINY_PINS = Pins(
    settings="b4a95fc7c572816034708c4e5c079f0f91f82eb9681cfa069565aacc9887aefd",
    vk="34ab48a8ac0946be4faf10d85699ac729adb20ec4c6251da1d1d0d4ea18a4d26",
    srs=SRS_SHA256,
)
MULTI_PINS = Pins(
    settings="89f83ab3aace396b42aced55d3b482d33cb8a196f2d00259ddfa20fea92a3121",
    vk="0bd4cec6c17051d478ac3cfd3bff3ca5f4d98184673c99949bdb838b208411c7",
    srs=SRS_SHA256,
)
TINY_X = [0.5, -0.2, 0.9, 0.1]
MULTI_X = [[0.25, -0.5], [-0.75, 0.125]]

# CI sets this so a missing EZKL or SRS fails the run instead of skipping the real tests.
REQUIRE = os.environ.get("PGE_REQUIRE_INTEGRATION") == "1"


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(fn, *a, **k):
    async def _c():
        r = fn(*a, **k)
        return await r if inspect.isawaitable(r) else r
    return asyncio.run(_c())


def _unavailable(reason: str):
    if REQUIRE:
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def ezkl():
    try:
        import ezkl as mod
    except ImportError:
        _unavailable("ezkl not installed")
    if mod.__version__ != "23.0.5":
        pytest.fail(f"tests are pinned to ezkl 23.0.5, found {mod.__version__}")
    return mod


@pytest.fixture(scope="session")
def srs_path(ezkl, tmp_path_factory):
    for c in (os.environ.get("PGE_SRS"), ROOT / "prover" / "artifacts" / "kzg.srs"):
        if c and Path(c).is_file() and sha256(Path(c)) == SRS_SHA256:
            return Path(c)
    dest = tmp_path_factory.mktemp("srs") / "kzg.srs"
    try:
        ok = run(ezkl.get_srs, settings_path=str(TINY / "settings.json"), srs_path=str(dest))
    except Exception as e:
        _unavailable(f"SRS not available and download failed: {e}")
    if ok is not True or sha256(dest) != SRS_SHA256:
        pytest.fail("downloaded SRS does not match the pin")
    return dest


@pytest.fixture(scope="session")
def tiny_proof():
    return json.loads((TINY / "proof.json").read_text())


@pytest.fixture(scope="session")
def multi_proof():
    return json.loads((MULTI / "proof.json").read_text())


@pytest.fixture()
def tiny_bundle(srs_path):
    return Bundle.load(TINY / "settings.json", TINY / "vk.key", srs_path, TINY_PINS)


@pytest.fixture()
def multi_bundle(srs_path):
    return Bundle.load(MULTI / "settings.json", MULTI / "vk.key", srs_path, MULTI_PINS)


@pytest.fixture()
def workdir(tmp_path):
    for d in ("tiny", "multi"):
        (tmp_path / d).mkdir()
        for name in ("settings.json", "vk.key", "network.ezkl", "proof.json"):
            shutil.copy(FIX / d / name, tmp_path / d / name)
    return tmp_path
