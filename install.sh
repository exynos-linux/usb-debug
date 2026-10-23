#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# install.sh: install the exynos-linux USB debug tool on this PC.
#
#   curl -fsSL https://github.com/exynos-linux/usb-debug/releases/latest/download/install.sh | sh
#   curl -fsSL https://github.com/exynos-linux/usb-debug/raw/main/install.sh | sh -s -- --yes
#   ./install.sh [OPTIONS]          from a git clone: installs that checkout
#
# It installs the "tab" command (also called "usb-debug": console, file
# transfer, screenshots, flashing, the siren/notify helper, self-update)
# under ~/.local, then the parts that need root (through sudo): udev rules
# for Samsung's Download mode and the debug gadget, and a NetworkManager (or
# systemd-networkd) profile for the device's USB link (IPv6 link-local for
# the console, 10.42.7.1/24 shared to the device).
#
# Options. This is a stable interface: device repositories' setup.sh run
# "sh install.sh --yes [--device=ID] [--version=TAG]".
#   --yes             never ask anything (sudo may still want a password)
#   --version=TAG     install this release (v1.2.3 or 1.2.3). Default: the
#                     release this script belongs to, else the newest one;
#                     from a git clone without --version: the checkout
#   --device=ID       vendor-codename (e.g. samsung-gtactive3wifi) whose
#                     settings tab uses outside a device repository
#   --repo=DIR        a device repository whose config.toml tab uses by default
#   --no-system       skip everything that needs root
#   --no-link         do not make it the default "tab" (a pinned copy for one
#                     device repository; implies --no-system)
#   --auto-update     add a daily user timer running "tab self-update"
#   --uninstall       remove what this script installed (all versions)
#   --data=DIR        where releases go (default $XDG_DATA_HOME/usb-debug)
#   --bin=DIR         where the commands go (default ~/.local/bin)
#   --sysroot=DIR     (tests) write the root-owned files below DIR, no sudo
#   -h, --help
# Exit status 0 on success; the last line then is
# "usb-debug <version> installed" (or "usb-debug uninstalled").
set -eu

REPO=exynos-linux/usb-debug
VERSION=@VERSION@		# the release workflow fills this in
# a mirror of the release downloads (tests): $USB_DEBUG_RELEASES/vX.Y.Z/<file>
RELEASES=${USB_DEBUG_RELEASES:-https://github.com/$REPO/releases/download}
API=${USB_DEBUG_API:-https://api.github.com/repos/$REPO/releases}
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/usb-debug
BIN=$HOME/.local/bin
CONF=${XDG_CONFIG_HOME:-$HOME/.config}/usb-debug
UNITS=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
YES=0 WANT='' DEVICE='' DREPO='' SYSTEM=1 LINK=1 AUTO=0 UNINSTALL=0 SYSROOT=''

say() { printf 'usb-debug install: %s\n' "$*" >&2; }
die() { say "error: $*"; exit 1; }

usage() {
	sed -n '/^# install.sh/,/^set -eu/p' "$0" 2>/dev/null | sed -e '/^set -eu/d' -e 's/^# \{0,1\}//' ||
		echo "see https://github.com/$REPO#install-the-host-tool"
}

while [ $# -gt 0 ]; do
	case $1 in
		--yes | -y) YES=1 ;;
		--version=*) WANT=${1#*=} ;;
		--version) WANT=${2:?--version needs a tag}; shift ;;
		--device=*) DEVICE=${1#*=} ;;
		--device) DEVICE=${2:?--device needs an id}; shift ;;
		--repo=*) DREPO=${1#*=} ;;
		--no-system) SYSTEM=0 ;;
		--no-link) LINK=0 SYSTEM=0 ;;
		--auto-update) AUTO=1 ;;
		--uninstall) UNINSTALL=1 ;;
		--data=*) DATA=${1#*=} ;;
		--bin=*) BIN=${1#*=} ;;
		--sysroot=*) SYSROOT=${1#*=} ;;
		-h | --help) usage; exit 0 ;;
		*) die "unknown option $1 (--help lists them)" ;;
	esac
	shift
done
WANT=${WANT#v}
case $DEVICE in '' | [a-z0-9]*-[a-z0-9]*) ;; *) die "--device wants vendor-codename, e.g. samsung-gtactive3wifi" ;; esac
# --yes: nothing below asks a question (yet); accepted so callers can rely on it
: "$YES"

# ---------- root-owned files ----------

root() {	# run as root: directly, in the test sysroot, or through sudo
	if [ -n "$SYSROOT" ] || [ "$(id -u)" = 0 ]; then
		"$@"
	elif command -v sudo > /dev/null; then
		sudo "$@"
	else
		return 1
	fi
}

sysrun() {	# a reload command; not in the test sysroot
	[ -n "$SYSROOT" ] && return 0
	root "$@" > /dev/null 2>&1 || true
}

put_root() {	# put_root MODE SOURCE DEST
	root install -D -m "$1" "$2" "$SYSROOT$3"
}

nm_active() { [ -z "$SYSROOT" ] && command -v nmcli > /dev/null && nmcli -t -f RUNNING general 2>/dev/null | grep -q running; }
networkd_active() { [ -z "$SYSROOT" ] && systemctl is-active -q systemd-networkd 2>/dev/null; }

# a NetworkManager profile other than ours that already matches the link
nm_existing() {
	nmcli -t -f NAME,TYPE connection show 2>/dev/null | while IFS=: read -r name type; do
		[ "$type" = 802-3-ethernet ] || continue
		[ "$name" = usb-debug ] && continue
		mac=$(nmcli -g 802-3-ethernet.mac-address connection show "$name" 2>/dev/null | tr 'a-f' 'A-F')
		[ "$mac" = 02:57:0A:00:00:02 ] && { echo "$name"; break; }
	done
}

system_install() {	# $1: the installed tree
	src=$1/host/system
	stmp=$(mktemp -d)
	say "system setup (udev rules, network profile; may ask for your password)"
	put_root 644 "$src/70-usb-debug.rules" /etc/udev/rules.d/70-usb-debug.rules ||
		{ rm -rf "$stmp"; return 1; }
	sysrun udevadm control --reload
	sysrun udevadm trigger --subsystem-match=usb --action=add

	if [ -n "$SYSROOT" ] || nm_active; then
		other=$( (nm_active && nm_existing) || true)
		if [ -n "$other" ]; then
			say "NetworkManager already has a profile for the link: '$other' (kept)"
		else
			method=manual
			command -v dnsmasq > /dev/null && method=shared
			uuid=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo 7c1d4a60-57aa-4e6b-9c55-0b6d3f5e1a01)
			sed -e "s/@UUID@/$uuid/" -e "s/@IPV4_METHOD@/$method/" "$src/usb-debug.nmconnection" > "$stmp/nm"
			put_root 600 "$stmp/nm" /etc/NetworkManager/system-connections/usb-debug.nmconnection ||
				{ rm -rf "$stmp"; return 1; }
			sysrun nmcli connection reload
			[ "$method" = shared ] || say "dnsmasq is not installed: IPv4 is not shared with the device (the console needs only IPv6)"
		fi
	elif networkd_active; then
		put_root 644 "$src/50-usb-debug.network" /etc/systemd/network/50-usb-debug.network ||
			{ rm -rf "$stmp"; return 1; }
		sysrun networkctl reload
	else
		say "neither NetworkManager nor systemd-networkd runs: leave the device's USB interface up (IPv6 link-local is all the console needs)"
	fi
	rm -rf "$stmp"
}

system_uninstall() {
	say "removing the system files (may ask for your password)"
	for f in /etc/udev/rules.d/70-usb-debug.rules \
		/etc/NetworkManager/system-connections/usb-debug.nmconnection \
		/etc/systemd/network/50-usb-debug.network; do
		if [ -e "$SYSROOT$f" ]; then
			root rm -f "$SYSROOT$f" || return 1
		fi
	done
	sysrun udevadm control --reload
	nm_active && sysrun nmcli connection reload
	networkd_active && sysrun networkctl reload
	return 0
}

# ---------- uninstall ----------

if [ "$UNINSTALL" = 1 ]; then
	if command -v systemctl > /dev/null && [ -f "$UNITS/usb-debug-update.timer" ]; then
		systemctl --user disable --now usb-debug-update.timer > /dev/null 2>&1 || true
	fi
	rm -f "$UNITS/usb-debug-update.timer" "$UNITS/usb-debug-update.service"
	for c in tab usb-debug; do
		t=$(readlink "$BIN/$c" 2>/dev/null || true)
		case $t in "$DATA"/*) rm -f "$BIN/$c" ;; esac
	done
	# only what install.sh made: releases, the current link, leftovers
	if [ -d "$DATA" ]; then
		for e in "$DATA"/* "$DATA"/.current.new "$DATA"/.install.*; do
			if [ -L "$e" ] || [ -x "$e/host/usb-debug" ]; then
				rm -rf "$e"
			elif [ -d "$e" ] && [ "${e##*/.install.}" != "$e" ]; then
				rm -rf "$e"
			fi
		done
		rmdir "$DATA" 2> /dev/null || say "$DATA has other files: left in place"
	fi
	rm -f "$CONF/config.toml"
	rmdir "$CONF" 2> /dev/null || true
	if [ "$SYSTEM" = 1 ]; then
		system_uninstall || die "could not remove the system files (run again with sudo rights)"
	fi
	echo "usb-debug uninstalled"
	exit 0
fi

# ---------- what to install ----------

for t in python3 tar gzip; do
	command -v $t > /dev/null || die "needs $t"
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "needs Python 3.8 or newer"

here=
[ -f "$0" ] && here=$(cd "$(dirname "$0")" && pwd)
checkout=
if [ -z "$WANT" ] && [ -n "$here" ] && [ -x "$here/host/usb-debug" ] && [ -f "$here/VERSION" ]; then
	checkout=$here
	VERSION=$(cat "$here/VERSION")
else
	case $VERSION in @*@) VERSION= ;; esac
	VERSION=${WANT:-$VERSION}
fi

if command -v curl > /dev/null; then
	fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }
	fetch_stdout() { curl -fsSL --retry 3 "$1"; }
elif command -v wget > /dev/null; then
	fetch() { wget -q -O "$2" "$1"; }
	fetch_stdout() { wget -q -O - "$1"; }
else
	fetch() { return 1; }
	fetch_stdout() { return 1; }
fi

if [ -z "$checkout" ] && [ -z "$VERSION" ]; then
	VERSION=$(fetch_stdout "$API/latest" | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -n 1) || true
	[ -n "$VERSION" ] || die "cannot find the newest release (no network, or no release yet?)"
fi

# ---------- the tool itself ----------

if [ -n "$checkout" ]; then
	dest=$checkout
	say "using the checkout $checkout ($VERSION)"
else
	dest=$DATA/$VERSION
	if [ -x "$dest/host/usb-debug" ]; then
		say "$VERSION is already installed in $dest"
	else
		command -v sha256sum > /dev/null || die "needs sha256sum"
		mkdir -p "$DATA"
		tmp=$(mktemp -d "$DATA/.install.XXXXXX")
		trap 'rm -rf "$tmp"' EXIT INT TERM
		base=$RELEASES/v$VERSION
		say "downloading $VERSION"
		fetch "$base/SHA256SUMS" "$tmp/SHA256SUMS" || die "no release $VERSION at $base"
		tarball=usb-debug-$VERSION.tar.gz
		grep -q "  $tarball\$" "$tmp/SHA256SUMS" || die "$tarball is not in SHA256SUMS"
		fetch "$base/$tarball" "$tmp/$tarball" || die "cannot download $tarball"
		for opt in APKBUILD uinput-type-aarch64 usb-debug-console-aarch64; do
			if grep -q "  $opt\$" "$tmp/SHA256SUMS"; then
				fetch "$base/$opt" "$tmp/$opt" || die "cannot download $opt"
			fi
		done
		(cd "$tmp" && sha256sum --check --ignore-missing --quiet SHA256SUMS) || die "checksum mismatch"
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
fi

if [ "$LINK" = 1 ]; then
	mkdir -p "$DATA" "$BIN"
	ln -sfn "$dest" "$DATA/.current.new" && mv -T "$DATA/.current.new" "$DATA/current"
	for c in tab usb-debug; do
		if [ -e "$BIN/$c" ] && [ ! -L "$BIN/$c" ]; then
			say "$BIN/$c exists and is not ours: left alone"
			continue
		fi
		ln -sfn "$DATA/current/host/usb-debug" "$BIN/$c"
	done
	case ":$PATH:" in
		*":$BIN:"*) ;;
		*) say "add $BIN to the END of your PATH (e.g. PATH=\$PATH:$BIN in ~/.profile)" ;;
	esac
fi

# ---------- settings ----------

if [ -n "$DEVICE" ] || [ -n "$DREPO" ]; then
	mkdir -p "$CONF"
	{
		echo "# written by usb-debug install.sh; the settings tab uses outside a device repository"
		[ -z "$DEVICE" ] || echo "device = \"$DEVICE\""
		[ -z "$DREPO" ] || echo "repo = \"$(cd "$DREPO" && pwd)\""
	} > "$CONF/config.toml"
	if [ -n "$DEVICE" ] && [ ! -f "$dest/devices/$DEVICE.toml" ]; then
		say "no built-in settings for $DEVICE: tab uses the defaults (ports 23/2323) outside its repository"
	fi
fi

# ---------- the update timer ----------

if [ "$AUTO" = 1 ]; then
	mkdir -p "$UNITS"
	sed "s|%h/.local/bin/usb-debug|$BIN/usb-debug|" "$dest/host/system/usb-debug-update.service" > "$UNITS/usb-debug-update.service"
	cp "$dest/host/system/usb-debug-update.timer" "$UNITS/"
	if [ -z "$SYSROOT" ]; then
		if ! { systemctl --user daemon-reload && systemctl --user enable --now usb-debug-update.timer; } > /dev/null 2>&1; then
			die "could not enable the update timer (no user systemd?)"
		fi
	fi
fi

# ---------- system parts ----------

if [ "$SYSTEM" = 1 ]; then
	system_install "$dest" || die "the system setup failed (no root rights?); --no-system skips it"
fi

# ---------- what is still missing ----------

missing=
for t in heimdall notify-send ping; do
	command -v $t > /dev/null || missing="$missing $t"
done
command -v pw-play > /dev/null || command -v paplay > /dev/null || missing="$missing pw-play"
[ -z "$missing" ] || say "optional, not installed:$missing (flashd needs heimdall, notify the others)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2> /dev/null ||
	say "Python < 3.11: config.toml is read through the repository's scripts/config only"

echo "usb-debug $VERSION installed"
