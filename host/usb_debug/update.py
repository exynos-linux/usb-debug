# SPDX-License-Identifier: GPL-2.0-only
"""Installed releases and self-update (GitHub Releases).

Layout (what install.sh makes):
    $XDG_DATA_HOME/usb-debug/<version>/   one release: the source tree, plus
        APKBUILD.release                  the aport with its checksum
        bin/aarch64/                      prebuilt device binaries
    $XDG_DATA_HOME/usb-debug/current      -> the version "usb-debug" runs
    ~/.local/bin/usb-debug                -> current/host/usb-debug

Device repositories pin a version and run it from its own directory (see
docs/MIGRATION.md), so a self-update never changes what they run.
Downloads are checked against the release's SHA256SUMS.
"""
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import REPO, ROOT, VERSION

API = os.environ.get("USB_DEBUG_API") or f"https://api.github.com/repos/{REPO}/releases"
# a mirror of the release downloads (tests): $USB_DEBUG_RELEASES/vX.Y.Z/<file>
DL = os.environ.get("USB_DEBUG_RELEASES") or f"https://github.com/{REPO}/releases/download"
ASSETS_OPTIONAL = {"APKBUILD": "APKBUILD.release",
                   "uinput-type-aarch64": "bin/aarch64/uinput-type",
                   "usb-debug-console-aarch64": "bin/aarch64/usb-debug-console"}


def vtuple(v):
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return (0,)


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": f"usb-debug/{VERSION}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest():
    return json.loads(fetch(f"{API}/latest"))["tag_name"].lstrip("v")


def data_root():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "usb-debug"


def installed_root():
    """The directory of installed releases this copy runs from, or None
    (a source checkout)."""
    if (ROOT.parent / "current").is_symlink() and ROOT.name == VERSION:
        return ROOT.parent
    return None


def _safe_extract(tf, dest):
    dest = dest.resolve()
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise RuntimeError(f"unsafe path in the archive: {m.name}")
        if m.issym() or m.islnk():
            link = (target.parent / m.linkname).resolve()
            if not str(link).startswith(str(dest) + os.sep):
                raise RuntimeError(f"unsafe link in the archive: {m.name}")
        if m.isdev():
            raise RuntimeError(f"device file in the archive: {m.name}")
    if hasattr(tarfile, "data_filter"):
        tf.extractall(dest, filter="data")
    else:
        tf.extractall(dest)


def install(version, root):
    """Download, verify and unpack a release into root/<version>."""
    final = root / version
    if (final / "host" / "usb-debug").is_file():
        return final
    root.mkdir(parents=True, exist_ok=True)
    base = f"{DL}/v{version}"
    sums = {}
    for ln in fetch(f"{base}/SHA256SUMS").decode().splitlines():
        h, _, name = ln.partition("  ")
        if h and name:
            sums[name.strip().lstrip("*")] = h.strip()
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        tmp = Path(tmp)

        def get(name, required):
            if name not in sums:
                if required:
                    raise RuntimeError(f"{name} is not in the release's SHA256SUMS")
                return None
            data = fetch(f"{base}/{name}", timeout=300)
            if hashlib.sha256(data).hexdigest() != sums[name]:
                raise RuntimeError(f"{name}: checksum mismatch")
            return data

        tarname = f"usb-debug-{version}.tar.gz"
        (tmp / tarname).write_bytes(get(tarname, True))
        with tarfile.open(tmp / tarname) as tf:
            _safe_extract(tf, tmp / "x")
        tree = tmp / "x" / f"usb-debug-{version}"
        for name, rel in ASSETS_OPTIONAL.items():
            data = get(name, False)
            if data is not None:
                p = tree / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                if rel.startswith("bin/"):
                    p.chmod(0o755)
        os.replace(tree, final)
    return final


def switch(root, version):
    tmp = root / ".current.new"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(version, tmp)
    os.replace(tmp, root / "current")


def self_update(a):
    root = installed_root()
    try:
        want = (a.to or latest()).lstrip("v")
    except OSError as e:
        print(f"usb-debug: cannot reach GitHub: {e}", file=sys.stderr)
        return 1
    newer = vtuple(want) > vtuple(VERSION)
    if getattr(a, "quiet", False) and not newer and not a.to:
        return 0
    if a.check:
        print(f"usb-debug {want} is available (this is {VERSION})" if newer
              else f"usb-debug {VERSION} is up to date")
        return 0
    if root is None:
        print(f"usb-debug: running from {ROOT}, not an installed release: update it with git, "
              f"or install one with install.sh (README.md)", file=sys.stderr)
        return 1
    if not newer and not a.to:
        print(f"usb-debug {VERSION} is up to date")
        return 0
    try:
        install(want, root)
    except (OSError, RuntimeError, tarfile.TarError) as e:
        print(f"usb-debug: update to {want} failed: {e}", file=sys.stderr)
        return 1
    switch(root, want)
    print(f"usb-debug: {VERSION} -> {want}")
    return 0


def aport(a):
    src = ROOT / "APKBUILD.release"
    if not src.is_file():
        print(f"usb-debug: {ROOT} has no released APKBUILD (a source checkout?); "
              "releases attach it with its checksum filled in", file=sys.stderr)
        return 1
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, d / "APKBUILD")
    print(d / "APKBUILD")
    return 0
