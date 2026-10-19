//! Spike: a pinned S-two Cairo proof verifier for the device (stwo-cairo sharp-7.4).
//! Usage: pge_stwo_verify PROOF.bin PROGRAM_HASH [OUTPUT_FELT,...]
//! PROOF is run_and_prove's binary format (bincode, bzip2). Hashes and felts are 0x-hex.
//! Exit 0 = valid, 1 = invalid or not what we pinned, 2 = could not check, 3 = verifier panicked.
use std::io::Read;
use std::panic::{catch_unwind, AssertUnwindSafe};

use bincode::Options;
use bzip2::bufread::BzDecoder;
use cairo_air::utils::get_verification_output;
use cairo_air::verifier::verify_cairo;
use cairo_air::CairoProofForRustVerifier;
use starknet_ff::FieldElement;
use stwo::core::fri::FriConfig;
use stwo::core::pcs::PcsConfig;
use stwo::core::vcs_lifted::blake2_merkle::{Blake2sMerkleChannel, Blake2sMerkleHasher};
use stwo_cairo_common::preprocessed_columns::preprocessed_trace::PreProcessedTraceVariant;

const MAX_FILE_BYTES: u64 = 8 << 20;
// The library decodes without limits; a small bzip2 file can expand to gigabytes.
const MAX_RAW_BYTES: u64 = 16 << 20;

// The prover's default prod parameters (96 bits: pow_bits + log_blowup_factor * n_queries).
// verify_cairo reads the config from the proof itself, so it has to be pinned here.
const PINNED_CONFIG: PcsConfig = PcsConfig {
    pow_bits: 26,
    fri_config: FriConfig {
        log_blowup_factor: 1,
        log_last_layer_degree_bound: 0,
        n_queries: 70,
        fold_step: 1,
    },
    lifting_log_size: None,
};
const PINNED_SALT: u32 = 0;

type Proof = CairoProofForRustVerifier<Blake2sMerkleHasher>;

enum Outcome {
    Valid,
    Invalid(String),
    Unchecked(String),
}

fn read_limited<R: Read>(r: R, max: u64, what: &str) -> Result<Vec<u8>, String> {
    let mut buf = Vec::new();
    r.take(max + 1).read_to_end(&mut buf).map_err(|e| format!("{what}: {e}"))?;
    if buf.len() as u64 > max {
        return Err(format!("{what} larger than {max} bytes"));
    }
    Ok(buf)
}

fn load(path: &str) -> Result<Proof, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("open: {e}"))?;
    let packed = read_limited(file, MAX_FILE_BYTES, "proof file")?;
    let mut dec = BzDecoder::new(packed.as_slice());
    let raw = read_limited(&mut dec, MAX_RAW_BYTES, "decompressed proof")?;
    // The decoder stops after one bzip2 stream; anything after it would be ignored.
    if !dec.into_inner().is_empty() {
        return Err("bytes after the compressed proof".into());
    }
    // Same encoding as bincode::serialize, plus a size limit and no trailing bytes.
    bincode::options()
        .with_fixint_encoding()
        .with_limit(MAX_RAW_BYTES)
        .reject_trailing_bytes()
        .deserialize(&raw)
        .map_err(|e| format!("decode: {e}"))
}

fn parse_felt(s: &str) -> Result<FieldElement, String> {
    FieldElement::from_hex_be(s).map_err(|_| format!("bad felt {s}"))
}

fn check(a: &[String]) -> Outcome {
    let want_program = match parse_felt(&a[1]) {
        Ok(f) => f,
        Err(e) => return Outcome::Unchecked(e),
    };
    let want_output: Option<Vec<FieldElement>> = match a.get(2) {
        None => None,
        Some(s) if s.is_empty() => Some(vec![]),
        Some(s) => match s.split(',').map(parse_felt).collect() {
            Ok(v) => Some(v),
            Err(e) => return Outcome::Unchecked(e),
        },
    };
    let proof = match load(&a[0]) {
        Ok(p) => p,
        Err(e) => return Outcome::Unchecked(e),
    };
    if proof.stark_proof.config != PINNED_CONFIG {
        return Outcome::Invalid(format!("config not pinned: {:?}", proof.stark_proof.config));
    }
    if proof.channel_salt != PINNED_SALT {
        return Outcome::Invalid(format!("channel salt {} not pinned", proof.channel_salt));
    }
    if !matches!(proof.preprocessed_trace_variant, PreProcessedTraceVariant::Canonical) {
        return Outcome::Invalid("preprocessed trace variant not pinned".into());
    }
    let out = get_verification_output(&proof.claim.public_data.public_memory);
    if out.program_hash != want_program {
        return Outcome::Invalid(format!("program {:#x} not pinned", out.program_hash));
    }
    if let Some(want) = want_output {
        if out.output != want {
            return Outcome::Invalid("output not pinned".into());
        }
    }
    match verify_cairo::<Blake2sMerkleChannel>(proof) {
        Ok(()) => Outcome::Valid,
        Err(e) => Outcome::Invalid(e.to_string()),
    }
}

fn main() {
    let a: Vec<String> = std::env::args().skip(1).collect();
    if a.len() != 2 && a.len() != 3 {
        eprintln!("usage: pge_stwo_verify PROOF.bin PROGRAM_HASH [OUTPUT_FELT,...]");
        std::process::exit(2);
    }
    std::panic::set_hook(Box::new(|_| {}));
    match catch_unwind(AssertUnwindSafe(|| check(&a))) {
        Ok(Outcome::Valid) => println!("verified: true"),
        Ok(Outcome::Invalid(e)) => {
            println!("verified: false");
            eprintln!("[stwo] {e}");
            std::process::exit(1);
        }
        Ok(Outcome::Unchecked(e)) => {
            eprintln!("error: {e}");
            std::process::exit(2);
        }
        Err(_) => {
            eprintln!("error: verifier panicked");
            std::process::exit(3);
        }
    }
}
