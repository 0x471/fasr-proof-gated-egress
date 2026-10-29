"""Ordering channel: the real EZKL prover in order_bits mode against the endpoint on localhost, v0 vs v1.

Run from the repo root: python observer/order_experiment.py. Prints one JSON line per (k, release).
"""
import asyncio, json, random, sys, tempfile, time
from pathlib import Path
ROOT = Path.cwd()
sys.path[:0] = [str(ROOT), str(ROOT / "prover"), str(ROOT / "observer"), str(ROOT / "tests")]
import ezkl
from conftest import TINY, TINY_PINS, TINY_X
from pge.bundle import Bundle
from pge.service import Endpoint, Policy
from server import Prover, Server, bits_per_batch
from client import trace_batches, decode_order_bits

SRS = ROOT / "prover" / "artifacts" / "kzg.srs"
bundle = Bundle.load(TINY / "settings.json", TINY / "vk.key", SRS, TINY_PINS)
prover = Prover(SRS)

def verify(data, timeout):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "proof.json"; p.write_bytes(data)
        return bundle.verify(ezkl, p)

async def run(release, k, batches, bits):
    srv = Server(prover, mode="order_bits", bits=bits, batch=k)
    endpoint = Endpoint(spec=bundle.spec, verifier=verify, policy=Policy(deadline=30, release=release), log=lambda r: None)
    s1 = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
    s2 = await asyncio.start_server(endpoint.serve_client, "127.0.0.1", 0)
    link = asyncio.create_task(endpoint.link("127.0.0.1", s1.sockets[0].getsockname()[1]))
    while endpoint._server is None:
        await asyncio.sleep(0.01)
    t0 = time.monotonic()
    rows = await trace_batches("127.0.0.1", s2.sockets[0].getsockname()[1], [[TINY_X]] * (k * batches), k)
    dt = time.monotonic() - t0
    link.cancel(); s1.close(); s2.close()
    got = decode_order_bits(rows, k)
    ok = sum(r["answer"]["ok"] for r in rows)
    errs = sum(a != b for a, b in zip(got, bits))
    return dict(release=release, k=k, batches=batches, bits_sent=len(bits), bit_errors=errs,
                bits_per_batch=bits_per_batch(k), answers_ok=ok, answers=len(rows), seconds=round(dt, 1),
                decoded_constant=len(set(got)) <= 1)

rng = random.Random(20261019)
out = []
for k in (2, 3, 4):
    batches = 16
    bits = "".join(rng.choice("01") for _ in range(bits_per_batch(k) * batches))
    for release in ("immediate", "arrival"):
        r = asyncio.run(run(release, k, batches, bits))
        print(json.dumps(r), flush=True); out.append(r)
