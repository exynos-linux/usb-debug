# SPDX-License-Identifier: GPL-2.0-only
"""usb-debug: talk to an exynos-linux device over its USB debug link.

The device is the bring-up Linux (usb-debug's nolibc init) or a development
image (the duranium-usb-debug package). Settings come from the device
repository's config.toml (see config.py); run it inside the repository or
pass --repo.
"""
import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import REPO, ROOT, VERSION, link
from .config import Config


def out(b):
    sys.stdout.buffer.write(b if isinstance(b, bytes) else b.encode())
    sys.stdout.buffer.flush()


def die(msg, code=1):
    print(f"usb-debug: {msg}", file=sys.stderr)
    return code


# ---------- link ----------

def c_wait(cfg, a):
    """Wait until the device's USB network (or its console) answers."""
    ping = shutil.which("ping")
    port = int(cfg.get("console.port"))
    for _ in range(a.seconds):
        t0 = time.time()
        try:
            addr = link.address()
            if a.console or not ping:
                with link.Console(addr, port, timeout=1):
                    pass
                ok = True
            else:
                ok = subprocess.run([ping, "-6", "-c1", "-W1", addr], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL).returncode == 0
            if ok:
                print(f"up on {link.iface() or addr}")
                return 0
        except link.LinkError:
            pass
        time.sleep(max(0, 1 - (time.time() - t0)))
    print("timeout")
    return 1


def c_addr(cfg, a):
    print(link.address())


def c_cmd(cfg, a):
    """One console command; prints the answer."""
    line = " ".join(a.line)
    with link.connect(cfg) as c:
        r = c.cmd(line, timeout=a.timeout)
    out(r)
    if a.check and (r.startswith(b"error") or r.startswith(b"unknown command") or b"FAILED" in r):
        return 1
    return 0


def _size(c, remote):
    """Bytes "cat remote" streams: the console's size command, else the
    block device size from sysfs (consoles before usb-debug 1.0)."""
    r = c.cmd(f"size {remote}").decode(errors="replace").strip()
    if r.isdigit():
        return int(r)
    if r == "error":
        raise link.LinkError(f"{remote}: no such file on the device")
    r = c.cmd(f"cat /sys/class/block/{os.path.basename(remote)}/size").decode(errors="replace")
    digits = "".join(ch for ch in r if ch.isdigit())
    if not digits:
        raise link.LinkError(f"cannot tell the size of {remote}: give BYTES")
    return int(digits) * 512


def c_put(cfg, a):
    """Upload a file (or - for stdin) to the device."""
    try:
        if a.local == "-":
            f = io.BytesIO(sys.stdin.buffer.read())
        elif os.path.isfile(a.local):
            f = open(a.local, "rb")
        else:  # a pipe, e.g. <(printf ...): read it whole to know its size
            with open(a.local, "rb") as p:
                f = io.BytesIO(p.read())
        n = os.fstat(f.fileno()).st_size if isinstance(f, io.BufferedReader) else len(f.getbuffer())
    except OSError as e:
        return die(str(e))
    with link.connect(cfg, timeout=30) as c:
        r = c.put(f, a.remote, n)
    print(r)
    return 0 if r == f"ok {n}" else 1


def c_get(cfg, a):
    """Copy a file or block device from the device."""
    with link.connect(cfg) as c:
        whole = a.bytes is None
        n = _size(c, a.remote) if whole else a.bytes
        t0 = time.time()
        with open(a.local, "wb") as f:
            digest = c.stream(a.remote, n, f.write, whole=whole)
    dt = time.time() - t0
    print(f"{a.local} {n} bytes {n / dt / 1e6 if dt else 0:.1f} MB/s sha256 {digest}")
    return 0


def run_script(cfg, script, args=(), timeout=75, name="sh"):
    """Upload a shell script to /run and run it with /bin/sh (development
    images: the bring-up Linux has no shell). Returns (output, status)."""
    rundir = os.environ.get("USB_DEBUG_RUNDIR", "/run")  # tests: a writable directory
    remote = f"{rundir}/usb-debug-{name}.{os.getpid()}.sh"
    body = f'trap \'rm -f "$0"\' EXIT\n{script}\n'.encode()
    with link.connect(cfg, timeout=30) as c:
        r = c.put(io.BytesIO(body), remote, len(body))
        if r != f"ok {len(body)}":
            raise link.LinkError(f"upload of the script failed: {r}")
        res = c.cmd(" ".join(["run", "/bin/sh", remote, *args]), timeout=timeout)
    text = res.decode(errors="replace")
    lines = text.rstrip("\n").split("\n")
    status = 1
    if lines and lines[-1].startswith("run: exit "):
        f = lines.pop().split()
        status = int(f[2]) if f[4] == "0" else 128 + int(f[4])
    elif lines and lines[-1].startswith("run: "):
        lines.pop()
    body = "\n".join(lines)
    return (body + "\n" if body else ""), status


def c_sh(cfg, a):
    """Run a shell script on the device (development images)."""
    args = list(a.rest)
    if a.command is not None:
        script = a.command
    elif not args:
        return die("sh: give -c TEXT or a script file (- = stdin)")
    elif args[0] == "-":
        script = sys.stdin.read()
        args.pop(0)
    else:
        script = Path(args.pop(0)).read_text()
    if len(args) > 6:
        return die("sh: at most 6 arguments (the console runs at most 8 words)")
    text, status = run_script(cfg, script, args, timeout=a.timeout)
    out(text)
    return status


def c_shell(cfg, a):
    from .telnet import interact
    return interact(link.address(), int(cfg.get("console.shell_port")))


def c_klog(cfg, a):
    port = int(cfg.get("console.klog_port", 24))
    try:
        s = socket.create_connection((link.address(), port), timeout=10)
    except OSError as e:
        return die(f"cannot reach the kernel log at port {port}: {e}")
    s.settimeout(None)
    try:
        while True:
            d = s.recv(65536)
            if not d:
                return 0
            out(d)
    except (KeyboardInterrupt, BrokenPipeError):
        return 0


def c_ssh(cfg, a):
    ssh = shutil.which("ssh")
    if not ssh:
        return die("no ssh client")
    # every device has the same address, so do not pin host keys
    argv = [ssh, "-p", str(cfg.get("console.ssh_port", 22)), "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            f"{a.user}@{link.address()}", *a.args]
    os.execv(ssh, argv)


# ---------- the rest ----------

def c_shot(cfg, a):
    from .shot import shot
    return shot(cfg, a)


def c_login(cfg, a):
    from .secret import login
    return login(cfg, a)


def c_unlock(cfg, a):
    from .secret import unlock
    return unlock(cfg, a)


def c_checkpw(cfg, a):
    from .secret import checkpw
    return checkpw(cfg, a)


def c_flashd(cfg, a):
    from .flashd import flashd
    return flashd(cfg, a)


def c_notify(cfg, a):
    from .notify import notify
    return notify(cfg, a)


def c_hot_modules(cfg, a):
    k = Path(a.ksrc) if a.ksrc else cfg.path("src", "linux")
    o = Path(a.kbuild) if a.kbuild else cfg.path("build", "bringup")
    stage = Path(a.stage) if a.stage else cfg.path("build", "initramfs", "modules")
    if not k or not o or not stage:
        return die("hot-modules: no device repository: give --ksrc, --kbuild and --stage")
    cross = os.environ.get("CROSS_COMPILE", "aarch64-linux-gnu-")
    r = subprocess.run(["nice", "-n", "19", "make", f"-j{a.jobs}", "-s", "-C", str(k), "ARCH=arm64",
                        f"CROSS_COMPILE={cross}", f"O={o}", "modules"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for ln in r.stdout.splitlines():
        if "error" in ln.lower() or "warning: " in ln.lower():
            print(ln)
    stage.mkdir(parents=True, exist_ok=True)
    rc = 0
    for m in a.modules:
        name = os.path.basename(m) + ".ko"
        dst = stage / name
        subprocess.run([f"{cross}strip", "--strip-debug", "-o", str(dst), str(o / f"{m}.ko")], check=True)
        with open(dst, "rb") as f, link.connect(cfg, timeout=30) as c:
            res = c.put(f, f"{a.dest}/{name}", dst.stat().st_size)
        print(f"{name}: {res}")
        rc |= not res.startswith("ok")
    return rc


def c_config(cfg, a):
    if a.key:
        v = cfg.get(a.key)
        if v is None:
            return die(f"no key {a.key}")
        print(json.dumps(v) if isinstance(v, (dict, list)) else v)
        return 0
    print(f"# repository: {cfg.repo or '(none)'}\n# source: {cfg.source}")
    print(json.dumps({k: cfg.data[k] for k in ("device", "console", "installer") if k in cfg.data}, indent=2))
    return 0


def c_version(cfg, a):
    print(f"usb-debug {VERSION} ({ROOT})")
    if a.device:
        with link.connect(cfg, timeout=5) as c:
            r = c.cmd("version").decode(errors="replace").strip()
        print(f"device: {r if not r.startswith('unknown') else 'console older than usb-debug 1.0'}")
    return 0


def c_self_update(cfg, a):
    from .update import self_update
    return self_update(a)


def c_src(cfg, a):
    print(ROOT)


def c_aport(cfg, a):
    from .update import aport
    return aport(a)


def parser():
    p = argparse.ArgumentParser(prog="usb-debug", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=f"Docs: https://github.com/{REPO}#readme")
    p.add_argument("--repo", help="the device repository (default: found from the current directory)")
    p.add_argument("--addr", help="device address (default fe80::57:aff:fe00:1%%<cdc_ncm interface>)")
    p.add_argument("--port", type=int, help="console port (default: config.toml console.port)")
    p.add_argument("-V", "--version", action="version", version=f"usb-debug {VERSION}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def cmd(name, func, help, aliases=()):
        s = sub.add_parser(name, help=help, description=help, aliases=list(aliases))
        s.set_defaults(func=func)
        return s

    s = cmd("wait", c_wait, "wait until the device's USB network is up")
    s.add_argument("seconds", nargs="?", type=int, default=120)
    s.add_argument("--console", action="store_true", help="wait for the console, not just ping")
    cmd("addr", c_addr, "print the device's address")
    s = cmd("cmd", c_cmd, "run one console command (dmesg, peek ADDR, cat PATH, ...)", aliases=["c"])
    s.add_argument("--timeout", type=int, default=30)
    s.add_argument("--check", action="store_true", help="exit 1 when the answer is an error")
    s.add_argument("line", nargs=argparse.REMAINDER)
    s = cmd("put", c_put, "upload a file (- = stdin)")
    s.add_argument("local")
    s.add_argument("remote")
    s = cmd("get", c_get, "download a file or block device")
    s.add_argument("remote")
    s.add_argument("local")
    s.add_argument("bytes", nargs="?", type=int, help="only the first BYTES (default: all of it)")
    s = cmd("sh", c_sh, "run a shell script on the device (development images): "
            "sh -c TEXT [ARGS] or sh FILE|- [ARGS]")
    s.add_argument("-c", dest="command", help="the script text")
    s.add_argument("--timeout", type=int, default=75)
    s.add_argument("rest", nargs=argparse.REMAINDER, metavar="[FILE] ARGS")
    cmd("shell", c_shell, "interactive root shell (console.shell_port)")
    cmd("klog", c_klog, "follow the kernel log (development images)")
    s = cmd("ssh", c_ssh, "ssh into the device (development images)")
    s.add_argument("--user", default="root")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s = cmd("shot", c_shot, "screenshot of the panel as a PNG")
    s.add_argument("out", nargs="?")
    s.add_argument("--src", help="file holding the scanout buffer (default /sys/kernel/debug/dri/0/scanout)")
    s.add_argument("--base", help="read physical memory at this address instead (memcat)")
    s.add_argument("--base-reg", help="read the buffer address from this register (peek)")
    s.add_argument("--size", help="WxH (default: the connected DRM mode, else fb0)")
    for name, func, help in (("login", c_login, "log the user in at the login screen"),
                             ("checkpw", c_checkpw, "check the test password (disk and user)")):
        s = cmd(name, func, help + " (password: $USB_DEBUG_PASSWORD or stdin)")
        s.add_argument("user", nargs="?")
        s.add_argument("--password-env", default="USB_DEBUG_PASSWORD", metavar="NAME")
        if name == "login":
            s.add_argument("--uinput-type", help="the aarch64 uinput-type binary to use")
    s = cmd("unlock", c_unlock, "answer the disk passphrase prompt (password: $USB_DEBUG_PASSWORD or stdin)")
    s.add_argument("timeout", nargs="?", type=int, default=600)
    s.add_argument("--password-env", default="USB_DEBUG_PASSWORD", metavar="NAME")
    s = cmd("flashd", c_flashd, "flash the boot image whenever the device enters Download mode")
    s.add_argument("--image", help="default build/images/boot-mainline.img")
    s.add_argument("--good", help="known-good fallback, default build/images/boot-good.img")
    s.add_argument("--queue", help="one-shot RECOVERY image, default build/images/recovery-queue.img")
    s.add_argument("--log", help="default logs/flashd.log")
    s.add_argument("--once", action="store_true", help="flash once and exit")
    s = cmd("notify", c_notify, "get the user's attention (notification, siren, speech)")
    s.add_argument("--info", action="store_true", help="softer: a chime, no speech")
    s.add_argument("--wait", type=int, default=0, metavar="SECS", help="wait for the Done button")
    s.add_argument("--quiet", action="store_true", help="no sound")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("title")
    s.add_argument("body", nargs="?", default="")
    s = cmd("hot-modules", c_hot_modules, "rebuild kernel modules and push them to the device")
    s.add_argument("--ksrc", help="kernel tree (default src/linux)")
    s.add_argument("--kbuild", help="kernel build dir (default build/bringup)")
    s.add_argument("--stage", help="where the stripped modules go (default build/initramfs/modules)")
    s.add_argument("--dest", default="/lib/modules", help="directory on the device")
    s.add_argument("-j", "--jobs", type=int, default=4)
    s.add_argument("modules", nargs="+", metavar="DIR/MODULE", help="e.g. drivers/ufs/host/ufs-exynos")
    s = cmd("config", c_config, "show the settings read from config.toml")
    s.add_argument("key", nargs="?")
    s = cmd("version", c_version, "print the version")
    s.add_argument("--device", action="store_true", help="also ask the device's console")
    s = cmd("self-update", c_self_update, "update to the newest release (GitHub Releases)")
    s.add_argument("--check", action="store_true", help="only say whether there is one")
    s.add_argument("--version", dest="to", metavar="X.Y.Z", help="install this release instead")
    cmd("src", c_src, "print where this version's files are (console/ for the bring-up init)")
    s = cmd("aport", c_aport, "write this version's released APKBUILD into a directory")
    s.add_argument("dir")
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    if not getattr(a, "func", None):
        p.print_help()
        return 2
    if a.addr:
        os.environ["USB_DEBUG_ADDR"] = a.addr
    cfg = Config(a.repo)
    if a.port:
        cfg.data["console"]["port"] = a.port
    try:
        return a.func(cfg, a) or 0
    except link.LinkError as e:
        return die(str(e))
    except subprocess.CalledProcessError as e:
        return die(f"{' '.join(map(str, e.cmd))}: exit {e.returncode}")
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    except OSError as e:
        return die(str(e))
