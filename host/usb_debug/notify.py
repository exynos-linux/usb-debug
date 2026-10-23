# SPDX-License-Identifier: GPL-2.0-only
"""Get the user's attention when a test needs their hands (a button press,
a finger on the screen, plugging a cable): a desktop notification, a siren
at full volume (--info: a chime; the volume and mute state are restored
afterwards), the request read out loud, and a line in user-requests.log.

--wait SECS keeps the notification up with a "Done" button and waits for
it; the sound repeats every 90 s. Prints "done" or "timeout", exit 0 / 2.
"""
import math
import os
import select
import shutil
import struct
import subprocess
import sys
import time
import wave

SINK = "@DEFAULT_AUDIO_SINK@"
SOUNDS = "/usr/share/sounds/freedesktop/stereo"


def siren(path):
    """A siren (rising and falling wail, ~6 s)."""
    rate, frames, phase = 44100, [], 0.0
    for _ in range(4):
        n = int(rate * 1.5)
        for i in range(n):
            t = i / n
            f = 650 + 750 * (math.sin(math.pi * t) ** 2)  # 650 -> 1400 -> 650 Hz
            phase += 2 * math.pi * f / rate
            v = math.sin(phase) + 0.3 * math.sin(3 * phase)  # a bit of edge
            frames.append(int(max(-1, min(1, v / 1.3)) * 30000))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(frames), *frames))


def notify(cfg, a):
    urgent = not a.info
    uid = os.getuid()
    env = dict(os.environ)
    # agents may run without the desktop session's environment
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={env['XDG_RUNTIME_DIR']}/bus")

    def run(*argv, **kw):
        if a.dry_run:
            print("+ " + " ".join(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if not shutil.which(argv[0]):
            return subprocess.CompletedProcess(argv, 127, "", "")
        return subprocess.run(argv, env=env, capture_output=True, text=True, **kw)

    logs = cfg.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "user-requests.log", "a") as f:
        f.write(f"{time.strftime('%F %T')} {a.title}: {a.body}\n")

    chime = f"{SOUNDS}/complete.oga"
    if urgent:
        wav = cfg.build_dir() / "siren.wav"
        try:
            if not wav.is_file() or wav.stat().st_size == 0:
                siren(wav)
            chime = str(wav)
        except OSError:
            chime = f"{SOUNDS}/alarm-clock-elapsed.oga"

    def alert():
        if a.quiet:
            return
        r = run("wpctl", "get-volume", SINK)
        vol = r.stdout.split()[1] if r.returncode == 0 and len(r.stdout.split()) > 1 else None
        muted = "MUTED" in (r.stdout or "")
        run("wpctl", "set-mute", SINK, "0")
        run("wpctl", "set-volume", SINK, "1.0")
        if run("pw-play", chime).returncode != 0:
            run("paplay", chime)
        if urgent:
            who = cfg.get("device.name") or "the device"
            run("spd-say", "--wait", "-r", "-10", f"Test on {who} needs you. {a.title}. {a.body}")
        if vol:
            run("wpctl", "set-volume", SINK, vol)
        if muted:
            run("wpctl", "set-mute", SINK, "1")

    app = f"{cfg.get('device.name') or 'usb-debug'} test agent"
    args = ["-a", app, "-i", "input-tablet", "-u", "critical" if urgent else "normal"]
    if not a.wait:
        run("notify-send", *args, a.title, a.body)
        alert()
        return 0

    # waiting: the notification's "Done" button answers; the sound repeats
    if a.dry_run or not shutil.which("notify-send"):
        print(f"+ notify-send {' '.join(args)} -A done=Done {a.title!r} {a.body!r}")
        alert()
        print("done")
        return 0
    p = subprocess.Popen(["notify-send", *args, "-A", "done=Done", a.title, a.body],
                         env=env, stdout=subprocess.PIPE, text=True)
    try:
        alert()
        deadline = time.time() + a.wait
        nxt = time.time() + 90
        while time.time() < deadline:
            r, _, _ = select.select([p.stdout], [], [], 5)
            if r:
                answer = p.stdout.readline().strip()
                if answer == "done" or p.poll() is not None:
                    # the button, or closed without it: treat as seen
                    print("done")
                    return 0
            elif p.poll() is not None:
                print("done")
                return 0
            if time.time() >= nxt:
                alert()
                nxt = time.time() + 90
        print("timeout")
        return 2
    finally:
        if p.poll() is None:
            p.kill()
