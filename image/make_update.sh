#!/usr/bin/env bash
# Makes a signed update for the device from the pinned tiny bundle (operator side, never on the device).
# Usage from the repo root: image/make_update.sh KEYS_DIR OUT_DIR VERSION [DEVICE]
# KEYS_DIR holds root.key and ops-1.key (secret scalars, 64 hex + newline); missing keys are created
# with fresh random values. Writes KEYS_DIR/root.pub for the image and OUT_DIR with the update.
# PGE_SIGN is the pge-sign binary (default: the release build); PGE_SRS the pinned setup file.
# POLICY overrides the signed policy lines (default: fixed release, 4 s, 5 ms spin, no cap).
set -euo pipefail
cd "$(dirname "$0")/.."
KEYS=$1 OUT=$2 VERSION=$3 DEVICE=${4:-pi-1}
SIGN="${PGE_SIGN:-endpoint/target/release/pge-sign}"
SRS="${PGE_SRS:-prover/artifacts/kzg.srs}"
POLICY="${POLICY:-release fixed
deadline-ms 4000
spin-ms 5
proof-bytes 3072
max-pending 32
halt none
cap none}"
[[ "$VERSION" =~ ^[1-9][0-9]*$ && "$DEVICE" =~ ^[a-z0-9-]{1,32}$ ]] || { echo "bad VERSION or DEVICE" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "$OUT exists" >&2; exit 2; }
mkdir -p "$KEYS"
umask 077
for k in root ops-1; do
  [ -f "$KEYS/$k.key" ] || { openssl rand -hex 32 > "$KEYS/$k.key"; }
done
umask 022
"$SIGN" --public "$KEYS/root.key" > "$KEYS/root.pub"
mkdir -p "$OUT"
cp tests/fixtures/tiny/settings.json tests/fixtures/tiny/vk.key tests/fixtures/tiny/domain.json "$OUT/"
cp "$SRS" "$OUT/kzg.srs"
(cd "$OUT" && grep -E ' (settings.json|vk.key|kzg.srs)$' "$OLDPWD/device/tiny.sha256" | shasum -a 256 -c - >/dev/null)
printf 'pge-keys 1\nversion 1\nkey ops-1 %s\n' "$("$SIGN" --public "$KEYS/ops-1.key")" > "$OUT/keys"
{
  printf 'pge-update 1\ndevice %s\nversion %s\n' "$DEVICE" "$VERSION"
  for f in settings.json vk.key kzg.srs domain.json; do
    printf '%s %s\n' "$f" "$(shasum -a 256 "$OUT/$f" | cut -d' ' -f1)"
  done
  printf '%s\nkey ops-1\n' "$POLICY"
} > "$OUT/manifest"
"$SIGN" "$KEYS/root.key" < "$OUT/keys" > "$OUT/keys.sig"
"$SIGN" "$KEYS/ops-1.key" < "$OUT/manifest" > "$OUT/manifest.sig"
echo "update $VERSION for $DEVICE in $OUT; root key $(cat "$KEYS/root.pub")"
