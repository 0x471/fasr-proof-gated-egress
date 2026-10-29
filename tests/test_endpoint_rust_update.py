"""Signed updates through the Rust binary: every refusal rule, and an endpoint that runs from the store."""
import asyncio
import hashlib
import json
import subprocess

import pytest

from conftest import TINY, TINY_X
from pge.protocol import Wire, frame, read_frame
from test_endpoint_rust import free_port, rust_bin  # noqa: F401
from test_service import FakeProver
from prover.server import Server

FILES = ("settings.json", "vk.key", "kzg.srs", "domain.json")


@pytest.fixture()
def signer(rust_bin):  # noqa: F811
    path = rust_bin.with_name("pge-sign")
    if not path.is_file():
        pytest.fail(f"missing pge-sign next to {rust_bin}")
    return path


class Keys:
    def __init__(self, signer, work):
        self.signer, self.work = signer, work
        work.mkdir(parents=True, exist_ok=True)

    def secret(self, n):
        path = self.work / f"secret{n}"
        path.write_text(f"{n:02x}" * 32 + "\n")
        return path

    def public(self, n):
        return subprocess.run([self.signer, "--public", self.secret(n)], capture_output=True, check=True,
                              text=True).stdout.strip()

    def sign(self, n, data):
        return subprocess.run([self.signer, self.secret(n)], input=data, capture_output=True, check=True).stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_update(keys, out, version, *, device="pi-1", key="ops-1", signer=2, keys_version=1, listed=(("ops-1", 2),),
                root=1, files=None):
    out.mkdir(parents=True)
    raw = files or {name: (TINY / name).read_bytes() for name in ("settings.json", "vk.key", "domain.json")}
    for name in FILES:
        (out / name).write_bytes(raw[name])
    key_list = f"pge-keys 1\nversion {keys_version}\n" + "".join(f"key {i} {keys.public(n)}\n" for i, n in listed)
    manifest = f"pge-update 1\ndevice {device}\nversion {version}\n" + "".join(
        f"{name} {sha(raw[name])}\n" for name in FILES) + f"key {key}\n"
    (out / "keys").write_text(key_list)
    (out / "keys.sig").write_bytes(keys.sign(root, key_list.encode()))
    (out / "manifest").write_text(manifest)
    (out / "manifest.sig").write_bytes(keys.sign(signer, manifest.encode()))
    return out


@pytest.fixture()
def env(signer, tiny_bundle, tmp_path):
    keys = Keys(signer, tmp_path / "keys")
    root = tmp_path / "root.pub"
    root.write_text(keys.public(1) + "\n")
    store = tmp_path / "store"
    store.mkdir()
    files = {name: (TINY / name).read_bytes() for name in ("settings.json", "vk.key", "domain.json")}
    files["kzg.srs"] = tiny_bundle.srs.read_bytes()
    return {"keys": keys, "root": root, "store": store, "files": files, "tmp": tmp_path}


def apply(binary, env, update, device="pi-1"):
    r = subprocess.run([binary, "--apply-update", update, "--root", env["root"], "--device", device,
                        "--store", env["store"]], capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout.strip()


def update(env, name, version, **kw):
    return make_update(env["keys"], env["tmp"] / name, version, files=kw.pop("files", env["files"]), **kw)


def test_a_valid_update_applies_once(rust_bin, env):  # noqa: F811
    first = update(env, "u1", 1)
    assert apply(rust_bin, env, first) == (0, "applied 1")
    assert apply(rust_bin, env, first) == (0, "same")
    state = (env["store"] / "state").read_text()
    assert state == f"pge-state 1\nversion 1\nkeys 1\ndigest {sha((first / 'manifest').read_bytes())}\n"
    assert sorted(p.name for p in (env["store"] / "active").iterdir()) == sorted(
        FILES + ("keys", "keys.sig", "manifest", "manifest.sig"))
    assert apply(rust_bin, env, update(env, "u2", 2)) == (0, "applied 2")


def test_rollback_and_replay_are_refused(rust_bin, env):  # noqa: F811
    assert apply(rust_bin, env, update(env, "u2", 2)) == (0, "applied 2")
    assert apply(rust_bin, env, update(env, "u1", 1)) == (1, "refused rollback")
    other = dict(env["files"], **{"domain.json": b'{"other": true}'})
    assert apply(rust_bin, env, update(env, "u2b", 2, files=other)) == (1, "refused replay")
    assert apply(rust_bin, env, update(env, "k0", 3, keys_version=1)) == (0, "applied 3")


def test_an_older_key_list_is_refused(rust_bin, env):  # noqa: F811
    assert apply(rust_bin, env, update(env, "u1", 1, keys_version=2)) == (0, "applied 1")
    assert apply(rust_bin, env, update(env, "u2", 2, keys_version=1)) == (1, "refused rollback")


@pytest.mark.parametrize("change,word", [
    ({"device": "pi-2"}, "device"),
    ({"key": "ops-2", "signer": 3}, "key"),
    ({"listed": (("ops-2", 3),), "key": "ops-2", "signer": 2}, "signature"),
    ({"root": 2}, "signature"),
    ({"signer": 3}, "signature"),
])
def test_key_and_device_confusion_are_refused(rust_bin, env, change, word):  # noqa: F811
    assert apply(rust_bin, env, update(env, "u", 1, **change)) == (1, f"refused {word}")
    assert not (env["store"] / "state").exists()


@pytest.mark.parametrize("name", FILES)
def test_a_substituted_file_is_refused(rust_bin, env, name):  # noqa: F811
    u = update(env, "u", 1)
    (u / name).write_bytes((u / name).read_bytes() + b" ")
    assert apply(rust_bin, env, u) == (1, "refused bundle")


def test_an_edited_manifest_is_refused(rust_bin, env):  # noqa: F811
    u = update(env, "u", 1)
    (u / "manifest").write_text((u / "manifest").read_text().replace("version 1", "version 9"))
    assert apply(rust_bin, env, u) == (1, "refused signature")


def run_endpoint(binary, env, tiny_bundle, work):
    async def go():
        connected = asyncio.Event()
        server = Server(FakeProver(tiny_bundle.spec))

        async def handle(reader, writer):
            connected.set()
            await server.handle(reader, writer)

        upstream = await asyncio.start_server(handle, "127.0.0.1", 0)
        sport, port = upstream.sockets[0].getsockname()[1], free_port()
        log = work / "endpoint.log"
        proc = await asyncio.create_subprocess_exec(
            binary, "--listen", f"127.0.0.1:{port}", "--server", f"127.0.0.1:{sport}", "--store", env["store"],
            "--root", env["root"], "--device", "pi-1", "--proof-bytes", "3072", "--deadline", "5", "--log", log,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.wait_for(connected.wait(), 10)
            await asyncio.sleep(0.5)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(frame(Wire.for_spec(tiny_bundle.spec).encode_request(7, [TINY_X])))
            await writer.drain()
            reply = Wire.for_spec(tiny_bundle.spec).parse_reply(await asyncio.wait_for(read_frame(reader, 65536), 10))
            writer.close()
        finally:
            proc.terminate()
            await proc.wait()
            upstream.close()
        return reply, [json.loads(line) for line in log.read_text().splitlines()]
    return asyncio.run(go())


def test_the_endpoint_runs_from_the_store(rust_bin, env, tiny_bundle, tmp_path):  # noqa: F811
    assert apply(rust_bin, env, update(env, "u1", 1)) == (0, "applied 1")
    reply, records = run_endpoint(rust_bin, env, tiny_bundle, tmp_path)
    assert reply["ok"]
    assert records[0] == {"result": "update", "version": 1}
    assert records[1]["result"] == "accept"


def start_fails(binary, env):
    r = subprocess.run([binary, "--listen", f"127.0.0.1:{free_port()}", "--server", "127.0.0.1:9", "--store",
                        env["store"], "--root", env["root"], "--device", "pi-1", "--proof-bytes", "3072"],
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr.strip()


def test_the_endpoint_refuses_a_changed_store(rust_bin, env):  # noqa: F811
    assert start_fails(rust_bin, env) == (2, "endpoint startup/runtime error: no update applied")
    assert apply(rust_bin, env, update(env, "u1", 1)) == (0, "applied 1")
    vk = env["store"] / "active" / "vk.key"
    original = vk.read_bytes()
    vk.write_bytes(original + b" ")
    assert start_fails(rust_bin, env) == (2, "endpoint startup/runtime error: store refused: bundle")
    vk.write_bytes(original)
    state = env["store"] / "state"
    state.write_text(state.read_text().replace("version 1", "version 2"))
    assert start_fails(rust_bin, env) == (2, "endpoint startup/runtime error: store refused: state")


def test_store_and_pinned_flags_do_not_mix(rust_bin, env):  # noqa: F811
    r = subprocess.run([rust_bin, "--listen", "127.0.0.1:1", "--server", "127.0.0.1:2", "--store", env["store"],
                        "--root", env["root"], "--device", "pi-1", "--pins", "x", "--proof-bytes", "3072"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "--store excludes" in r.stderr
