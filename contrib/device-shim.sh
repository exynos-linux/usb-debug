#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# scripts/usb-debug of a device repository: run the exynos-linux/usb-debug
# release this repository pins in .usb-debug-version. It installs that
# release on first use (checked against its SHA256SUMS) next to any others,
# so a self-update of the user's own usb-debug never changes what this
# repository runs. Copy it to scripts/usb-debug (mode 755).
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
ver=$(tr -d ' \n' < "$repo/.usb-debug-version")
dir=${XDG_DATA_HOME:-$HOME/.local/share}/usb-debug/$ver
if [ ! -x "$dir/host/usb-debug" ]; then
	url=https://github.com/exynos-linux/usb-debug/releases/download/v$ver/setup.sh
	if command -v curl > /dev/null; then
		curl -fsSL "$url" | sh -s -- --version "$ver" --no-link >&2
	else
		wget -q -O - "$url" | sh -s -- --version "$ver" --no-link >&2
	fi
fi
exec "$dir/host/usb-debug" --repo "$repo" "$@"
