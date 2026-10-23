# Moving a device repository to usb-debug

These steps are for a device repository that has its own copies of the
debug tooling, as gtactive3-mainline / samsung-gtactive3wifi and
device-template had: the `duranium-usb-debug` aport, a register-console
subpackage in the device package, `initramfs/init.c`, and `scripts/tab*`.
Do the steps in order and test on the device after each step marked
**device test**. Only the device's lead session runs device tests.

## 1. Pin a version

```sh
echo 1.0.0 > .usb-debug-version
cp <usb-debug>/contrib/device-shim.sh scripts/usb-debug && chmod 755 scripts/usb-debug
scripts/usb-debug version          # installs 1.0.0 on first use
```

`scripts/usb-debug` runs the pinned release with `--repo` set to the
repository. A version bump is a one-line commit.

## 2. Host scripts → `usb-debug`

| Old | New |
|---|---|
| `scripts/tab wait [N]` | `usb-debug wait [N]` |
| `scripts/tab CMD...` | `usb-debug cmd CMD...` |
| `scripts/tab-put L R` | `usb-debug put L R` (exits 1 unless the answer is `ok <size>`) |
| `scripts/tab-get R L [N]` | `usb-debug get R L [N]` (the size comes from the console's `size`) |
| `scripts/tab-shot [OUT]` | `usb-debug shot [OUT] [--size WxH]` (`SRC=` → `--src`, `BASE=` → `--base`, `BASE=live` → `--base-reg 0x16071040` on Exynos 9810) |
| `GTA3_PASSWORD=… scripts/tab-login [U]` | `usb-debug login [U] --password-env GTA3_PASSWORD` |
| `… scripts/tab-unlock [T]` | `usb-debug unlock [T] --password-env GTA3_PASSWORD` |
| `… scripts/tab-checkpw [U]` | `usb-debug checkpw [U] --password-env GTA3_PASSWORD` |
| `scripts/devd.sh` | `usb-debug flashd --log logs/devd.log` |
| `scripts/notify-user ...` | `usb-debug notify ...` |
| `scripts/hot-modules.sh M...` | `usb-debug hot-modules M...` |

To keep every caller working, replace each old script with a two-line
wrapper rather than deleting it:

```sh
#!/bin/sh
# scripts/tab: kept for older callers, see exynos-linux/usb-debug
exec "$(dirname "$0")/usb-debug" cmd "$@"
```

`tab wait` is the exception: map it to `usb-debug wait "$2"`. The scripts
that lock the console themselves (`flock build/tab.lock scripts/tab ...`)
keep working: `cmd`, `put` and `get` take no lock. `shot`, `login`,
`unlock` and `checkpw` lock `$USB_DEBUG_LOCK`, so the wrappers set
`USB_DEBUG_LOCK=$repo/build/tab.lock` to share the old lock.
Remote temporary files are renamed (`/run/gta3-*.pw` → `/run/usb-debug-*.pw`).

## 3. The aport

```sh
scripts/usb-debug aport pmos/aports/main/duranium-usb-debug   # writes APKBUILD
git rm pmos/aports/main/duranium-usb-debug/<every other file>
```

The released APKBUILD fetches `usb-debug-X.Y.Z.tar.gz` from the GitHub
release and carries its checksum. The package names stay the same
(`duranium-usb-debug`, `-initramfs`, `-adb`), so image lists and
device-template's `debug_package_regex` / `debug_file_regex` need no change.
What changes on the device:

- The **register console on TCP 23** is now part of `-initramfs`:
  `/usr/libexec/duranium/usb-debug-console`, `duranium-debug-console.socket`.
  Drop the device package's own console subpackage in the **same** change
  (gtactive3wifi: `device-samsung-gtactive3wifi-console`, `gta3-console.c`,
  `gta3-console-libc.h`, `gta3-debug-console.socket`/`@.service`).
  Otherwise two sockets want port 23. To keep the device's own console for
  a while, set `CONSOLE_PORT=0` in its conf file instead.
- **Settings**: the device package installs
  `/usr/lib/duranium-usb-debug/conf.d/<device>.conf`. gtactive3wifi keeps
  its old USB strings with:

  ```
  PRODUCT=Duranium Debug (Tab Active3)
  SERIAL=gtactive3-duranium-debug
  ```

  `UDC=` can name the controller when a device has more than one.
- `sshd_config` moved to `/usr/share/duranium-usb-debug/sshd_config`. The
  old `/etc` path still wins if an image has it.
- The firewall rule now also opens 2323. It also comes as a tmpfiles link
  for images whose `/etc` does not take package files.

Bump the device package's pkgrel, build a **development** image, and run a
**device test**. Check that `usb-debug cmd version` answers in the initrd and
after switch-root, that `usb-debug shell`, `klog` and `ssh` work, and that
`systemctl status duranium-debug-console.socket` is active. Then build a
**release** image and check that it contains none of it.

## 4. The bring-up init

Build the init from the pinned release instead of `initramfs/init.c`:

```sh
make -s -C "$(scripts/usb-debug src)/console" init BOARDS=gtactive3wifi \
    NOLIBC="$K/tools/include/nolibc" KHDR="$P/build/hdr/usr/include" \
    CROSS_COMPILE=aarch64-linux-gnu- O="$P/build/initramfs"
```

`BOARDS=` limits the build to one board. Leave it out to build in all of
them: the device tree picks the right one. Module-list changes that cannot
wait for a release go into the initramfs as `/etc/usb-debug.conf` (see
[BOARDS.md](BOARDS.md)). The gtactive3wifi entry is the `init.c` of
gtactive3-mainline 4b70929, with these differences:

- The banner names usb-debug and the board. The console banner reads
  `Galaxy Tab Active3 USB debug console (usb-debug 1.0.0). Commands: ...`.
- New commands: `version` and `size`. In the standalone console, `insmod`
  now reports `already loaded` and real errno values (it used to report
  errno 1).
- `discard` checks the partition's GPT name (metadata, super, cache,
  userdata on `sda`) instead of the device numbers sda24/25/28/31. These
  are the same partitions on the SM-T570 (HARDWARE.md).
- `gta3.recovery` still selects RECOVERY mode, as a whole word, and
  `usbdbg.recovery` does too.
- The unused `max77705_com_to_usb()` (i2c-dev) is gone. The bit-banged
  switch is unchanged.

Run a **device test** of the bring-up image. Check the banner, that "Any
reset now goes to Download mode." is printed, that the console works over
USB, and that VOLUME UP restarts into Download mode. Then delete
`initramfs/init.c` and `initramfs/console-libc.h`.

## 5. Later bumps

Change `.usb-debug-version`, then run `scripts/usb-debug aport ...` again
and rebuild. Each release's notes list protocol changes.
