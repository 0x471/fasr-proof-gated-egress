import hashlib
import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, TINY

SCRIPT = ROOT / "device" / "measure.sh"


def _run(*files, env=None):
    r = subprocess.run(["bash", str(SCRIPT), *map(str, files)], capture_output=True, text=True, timeout=120, env=env)
    lines = r.stdout.splitlines()
    summary = json.loads(next(l for l in lines if l.startswith("summary: "))[len("summary: "):])
    return r.returncode, lines, summary


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the device runs Linux")
def test_measure_reports_counts_and_hashes():
    rc, lines, summary = _run(TINY / "vk.key")
    assert rc == 0 and summary["valid"] is True
    assert set(summary) == {"packages", "running_services", "processes", "listening_sockets", "valid"}
    assert summary["processes"] > 0 and summary["packages"] > 0
    vk = hashlib.sha256((TINY / "vk.key").read_bytes()).hexdigest()
    assert f"sha256 {TINY / 'vk.key'}: {vk}  ({(TINY / 'vk.key').stat().st_size} bytes)" in lines


def test_missing_file_makes_the_measurement_invalid(tmp_path):
    rc, lines, summary = _run(tmp_path / "nope")
    assert rc == 1 and summary["valid"] is False and f"missing {tmp_path / 'nope'}" in lines


def test_failing_tools_are_unknown_not_zero(tmp_path):
    for tool in ("dpkg-query", "systemctl", "ps", "ss"):
        p = tmp_path / tool
        p.write_text("#!/bin/sh\nexit 1\n")
        p.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    rc, lines, summary = _run(env=env)
    assert rc == 1 and summary == {"packages": -1, "running_services": -1, "processes": -1,
                                   "listening_sockets": -1, "valid": False}
