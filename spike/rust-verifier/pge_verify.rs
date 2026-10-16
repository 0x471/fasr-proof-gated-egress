//! Spike: the smallest EZKL 23.0.5 verifier, without the CLI's prover, setup and EVM code.
//! Usage: pge_verify SETTINGS VK SRS PROOF. Exit 0 = valid, 1 = invalid, 2 = could not check.
use ezkl::graph::{GraphCircuit, GraphSettings};
use ezkl::pfsys::{load_vk, srs::load_srs_verifier, verify_proof_circuit, Snark};
use halo2_proofs::poly::commitment::Params;
use halo2_proofs::poly::kzg::commitment::KZGCommitmentScheme;
use halo2_proofs::poly::kzg::multiopen::VerifierSHPLONK;
use halo2_proofs::poly::kzg::strategy::SingleStrategy;
use halo2curves::bn256::{Bn256, Fr, G1Affine};
use snark_verifier::system::halo2::transcript::evm::EvmTranscript;
use std::path::PathBuf;

fn run(a: &[String]) -> Result<Result<(), String>, String> {
    let settings = GraphSettings::load(&PathBuf::from(&a[0])).map_err(|e| format!("settings: {e}"))?;
    let logrows = settings.run_args.logrows;
    let mut params = load_srs_verifier::<KZGCommitmentScheme<Bn256>>(PathBuf::from(&a[2]))
        .map_err(|e| format!("srs: {e}"))?;
    if logrows < params.k() {
        params.downsize(logrows);
    }
    let vk = load_vk::<KZGCommitmentScheme<Bn256>, GraphCircuit>(PathBuf::from(&a[1]), settings)
        .map_err(|e| format!("vk: {e}"))?;
    let snark: Snark<Fr, G1Affine> = Snark::load::<KZGCommitmentScheme<Bn256>>(&PathBuf::from(&a[3]))
        .map_err(|e| format!("proof: {e}"))?;
    let strategy = SingleStrategy::new(&params);
    let r = verify_proof_circuit::<VerifierSHPLONK<'_, Bn256>, _, _, _, EvmTranscript<G1Affine, _, _, _>>(
        &snark, &params, &vk, strategy, 1 << logrows,
    );
    Ok(r.map(|_| ()).map_err(|e| e.to_string()))
}

fn main() {
    let a: Vec<String> = std::env::args().skip(1).collect();
    if a.len() != 4 {
        eprintln!("usage: pge_verify SETTINGS VK SRS PROOF");
        std::process::exit(2);
    }
    match run(&a) {
        Ok(Ok(())) => println!("verified: true"),
        Ok(Err(e)) => {
            println!("verified: false");
            eprintln!("[halo2] {e}");
            std::process::exit(1);
        }
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(2);
        }
    }
}
