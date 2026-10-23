/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * usb-debug console: what a SoC and a board (device) entry describe, and the
 * helpers of the core (core.c) that board files may use.
 *
 * Every hardware-specific value of the console lives in a struct soc
 * (soc/<soc>.c) or a struct board (boards/<device>.c). The core never pokes
 * a register that a matching entry does not name: on a device without one,
 * the bring-up init only brings up the USB gadget and the console.
 */
#ifndef USBDBG_H
#define USBDBG_H

/* A clock gate register that the init forces on (soc->gate_on bits). */
struct gate {
	const char *name;
	unsigned long phys;
};

/*
 * One step of the bring-up init once its console is up: load a module
 * (a path, or a name in /lib/modules) or write a value to a sysfs file.
 */
struct step {
	const char *module;
	const char *write;
	const char *value;
};
#define MODULE(m)	{ (m), 0, 0 }
#define WRITE(p, v)	{ 0, (p), (v) }

/* Names the bring-up init prints for key presses. */
struct keyname {
	int code;
	const char *name;
};

struct soc {
	const char *name;		/* "exynos9810", as in the kernel's DT names */
	const char *compatible;		/* its DT compatible, "samsung,exynos9810" */

	/*
	 * Samsung PMU: S-Boot reads INFORM2/INFORM3 after a reset to pick
	 * Download mode, RECOVERY or a normal boot. 0 = no PMU: the init
	 * cannot arm a reset target.
	 */
	unsigned long pmu_base;
	unsigned int pmu_inform2;	/* offsets from pmu_base */
	unsigned int pmu_inform3;
	unsigned int pmu_rst_stat;	/* 0 = none */
	unsigned int rst_wdt_mask;	/* RST_STAT bits of a watchdog reset */

	/* bits a forced-on gate gets (CG_VAL | MANUAL on Exynos) */
	unsigned int gate_on;
	/* gates the USB controller needs before its modules load, 0-terminated */
	const struct gate *usb_gates;
	/* PHY and DWC3 modules, loaded in this order before the gadget */
	const char *const *usb_modules;
	/* loaded after the console is up (storage first), 0-terminated */
	const char *const *modules;
};

struct board {
	const char *name;		/* "gtactive3wifi": file name, BOARD= */
	const char *compatible;		/* its DT compatible, "samsung,gtactive3wifi" */
	const char *title;		/* "Galaxy Tab Active3", for the banners */
	const struct soc *soc;

	/* the bring-up gadget's USB strings (0 = generic ones) */
	const char *manufacturer;
	const char *product;
	const char *serial;

	/*
	 * Runs after the SoC's USB gates, before the USB modules load: e.g. a
	 * USB-C data switch that must point at the SoC. Returns 0 (could not
	 * talk to the hardware), 1 (it did not answer) or 2 (done), which the
	 * init reports through its no-host reboot delay (see core.c).
	 */
	int (*usb_prep)(void);

	/* after the SoC's modules, in order, ended by an empty step */
	const struct step *late;

	const char *keys;		/* evdev node of the buttons */
	const struct keyname *keynames;	/* 0-terminated, extends the core's */
	int key_download;		/* restarts into Download mode */

	const char *i2c;		/* bus of the i2cw / i2cr commands */

	/*
	 * "discard DEV" only accepts partitions of this whole disk whose GPT
	 * name is in the list (the Android partitions an installer may wipe).
	 * 0 = the command is refused.
	 */
	const char *disk;
	const char *const *discard;

	/* U-Boot's RAM console (CONFIG_EXYNOS_MOBILE_RAMLOG); 0 = none */
	unsigned long ulog_base;
	unsigned long ulog_size;
	/*
	 * uniLoader's boot record page (exynos-linux boot chain); 0 = none.
	 * With it, the init restarts an OS that the watchdog reset.
	 */
	unsigned long ul_record;

	/* kernel command line word for a RECOVERY test kernel, besides usbdbg.recovery */
	const char *recovery_arg;
	int watchdog_s;			/* 0 = 30 */
};

extern const struct board *board;	/* the one matching this device, or 0 */

/* output: the framebuffer console and /dev/kmsg (bring-up), stderr (console) */
void out(const char *s);
void hex32(char *b, unsigned long v);
void outhex(const char *label, unsigned long v);

/* physical memory through /dev/mem */
volatile unsigned int *map_reg(unsigned long phys);
void unmap_reg(volatile unsigned int *r);
int peek(unsigned long phys, unsigned int *val);
int poke(unsigned long phys, unsigned int val);
void force_gate(const char *name, unsigned long phys);

#endif
