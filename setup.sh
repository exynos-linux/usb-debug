#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Install the usb-debug host tool from a GitHub release:
#
#   curl -fsSL https://github.com/exynos-linux/usb-debug/releases/latest/download/setup.sh | sh
#   curl -fsSL .../releases/download/vX.Y.Z/setup.sh | sh -s -- [options]
#
#   --version X.Y.Z  install this release (default: the release this script
#                    came with, or the newest one)
#   --no-link        only unpack it (device repositories run a pinned version
#                    from its own directory, see docs/MIGRATION.md)
#   --data DIR       where releases go (default $XDG_DATA_HOME/usb-debug)
#   --bin DIR        where the usb-debug link goes (default ~/.local/bin)
#
# Every download is checked against the release's SHA256SUMS. Nothing is
# installed system-wide and nothing needs root.
set -eu

REPO=exynos-linux/usb-debug
# a mirror of the release downloads (tests): $USB_DEBUG_RELEASES/vX.Y.Z/<file>
RELEASES=${USB_DEBUG_RELEASES:-https://github.com/$REPO/releases/download}
VERSION=@VERSION@		# the release workflow fills this in
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/usb-debug
BIN=$HOME/.local/bin
LINK=1

while [ $# -gt 0 ]; do
	case $1 in
		--version) VERSION=${2#v}; shift 2 ;;
		--no-link) LINK=0; shift ;;
		--data) DATA=$2; shift 2 ;;
		--bin) BIN=$2; shift 2 ;;
		-h | --help) sed -n '3,17p' "$0" 2>/dev/null || true; exit 0 ;;
		*) echo "setup.sh: unknown option $1" >&2; exit 1 ;;
	esac
done

say() { printf 'usb-debug setup: %s\n' "$*" >&2; }
die() { say "$*"; exit 1; }

if command -v curl > /dev/null; then
	fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }
	fetch_stdout() { curl -fsSL --retry 3 "$1"; }
elif command -v wget > /dev/null; then
	fetch() { wget -q -O "$2" "$1"; }
	fetch_stdout() { wget -q -O - "$1"; }
else
	die "needs curl or wget"
fi
for t in tar gzip sha256sum python3; do
	command -v $t > /dev/null || die "needs $t"
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "needs Python 3.8 or newer"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
	say "note: Python older than 3.11 cannot read config.toml itself (it uses the repository's scripts/config, else defaults)"

case $VERSION in
	@*@ | '')
		VERSION=$(fetch_stdout "https://api.github.com/repos/$REPO/releases/latest" |
			sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -n 1)
		[ -n "$VERSION" ] || die "cannot find the newest release" ;;
esac

dest=$DATA/$VERSION
if [ -x "$dest/host/usb-debug" ]; then
	say "$VERSION is already installed in $dest"
else
	mkdir -p "$DATA"
	tmp=$(mktemp -d "$DATA/.setup.XXXXXX")
	trap 'rm -rf "$tmp"' EXIT INT TERM
	base=$RELEASES/v$VERSION
	say "downloading $VERSION"
	fetch "$base/SHA256SUMS" "$tmp/SHA256SUMS" || die "no release $VERSION"
	tarball=usb-debug-$VERSION.tar.gz
	fetch "$base/$tarball" "$tmp/$tarball"
	for opt in APKBUILD uinput-type-aarch64 usb-debug-console-aarch64; do
		if grep -q "  $opt\$" "$tmp/SHA256SUMS"; then
			fetch "$base/$opt" "$tmp/$opt"
		fi
	done
	(cd "$tmp" && sha256sum --check --ignore-missing --quiet SHA256SUMS) || die "checksum mismatch"
	grep -q "  $tarball\$" "$tmp/SHA256SUMS" || die "$tarball is not in SHA256SUMS"
	mkdir "$tmp/x"
	tar -xzf "$tmp/$tarball" -C "$tmp/x" --no-same-owner
	tree=$tmp/x/usb-debug-$VERSION
	[ -x "$tree/host/usb-debug" ] || die "$tarball has no host/usb-debug"
	if [ -f "$tmp/APKBUILD" ]; then
		cp "$tmp/APKBUILD" "$tree/APKBUILD.release"
	fi
	for b in uinput-type usb-debug-console; do
		if [ -f "$tmp/$b-aarch64" ]; then
			mkdir -p "$tree/bin/aarch64"
			install -m 755 "$tmp/$b-aarch64" "$tree/bin/aarch64/$b"
		fi
	done
	mv "$tree" "$dest"
	say "installed $VERSION in $dest"
fi

if [ "$LINK" = 1 ]; then
	ln -sfn "$VERSION" "$DATA/.current.new" && mv -T "$DATA/.current.new" "$DATA/current"
	mkdir -p "$BIN"
	ln -sf "$DATA/current/host/usb-debug" "$BIN/usb-debug"
	say "usb-debug -> $VERSION ($BIN/usb-debug)"
	case ":$PATH:" in
		*":$BIN:"*) ;;
		*) say "add $BIN to the END of your PATH (e.g. PATH=\$PATH:$BIN in ~/.profile)" ;;
	esac
fi
