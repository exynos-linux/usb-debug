/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Exynos 9810: what the bring-up init needs of the SoC.
 * Values from the SM-T570 port (exynos-linux/samsung-gtactive3wifi).
 */
#include "../usbdbg.h"

#define CMU_TOP_BASE	0x1a240000UL
#define CMU_FSYS0_BASE	0x11000000UL

static const struct gate usb_gates[] = {
	{ "CMU_TOP GATE_CLKCMU_FSYS0_USB30DRD", CMU_TOP_BASE + 0x2064 },
	{ "FSYS0 GOUT_USB30DRD_REF_CLK", CMU_FSYS0_BASE + 0x206c },
	{ 0, 0 },
};

static const char *const usb_modules[] = {
	"phy-exynos5-usbdrd",
	"dwc3",
	"dwc3-exynos",
	0,
};

/* storage, so the partitions are there without a manual insmod */
static const char *const modules[] = {
	"phy-exynos-ufs",
	"ufshcd-core",
	"ufshcd-pltfrm",
	"ufs-exynos",
	0,
};

const struct soc soc_exynos9810 = {
	.name = "exynos9810",
	.compatible = "samsung,exynos9810",
	.pmu_base = 0x14060000UL,
	.pmu_inform2 = 0x0808,
	.pmu_inform3 = 0x080c,
	.pmu_rst_stat = 0x0404,
	.rst_wdt_mask = 1u << 24,	/* RST_STAT CL0_WDTRESET */
	.gate_on = (1u << 21) | (1u << 20),	/* CG_VAL | MANUAL */
	.usb_gates = usb_gates,
	.usb_modules = usb_modules,
	.modules = modules,
};
