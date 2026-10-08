/* UJA113x system basis chip: register map (C and C++, shared with the bootloader).
 *
 * Sources: UJA113x series product data sheet (Rev. 2, 5 July 2016, public), NXP AH1501 (software flow) with its example software for the
 * UJA1132 / UJA1132FD4. The /FD variants add CAN FD
 * tolerance and partial networking bits; everything used here is common to all UJA113x derivatives.
 *
 * SPI frame (16 bit, MSB first, CPOL 0 / CPHA 1): [address(7) | RO(1)] [data(8)].
 * RO = 1 reads without writing. Interrupt status bits are cleared by writing 1. */

#pragma once

/* ---- register addresses ---- */
#define UJA113X_REG_WATCHDOG_CONTROL      0x00U
#define UJA113X_REG_MODE_CONTROL          0x01U
#define UJA113X_REG_FAIL_SAFE_CONTROL     0x02U
#define UJA113X_REG_MAIN_STATUS           0x03U
#define UJA113X_REG_SYSTEM_IRQ_ENABLE     0x04U
#define UJA113X_REG_WATCHDOG_STATUS       0x05U
#define UJA113X_REG_MEMORY_0              0x06U
#define UJA113X_REG_LOCK_CONTROL          0x0AU
#define UJA113X_REG_REGULATOR_CONTROL     0x10U
#define UJA113X_REG_BM_SOURCE_CONTROL     0x11U /* battery monitor event trigger source */
#define UJA113X_REG_BM_UV_THRESHOLD       0x12U
#define UJA113X_REG_BM_OV_THRESHOLD       0x13U
#define UJA113X_REG_BM_HYSTERESIS         0x14U
#define UJA113X_REG_BM_VBAT_HIGH          0x15U /* 8 MSB of the BAT conversion result */
#define UJA113X_REG_BM_VBAT_LOW           0x16U /* BMBCS + 2 LSB */
#define UJA113X_REG_BM_VBATSENSE_HIGH     0x17U
#define UJA113X_REG_BM_VBATSENSE_LOW      0x18U
#define UJA113X_REG_SUPPLY_STATUS         0x1BU
#define UJA113X_REG_SUPPLY_IRQ_ENABLE     0x1CU
#define UJA113X_REG_CAN_CONTROL           0x20U
#define UJA113X_REG_TRANSCEIVER_STATUS    0x22U
#define UJA113X_REG_TRANSCEIVER_IRQ_ENABLE 0x23U
#define UJA113X_REG_PN_DATA_RATE          0x26U /* /FD only: partial networking 0x26..0x2F, 0x68..0x6F */
#define UJA113X_REG_PN_ID_0               0x27U /* ID bits 7:0, 0x28..0x2A follow */
#define UJA113X_REG_PN_ID_MASK_0          0x2BU /* ID mask bits 7:0, 0x2C..0x2E follow */
#define UJA113X_REG_PN_FRAME_CONTROL      0x2FU
#define UJA113X_REG_BANK0_HVIO_CONTROL    0x30U /* HVIO1..HVIO4 at 0x30..0x33 */
#define UJA113X_REG_BANK0_WAKE_THRESHOLD  0x34U
#define UJA113X_REG_BANK0_WAKE_LEVEL      0x35U
#define UJA113X_REG_BANK0_DRIVER_STATUS   0x36U
#define UJA113X_REG_BANK0_WAKE_IRQ_ENABLE 0x37U
#define UJA113X_REG_BANK0_FAIL_IRQ_ENABLE 0x38U
#define UJA113X_REG_BANK0_SC_THRESHOLD    0x39U
#define UJA113X_REG_BANK0_OL_THRESHOLD    0x3AU
#define UJA113X_REG_BANK1_HVIO_CONTROL    0x40U /* HVIO5..HVIO8 at 0x40..0x43, two-bank devices only */
#define UJA113X_REG_BANK1_WAKE_THRESHOLD  0x44U
#define UJA113X_REG_BANK1_WAKE_LEVEL      0x45U
#define UJA113X_REG_BANK1_DRIVER_STATUS   0x46U
#define UJA113X_REG_BANK1_WAKE_IRQ_ENABLE 0x47U
#define UJA113X_REG_BANK1_FAIL_IRQ_ENABLE 0x48U
#define UJA113X_REG_BANK1_SC_THRESHOLD    0x49U
#define UJA113X_REG_BANK1_OL_THRESHOLD    0x4AU
#define UJA113X_REG_GLOBAL_IRQ_STATUS     0x60U
#define UJA113X_REG_SYSTEM_IRQ_STATUS     0x61U
#define UJA113X_REG_SUPPLY_IRQ_STATUS     0x62U
#define UJA113X_REG_TRANSCEIVER_IRQ_STATUS 0x63U
#define UJA113X_REG_BANK0_WAKE_IRQ_STATUS 0x64U
#define UJA113X_REG_BANK0_FAIL_IRQ_STATUS 0x65U
#define UJA113X_REG_BANK1_WAKE_IRQ_STATUS 0x66U
#define UJA113X_REG_BANK1_FAIL_IRQ_STATUS 0x67U
#define UJA113X_REG_PN_DATA_MASK_0        0x68U /* /FD only, data mask 0..7 at 0x68..0x6F */
#define UJA113X_REG_MTPNV_STATUS          0x70U
#define UJA113X_REG_STARTUP_CONTROL       0x73U /* MTPNV */
#define UJA113X_REG_SBC_CONFIG_CONTROL    0x74U /* MTPNV */
#define UJA113X_REG_MTPNV_CRC_CONTROL     0x75U /* writing starts MTPNV programming, never used */
#define UJA113X_REG_IDENTIFICATION_0      0x7EU
#define UJA113X_REG_IDENTIFICATION_1      0x7FU

/* ---- 0x00 watchdog control ---- */
#define UJA113X_WMC_MASK          0xE0U
#define UJA113X_WMC_SHIFT         5U
#define UJA113X_WMC_AUTONOMOUS    1U
#define UJA113X_WMC_TIMEOUT       2U
#define UJA113X_WMC_WINDOW        4U
#define UJA113X_NWP_MASK          0x0FU
#define UJA113X_NWP_8MS           8U
#define UJA113X_NWP_16MS          1U
#define UJA113X_NWP_32MS          2U
#define UJA113X_NWP_64MS          11U
#define UJA113X_NWP_128MS         4U
#define UJA113X_NWP_256MS         13U
#define UJA113X_NWP_1024MS        14U
#define UJA113X_NWP_4096MS        7U

/* ---- 0x01 mode control ---- */
#define UJA113X_MC_MASK           0x07U
#define UJA113X_MC_SLEEP          1U
#define UJA113X_MC_STANDBY        4U
#define UJA113X_MC_NORMAL         7U

/* ---- 0x02 fail-safe control ---- */
#define UJA113X_ENSC_MASK         0xC0U /* EN shut-down response to a battery monitor event */
#define UJA113X_ENSC_SHIFT        6U
#define UJA113X_ENDC_MASK         0x20U /* 0 = push-pull, 1 = open drain (external pull-up to V1 needed) */
#define UJA113X_ENDC_SHIFT        5U
#define UJA113X_ENC_MASK          0x18U
#define UJA113X_ENC_SHIFT         3U
#define UJA113X_ENC_LOW           0U /* EN permanently low */
#define UJA113X_ENC_HIGH_NORMAL_RESET_STANDBY 1U
#define UJA113X_ENC_HIGH_NORMAL   2U /* EN high in Normal mode only: low again on every reset */
#define UJA113X_ENC_TIMER2        3U
#define UJA113X_LHC_MASK          0x04U /* limp home active (LIMP pin low) */
#define UJA113X_RCC_MASK          0x03U /* reset counter, overflow beyond 3 -> forced sleep preparation */

/* ---- shut-down response to a battery monitor event, shared coding of ENSC, V2SC, CSC and IOnSC ---- */
#define UJA113X_SHUTDOWN_NONE     0U
#define UJA113X_SHUTDOWN_UV       1U
#define UJA113X_SHUTDOWN_OV       2U
#define UJA113X_SHUTDOWN_UV_OV    3U

/* ---- 0x03 main status ---- */
#define UJA113X_OTWS_MASK         0x40U
#define UJA113X_NMS_MASK          0x20U /* 1 = Normal mode NOT entered */
#define UJA113X_RSS_MASK          0x1FU

/* ---- 0x04 system interrupt enable ---- */
#define UJA113X_OTWIE_MASK        0x04U
#define UJA113X_SPIFIE_MASK       0x02U

/* ---- 0x05 watchdog status ---- */
#define UJA113X_FNMS_MASK         0x08U /* forced normal mode */
#define UJA113X_SDMS_MASK         0x04U /* software development mode */
#define UJA113X_WDS_MASK          0x03U /* 0 off, 1 first half, 2 second half */

/* ---- 0x10 regulator control ---- */
#define UJA113X_V2SC_MASK         0x30U /* V2 shut-down response to a battery monitor event */
#define UJA113X_V2SC_SHIFT        4U
#define UJA113X_V2C_MASK          0x0CU
#define UJA113X_V2C_SHIFT         2U
#define UJA113X_V2C_OFF           0U
#define UJA113X_V2C_ON_NORMAL     1U
#define UJA113X_V2C_ON_NORMAL_STANDBY_RESET 2U
#define UJA113X_V2C_ON_ALWAYS     3U
#define UJA113X_V1RTC_MASK        0x03U

/* ---- 0x11 battery monitor event trigger source ---- */
#define UJA113X_BMSC_MASK         0x01U /* 0 = pin BAT triggers, 1 = pin BATSENSE */

/* ---- 0x12 BMUTC / 0x13 BMOTC: threshold = code / 255 * 20 V (full scale of the monitor ADC) ----
 * 0x14 hysteresis: BMHOC (7:4) and BMHUC (3:0), release level = code * 4/255 * 20 V off the threshold.
 * 0x15..0x18: 10 bit conversion results, 8 MSB in the first register, 2 LSB plus the read-out flag
 * (BMBCS/BMSCS, set once the MSBs were fetched over SPI) in the second one. */
#define UJA113X_BM_FULL_SCALE_MV  20000UL
#define UJA113X_BM_THRESHOLD_STEPS 255U
#define UJA113X_BM_ADC_STEPS      1023U
#define UJA113X_BMHOC_MASK        0xF0U
#define UJA113X_BMHOC_SHIFT       4U
#define UJA113X_BMHUC_MASK        0x0FU
#define UJA113X_BM_ADC_LSB_MASK   0x03U
#define UJA113X_BM_ADC_READ_MASK  0x04U /* BMBCS / BMSCS */

/* ---- 0x1B supply status ---- */
#define UJA113X_BMOVS_MASK        0x20U
#define UJA113X_BMUVS_MASK        0x10U
#define UJA113X_SMPSS_MASK        0x08U
#define UJA113X_VEXTS_MASK        0x06U
#define UJA113X_VEXTS_SHIFT       1U
#define UJA113X_V1S_MASK          0x01U

/* ---- 0x1C supply interrupt enable / 0x62 supply interrupt status ---- */
#define UJA113X_SMPSSI_MASK       0x20U
#define UJA113X_BMOI_MASK         0x10U
#define UJA113X_BMUI_MASK         0x08U
#define UJA113X_VEXTOI_MASK       0x04U
#define UJA113X_VEXTUI_MASK       0x02U
#define UJA113X_V1UI_MASK         0x01U

/* ---- 0x20 CAN control ---- */
#define UJA113X_CFDC_MASK         0x40U /* /FD only */
#define UJA113X_PNCOK_MASK        0x20U /* /FD only */
#define UJA113X_CPNC_MASK         0x10U /* /FD only */
#define UJA113X_CSC_MASK          0x0CU
#define UJA113X_CMC_MASK          0x03U
#define UJA113X_CMC_OFFLINE       0U
#define UJA113X_CMC_ACTIVE        1U /* active in Normal mode, VCAN undervoltage detection on */
#define UJA113X_CMC_ACTIVE_NO_UV  2U /* active in Normal mode, VCAN undervoltage ignored */
#define UJA113X_CMC_LISTEN_ONLY   3U

/* ---- 0x26 data rate (/FD only), CDR: bit rate of the wake-up frame ---- */
#define UJA113X_CDR_MASK          0x07U
#define UJA113X_CDR_50K           0U
#define UJA113X_CDR_100K          1U
#define UJA113X_CDR_125K          2U
#define UJA113X_CDR_250K          3U
#define UJA113X_CDR_500K          5U
#define UJA113X_CDR_1000K         7U

/* ---- 0x27..0x2A ID, 0x2B..0x2E ID mask (/FD only) ----
 * 29 bit field over four registers, LSB first; the top register has 5 bits. A standard (11 bit) identifier
 * occupies the upper 11 bits of the field (ID28..ID18). Mask bit = 1 means "don't care". */
#define UJA113X_PN_ID_FIELD_MASK  0x1FFFFFFFUL
#define UJA113X_PN_ID_STD_SHIFT   18U
#define UJA113X_PN_ID_TOP_MASK    0x1FU

/* ---- 0x2F frame control (/FD only) ---- */
#define UJA113X_IDE_MASK          0x80U /* 1 = extended frame format */
#define UJA113X_PNDM_MASK         0x40U /* 1 = DLC and data field are evaluated */
#define UJA113X_PN_DLC_MASK       0x0FU

/* ---- 0x22 transceiver status ---- */
#define UJA113X_CTS_MASK          0x80U /* 1 = CAN transmitter active */
#define UJA113X_CPNERR_MASK       0x40U /* /FD only */
#define UJA113X_CPNS_MASK         0x20U /* /FD only */
#define UJA113X_COSCS_MASK        0x10U /* /FD only */
#define UJA113X_CBSS_MASK         0x08U /* CAN bus silence */
#define UJA113X_VLINS_MASK        0x04U
#define UJA113X_VCS_MASK          0x02U /* VCAN undervoltage */
#define UJA113X_CFS_MASK          0x01U /* TXD dominant clamping / CAN failure */

/* ---- 0x23 transceiver interrupt enable / 0x63 transceiver interrupt status ---- */
#define UJA113X_PNFDEI_MASK       0x20U /* status only, /FD only */
#define UJA113X_CBSI_MASK         0x10U
#define UJA113X_LWI2_MASK         0x08U
#define UJA113X_LWI1_MASK         0x04U
#define UJA113X_CFI_MASK          0x02U
#define UJA113X_CWI_MASK          0x01U

/* ---- 0x30..0x33 / 0x40..0x43 HVIOn control ---- */
#define UJA113X_IOSC_MASK         0xC0U /* shut-down response to a battery monitor event */
#define UJA113X_IOSC_SHIFT        6U
#define UJA113X_IOAC_MASK         0x38U /* activation control */
#define UJA113X_IOAC_SHIFT        3U
#define UJA113X_IOAC_OFF          0U
#define UJA113X_IOAC_ON           1U
#define UJA113X_IOAC_TIMER1       2U
#define UJA113X_IOAC_TIMER2       3U
#define UJA113X_IOAC_TIMER3       4U
#define UJA113X_IOAC_TIMER4       5U
#define UJA113X_IOAC_DIRECT_INV   6U /* bank 0 only: driven by HVIO(n+4), inverted */
#define UJA113X_IOAC_DIRECT       7U /* bank 0 only: driven by HVIO(n+4) */
#define UJA113X_IOCC_MASK         0x07U /* configuration control: function and slope control */
#define UJA113X_IOCC_OFF          0U
#define UJA113X_IOCC_HS_SLOPE     1U
#define UJA113X_IOCC_LS_SLOPE     2U
#define UJA113X_IOCC_WAKE_INPUT   3U
#define UJA113X_IOCC_HS_WAKE_SLOPE 4U
#define UJA113X_IOCC_HS           5U
#define UJA113X_IOCC_LS           6U
#define UJA113X_IOCC_HS_WAKE      7U

/* ---- 0x34 / 0x44 bank wake-up threshold ---- */
#define UJA113X_WTC_MASK          0x01U /* 0 = ratiometric to BATHSx, 1 = absolute (2.4 V .. 4.1 V) */

/* ---- 0x36 / 0x46 driver status, two bits per HVIO ---- */
#define UJA113X_IODS_OK           0U
#define UJA113X_IODS_OPEN_LOAD    1U
#define UJA113X_IODS_SHORT_CIRCUIT 2U
#define UJA113X_IODS_OFF          3U

/* ---- 0x37 / 0x47 wake interrupt enable, 0x64 / 0x66 status: two bits per HVIO ----
 * bit 2*i = rising edge (IOnREIE), bit 2*i + 1 = falling edge (IOnFEIE) of HVIO(i + 1) in that bank. */
#define UJA113X_HVIO_RISING_SHIFT  0U
#define UJA113X_HVIO_FALLING_SHIFT 1U

/* ---- 0x38 / 0x48 fail interrupt enable, 0x65 / 0x67 status: two bits per HVIO ----
 * bit 2*i = open load (IOnOLIE), bit 2*i + 1 = short circuit (IOnSCIE). */
#define UJA113X_HVIO_OPEN_LOAD_SHIFT     0U
#define UJA113X_HVIO_SHORT_CIRCUIT_SHIFT 1U

/* ---- 0x39 / 0x49 short-circuit, 0x3A / 0x4A open-load detection threshold: two bits per HVIO ---- */
#define UJA113X_HVIO_PER_BANK      4U
#define UJA113X_HVIO_BANKS         2U
#define UJA113X_HVIO_COUNT         8U
#define UJA113X_HVIO_THRESHOLD_MASK 0x03U

/* ---- 0x73 start-up control (MTPNV): limp home function of HVIO2..HVIO4 ---- */
#define UJA113X_IO2SFC_MASK       0x01U
#define UJA113X_IO3SFC_MASK       0x02U
#define UJA113X_IO4SFC_MASK       0x04U

/* ---- 0x60 global interrupt status ---- */
#define UJA113X_B1FIS_MASK        0x40U
#define UJA113X_B1WIS_MASK        0x20U
#define UJA113X_B0FIS_MASK        0x10U
#define UJA113X_B0WIS_MASK        0x08U
#define UJA113X_TRXIS_MASK        0x04U
#define UJA113X_SUPIS_MASK        0x02U
#define UJA113X_SYSIS_MASK        0x01U

/* ---- 0x61 system interrupt status ---- */
#define UJA113X_OVSDI_MASK        0x20U
#define UJA113X_POSI_MASK         0x10U
#define UJA113X_OTWI_MASK         0x04U
#define UJA113X_SPIFI_MASK        0x02U
#define UJA113X_WDI_MASK          0x01U

/* ---- 0x68..0x6F data mask (/FD only) ----
 * A wake-up frame with DLC n (1..8) is compared against the last n data mask registers: data byte 0 against
 * data mask 8 - n, the last data byte against data mask 7 (0x6F). */
#define UJA113X_PN_DATA_MASK_COUNT 8U

/* ---- 0x7E identification ----
 * All /FD variants (ID 0x40, 0x41, 0x50, 0x51, 0x60, 0x61, 0x70, 0x71) have bit 6 set, the variants without
 * partial networking (0x00, 0x01, 0x10, 0x11) do not. */
#define UJA113X_ID_FD_MASK        0x40U
/* Number of HVIO banks from the identification code (data sheet table 78): the HW variants without /FD
 * (0x00, 0x01, 0x10, 0x11) have two banks of four HVIOs, the /FD/4 variants (bit 6, bit 5 clear) only bank 0,
 * and the /FD/0 variants (bit 6 and bit 5 set) none at all. */
#define UJA113X_ID_NO_HVIO_MASK   0x20U

/* ---- 0x70 MTPNV status ---- */
#define UJA113X_WRCNTS_MASK       0xFCU
#define UJA113X_WRCNTS_SHIFT      2U
#define UJA113X_ECCS_MASK         0x02U
#define UJA113X_NVMPS_MASK        0x01U /* 1 = factory preset, ready for programming */

/* ---- 0x74 SBC configuration control (MTPNV) ---- */
#define UJA113X_V1RTSUC_MASK      0x30U
#define UJA113X_FNMC_MASK         0x08U
#define UJA113X_SDMC_MASK         0x04U
#define UJA113X_VEXTAC_MASK       0x02U
#define UJA113X_SLPC_MASK         0x01U /* 1 = Sleep mode not supported */

/* ---- reset source (0x03 RSS) ---- */
#define UJA113X_RSS_EXIT_OFF           0U
#define UJA113X_RSS_CAN_WAKEUP         1U
#define UJA113X_RSS_WD_OVERFLOW_SLEEP  12U
#define UJA113X_RSS_DIAGNOSTIC_WAKEUP  13U
#define UJA113X_RSS_WD_TRIGGER_EARLY   14U
#define UJA113X_RSS_WD_OVERFLOW        15U
#define UJA113X_RSS_ILLEGAL_WMC_CHANGE 16U
#define UJA113X_RSS_RSTN_PULLED_DOWN   17U
#define UJA113X_RSS_OVERLOAD_EXIT      18U
#define UJA113X_RSS_V1_UNDERVOLTAGE    19U
#define UJA113X_RSS_ILLEGAL_SLEEP      20U
#define UJA113X_RSS_WAKEUP_FROM_FSP    21U

/* ---- SPI frame ---- */
#define UJA113X_SPI_RO_BIT        0x0100U
#define UJA113X_SPI_ADDRESS_SHIFT 9U

/* ---- SPI protocol (data sheet 7.16 p.90, Table 91 p.115; the same for every UJA113x and every board) ---- */
#define UJA113X_SPI_FRAME_BITS    16U  /* address byte + data byte; 24/32-bit auto-increment frames are not used */
#define UJA113X_SPI_CPOL          0U   /* SCK idles LOW */
#define UJA113X_SPI_CPHA          1U   /* SBC shifts on the rising, samples on the falling SCK edge */
#define UJA113X_SPI_MAX_SCK_HZ    4000000UL /* tcy(clk) >= 250 ns */
