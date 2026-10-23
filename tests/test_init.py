# SPDX-License-Identifier: GPL-2.0-only
"""The nolibc bring-up init, built for this host as a test build
(USBDBG_TEST: no register access, no restarts) and run as an ordinary
process with a fake device tree, command line and block devices.

Needs the kernel's nolibc: NOLIBC=<linux>/tools/include/nolibc (CI fetches
it); skipped without it, and always as root (the init mounts, and a real
build would write registers and restart the machine).
"""
import os
import platform
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.fakeconsole import TOP

NOLIBC = os.environ.get("NOLIBC")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(NOLIBC and Path(NOLIBC, "nolibc.h").is_file(), "NOLIBC is not set")
@unittest.skipIf(os.geteuid() == 0, "never run the init as root")
@unittest.skipUnless(platform.machine() in ("x86_64", "aarch64"), "nolibc arch not tested")
class Init(unittest.TestCase):
    def build_and_run(self, compatible, conf=None, seconds=7, lines=()):
        tmp = Path(self.tmp.name)
        dt = tmp / "dt"
        dt.mkdir(exist_ok=True)
        (dt / "compatible").write_bytes(compatible)
        (dt / "model").write_bytes(b"Test Model\0")
        (tmp / "cmdline").write_text("console=tty0\n")
        blk = tmp / "block"
        for name, part in (("sda31", "userdata"), ("sda1", "efs"), ("sdb1", "userdata")):
            (blk / name).mkdir(parents=True, exist_ok=True)
            (blk / name / "uevent").write_text(f"MAJOR=8\nDEVNAME={name}\nPARTNAME={part}\n")
        port = free_port()
        cfg = tmp / "testcfg.h"
        cfg.write_text(f'#define USBDBG_TEST 1\n#define USBDBG_DT "{dt}"\n#define USBDBG_CMDLINE "{tmp}/cmdline"\n'
                       f'#define USBDBG_CONF "{tmp}/usb-debug.conf"\n#define USBDBG_PORT {port}\n'
                       f'#define USBDBG_SYSBLOCK "{blk}"\n')
        if conf is not None:
            (tmp / "usb-debug.conf").write_text(conf)
        out = tmp / "build"
        khdr = os.environ.get("KHDR")  # UAPI headers matching NOLIBC (default: the system's)
        subprocess.run(["make", "-s", "-C", str(TOP / "console"), "init", f"O={out}", f"NOLIBC={NOLIBC}",
                        *([f"KHDR={khdr}"] if khdr else []), "CROSS_COMPILE=",
                        f"EXTRA_CFLAGS=-include {cfg}", "-B"], check=True, capture_output=True)
        log = tmp / "out.log"
        with open(log, "wb") as f:
            p = subprocess.Popen([str(out / "init")], stdout=f, stderr=subprocess.STDOUT, cwd=tmp)
        answers = []
        try:
            deadline = time.time() + 15
            while b"debug console on port" not in log.read_bytes() and time.time() < deadline:
                time.sleep(0.2)
            if lines:
                s = socket.create_connection(("127.0.0.1", port), timeout=10)

                def prompt():
                    buf = b""
                    while not buf.endswith(b"> "):
                        d = s.recv(65536)
                        if not d:
                            break
                        buf += d
                    return buf[:-2]
                prompt()
                for ln in lines:
                    s.sendall(ln.encode() + b"\n")
                    answers.append(prompt().decode())
                s.close()
            time.sleep(seconds)
        finally:
            p.kill()
            p.wait()
        return log.read_text(errors="replace"), answers

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_gtactive3wifi(self):
        text, (version, d_ok, d_efs, d_other, d_disk, ulog) = self.build_and_run(
            b"samsung,gtactive3wifi\0samsung,exynos9810\0", seconds=1,
            lines=["version", "discard /dev/sda31", "discard /dev/sda1", "discard /dev/sdb1",
                   "discard /dev/sda", "ulog"])
        self.assertIn("Galaxy Tab Active3 - mainline Linux is alive", text)
        self.assertIn("board gtactive3wifi, SoC exynos9810", text)
        # register access is off in the test build: arming fails loudly
        self.assertIn("WARNING: could not arm the reset target (/dev/mem)", text)
        self.assertIn("bitbang: cannot map pin controller", text)
        mods = [ln.split()[-1] for ln in text.splitlines() if ln.strip().startswith("missing /lib/modules/")]
        self.assertEqual(mods[:3], ["/lib/modules/phy-exynos5-usbdrd.ko", "/lib/modules/dwc3.ko",
                                    "/lib/modules/dwc3-exynos.ko"])
        self.assertEqual(mods[3:7], ["/lib/modules/phy-exynos-ufs.ko", "/lib/modules/ufshcd-core.ko",
                                     "/lib/modules/ufshcd-pltfrm.ko", "/lib/modules/ufs-exynos.ko"])
        self.assertEqual(mods[-2:], ["/lib/modules/gpu-sched.ko", "/lib/modules/panfrost.ko"])
        self.assertEqual(len(mods), 3 + 4 + 19)  # USB, storage, the board's late list
        self.assertIn("VOLUME UP = restart into Download mode.", text)
        self.assertTrue(version.startswith("usb-debug "), version)
        self.assertIn("board gtactive3wifi soc exynos9810", version)
        self.assertEqual(d_ok, "discard: approved (test build)\n")
        for r in (d_efs, d_other, d_disk):
            self.assertEqual(r, "discard: not an approved partition\n")
        self.assertEqual(ulog, "ulog: cannot map\n")

    def test_generic_device(self):
        text, (version, d) = self.build_and_run(b"vendor,unknown\0vendor,unknownsoc\0", seconds=1,
                                                 lines=["version", "discard /dev/sda31"])
        self.assertIn("exynos-linux device - mainline Linux is alive", text)
        self.assertIn("WARNING: unknown SoC, the reset target is not armed", text)
        self.assertNotIn("insmod", text)
        self.assertNotIn("missing /lib/modules", text)
        self.assertIn("board none soc none", version)
        self.assertEqual(d, "discard: not an approved partition\n")

    def test_soc_without_board(self):
        text, _ = self.build_and_run(b"samsung,other9810\0samsung,exynos9810\0", seconds=0)
        self.assertIn("board none (generic), SoC exynos9810", text)
        self.assertIn("WARNING: could not arm the reset target", text)  # it tried: the SoC is known
        self.assertIn("missing /lib/modules/dwc3.ko", text)
        self.assertNotIn("bitbang", text)

    def test_conf_replaces_the_late_list(self):
        text, _ = self.build_and_run(b"samsung,gtactive3wifi\0samsung,exynos9810\0", seconds=0,
                                     conf="# test\nmodule /lib/modules/extra.ko foo=1\nwrite /nonexistent 1\nbogus\n")
        self.assertIn("late list from", text)
        self.assertIn("missing /lib/modules/extra.ko", text)
        self.assertIn("write /nonexistent: FAILED", text)
        self.assertIn("bad line: bogus", text)
        self.assertNotIn("panfrost", text)
        self.assertNotIn("ufs-exynos", text)  # the SoC's late modules too


if __name__ == "__main__":
    unittest.main()
