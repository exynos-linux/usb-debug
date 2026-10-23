# Changelog

## 1.0.0

The first release. The code comes from gtactive3-mainline (commit 4b70929:
`initramfs/init.c`, `scripts/tab*`, `devd.sh`, `notify-user`,
`hot-modules.sh`, the `duranium-usb-debug` aport r6) and no longer assumes
one device.

- **Bring-up init**: a generic core plus SoC and board entries matched by
  the device tree. `gtactive3wifi` (Exynos 9810) is the first board and
  loads the same modules in the same order. `/etc/usb-debug.conf` can
  replace the late module list. Device repositories can add their own board
  file (`EXTRA_BOARDS=`).
- **Console protocol**: new `version` and `size` commands. `discard` checks
  GPT names against the board's list. The banner names usb-debug and the
  board. The standalone console reports real errno values.
- **duranium-usb-debug 1.0.0** (was 1-r6): the register console on TCP 23
  is now part of the package (`duranium-debug-console.socket`), replacing
  each device's own console subpackage. Settings live in
  `/usr/lib/duranium-usb-debug/conf.d/*.conf` (UDC, ports, USB strings), and
  a systemd generator applies the ports. `sshd_config` moved to `/usr/share`.
  The firewall rule now opens 2323 and is also linked by tmpfiles. The
  sources come from a release tarball, so the aport is a candidate for
  upstream pmaports.
- **Host tool** `usb-debug`: one command for wait, cmd, put, get, sh,
  shell, klog, ssh, shot, login, unlock, checkpw, flashd, notify,
  hot-modules, config, version, self-update, src and aport. It reads
  `config.toml` through the device repository's `scripts/config`. `setup.sh`
  installs it, and device repositories pin a version with
  `contrib/device-shim.sh`.
