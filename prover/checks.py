"""Check that the proof in prover/artifacts verifies and that tampering x, y or the proof is rejected.

Exits non-zero unless the original is ACCEPT and every tampered copy is REJECT.
An ERROR (missing file, crash, unexpected result) is a failure, not a rejection.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ezkl  # noqa: E402

from pge.verdict import Verdict, verify  # noqa: E402

A = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
P = lambda n: os.path.join(A, n)


def cases(proof):
    yield "untouched", proof, Verdict.ACCEPT
    for label, idx in (("input x[0]", 0), ("output y[0]", -3)):
        bad = json.loads(json.dumps(proof))
        inst = bad["instances"][0]
        inst[idx] = inst[1] if inst[idx] != inst[1] else inst[2]
        yield label, bad, Verdict.REJECT
    bad = json.loads(json.dumps(proof))
    h = bad["hex_proof"]
    bad["hex_proof"] = h[:-2] + ("00" if h[-2:] != "00" else "01")
    bad["proof"] = list(bytes.fromhex(bad["hex_proof"].removeprefix("0x")))
    yield "proof last byte", bad, Verdict.REJECT


def main():
    proof = json.load(open(P("proof.json")))
    failures = 0
    with tempfile.TemporaryDirectory() as d:
        for label, obj, want in cases(proof):
            path = os.path.join(d, "candidate.json")
            json.dump(obj, open(path, "w"))
            got, detail = verify(ezkl, path, P("settings.json"), P("vk.key"), P("kzg.srs"))
            ok = got == want
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {label}: want {want.name}, got {got.name} {detail}")
    if failures:
        sys.exit(f"{failures} check(s) failed")
    print("all checks passed")


if __name__ == "__main__":
    main()
