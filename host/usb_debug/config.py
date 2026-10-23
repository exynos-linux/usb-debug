# SPDX-License-Identifier: GPL-2.0-only
"""The device repository's public configuration (config.toml).

Read the way exynos-linux/device-template reads it: through the
repository's own scripts/config ("scripts/config json", derived values
filled in), so every tool agrees. Without that script, config.toml is read
directly (Python 3.11+, tomllib); without a repository, the defaults below
(the template's values) apply.

The repository is --repo / $USB_DEBUG_REPO, else the nearest directory
above the current one with a config.toml that has a [console] or [device]
table.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULTS = {
    "device": {"id": "", "name": "", "codename": ""},
    "console": {"port": 23, "shell_port": 2323, "ssh_port": 22, "klog_port": 24},
    "installer": {
        "download_usb": "04e8:685d",
        "flash_allow": ["BOOT", "RECOVERY"],
        "boot_partition": "BOOT",
        "recovery_partition": "RECOVERY",
        "protected": [],
    },
}


def _toml(path):
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        try:
            import tomli as tomllib
        except ImportError:
            print(f"usb-debug: cannot read {path} (needs Python 3.11+); using defaults", file=sys.stderr)
            return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _is_repo(d):
    f = d / "config.toml"
    if not f.is_file():
        return False
    if (d / "scripts" / "config").is_file():
        return True
    try:
        text = f.read_text(errors="replace")
    except OSError:
        return False
    return "[console]" in text or "[device]" in text


def find_repo(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("USB_DEBUG_REPO")
    if env:
        return Path(env).resolve()
    d = Path.cwd().resolve()
    for p in (d, *d.parents):
        if _is_repo(p):
            return p
    return None


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, repo=None):
        self.repo = find_repo(repo)
        self.source = "defaults"
        data = {}
        if self.repo:
            data = self._load()
        self.data = _merge(copy.deepcopy(DEFAULTS), data)

    def _load(self):
        script = self.repo / "scripts" / "config"
        if script.is_file() and os.access(script, os.X_OK):
            env = dict(os.environ)
            env.pop("CONFIG", None)
            try:
                r = subprocess.run([str(script), "json"], cwd=self.repo, env=env,
                                   capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    self.source = str(script)
                    return json.loads(r.stdout)
                print(f"usb-debug: {script} json failed: {r.stderr.strip()}", file=sys.stderr)
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                print(f"usb-debug: {script}: {e}", file=sys.stderr)
        toml = self.repo / "config.toml"
        if toml.is_file():
            self.source = str(toml)
            return _toml(toml)
        return {}

    def get(self, key, default=None):
        v = self.data
        for part in key.split("."):
            if not isinstance(v, dict) or part not in v:
                return default
            v = v[part]
        return v

    def path(self, *parts):
        """A path in the device repository (None without one)."""
        return self.repo.joinpath(*parts) if self.repo else None

    def logs_dir(self):
        """The repository's logs/, else $XDG_STATE_HOME/usb-debug."""
        if self.repo:
            return self.repo / "logs"
        return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "usb-debug"

    def build_dir(self):
        """The repository's build/, else $XDG_CACHE_HOME/usb-debug."""
        if self.repo:
            return self.repo / "build"
        return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "usb-debug"
