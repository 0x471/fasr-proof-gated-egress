"""A pinned model bundle: settings, verification key and SRS, checked before use.

load reads each file once, checks its sha256 and keeps a private read-only copy of
exactly those bytes. verify re-checks the copies before every call, so a lasting change
shows up as ERROR instead of a [halo2] failure. The same user can still swap a file
between the re-check and EZKL's read.
"""
from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pge.binding import BindingError, CircuitSpec
from pge.verdict import Verdict, verify

_FILES = (("settings", "settings.json"), ("vk", "vk.key"), ("srs", "kzg.srs"))


@dataclass(frozen=True)
class Pins:
    settings: str
    vk: str
    srs: str


def _check(name: str, data: bytes, want: str) -> None:
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise BindingError(f"{name} sha256 {got} does not match pin {want}")


@dataclass(frozen=True)
class Bundle:
    spec: CircuitSpec
    settings: Path
    vk: Path
    srs: Path
    pins: Pins
    _store: tempfile.TemporaryDirectory = field(repr=False, compare=False)

    @classmethod
    def load(cls, settings: str | Path, vk: str | Path, srs: str | Path, pins: Pins) -> "Bundle":
        raw = {"settings": Path(settings).read_bytes(), "vk": Path(vk).read_bytes(), "srs": Path(srs).read_bytes()}
        _check("vk", raw["vk"], pins.vk)
        _check("srs", raw["srs"], pins.srs)
        spec = CircuitSpec.from_bytes(raw["settings"], pins.settings)
        store = tempfile.TemporaryDirectory(prefix="pge-bundle-")
        paths = {}
        for name, fname in _FILES:
            p = Path(store.name) / fname
            p.write_bytes(raw[name])
            p.chmod(0o400)
            paths[name] = p
        return cls(spec=spec, pins=pins, _store=store, **paths)

    def check(self) -> None:
        for name, _ in _FILES:
            _check(name, getattr(self, name).read_bytes(), getattr(self.pins, name))

    def verify(self, ezkl, proof: str | Path) -> tuple[Verdict, str]:
        try:
            self.check()
        except (OSError, BindingError) as e:
            return Verdict.ERROR, f"bundle check failed: {e}"
        return verify(ezkl, proof, self.settings, self.vk, self.srs)
