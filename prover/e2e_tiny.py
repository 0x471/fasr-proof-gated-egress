"""End-to-end EZKL run on a tiny deterministic model (W1 task).

Builds y = ReLU(x @ W + b) directly in ONNX (no PyTorch), then runs the EZKL
pipeline: settings -> calibrate -> compile -> SRS -> setup -> witness -> prove -> verify.
Visibility: input public, output public, params fixed (weights baked into the vk).
Writes all artifacts to ./artifacts and prints sizes and timings.
"""
import asyncio
import inspect
import json
import os
import sys
import time

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

import ezkl

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pge.verdict import Verdict, verify  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
os.makedirs(OUT, exist_ok=True)
P = lambda name: os.path.join(OUT, name)


def run(fn, *a, **k):
    """Call an EZKL binding inside an event loop; some return awaitables, some don't."""
    async def _call():
        r = fn(*a, **k)
        return await r if inspect.isawaitable(r) else r
    return asyncio.run(_call())


def build_model():
    rng = np.random.default_rng(0)
    W = rng.uniform(-1, 1, size=(4, 3)).astype(np.float32)
    b = rng.uniform(-0.5, 0.5, size=(3,)).astype(np.float32)
    graph = helper.make_graph(
        [
            helper.make_node("MatMul", ["x", "W"], ["xw"]),
            helper.make_node("Add", ["xw", "b"], ["z"]),
            helper.make_node("Relu", ["z"], ["y"]),
        ],
        "tiny",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 4])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 3])],
        initializer=[numpy_helper.from_array(W, "W"), numpy_helper.from_array(b, "b")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, P("model.onnx"))
    x = np.array([[0.5, -0.2, 0.9, 0.1]], dtype=np.float32)
    with open(P("input.json"), "w") as f:
        json.dump({"input_data": [x.flatten().tolist()]}, f)
    return x, np.maximum(x @ W + b, 0)


def main():
    x, y_float = build_model()
    print("float output:", y_float.round(4).tolist())

    args = ezkl.PyRunArgs()
    args.input_visibility = "public"
    args.output_visibility = "public"
    args.param_visibility = "fixed"

    t = {}
    s = time.perf_counter()
    ok = run(ezkl.gen_settings, P("model.onnx"), P("settings.json"), py_run_args=args)
    if ok is not True:
        sys.exit("gen_settings failed")
    run(ezkl.calibrate_settings, P("input.json"), P("model.onnx"), P("settings.json"), "resources")
    ok = run(ezkl.compile_circuit, P("model.onnx"), P("network.ezkl"), P("settings.json"))
    if ok is not True:
        sys.exit("compile_circuit failed")
    t["settings+calibrate+compile"] = time.perf_counter() - s

    settings = json.load(open(P("settings.json")))
    ra = settings["run_args"]
    print("visibility:", ra["input_visibility"], ra["output_visibility"], ra["param_visibility"],
          "| logrows:", ra["logrows"])
    # Startup check: inputs and outputs must be public and parameters fixed, or the proof stops binding x and y.
    if not (ra["input_visibility"] == "Public" and ra["output_visibility"] == "Public"
            and ra["param_visibility"] == "Fixed"):
        sys.exit(f"wrong visibility settings: {ra}")

    s = time.perf_counter()
    ok = run(ezkl.get_srs, settings_path=P("settings.json"), srs_path=P("kzg.srs"))
    if ok is not True:
        sys.exit("get_srs failed")
    t["get_srs"] = time.perf_counter() - s

    s = time.perf_counter()
    ok = run(ezkl.setup, P("network.ezkl"), P("vk.key"), P("pk.key"), srs_path=P("kzg.srs"))
    if ok is not True:
        sys.exit("setup failed")
    t["setup"] = time.perf_counter() - s

    s = time.perf_counter()
    run(ezkl.gen_witness, P("input.json"), P("network.ezkl"), P("witness.json"))
    t["witness"] = time.perf_counter() - s

    s = time.perf_counter()
    run(ezkl.prove, P("witness.json"), P("network.ezkl"), P("pk.key"), P("proof.json"), srs_path=P("kzg.srs"))
    t["prove"] = time.perf_counter() - s

    s = time.perf_counter()
    good, detail = verify(ezkl, P("proof.json"), P("settings.json"), P("vk.key"), P("kzg.srs"))
    t["verify"] = time.perf_counter() - s
    print("verify:", good.name, detail)

    proof = json.load(open(P("proof.json")))
    bad = json.loads(json.dumps(proof))
    inst = bad["instances"][0]
    inst[-1] = inst[0] if inst[-1] != inst[0] else inst[1]
    json.dump(bad, open(P("proof_tampered.json"), "w"))
    tampered, detail = verify(ezkl, P("proof_tampered.json"), P("settings.json"), P("vk.key"), P("kzg.srs"))
    print("tampered output:", tampered.name, detail)

    for name in ["model.onnx", "settings.json", "network.ezkl", "kzg.srs", "vk.key", "pk.key", "proof.json"]:
        print(f"  {name:14s} {os.path.getsize(P(name)):>12,d} bytes")
    for k, v in t.items():
        print(f"  {k:28s} {v*1000:9.1f} ms")

    if good is not Verdict.ACCEPT:
        sys.exit(f"FAIL: valid proof gave {good.name}")
    if tampered is not Verdict.REJECT:
        sys.exit(f"FAIL: tampered proof gave {tampered.name}")
    print("PASS")


if __name__ == "__main__":
    main()
