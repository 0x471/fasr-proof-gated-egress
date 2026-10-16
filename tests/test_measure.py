import hashlib
import json
import subprocess
import sys

import pytest

from conftest import ROOT, TINY

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the device runs Linux")


def test_measure_reports_counts_and_hashes(tmp_path):
    r = subprocess.run(["bash", str(ROOT / "device" / "measure.sh"), str(TINY / "vk.key"), str(tmp_path / "nope")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    summary = json.loads(next(l for l in lines if l.startswith("summary: "))[len("summary: "):])
    assert set(summary) == {"packages", "running_services", "processes", "listening_sockets"}
    assert summary["processes"] > 0 and summary["packages"] > 0
    vk = hashlib.sha256((TINY / "vk.key").read_bytes()).hexdigest()
    assert f"sha256 {TINY / 'vk.key'}: {vk}  ({(TINY / 'vk.key').stat().st_size} bytes)" in lines
    assert f"missing {tmp_path / 'nope'}" in lines
