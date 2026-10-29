//! Offline signing tool for update files; it never runs on the endpoint.
//!   pge-sign KEYFILE < FILE > FILE.sig     sign FILE with the secret key in KEYFILE
//!   pge-sign --public KEYFILE              print the public key as the key list expects it
//! KEYFILE holds a secret scalar as 64 lowercase hex characters and a newline.
use k256::ecdsa::{signature::Signer, Signature, SigningKey};
use std::{
    io::{self, Read, Write},
    process::ExitCode,
};

fn key(path: &str) -> Option<SigningKey> {
    let text = std::fs::read_to_string(path).ok()?;
    let text = text.strip_suffix('\n')?;
    if text.len() != 64
        || !text
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return None;
    }
    let raw: Vec<u8> = (0..64)
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap_or(0))
        .collect();
    SigningKey::from_slice(&raw).ok()
}

fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect()
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (public, path) = match args.as_slice() {
        [flag, path] if flag == "--public" => (true, path),
        [path] => (false, path),
        _ => {
            eprintln!("usage: pge-sign [--public] KEYFILE");
            return ExitCode::from(2);
        }
    };
    let Some(signing) = key(path) else {
        eprintln!("pge-sign: bad key file");
        return ExitCode::from(2);
    };
    let out = if public {
        hex(&signing.verifying_key().to_encoded_point(true).to_bytes())
    } else {
        let mut message = Vec::new();
        if io::stdin().read_to_end(&mut message).is_err() {
            return ExitCode::from(2);
        }
        let sig: Signature = signing.sign(&message);
        hex(&sig.to_bytes())
    };
    if writeln!(io::stdout(), "{out}").is_err() {
        return ExitCode::from(2);
    }
    ExitCode::SUCCESS
}
