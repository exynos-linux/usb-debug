# SPDX-License-Identifier: GPL-2.0-only
"""How the host tool finds and reads a device repository's config.toml."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
from usb_debug.config import Config, find_repo  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.old = os.getcwd()
        os.environ.pop("USB_DEBUG_REPO", None)

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def test_defaults_without_repo(self):
        os.chdir(self.d)
        c = Config()
        self.assertIsNone(c.repo)
        self.assertEqual(c.get("console.port"), 23)
        self.assertEqual(c.get("console.shell_port"), 2323)
        self.assertEqual(c.get("installer.download_usb"), "04e8:685d")

    def test_found_from_a_subdirectory(self):
        (self.d / "config.toml").write_text('schema = 1\n[console]\nport = 2300\n')
        sub = self.d / "a" / "b"
        sub.mkdir(parents=True)
        os.chdir(sub)
        self.assertEqual(find_repo(), self.d.resolve())
        c = Config()
        self.assertEqual(c.get("console.port"), 2300)
        self.assertEqual(c.get("console.shell_port"), 2323)  # default kept

    def test_unrelated_config_toml_is_ignored(self):
        (self.d / "config.toml").write_text('title = "a website"\n')
        os.chdir(self.d)
        self.assertIsNone(find_repo())

    def test_scripts_config_is_preferred(self):
        (self.d / "config.toml").write_text('[console]\nport = 1\n')
        s = self.d / "scripts"
        s.mkdir()
        (s / "config").write_text('#!/bin/sh\n[ "$1" = json ] && echo \'{"console": {"port": 4242}, '
                                  '"device": {"id": "x-y"}}\'\n')
        (s / "config").chmod(0o755)
        c = Config(str(self.d))
        self.assertEqual(c.get("console.port"), 4242)
        self.assertEqual(c.get("device.id"), "x-y")
        self.assertEqual(c.get("console.klog_port"), 24)

    def test_env_repo(self):
        (self.d / "config.toml").write_text('[device]\nname = "T"\n')
        os.environ["USB_DEBUG_REPO"] = str(self.d)
        try:
            self.assertEqual(Config().get("device.name"), "T")
        finally:
            del os.environ["USB_DEBUG_REPO"]


if __name__ == "__main__":
    unittest.main()
