// The update store: STORE/state records the applied update and STORE/active holds its files.
// Updates are applied only by `pge-endpoint --apply-update`, never while the endpoint serves, so
// every answer is checked under one state.
use pge_update::{Outcome, Refusal, State, Update, FILES};
use pge_verify::{read_regular, Pins};
use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::ExitCode,
};

const META: [&str; 4] = ["keys", "keys.sig", "manifest", "manifest.sig"];
const LIMITS: [usize; 4] = [1 << 20, 64 << 20, 64 << 20, 1 << 20];

pub struct Active {
    pub dir: PathBuf,
    pub pins: Pins,
    pub domain: Vec<u8>,
    pub version: u64,
    pub policy: pge_core::Policy,
    pub proof_bytes: usize,
}

struct Files {
    meta: [Vec<u8>; 4],
    files: [Vec<u8>; 4],
}
impl Files {
    fn read(dir: &Path) -> Result<Self, String> {
        let mut meta: [Vec<u8>; 4] = Default::default();
        for (i, name) in META.iter().enumerate() {
            meta[i] = read_regular(&dir.join(name), 4096).map_err(|_| format!("read {name}"))?;
        }
        let mut files: [Vec<u8>; 4] = Default::default();
        for (i, name) in FILES.iter().enumerate() {
            files[i] =
                read_regular(&dir.join(name), LIMITS[i]).map_err(|_| format!("read {name}"))?;
        }
        Ok(Self { meta, files })
    }
    fn update(&self) -> Update<'_> {
        Update {
            keys: &self.meta[0],
            keys_signature: &self.meta[1],
            manifest: &self.meta[2],
            manifest_signature: &self.meta[3],
            files: [
                &self.files[0],
                &self.files[1],
                &self.files[2],
                &self.files[3],
            ],
        }
    }
}

fn root_key(path: &Path) -> Result<pge_update::VerifyingKey, String> {
    let raw = read_regular(path, 4096).map_err(|_| "read root key")?;
    let text = std::str::from_utf8(&raw)
        .ok()
        .and_then(|t| t.strip_suffix('\n'))
        .ok_or("root key format")?;
    pge_update::public_key(text).map_err(|_| "root key format".into())
}

fn state(store: &Path) -> Result<Option<State>, String> {
    match fs::symlink_metadata(store.join("state")) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        _ => {
            let raw = read_regular(&store.join("state"), 4096).map_err(|_| "read state")?;
            State::parse(&raw)
                .map(Some)
                .map_err(|_| "state format".into())
        }
    }
}

fn write_synced(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let mut f = OpenOptions::new().write(true).create_new(true).open(path)?;
    f.write_all(data)?;
    f.sync_all()
}

fn sync_dir(dir: &Path) -> std::io::Result<()> {
    File::open(dir)?.sync_all()
}

/// Copies the update into STORE/next, swaps it in as STORE/active, then writes STORE/state.
/// A crash before the state is written leaves active and state disagreeing, and the endpoint
/// then refuses to start until the update is applied again.
fn install(store: &Path, files: &Files, next_state: &State) -> std::io::Result<()> {
    let next = store.join("next");
    let old = store.join("old");
    for dir in [&next, &old] {
        if dir.exists() {
            fs::remove_dir_all(dir)?;
        }
    }
    fs::create_dir(&next)?;
    for (name, data) in META
        .iter()
        .zip(&files.meta)
        .chain(FILES.iter().zip(&files.files))
    {
        write_synced(&next.join(name), data)?;
    }
    sync_dir(&next)?;
    let active = store.join("active");
    if active.exists() {
        fs::rename(&active, &old)?;
    }
    fs::rename(&next, &active)?;
    sync_dir(store)?;
    let tmp = store.join("state.tmp");
    if tmp.exists() {
        fs::remove_file(&tmp)?;
    }
    write_synced(&tmp, next_state.text().as_bytes())?;
    fs::rename(&tmp, store.join("state"))?;
    sync_dir(store)?;
    if old.exists() {
        fs::remove_dir_all(&old)?;
    }
    Ok(())
}

fn flags(args: &[String], names: &[&str]) -> Result<Vec<String>, String> {
    let mut out = vec![None; names.len()];
    let mut it = args.iter();
    while let Some(k) = it.next() {
        let i = names.iter().position(|n| n == k).ok_or("unknown flag")?;
        if out[i]
            .replace(it.next().ok_or("missing flag value")?.clone())
            .is_some()
        {
            return Err("repeated flag".into());
        }
    }
    out.into_iter()
        .map(|v| v.ok_or_else(|| "missing flag".to_owned()))
        .collect()
}

/// `pge-endpoint --apply-update DIR --root FILE --device ID --store DIR`
/// Prints one word: applied, same, or refused with the reason; exits 0, 0 or 1 (2 on errors).
pub fn apply(args: &[String]) -> ExitCode {
    let result = (|| -> Result<String, String> {
        let v = flags(args, &["--apply-update", "--root", "--device", "--store"])?;
        let (dir, root, device, store) =
            (Path::new(&v[0]), Path::new(&v[1]), &v[2], Path::new(&v[3]));
        let root = root_key(root)?;
        let current = state(store)?;
        let files = Files::read(dir)?;
        match pge_update::decide(&root, device, current.as_ref(), &files.update()) {
            Ok(Outcome::Same) => Ok("same".into()),
            Ok(Outcome::Apply(next)) => {
                install(store, &files, &next).map_err(|e| format!("install: {e}"))?;
                Ok(format!("applied {}", next.version))
            }
            Err(r) => Ok(format!("refused {}", r.word())),
        }
    })();
    match result {
        Ok(word) => {
            println!("{word}");
            ExitCode::from(if word.starts_with("refused") { 1 } else { 0 })
        }
        Err(e) => {
            eprintln!("apply-update error: {e}");
            ExitCode::from(2)
        }
    }
}

/// Loads the applied update for the endpoint, checking it again from scratch.
pub fn load(store: &Path, root: &Path, device: &str) -> Result<Active, String> {
    let root = root_key(root)?;
    let state = state(store)?.ok_or("no update applied")?;
    let dir = store.join("active");
    let files = Files::read(&dir)?;
    let manifest = pge_update::active(&root, device, &state, &files.update())
        .map_err(|r: Refusal| format!("store refused: {}", r.word()))?;
    let pins = Pins([
        manifest.files[0].clone(),
        manifest.files[1].clone(),
        manifest.files[2].clone(),
    ]);
    let (policy, proof_bytes) = manifest
        .settings
        .policy()
        .map_err(|r| format!("store refused: {}", r.word()))?;
    Ok(Active {
        dir,
        pins,
        domain: files.files[3].clone(),
        version: manifest.version,
        policy,
        proof_bytes,
    })
}
