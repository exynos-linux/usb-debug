# SPDX-License-Identifier: GPL-2.0-only
"""The USB link and the line console on it (docs/PROTOCOL.md).

Every exynos-linux device, in the bring-up Linux and in development images,
is fe80::57:aff:fe00:1 on its USB NCM gadget; the host's side of the link
has the MAC 02:57:0a:00:00:02. The console answers with a "> " prompt and
takes one command per line.

Overrides: $USB_DEBUG_ADDR (a full address, e.g. ::1 for the tests),
$USB_DEBUG_IFACE (the host interface).
"""
import contextlib
import fcntl
import hashlib
import os
import socket
import sys
import time
from pathlib import Path

LL = "fe80::57:aff:fe00:1"
HOST_MAC = "02:57:0a:00:00:02"
PROMPT = b"> "


class LinkError(Exception):
    pass


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def iface():
    """The host interface of the device's USB link, or None."""
    env = os.environ.get("USB_DEBUG_IFACE")
    if env:
        return env
    base = Path("/sys/class/net")
    try:
        names = sorted(p.name for p in base.iterdir())
    except OSError:
        return None
    for n in names:
        if _read(base / n / "address").lower() == HOST_MAC:
            return n
    for n in names:
        try:
            drv = os.path.basename(os.readlink(base / n / "device" / "driver"))
        except OSError:
            continue
        if drv == "cdc_ncm":
            return n
    return None


def address():
    env = os.environ.get("USB_DEBUG_ADDR")
    if env:
        return env
    i = iface()
    if not i:
        raise LinkError("device USB network not present (no cdc_ncm interface)")
    return f"{LL}%{i}"


@contextlib.contextmanager
def lock(path):
    """flock(path) for the duration, the way the shell tools did; None = no lock."""
    if not path:
        yield
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def lock_path(cfg):
    env = os.environ.get("USB_DEBUG_LOCK")
    if env is not None:
        return env or None
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, "usb-debug.lock") if os.path.isdir(run) else None


class Progress:
    """One line per 5 % when not on a terminal, a live line on one."""

    def __init__(self, label, total):
        self.label, self.total, self.t0 = label, total, time.time()
        self.tty = sys.stderr.isatty()
        self.last = -1

    def __call__(self, done, final=False):
        pct = 100 * done // self.total if self.total else 100
        if not final and (pct == self.last or (not self.tty and pct % 5)):
            return
        self.last = pct
        dt = max(time.time() - self.t0, 1e-3)
        rate = done / dt
        eta = int((self.total - done) / rate) if rate > 0 else 0
        msg = (f"  {self.label}: {done / 2**20:.1f} / {self.total / 2**20:.1f} MiB "
               f"{pct:3d}% {rate / 1e6:.1f} MB/s ETA {eta // 60}:{eta % 60:02d}")
        sys.stderr.write(("\r" + msg + ("\n" if final else "")) if self.tty else msg + "\n")
        sys.stderr.flush()


class Console:
    """One connection to the line console. Use as a context manager."""

    def __init__(self, addr, port, timeout=10):
        self.addr, self.port = addr, port
        try:
            self.s = socket.create_connection((addr, port), timeout=timeout)
        except OSError as e:
            raise LinkError(f"cannot reach the console at [{addr}]:{port}: {e}") from e
        self.s.settimeout(30)
        self.banner = self.until_prompt()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        with contextlib.suppress(OSError):
            self.s.close()

    def until_prompt(self):
        buf = b""
        while not buf.endswith(PROMPT):
            d = self.s.recv(65536)
            if not d:
                break
            buf += d
        return buf[:-2] if buf.endswith(PROMPT) else buf

    def cmd(self, line, timeout=30):
        """Run one command, return its answer (bytes, prompt removed)."""
        self.s.settimeout(timeout)
        self.s.sendall(line.encode() + b"\n")
        return self.until_prompt()

    def put(self, f, remote, n, label=None):
        """Upload n bytes from the file object f; return the answer line."""
        self.s.sendall(f"put {remote} {n}\n".encode())
        prog = Progress(label or os.path.basename(remote), n) if n > 1 << 20 else None
        sent = 0
        while sent < n:
            c = f.read(min(1 << 20, n - sent))
            if not c:
                raise LinkError(f"short read at {sent} of {n} bytes")
            self.s.sendall(c)
            sent += len(c)
            if prog:
                prog(sent)
        if prog:
            prog(sent, True)
        self.s.settimeout(300)  # the last page-cache flush happens before the reply
        r = self.until_prompt()
        return r.split(b"\n")[0].decode(errors="replace").strip()

    def stream(self, remote, n, sink, label=None, base=None, whole=True):
        """cat REMOTE (or memcat BASE n): feed exactly n bytes to sink.
        whole: n is all of it, so the prompt must follow right away (else
        the connection is just dropped after n bytes). Returns the sha256."""
        self.s.settimeout(120)
        self.s.sendall((f"memcat {base:#x} {n}\n" if base is not None else f"cat {remote}\n").encode())
        h = hashlib.sha256()
        prog = Progress(label or os.path.basename(str(remote)), n) if n > 1 << 20 else None
        got = 0
        head = b""
        while got < n:
            d = self.s.recv(min(1 << 20, n - got))
            if not d:
                raise LinkError(f"console closed after {got} of {n} bytes")
            if got < 256:
                head = (head + d)[:256]
                # an error answer is short and followed by the prompt
                if head.endswith(PROMPT) and (head.startswith(b"error") or head.startswith(b"unknown command")):
                    raise LinkError(f"the device cannot read {remote or hex(base)}: {head[:-2].decode(errors='replace').strip()}")
            h.update(d)
            sink(d)
            got += len(d)
            if prog:
                prog(got)
        if prog:
            prog(got, True)
        if whole:
            tail = b""
            while len(tail) < len(PROMPT):
                d = self.s.recv(len(PROMPT) - len(tail))
                if not d:
                    break
                tail += d
            if tail != PROMPT:
                raise LinkError(f"unexpected trailer {tail!r} after {n} bytes (size mismatch?)")
        return h.hexdigest()


def connect(cfg, port=None, timeout=10):
    return Console(address(), port or int(cfg.get("console.port")), timeout=timeout)
