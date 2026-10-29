use k256::ecdsa::{signature::Signer, Signature, SigningKey};
use pge_update::{decide, sha256, Manifest, Outcome, Refusal, State, Update};

fn key(n: u8) -> SigningKey {
    SigningKey::from_slice(&[n; 32]).unwrap()
}
fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect()
}
fn public(k: &SigningKey) -> String {
    hex(&k.verifying_key().to_encoded_point(true).to_bytes())
}
fn sign(k: &SigningKey, message: &[u8]) -> Vec<u8> {
    let sig: Signature = k.sign(message);
    format!("{}\n", hex(&sig.to_bytes())).into_bytes()
}
fn keys_text(version: u64, keys: &[(&str, &SigningKey)]) -> Vec<u8> {
    let mut s = format!("pge-keys 1\nversion {version}\n");
    for (id, k) in keys {
        s += &format!("key {id} {}\n", public(k));
    }
    s.into_bytes()
}
const FILES: [&[u8]; 4] = [b"settings", b"vk", b"srs", b"domain"];
fn manifest_text(device: &str, version: u64, files: [&[u8]; 4], key: &str) -> Vec<u8> {
    format!(
        "pge-update 1\ndevice {device}\nversion {version}\nsettings.json {}\nvk.key {}\nkzg.srs {}\ndomain.json {}\nkey {key}\n",
        sha256(files[0]),
        sha256(files[1]),
        sha256(files[2]),
        sha256(files[3])
    )
    .into_bytes()
}

struct Case {
    keys: Vec<u8>,
    keys_sig: Vec<u8>,
    manifest: Vec<u8>,
    manifest_sig: Vec<u8>,
    files: [&'static [u8]; 4],
}
impl Case {
    fn new(version: u64) -> Self {
        let keys = keys_text(1, &[("ops-1", &key(2))]);
        let manifest = manifest_text("pi-1", version, FILES, "ops-1");
        Self {
            keys_sig: sign(&key(1), &keys),
            manifest_sig: sign(&key(2), &manifest),
            keys,
            manifest,
            files: FILES,
        }
    }
    fn run(&self, state: Option<&State>) -> Result<Outcome, Refusal> {
        let update = Update {
            keys: &self.keys,
            keys_signature: &self.keys_sig,
            manifest: &self.manifest,
            manifest_signature: &self.manifest_sig,
            files: self.files,
        };
        decide(key(1).verifying_key(), "pi-1", state, &update)
    }
}

fn state_after(case: &Case) -> State {
    match case.run(None) {
        Ok(Outcome::Apply(s)) => s,
        other => panic!("{other:?}"),
    }
}

#[test]
fn a_valid_update_applies_and_records_its_version() {
    let s = state_after(&Case::new(3));
    assert_eq!((s.version, s.keys), (3, 1));
    assert_eq!(State::parse(s.text().as_bytes()), Ok(s));
}

#[test]
fn the_same_update_again_is_a_no_op() {
    let case = Case::new(3);
    assert_eq!(case.run(Some(&state_after(&case))), Ok(Outcome::Same));
}

#[test]
fn a_newer_version_applies() {
    let old = state_after(&Case::new(3));
    assert!(matches!(Case::new(4).run(Some(&old)), Ok(Outcome::Apply(s)) if s.version == 4));
}

#[test]
fn an_older_version_is_a_rollback() {
    let now = state_after(&Case::new(4));
    assert_eq!(Case::new(3).run(Some(&now)), Err(Refusal::Rollback));
}

#[test]
fn a_different_manifest_with_the_same_version_is_a_replay() {
    let now = state_after(&Case::new(4));
    let mut case = Case::new(4);
    case.files = [b"other settings", b"vk", b"srs", b"domain"];
    case.manifest = manifest_text("pi-1", 4, case.files, "ops-1");
    case.manifest_sig = sign(&key(2), &case.manifest);
    assert_eq!(case.run(Some(&now)), Err(Refusal::Replay));
}

#[test]
fn an_older_key_list_is_a_rollback() {
    let mut newer = Case::new(3);
    newer.keys = keys_text(2, &[("ops-1", &key(2))]);
    newer.keys_sig = sign(&key(1), &newer.keys);
    let now = state_after(&newer);
    assert_eq!(Case::new(4).run(Some(&now)), Err(Refusal::Rollback));
}

#[test]
fn a_manifest_for_another_device_is_refused() {
    let mut case = Case::new(3);
    case.manifest = manifest_text("pi-2", 3, FILES, "ops-1");
    case.manifest_sig = sign(&key(2), &case.manifest);
    assert_eq!(case.run(None), Err(Refusal::Device));
}

#[test]
fn a_key_missing_from_the_list_is_refused() {
    let mut case = Case::new(3);
    case.manifest = manifest_text("pi-1", 3, FILES, "ops-2");
    case.manifest_sig = sign(&key(3), &case.manifest);
    assert_eq!(case.run(None), Err(Refusal::Key));
}

#[test]
fn a_revoked_key_is_refused() {
    let mut case = Case::new(3);
    case.keys = keys_text(2, &[("ops-2", &key(3))]);
    case.keys_sig = sign(&key(1), &case.keys);
    assert_eq!(case.run(None), Err(Refusal::Key));
}

#[test]
fn a_manifest_signed_by_another_listed_key_is_refused() {
    let mut case = Case::new(3);
    case.keys = keys_text(1, &[("ops-1", &key(2)), ("ops-2", &key(3))]);
    case.keys_sig = sign(&key(1), &case.keys);
    case.manifest_sig = sign(&key(3), &case.manifest);
    assert_eq!(case.run(None), Err(Refusal::Signature));
}

#[test]
fn a_key_list_not_signed_by_the_root_is_refused() {
    let mut case = Case::new(3);
    case.keys_sig = sign(&key(2), &case.keys);
    assert_eq!(case.run(None), Err(Refusal::Signature));
}

#[test]
fn a_changed_manifest_breaks_its_signature() {
    let mut case = Case::new(3);
    case.manifest = manifest_text("pi-1", 30, FILES, "ops-1");
    assert_eq!(case.run(None), Err(Refusal::Signature));
}

#[test]
fn each_substituted_file_is_refused() {
    for i in 0..4 {
        let mut case = Case::new(3);
        let mut files = FILES;
        files[i] = b"substituted";
        case.files = files;
        assert_eq!(case.run(None), Err(Refusal::Bundle), "file {i}");
    }
}

#[test]
fn signature_files_have_one_form() {
    let case = Case::new(3);
    let good = case.manifest_sig.clone();
    let mut upper = good.clone();
    upper.make_ascii_uppercase();
    let forms: [Vec<u8>; 4] = [
        good[..good.len() - 1].to_vec(),
        [good.as_slice(), b"\n".as_slice()].concat(),
        upper,
        b"00\n".to_vec(),
    ];
    for bad in forms {
        let mut c = Case::new(3);
        c.manifest_sig = bad;
        assert_eq!(c.run(None), Err(Refusal::Signature));
    }
}

#[test]
fn manifests_have_exactly_one_form() {
    let good = String::from_utf8(manifest_text("pi-1", 3, FILES, "ops-1")).unwrap();
    assert!(Manifest::parse(good.as_bytes()).is_ok());
    let bad = [
        good.replace('\n', "\r\n"),
        good.replace("version 3", "version 03"),
        good.replace("version 3", "version 0"),
        good.replace("version 3", "version  3"),
        good.replace("key ops-1\n", "key ops-1 \n"),
        good.replace("key ops-1\n", "key Ops-1\n"),
        good.replace("pge-update 1", "pge-update 2"),
        good.trim_end().to_owned(),
        format!("{good}extra 1\n"),
        good.replacen("device pi-1\n", "", 1),
        good.replace(&sha256(b"vk"), &sha256(b"vk").to_uppercase()),
        format!("{good}{}", "x".repeat(4096)),
    ];
    for b in bad {
        assert_eq!(Manifest::parse(b.as_bytes()), Err(Refusal::Format), "{b:?}");
    }
}

#[test]
fn malformed_manifests_are_format_errors_even_when_signed() {
    let mut case = Case::new(3);
    case.manifest = b"pge-update 1\ndevice pi-1\n".to_vec();
    case.manifest_sig = sign(&key(2), &case.manifest);
    assert_eq!(case.run(None), Err(Refusal::Format));
}

#[test]
fn key_lists_must_be_sorted_and_unique() {
    for keys in [
        keys_text(1, &[("ops-2", &key(3)), ("ops-1", &key(2))]),
        keys_text(1, &[("ops-1", &key(2)), ("ops-1", &key(3))]),
        b"pge-keys 1\nversion 1\n".to_vec(),
    ] {
        let mut case = Case::new(3);
        case.keys_sig = sign(&key(1), &keys);
        case.keys = keys;
        assert_eq!(case.run(None), Err(Refusal::Format));
    }
}
