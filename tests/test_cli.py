# SPDX-License-Identifier: GPL-2.0-only
"""The host tool against the real console binary on a loopback port."""
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from tests.fakeconsole import TOP, FakeConsole

CLI = TOP / "host" / "usb-debug"


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fc = FakeConsole()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        # a device repository whose config.toml moves the console port
        cls.repo = cls.d / "repo"
        cls.repo.mkdir()
        (cls.repo / "config.toml").write_text(
            f'schema = 1\n[device]\nname = "Test tablet"\n[console]\nport = {cls.fc.port}\nshell_port = 2323\n')

    @classmethod
    def tearDownClass(cls):
        cls.fc.close()
        cls.tmp.cleanup()

    def run_cli(self, *args, input=None, env=None, check=True):
        e = dict(os.environ, USB_DEBUG_ADDR="127.0.0.1", USB_DEBUG_LOCK="", USB_DEBUG_RUNDIR=str(self.d),
                 XDG_STATE_HOME=str(self.d / "state"), XDG_CACHE_HOME=str(self.d / "cache"))
        e.pop("USB_DEBUG_REPO", None)
        e.update(env or {})
        r = subprocess.run([sys.executable, str(CLI), "--repo", str(self.repo), *args], input=input,
                           capture_output=True, env=e, timeout=60)
        if check and r.returncode != 0:
            self.fail(f"usb-debug {' '.join(args)}: exit {r.returncode}\n{r.stderr.decode()}")
        return r

    def test_config_from_repo(self):
        r = self.run_cli("config", "console.port")
        self.assertEqual(r.stdout.decode().strip(), str(self.fc.port))
        r = self.run_cli("config", "installer.download_usb")
        self.assertEqual(r.stdout.decode().strip(), "04e8:685d")

    def test_cmd(self):
        r = self.run_cli("cmd", "version")
        self.assertTrue(r.stdout.startswith(b"usb-debug "), r.stdout)
        r = self.run_cli("cmd", "frobnicate")
        self.assertEqual(r.stdout, b"unknown command\n")
        r = self.run_cli("cmd", "--check", "frobnicate", check=False)
        self.assertEqual(r.returncode, 1)

    def test_put_get(self):
        data = os.urandom(3 << 20)  # past the progress threshold
        src, dst, back = self.d / "a.bin", self.d / "b.bin", self.d / "c.bin"
        src.write_bytes(data)
        r = self.run_cli("put", str(src), str(dst))
        self.assertEqual(r.stdout.decode().strip(), f"ok {len(data)}")
        self.assertEqual(dst.read_bytes(), data)
        r = self.run_cli("get", str(dst), str(back))
        self.assertEqual(back.read_bytes(), data)
        self.assertIn(f"{len(data)} bytes", r.stdout.decode())
        # the first bytes only
        r = self.run_cli("get", str(dst), str(back), "1000")
        self.assertEqual(back.read_bytes(), data[:1000])
        # stdin
        r = self.run_cli("put", "-", str(dst), input=b"hello")
        self.assertEqual(dst.read_bytes(), b"hello")

    def test_put_failure_exits_nonzero(self):
        r = self.run_cli("put", "-", str(self.d / "no" / "such" / "dir"), input=b"x", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.decode().startswith("error"))

    def test_sh(self):
        r = self.run_cli("sh", "-c", 'echo "args: $*"; exit 3', "x", "y", check=False)
        self.assertEqual(r.stdout.decode(), "args: x y\n")
        self.assertEqual(r.returncode, 3)
        self.assertEqual([p for p in self.d.iterdir() if p.name.startswith("usb-debug-sh")], [])

    def test_shot(self):
        w, h = 8, 4
        px = b"".join(struct.pack("<BBBB", x * 30, y * 60, 200, 0) for y in range(h) for x in range(w))
        fb = self.d / "fb.bin"
        fb.write_bytes(px + b"trailing bytes the screenshot must ignore")
        out = self.d / "s.png"
        r = self.run_cli("shot", str(out), "--src", str(fb), "--size", f"{w}x{h}")
        self.assertIn("non-black", r.stdout.decode())
        png = out.read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (w, h))
        # first pixel in memory B=0 G=0 R=200 X=0 (XRGB8888 little endian) -> RGB (200, 0, 0)
        idat = png.index(b"IDAT")
        n = struct.unpack(">I", png[idat - 4:idat])[0]
        raw = zlib.decompress(png[idat + 4:idat + 4 + n])
        self.assertEqual(raw[1:4], bytes([200, 0, 0]))

    def test_wait_console(self):
        r = self.run_cli("wait", "--console", "3")
        self.assertTrue(r.stdout.decode().startswith("up on"))

    def test_version_device(self):
        r = self.run_cli("version", "--device")
        self.assertIn("device: usb-debug", r.stdout.decode())

    def test_no_link(self):
        r = self.run_cli("cmd", "version", env={"USB_DEBUG_ADDR": "", "USB_DEBUG_IFACE": "nonexistent0"},
                         check=False)
        # an interface name that does not exist still gives an address; the
        # connection then fails cleanly
        self.assertEqual(r.returncode, 1)
        self.assertIn("usb-debug:", r.stderr.decode())

    def test_notify_dry_run(self):
        r = self.run_cli("notify", "--dry-run", "--info", "Press POWER", "now")
        self.assertIn("notify-send", r.stdout.decode())
        log = (self.repo / "logs" / "user-requests.log").read_text()
        self.assertIn("Press POWER: now", log)


if __name__ == "__main__":
    unittest.main()
