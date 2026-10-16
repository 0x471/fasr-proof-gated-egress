#!/usr/bin/env bash
# Baseline attack-surface snapshot of the device, the same for every version (v0, v1, v2).
# Usage on the device: ./measure.sh [FILE ...]   (FILEs are hashed too, e.g. the ezkl binary and bundle)
# Run with sudo to see which process owns each listening socket.
# Prints "key: value" lines, then one JSON summary line starting with "summary: ".
set -uo pipefail
export LC_ALL=C

have() { command -v "$1" >/dev/null 2>&1; }
sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
count() { grep -c . || true; }

echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "kernel: $(uname -srm)"
os="$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
echo "os: ${os:-unknown}"
echo "python3: $(python3 --version 2>&1 || echo none)"

for f in /boot/firmware/config.txt /boot/firmware/cmdline.txt /proc/cmdline; do
  [ -r "$f" ] && echo "sha256 $f: $(sha "$f")"
done

if have dpkg-query; then
  # Installed only (status ii), the same count as `dpkg -l | grep -c ^ii` on 11 Oct.
  pkgs="$(dpkg-query -W -f '${db:Status-Abbrev} ${Package} ${Version}\n' | awk '$1 == "ii" {print $2, $3}' | sort)"
  npkg="$(count <<<"$pkgs")"
  echo "packages: $npkg"
  echo "packages sha256: $(sha256sum <<<"$pkgs" | cut -d' ' -f1)"
else
  npkg=-1
  echo "packages: unknown (no dpkg)"
fi

if have systemctl; then
  svcs="$(systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}' | sort)"
  nsvc="$(count <<<"$svcs")"
  echo "running services: $nsvc"
  sed 's/^/  service /' <<<"$svcs"
else
  nsvc=-1
  echo "running services: unknown (no systemd)"
fi

nproc_run="$(ps -e -o pid= | count)"
echo "processes: $nproc_run"

if have ss; then
  socks="$(ss -H -tulpn 2>/dev/null | awk '{print $1, $5, $7}' | sort)"
  nsock="$(count <<<"$socks")"
  echo "listening sockets: $nsock"
  sed 's/^/  socket /' <<<"$socks"
else
  nsock=-1
  echo "listening sockets: unknown (no ss)"
fi

for f in "$@"; do
  if [ -f "$f" ]; then
    echo "sha256 $f: $(sha "$f")  ($(wc -c <"$f" | tr -d " ") bytes)"
  else
    echo "missing $f"
  fi
done

if have vcgencmd; then
  echo "temp: $(vcgencmd measure_temp 2>/dev/null)"
  echo "throttled: $(vcgencmd get_throttled 2>/dev/null)"
fi

printf 'summary: {"packages": %s, "running_services": %s, "processes": %s, "listening_sockets": %s}\n' \
  "$npkg" "$nsvc" "$nproc_run" "$nsock"
