"""Run device/check_verifier.sh against a stub CLI that prints EZKL 23.0.5's log format."""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import time

import pytest

from conftest import ROOT, TINY

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not all(map(shutil.which, ("timeout", "sha256sum", "python3"))),
    reason="needs Linux with GNU coreutils and python3, like the device",
)

SCRIPT = ROOT / "device" / "check_verifier.sh"
TS = "[2026-10-14 12:00:00:000"
STUB = rf"""#!/usr/bin/env bash
if [ "$1" = --version ]; then
  [ "$STUB" = hang_version ] && exec sleep 60
  echo "ezkl ${{STUB_VERSION:-23.0.5}}"; exit 0
fi
proof=""
while [ $# -gt 0 ]; do [ "$1" = --proof-path ] && proof="$2"; shift; done
good=0; cmp -s "$proof" proof.json && good=1
case "$STUB" in accept_all) good=1;; reject_all) good=0;; esac
if [ $good = 1 ]; then
  echo "[*] {TS}, ezkl::execute] - verified: true"; echo "[*] {TS}, ezkl] - succeeded"; exit 0
fi
case "$STUB" in
  ignore_term) [ "$proof" = tampered_output.json ] && {{ trap '' TERM; sleep 60; }};;
  segv) kill -SEGV $$;;
  perm) echo "[E] {TS}, ezkl] - [pfsys] failed to load the proof: Permission denied (os error 13)"; exit 1;;
  srs_path) echo "[E] {TS}, ezkl] - [srs] failed to load srs from /x/[halo2].srs"; exit 1;;
  halo2_unverified) echo "[E] {TS}, ezkl] - [halo2] The constraint system is not satisfied"; exit 1;;
  two_errors) echo "[*] {TS}, ezkl::execute] - verified: false"
    echo "[E] {TS}, ezkl] - [halo2] The constraint system is not satisfied"
    echo "[E] {TS}, ezkl] - [srs] something"; exit 1;;
  mutate) echo x >> vk.key;;
esac
if [ "$proof" = garbage.json ]; then
  echo "[E] {TS}, ezkl] - [pfsys] failed to load the proof: expected value at line 1 column 1"; exit 1
fi
echo "[*] {TS}, ezkl::execute] - verified: false"
echo "[E] {TS}, ezkl] - [halo2] The constraint system is not satisfied"
exit 1
"""


def _archive():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = STUB.encode()
        info = tarfile.TarInfo("ezkl")
        info.size, info.mode = len(data), 0o755
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture()
def device(tmp_path):
    home, run = tmp_path / "home", tmp_path / "run"
    (home / "base" / "cache").mkdir(parents=True)
    run.mkdir()
    for name in ("proof.json", "settings.json", "vk.key"):
        shutil.copy(TINY / name, run / name)
    (run / "kzg.srs").write_bytes(b"stand-in srs")
    archive = _archive()
    (home / "base" / "cache" / "ezkl.tgz").write_bytes(archive)
    pins = "\n".join(f"{hashlib.sha256((run / n).read_bytes()).hexdigest()}  {n}"
                     for n in ("kzg.srs", "vk.key", "settings.json"))

    def go(stub="", sha=None, tmo=2, **env):
        args = [str(run), "base", "23.0.5", "ezkl.tgz", sha or hashlib.sha256(archive).hexdigest(), "2", str(tmo), pins]
        e = dict(os.environ, HOME=str(home), STUB=stub, **env)
        start = time.monotonic()
        r = subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr, time.monotonic() - start

    go.run = run
    return go


def test_correct_cli_passes(device):
    rc, out, _ = device()
    assert rc == 0 and "all checks passed" in out, out
    assert len((device.run / "timings_ms.txt").read_text().split()) == 2


@pytest.mark.parametrize("stub", ["accept_all", "reject_all", "segv", "perm", "srs_path", "halo2_unverified",
                                  "two_errors", "mutate"])
def test_wrong_behaviour_fails(device, stub):
    rc, out, _ = device(stub)
    assert rc == 1 and "all checks passed" not in out, out


def test_misleading_diagnostics_are_errors(device):
    for stub in ("perm", "srs_path", "halo2_unverified"):
        rc, out, _ = device(stub)
        assert "FAIL  tampered output: got ERROR(rc=1), want REJECT" in out, out


def test_sigterm_is_followed_by_kill(device):
    rc, out, took = device("ignore_term", tmo=1)
    assert rc == 1 and "ERROR(rc=137)" in out, out
    assert took < 20


def test_hanging_version_is_bounded(device):
    rc, out, took = device("hang_version", tmo=1)
    assert rc == 1 and "ezkl --version failed" in out, out
    assert took < 15


def test_wrong_version_fails(device):
    rc, out, _ = device(STUB_VERSION="23.0.4")
    assert rc == 1 and "expected 23.0.5" in out, out


def test_wrong_archive_digest_fails_before_extracting(device):
    rc, out, _ = device(sha="00" * 32)
    assert rc == 1 and not (device.run / "ezkl").exists(), out
