"""Input domain of an affine + ReLU circuit (y = ReLU(xW + b)), as EZKL 23.0.5 builds it.

Witness generation decomposes the quantized inputs and every pre-activation in base
`decomp_base` with `decomp_legs` digits, so each must satisfy |v| <= base**legs - 1.
Requests outside that region can never get a valid proof, so the device rejects them
before forwarding instead of letting the prover fail on demand.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Sequence

from pge.binding import BindingError, CircuitSpec, _round_half_away, quantize_inputs


def _is_f32(v) -> bool:
    try:
        return math.isfinite(v) and struct.unpack("<f", struct.pack("<f", v))[0] == v
    except (OverflowError, TypeError, struct.error):
        return False


@dataclass(frozen=True)
class AffineReluDomain:
    wq: tuple[tuple[int, ...], ...]  # [n_in][n_out], at the param scale
    bq: tuple[int, ...]              # at the output scale (input scale + param scale)
    bound: int                       # decomp_base ** decomp_legs - 1

    @classmethod
    def from_float(cls, w: Sequence[Sequence[float]], b: Sequence[float], param_scale: int,
                   out_scale: int, decomp_base: int, decomp_legs: int) -> "AffineReluDomain":
        """Quantize W and b the way EZKL 23.0.5 does for the pinned tiny graph.

        Only valid when W and b are the model's F32 constants, each used once, in a
        MatMul + Add + ReLU graph that is not rescaled before the output (output scale =
        input scale + param scale). EZKL requantizes a shared bias differently, so other
        graphs need their compiled constants instead.
        """
        for v in (*(x for row in w for x in row), *b):
            if not _is_f32(v):
                raise BindingError(f"parameter {v!r} is not a finite F32 value")
        wq = tuple(tuple(_round_half_away(v * 2.0 ** param_scale) for v in row) for row in w)
        bq = tuple(_round_half_away(v * 2.0 ** out_scale) for v in b)
        return cls(wq=wq, bq=bq, bound=decomp_base ** decomp_legs - 1)

    def preactivations(self, n: Sequence[int]) -> list[int]:
        return [sum(n[i] * self.wq[i][j] for i in range(len(n))) + self.bq[j] for j in range(len(self.bq))]

    def admits(self, n: Sequence[int]) -> bool:
        if len(n) != len(self.wq):
            return False
        if any(abs(v) > self.bound for v in n):
            return False
        return all(abs(z) <= self.bound for z in self.preactivations(n))

    def check(self, spec: CircuitSpec, inputs: Sequence[Sequence[float]]) -> list[int]:
        (n,) = quantize_inputs(spec, inputs)
        if not self.admits(n):
            raise BindingError("request outside the circuit's input domain")
        return n
