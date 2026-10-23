# SPDX-License-Identifier: GPL-2.0-only
"""setup.sh and "usb-debug self-update" against a local release mirror
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
        for part in ("host", "console", "tools", "docs"):
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
        self.env = dict(os.environ, USB_DEBUG_RELEASES=f"http://127.0.0.1:{self.srv.server_port}",
                        XDG_DATA_HOME=str(self.d / "data"), HOME=str(self.d / "home"))
        self.data = self.d / "data" / "usb-debug"
        self.bin = self.d / "home" / ".local" / "bin"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def setup_sh(self, *args, check=True):
        r = subprocess.run(["sh", str(TOP / "setup.sh"), *args], env=self.env, capture_output=True, text=True)
        if check and r.returncode:
            self.fail(f"setup.sh {args}: {r.returncode}\n{r.stderr}")
        return r

    def test_setup_and_self_update(self):
        release(self.mirror, VERSION, extra={"uinput-type-aarch64": b"\x7fELF fake"})
        self.setup_sh("--version", VERSION)
        inst = self.data / VERSION
        self.assertTrue((inst / "host" / "usb-debug").is_file())
        self.assertEqual((inst / "APKBUILD.release").read_text(), f"pkgver={VERSION}\n")
        self.assertTrue(os.access(inst / "bin" / "aarch64" / "uinput-type", os.X_OK))
        self.assertEqual(os.readlink(self.data / "current"), VERSION)
        exe = self.bin / "usb-debug"
        r = subprocess.run([str(exe), "version"], env=self.env, capture_output=True, text=True, check=True)
        self.assertTrue(r.stdout.startswith(f"usb-debug {VERSION} "), r.stdout)
        # the installed copy writes its APKBUILD for a device repository
        out = self.d / "aport"
        subprocess.run([str(exe), "aport", str(out)], env=self.env, check=True, capture_output=True)
        self.assertEqual((out / "APKBUILD").read_text(), f"pkgver={VERSION}\n")
        # a second run is a no-op
        self.assertIn("already installed", self.setup_sh("--version", VERSION).stderr)

        release(self.mirror, "99.0.0")
        r = subprocess.run([str(exe), "self-update", "--version", "99.0.0"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.data / "current"), "99.0.0")
        r = subprocess.run([str(exe), "version"], env=self.env, capture_output=True, text=True, check=True)
        self.assertTrue(r.stdout.startswith("usb-debug 99.0.0 "), r.stdout)
        # the old release stays for repositories that pin it
        self.assertTrue((self.data / VERSION / "host" / "usb-debug").is_file())

    def test_pinned_install_without_link(self):
        release(self.mirror, VERSION)
        self.setup_sh("--version", VERSION, "--no-link")
        self.assertTrue((self.data / VERSION / "host" / "usb-debug").is_file())
        self.assertFalse((self.data / "current").exists())
        self.assertFalse((self.bin / "usb-debug").exists())

    def test_checksum_mismatch(self):
        release(self.mirror, VERSION, corrupt=True)
        r = self.setup_sh("--version", VERSION, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum", r.stderr)
        self.assertFalse((self.data / VERSION).exists())

    def test_self_update_refuses_a_checkout(self):
        r = subprocess.run([str(TOP / "host" / "usb-debug"), "self-update", "--version", VERSION],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not an installed release", r.stderr)


if __name__ == "__main__":
    unittest.main()
