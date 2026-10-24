mod config;
mod network;
mod process;
mod service;

use pge_verify::{Bundle, Pins, Verdict};
use std::{
    io::{self, Write},
    process::{Command, ExitCode},
    sync::Arc,
    time::{Duration, Instant},
};

fn verify_job(bundle: Arc<Bundle>, data: Vec<u8>, budget: Duration, proof_bytes: usize) -> Verdict {
    let start = Instant::now();
    let mut input = Vec::new();
    if bundle.write_job(&data, &mut input).is_err() {
        return Verdict::Error;
    }
    let Ok(exe) = std::env::current_exe() else {
        return Verdict::Error;
    };
    let Some(left) = budget.checked_sub(start.elapsed()).filter(|d| !d.is_zero()) else {
        return Verdict::Error;
    };
    let mut cmd = Command::new(exe);
    cmd.args([
        "--verify-child",
        &left.as_secs_f64().ceil().max(1.).to_string(),
        &proof_bytes.to_string(),
    ]);
    cmd.args(&bundle.pins.0);
    process::run(cmd, left, &input)
}

fn child(args: &[String]) -> ExitCode {
    if args.len() != 6 {
        return ExitCode::from(2);
    }
    let (Ok(cpu), Ok(proof_bytes)) = (args[1].parse::<u64>(), args[2].parse::<usize>()) else {
        return ExitCode::from(2);
    };
    if process::limits(cpu).is_err() {
        return ExitCode::from(2);
    }
    let pins = Pins([args[3].clone(), args[4].clone(), args[5].clone()]);
    let Ok((bundle, raw)) = Bundle::read_job(&mut io::stdin().lock(), pins) else {
        return ExitCode::from(2);
    };
    let (code, word) = match pge_verify::check_proof(&bundle, &raw, proof_bytes) {
        Verdict::Accept => (0, b"accept\n".as_slice()),
        Verdict::Reject => (1, b"reject\n".as_slice()),
        Verdict::Error => (2, b"error\n".as_slice()),
    };
    if io::stdout().lock().write_all(word).is_err() {
        return ExitCode::from(2);
    }
    ExitCode::from(code)
}

fn main() -> ExitCode {
    let args: std::result::Result<Vec<_>, _> = std::env::args_os()
        .skip(1)
        .map(|a| a.into_string())
        .collect();
    let Ok(args) = args else {
        eprintln!("arguments must be UTF-8");
        return ExitCode::from(2);
    };
    if args.first().map(String::as_str) == Some("--verify-child") {
        return child(&args);
    }
    if args == ["--help"] {
        println!("pge-gate --listen HOST:PORT --server HOST:PORT --bundle DIR --pins FILE --proof-bytes N [--domain FILE] [--deadline SECONDS] [--max-pending N] [--halt-after N] [--log FILE] [--spin-ms MS] [--release arrival|fixed]");
        return ExitCode::SUCCESS;
    }
    match config::Config::parse(args).and_then(service::run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("gate startup/runtime error: {e}");
            ExitCode::from(2)
        }
    }
}
