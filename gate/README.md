# Rust gate

The phase-one binary implements the Python gate's `arrival` and `fixed`
policies. The Python gate remains the reference. See
[SERVICE_SPEC](../docs/SERVICE_SPEC.md) and
[BINDING_SPEC](../docs/BINDING_SPEC.md) for the protocol and binding rules.

## Build

Run from the repository root on a build machine with Git, Python 3.12,
a C/C++ toolchain and rustup:

```sh
rustup toolchain install nightly-2025-12-01 --profile minimal
bash gate/prepare.sh
export CARGO_HOME="$PWD/.deps/cargo"
cargo +nightly-2025-12-01 build --manifest-path gate/Cargo.toml --release --locked
```

`sources.toml` pins each upstream checkout to a commit. Preparation checks
the commit, origin and clean working tree, and checks EZKL's original lock
file. The workspace lock derives from that lock; Cargo patches resolve the
pinned sources under the ignored `.deps/` directory. Run preparation before
every build. Do not share target directories with mutated source copies.

Only the binary and the pinned model bundle are needed on the device.
Neither Python nor the EZKL CLI is a runtime dependency. Run
`gate/target/release/pge-gate --help` for flags. Supply the same bundle,
pins, domain and policy as the Python gate, omitting `--cli`. Immediate
release is intentionally unsupported.

## Boundaries

- `wire` parses and canonically re-encodes messages; it forbids unsafe code.
- `core` is a deterministic state machine. Its caller supplies time and
  events and executes release, wait and spin actions.
- `verify` binds requests and checks immutable bundle snapshots in memory.
- `gate` owns sockets, timers and logs. Each proof runs in a fresh child
  with resource limits; bounded pipe records carry the bundle and proof.

The verifier writes no runtime files. `--log` is an explicit optional write
by the parent; omit it to send records to stderr. Verification re-hashes the
private memory snapshot before each job and again in the child. Changes to
the original bundle after startup cannot change that snapshot. The network
and scheduler still share a process and user. Process isolation does not
replace the device's planned user, filesystem and network restrictions.

## Tests

Provide the pinned setup file through `PGE_SRS`:

```sh
export PGE_SRS=/absolute/path/to/kzg.srs
cargo +nightly-2025-12-01 test --manifest-path gate/Cargo.toml --workspace --locked
PGE_RUST_BIN="$PWD/gate/target/release/pge-gate" \
  .venv/bin/python -m pytest tests/test_gate_rust.py tests/test_docs.py -q
```

Set `PGE_RUST_REFERENCE_CLI` to an official pinned EZKL executable to use
the Python gate's CLI backend in differential tests. Without a Python
wheel, that mode skips only new proof generation, while checking existing
proofs through both gates. An explicitly configured missing Rust binary
fails; without one, the differential tests skip if the default binary is
absent.

For fuzzing, install cargo-fuzz on the build machine and run from `gate/`:

```sh
cargo +nightly-2025-12-01 fuzz run wire -- -runs=10000
cargo +nightly-2025-12-01 fuzz run verifier_input -- -runs=10000
```

These bounded runs are smoke checks, not evidence that parsing is exhaustive.
The CI workflow builds and tests native Linux targets and a static aarch64
musl binary. Target execution and deployment measurements belong in the
handoff and existing results workflow, not this build guide.
