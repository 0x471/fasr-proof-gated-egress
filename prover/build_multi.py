"""Build and prove a two-input, two-output circuit used to test tensor order and per-output scales.

Inputs are declared in the order (z, a); outputs are d = a - z (scale 13) and p = z * a (scale 26).
"""
import asyncio
import inspect
import json
import os
import sys

import onnx
from onnx import TensorProto, helper

import ezkl

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts_multi")
os.makedirs(OUT, exist_ok=True)
P = lambda name: os.path.join(OUT, name)


def run(fn, *a, **k):
    async def _call():
        r = fn(*a, **k)
        return await r if inspect.isawaitable(r) else r
    return asyncio.run(_call())


def main():
    graph = helper.make_graph(
        [helper.make_node("Sub", ["a", "z"], ["d"]), helper.make_node("Mul", ["z", "a"], ["p"])],
        "multi",
        [helper.make_tensor_value_info("z", TensorProto.FLOAT, [1, 2]),
         helper.make_tensor_value_info("a", TensorProto.FLOAT, [1, 2])],
        [helper.make_tensor_value_info("d", TensorProto.FLOAT, [1, 2]),
         helper.make_tensor_value_info("p", TensorProto.FLOAT, [1, 2])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, P("model.onnx"))
    with open(P("input.json"), "w") as f:
        json.dump({"input_data": [[0.25, -0.5], [-0.75, 0.125]]}, f)

    args = ezkl.PyRunArgs()
    args.input_visibility = "public"
    args.output_visibility = "public"
    args.param_visibility = "fixed"
    args.input_scale = 13
    args.param_scale = 13
    args.logrows = 15
    args.scale_rebase_multiplier = 2
    steps = [
        ("gen_settings", lambda: run(ezkl.gen_settings, P("model.onnx"), P("settings.json"), py_run_args=args)),
        ("compile", lambda: run(ezkl.compile_circuit, P("model.onnx"), P("network.ezkl"), P("settings.json"))),
        ("srs", lambda: run(ezkl.get_srs, settings_path=P("settings.json"), srs_path=P("kzg.srs"))),
        ("setup", lambda: run(ezkl.setup, P("network.ezkl"), P("vk.key"), P("pk.key"), srs_path=P("kzg.srs"))),
    ]
    for name, step in steps:
        if step() is not True:
            sys.exit(f"{name} failed")
    run(ezkl.gen_witness, P("input.json"), P("network.ezkl"), P("witness.json"))
    run(ezkl.prove, P("witness.json"), P("network.ezkl"), P("pk.key"), P("proof.json"), srs_path=P("kzg.srs"))
    if run(ezkl.verify, P("proof.json"), P("settings.json"), P("vk.key"), srs_path=P("kzg.srs")) is not True:
        sys.exit("verify failed")
    s = json.load(open(P("settings.json")))
    print("scales in/out:", s["model_input_scales"], s["model_output_scales"], "logrows:", s["run_args"]["logrows"])
    print("witness outputs:", json.load(open(P("witness.json")))["pretty_elements"]["rescaled_outputs"])


if __name__ == "__main__":
    main()
