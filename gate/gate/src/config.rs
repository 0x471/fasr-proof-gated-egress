use pge_core::{Cap, Mode, Policy};
use std::{
    collections::BTreeMap,
    net::{SocketAddr, ToSocketAddrs},
    path::PathBuf,
};

pub struct Config {
    pub listen: SocketAddr,
    pub server: SocketAddr,
    pub bundle: PathBuf,
    pub pins: PathBuf,
    pub domain: Option<PathBuf>,
    pub log: Option<PathBuf>,
    pub policy: Policy,
    pub proof_bytes: usize,
}
fn seconds(s: &str, allow_zero: bool, max: f64) -> Result<u64, String> {
    let v: f64 = s.parse().map_err(|_| "invalid time")?;
    if !v.is_finite() || v < 0. || (!allow_zero && v == 0.) || v > max {
        return Err("invalid time".into());
    }
    // Round upward so representing a fractional nanosecond never releases early.
    Ok((v * 1e9).ceil() as u64)
}
fn address(s: &str) -> Result<SocketAddr, String> {
    s.to_socket_addrs()
        .map_err(|_| "invalid address")?
        .next()
        .ok_or_else(|| "empty address".into())
}
impl Config {
    pub fn parse(args: impl IntoIterator<Item = String>) -> Result<Self, String> {
        let mut args = args.into_iter();
        let mut map = BTreeMap::new();
        let allowed = [
            "--listen",
            "--server",
            "--bundle",
            "--pins",
            "--domain",
            "--deadline",
            "--max-pending",
            "--halt-after",
            "--proof-bytes",
            "--log",
            "--spin-ms",
            "--release",
            "--cap-window",
            "--cap-failures",
            "--cap-cooldown",
        ];
        while let Some(arg) = args.next() {
            let (key, value) = if let Some((k, v)) = arg.split_once('=') {
                (k.to_owned(), v.to_owned())
            } else {
                (arg, args.next().ok_or("missing flag value")?)
            };
            if !allowed.contains(&key.as_str()) || map.insert(key, value).is_some() {
                return Err("unknown or repeated flag".into());
            }
        }
        let required = |k| {
            map.get(k)
                .map(String::as_str)
                .ok_or_else(|| format!("missing {k}"))
        };
        let get = |k, default| map.get(k).map(String::as_str).unwrap_or(default);
        let mode = match get("--release", "arrival") {
            "arrival" => Mode::Arrival,
            "fixed" => Mode::Fixed,
            _ => return Err("invalid release".into()),
        };
        let deadline = seconds(get("--deadline", "10"), false, 3600.)?;
        let spin_ms: f64 = get("--spin-ms", "0").parse().map_err(|_| "invalid spin")?;
        let spin = seconds(&(spin_ms / 1000.).to_string(), true, 0.05)?;
        let capacity = get("--max-pending", "32")
            .parse()
            .map_err(|_| "invalid capacity")?;
        let halt = map
            .get("--halt-after")
            .map(|s| s.parse::<u64>().map_err(|_| "invalid halt"))
            .transpose()?;
        let proof_bytes = required("--proof-bytes")?
            .parse::<usize>()
            .map_err(|_| "invalid proof length")?;
        if !(1..=65536).contains(&proof_bytes) {
            return Err("invalid proof length".into());
        }
        let mut policy =
            Policy::new(deadline, capacity, halt, mode, spin).map_err(str::to_owned)?;
        if let Some(window) = map.get("--cap-window") {
            let number = |k, default| {
                get(k, default)
                    .parse::<u32>()
                    .map_err(|_| "invalid cap".to_owned())
            };
            let window = window.parse::<u32>().map_err(|_| "invalid cap")?;
            let cap = Cap::new(
                window,
                number("--cap-failures", "0")?,
                number("--cap-cooldown", "1")?,
            )
            .map_err(str::to_owned)?;
            policy = policy.with_cap(cap);
        } else if map.contains_key("--cap-failures") || map.contains_key("--cap-cooldown") {
            return Err("cap flags need --cap-window".into());
        }
        Ok(Self {
            listen: address(required("--listen")?)?,
            server: address(required("--server")?)?,
            bundle: required("--bundle")?.into(),
            pins: required("--pins")?.into(),
            domain: map.get("--domain").map(PathBuf::from),
            log: map.get("--log").map(PathBuf::from),
            policy,
            proof_bytes,
        })
    }
}
