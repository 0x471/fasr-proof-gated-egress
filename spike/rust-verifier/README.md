# Spike: EZKL's verifier as a small Rust program (W2, 16 Oct 2026)

Question from the plan: can EZKL's verifier be built as one small program for the device, instead of shipping EZKL's 36 MB CLI (which also contains the prover, setup and EVM code)?

**Answer: yes, on the laptop.** `pge_verify.rs` is a 50-line example built inside EZKL v23.0.5's own checkout with `--no-default-features`, so none of the ONNX, EVM, tokio, reqwest or OpenSSL code is compiled in. It calls the same functions as the CLI's `verify` (`src/execute.rs`): settings, SRS (downsized to `logrows`), vk, proof, then `verify_proof_circuit` with SHPLONK, a single strategy and the EVM transcript.

| | EZKL CLI 23.0.5 | `pge_verify` |
|---|---|---|
| Size | 36 MB (aarch64 Linux) | 2.1 MB (aarch64 macOS) |
| Links | glibc | libSystem, libiconv |
| Whole-process verify, tiny bundle | 99-103 ms on the Pi | 15.0-17.1 ms on an M4 Pro (n = 20, median 15.3) |
| Valid / tampered output / tampered input / truncated / garbage | accept / reject / reject / reject / error | exit 0 / 1 / 1 / 1 / 2 |
| Bytes appended to a valid proof | accepted | accepted (exit 0), same as the CLI |

The two time columns are on different machines, so they aren't comparable; the Pi run is still to do.

Build and check:

```
BIN=$(spike/rust-verifier/build.sh)            # clones EZKL v23.0.5 into .build/, about 5 min the first time
spike/rust-verifier/check.sh "$BIN" prover/artifacts/kzg.srs
```

Toolchain: EZKL's pinned `nightly-2025-12-01` (rustc 1.93.0-nightly). The build uses EZKL's `Cargo.lock` (`--locked`).

## Not done yet

- A Linux aarch64 build for the Pi, ideally static (musl). Not tried.
- Exit codes are the spike's own: 0 valid, 1 any halo2 error, 2 could not load an input. EZKL's errors don't separate bad data from failed I/O cleanly (see `docs/BINDING_SPEC.md` section 6), so the gate should keep treating anything but 0 and 1 as ERROR, and run the canonical-proof check first.
- Malformed inputs could make the halo2 code panic; a panic exits with 101, which the gate must treat as ERROR. Not fuzzed.
- No memory bound on what it loads; the gate bounds the proof size before calling it.
- Not wired into the gate yet (`pge/cli.py` expects the CLI's log format).
