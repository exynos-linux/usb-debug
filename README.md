# usb-debug

A USB debug link for every [exynos-linux](https://github.com/exynos-linux)
device port. A tablet becomes a USB network device. Over that network you
get a root console, file transfer, screenshots and the kernel log. The same
tools work from the first bring-up kernel to a full postmarketOS (Duranium)
development image.

| Part | What it is | Where |
|---|---|---|
| **Bring-up init** | A tiny `init` built with the kernel's nolibc. It arms "reboot into Download mode", brings up the USB gadget, serves the console and reboots into Download mode when no host shows up. | [`console/`](console/) |
| **Device layer** | The `duranium-usb-debug` package for development images: NCM gadget, register console (TCP 23), root shell (2323), ssh (22), kernel log (24), optional adb. It is in the initrd and in the booted system. | [`device/`](device/), [`pmos/aports/main/duranium-usb-debug`](pmos/aports/main/duranium-usb-debug/APKBUILD) |
| **Host tool** | `usb-debug`, one command with subcommands. It reads the device repository's `config.toml`. | [`host/`](host/) |

Every device is `fe80::57:aff:fe00:1` on its USB link (the gadget's fixed
MAC addresses make it so), and the console protocol is the same everywhere:
see [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Install the host tool

```sh
curl -fsSL https://github.com/exynos-linux/usb-debug/releases/latest/download/install.sh | sh
```

This installs into `~/.local/share/usb-debug/<version>` and links
`~/.local/bin/usb-debug`. It needs Python 3 (3.11 or newer reads
`config.toml` directly), curl or wget, and nothing else. `usb-debug
self-update` moves to the newest release. Downloads are checked against the
release's `SHA256SUMS`. Device repositories pin a version instead (see
[docs/MIGRATION.md](docs/MIGRATION.md)).

## Use it

Run it inside a device repository, or pass `--repo DIR`. The ports and the
Download-mode USB id come from that repository's `config.toml`
(`[console] port, shell_port`, `[installer] download_usb, flash_allow,
boot_partition, recovery_partition, protected`), read through its
`scripts/config` the way
[device-template](https://github.com/exynos-linux/device-template) does.
Without a repository the template's defaults apply.

```sh
usb-debug wait                      # until the device's USB network is up
usb-debug cmd dmesg                 # one console command (see PROTOCOL.md)
usb-debug cmd peek 0x1406080c
usb-debug put Image /run/Image      # upload; prints "ok <bytes>"
usb-debug get /dev/sda24 meta.img   # download a file or a whole block device
usb-debug sh -c 'uname -a; ls /run' # a shell script (development images)
usb-debug shell                     # interactive root shell (Ctrl-] quits)
usb-debug klog                      # follow the kernel log
usb-debug ssh                       # ssh root@device
usb-debug shot                      # panel screenshot -> build/shots/*.png
usb-debug unlock | login | checkpw  # unattended tests; password in $USB_DEBUG_PASSWORD or stdin
usb-debug flashd                    # flash build/images/boot-mainline.img on every Download mode
usb-debug notify "Press POWER" "Hold it 10 s"   # siren + notification for a human
usb-debug hot-modules drivers/ufs/host/ufs-exynos
usb-debug config                    # what it read from config.toml
usb-debug version --device          # this tool and the device's console
usb-debug self-update
```

`usb-debug --help` and `usb-debug COMMAND --help` describe every option.
The environment variables `USB_DEBUG_ADDR`, `USB_DEBUG_IFACE`,
`USB_DEBUG_REPO` and `USB_DEBUG_LOCK` override the address, the host
interface, the repository and the lock file that `shot`, `login`, `unlock`
and `checkpw` take.

## The bring-up init

```sh
make -C "$(usb-debug src)/console" init \
    NOLIBC=$KERNEL/tools/include/nolibc KHDR=$KERNEL_HEADERS/usr/include \
    CROSS_COMPILE=aarch64-linux-gnu- O=build/initramfs
```

The core ([`console/core.c`](console/core.c)) has nothing device-specific.
The SoC entry ([`console/soc/`](console/soc/)) and the device entry
([`console/boards/`](console/boards/)) hold the PMU reset registers, the USB
clock gates, the modules, the USB-C switch and the keys. The init picks the
entry that matches the device tree's `compatible`, or `usbdbg.board=NAME`
on the kernel command line. On a device without an entry it writes no
registers. It only brings up the gadget and the console. The first entry is
the Galaxy Tab Active3 (`gtactive3wifi`, Exynos 9810). [docs/BOARDS.md](docs/BOARDS.md)
explains how to add a device. A device repository can also keep its own
board file (`EXTRA_BOARDS=`) or change what the init loads without a new
release (`/etc/usb-debug.conf` in the initramfs).

## The device layer

Development images add `duranium-usb-debug` (and optionally
`duranium-usb-debug-adb`). Release images leave it out. A device package
configures it with a `KEY=value` file in
`/usr/lib/duranium-usb-debug/conf.d/` (the UDC, the ports, the USB strings;
see [`usb-debug.conf.example`](device/conf/usb-debug.conf.example)).
[`device/README`](device/README) is the on-device documentation.

The aport is written to go upstream to
[pmaports](https://gitlab.postmarketos.org/postmarketOS/pmaports) once it
has settled. It fetches its sources from this repository's release tarball
and has nothing specific to one device.

## Security

All of this gives **unauthenticated root** to anyone with a USB cable:
register writes, block-device erase (`discard`, limited to the partitions a
board lists), a root shell. The bring-up init and the development images are
for development only. Release images must not contain the
`duranium-usb-debug` package (device-template's `ci/check-image` enforces
this).

## Develop

```sh
make check                    # console + tests (Python 3, a C compiler)
NOLIBC=~/linux/tools/include/nolibc make check   # also tests the nolibc init
```

The init test builds a test variant of the real init (`USBDBG_TEST`: no
register access, no restarts) for the host and runs it as an ordinary
process against a fake device tree. It refuses to run as root. CI also
builds the init and the console for aarch64 and builds the aport on Alpine
for x86_64 and aarch64. [docs/RELEASING.md](docs/RELEASING.md) describes a
release.

The code came from
[gtactive3-mainline](https://github.com/dixonSolutions/gtactive3-mainline)
(moving to exynos-linux/samsung-gtactive3wifi): `initramfs/init.c`,
`scripts/tab*` and the `duranium-usb-debug` aport, at commit 4b70929.

## License

The console, the init and the host tool are GPL-2.0-only. The device
layer's scripts and units are GPL-3.0-or-later. Each file has an SPDX
header, and [LICENSES/](LICENSES/) holds both license texts.
