# Rust endpoint

The endpoint the device runs (from 26 Oct). It implements the Python endpoint's
`arrival` and `fixed` policies, plus a failure cap the Python endpoint doesn't
have. The Python endpoint remains the reference the tests compare against. See
[SERVICE_SPEC](../docs/SERVICE_SPEC.md) and
[BINDING_SPEC](../docs/BINDING_SPEC.md) for the protocol and binding rules.

## Build

Run from the repository root on a build machine with Git, Python 3.12,
a C/C++ toolchain and rustup:

```sh
rustup toolchain install nightly-2025-12-01 --profile minimal
bash endpoint/prepare.sh
export CARGO_HOME="$PWD/.deps/cargo"
cargo +nightly-2025-12-01 build --manifest-path endpoint/Cargo.toml --release --locked
```

`sources.toml` pins each upstream checkout to a commit. Preparation checks
the commit, origin and clean working tree, and checks EZKL's original lock
file. The workspace lock derives from that lock; Cargo patches resolve the
pinned sources under the ignored `.deps/` directory. Run preparation before
every build. Do not share target directories with mutated source copies.

Only the binary and the pinned model bundle are needed on the device.
Neither Python nor the EZKL CLI is a runtime dependency. Run
`endpoint/target/release/pge-endpoint --help` for flags. Supply the same bundle,
pins, domain and policy as the Python endpoint, omitting `--cli`. Immediate
release is intentionally unsupported.

## Boundaries

- `wire` parses and canonically re-encodes messages; it forbids unsafe code.
- `core` is a deterministic state machine. Its caller supplies time and
  events and executes release, wait and spin actions.
- `verify` binds requests and checks immutable bundle snapshots in memory.
- `update` decides whether a signed update may be applied: key list signed by
  the root, manifest signed by a listed key, this device, no rollback or
  replay, every file's hash. `pge-sign` (same crate) signs offline.
- `endpoint` owns sockets, timers and logs. Each proof runs in a fresh child
  with resource limits; bounded pipe records carry the bundle and proof.

The verifier writes no runtime files. `--log` is an explicit optional write
by the parent; omit it to send records to stderr. The child re-hashes the
bundle it receives against the pins before every check. Changes to
the original bundle after startup cannot change that snapshot. The network
and scheduler still share a process and user. Process isolation does not
replace the device's planned user, filesystem and network restrictions.

## Event loop, cap and errors

- One thread waits in `poll()` on the listener, the server, every client and a
  wake-up pipe that the verifier and connect threads write to. The wait ends
  at the earliest timer: the next fixed release (minus the spin), the next
  expiry, a frame or idle deadline, or a reconnect. A fixed release wakes
  `spin` early and busy-waits to its due time, at most once per loop.
- `--cap-window W --cap-failures F --cap-cooldown C` turns on the failure
  cap: more than F server-caused failures in the last W replies trip it,
  the history is cleared, and the next C replies are unavailable. `core`
  implements `observer/cap_model.py` exactly; `tests/test_endpoint_rust_cap.py`
  checks the binary against it. `observer/cap_bound.py` bounds only the
  ok/unavailable sequence of admitted replies, not busy replies, timing or
  logs (its docstring says what it covers).
- A failed `accept()` is logged as `accept_failed` and pauses accepting for
  1 s; the endpoint never exits on it, and a check that can't get resources is
  ERROR. The verifier child only lowers its resource limits, soft and hard,
  never asking for more than the endpoint was started with.
- `--idle SECONDS` (default 60): a client that sends nothing for that long,
  or whose replies pile up unread, stops being read; its answers drain for up
  to the deadline plus 1 s and then it is closed.

## Signed updates

`pge-endpoint --apply-update DIR --root FILE --device ID --store STORE` applies
a signed update, and `--store STORE --root FILE --device ID` runs the endpoint
from it instead of `--bundle`/`--pins`/`--domain`. Formats, rules and limits are
in [UPDATE_SPEC](../docs/UPDATE_SPEC.md).

## Tests

Provide the pinned setup file through `PGE_SRS`:

```sh
export PGE_SRS=/absolute/path/to/kzg.srs
cargo +nightly-2025-12-01 test --manifest-path endpoint/Cargo.toml --workspace --locked
PGE_RUST_BIN="$PWD/endpoint/target/release/pge-endpoint" \
  .venv/bin/python -m pytest tests/test_endpoint_rust.py tests/test_endpoint_rust_cap.py \
  tests/test_endpoint_rust_robust.py tests/test_cap_model.py tests/test_docs.py -q
```

`PGE_TIMING=1` also runs a release-timing check (fixed release with a 5 ms
spin must be on time); it is off by default because shared CI machines are
too noisy for it.

Set `PGE_RUST_REFERENCE_CLI` to an official pinned EZKL executable to use
the Python endpoint's CLI backend in differential tests. Without a Python
wheel, that mode skips only new proof generation, while checking existing
proofs through both endpoints. An explicitly configured missing Rust binary
fails; without one, the differential tests skip if the default binary is
absent.

For fuzzing, install cargo-fuzz on the build machine and run from `endpoint/`:

```sh
cargo +nightly-2025-12-01 fuzz run wire -- -runs=10000
cargo +nightly-2025-12-01 fuzz run verifier_input -- -runs=10000
```

These bounded runs are smoke checks, not evidence that parsing is exhaustive.
The CI workflow builds and tests native Linux targets and a static aarch64
musl binary. Target execution and deployment measurements belong in the
handoff and existing results workflow, not this build guide.
