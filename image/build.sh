#!/usr/bin/env bash
# Build the device image with Buildroot on a Linux host (Buildroot does not build on macOS).
# Usage: image/build.sh [WORKDIR]   (default ~/p01-image); the image ends up in WORKDIR/out/images/sdcard.img
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
mkdir -p "$WORK/dl"
cd "$WORK"
[ -f "$BR.tar.xz" ] || curl -sSL --fail -o "$BR.tar.xz" "https://buildroot.org/downloads/$BR.tar.xz"
echo "$BR_SHA  $BR.tar.xz" | sha256sum -c -
[ -d "$BR" ] || tar -xJf "$BR.tar.xz"
export BR2_DL_DIR="$WORK/dl"
make -C "$BR" O="$WORK/out" raspberrypi4_64_defconfig
cat "$HERE/v1.fragment" >> "$WORK/out/.config"
make -C "$BR" O="$WORK/out" olddefconfig
# Every fragment line must survive olddefconfig, or the image isn't what the fragment says.
grep -E '^BR2_' "$HERE/v1.fragment" | while read -r line; do
  grep -qxF "$line" "$WORK/out/.config" || { echo "config lost: $line" >&2; exit 1; }
done
make -C "$BR" O="$WORK/out"
sha256sum "$WORK/out/images/sdcard.img"
