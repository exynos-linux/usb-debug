# SPDX-License-Identifier: GPL-2.0-only
"""The console protocol (docs/PROTOCOL.md), against the real console binary."""
import os
import tempfile
import unittest
from pathlib import Path

from tests.fakeconsole import TOP, FakeConsole, talk


class ConsoleProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fc = FakeConsole()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.fc.close()
        cls.tmp.cleanup()

    def one(self, line):
        return talk(self.fc.port, [line])[1][0]

    def test_banner_and_prompt(self):
        banner, _ = talk(self.fc.port, [])
        self.assertIn(b"USB debug console (usb-debug ", banner)
        self.assertIn(b"Commands:", banner)

    def test_version(self):
        version = (TOP / "VERSION").read_text().strip()
        r = self.one("version").decode()
        self.assertTrue(r.startswith(f"usb-debug {version} board "), r)
        # no device tree on the test host: no board, no SoC
        self.assertIn("board none soc none", r)

    def test_unknown_and_empty(self):
        _, (r1, r2) = talk(self.fc.port, ["frobnicate", ""])
        self.assertEqual(r1, b"unknown command\n")
        self.assertEqual(r2, b"")

    def test_cat_size_put(self):
        data = bytes(range(256)) * 300 + b"end"
        src = self.d / "src.bin"
        src.write_bytes(data)
        self.assertEqual(self.one(f"size {src}"), f"{len(data)}\n".encode())
        self.assertEqual(self.one(f"cat {src}"), data)
        dst = self.d / "dst.bin"
        _, (r,) = talk(self.fc.port, [f"put {dst} {len(data)}\n".encode() + data])
        self.assertEqual(r, f"ok {len(data)}\n".encode())
        self.assertEqual(dst.read_bytes(), data)

    def test_errors(self):
        missing = self.d / "nope"
        self.assertEqual(self.one(f"cat {missing}"), b"error\n")
        self.assertEqual(self.one(f"size {missing}"), b"error\n")
        self.assertEqual(self.one(f"ls {missing}"), b"error\n")
        self.assertEqual(self.one(f"evread {missing} 1 1"), b"error\n")
        self.assertTrue(self.one(f"insmod {missing}").startswith(b"FAILED\nerrno 2"))
        self.assertEqual(self.one("put"), b"unknown command\n")
        self.assertEqual(self.one("write /x"), b"usage: write PATH VALUE\n")

    def test_write_ls(self):
        f = self.d / "w.txt"
        f.write_text("")
        self.assertEqual(self.one(f"write {f} hello world"), b"ok\n")
        self.assertEqual(f.read_text(), "hello world")
        names = self.one(f"ls {self.d}").decode().split()
        self.assertIn("w.txt", names)

    def test_run(self):
        r = self.one("run /bin/echo one two").decode()
        self.assertEqual(r, "one two\nrun: exit 0 signal 0\n")
        r = self.one("run /bin/false").decode()
        self.assertEqual(r, "run: exit 1 signal 0\n")
        r = self.one(f"run {self.d}/missing").decode()
        self.assertEqual(r, "run: exit 127 signal 0\n")

    def test_board_specific_commands_without_a_board(self):
        self.assertEqual(self.one("discard /dev/sda31"), b"discard: not an approved partition\n")
        self.assertEqual(self.one("ulog"), b"ulog: no RAM log on this device\n")
        if not os.path.exists("/dev/i2c-0"):
            self.assertEqual(self.one("i2cr 0x25 0x51 3"), b"i2c error (no ACK?)\n")

    @unittest.skipIf(os.geteuid() == 0, "would map physical memory as root")
    def test_peek_without_devmem_access(self):
        self.assertEqual(self.one("peek 0x14060808"), b"error\n")
        self.assertEqual(self.one("poke 0x14060808 0x12345671"), b"error\n")
        self.assertEqual(self.one("memcat 0x1000 16"), b"error\n")


if __name__ == "__main__":
    unittest.main()
