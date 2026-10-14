"""Request binding for EZKL 23.0.5 proofs.

The device encodes the request it received itself, checks the proof against that
encoding plus the server's output field elements, and releases only the outputs
decoded from those same verified values. See docs/BINDING_SPEC.md.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# BN254 scalar field
Q = 21888242871839275222246405745257275088548364400416034343698204186575808495617

FELT_HEX_LEN = 64
EZKL_VERSION = "23.0.5"
MAX_SCALE = 30
MAX_TENSOR_LEN = 1 << 16
# Narrower than EZKL's i128 on purpose; the circuit's own domain is far smaller (see domain.py).
MAX_ABS_SCALED = 2.0 ** 63


class BindingError(ValueError):
    pass


def _int(v, what: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise BindingError(f"{what} must be an integer in [{lo}, {hi}], got {v!r}")
    return v


@dataclass(frozen=True)
class CircuitSpec:
    input_scales: tuple[int, ...]
    output_scales: tuple[int, ...]
    input_shapes: tuple[tuple[int, ...], ...]
    output_shapes: tuple[tuple[int, ...], ...]
    input_types: tuple[str, ...]
    settings_sha256: str

    @classmethod
    def from_settings(cls, path: str | Path, expected_sha256: str) -> "CircuitSpec":
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_sha256:
            raise BindingError(f"settings sha256 {digest} does not match pin {expected_sha256}")
        return cls.parse(json.loads(raw), digest)

    @classmethod
    def parse(cls, s: dict, digest: str) -> "CircuitSpec":
        if s.get("version") != EZKL_VERSION:
            raise BindingError(f"settings version {s.get('version')!r}, expected {EZKL_VERSION}")
        ra = s.get("run_args")
        if not isinstance(ra, dict):
            raise BindingError("settings have no run_args")
        if ra.get("input_visibility") != "Public" or ra.get("output_visibility") != "Public":
            raise BindingError("binding needs public inputs and outputs")
        if ra.get("param_visibility") != "Fixed":
            raise BindingError("binding needs fixed parameters")

        in_scales = s.get("model_input_scales")
        out_scales = s.get("model_output_scales")
        shapes = s.get("model_instance_shapes")
        in_types = s.get("input_types")
        for name, v in (("model_input_scales", in_scales), ("model_output_scales", out_scales),
                        ("model_instance_shapes", shapes), ("input_types", in_types)):
            if not isinstance(v, list) or not v:
                raise BindingError(f"settings field {name} missing or empty")
        if len(in_types) != len(in_scales):
            raise BindingError("input_types and model_input_scales have different lengths")
        if len(shapes) != len(in_scales) + len(out_scales):
            raise BindingError("instance shapes don't match the number of inputs and outputs")
        for t in in_types:
            if t not in ("F32", "F64"):
                raise BindingError(f"unsupported input type {t!r}")

        parsed_shapes = []
        for sh in shapes:
            if not isinstance(sh, list) or not sh:
                raise BindingError(f"bad shape {sh!r}")
            dims = tuple(_int(d, "dimension", 1, MAX_TENSOR_LEN) for d in sh)
            if math.prod(dims) > MAX_TENSOR_LEN:
                raise BindingError(f"tensor too large: {dims}")
            parsed_shapes.append(dims)

        n_in = len(in_scales)
        return cls(
            input_scales=tuple(_int(x, "input scale", 0, MAX_SCALE) for x in in_scales),
            output_scales=tuple(_int(x, "output scale", 0, MAX_SCALE) for x in out_scales),
            input_shapes=tuple(parsed_shapes[:n_in]),
            output_shapes=tuple(parsed_shapes[n_in:]),
            input_types=tuple(in_types),
            settings_sha256=digest,
        )

    @property
    def input_lens(self) -> tuple[int, ...]:
        return tuple(math.prod(sh) for sh in self.input_shapes)

    @property
    def output_lens(self) -> tuple[int, ...]:
        return tuple(math.prod(sh) for sh in self.output_shapes)


def _round_half_away(v: float) -> int:
    # Rust f64::round. Not floor(v + 0.5): that addition can round, e.g. 0.49999999999999994 + 0.5 == 1.0
    a = abs(v)
    fl = math.floor(a)
    r = fl + 1 if a - fl >= 0.5 else fl
    return int(r) if v >= 0 else -int(r)


def quantize(x: float, scale: int, input_type: str = "F32") -> int:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise BindingError(f"input must be a number, got {type(x).__name__}")
    try:
        x = float(x)
        if not math.isfinite(x):
            raise BindingError("input must be finite")
        if input_type == "F32":
            v = struct.unpack("<f", struct.pack("<f", x))[0]
        elif input_type == "F64":
            v = x
        else:
            raise BindingError(f"unsupported input type {input_type!r}")
    except (OverflowError, struct.error) as e:
        raise BindingError(f"input does not fit {input_type}") from e
    scaled = v * (2.0 ** scale)
    if not math.isfinite(scaled) or abs(scaled) >= MAX_ABS_SCALED:
        raise BindingError("scaled input out of range")
    return _round_half_away(scaled)


def int_to_felt(v: int) -> int:
    return v % Q


def felt_to_signed(f: int) -> int:
    if not 0 <= f < Q:
        raise BindingError("field element out of range")
    return f - Q if f > Q // 2 else f


def felt_to_hex(f: int) -> str:
    if not 0 <= f < Q:
        raise BindingError("field element out of range")
    return f.to_bytes(32, "little").hex()


def hex_to_felt(h: str) -> int:
    if not isinstance(h, str) or len(h) != FELT_HEX_LEN:
        raise BindingError("field element must be exactly 64 hex characters")
    if any(c not in "0123456789abcdef" for c in h):
        raise BindingError("field element must be lower-case hex without a prefix")
    f = int.from_bytes(bytes.fromhex(h), "little")
    if f >= Q:
        raise BindingError("non-canonical field element")
    return f


def quantize_inputs(spec: CircuitSpec, inputs: Sequence[Sequence[float]]) -> list[list[int]]:
    if len(inputs) != len(spec.input_scales):
        raise BindingError(f"expected {len(spec.input_scales)} input tensor(s), got {len(inputs)}")
    out = []
    for vals, scale, n, t in zip(inputs, spec.input_scales, spec.input_lens, spec.input_types, strict=True):
        if len(vals) != n:
            raise BindingError(f"expected {n} input values, got {len(vals)}")
        out.append([quantize(x, scale, t) for x in vals])
    return out


def encode_inputs(spec: CircuitSpec, inputs: Sequence[Sequence[float]]) -> list[list[str]]:
    return [[felt_to_hex(int_to_felt(v)) for v in t] for t in quantize_inputs(spec, inputs)]


def check_outputs(spec: CircuitSpec, outputs_hex: Sequence[Sequence[str]]) -> list[list[str]]:
    if len(outputs_hex) != len(spec.output_scales):
        raise BindingError(f"expected {len(spec.output_scales)} output tensor(s), got {len(outputs_hex)}")
    checked = []
    for vals, n in zip(outputs_hex, spec.output_lens, strict=True):
        if len(vals) != n:
            raise BindingError(f"expected {n} output values, got {len(vals)}")
        checked.append([felt_to_hex(hex_to_felt(h)) for h in vals])
    return checked


def build_instances(spec: CircuitSpec, request_inputs: Sequence[Sequence[float]],
                    server_outputs_hex: Sequence[Sequence[str]]) -> list[list[str]]:
    # EZKL concatenates all input tensors in graph order, then all outputs, into one column.
    flat: list[str] = []
    for tensor in encode_inputs(spec, request_inputs):
        flat.extend(tensor)
    for tensor in check_outputs(spec, server_outputs_hex):
        flat.extend(tensor)
    return [flat]


def decode_outputs(spec: CircuitSpec, outputs_hex: Sequence[Sequence[str]]) -> list[list[float]]:
    decoded = []
    for vals, scale in zip(check_outputs(spec, outputs_hex), spec.output_scales, strict=True):
        decoded.append([felt_to_signed(hex_to_felt(h)) / (2 ** scale) for h in vals])
    return decoded


def split_instances(spec: CircuitSpec, instances: Sequence[Sequence[str]]) -> tuple[list[list[str]], list[list[str]]]:
    if len(instances) != 1:
        raise BindingError("expected a single instance column")
    flat = list(instances[0])
    if len(flat) != sum(spec.input_lens) + sum(spec.output_lens):
        raise BindingError("instance vector has the wrong length")
    ins, outs, i = [], [], 0
    for n in spec.input_lens:
        ins.append(flat[i:i + n])
        i += n
    for n in spec.output_lens:
        outs.append(flat[i:i + n])
        i += n
    return ins, outs
