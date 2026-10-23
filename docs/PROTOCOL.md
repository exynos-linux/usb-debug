# The USB debug console protocol

This is the contract between a device and every host tool: `usb-debug`, the
device-template installer (`scripts/installer/py/console.py`) and the
scripts in device repositories. The bring-up init (`console/core.c`) and the
standalone console of development images (`usb-debug-console`, the same
source built with `CONSOLE_STDIO`) both serve it.

## The link

- USB gadget `1d6b:0104` with one NCM function. The device MAC is
  `02:57:0a:00:00:01` and the host MAC is `02:57:0a:00:00:02`. The IPv6
  link-local addresses therefore never change: the device is
  `fe80::57:aff:fe00:1` and the host is `fe80::57:aff:fe00:2`. Every
  exynos-linux device uses the same addresses.
- On the host, the link is the interface whose MAC is `02:57:0a:00:00:02`
  (the `cdc_ncm` driver), for example `fe80::57:aff:fe00:1%enp0s20f0u2`.

| TCP port | Service | Where |
|---|---|---|
| `console.port` (23) | this line console | bring-up init, initrd, booted system |
| `console.shell_port` (2323) | root shell (busybox `telnetd -l /bin/sh`) | initrd, booted system |
| 24 | kernel log stream (`dmesg -w`) | initrd, booted system |
| 22 | sshd with its own host key (root login needs a key or password) | booted system |

The ports are settings on both sides. On the host they come from
`config.toml` `[console]`. On the device they come from
`/usr/lib/duranium-usb-debug/conf.d/*.conf` (`CONSOLE_PORT=`, ...). Change
both together.

## The console

When a client connects, the console sends a banner line, then the prompt
`> ` (greater-than, space; no newline). The client sends one command per
line (`\n`; a `\r` is ignored). The console answers and sends the prompt
again. The prompt always ends an answer, so a client reads until the
buffer ends with `> `. Binary output (`cat`, `memcat`) has exactly the
length the client asked for, followed by the prompt. Each connection is
served by its own process: a command that crashes (for example a `peek` of
an unclocked block) only drops that connection.

Lines are at most 254 bytes. Numbers are decimal or `0x` hex. Arguments are
separated by single spaces and cannot be quoted.

| Command | Answer |
|---|---|
| `version` | `usb-debug X.Y.Z board NAME soc NAME` (`none` when there is no entry). New in 1.0; older consoles say `unknown command`. |
| `dmesg` | the kernel log buffer |
| `peek ADDR` | `0x%08x` of the 32-bit register at physical ADDR, or `error` |
| `dump ADDR N` | N lines `%08lx 0x%08x` (at most 1024), stops at `error` |
| `poke ADDR VAL` | `ok` / `error` |
| `memcat ADDR LEN` | LEN raw bytes of physical memory (4-byte aligned; `/dev/mem`, so not ordinary RAM under STRICT_DEVMEM), or `error` |
| `cat PATH` | the raw contents (a block device: all of it), or `error` |
| `size PATH` | the byte count `cat PATH` sends (block devices: their size), or `error`. New in 1.0. |
| `write PATH VALUE` | writes VALUE (the rest of the line, no newline added): `ok` / `error` |
| `put PATH LEN` + LEN raw bytes | creates or truncates PATH (mode 0644): `ok <received>`, or `error` (the bytes are still consumed) |
| `ls DIR` | one name per line (no dot files), or `error` |
| `run PATH [ARGS]` | runs PATH (made executable) with at most 7 arguments, output on the connection, killed after 60 s; then `run: exit N signal S` (`run: killed after 60 s` before it) |
| `insmod PATH [PARAMS]` | `ok`, `already loaded`, or `FAILED` + `errno N` |
| `rmmod NAME` | `ok` / `FAILED errno N` |
| `i2cw ADDR B0 B1 ...` | raw I2C write on the board's bus: `ok` / `i2c error (no ACK?)` |
| `i2cr ADDR REG N` | write REG, read N bytes (at most 64): `0xNN` hex bytes separated by spaces |
| `evread PATH [N] [SECS]` | `name: <device>`, then up to N (64) events `sec.usec type code value` (hex type and code); `timeout` after SECS (5) without an event |
| `discard DEV` | erases (BLKDISCARD) a whole partition: `discarded N bytes`. Only partitions of the board's main disk whose GPT name the board lists (Android's metadata, super, cache, userdata). Anything else: `discard: not an approved partition`. |
| `ulog` | U-Boot's RAM console log (boards that have one), or `ulog: ...` |
| `reboot [download\|recovery\|normal]` | `rebooting`, then the device restarts (`LINUX_REBOOT_CMD_RESTART2` with that word) |
| `kexec PATH` | starts the kernel Image at PATH (its own built-in initramfs) |
| `stay` | bring-up init only: cancels the pending restart of a hung OS (`ok`) |
| anything else | `unknown command` |

Hosts must treat the banner as free text. Version 1.0 changed it from
`SM-T570 mainline bring-up console. Commands: ...` to `<title> USB debug
console (usb-debug X.Y.Z). Commands: ...`.

## The bring-up init's other duties

- Arms "reboot into Download mode" for any reset (Samsung PMU INFORM2 =
  `0x12345678`, INFORM3 = `0x12345671`; with `usbdbg.recovery` or the
  board's `recovery_arg` on the command line, `0x12345674` = RECOVERY). It
  only does this when the SoC entry matches.
- If no host configures the gadget, it reboots into Download mode after
  `30 + 10 × code` seconds. `code` is the board's USB preparation status
  (0 no bus, 1 NACK, 2 ACK) plus 3 when the gadget bound. A host loop can
  read this timing even when the link never comes up.
- Until a host connects, it drops and re-asserts the D+ pull-up every 8 s.
- On boards with a uniLoader record (`ul_record`): if the watchdog reset an
  OS that U-Boot had handed over to, it restarts that OS after 15 s, at
  most twice in a row. The board's Download key or the `stay` command keeps
  it in the console instead.
- It pets the hardware watchdog (`/dev/watchdog0`, the board's timeout,
  default 30 s), so a wedged init resets the SoC into Download mode.
- Its Download key (the board's `key_download`, default VOLUME UP) restarts
  into Download mode.
