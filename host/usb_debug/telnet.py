# SPDX-License-Identifier: GPL-2.0-only
"""A minimal telnet client for the root shell (busybox telnetd -l /bin/sh).

Character mode: the device echoes (WILL ECHO, WILL SUPPRESS-GO-AHEAD are
accepted, every other option refused), the local terminal is raw.
Ctrl-] quits. Without a terminal, stdin is sent as it is.
"""
import os
import select
import socket
import sys

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ECHO, SGA = 1, 3
ESCAPE = 0x1d  # Ctrl-]


class Parser:
    def __init__(self, sock):
        self.sock, self.state, self.cmd = sock, 0, 0

    def feed(self, data):
        """Strip telnet commands from data, answer negotiations; return text."""
        text = bytearray()
        for b in data:
            if self.state == 0:
                if b == IAC:
                    self.state = 1
                else:
                    text.append(b)
            elif self.state == 1:  # after IAC
                if b == IAC:
                    text.append(IAC)
                    self.state = 0
                elif b in (DO, DONT, WILL, WONT):
                    self.cmd, self.state = b, 2
                elif b == SB:
                    self.state = 3
                else:
                    self.state = 0
            elif self.state == 2:  # option of DO/DONT/WILL/WONT
                if self.cmd == WILL:
                    self.sock.sendall(bytes([IAC, DO if b in (ECHO, SGA) else DONT, b]))
                elif self.cmd == DO:
                    self.sock.sendall(bytes([IAC, WILL if b == SGA else WONT, b]))
                self.state = 0
            elif self.state == 3:  # subnegotiation: skip to IAC SE
                if b == IAC:
                    self.state = 4
            elif self.state == 4:
                self.state = 0 if b == SE else 3
        return bytes(text)


def interact(addr, port):
    try:
        s = socket.create_connection((addr, port), timeout=10)
    except OSError as e:
        print(f"usb-debug: cannot reach the shell at [{addr}]:{port}: {e}", file=sys.stderr)
        return 1
    s.settimeout(None)
    p = Parser(s)
    fd = sys.stdin.fileno()
    tty = os.isatty(fd)
    old = None
    if tty:
        import termios
        import tty as ttym
        old = termios.tcgetattr(fd)
        ttym.setraw(fd)
        sys.stderr.write("(Ctrl-] quits)\r\n")
    try:
        while True:
            r, _, _ = select.select([s, fd], [], [])
            if s in r:
                d = s.recv(65536)
                if not d:
                    return 0
                os.write(sys.stdout.fileno(), p.feed(d))
            if fd in r:
                d = os.read(fd, 1024)
                if not d:
                    s.shutdown(socket.SHUT_WR)
                    fd = -1
                    continue
                if tty and ESCAPE in d:
                    return 0
                s.sendall(d.replace(bytes([IAC]), bytes([IAC, IAC])))
    except (KeyboardInterrupt, OSError):
        return 0
    finally:
        if old is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
        s.close()
