/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Samsung Galaxy Tab Active3 Wi-Fi (SM-T570), Exynos 9810:
 * exynos-linux/samsung-gtactive3wifi, the first usb-debug board.
 */
#include <linux/input.h>
#include "../usbdbg.h"

extern const struct soc soc_exynos9810;

/* ---------- MAX77705: route USB D+/D- to the AP ----------
 *
 * The bootloader leaves the MAX77705 USB-C data switch open after a warm
 * boot with the cable already attached; Samsung's MUIC driver writes
 * COM_USB at boot (COMMAND_CONTROL1_WRITE = COMP2SW = COMN1SW = USB).
 *
 * The HSI2C_40 controller times out after the bootloader (USI/clock state
 * not fully known yet), but the MAX77705 only needs a few bytes. Drive the
 * two bus pins as open-drain GPIOs through the CMGP pin controller: "0" =
 * output low, "1" = input (released, pulled up by the board). Never drives
 * a line high.
 */
#define PINCTRL_CMGP	0x14220000UL
#define GPM14_OFF	0x180
#define GPM15_OFF	0x1a0

#define MAX77705_PMIC	0x66	/* PMIC block: probed to find SDA */
#define MAX77705_USBC	0x25	/* USB-C / MUIC block */

static volatile unsigned int *cmgp;
static unsigned int bb_sda, bb_scl;

#define CON(o)	cmgp[(o) / 4]
#define DAT(o)	cmgp[((o) + 4) / 4]
#define PUD(o)	cmgp[((o) + 8) / 4]

static void bb_delay(void) { for (int i = 0; i < 80; i++) (void)CON(GPM14_OFF); }
static void bb_rel(unsigned int o) { CON(o) = CON(o) & ~0xfu; }
static void bb_low(unsigned int o) { DAT(o) = DAT(o) & ~1u; CON(o) = (CON(o) & ~0xfu) | 1u; }
static int bb_get(unsigned int o) { return DAT(o) & 1; }

static void bb_scl_rel(void)
{
	bb_rel(bb_scl);
	for (int i = 0; i < 2000 && !bb_get(bb_scl); i++)	/* clock stretching */
		bb_delay();
}

static void bb_start(void) { bb_rel(bb_sda); bb_scl_rel(); bb_delay(); bb_low(bb_sda); bb_delay(); bb_low(bb_scl); bb_delay(); }
static void bb_stop(void) { bb_low(bb_sda); bb_delay(); bb_scl_rel(); bb_delay(); bb_rel(bb_sda); bb_delay(); }

static int bb_bit(int b)	/* write bit b, return the line as sampled */
{
	if (b) bb_rel(bb_sda); else bb_low(bb_sda);
	bb_delay();
	bb_scl_rel();
	bb_delay();
	b = bb_get(bb_sda);
	bb_low(bb_scl);
	bb_delay();
	return b;
}

static int bb_byte(int v)	/* returns 1 on ACK */
{
	for (int i = 7; i >= 0; i--) bb_bit((v >> i) & 1);
	return !bb_bit(1);
}

static int bb_rbyte(int ack)
{
	int v = 0;

	for (int i = 0; i < 8; i++) v = (v << 1) | bb_bit(1);
	bb_bit(!ack);
	return v;
}

static int bb_write(int addr, const unsigned char *b, int n)
{
	int ok;

	bb_start();
	ok = bb_byte(addr << 1);
	for (int i = 0; ok && i < n; i++) ok = bb_byte(b[i]);
	bb_stop();
	return ok;
}

static int bb_read(int addr, int reg, unsigned char *b, int n)
{
	int ok;

	bb_start();
	ok = bb_byte(addr << 1) && bb_byte(reg);
	if (ok) {
		bb_start();	/* repeated start */
		ok = bb_byte((addr << 1) | 1);
		for (int i = 0; ok && i < n; i++) b[i] = bb_rbyte(i + 1 < n);
	}
	bb_stop();
	return ok;
}

/* MAX77705 USB-C block: route D+/D- to the AP (Samsung COMMAND_CONTROL1_WRITE) */
static int max77705_bitbang(void)
{
	static const unsigned char sw[3] = { 0x21, 0x06, 0x09 }, end[2] = { 0x41, 0x00 };
	static const unsigned char rq[2] = { 0x21, 0x05 };
	unsigned int con14, con15, pud14, pud15;
	unsigned char rb[4] = { 0 };
	volatile unsigned int *base;
	char b[12];
	int map, st = 0;

	base = map_reg(PINCTRL_CMGP);
	if (!base) { out("  bitbang: cannot map pin controller\n"); return 0; }
	cmgp = base;
	con14 = CON(GPM14_OFF); con15 = CON(GPM15_OFF);
	pud14 = PUD(GPM14_OFF); pud15 = PUD(GPM15_OFF);
	PUD(GPM14_OFF) = pud14 & ~0xfu;	/* board pull-ups only */
	PUD(GPM15_OFF) = pud15 & ~0xfu;
	bb_rel(GPM14_OFF); bb_rel(GPM15_OFF);
	bb_delay(); bb_delay();
	out("  bitbang: idle lines gpm14="); out(bb_get(GPM14_OFF) ? "1" : "0");
	out(" gpm15="); out(bb_get(GPM15_OFF) ? "1\n" : "0\n");

	/* find which pin is SDA by probing the MAX77705 PMIC block */
	for (map = 0; map < 2; map++) {
		bb_sda = map ? GPM15_OFF : GPM14_OFF;
		bb_scl = map ? GPM14_OFF : GPM15_OFF;
		if (bb_write(MAX77705_PMIC, 0, 0)) break;
	}
	if (map == 2) {
		out("  bitbang: no ACK from 0x66 on either pin order\n");
		st = 0;
	} else {
		out(map ? "  bitbang: SDA=gpm15 SCL=gpm14, 0x66 ACK\n" : "  bitbang: SDA=gpm14 SCL=gpm15, 0x66 ACK\n");
		if (bb_write(MAX77705_USBC, sw, 3) && bb_write(MAX77705_USBC, end, 2)) {
			out("  MAX77705: data switch -> USB (bit-banged)\n");
			st = 2;
		} else {
			out("  MAX77705: USB-C block 0x25 NACK\n");
			st = 1;
		}
		/* read CONTROL1 back for the log */
		if (bb_write(MAX77705_USBC, rq, 2) && bb_write(MAX77705_USBC, end, 2)) {
			usleep(20000);
			if (bb_read(MAX77705_USBC, 0x51, rb, 3)) {
				out("  MAX77705 CONTROL1 read:");
				for (int i = 0; i < 3; i++) { hex32(b, rb[i]); out(" "); out(b + 8); }
				out("\n");
			}
		}
	}
	/* hand the pins back to the HSI2C function */
	CON(GPM14_OFF) = con14; CON(GPM15_OFF) = con15;
	PUD(GPM14_OFF) = pud14; PUD(GPM15_OFF) = pud15;
	unmap_reg(base);
	return st;
}

/*
 * HSI2C_40 (the MAX77705's bus) sits in a USI block whose USI_CON[0] reset
 * stays asserted after the bootloader; Samsung's driver clears it before
 * any transfer. Put it into I2C mode and clock it, then switch the
 * MAX77705's data lines to the AP.
 * TODO: describe the USI wrapper in DT instead (exynos-usi).
 */
#define SYSREG_CMGP	0x14212030UL	/* USI_03_CMGP (hsi2c@14360000) SW_CONF */
#define CMU_CMGP	0x14200000UL
#define USI_CMGP03	0x14360000UL

static int usb_prep(void)
{
	unsigned int mux = 0, div = 0;
	char b[12];

	poke(SYSREG_CMGP, 0x4);		/* I2C mode */
	force_gate("CMGP GATE_CLK_USI_CMGP03", CMU_CMGP + 0x2014);
	force_gate("CMGP USI_CMGP03 IPCLK", CMU_CMGP + 0x20a8);
	force_gate("CMGP USI_CMGP03 PCLK", CMU_CMGP + 0x20ac);
	force_gate("CMGP RSTNSYNC USI_CMGP03", CMU_CMGP + 0x2074);
	peek(CMU_CMGP + 0x1018, &mux);
	peek(CMU_CMGP + 0x1814, &div);
	hex32(b, mux); out("  USI_CMGP03 mux "); out(b);
	hex32(b, div); out(" div "); out(b); out("\n");
	poke(USI_CMGP03 + 0xc4, 0);	/* release the USI reset */
	return max77705_bitbang();
}

static const struct step late[] = {
	/* bundle 1 (tested 2026-10-21): hardware I2C behind its USI wrapper, DCS backlight */
	MODULE("exynos-usi"),
	MODULE("i2c-exynos5"),
	MODULE("exynos9810-dsim-bl"),
	/* bundle 2 (tested 2026-10-21): TMU, ACPM + read-only S2MPS18, MAX77705 */
	MODULE("exynos9810_tmu"),
	MODULE("exynos-mailbox"),
	MODULE("acpm-protocol"),
	MODULE("sec-core"),
	MODULE("sec-acpm"),
	MODULE("s2mps11"),
	/* ACPM DVFS clocks + cpufreq-dt (CPU frequency scaling) */
	MODULE("clk-acpm"),
	MODULE("cpufreq-dt"),
	MODULE("max77705"),
	/* USB-C/BC1.2 input detection: the charger's input supplier */
	MODULE("max77705_usbc_psy"),
	MODULE("max77705_charger"),
	MODULE("simple-mfd-i2c"),
	MODULE("max77705-hwmon"),
	MODULE("max17042_battery"),
	/*
	 * Safety: no battery temperature input yet (the NTC is on an ADC we
	 * have no driver for), so never charge faster than 1.5 A.
	 */
	WRITE("/sys/class/power_supply/max77705-charger/constant_charge_current", "1500000"),
	/* bundle 3 (tested 2026-10-21): panfrost on the Mali-G72, G3D left on by S-Boot */
	MODULE("gpu-sched"),
	MODULE("panfrost"),
	{ 0, 0, 0 },
};

static const struct keyname keynames[] = {
	{ KEY_PROG1, "ACTIVE KEY" },
	{ 0, 0 },
};

/* the Android partitions the user approved wiping (sda24, 25, 28, 31) */
static const char *const discard[] = { "metadata", "super", "cache", "userdata", 0 };

const struct board board_gtactive3wifi = {
	.name = "gtactive3wifi",
	.compatible = "samsung,gtactive3wifi",
	.title = "Galaxy Tab Active3",
	.soc = &soc_exynos9810,
	.manufacturer = "Samsung",
	.product = "Galaxy Tab Active3 (mainline bring-up)",
	.serial = "gtactive3-bringup",
	.usb_prep = usb_prep,
	.late = late,
	.keys = "/dev/input/event0",
	.keynames = keynames,
	.key_download = KEY_VOLUMEUP,
	.i2c = "/dev/i2c-0",
	.disk = "sda",
	.discard = discard,
	/* U-Boot's RAM log; uniLoader's record is its last 4 KiB page */
	.ulog_base = 0x8ff100000UL,
	.ulog_size = 0x100000UL,
	.ul_record = 0x8ff1ff000UL,
	.recovery_arg = "gta3.recovery",
	.watchdog_s = 30,
};
