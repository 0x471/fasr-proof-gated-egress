#!/usr/bin/env bash
# Build the device image with Buildroot on a Linux host (Buildroot does not build on macOS).
# Usage: image/build.sh [WORKDIR]   (default ~/p01-image); the image ends up in WORKDIR/out/images/sdcard.img
# BIN is the static aarch64 endpoint (default: the CI artifact under endpoint/target) and PGE_SRS the
# pinned setup file (default prover/artifacts/kzg.srs); both go into the image with the tiny bundle.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:-$HOME/p01-image}"
BR=buildroot-2025.02.18
# From the release's signed .sign file (signed by Arnout Vandecappelle, key 18C7DF2819C1733D822D599EA500D6EE9CB0E540).
BR_SHA=e38ad1df6ea0479fff6419a87a64535d02131674d463be154a763ef725b55321

[ "$(uname -s)" = Linux ] || { echo "Buildroot needs a Linux host" >&2; exit 2; }
# The kernel config probes for rustc; keep a host rustup out of it so the build does not depend on it.
PATH="$(tr ':' '\n' <<<"$PATH" | grep -v '/\.cargo/bin' | paste -sd: -)"
export PATH
REPO="$(cd "$HERE/.." && pwd)"
BIN="${BIN:-$REPO/endpoint/target/aarch64-unknown-linux-musl/release/pge-endpoint}"
SRS="${PGE_SRS:-$REPO/prover/artifacts/kzg.srs}"
mkdir -p "$WORK/dl"
STAGE="$WORK/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/usr/bin" "$STAGE/etc/pge/bundle"
install -m 0755 "$BIN" "$STAGE/usr/bin/pge-endpoint"
install -m 0644 "$REPO/tests/fixtures/tiny/settings.json" "$REPO/tests/fixtures/tiny/vk.key" "$STAGE/etc/pge/bundle/"
install -m 0644 "$SRS" "$STAGE/etc/pge/bundle/kzg.srs"
install -m 0644 "$REPO/device/tiny.sha256" "$STAGE/etc/pge/pins.sha256"
install -m 0644 "$REPO/tests/fixtures/tiny/domain.json" "$STAGE/etc/pge/domain.json"
# The endpoint refuses to start on a bundle that doesn't match its pins; fail here instead.
(cd "$STAGE/etc/pge/bundle" && sha256sum -c ../pins.sha256)
sha256sum "$STAGE/usr/bin/pge-endpoint"
cd "$WORK"
[ -f "$BR.tar.xz" ] || curl -sSL --fail -o "$BR.tar.xz" "https://buildroot.org/downloads/$BR.tar.xz"
echo "$BR_SHA  $BR.tar.xz" | sha256sum -c -
[ -d "$BR" ] || tar -xJf "$BR.tar.xz"
export BR2_DL_DIR="$WORK/dl"
make -C "$BR" O="$WORK/out" raspberrypi4_64_defconfig
{ cat "$HERE/v1.fragment"
  echo "BR2_ROOTFS_OVERLAY=\"$HERE/overlay $STAGE\""
  echo "BR2_ROOTFS_USERS_TABLES=\"$HERE/users.txt\""
} > "$WORK/fragment"
cat "$WORK/fragment" >> "$WORK/out/.config"
make -C "$BR" O="$WORK/out" olddefconfig
# Every fragment line must survive olddefconfig, or the image isn't what the fragment says.
grep -E '^BR2_' "$WORK/fragment" | while read -r line; do
  grep -qxF "$line" "$WORK/out/.config" || { echo "config lost: $line" >&2; exit 1; }
done
grep -qx '# BR2_TARGET_GENERIC_REMOUNT_ROOTFS_RW is not set' "$WORK/out/.config" || { echo "root filesystem would be writable" >&2; exit 1; }
make -C "$BR" O="$WORK/out"
sha256sum "$WORK/out/images/sdcard.img"
