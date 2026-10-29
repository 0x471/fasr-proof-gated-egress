#![forbid(unsafe_code)]

use num_bigint::{BigInt, Sign};
use num_traits::{Signed, ToPrimitive};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Cursor, Read, Write},
    os::unix::fs::OpenOptionsExt,
    path::Path,
};

pub type Result<T> = std::result::Result<T, &'static str>;
pub const MAX_PROOF_JSON: usize = 1 << 20;
const FILES: [&str; 3] = ["settings.json", "vk.key", "kzg.srs"];
pub const BUNDLE_LIMITS: [usize; 3] = [1 << 20, 64 << 20, 64 << 20];

pub fn read_regular(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(path)
        .map_err(|_| "file open")?;
    if !file.metadata().map_err(|_| "file metadata")?.is_file() {
        return Err("not a regular file");
    }
    let mut out = Vec::new();
    file.take(limit as u64 + 1)
        .read_to_end(&mut out)
        .map_err(|_| "file read")?;
    if out.len() > limit {
        return Err("file size");
    }
    Ok(out)
}

pub fn digest(data: &[u8]) -> String {
    hex(&Sha256::digest(data))
}
pub fn hex(data: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut s = String::with_capacity(data.len().saturating_mul(2));
    for b in data {
        s.push(DIGITS[(b >> 4) as usize] as char);
        s.push(DIGITS[(b & 15) as usize] as char);
    }
    s
}
fn modulus() -> BigInt {
    BigInt::from_bytes_le(
        Sign::Plus,
        &[
            1, 0, 0, 240, 147, 245, 225, 67, 145, 112, 185, 121, 72, 232, 51, 40, 93, 88, 129, 129,
            182, 69, 80, 184, 41, 160, 49, 225, 114, 78, 100, 48,
        ],
    )
}
pub fn parse_hex(s: &str) -> Result<[u8; 32]> {
    if s.len() != 64
        || !s
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err("field encoding");
    }
    let mut bytes = [0; 32];
    for (i, b) in bytes.iter_mut().enumerate() {
        *b = u8::from_str_radix(&s[2 * i..2 * i + 2], 16).map_err(|_| "field encoding")?;
    }
    canonical(&bytes)?;
    Ok(bytes)
}
pub fn canonical(f: &[u8; 32]) -> Result<BigInt> {
    let n = BigInt::from_bytes_le(Sign::Plus, f);
    if n >= modulus() {
        Err("noncanonical field")
    } else {
        Ok(n)
    }
}
pub fn felt(n: i64) -> [u8; 32] {
    let n = if n < 0 {
        modulus() + n
    } else {
        BigInt::from(n)
    };
    let bytes = n.to_bytes_le().1;
    let mut out = [0; 32];
    out[..bytes.len()].copy_from_slice(&bytes);
    out
}
pub fn quantize(x: f64, scale: u32, f32_input: bool) -> Result<i64> {
    if scale > 30 || !x.is_finite() {
        return Err("input");
    }
    let v = if f32_input { (x as f32) as f64 } else { x };
    let scaled = v * 2f64.powi(scale as i32);
    if !scaled.is_finite() || scaled.abs() >= 2f64.powi(63) {
        return Err("input range");
    }
    Ok(scaled.round() as i64)
}

#[derive(Debug, Clone)]
pub struct Spec {
    pub input_lens: Vec<usize>,
    pub output_lens: Vec<usize>,
    pub input_scales: Vec<u32>,
    pub output_scales: Vec<u32>,
    pub f32_inputs: Vec<bool>,
}
fn array<'a>(s: &'a Value, name: &str) -> Result<&'a Vec<Value>> {
    s[name]
        .as_array()
        .filter(|v| !v.is_empty())
        .ok_or("missing settings field")
}
fn scales(s: &Value, name: &str) -> Result<Vec<u32>> {
    array(s, name)?
        .iter()
        .map(|v| {
            v.as_u64()
                .filter(|n| *n <= 30)
                .map(|n| n as u32)
                .ok_or("scale")
        })
        .collect()
}
impl Spec {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let s: Value = serde_json::from_slice(raw).map_err(|_| "settings JSON")?;
        if s["version"] != "23.0.5"
            || s["run_args"]["input_visibility"] != "Public"
            || s["run_args"]["output_visibility"] != "Public"
            || s["run_args"]["param_visibility"] != "Fixed"
        {
            return Err("settings policy");
        }
        let input_scales = scales(&s, "model_input_scales")?;
        let output_scales = scales(&s, "model_output_scales")?;
        let f32_inputs: Vec<bool> = array(&s, "input_types")?
            .iter()
            .map(|t| match t.as_str() {
                Some("F32") => Ok(true),
                Some("F64") => Ok(false),
                _ => Err("input type"),
            })
            .collect::<Result<_>>()?;
        let shapes = array(&s, "model_instance_shapes")?;
        if f32_inputs.len() != input_scales.len()
            || shapes.len()
                != input_scales
                    .len()
                    .checked_add(output_scales.len())
                    .ok_or("shape")?
        {
            return Err("shape");
        }
        let mut lens = Vec::new();
        for shape in shapes {
            let dims = shape.as_array().filter(|d| !d.is_empty()).ok_or("shape")?;
            let mut n = 1usize;
            for d in dims {
                let d = d
                    .as_u64()
                    .filter(|d| (1..=65536).contains(d))
                    .ok_or("dimension")? as usize;
                n = n
                    .checked_mul(d)
                    .filter(|n| *n <= 65536)
                    .ok_or("tensor too large")?;
            }
            lens.push(n);
        }
        let output_lens = lens.split_off(input_scales.len());
        let spec = Self {
            input_lens: lens,
            output_lens,
            input_scales,
            output_scales,
            f32_inputs,
        };
        spec.wire()?;
        Ok(spec)
    }
    pub fn wire(&self) -> Result<pge_wire::Wire> {
        let sum = |v: &[usize]| {
            v.iter()
                .try_fold(0usize, |a, b| a.checked_add(*b))
                .ok_or("shape overflow")
        };
        pge_wire::Wire::new(sum(&self.input_lens)?, sum(&self.output_lens)?)
            .map_err(|_| "wire size")
    }
    pub fn quantize(&self, x: &[f64]) -> Result<Vec<i64>> {
        let mut out = Vec::new();
        let mut offset: usize = 0;
        for ((n, scale), f32_input) in self
            .input_lens
            .iter()
            .zip(&self.input_scales)
            .zip(&self.f32_inputs)
        {
            let end = offset.checked_add(*n).ok_or("input shape")?;
            for v in x.get(offset..end).ok_or("input shape")? {
                out.push(quantize(*v, *scale, *f32_input)?);
            }
            offset = end;
        }
        if offset != x.len() {
            return Err("input shape");
        }
        Ok(out)
    }
    pub fn request_hash(&self, n: &[i64]) -> Result<String> {
        let mut offset: usize = 0;
        let mut tensors = Vec::new();
        for count in &self.input_lens {
            let end = offset.checked_add(*count).ok_or("input shape")?;
            let values: Vec<_> = n
                .get(offset..end)
                .ok_or("input shape")?
                .iter()
                .map(|v| format!("\"{}\"", hex(&felt(*v))))
                .collect();
            tensors.push(format!("[{}]", values.join(", ")));
            offset = end;
        }
        if offset != n.len() {
            return Err("input shape");
        }
        Ok(digest(format!("[{}]", tensors.join(", ")).as_bytes()))
    }
    pub fn bind(
        &self,
        x: &[f64],
        outputs: &[[u8; 32]],
        proof: &[u8],
        proof_bytes: usize,
    ) -> Result<(Vec<u8>, Vec<f64>)> {
        if proof.is_empty() || proof.len() != proof_bytes || proof.len() > pge_wire::PROOF_MAX {
            return Err("proof_length");
        }
        let mut instances: Vec<_> = self
            .quantize(x)?
            .into_iter()
            .map(|n| hex(&felt(n)))
            .collect();
        let mut y = Vec::new();
        let mut offset: usize = 0;
        for (n, scale) in self.output_lens.iter().zip(&self.output_scales) {
            let end = offset.checked_add(*n).ok_or("binding")?;
            for f in outputs.get(offset..end).ok_or("binding")? {
                let mut v = canonical(f)?;
                if v > modulus() / 2 {
                    v -= modulus();
                }
                y.push(v.to_f64().ok_or("binding")? / 2f64.powi(*scale as i32));
                instances.push(hex(f));
            }
            offset = end;
        }
        if offset != outputs.len() {
            return Err("binding");
        }
        let data = serde_json::to_vec(&json!({"instances": [instances], "proof": proof}))
            .map_err(|_| "binding")?;
        if data.len() > MAX_PROOF_JSON {
            return Err("binding");
        }
        Ok((data, y))
    }
}

pub struct Domain {
    wq: Vec<Vec<BigInt>>,
    bq: Vec<BigInt>,
    bound: BigInt,
}
impl Domain {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let d: Value = serde_json::from_slice(raw).map_err(|_| "domain JSON")?;
        if d.as_object().map(|d| d.len()) != Some(3) {
            return Err("domain fields");
        }
        let integer = |v: &Value| -> Result<BigInt> {
            let s = v.as_number().ok_or("domain integer")?.to_string();
            s.parse().map_err(|_| "domain integer")
        };
        let bq: Vec<_> = array(&d, "bq")?
            .iter()
            .map(integer)
            .collect::<Result<_>>()?;
        let wq: Vec<Vec<_>> = array(&d, "wq")?
            .iter()
            .map(|r| {
                let r = r
                    .as_array()
                    .filter(|r| r.len() == bq.len())
                    .ok_or("domain shape")?;
                r.iter().map(integer).collect()
            })
            .collect::<Result<_>>()?;
        let bound = integer(&d["bound"])?;
        if bound <= BigInt::from(0) {
            return Err("domain bound");
        }
        Ok(Self { wq, bq, bound })
    }
    pub fn admits(&self, n: &[i64]) -> bool {
        if n.len() != self.wq.len() || n.iter().any(|v| BigInt::from(*v).abs() > self.bound) {
            return false;
        }
        self.bq.iter().enumerate().all(|(j, b)| {
            let z = n
                .iter()
                .zip(&self.wq)
                .fold(b.clone(), |z, (v, row)| z + *v * &row[j]);
            z.abs() <= self.bound
        })
    }
}

#[derive(Clone, Debug)]
pub struct Pins(pub [String; 3]);
impl Pins {
    pub fn parse(raw: &str) -> Result<Self> {
        let mut pins = [String::new(), String::new(), String::new()];
        for line in raw.lines() {
            let (h, name) = line.split_once("  ").ok_or("pins format")?;
            if let Some(i) = FILES.iter().position(|f| *f == name) {
                if !pins[i].is_empty()
                    || h.len() != 64
                    || !h
                        .bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                {
                    return Err("pins format");
                }
                pins[i] = h.to_owned();
            }
        }
        if pins.iter().any(String::is_empty) {
            return Err("pins missing");
        }
        Ok(Self(pins))
    }
    pub fn check(&self, raw: &[Vec<u8>; 3]) -> Result<()> {
        for (bytes, pin) in raw.iter().zip(&self.0) {
            if digest(bytes) != *pin {
                return Err("bundle pin");
            }
        }
        Ok(())
    }
    pub fn text(&self) -> String {
        self.0
            .iter()
            .zip(FILES)
            .map(|(p, f)| format!("{p}  {f}\n"))
            .collect()
    }
}
pub struct Bundle {
    pub spec: Spec,
    pub pins: Pins,
    raw: [Vec<u8>; 3],
}
impl Bundle {
    pub fn load(root: &Path, pins: Pins) -> Result<Self> {
        let mut raw = [Vec::new(), Vec::new(), Vec::new()];
        for (i, name) in FILES.iter().enumerate() {
            raw[i] = read_regular(&root.join(name), BUNDLE_LIMITS[i])?;
        }
        Self::from_bytes(raw, pins)
    }
    pub fn from_bytes(raw: [Vec<u8>; 3], pins: Pins) -> Result<Self> {
        for (r, limit) in raw.iter().zip(BUNDLE_LIMITS) {
            if r.len() > limit {
                return Err("bundle size");
            }
        }
        pins.check(&raw)?;
        let spec = Spec::parse(&raw[0])?;
        Ok(Self { spec, pins, raw })
    }
    pub fn check(&self) -> Result<()> {
        self.pins.check(&self.raw)
    }
    // The child re-hashes everything it receives, so a changed snapshot fails there.
    pub fn write_job(&self, proof: &[u8], out: &mut impl Write) -> Result<()> {
        if proof.len() > MAX_PROOF_JSON {
            return Err("proof size");
        }
        for raw in self
            .raw
            .iter()
            .map(Vec::as_slice)
            .chain(std::iter::once(proof))
        {
            let n = u32::try_from(raw.len()).map_err(|_| "job size")?;
            out.write_all(&n.to_be_bytes())
                .and_then(|_| out.write_all(raw))
                .map_err(|_| "job write")?;
        }
        Ok(())
    }
    pub fn read_job(input: &mut impl Read, pins: Pins) -> Result<(Self, Vec<u8>)> {
        let read = |input: &mut dyn Read, limit: usize| -> Result<Vec<u8>> {
            let mut head = [0; 4];
            input.read_exact(&mut head).map_err(|_| "job header")?;
            let n = u32::from_be_bytes(head) as usize;
            if n > limit {
                return Err("job size");
            }
            let mut raw = vec![0; n];
            input.read_exact(&mut raw).map_err(|_| "job body")?;
            Ok(raw)
        };
        let raw = [
            read(input, BUNDLE_LIMITS[0])?,
            read(input, BUNDLE_LIMITS[1])?,
            read(input, BUNDLE_LIMITS[2])?,
        ];
        let proof = read(input, MAX_PROOF_JSON)?;
        let mut end = [0; 1];
        if input.read(&mut end).map_err(|_| "job end")? != 0 {
            return Err("job tail");
        }
        Ok((Self::from_bytes(raw, pins)?, proof))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Accept,
    Reject,
    Error,
}

pub fn canonical_proof(raw: &[u8]) -> Result<Value> {
    if raw.len() > MAX_PROOF_JSON {
        return Err("proof size");
    }
    let v: Value = serde_json::from_slice(raw).map_err(|_| "proof JSON")?;
    let proof = v["proof"].as_array().ok_or("proof bytes")?;
    if !proof.iter().all(|v| v.as_u64().is_some_and(|n| n <= 255)) {
        return Err("proof bytes");
    }
    let cols = v["instances"].as_array().ok_or("instances")?;
    for col in cols {
        for f in col.as_array().ok_or("column")? {
            parse_hex(f.as_str().ok_or("field")?)?;
        }
    }
    Ok(json!({"instances": cols, "proof": proof}))
}

// Run only in the isolated child: dependency deserializers and curve code may panic.
pub fn check_proof(bundle: &Bundle, raw: &[u8], proof_bytes: usize) -> Verdict {
    match checked_proof(bundle, raw, proof_bytes) {
        Ok(v) => v,
        Err(_) => Verdict::Error,
    }
}
fn checked_proof(bundle: &Bundle, raw: &[u8], proof_bytes: usize) -> Result<Verdict> {
    use ezkl::{
        graph::{GraphCircuit, GraphSettings},
        pfsys::{verify_proof_circuit, Snark},
    };
    use halo2_proofs::{
        plonk::VerifyingKey,
        poly::{
            commitment::Params,
            kzg::{commitment::ParamsKZG, multiopen::VerifierSHPLONK, strategy::SingleStrategy},
        },
        SerdeFormat,
    };
    use halo2curves::bn256::{Bn256, Fr, G1Affine};
    use snark_verifier::system::halo2::transcript::evm::EvmTranscript;
    let v = match canonical_proof(raw) {
        Ok(v) => v,
        Err(_) => return Ok(Verdict::Reject),
    };
    if v["proof"].as_array().map(Vec::len) != Some(proof_bytes)
        || proof_bytes == 0
        || proof_bytes > pge_wire::PROOF_MAX
    {
        return Ok(Verdict::Reject);
    }
    let snark: Snark<Fr, G1Affine> = serde_json::from_value(v).map_err(|_| "proof decode")?;
    let settings: GraphSettings = serde_json::from_slice(&bundle.raw[0]).map_err(|_| "settings")?;
    let logrows = settings.run_args.logrows;
    let orig_n = 1u64.checked_shl(logrows).ok_or("logrows")?;
    let mut params =
        ParamsKZG::<Bn256>::read(&mut Cursor::new(&bundle.raw[2])).map_err(|_| "srs")?;
    if logrows > params.k() {
        return Err("srs too small");
    }
    if logrows < params.k() {
        params.downsize(logrows);
    }
    let vk = VerifyingKey::<G1Affine>::read::<_, GraphCircuit>(
        &mut Cursor::new(&bundle.raw[1]),
        SerdeFormat::RawBytes,
        settings,
    )
    .map_err(|_| "vk")?;
    let strategy = SingleStrategy::new(&params);
    Ok(
        match verify_proof_circuit::<
            VerifierSHPLONK<'_, Bn256>,
            _,
            _,
            _,
            EvmTranscript<G1Affine, _, _, _>,
        >(&snark, &params, &vk, strategy, orig_n)
        {
            Ok(_) => Verdict::Accept,
            Err(_) => Verdict::Reject,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tiny_binding_vector() {
        let spec =
            Spec::parse(include_bytes!("../../../tests/fixtures/tiny/settings.json")).unwrap();
        let x = [0.5, -0.2, 0.9, 0.1];
        assert_eq!(spec.quantize(&x).unwrap(), [4096, -1638, 7373, 819]);
        let proof: serde_json::Value =
            serde_json::from_slice(include_bytes!("../../../tests/fixtures/tiny/proof.json"))
                .unwrap();
        let outputs: Vec<_> = proof["instances"][0].as_array().unwrap()[4..]
            .iter()
            .map(|f| parse_hex(f.as_str().unwrap()).unwrap())
            .collect();
        let (data, y) = spec.bind(&x, &outputs, &[0; 3072], 3072).unwrap();
        assert_eq!(y, [0.9666305631399155, 0.0, 0.0]);
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&data).unwrap()["instances"],
            proof["instances"]
        );
    }
}
