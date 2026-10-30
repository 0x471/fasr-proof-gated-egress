# Signed updates

How the endpoint learns which model to check proofs for, and how that changes. First slice, 29 Oct 2026; code in `endpoint/update/` (rules) and `endpoint/endpoint/src/store.rs` (applying and loading). Attacks and their tests: `docs/ATTACKS.md`, section Updates.

## Who can do what
- The **root key** is offline. Its public half is given to the endpoint as a file (`--root`); on a device image it is part of the read-only system. It signs only the key list.
- **Update keys** sign manifests. The root adds or removes them by signing a new key list.
- The **AI server** can't send updates: they never arrive over the server link or in answers.
- Whoever can run `--apply-update` on the device, or write to its store, is trusted not to delete the store; everything else they could do is refused (below).

## Files
Every file is ASCII, lines end in a single `\n` (no `\r`, no trailing space, no empty line), fields are separated by one space, at most 4096 bytes. Hashes are SHA-256 in lowercase hex; names are 1-32 characters of `a-z`, `0-9` and `-`; numbers have no leading zero and start at 1. There is one way to write each file, so the signed bytes are the file.

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

`keys.sig`, `manifest.sig`: ECDSA over secp256k1 with SHA-256, the 64-byte (r, s) signature in lowercase hex and a newline. High-s signatures are refused. `endpoint/update/src/bin/pge-sign.rs` makes them (offline, never on the device).

`state` (on the device):
```
pge-state 1
version 3
keys 1
digest <sha256 of the applied manifest>
```

## Rules, in the order they are checked
| Check | Refusal |
|---|---|
| key list signed by the root | `signature` |
| key list and manifest in the one allowed form | `format` |
| manifest's key in the key list | `key` |
| manifest signed by that key | `signature` |
| manifest for this device (`--device`) | `device` |
| key list version not below the state's, manifest version not below the state's | `rollback` |
| same manifest version as the state: the same manifest is a no-op (`same`), another one is refused | `replay` |
| policy values in range (the same limits as the command-line flags) | `policy` |
| every file's hash equals the manifest | `bundle` |

`pge-endpoint --apply-update DIR --root FILE --device ID --store STORE` prints `applied N`, `same` or `refused WORD` and exits 0, 0 or 1 (2 for an error such as a missing file). Applying copies the update into `STORE/next`, syncs it, swaps it in as `STORE/active`, then writes `STORE/state` the same way. The state is written last, so a crash in between leaves the two disagreeing and the endpoint refuses to start until the update is applied again.

## Running from the store
`--store STORE --root FILE --device ID` replaces `--bundle`, `--pins` and `--domain`, and the policy flags (`--release`, `--deadline`, `--spin-ms`, `--proof-bytes`, `--max-pending`, `--halt-after`, `--cap-*`); giving any of them with `--store` is an error, because the policy comes from the signed manifest. At start the endpoint checks the stored update from scratch: the key list and manifest signatures, the device, that the manifest is exactly the one the state records (version, key list version and digest; otherwise `state`), and every file's hash (otherwise `bundle`). It then logs `{"result":"update","version":N}` once. Updates are never applied while it serves, so every answer is checked under one update.

## What this doesn't do yet
- **A whole older copy of the store** (state and files together) is consistent with itself, so it is accepted. Stopping that needs a counter the SD card can't roll back: a TPM's monotonic counter (v2).
- **Freshness in time**: the Pi has no battery clock, so manifests carry no expiry; "fresh" means only "not older than the last applied update".
- `--idle` (how long a silent client is kept) is still a flag, not part of the signed policy.
- One signature per manifest; two-party signing (for example the lab and an auditor) is a small extension.
- No delivery path once SSH is gone; the plan is the serial console or a separate management port.
