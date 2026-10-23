# SPDX-License-Identifier: GPL-2.0-only
"""Screenshot of the device's panel (XRGB8888 scanout buffer) as a PNG.

By default the console's "cat" reads the buffer on screen from the KMS
driver's debugfs file (/sys/kernel/debug/dri/0/scanout, the exynos-linux
DECON driver; GNOME on the GPU scans out CMA dumb buffers). --src reads
another file. --base ADDR reads physical memory with "memcat" instead
(simpledrm / bring-up framebuffer); --base-reg REG first peeks the buffer
address from a display controller register. memcat goes through /dev/mem,
which cannot map ordinary RAM such as the CMA buffers a KMS driver flips
between (STRICT_DEVMEM): use the default there.
"""
import struct
import sys
import time
import zlib
from pathlib import Path

from . import link

SCANOUT = "/sys/kernel/debug/dri/0/scanout"


def panel_size(c):
    """WxH of the connected panel: its DRM mode, else fb0."""
    names = c.cmd("ls /sys/class/drm").decode(errors="replace").split()
    for n in sorted(names):
        if "-" not in n:
            continue
        st = c.cmd(f"cat /sys/class/drm/{n}/status").decode(errors="replace").strip()
        if st != "connected":
            continue
        mode = c.cmd(f"cat /sys/class/drm/{n}/modes").decode(errors="replace").split()
        if mode and "x" in mode[0]:
            w, h = mode[0].split("x", 1)
            return int(w), int("".join(ch for ch in h if ch.isdigit()))
    vs = c.cmd("cat /sys/class/graphics/fb0/virtual_size").decode(errors="replace").strip()
    if "," in vs:
        w, h = vs.split(",", 1)
        if w.isdigit() and h.isdigit():
            return int(w), int(h)
    raise link.LinkError("cannot tell the panel size: give --size WxH")


def png(rgbx, w, h):
    """XRGB8888 little endian (B G R X) -> PNG bytes."""
    raw = bytearray()
    for y in range(h):
        row = rgbx[y * w * 4:(y + 1) * w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        raw.append(0)
        raw += rgb

    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def shot(cfg, a):
    out = Path(a.out) if a.out else cfg.build_dir() / "shots" / time.strftime("shot-%Y%m%d-%H%M%S.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    lk = link.lock_path(cfg)
    base = int(a.base, 0) if a.base else None
    with link.lock(lk), link.connect(cfg, timeout=30) as c:
        if a.base_reg:
            r = c.cmd(f"peek {a.base_reg}").decode(errors="replace").strip()
            if not r.startswith("0x"):
                raise link.LinkError(f"peek {a.base_reg}: {r}")
            base = int(r, 16)
        if a.size:
            w, h = (int(x) for x in a.size.lower().split("x"))
        else:
            w, h = panel_size(c)
        n = w * h * 4
        data = bytearray()
        c.stream(a.src or SCANOUT, n, data.extend, label="screen", base=base, whole=False)
    out.write_bytes(png(data, w, h))
    nz = sum(1 for i in range(0, n, 4096) if data[i:i + 3] != b"\0\0\0")
    print(f"{out} ({nz * 100 // max(1, n // 4096)}% of samples non-black)")
    return 0
