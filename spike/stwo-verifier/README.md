# Spike: a pinned S-two verifier

Can the device verify S-two (Circle STARK) proofs of Cairo programs, and what does it have to pin to do it safely? Built on StarkWare's stwo-cairo at the commit tagged `sharp-7.4` (`b0b7400`, 17 Jun 2026), stwo `74951f79`, Rust `nightly-2026-01-15`. A branch also named `sharp-7.4` points to a later commit (`9b93cc3`, tagged `sharp-7.4.RC3`, 1 Jul); its Rust verifier crate (`cairo-air`) is identical, so the pin stays on the tag. Which commit SHARP runs in production isn't known to us.

Not on the device yet. The endpoint still uses EZKL.

## What it checks

`pge-stwo-verify PROOF.bin PROGRAM_HASH [OUTPUT_FELT,...]`

- Input is the prover's binary format only (bincode, then bzip2). The file is capped at 8 MiB and the decompressed payload at 16 MiB. Nothing may follow the bzip2 stream or the bincode payload.
- The PCS config must equal the prover's 96-bit defaults (`pow_bits` 26, blowup 2^1, 70 queries, last layer 0, fold step 1). The channel salt must be 0 and the preprocessed trace `Canonical`.
- The program hash (stwo-cairo's own `get_verification_output`) must equal `PROGRAM_HASH`. If given, the output felts must match too; an empty string pins an empty output.
- Exit 0 = valid, 1 = invalid or not what was pinned, 2 = could not check, 3 = the verifier panicked.

## Why the pins are needed

- `verify_cairo` takes the PCS config from the proof itself (`crates/cairo-air/src/verifier.rs:314` at `sharp-7.4`; line 281 on `main` as of 13 Aug 2026) and checks no minimum. StarkWare's own `verify` CLI accepts `fixtures/weak_all_opcode.bin`, a proof made with `pow_bits` 1 and one FRI query, about 2 bits of security. This verifier rejects it.
- `verify_claim` checks the claim with `assert!`, so a malformed claim panics instead of returning an error. Seen in the source; none of the cases below reach it yet.
- The library's binary loader reads the whole bzip2 stream and calls `bincode::deserialize` without a limit, so a small file can expand to gigabytes.
- Without the program hash, a valid proof of any program would pass.

The program hashes in `fixtures/pins.txt` were first read from honest proofs. `program_hash.py` recomputes them from the compiled programs without the prover (Blake2s over the encoded program words, as `get_verification_output` does), and `prove.sh` checks that they match.

## Running it

```
spike/stwo-verifier/check.sh   # build, check the fixtures' hashes, run every case
spike/stwo-verifier/prove.sh   # rebuild the prover at the pinned commit and regenerate the fixtures (about 15 GB of RAM)
```

Proving is deterministic: `prove.sh` reproduces `fixtures/SHA256SUMS` byte for byte.

## Results (19 Oct 2026, Apple M4 Pro)

| Program (stwo-cairo test_data) | JSON proof | Binary proof | Prove | Prover peak RSS |
|---|---|---|---|---|
| ret_opcode | 7.83 MB | 455 KB | 7.6 s | 13.3 GB |
| all_opcode_components | 11.5 MB | 870 KB | 15.5 s | 7.0 GB |

Whole-process verify with all pins, n = 20: ret_opcode 35 / 36 / 36 ms (min / median / max), all_opcode_components 65 / 65 / 67 ms. Peak RSS 9 MB and 15 MB.

On the Raspberry Pi 4 (2 GB, Pi OS, 19 Oct), same pins, n = 20: ret_opcode median 122 ms, all_opcode_components 254 ms, the same with one thread. Peak memory 5.6 MB and 8.6 MB (sampled), 22 MB for the bomb. That build used `nightly-2025-12-01` from the build VM instead of the pinned toolchain, and passed all 55 cases.

`cases.py` runs 55 cases: valid proofs, an output pin that differs, a proof of another program, the weak proof, garbage, empty, truncated, a flipped compressed byte, zeros or a second stream after the bzip2 stream, trailing payload bytes, a file over 8 MiB, a 1 GiB bzip2 bomb (stopped at 16 MiB, 21 MB peak RSS), a huge length prefix and 40 random payload bit flips. All get the expected exit code; none panics.

## Open question

On 1 Jul the Cairo verifier gained two checks in `verify_claim`: the output segment must start at the final `ap`, and the output's length goes into the builtin checks (commit `9b93cc3`, "validate output segment start and length"). The Rust verifier has neither, at the tag or on `main` (`verify_builtins` checks only `start_ptr <= stop_ptr` and `stop_ptr < 2^31`).

A tamper test (append or change a valid proof's claimed output, re-verify) shows the output section is bound into the Fiat-Shamir transcript: re-presenting a valid proof with a different claimed output is REJECTed. The values `get_verification_output` returns are placed at `final_ap` and bound to committed memory. Not shown (no local Cairo compiler to build an output-writing program): whether a malicious prover can emit a proof whose reported output segment range disagrees with the bound values. Until StarkWare confirms, a consumer must read outputs via the bound values at `final_ap`, not via the reported segment range.

## Not done

- An aarch64 build with the pinned toolchain.
- Fuzzing the claim fields to reach the `assert!` paths.
