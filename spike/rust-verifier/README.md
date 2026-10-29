# Spike: EZKL's verifier as a small Rust program (W2, 16 Oct 2026)

Question from the plan: can EZKL's verifier be built as one small program for the device, instead of shipping EZKL's whole CLI (which also contains the prover, setup and EVM code)?

**Answer: yes, on the laptop.** `pge_verify.rs` is a 50-line example built inside EZKL v23.0.5's own checkout with `--no-default-features`, which turns off EZKL's ONNX, Ethereum deployment (alloy, foundry), tokio, reqwest and OpenSSL features. EZKL's proof library `snark-verifier` still compiles `revm` (an Ethereum virtual machine) and `alloy-primitives` as dependencies; the linker drops almost all of it (one `revm` symbol in the macOS binary). The `EvmTranscript` names in the binary are the Keccak transcript EZKL proofs use, which any verifier needs. A trusted-code-size claim should count what is linked, not what is compiled. It calls the same functions as the CLI's `verify` (`src/execute.rs`): settings, SRS (downsized to `logrows`), vk, proof, then `verify_proof_circuit` with SHPLONK, a single strategy and the EVM transcript.

| | EZKL CLI 23.0.5 | `pge_verify` |
|---|---|---|
| Size | see `book/src/results.md` (checking one proof) | 2.3 MB (aarch64 Linux); 2.1 MB (aarch64 macOS); 2.5 MB (x86_64 Linux, CI) |
| Links | glibc (needs 2.18 or newer) | libc, libm, libgcc_s on Linux (built on Ubuntu 24.04, so needs glibc 2.35 or newer); libSystem, libiconv on macOS |
| Peak memory, tiny bundle (aarch64 Linux VM) | 64 MB resident; needs about 768 MiB of address space with 6 threads, 128 MiB with `RAYON_NUM_THREADS=1` | 25 MB resident; runs in a 64 MiB address-space limit with one thread |
| Whole-process verify, tiny bundle | on the Pi: see `book/src/results.md` | 15.0-17.1 ms on an M4 Pro (n = 20, median 15.3) |
| Valid / tampered output / tampered input / truncated / garbage | accept / reject / reject / reject / error | exit 0 / 1 / 1 / 1 / 2 (macOS and Linux CI) |
| Bytes appended to a valid proof | accepted | accepted (exit 0), same as the CLI |

The two time columns are on different machines, so they aren't comparable; the Pi run is still to do.

Build and check:

```
BIN=$(spike/rust-verifier/build.sh)            # clones EZKL v23.0.5 into .build/, about 5 min the first time
spike/rust-verifier/check.sh "$BIN" prover/artifacts/kzg.srs
```

Toolchain: EZKL's pinned `nightly-2025-12-01` (rustc 1.93.0-nightly). The build uses EZKL's `Cargo.lock` (`--locked`).

## Not done yet

- Run the aarch64 Linux build on the Pi (built natively in an Ubuntu 24.04 arm64 VM on 16 Oct; passes `check.sh` there). A static (musl) build is not tried.
- Exit codes are the spike's own: 0 valid, 1 any halo2 error, 2 could not load an input. EZKL's errors don't separate bad data from failed I/O cleanly (see `docs/BINDING_SPEC.md` section 6), so the endpoint should keep treating anything but 0 and 1 as ERROR, and run the canonical-proof check first.
- Malformed inputs could make the halo2 code panic; a panic exits with 101, which the endpoint must treat as ERROR. Not fuzzed.
- No memory bound on what it loads; the endpoint bounds the proof size before calling it.
- Not wired into the endpoint yet (`pge/cli.py` expects the CLI's log format).
