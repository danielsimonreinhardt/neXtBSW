#pragma once

/* TMC6200 register map (C compatible).
 *
 * Source of every value: TMC6200 datasheet Rev. 1.08 (2022-06-01),
 * doc/hardware/tmc6200/TMC6200_datasheet_rev1.08.pdf. "p." is the page number printed in that document.
 * "verify" marks values the datasheet states unclearly or contradictorily (listed in libs/bsw/gdTmc6200/doc).
 *
 * All registers are 32 bit wide on the bus; unused bits read 0 (p.18 4.1, p.21 note "reset to 0 unless
 * otherwise noted"). */

/* ------------------------------------------------------------------------------------------- SPI datagram */
#define TMC6200_FRAME_BITS    40U   /* p.18 4.1: 8 bit address + 32 bit data, CSN low for the whole datagram */
#define TMC6200_WRITE_FLAG    0x80U /* p.18 4.1.1: MSB of the address byte, 1 = write */
#define TMC6200_ADDRESS_MASK  0x7FU
#define TMC6200_DATA_MASK     0xFFFFFFFFUL
/* p.18: the first received byte is the "8 bit SPI status"; the example shows the address byte of the previous
 * access there (verify: the figure and the example disagree). The driver does not rely on it. */
#define TMC6200_STATUS_SHIFT  32U
/* p.19-20 4.2/4.3: MSB first, SPI mode 3 (CPOL 1, CPHA 1) */
#define TMC6200_SPI_CPOL      1U
#define TMC6200_SPI_CPHA      1U
/* p.20 table "SPI interface timing": SCK with internal clock at most 3.5 MHz (assumes minimum oscillator frequency) */
#define TMC6200_SPI_MAX_SCK_HZ_INTERNAL_CLOCK 3500000UL

/* ------------------------------------------------------------------------------------------- addresses */
#define TMC6200_REG_GCONF        0x00U /* p.22 RW  global configuration */
#define TMC6200_REG_GSTAT        0x01U /* p.22-23 R+WC  global status, write 1 to clear */
#define TMC6200_REG_IOIN         0x04U /* p.23-24 R  input pins, temperature levels, version */
#define TMC6200_REG_OTP_PROG     0x06U /* p.24 W  OTP programming - never written by the driver */
#define TMC6200_REG_OTP_READ     0x07U /* p.24 R  OTP content (p.26 table title says 0x05: verify) */
#define TMC6200_REG_FACTORY_CONF 0x08U /* p.24 RW  clock trim - never written ("should not be altered", p.26) */
#define TMC6200_REG_SHORT_CONF   0x09U /* p.24-25 RW  short detection */
#define TMC6200_REG_DRV_CONF     0x0AU /* p.25 RW  break-before-make, overtemperature level, drive strength */

/* ------------------------------------------------------------------------------------------- GCONF 0x00, p.22 */
#define TMC6200_GCONF_DISABLE_MASK        0x01UL /* bit 0: 1 = driver disabled (resets short condition) */
#define TMC6200_GCONF_SINGLELINE_MASK     0x02UL /* bit 1: 0 = individual L+H signals, 1 = H polarity / L enable */
#define TMC6200_GCONF_FAULTDIRECT_MASK    0x04UL /* bit 2: 1 = FAULT shows each overcurrent shutdown action */
#define TMC6200_GCONF_AMPLIFICATION_SHIFT 4U     /* bits 5:4: 0 = x5, 1 = x10, 2 = (x10), 3 = x20 */
#define TMC6200_GCONF_AMPLIFICATION_MASK  0x30UL
#define TMC6200_GCONF_AMPLIFIER_OFF_MASK  0x40UL /* bit 6: 1 = current sense amplifiers off */
#define TMC6200_GCONF_TEST_MODE_MASK      0x80UL /* bit 7: analog test output on DRV_EN - "not for user, set to 0" */
#define TMC6200_GCONF_WRITABLE_MASK       0xF7UL /* bit 3 and 31:8 unused */
/* p.22 singleline "reset default = 1", p.6 "the driver starts up in single line mode"; all other bits 0 (p.21) */
#define TMC6200_GCONF_RESET               0x02UL

/* ------------------------------------------------------------------------------------------- GSTAT 0x01, p.22-23 */
#define TMC6200_GSTAT_RESET_MASK      0x0001UL /* bit 0: IC reset, registers at reset values; clear needs DRV_EN high */
#define TMC6200_GSTAT_DRV_OTPW_MASK   0x0002UL /* bit 1: overtemperature prewarning (no action), latched */
#define TMC6200_GSTAT_DRV_OT_MASK     0x0004UL /* bit 2: shut down by overtemperature, clears only when cool */
#define TMC6200_GSTAT_UV_CP_MASK      0x0008UL /* bit 3: charge pump undervoltage, driver disabled meanwhile */
#define TMC6200_GSTAT_SHORTDET_U_MASK 0x0010UL /* bit 4: short counter U has triggered at least once */
#define TMC6200_GSTAT_S2GU_MASK       0x0020UL /* bit 5: short to GND U, driver disabled until cleared */
#define TMC6200_GSTAT_S2VSU_MASK      0x0040UL /* bit 6: short to VS U, driver disabled until cleared */
#define TMC6200_GSTAT_SHORTDET_V_MASK 0x0100UL /* bit 8 */
#define TMC6200_GSTAT_S2GV_MASK       0x0200UL /* bit 9 */
#define TMC6200_GSTAT_S2VSV_MASK      0x0400UL /* bit 10 */
#define TMC6200_GSTAT_SHORTDET_W_MASK 0x1000UL /* bit 12 */
#define TMC6200_GSTAT_S2GW_MASK       0x2000UL /* bit 13 */
#define TMC6200_GSTAT_S2VSW_MASK      0x4000UL /* bit 14 */
#define TMC6200_GSTAT_ALL_MASK        0x777FUL /* bits 7 and 11 unused ("-") */
/* Cycling DRV_EN clears all bits except reset and drv_otpw (p.22) */
#define TMC6200_GSTAT_CLEARED_BY_DRV_EN_MASK \
    (TMC6200_GSTAT_ALL_MASK & ~(TMC6200_GSTAT_RESET_MASK | TMC6200_GSTAT_DRV_OTPW_MASK))

/* ------------------------------------------------------------------------------------------- IOIN 0x04, p.23-24 */
#define TMC6200_IOIN_UL_MASK      0x001UL /* bit 0 */
#define TMC6200_IOIN_UH_MASK      0x002UL /* bit 1 */
#define TMC6200_IOIN_VL_MASK      0x004UL /* bit 2 */
#define TMC6200_IOIN_VH_MASK      0x008UL /* bit 3 */
#define TMC6200_IOIN_WL_MASK      0x010UL /* bit 4 */
#define TMC6200_IOIN_WH_MASK      0x020UL /* bit 5 */
#define TMC6200_IOIN_DRV_EN_MASK  0x040UL /* bit 6 */
#define TMC6200_IOIN_OTPW_MASK    0x100UL /* bit 8 */
#define TMC6200_IOIN_OT136_MASK   0x200UL /* bit 9 */
#define TMC6200_IOIN_OT143_MASK   0x400UL /* bit 10 */
#define TMC6200_IOIN_OT150_MASK   0x800UL /* bit 11 */
#define TMC6200_IOIN_VERSION_SHIFT 24U    /* bits 31:24 */
#define TMC6200_IOIN_VERSION_MASK 0xFF000000UL
#define TMC6200_VERSION           0x10U   /* "0x10 = first version of the IC" */

/* ------------------------------------------------------------------------------------------- OTP_READ 0x07, p.26 */
#define TMC6200_OTP_FCLKTRIM_MASK   0x1FUL /* otp0.4..0: factory clock trim, differs per IC */
#define TMC6200_OTP_S2_LEVEL_MASK   0x20UL /* otp0.5: 0 = short levels 6, 1 = short levels 12 */
#define TMC6200_OTP_BBM_SHIFT       6U     /* otp0.7..6: 0 = 4 clocks, 1 = 1, 2 = 2, 3 = 3 */
#define TMC6200_OTP_BBM_MASK        0xC0UL

/* ------------------------------------------------------------------------------------------- FACTORY_CONF 0x08, p.24 */
#define TMC6200_FACTORY_CONF_FCLKTRIM_MASK 0x1FUL /* 4:0, reset from OTP */

/* ------------------------------------------------------------------------------------------- SHORT_CONF 0x09, p.24-25, p.32 */
#define TMC6200_SHORT_CONF_S2VS_LEVEL_SHIFT   0U  /* 3:0: 1 (most sensitive) .. 15; reset OTP 6 or 12 */
#define TMC6200_SHORT_CONF_S2VS_LEVEL_MASK    0x0000000FUL
#define TMC6200_SHORT_CONF_S2G_LEVEL_SHIFT    8U  /* 11:8: 2 (most sensitive) .. 15; reset OTP 6 or 12 */
#define TMC6200_SHORT_CONF_S2G_LEVEL_MASK     0x00000F00UL
#define TMC6200_SHORT_CONF_SHORTFILTER_SHIFT  16U /* 17:16: 0 = 100 ns, 1 = 1 us, 2 = 2 us, 3 = 3 us; reset 1 */
#define TMC6200_SHORT_CONF_SHORTFILTER_MASK   0x00030000UL
#define TMC6200_SHORT_CONF_SHORTDELAY_SHIFT   20U /* bit 20: 0 = 750 ns, 1 = 1500 ns; reset 0 */
#define TMC6200_SHORT_CONF_SHORTDELAY_MASK    0x00100000UL
#define TMC6200_SHORT_CONF_RETRY_SHIFT        24U /* 25:24: retries before permanent shutdown; reset 3 */
#define TMC6200_SHORT_CONF_RETRY_MASK         0x03000000UL
#define TMC6200_SHORT_CONF_PROTECT_PARALLEL_MASK 0x10000000UL /* bit 28: 1 = all bridges off; reset 1 */
#define TMC6200_SHORT_CONF_DISABLE_S2G_MASK   0x20000000UL /* bit 29: 1 = no short to GND protection; reset 0 */
#define TMC6200_SHORT_CONF_DISABLE_S2VS_MASK  0x40000000UL /* bit 30: 1 = no short to VS protection; reset 0 */
#define TMC6200_SHORT_CONF_WRITABLE_MASK      0x73130F0FUL
#define TMC6200_SHORT_LEVEL_S2VS_MIN          1U  /* p.24 (p.32 says 4..15: verify) */
#define TMC6200_SHORT_LEVEL_S2G_MIN           2U  /* p.24, p.32 */
#define TMC6200_SHORT_LEVEL_MAX               15U
/* Reset value without the OTP dependent levels (filter 1, delay 0, retry 3, protect_parallel 1) */
#define TMC6200_SHORT_CONF_RESET_WITHOUT_LEVELS 0x13010000UL

/* ------------------------------------------------------------------------------------------- DRV_CONF 0x0A, p.25, p.14 */
#define TMC6200_DRV_CONF_BBMCLKS_SHIFT     0U  /* 4:0: digital BBM time in clock cycles 0..15 (typ. 42 ns/clock), only
                                                  with singleline = 1; reset OTP 1..4 (4 unprogrammed). Field is 5 bit
                                                  wide, range 0..15 (verify) */
#define TMC6200_DRV_CONF_BBMCLKS_MASK      0x0000001FUL
#define TMC6200_BBMCLKS_MAX                15U
#define TMC6200_DRV_CONF_OTSELECT_SHIFT    16U /* 17:16: 0 = 150 C, 1 = 143 C, 2 = 136 C, 3 = 120 C; reset 0 */
#define TMC6200_DRV_CONF_OTSELECT_MASK     0x00030000UL
#define TMC6200_DRV_CONF_DRVSTRENGTH_SHIFT 18U /* 19:18: 0 weak, 1 weak+TC, 2 medium, 3 strong; reset 2 */
#define TMC6200_DRV_CONF_DRVSTRENGTH_MASK  0x000C0000UL
#define TMC6200_DRV_CONF_WRITABLE_MASK     0x000F001FUL
/* Reset value without the OTP dependent BBM time (OTSELECT 0, DRVSTRENGTH 2) */
#define TMC6200_DRV_CONF_RESET_WITHOUT_BBM 0x00080000UL

/* ------------------------------------------------------------------------------------------- characteristics */
/* p.14, p.25: BBM time per clock cycle, typical (depends on the clock: p.37 24 MHz, p.24 says 12 MHz: verify) */
#define TMC6200_BBM_NS_PER_CLOCK          42U
/* p.38 "Short detector level", typical: level 6 = 625 mV, level 15 = 1560 mV for S2VS and for S2G (VS < 52 V).
 * Only these two points are given; linear between them (104 mV per step) is an interpolation (verify). */
#define TMC6200_SHORT_LEVEL_6_MV          625U
#define TMC6200_SHORT_LEVEL_15_MV         1560U
/* p.36 gate drive current turning on at 2 V VGS, typical: DRVSTRENGTH 0 = 400 mA, 2 = 800 mA, 3 = 1200 mA
 * (no value for 1 "weak+TC") */
#define TMC6200_GATE_CURRENT_WEAK_MA      400U
#define TMC6200_GATE_CURRENT_MEDIUM_MA    800U
#define TMC6200_GATE_CURRENT_STRONG_MA    1200U
