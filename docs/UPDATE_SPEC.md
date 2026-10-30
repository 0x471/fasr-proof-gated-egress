# Signed updates

How the endpoint learns which model to check proofs for, and how that changes. First slice 29 Oct 2026, revised 30 Oct after an outside review; code in `endpoint/update/` (rules) and `endpoint/endpoint/src/store.rs` (applying and loading). Attacks and their tests: `docs/ATTACKS.md`, section Updates.

## Who can do what
- The **root key** is offline. Its public half is given to the endpoint as a file (`--root`); on a device image it is part of the read-only system. It signs only the key list.
- **Update keys** sign manifests. The root adds or removes them by signing a new key list.
- The **AI server** can't send updates: they never arrive over the server link or in answers.
- Whoever can run `--apply-update` on the device or write to its store is **trusted**. The store's `state` file is not signed: it is the device's record of how far it has moved forward, and the anti-rollback rules below are only as good as that record. The checks catch an edited state that no longer matches the stored files, and an older update applied on top of it, but a writer who replaces the state *and* the files with an older consistent copy is not stopped (K43). Stopping that needs a counter the card can't roll back (a TPM, v2).

## Files
**Signed metadata** (`keys`, `manifest`) and `state` are ASCII, lines end in a single `\n` (no `\r`, no trailing space, no empty line), fields are separated by one space, at most 4096 bytes. Hashes are SHA-256 in lowercase hex; names are 1-32 characters of `a-z`, `0-9` and `-`; numbers are decimal with no leading zero, at most 19 digits, and at least 1, except `spin-ms` and the cap's failure count, which may be 0. Each value has exactly one way to be written, so the signed bytes are the file. The model files themselves are opaque bytes checked only by hash, with the size limits in `endpoint/endpoint/src/store.rs` (`LIMITS`).

`keys` (signed by the root), keys in strictly increasing order of name, 1 to 8 of them, each a compressed secp256k1 public key (66 hex characters):
```
pge-keys 1
version 1
key ops-1 02...
```

`manifest` (signed by the key it names):
```
pge-update 1
device pi-1
version 3
settings.json <sha256>
vk.key <sha256>
kzg.srs <sha256>
domain.json <sha256>
release fixed
deadline-ms 4000
spin-ms 5
proof-bytes 3072
max-pending 32
halt none
cap 64 1 64
key ops-1
```
The policy lines set the endpoint's release rule (`arrival` or `fixed`), deadline, spin, proof length, queue limit, halt (`none` or a count) and failure cap (`none` or window, failures, cooldown). They are signed with the model, so the policy can't be changed or rolled back on its own.

`keys.sig`, `manifest.sig`: ECDSA over secp256k1 with SHA-256, the 64-byte (r, s) signature in lowercase hex and a newline. High-s signatures are refused, so each (r, s) pair has one accepted form; a signer can still produce other valid signatures for the same file (ECDSA nonces differ), and each of them is accepted. `endpoint/update/src/bin/pge-sign.rs` makes them (offline, never on the device).

`state` (on the device):
```
pge-state 1
version 3
keys 1
keys-digest <sha256 of the applied key list>
digest <sha256 of the applied manifest>
```

## Applying an update
`pge-endpoint --apply-update DIR --root FILE --device ID --store STORE` prints `applied N` (N is the manifest version), `same` or `refused WORD` and exits 0, 0 or 1; it exits 2 with a message on stderr for errors, including a missing, non-regular or oversized file in DIR (all files are read and size-checked before any signature) and `store busy`.

1. **Lock.** It takes `STORE/lock` exclusively, without waiting, and holds it to the end. A serving endpoint holds the same lock shared for its whole life, so an install never overlaps another install or a running endpoint: either fails with `store busy`.
2. **Consistency.** If a state exists, the stored files must still pass the start-up check against it (below); otherwise `refused state`. The one exception finishes an interrupted install: if the stored manifest is byte-for-byte the manifest being applied, the install continues. This stops an edited state from letting an older update in.
3. **Rules,** in this order:

| Check | Refusal |
|---|---|
| key list signed by the root | `signature` |
| key list and manifest in the one allowed form | `format` |
| manifest's key in the key list | `key` |
| manifest signed by that key | `signature` |
| manifest for this device (`--device`) | `device` |
| key list version not below the state's, manifest version not below the state's | `rollback` |
| a key list with the state's version but other content | `replay` |
| the state's manifest version: the same manifest with the same key list is a no-op (`same`, nothing is checked or written further); another manifest is refused; the same manifest with a **newer** key list is applied (key rotation or revocation) | `replay` |
| policy values in range (the same limits as the command-line flags) | `policy` |
| every file's hash equals the manifest | `bundle` |

4. **Install.** It copies the update into `STORE/next`, syncs it, swaps it in as `STORE/active`, then writes `STORE/state` the same way. The state is written last, so a crash in between leaves the two disagreeing; the endpoint then refuses to start, and applying the same update again finishes it (step 2's exception).

An installed update is not yet in use: **installed** (on disk), **validated** (the start-up check passes) and **active** (a running endpoint loaded it) are separate. Because of the lock, installing means the endpoint is stopped; it takes effect when the endpoint is started again.

## Running from the store
`--store STORE --root FILE --device ID` replaces `--bundle`, `--pins` and `--domain`, and the policy flags (`--release`, `--deadline`, `--spin-ms`, `--proof-bytes`, `--max-pending`, `--halt-after`, `--cap-*`); giving any of them with `--store` is an error, because the policy comes from the signed manifest. At start the endpoint takes the store lock shared (no lock file means nothing was ever applied) and checks the stored update from scratch: the key list and manifest signatures, the device, that the key list and manifest are exactly the ones the state records (versions and digests; otherwise `state`), and every file's hash (otherwise `bundle`). It then logs `{"result":"update","version":N}` once and keeps the lock until it exits.

## On the device image
`image/make_update.sh` signs an update on the operator's machine; `image/build.sh` (with `UPDATE` and `ROOT_PUB`) applies it with the device's own binary into a store on a third, writable partition (`/data`, ext4, `noexec`), puts the root public key into the read-only root, and starts the endpoint with `--store /data/pge-store --device pi-1`. Store files are root-owned; the endpoint runs as `pge` and only reads them.

## What this doesn't do yet
- **A whole older copy of the store** (state and files together) is consistent with itself, so it is accepted (K43). Stopping that needs a counter the card can't roll back: a TPM's monotonic counter (v2).
- **Freshness in time**: the Pi has no battery clock, so manifests carry no expiry; "fresh" means only "not older than the last applied update".
- **Power loss**: the install order was tested by killing the installer at each step (outside review, macOS), not by cutting power on the Pi's ext4.
- `--idle` (how long a silent client is kept) is still a flag, not part of the signed policy.
- One signature per manifest; two-party signing (for example the lab and an auditor) is a small extension.
- No delivery path on the device yet: a new update means a new image, or stopping the endpoint and running `--apply-update` from the serial console with the update on removable media. A management port or the serial console is the plan.
