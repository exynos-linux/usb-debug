# SPDX-License-Identifier: GPL-2.0-only
"""Settings: the device repository's public configuration (config.toml).

Read the way exynos-linux/device-template reads it: through the
repository's own scripts/config ("scripts/config json", derived values
filled in), so every tool agrees. Without that script, config.toml is read
directly. Without a repository, the device table (devices/<id>.toml in this
tree, chosen with --device, $USB_DEBUG_DEVICE or the user config), then the
defaults below (the template's values) apply.

The repository is --repo / $USB_DEBUG_REPO, else the nearest directory
above the current one with a config.toml that has a [console] or [device]
table, else the user config's "repo".

User config ($XDG_CONFIG_HOME/usb-debug/config.toml, written by
install.sh --device / --repo):
    device = "samsung-gtactive3wifi"
    repo = "/home/me/src/samsung-gtactive3wifi"
"""
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import ROOT

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


def _value(v):
    v = v.strip()
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1]
    if v in ("true", "false"):
        return v == "true"
    if v.startswith("["):
        inner = v.strip("[]").strip()
        return [_value(x) for x in re.findall(r'"[^"]*"|\'[^\']*\'|[^,\s]+', inner)] if inner else []
    try:
        return int(v, 0)
    except ValueError:
        return v


def mini_toml(text):
    """The TOML subset the config files use ([table], key = string, int,
    bool, or a list of those, also over several lines), for Python < 3.11."""
    data, table, pending = {}, None, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if '"' not in raw else re.sub(r'\s+#[^"]*$', "", raw).strip()
        if pending is not None:
            pending[1] += " " + line
            if line.endswith("]"):
                table[pending[0]] = _value(pending[1])
                pending = None
            continue
        if not line:
            continue
        m = re.fullmatch(r"\[([A-Za-z0-9_.-]+)\]", line)
        if m:
            table = data
            for part in m.group(1).split("."):
                table = table.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if table is None:
            table = data
        if v.startswith("[") and not v.endswith("]"):
            pending = [k, v]
            continue
        table[k] = _value(v)
    return data


def read_toml(path):
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        tomllib = None
    if tomllib:
        with open(path, "rb") as f:
            return tomllib.load(f)
    return mini_toml(Path(path).read_text())


def user_config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "usb-debug" / "config.toml"


def device_file(device_id):
    return ROOT / "devices" / f"{device_id}.toml"


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


def find_repo(explicit=None, user=None):
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("USB_DEBUG_REPO")
    if env:
        return Path(env).resolve()
    d = Path.cwd().resolve()
    for p in (d, *d.parents):
        if _is_repo(p):
            return p
    r = (user or {}).get("repo")
    if r and _is_repo(Path(r)):
        return Path(r).resolve()
    return None


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, repo=None, device=None):
        user = {}
        up = user_config_path()
        if up.is_file():
            try:
                user = read_toml(up)
            except (OSError, ValueError) as e:
                print(f"usb-debug: {up}: {e}", file=sys.stderr)
        self.repo = find_repo(repo, user)
        self.device = device or os.environ.get("USB_DEBUG_DEVICE") or user.get("device") or ""
        self.source = "defaults"
        self.data = copy.deepcopy(DEFAULTS)
        if self.repo:
            _merge(self.data, self._load())
        elif self.device:
            f = device_file(self.device)
            if f.is_file():
                _merge(self.data, read_toml(f))
                self.source = str(f)
            else:
                print(f"usb-debug: no settings for device {self.device!r} in {f.parent}; using defaults",
                      file=sys.stderr)

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
            return read_toml(toml)
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
