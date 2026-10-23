# SPDX-License-Identifier: GPL-2.0-only
"""Unattended tests that need the test password: unlock the disk, log in,
check the password. Development images (pmOS) only.

The password comes in an environment variable ($USB_DEBUG_PASSWORD, or the
one --password-env names) or on stdin, never as an argument. It crosses to
tmpfs on the device in one upload and is deleted right after use.
"""
import io
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import ROOT, link
from .cli import run_script


def password(a, what):
    pw = os.environ.pop(a.password_env, "")
    if not pw and not sys.stdin.isatty():
        pw = sys.stdin.read()
    if not pw:
        raise link.LinkError(f"{what}: no password (${a.password_env} or stdin)")
    return pw


def _put(cfg, data, remote):
    with link.lock(link.lock_path(cfg)), link.connect(cfg, timeout=30) as c:
        r = c.put(io.BytesIO(data), remote, len(data))
    if r != f"ok {len(data)}":
        raise link.LinkError(f"upload to {remote} failed: {r}")


def _cmd(cfg, line, timeout=30):
    with link.lock(link.lock_path(cfg)), link.connect(cfg) as c:
        return c.cmd(line, timeout=timeout).decode(errors="replace").replace("\r", "")


def _sh(cfg, script, *args, timeout=75):
    with link.lock(link.lock_path(cfg)):
        return run_script(cfg, script, args, timeout=timeout)[0]


# ---------- unlock ----------

def unlock(cfg, a):
    """Answer a pending systemd password request (the LUKS root) with
    systemd-reply-password. Exit 0 accepted, 1 no request in time, 2 rejected."""
    pw = password(a, "unlock")
    ask = None
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        try:
            ask = next((n for n in _cmd(cfg, "ls /run/systemd/ask-password").split() if n.startswith("ask.")), None)
        except link.LinkError:
            ask = None
        if ask:
            break
        time.sleep(3)
    if not ask:
        print(f"unlock: no password request within {a.timeout} s", file=sys.stderr)
        return 1
    info = _cmd(cfg, f"cat /run/systemd/ask-password/{ask}")
    sock = next((ln[7:] for ln in info.splitlines() if ln.startswith("Socket=")), "")
    msg = next((ln[8:] for ln in info.splitlines() if ln.startswith("Message=")), "")
    if not sock:
        print(f"unlock: {ask} names no socket", file=sys.stderr)
        return 1
    print(f"unlock: answering {ask} ({msg})")
    _put(cfg, pw.encode(), "/run/usb-debug-unlock.pw")
    del pw
    script = (f"/usr/lib/systemd/systemd-reply-password 1 {shlex.quote(sock)} < /run/usb-debug-unlock.pw\n"
              "rc=$?\nrm -f /run/usb-debug-unlock.pw\nexit $rc\n")
    try:
        _sh(cfg, script, timeout=30)
    except link.LinkError:
        pass  # the initrd may switch root right away
    for _ in range(20):
        time.sleep(3)
        try:
            now = next((n for n in _cmd(cfg, "ls /run/systemd/ask-password").split() if n.startswith("ask.")), None)
        except link.LinkError:
            continue
        if not now:
            print("unlock: accepted")
            return 0
        if now != ask:
            print("unlock: rejected (asked again)", file=sys.stderr)
            return 2
    print("unlock: the request is still pending", file=sys.stderr)
    return 2


# ---------- checkpw ----------

CHECK = r'''u=${1:-$(cat /etc/default_user)}
root=$(lsblk -nro NAME,PARTLABEL | awk '$2 == "pmOS_root" {print "/dev/" $1; exit}')
if cryptsetup open --test-passphrase --key-file /run/usb-debug-check.pw "$root" 2>/dev/null; then
	echo "disk:  ok ($root)"
else
	echo "disk:  WRONG ($root)"
fi
if /usr/sbin/unix_chkpwd "$u" nullok < /run/usb-debug-check.pw 2>/dev/null; then
	echo "login: ok ($u)"
else
	echo "login: WRONG ($u)"
fi
rm -f /run/usb-debug-check.pw
'''


def checkpw(cfg, a):
    """Check the test password against the LUKS root (cryptsetup
    --test-passphrase) and the user's account (unix_chkpwd) without using
    it, so a failing unlock/login can be told apart from a wrong secret."""
    pw = password(a, "checkpw")
    _put(cfg, pw.encode(), "/run/usb-debug-check.pw")
    del pw
    text = _sh(cfg, CHECK, *([a.user] if a.user else []))
    lines = [ln for ln in text.splitlines() if ln.startswith(("disk:", "login:"))]
    print("\n".join(lines))
    return 0 if lines and all(" ok " in ln for ln in lines) else 1


# ---------- login ----------

def uinput_type(cfg, a):
    """The aarch64 uinput-type binary: --uinput-type, the release's prebuilt
    one, else built from source with the device repository's kernel nolibc."""
    if a.uinput_type:
        return Path(a.uinput_type)
    env = os.environ.get("USB_DEBUG_UINPUT_TYPE")
    if env:
        return Path(env)
    pre = ROOT / "bin" / "aarch64" / "uinput-type"
    if pre.is_file():
        return pre
    src = ROOT / "tools" / "uinput-type" / "uinput-type.c"
    out = cfg.build_dir() / "usb-debug" / "uinput-type"
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    nolibc = os.environ.get("NOLIBC") or str(cfg.path("src", "linux", "tools", "include", "nolibc") or "")
    khdr = os.environ.get("KHDR") or str(cfg.path("build", "hdr", "usr", "include") or "")
    if not nolibc or not Path(nolibc).is_dir():
        raise link.LinkError("login: no uinput-type binary; set NOLIBC=<linux>/tools/include/nolibc to build it")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["make", "-s", "-C", str(ROOT / "console"), "uinput-type", f"O={out.parent}",
           f"NOLIBC={nolibc}", "CROSS_COMPILE=" + os.environ.get("CROSS_COMPILE", "aarch64-linux-gnu-")]
    if khdr and Path(khdr).is_dir():
        cmd.append(f"KHDR={khdr}")
    subprocess.run(cmd, check=True)
    return out


SESSIONS = r'''loginctl list-sessions --no-legend 2>/dev/null | while read -r id _ u _; do
	[ "$u" = "$1" ] && [ "$(loginctl show-session "$id" -p Class --value)" = user ] &&
		[ "$(loginctl show-session "$id" -p Type --value)" = wayland ] && echo "$id"
done
'''

# GDM's greeter user gets the GNOME input sources too, IBus Typing Booster
# first: it handles hardware key events (the on-screen keyboard bypasses
# it). Plain US for the greeter while typing, reset afterwards.
GSRUN = r'''u=$(loginctl list-sessions --no-legend | awk '$4 == "seat0" {print $3; exit}')
runuser -u "$u" -- env DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u "$u")/bus gsettings "$@"
'''


def login(cfg, a):
    """Log the user in at GDM (GNOME) or greetd + phrog (Phosh) with a
    virtual keyboard. Exit 0 the user has a graphical session, 1 no login
    screen / setup problem, 2 typed but no session appeared."""
    pw = password(a, "login")
    binary = uinput_type(cfg, a)
    user = a.user or (_sh(cfg, "cat /etc/default_user 2>/dev/null").split() or [""])[0]
    if not user:
        print("login: no user given and no /etc/default_user", file=sys.stderr)
        return 1
    if _sh(cfg, SESSIONS, user).strip():
        print(f"login: {user} is already logged in")
        return 0
    dm = _sh(cfg, "for d in gdm greetd; do systemctl -q is-active $d.service && echo $d; done").split()
    dm = next((d for d in dm if d in ("gdm", "greetd")), "")
    if dm == "gdm":
        # a blanked screen wakes on the first key and swallows it: Shift first;
        # the default user's password field has the focus
        keys = ["@shift", "@sleep:2000", "@file:/run/usb-debug-login.pw", "@enter"]
    elif dm == "greetd":
        # phrog: the lock-screen page first; a key press opens the keypad
        keys = ["@shift", "@sleep:2000", "@enter", "@sleep:1500", "@file:/run/usb-debug-login.pw", "@enter"]
    else:
        print("login: no login screen running (gdm/greetd)", file=sys.stderr)
        return 1
    if dm == "gdm":
        _sh(cfg, GSRUN, "set", "org.gnome.desktop.input-sources", "sources", "[('xkb','us')]")
    _put(cfg, binary.read_bytes(), "/run/uinput-type")
    _put(cfg, pw.encode(), "/run/usb-debug-login.pw")
    del pw
    res = _sh(cfg, "chmod 755 /run/uinput-type\n/run/uinput-type " + " ".join(keys)
              + "\nrc=$?\nrm -f /run/usb-debug-login.pw\necho rc=$rc\n")
    rc = next((ln for ln in res.splitlines() if ln.startswith("rc=")), res.strip())
    print(f"login: typed at {dm} ({rc})")
    if dm == "gdm":
        try:
            _sh(cfg, GSRUN, "reset", "org.gnome.desktop.input-sources", "sources")
        except link.LinkError:
            pass
    for _ in range(20):
        time.sleep(3)
        try:
            sid = _sh(cfg, SESSIONS, user).split()
        except link.LinkError:
            continue
        if sid:
            print(f"login: {user} logged in (session {sid[0]})")
            return 0
    print(f"login: no session for {user} after 60 s", file=sys.stderr)
    return 2
