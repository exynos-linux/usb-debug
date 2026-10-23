# SPDX-License-Identifier: GPL-2.0-only
"""install.sh and "usb-debug self-update" against a local release mirror
($USB_DEBUG_RELEASES), built from this tree."""
import functools
import hashlib
import http.server
import io
import os
import subprocess
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

from tests.fakeconsole import TOP

VERSION = (TOP / "VERSION").read_text().strip()


def release(root, version, extra=None, corrupt=False):
    """A release directory like GitHub's: vX/usb-debug-X.tar.gz, SHA256SUMS ..."""
    d = root / f"v{version}"
    d.mkdir(parents=True)
    tarname = f"usb-debug-{version}.tar.gz"
    with tarfile.open(d / tarname, "w:gz") as tf:
        for part in ("host", "console", "tools", "docs", "devices"):
            tf.add(TOP / part, f"usb-debug-{version}/{part}",
                   filter=lambda t: None if "__pycache__" in t.name else t)
        data = f"{version}\n".encode()
        info = tarfile.TarInfo(f"usb-debug-{version}/VERSION")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    files = {tarname: (d / tarname).read_bytes()}
    (d / "APKBUILD").write_text(f"pkgver={version}\n")
    files["APKBUILD"] = (d / "APKBUILD").read_bytes()
    for name, data in (extra or {}).items():
        (d / name).write_bytes(data)
        files[name] = data
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files.items())
    if corrupt:
        sums = sums.replace(sums[0], "0" if sums[0] != "0" else "1", 1)
    (d / "SHA256SUMS").write_text(sums)
    return d


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.mirror = self.d / "mirror"
        handler = functools.partial(QuietHandler, directory=str(self.mirror))
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.home = self.d / "home"
        self.home.mkdir()
        self.env = dict(os.environ, USB_DEBUG_RELEASES=f"http://127.0.0.1:{self.srv.server_port}",
                        USB_DEBUG_API="http://127.0.0.1:1/none", HOME=str(self.home),
                        XDG_DATA_HOME=str(self.home / "data"), XDG_CONFIG_HOME=str(self.home / "config"),
                        XDG_STATE_HOME=str(self.home / "state"), XDG_CACHE_HOME=str(self.home / "cache"))
        for k in ("USB_DEBUG_REPO", "USB_DEBUG_DEVICE"):
            self.env.pop(k, None)
        self.data = self.home / "data" / "usb-debug"
        self.bin = self.home / ".local" / "bin"
        self.sysroot = self.d / "sysroot"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def install(self, *args, check=True, script=None):
        # run from a directory that is not a checkout or a device repository
        r = subprocess.run(["sh", str(script or self.release_script()), *args], env=self.env,
                           capture_output=True, text=True, cwd=self.home)
        if check and r.returncode:
            self.fail(f"install.sh {args}: {r.returncode}\n{r.stderr}")
        return r

    def release_script(self):
        """install.sh as a release asset: outside any checkout."""
        p = self.d / "install.sh"
        if not p.exists():
            p.write_text((TOP / "install.sh").read_text())
        return p

    def tool(self, name, *args, check=True):
        return subprocess.run([str(self.bin / name), *args], env=self.env, capture_output=True, text=True,
                              check=check, cwd=self.home)

    def test_install_device_self_update_uninstall(self):
        release(self.mirror, VERSION, extra={"uinput-type-aarch64": b"\x7fELF fake"})
        r = self.install("--yes", f"--version=v{VERSION}", "--device=samsung-gtactive3wifi",
                         f"--sysroot={self.sysroot}")
        self.assertEqual(r.stdout.strip().splitlines()[-1], f"usb-debug {VERSION} installed")
        inst = self.data / VERSION
        self.assertTrue((inst / "host" / "usb-debug").is_file())
        self.assertEqual((inst / "APKBUILD.release").read_text(), f"pkgver={VERSION}\n")
        self.assertTrue(os.access(inst / "bin" / "aarch64" / "uinput-type", os.X_OK))
        self.assertTrue(os.readlink(self.data / "current").endswith(VERSION))
        # both names, the "tab" shorthand, and the device's settings
        self.assertTrue(self.tool("usb-debug", "version").stdout.startswith(f"usb-debug {VERSION} "))
        self.assertTrue(self.tool("tab", "version").stdout.startswith(f"usb-debug {VERSION} "))
        self.assertEqual(self.tool("tab", "config", "console.panel").stdout.strip(), "1200x1920")
        self.assertEqual(self.tool("tab", "config", "device.id").stdout.strip(), "samsung-gtactive3wifi")
        # root-owned files, in the test sysroot
        rules = self.sysroot / "etc/udev/rules.d/70-usb-debug.rules"
        self.assertIn('ATTR{idProduct}=="685d"', rules.read_text())
        nm = self.sysroot / "etc/NetworkManager/system-connections/usb-debug.nmconnection"
        self.assertEqual(nm.stat().st_mode & 0o777, 0o600)
        text = nm.read_text()
        self.assertIn("mac-address=02:57:0A:00:00:02", text)
        self.assertNotIn("@", text)
        # the released APKBUILD for a device repository
        out = self.d / "aport"
        self.tool("usb-debug", "aport", str(out))
        self.assertEqual((out / "APKBUILD").read_text(), f"pkgver={VERSION}\n")
        # a second run is a no-op
        self.assertIn("already installed", self.install("--yes", f"--version={VERSION}", "--no-system").stderr)

        release(self.mirror, "99.0.0")
        r = self.tool("tab", "self-update", "--version", "99.0.0", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.data / "current"), "99.0.0")
        self.assertTrue(self.tool("tab", "version").stdout.startswith("usb-debug 99.0.0 "))
        self.assertTrue((self.data / VERSION / "host" / "usb-debug").is_file())  # kept for pins

        r = self.install("--yes", "--uninstall", f"--sysroot={self.sysroot}")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "usb-debug uninstalled")
        self.assertFalse(self.data.exists())
        self.assertFalse((self.bin / "tab").exists() or (self.bin / "tab").is_symlink())
        self.assertFalse((self.bin / "usb-debug").is_symlink())
        self.assertFalse(rules.exists() or nm.exists())
        self.assertFalse((self.home / "config" / "usb-debug" / "config.toml").exists())

    def test_pinned_install_without_link(self):
        release(self.mirror, VERSION)
        self.install("--yes", f"--version={VERSION}", "--no-link", f"--sysroot={self.sysroot}")
        self.assertTrue((self.data / VERSION / "host" / "usb-debug").is_file())
        self.assertFalse((self.data / "current").exists())
        self.assertFalse((self.bin / "usb-debug").exists())
        self.assertFalse(self.sysroot.exists())  # --no-link: no system changes

    def test_checksum_mismatch(self):
        release(self.mirror, VERSION, corrupt=True)
        r = self.install("--yes", f"--version={VERSION}", "--no-system", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum", r.stderr)
        self.assertFalse((self.data / VERSION).exists())

    def test_missing_release_fails(self):
        r = self.install("--yes", "--version=0.0.1", "--no-system", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("installed", r.stdout)

    def test_unknown_option_fails(self):
        r = self.install("--frobnicate", check=False)
        self.assertNotEqual(r.returncode, 0)

    def test_checkout_and_auto_update(self):
        # a stand-in for "systemctl --user": the test's units live in a
        # private XDG_CONFIG_HOME that no real user manager reads
        fake = self.d / "fakebin"
        fake.mkdir()
        (fake / "systemctl").write_text(f'#!/bin/sh\necho "$*" >> {self.d}/systemctl.calls\n')
        (fake / "systemctl").chmod(0o755)
        self.env["PATH"] = f"{fake}:{self.env['PATH']}"
        r = self.install("--yes", "--no-system", "--auto-update", script=TOP / "install.sh")
        self.assertIn("--user enable --now usb-debug-update.timer", (self.d / "systemctl.calls").read_text())
        self.assertEqual(r.stdout.strip().splitlines()[-1], f"usb-debug {VERSION} installed")
        self.assertEqual(os.readlink(self.data / "current"), str(TOP))
        self.assertIn(str(TOP), self.tool("tab", "version").stdout)
        unit = self.home / "config" / "systemd" / "user" / "usb-debug-update.service"
        self.assertIn(f"ExecStart={self.bin}/usb-debug self-update --quiet", unit.read_text())
        self.assertTrue((unit.parent / "usb-debug-update.timer").is_file())
        # a checkout updates with git, not self-update
        r = self.tool("tab", "self-update", "--version", VERSION, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not an installed release", r.stderr)
        # uninstall leaves the checkout alone
        self.install("--yes", "--uninstall", "--no-system", script=TOP / "install.sh")
        self.assertTrue((TOP / "host" / "usb-debug").is_file())
        self.assertFalse(unit.exists())


if __name__ == "__main__":
    unittest.main()
