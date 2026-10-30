//! Signed updates: which model the endpoint checks proofs for, and that this only ever moves forward.
//!
//! Three small text files, each with one fixed layout so there is exactly one way to write them:
//! a key list signed by the offline root key, a manifest signed by one key from that list, and the
//! device's state (the last applied manifest). Signatures are ECDSA over secp256k1 with SHA-256.
#![forbid(unsafe_code)]

pub use k256::ecdsa::VerifyingKey;
use k256::ecdsa::{signature::Verifier, Signature};
use sha2::{Digest, Sha256};

pub const FILES: [&str; 4] = ["settings.json", "vk.key", "kzg.srs", "domain.json"];
const MAX_TEXT: usize = 4096;
const MAX_KEYS: usize = 8;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    Format,
    Signature,
    Key,
    Device,
    Rollback,
    Replay,
    Bundle,
    State,
    Policy,
}
impl Refusal {
    pub fn word(self) -> &'static str {
        match self {
            Self::Format => "format",
            Self::Signature => "signature",
            Self::Key => "key",
            Self::Device => "device",
            Self::Rollback => "rollback",
            Self::Replay => "replay",
            Self::Bundle => "bundle",
            Self::State => "state",
            Self::Policy => "policy",
        }
    }
}
type Result<T> = std::result::Result<T, Refusal>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Manifest {
    pub device: String,
    pub version: u64,
    pub files: [String; 4],
    pub settings: Settings,
    pub key: String,
}

/// The endpoint's policy, signed with the model so it can't be changed or rolled back alone.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Settings {
    pub fixed: bool,
    pub deadline_ms: u64,
    pub spin_ms: u64,
    pub proof_bytes: u64,
    pub max_pending: u64,
    pub halt: Option<u64>,
    pub cap: Option<(u64, u64, u64)>,
}
impl Settings {
    /// The core policy and the exact proof length, or `Policy` if the values are out of range.
    pub fn policy(&self) -> Result<(pge_core::Policy, usize)> {
        let ms = |v: u64| v.checked_mul(1_000_000).ok_or(Refusal::Policy);
        let mode = if self.fixed {
            pge_core::Mode::Fixed
        } else {
            pge_core::Mode::Arrival
        };
        let capacity = usize::try_from(self.max_pending).map_err(|_| Refusal::Policy)?;
        let mut policy = pge_core::Policy::new(
            ms(self.deadline_ms)?,
            capacity,
            self.halt,
            mode,
            ms(self.spin_ms)?,
        )
        .map_err(|_| Refusal::Policy)?;
        if let Some((w, f, c)) = self.cap {
            let n = |v: u64| u32::try_from(v).map_err(|_| Refusal::Policy);
            policy = policy
                .with_cap(pge_core::Cap::new(n(w)?, n(f)?, n(c)?).map_err(|_| Refusal::Policy)?);
        }
        let proof_bytes = usize::try_from(self.proof_bytes).map_err(|_| Refusal::Policy)?;
        if !(1..=65536).contains(&proof_bytes) {
            return Err(Refusal::Policy);
        }
        Ok((policy, proof_bytes))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct State {
    pub version: u64,
    pub keys: u64,
    pub keys_digest: String,
    pub digest: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    Apply(State),
    Same,
}

pub fn sha256(data: &[u8]) -> String {
    Sha256::digest(data)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn lines<'a>(raw: &'a [u8], header: &str) -> Result<Vec<Vec<&'a str>>> {
    if raw.len() > MAX_TEXT || !raw.ends_with(b"\n") {
        return Err(Refusal::Format);
    }
    if !raw
        .iter()
        .all(|&c| c == b'\n' || c == b' ' || c.is_ascii_graphic())
    {
        return Err(Refusal::Format);
    }
    let text = std::str::from_utf8(raw).map_err(|_| Refusal::Format)?;
    let mut out: Vec<Vec<&str>> = Vec::new();
    for line in text[..text.len() - 1].split('\n') {
        let parts: Vec<&str> = line.split(' ').collect();
        if parts.iter().any(|p| p.is_empty()) {
            return Err(Refusal::Format);
        }
        out.push(parts);
    }
    if out.first().map(Vec::as_slice) != Some(&header.split(' ').collect::<Vec<_>>()[..]) {
        return Err(Refusal::Format);
    }
    Ok(out.split_off(1))
}

fn field<'a>(line: Option<&Vec<&'a str>>, name: &str, n: usize) -> Result<Vec<&'a str>> {
    match line {
        Some(parts) if parts.len() == n + 1 && parts[0] == name => Ok(parts[1..].to_vec()),
        _ => Err(Refusal::Format),
    }
}

fn number(s: &str) -> Result<u64> {
    let ok = !s.is_empty()
        && s.len() <= 19
        && s.bytes().all(|c| c.is_ascii_digit())
        && !s.starts_with('0');
    if !ok {
        return Err(Refusal::Format);
    }
    s.parse().map_err(|_| Refusal::Format)
}

fn number0(s: &str) -> Result<u64> {
    if s == "0" {
        Ok(0)
    } else {
        number(s)
    }
}

fn hex(s: &str, len: usize) -> Result<&str> {
    if s.len() == len
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        Ok(s)
    } else {
        Err(Refusal::Format)
    }
}

fn name(s: &str) -> Result<&str> {
    let ok = (1..=32).contains(&s.len())
        && s.bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-');
    if ok {
        Ok(s)
    } else {
        Err(Refusal::Format)
    }
}

fn bytes(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap_or(0))
        .collect()
}

pub fn public_key(text: &str) -> Result<VerifyingKey> {
    VerifyingKey::from_sec1_bytes(&bytes(hex(text, 66)?)).map_err(|_| Refusal::Format)
}

/// A signature file is 128 lowercase hex characters (r then s) and a newline.
pub fn check_signature(key: &VerifyingKey, message: &[u8], signature: &[u8]) -> Result<()> {
    let text = std::str::from_utf8(signature).map_err(|_| Refusal::Signature)?;
    let text = text.strip_suffix('\n').ok_or(Refusal::Signature)?;
    let raw = bytes(hex(text, 128).map_err(|_| Refusal::Signature)?);
    let sig = Signature::from_slice(&raw).map_err(|_| Refusal::Signature)?;
    key.verify(message, &sig).map_err(|_| Refusal::Signature)
}

pub struct Keys {
    pub version: u64,
    keys: Vec<(String, VerifyingKey)>,
}
impl Keys {
    /// The root signature is checked before anything in the list is read.
    pub fn verify(root: &VerifyingKey, raw: &[u8], signature: &[u8]) -> Result<Self> {
        check_signature(root, raw, signature)?;
        let lines = lines(raw, "pge-keys 1")?;
        let version = number(&field(lines.first(), "version", 1)?[0])?;
        let mut keys: Vec<(String, VerifyingKey)> = Vec::new();
        for line in &lines[1..] {
            let f = field(Some(line), "key", 2)?;
            let id = name(f[0])?;
            if keys.last().is_some_and(|(last, _)| last.as_str() >= id) {
                return Err(Refusal::Format);
            }
            keys.push((id.to_owned(), public_key(f[1])?));
        }
        if keys.is_empty() || keys.len() > MAX_KEYS {
            return Err(Refusal::Format);
        }
        Ok(Self { version, keys })
    }
    fn get(&self, id: &str) -> Option<&VerifyingKey> {
        self.keys.iter().find(|(k, _)| k == id).map(|(_, v)| v)
    }
}

impl Manifest {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let lines = lines(raw, "pge-update 1")?;
        if lines.len() != 14 {
            return Err(Refusal::Format);
        }
        let device = name(field(lines.first(), "device", 1)?[0])?.to_owned();
        let version = number(field(lines.get(1), "version", 1)?[0])?;
        let mut files: [String; 4] = Default::default();
        for (i, f) in FILES.iter().enumerate() {
            files[i] = hex(field(lines.get(2 + i), f, 1)?[0], 64)?.to_owned();
        }
        let fixed = match field(lines.get(6), "release", 1)?[0] {
            "arrival" => false,
            "fixed" => true,
            _ => return Err(Refusal::Format),
        };
        let one = |i: usize, k: &str| -> Result<u64> { number(field(lines.get(i), k, 1)?[0]) };
        let deadline_ms = one(7, "deadline-ms")?;
        let spin_ms = number0(field(lines.get(8), "spin-ms", 1)?[0])?;
        let proof_bytes = one(9, "proof-bytes")?;
        let max_pending = one(10, "max-pending")?;
        let halt = match field(lines.get(11), "halt", 1)?[0] {
            "none" => None,
            n => Some(number(n)?),
        };
        let cap = match lines.get(12).map(Vec::as_slice) {
            Some(["cap", "none"]) => None,
            Some(["cap", w, f, c]) => Some((number(w)?, number0(f)?, number(c)?)),
            _ => return Err(Refusal::Format),
        };
        let key = name(field(lines.get(13), "key", 1)?[0])?.to_owned();
        Ok(Self {
            device,
            version,
            files,
            settings: Settings {
                fixed,
                deadline_ms,
                spin_ms,
                proof_bytes,
                max_pending,
                halt,
                cap,
            },
            key,
        })
    }
}

impl State {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let lines = lines(raw, "pge-state 1")?;
        if lines.len() != 4 {
            return Err(Refusal::Format);
        }
        Ok(Self {
            version: number(field(lines.first(), "version", 1)?[0])?,
            keys: number(field(lines.get(1), "keys", 1)?[0])?,
            keys_digest: hex(field(lines.get(2), "keys-digest", 1)?[0], 64)?.to_owned(),
            digest: hex(field(lines.get(3), "digest", 1)?[0], 64)?.to_owned(),
        })
    }
    pub fn text(&self) -> String {
        format!(
            "pge-state 1\nversion {}\nkeys {}\nkeys-digest {}\ndigest {}\n",
            self.version, self.keys, self.keys_digest, self.digest
        )
    }
}

pub struct Update<'a> {
    pub keys: &'a [u8],
    pub keys_signature: &'a [u8],
    pub manifest: &'a [u8],
    pub manifest_signature: &'a [u8],
    /// The bundle files and the domain, in the order of FILES.
    pub files: [&'a [u8]; 4],
}

/// Decides whether this device may move to the update. Nothing is written here; the caller
/// applies the files and then writes `State::text()` of the returned state.
pub fn decide(
    root: &VerifyingKey,
    device: &str,
    state: Option<&State>,
    update: &Update,
) -> Result<Outcome> {
    let keys = Keys::verify(root, update.keys, update.keys_signature)?;
    let manifest = Manifest::parse(update.manifest)?;
    let key = keys.get(&manifest.key).ok_or(Refusal::Key)?;
    check_signature(key, update.manifest, update.manifest_signature)?;
    if manifest.device != device {
        return Err(Refusal::Device);
    }
    let digest = sha256(update.manifest);
    let keys_digest = sha256(update.keys);
    if let Some(s) = state {
        if keys.version < s.keys || manifest.version < s.version {
            return Err(Refusal::Rollback);
        }
        // One key list per version: a different list under a recorded version is refused.
        if keys.version == s.keys && keys_digest != s.keys_digest {
            return Err(Refusal::Replay);
        }
        if manifest.version == s.version {
            if digest != s.digest {
                return Err(Refusal::Replay);
            }
            if keys.version == s.keys {
                return Ok(Outcome::Same);
            }
            // The same manifest with a newer key list rotates or revokes keys: apply it.
        }
    }
    manifest.settings.policy()?;
    if update
        .files
        .iter()
        .zip(&manifest.files)
        .any(|(data, want)| sha256(data) != *want)
    {
        return Err(Refusal::Bundle);
    }
    Ok(Outcome::Apply(State {
        version: manifest.version,
        keys: keys.version,
        keys_digest,
        digest,
    }))
}

/// At start-up: the stored update must be exactly the one the state records, still signed by a
/// key the root lists, for this device, with every file unchanged since it was applied.
pub fn active(
    root: &VerifyingKey,
    device: &str,
    state: &State,
    update: &Update,
) -> Result<Manifest> {
    let keys = Keys::verify(root, update.keys, update.keys_signature)?;
    let manifest = Manifest::parse(update.manifest)?;
    let key = keys.get(&manifest.key).ok_or(Refusal::Key)?;
    check_signature(key, update.manifest, update.manifest_signature)?;
    if manifest.device != device {
        return Err(Refusal::Device);
    }
    if keys.version != state.keys
        || sha256(update.keys) != state.keys_digest
        || manifest.version != state.version
        || sha256(update.manifest) != state.digest
    {
        return Err(Refusal::State);
    }
    manifest.settings.policy()?;
    if update
        .files
        .iter()
        .zip(&manifest.files)
        .any(|(data, want)| sha256(data) != *want)
    {
        return Err(Refusal::Bundle);
    }
    Ok(manifest)
}
