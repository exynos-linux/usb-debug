# Adding a SoC or a device to the bring-up init

The init ([`console/core.c`](../console/core.c)) has no hardware knowledge of
its own. Everything that touches a register is in two kinds of entries
(types in [`console/usbdbg.h`](../console/usbdbg.h)):

- **`struct soc`** (`console/soc/<soc>.c`, defines `soc_<soc>`): the PMU
  registers S-Boot reads after a reset, the RST_STAT watchdog bit, the clock
  gates the USB controller needs before its modules load, the PHY/DWC3
  modules, and the storage modules.
- **`struct board`** (`console/boards/<device>.c`, defines
  `board_<device>`): the device tree `compatible`, a title for the banners,
  its SoC, the USB strings, a hook for the USB preparation the board needs
  (for example a USB-C switch that must point at the SoC), the modules and
  sysfs writes after the console is up, the keys, the I2C bus of
  `i2cw`/`i2cr`, the partitions `discard` may erase, U-Boot's RAM log and
  uniLoader's record, and the watchdog timeout.

The Makefile builds every file in `soc/` and `boards/` and generates the
table. At boot the init takes the board whose `compatible` is in the device
tree's list (`usbdbg.board=NAME` on the command line overrides that), and
the SoC from the board. If no board matches, it takes the first SoC whose
`compatible` matches.
Without either it runs generic: no register writes, no modules, just the
gadget, the console and the no-host timeout.

## A new device on a known SoC

Copy [`boards/gtactive3wifi.c`](../console/boards/gtactive3wifi.c) and
change:

1. `name` (the file name), `compatible` (the first entry of the DT's
   `compatible`), `title`, and `soc` (`extern const struct soc soc_<soc>;`).
2. `usb_prep`: whatever stops the USB controller from seeing the host. On
   the SM-T570 this is the MAX77705's data switch (bit-banged, because the
   HSI2C controller is not usable yet). Return 0, 1 or 2 (see
   PROTOCOL.md, no-host timeout). Leave it out if nothing is needed.
3. `late`: `MODULE("name")` (from `/lib/modules/name.ko`) and
   `WRITE(path, value)` steps, in order.
4. `keys`, `keynames`, `key_download`, `i2c`.
5. `disk` and `discard`: the main storage's whole-disk name and the GPT
   names an installer may erase. Never list EFS, param, persistent or
   the boot loader partitions.
6. `ulog_base`/`ulog_size`/`ul_record` if the device runs exynos-linux's
   U-Boot with `CONFIG_EXYNOS_MOBILE_RAMLOG` (0 otherwise).

Until a board file is merged here, a device repository can build its own:
`make -C console init EXTRA_BOARDS=/path/to/<device>.c ...`.

## A new SoC

Copy [`soc/exynos9810.c`](../console/soc/exynos9810.c). The PMU base and
the INFORM2/INFORM3 offsets are in the vendor kernel
(`exynos-pmu.h` / the `reboot` driver: `EXYNOS_PMU_INFORM2/3`). The gate
registers are in the vendor CMU (`cmucal`) tables. `gate_on` is the bit
pattern that forces a gate on (Exynos: `CG_VAL | MANUAL`, bits 21 and 20).
Set `pmu_base = 0` if the reset target is unknown: the init then warns
instead of writing.

## Without a usb-debug release

The modules and sysfs writes after the console is up change most often. An
initramfs file `/etc/usb-debug.conf` replaces the SoC's and the board's late
lists:

```
# loaded after the console is up, in this order
module /lib/modules/phy-exynos-ufs.ko
module /lib/modules/panfrost.ko some_param=1
write /sys/class/power_supply/max77705-charger/constant_charge_current 1500000
```

## Testing

`NOLIBC=<linux>/tools/include/nolibc make check` builds a test variant of
the init (no register access, no restarts) for the host. It runs that build
against a fake device tree and checks the banner, the modules in order, the
console and `discard`. Add your board to `tests/test_init.py` the same way
as `gtactive3wifi`. The real test is on the device: the device lead boots
it.
