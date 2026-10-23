# SPDX-License-Identifier: GPL-2.0-only
"""usb-debug: the host side of the exynos-linux USB debug console."""
from pathlib import Path

# host/usb_debug/__init__.py -> the tree root (a checkout or an install)
ROOT = Path(__file__).resolve().parent.parent.parent


def _version():
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


VERSION = _version()
REPO = "exynos-linux/usb-debug"
