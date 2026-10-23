# SPDX-License-Identifier: GPL-2.0-only
"""Serve the real usb-debug-console (CONSOLE_STDIO build) on a loopback TCP
port, one process per connection with the socket on stdin/stdout: what
systemd's Accept=yes socket does on the device.

The console is $USBDBG_CONSOLE, else build/usb-debug-console (make console).
"""
import os
import socket
import subprocess
import threading
from pathlib import Path

TOP = Path(__file__).resolve().parent.parent


def console_binary():
    p = Path(os.environ.get("USBDBG_CONSOLE") or TOP / "build" / "usb-debug-console")
    if not p.is_file():
        subprocess.run(["make", "-s", "-C", str(TOP), "console", f"O={p.parent}"], check=True)
    return p


class FakeConsole:
    def __init__(self, argv=None):
        self.argv = argv or [str(console_binary())]
        self.ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(8)
        self.port = self.ls.getsockname()[1]
        self.procs = []
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        while True:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            p = subprocess.Popen(self.argv, stdin=c, stdout=c, stderr=subprocess.DEVNULL)
            c.close()
            self.procs.append(p)

    def close(self):
        self.ls.close()
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()


def talk(port, lines, timeout=10):
    """Send command lines, return the answers (bytes, prompts removed)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    out = []

    def until_prompt():
        buf = b""
        while not buf.endswith(b"> "):
            d = s.recv(65536)
            if not d:
                break
            buf += d
        return buf[:-2] if buf.endswith(b"> ") else buf

    banner = until_prompt()
    for ln in lines:
        s.sendall(ln if isinstance(ln, bytes) else ln.encode() + b"\n")
        out.append(until_prompt())
    s.close()
    return banner, out
