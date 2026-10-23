# SPDX-License-Identifier: GPL-2.0-only
"""The device layer's scripts against a fake root (USBDBG_ROOT)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parent.parent
LIBEXEC = TOP / "device" / "libexec"
GENERATOR = TOP / "device" / "generator" / "duranium-usb-debug-generator"

STUBS = {
    # configfs is "mounted"; modules load; ip only logs
    "mountpoint": "exit 0",
    "mount": "exit 0",
    "modprobe": "exit 0",
    "ip": 'echo "ip $*" >> "$USBDBG_ROOT/ip.log"',
}


class Device(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            p = self.bin / name
            p.write_text(f"#!/bin/sh\n{body}\n")
            p.chmod(0o755)
        for d in ("sys/class/udc", "sys/kernel/config/usb_gadget", "run", "proc",
                  "sys/firmware/devicetree/base", "usr/lib/duranium-usb-debug/conf.d"):
            (self.root / d).mkdir(parents=True)
        (self.root / "proc/cmdline").write_text("console=tty0 quiet\n")
        (self.root / "sys/firmware/devicetree/base/model").write_bytes(b"Samsung Galaxy Tab Active3 (SM-T570)\0")
        (self.root / "sys/firmware/devicetree/base/compatible").write_bytes(
            b"samsung,gtactive3wifi\0samsung,exynos9810\0")
        self.env = dict(os.environ, USBDBG_ROOT=str(self.root), USBDBG_LIBEXEC=str(LIBEXEC),
                        PATH=f"{self.bin}:{os.environ['PATH']}")

    def tearDown(self):
        self.tmp.cleanup()

    def conf(self, text, name="test.conf"):
        (self.root / "usr/lib/duranium-usb-debug/conf.d" / name).write_text(text)

    def load(self, *names):
        script = f'. "{LIBEXEC}/usb-debug-config"; usbdbg_load; ' + "; ".join(
            f'echo "{n}=${n}"' for n in names)
        r = subprocess.run(["sh", "-c", script], env=self.env, capture_output=True, text=True, check=True)
        return dict(ln.split("=", 1) for ln in r.stdout.splitlines()), r.stderr

    def test_config_defaults(self):
        v, err = self.load("UDC", "CONSOLE_PORT", "SHELL_PORT", "MANUFACTURER", "UDC_WAIT")
        self.assertEqual(v, {"UDC": "", "CONSOLE_PORT": "23", "SHELL_PORT": "2323",
                             "MANUFACTURER": "postmarketOS", "UDC_WAIT": "10"})
        self.assertEqual(err, "")

    def test_config_files_quotes_and_cmdline(self):
        self.conf('# comment\nPRODUCT="Duranium Debug (Tab Active3)"\nSERIAL=\'x y\'\nUDC=*.dwc3\nBOGUS=1\n$(touch /tmp/pwned)=1\n')
        (self.root / "etc/duranium-usb-debug/conf.d").mkdir(parents=True)
        (self.root / "etc/duranium-usb-debug/conf.d/admin.conf").write_text("SHELL_PORT=0\nUDC_WAIT=abc")
        v, err = self.load("PRODUCT", "SERIAL", "UDC", "SHELL_PORT", "UDC_WAIT")
        self.assertEqual(v["PRODUCT"], "Duranium Debug (Tab Active3)")
        self.assertEqual(v["SERIAL"], "x y")
        self.assertEqual(v["UDC"], "*.dwc3")
        self.assertEqual(v["SHELL_PORT"], "0")
        self.assertEqual(v["UDC_WAIT"], "10")
        self.assertIn("unknown setting BOGUS", err)
        self.assertIn("unknown setting $(touch /tmp/pwned)", err)
        (self.root / "proc/cmdline").write_text("quiet duranium.usb_debug.udc=other.udc\n")
        v, _ = self.load("UDC")
        self.assertEqual(v["UDC"], "other.udc")

    def generate(self):
        normal, early = Path(self.tmp.name) / "gen", Path(self.tmp.name) / "early"
        subprocess.run(["sh", str(GENERATOR), str(normal), str(early), str(Path(self.tmp.name) / "late")],
                       env=self.env, check=True)
        return normal, early

    def test_generator_defaults_write_nothing(self):
        normal, early = self.generate()
        self.assertFalse(normal.exists() and any(normal.iterdir()))
        self.assertFalse(early.exists() and any(early.iterdir()))

    def test_generator_ports(self):
        self.conf("CONSOLE_PORT=2424\nSHELL_PORT=0\nKLOG_PORT=x\n")
        normal, early = self.generate()
        dropin = normal / "duranium-debug-console.socket.d" / "50-port.conf"
        self.assertEqual(dropin.read_text().splitlines()[1:], ["[Socket]", "ListenStream=", "ListenStream=2424"])
        mask = early / "duranium-debug-shell.socket"
        self.assertTrue(mask.is_symlink())
        self.assertEqual(os.readlink(mask), "/dev/null")
        self.assertFalse((normal / "duranium-debug-klog.socket.d").exists())
        self.assertFalse((normal / "duranium-debug-sshd.socket.d").exists())

    def gadget(self):
        return subprocess.run(["sh", str(LIBEXEC / "usb-debug-gadget-setup")], env=self.env,
                              capture_output=True, text=True)

    def test_gadget_setup(self):
        (self.root / "sys/class/udc/10c00000.dwc3").mkdir()
        g = self.root / "sys/kernel/config/usb_gadget/g_debug"
        # the kernel would create the interface once bound
        (g / "functions/ncm.usb0").mkdir(parents=True)
        (g / "functions/ncm.usb0/ifname").write_text("usb0\n")
        r = self.gadget()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rd = lambda p: (g / p).read_text().strip()  # noqa: E731
        self.assertEqual(rd("UDC"), "10c00000.dwc3")
        self.assertEqual(rd("idVendor"), "0x1d6b")
        self.assertEqual(rd("idProduct"), "0x0104")
        self.assertEqual(rd("strings/0x409/product"), "Duranium Debug (Samsung Galaxy Tab Active3 (SM-T570))")
        self.assertEqual(rd("strings/0x409/serialnumber"), "gtactive3wifi-duranium-debug")
        self.assertEqual(rd("functions/ncm.usb0/dev_addr"), "02:57:0a:00:00:01")
        self.assertEqual(rd("functions/ncm.usb0/host_addr"), "02:57:0a:00:00:02")
        self.assertTrue((g / "configs/c.1/ncm.usb0").is_symlink())
        self.assertEqual((self.root / "ip.log").read_text(), "ip link set usb0 up\n")
        self.assertEqual((self.root / "run/duranium-usb-debug.iface").read_text().strip(), "usb0")
        # bound already (carried over from the initrd): nothing to do
        (self.root / "ip.log").unlink()
        self.assertEqual(self.gadget().returncode, 0)
        self.assertFalse((self.root / "ip.log").exists())

    def test_gadget_udc_choice_and_strings(self):
        for u in ("a.other", "10c00000.dwc3"):
            (self.root / "sys/class/udc" / u).mkdir()
        self.conf('UDC=*.dwc3\nPRODUCT=Duranium Debug (Tab Active3)\nSERIAL=gtactive3-duranium-debug\n')
        g = self.root / "sys/kernel/config/usb_gadget/g_debug"
        (g / "functions/ncm.usb0").mkdir(parents=True)
        (g / "functions/ncm.usb0/ifname").write_text("usb0\n")
        r = self.gadget()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((g / "UDC").read_text().strip(), "10c00000.dwc3")
        self.assertEqual((g / "strings/0x409/product").read_text().strip(), "Duranium Debug (Tab Active3)")
        self.assertEqual((g / "strings/0x409/serialnumber").read_text().strip(), "gtactive3-duranium-debug")

    def test_gadget_no_udc(self):
        self.conf("UDC_WAIT=0\n")
        r = self.gadget()
        self.assertEqual(r.returncode, 0)
        self.assertIn("no UDC", r.stdout)


if __name__ == "__main__":
    unittest.main()
