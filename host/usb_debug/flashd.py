# SPDX-License-Identifier: GPL-2.0-only
"""Persistent flasher: whenever the device shows up in Samsung's Download
mode (installer.download_usb), flash the current boot image to the boot
partition (installer.boot_partition) with heimdall.

Loop guard: if the same image comes back to Download mode 3 times within
3 minutes it is crash-looping; flash the known-good image instead (and keep
doing so) until the boot image changes. A queued RECOVERY image
(installer.recovery_partition) is flashed once along with it, then removed.

Only partitions in installer.flash_allow and not in installer.protected
are ever written.
"""
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import link


def usb_present(vidpid):
    vid, pid = vidpid.lower().split(":")
    for d in Path("/sys/bus/usb/devices").glob("*"):
        try:
            if (d / "idVendor").read_text().strip() == vid and (d / "idProduct").read_text().strip() == pid:
                return True
        except OSError:
            continue
    return False


def short_hash(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:12]


def allowed(cfg, part):
    allow = {p.upper() for p in cfg.get("installer.flash_allow", [])}
    prot = {p.lower() for p in cfg.get("installer.protected", [])}
    return part.upper() in allow and part.lower() not in prot


def flashd(cfg, a):
    images = cfg.build_dir() / "images"
    img = Path(a.image) if a.image else images / "boot-mainline.img"
    good = Path(a.good) if a.good else images / "boot-good.img"
    queue = Path(a.queue) if a.queue else images / "recovery-queue.img"
    logf = Path(a.log) if a.log else cfg.logs_dir() / "flashd.log"
    usb = cfg.get("installer.download_usb")
    boot = cfg.get("installer.boot_partition")
    rec = cfg.get("installer.recovery_partition")
    heimdall = shutil.which("heimdall")
    if not heimdall:
        raise link.LinkError("flashd: heimdall is not installed")
    for p in (boot,):
        if not allowed(cfg, p):
            raise link.LinkError(f"flashd: {p} is not in installer.flash_allow (or is protected)")
    logf.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(logf, "a", buffering=1)

    def log(msg):
        line = f"[{time.strftime('%F %T')}] {msg}"
        log_fh.write(line + "\n")
        if sys.stdout.isatty():
            print(line)

    def flash(args):
        r = subprocess.run([heimdall, "flash", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for ln in r.stdout.splitlines():
            if any(k in ln for k in ("successful", "ERROR", "Failed", "Rebooting")):
                log_fh.write(ln + "\n")

    log(f"flashd started: {img} -> {boot} on {usb}")
    hist, bad = [], None
    while True:
        while not usb_present(usb):
            time.sleep(1)
        for _ in range(20):
            if subprocess.run([heimdall, "detect"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(1)
        h = short_hash(img)
        now = time.time()
        hist = (hist + [(now, h)])[-20:]
        n = sum(1 for t, x in hist if x == h and now - t <= 180)
        if bad != h and n >= 3 and good.exists() and h != short_hash(good):
            bad = h
            log(f"LOOP: {h} back in Download mode {n} times in 180 s, using {good.name} until it changes")
        fl = good if bad == h else img
        if queue.exists() and allowed(cfg, rec):
            log(f"Download mode: flashing {boot} {short_hash(fl)} + {rec} {short_hash(queue)}")
            flash([f"--{boot}", str(fl), f"--{rec}", str(queue)])
            queue.unlink()
        else:
            if queue.exists():
                log(f"not flashing {queue}: {rec} is not in installer.flash_allow")
            log(f"Download mode: flashing {short_hash(fl)}")
            flash([f"--{boot}", str(fl)])
        log("flash done")
        if a.once:
            return 0
        # wait for the device to leave Download mode before looking again
        while usb_present(usb):
            time.sleep(1)
