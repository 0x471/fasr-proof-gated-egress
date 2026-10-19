"""verify_with_cli against stub CLIs that print EZKL 23.0.5's log format, and against the real CLI."""
import ast
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

from conftest import REQUIRE, TINY, TINY_PINS
from pge.bundle import Bundle, Pins
import pge.cli as cli_module
from pge.cli import MAX_OUTPUT_BYTES, classify_cli, verify_with_cli
from pge.proc import memory_limit_supported
from pge.verdict import Verdict, canonical_proof

TS = "[2026-10-16 12:00:00:000"
OK = f"[*] {TS}, ezkl::execute] - verified: true\n[*] {TS}, ezkl] - succeeded\n"
NO = f"[*] {TS}, ezkl::execute] - verified: false\n[E] {TS}, ezkl] - [halo2] The constraint system is not satisfied\n"

STUB = """import os, signal, subprocess, sys, time
mode, good, marker = {mode!r}, {good!r}, {marker!r}
open(marker, "w").write(repr(sys.argv[1:]) + "\\n" + repr(sorted(os.environ)))
args = sys.argv[1:]
proof = open(args[args.index("--proof-path") + 1], "rb").read()
ok = proof == good
w = sys.stdout.write
ts = {ts!r}
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)
if mode == "grandchild":
    c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(marker + ".pid", "w").write(str(c.pid)); sys.stdout.flush(); time.sleep(60)
if mode == "killed":
    w({ok_text!r}); sys.stdout.flush(); os.kill(os.getpid(), signal.SIGKILL)
if mode == "huge":
    w({ok_text!r}); w(("x" * 99 + "\\n") * ({huge} // 100 + 1)); sys.exit(0)
if mode == "write_file":
    os.write(os.open(marker + ".w", os.O_WRONLY | os.O_CREAT), b"x"); w({ok_text!r}); sys.exit(0)
if mode == "rc0_error":
    w({ok_text!r}); w("[E] " + ts + ", ezkl] - [srs] something\\n"); sys.exit(0)
if mode == "binary":
    sys.stdout.flush(); os.write(1, b"\\xff\\xfe\\n"); w({ok_text!r}); sys.exit(0)
if mode == "env" and "RUST_LOG" in os.environ:
    w("[*] " + ts + ", ezkl::execute] - verified: false\\n"); sys.exit(0)
if mode == "rc1_verified":
    w("[*] " + ts + ", ezkl::execute] - verified: true\\n"); sys.exit(1)
if mode == "rc0_silent":
    w("[*] " + ts + ", ezkl] - succeeded\\n"); sys.exit(0)
if mode == "twice":
    w({ok_text!r}); w({ok_text!r}); sys.exit(0)
if mode == "true_and_false":
    w({ok_text!r}); w("[*] " + ts + ", ezkl::execute] - verified: false\\n"); sys.exit(0)
if mode == "accept_all" or ok:
    w({ok_text!r}); sys.exit(0)
if mode == "perm":
    w("[E] " + ts + ", ezkl] - [pfsys] failed to load the proof: Permission denied (os error 13)\\n"); sys.exit(1)
if mode == "eio":
    w("[E] " + ts + ", ezkl] - [pfsys] failed to load the proof: Input/output error (os error 5) at line 1 column 14\\n")
    sys.exit(1)
if mode == "srs_path":
    w("[E] " + ts + ", ezkl] - [srs] failed to load srs from /x/[halo2].srs\\n"); sys.exit(1)
if mode == "halo2_unverified":
    w("[E] " + ts + ", ezkl] - [halo2] The constraint system is not satisfied\\n"); sys.exit(1)
if mode == "two_errors":
    w({no_text!r}); w("[E] " + ts + ", ezkl] - [srs] something\\n"); sys.exit(1)
if mode == "reject_rc2":
    w({no_text!r}); sys.exit(2)
w({no_text!r}); sys.exit(1)
"""


@pytest.fixture()
def bundle(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "settings.json").write_bytes((TINY / "settings.json").read_bytes())
    (src / "vk.key").write_bytes((TINY / "vk.key").read_bytes())
    (src / "kzg.srs").write_bytes(b"stand-in srs")
    pins = Pins(settings=TINY_PINS.settings, vk=TINY_PINS.vk,
                srs=hashlib.sha256(b"stand-in srs").hexdigest())
    return Bundle.load(src / "settings.json", src / "vk.key", src / "kzg.srs", pins)


@pytest.fixture()
def stub(tmp_path):
    good = canonical_proof((TINY / "proof.json").read_bytes())

    def make(mode=""):
        path = tmp_path / f"ezkl_{mode or 'ok'}"
        marker = tmp_path / f"ran_{mode or 'ok'}"
        path.write_text(f"#!{sys.executable}\n" + STUB.format(
            mode=mode, good=good, marker=str(marker), ts=TS, ok_text=OK, no_text=NO, huge=70000))
        path.chmod(0o755)
        make.marker = marker
        return path

    return make


def tampered(tmp_path, tiny_proof, idx=-3):
    p = json.loads(json.dumps(tiny_proof))
    p["instances"][0][idx] = p["instances"][0][1]
    out = tmp_path / "tampered.json"
    out.write_text(json.dumps(p))
    return out


def test_stub_accepts_good_and_rejects_tampered(bundle, stub, tmp_path, tiny_proof):
    cli = stub()
    assert verify_with_cli(bundle, TINY / "proof.json", cli, 30) == (Verdict.ACCEPT, "")
    v, detail = verify_with_cli(bundle, tampered(tmp_path, tiny_proof), cli, 30)
    assert v == Verdict.REJECT and detail.startswith("[halo2] ")


def test_cli_gets_the_bundle_copies_and_a_canonical_proof(bundle, stub, monkeypatch):
    monkeypatch.setattr(cli_module, "CLI_FILE_SIZE", None)
    cli = stub()
    verify_with_cli(bundle, TINY / "proof.json", cli, 30)
    args = ast.literal_eval(stub.marker.read_text().splitlines()[0])
    assert args[0] == "verify"
    assert args[args.index("--settings-path") + 1] == str(bundle.settings)
    assert args[args.index("--vk-path") + 1] == str(bundle.vk)
    assert args[args.index("--srs-path") + 1] == str(bundle.srs)
    assert args[args.index("--proof-path") + 1] != str(TINY / "proof.json")


def test_cli_runs_with_a_minimal_environment(bundle, stub, monkeypatch):
    monkeypatch.setattr(cli_module, "CLI_FILE_SIZE", None)
    monkeypatch.setenv("RUST_LOG", "debug")
    assert verify_with_cli(bundle, TINY / "proof.json", stub("env"), 30) == (Verdict.ACCEPT, "")
    names = set(ast.literal_eval(stub.marker.read_text().splitlines()[1]))
    assert names <= {"PATH", "LC_ALL", "HOME", "LC_CTYPE", "__CF_USER_TEXT_ENCODING", "RAYON_NUM_THREADS"}
    assert "RAYON_NUM_THREADS" in names


@pytest.mark.parametrize("mode", ["accept_all", "perm", "eio", "srs_path", "halo2_unverified", "two_errors",
                                  "reject_rc2", "rc1_verified", "rc0_silent", "twice", "true_and_false",
                                  "killed", "huge", "binary", "rc0_error"])
def test_wrong_cli_behaviour_never_accepts_or_rejects_wrongly(bundle, stub, tmp_path, tiny_proof, mode):
    cli = stub(mode)
    good = verify_with_cli(bundle, TINY / "proof.json", cli, 30)[0]
    bad = verify_with_cli(bundle, tampered(tmp_path, tiny_proof), cli, 30)[0]
    if mode == "accept_all":
        assert bad == Verdict.ACCEPT  # the stub lies; only pinning the binary helps
    elif mode in ("perm", "eio", "srs_path", "halo2_unverified", "two_errors", "reject_rc2"):
        assert good == Verdict.ACCEPT and bad == Verdict.ERROR
    else:
        assert good == Verdict.ERROR and bad != Verdict.ACCEPT


def test_misleading_load_errors_are_not_rejections():
    for line in ("[pfsys] failed to load the proof: Input/output error (os error 5) at line 1 column 14",
                 "[srs] failed to load srs from /x/[halo2].srs"):
        out = f"[*] {TS}, ezkl::execute] - verified: false\n[E] {TS}, ezkl] - {line}\n".encode()
        assert classify_cli(1, out)[0] == Verdict.ERROR


def test_output_limit_is_64_kib():
    assert MAX_OUTPUT_BYTES == 65536


def test_negative_exit_code_is_error():
    assert classify_cli(-9, OK.encode())[0] == Verdict.ERROR
    assert classify_cli(-9, NO.encode())[0] == Verdict.ERROR


def test_deadline_kills_a_cli_that_ignores_sigterm(bundle, stub):
    start = time.monotonic()
    v, detail = verify_with_cli(bundle, TINY / "proof.json", stub("ignore_term"), 1)
    assert v == Verdict.ERROR and "timed out" in detail
    assert time.monotonic() - start < 10


def test_deadline_kills_the_whole_process_group(bundle, stub, monkeypatch):
    monkeypatch.setattr(cli_module, "CLI_FILE_SIZE", None)
    cli = stub("grandchild")
    v, _ = verify_with_cli(bundle, TINY / "proof.json", cli, 2)
    assert v == Verdict.ERROR
    pid = int(Path(str(stub.marker) + ".pid").read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"grandchild {pid} survived the deadline")


def test_malformed_proofs_never_reach_the_cli(bundle, stub, tmp_path):
    cli = stub("accept_all")
    garbage = tmp_path / "garbage.json"
    garbage.write_text("bad\n")
    big = tmp_path / "big.json"
    big.write_bytes(b" " * ((1 << 20) + 1))
    for p in (garbage, big):
        assert verify_with_cli(bundle, p, cli, 30)[0] == Verdict.REJECT
    assert not stub.marker.exists()


def test_missing_proof_is_error(bundle, stub, tmp_path):
    assert verify_with_cli(bundle, tmp_path / "nope.json", stub(), 30)[0] == Verdict.ERROR


def test_changed_bundle_is_error_before_running(bundle, stub):
    cli = stub("accept_all")
    bundle.vk.chmod(0o600)
    bundle.vk.write_bytes(b"changed")
    v, detail = verify_with_cli(bundle, TINY / "proof.json", cli, 30)
    assert v == Verdict.ERROR and detail.startswith("bundle check failed")
    assert not stub.marker.exists()


def test_unstartable_cli_is_error(bundle, tmp_path):
    assert verify_with_cli(bundle, TINY / "proof.json", tmp_path / "nope", 30)[0] == Verdict.ERROR


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "5", None])
def test_bad_timeout_is_error(bundle, stub, timeout):
    v, detail = verify_with_cli(bundle, TINY / "proof.json", stub(), timeout)
    assert v == Verdict.ERROR and detail.startswith("bad timeout")


@pytest.fixture(scope="session")
def real_cli():
    path = os.environ.get("PGE_EZKL_CLI")
    if not path or not Path(path).is_file():
        if REQUIRE:
            pytest.fail("PGE_EZKL_CLI not set")
        pytest.skip("set PGE_EZKL_CLI to EZKL's 23.0.5 CLI binary")
    return Path(path)


def test_real_cli(real_cli, tiny_bundle, tiny_proof, tmp_path):
    assert verify_with_cli(tiny_bundle, TINY / "proof.json", real_cli, 60) == (Verdict.ACCEPT, "")
    for idx in (-3, 0):
        v, detail = verify_with_cli(tiny_bundle, tampered(tmp_path, tiny_proof, idx), real_cli, 60)
        assert v == Verdict.REJECT and detail.startswith("[halo2] "), detail
    garbage = tmp_path / "garbage.json"
    garbage.write_text("bad\n")
    assert verify_with_cli(tiny_bundle, garbage, real_cli, 60)[0] == Verdict.REJECT


def test_a_cli_that_writes_a_file_is_stopped(bundle, stub):
    assert verify_with_cli(bundle, TINY / "proof.json", stub("write_file"), 30)[0] == Verdict.ERROR


def test_cli_limits():
    lim = cli_module.cli_limits(2.5)
    assert (lim.cpu, lim.file_size, lim.open_files) == (3, 0, 64)
    assert lim.memory == (256 << 20 if memory_limit_supported() else None)
    assert cli_module._env()["RAYON_NUM_THREADS"] == "1"
