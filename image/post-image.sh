#!/usr/bin/env bash
# Runs after the board's post-image script: adds a writable third partition holding the update store,
# so updates can be applied while the root file system stays read-only.
set -euo pipefail
: "${PGE_DATA:?build.sh sets PGE_DATA}"
rm -f "$BINARIES_DIR/data.ext4" "$BINARIES_DIR/sdcard.img"
# Root owns the store on the device; the endpoint (user pge) only reads it.
"$HOST_DIR/bin/fakeroot" -- sh -c 'chown -R 0:0 "$1" && chmod -R go-w "$1" && "$2/sbin/mkfs.ext4" -q -L data -d "$1" "$3" 64M' \
  sh "$PGE_DATA" "$HOST_DIR" "$BINARIES_DIR/data.ext4"
cat > "$BINARIES_DIR/genimage-data.cfg" <<'CFG'
image sdcard.img {
	hdimage {
	}
	partition boot {
		partition-type = 0xC
		bootable = "true"
		image = "boot.vfat"
	}
	partition rootfs {
		partition-type = 0x83
		image = "rootfs.ext4"
	}
	partition data {
		partition-type = 0x83
		image = "data.ext4"
	}
}
CFG
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
rm -rf "$BUILD_DIR/genimage-data.tmp"
genimage --rootpath "$root" --tmppath "$BUILD_DIR/genimage-data.tmp" --inputpath "$BINARIES_DIR" \
  --outputpath "$BINARIES_DIR" --config "$BINARIES_DIR/genimage-data.cfg"
