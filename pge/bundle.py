"""A pinned model bundle: settings, verification key and SRS, checked before use."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pge.binding import BindingError, CircuitSpec


@dataclass(frozen=True)
class Pins:
    settings: str
    vk: str
    srs: str


@dataclass(frozen=True)
class Bundle:
    spec: CircuitSpec
    settings: Path
    vk: Path
    srs: Path

    @classmethod
    def load(cls, settings: str | Path, vk: str | Path, srs: str | Path, pins: Pins) -> "Bundle":
        for path, want, name in ((vk, pins.vk, "vk"), (srs, pins.srs, "srs")):
            got = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if got != want:
                raise BindingError(f"{name} sha256 {got} does not match pin {want}")
        spec = CircuitSpec.from_settings(settings, pins.settings)
        return cls(spec=spec, settings=Path(settings), vk=Path(vk), srs=Path(srs))
