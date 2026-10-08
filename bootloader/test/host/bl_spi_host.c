/* Host build of the bootloader SPI path (BL-05), driven from Python
 * (bootloader/test/host/test_bl_platform_host.py):
 *
 *   sbcUja113x hook (bootloader/devices, family neutral)
 *     -> bl_spi.h
 *       -> family SPI HAL bootloader/platforms/s32k1xx/bl_spi_s32k1xx.c
 *         -> LPSPI/PCC/PORT register model (bootloader/test/host/model, vendor register layout)
 *           -> LPSPI and UJA113x slave simulation in this file
 *
 * The peripheral makes progress inside hal_millis(): the driver polls the status register and reads the clock in
 * every poll loop, so that is the point where the model may advance - like the hardware between two polls. Every
 * frame is logged, so the test sees what the hook sent, and the model refuses frames whose format does not match
 * the UJA113x (frame size, clock phase, chip select) instead of quietly answering. */

#include "bootloader/bl_board.h" /* board part first: the SOSC header below derives from its BL_OSC_HZ */

#include "bl_hal.h"
#include "bl_main.h"
#include "bl_sosc_s32k1xx.h"
#include "bl_spi.h"
#include "mcu/mcu.h"
#include "sbc/uja113xRegisters.h"
#include "sbc_uja113x.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

#define MODEL_SPI_INSTANCE 1U /* BL_SBC_SPI of the test board part (LPSPI1) */
#define TDR_IDLE           0xFFFFFFFFUL
#define TX_LOG_SIZE        64U
#define SLAVE_REG_COUNT    128U
#define SLAVE_RX_MARKER    0x5A00U /* high byte of every answer: the hook must use the low byte only */

/* Register model (mcu/mcu.h of the test model redirects the instance pointers here) */
LPSPI_Type bl_model_lpspi[BL_MODEL_LPSPI_COUNT];
PCC_Type bl_model_pcc;
PORT_Type bl_model_port[BL_MODEL_PORT_COUNT];

static uint32_t nowMs;
static uint32_t msPerPoll = 1U;
static uint32_t txLog[TX_LOG_SIZE];
static uint32_t txCount;
static uint32_t frameErrors;
static uint32_t stuckAfter = 0xFFFFFFFFUL; /* number of frames the model still answers */
static uint32_t notReadyFrames;             /* BL-26: frames the slave is not ready for (after power-on) */
static uint8_t notReadyAnswer;              /* what it shifts out meanwhile (board 2026-09-21: 0x00) */
static uint8_t slaveReg[SLAVE_REG_COUNT];

/* BL-28: watchdog of the simulated UJA113x, switched on by bl_test_wd_model() (the tests before BL-28 run without
 * it). Behaviour after data sheet 7.2 (Table 8, 7.3.3), as in libs/bsw/sbcUja113x/test/host (Uja113xHostTest.cpp):
 * Forced Normal mode: off; Normal mode: timeout (window: window) unless autonomous in software development mode;
 * Standby: timeout unless autonomous (INTN1 high by default; BL-42: with a pending interrupt, INTN1 low, autonomous
 * runs as timeout too without software development mode). A change of WMC/NWP in Normal mode is a system reset, a
 * write to register 0x00 is a trigger. The model counts what would reset the MCU instead of resetting, so a test
 * sees every event. */
static bool wdModel;
static bool wdLocked; /* register 0x00 ignores writes: the SBC does not take the setting */
static bool wdIntnLow; /* BL-42: interrupt pending (INTN1 low) - autonomous runs in Standby with SDMS 0 */
static uint32_t wdKickMs;
static uint32_t wdOverflows;
static uint32_t wdIllegalChanges;
static uint32_t wdWindowViolations;
static uint32_t wdMaxGapMs; /* longest time without trigger while the watchdog ran */

static uint32_t wd_period_ms(uint8_t control)
{
    switch (control & UJA113X_NWP_MASK)
    {
        case UJA113X_NWP_8MS: return 8U;
        case UJA113X_NWP_16MS: return 16U;
        case UJA113X_NWP_32MS: return 32U;
        case UJA113X_NWP_64MS: return 64U;
        case UJA113X_NWP_128MS: return 128U;
        case UJA113X_NWP_256MS: return 256U;
        case UJA113X_NWP_1024MS: return 1024U;
        case UJA113X_NWP_4096MS: return 4096U;
        default: return 128U;
    }
}

static uint32_t wd_wmc(uint8_t control) { return ((uint32_t)control & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT; }

static bool wd_normal(void) { return ((uint32_t)slaveReg[UJA113X_REG_MODE_CONTROL] & UJA113X_MC_MASK) == UJA113X_MC_NORMAL; }

static bool wd_running(void)
{
    uint8_t const status  = slaveReg[UJA113X_REG_WATCHDOG_STATUS];
    bool const autonomous = wd_wmc(slaveReg[UJA113X_REG_WATCHDOG_CONTROL]) == UJA113X_WMC_AUTONOMOUS;
    if ((status & UJA113X_FNMS_MASK) != 0U)
    {
        return false;
    }
    if (wd_normal())
    {
        return !autonomous || ((status & UJA113X_SDMS_MASK) == 0U);
    }
    return !autonomous || (wdIntnLow && ((status & UJA113X_SDMS_MASK) == 0U));
}

static void wd_note_gap(uint32_t now)
{
    uint32_t const gap = now - wdKickMs;
    if (wd_running() && (gap > wdMaxGapMs))
    {
        wdMaxGapMs = gap;
    }
}

/* A write the model handles itself; false: store it like any register */
static bool wd_write(uint8_t address, uint8_t data, uint32_t now)
{
    if (!wdModel)
    {
        return false;
    }
    if (address == UJA113X_REG_MODE_CONTROL)
    {
        if ((((uint32_t)data & UJA113X_MC_MASK) == UJA113X_MC_NORMAL) && !wd_normal() && !wd_running())
        {
            wdKickMs = now; /* the watchdog starts with Normal mode; one already running keeps its phase */
        }
        return false;
    }
    if (address != UJA113X_REG_WATCHDOG_CONTROL)
    {
        return false;
    }
    uint8_t const old = slaveReg[UJA113X_REG_WATCHDOG_CONTROL];
    uint8_t const cfg = UJA113X_WMC_MASK | UJA113X_NWP_MASK;
    wd_note_gap(now);
    if (wd_normal() && ((old & cfg) != (data & cfg)))
    {
        wdIllegalChanges++; /* data sheet 7.2, 7.3.3: system reset */
        return true;
    }
    if (wd_normal() && (wd_wmc(old) == UJA113X_WMC_WINDOW) && wd_running()
        && (((now - wdKickMs) * 100U) < (wd_period_ms(old) * 55U)))
    {
        wdWindowViolations++; /* trigger in the closed window: system reset */
    }
    wdKickMs = now;
    if (!wdLocked)
    {
        slaveReg[UJA113X_REG_WATCHDOG_CONTROL] = data;
    }
    return true;
}

static void wd_tick(uint32_t now)
{
    if (wdModel && wd_running() && ((now - wdKickMs) > wd_period_ms(slaveReg[UJA113X_REG_WATCHDOG_CONTROL])))
    {
        wd_note_gap(now);
        wdOverflows++; /* overflow: system reset; counted once per period */
        wdKickMs = now;
    }
}

/* The model plays the peripheral, so it writes registers that are read-only for the driver (__I in the vendor
 * header: RDR). Only the model does this - the code under test keeps the vendor access rights. */
static void model_set_rdr(LPSPI_Type* p, uint32_t value)
{
    uint32_t volatile* const rdr = (uint32_t volatile*)(uintptr_t)(void const*)&p->RDR;
    *rdr                         = value;
}

static void spi_model_poll(void)
{
    LPSPI_Type* const p = &bl_model_lpspi[MODEL_SPI_INSTANCE];
    if ((p->CR & LPSPI_CR_MEN_MASK) == 0U)
    {
        p->SR = 0U; /* module disabled: no status flag ever appears */
        return;
    }
    if (txCount >= stuckAfter)
    {
        return; /* injected fault: the port stops making progress */
    }
    if (p->TDR == TDR_IDLE)
    {
        p->SR |= LPSPI_SR_TDF_MASK; /* transmit FIFO has room */
        return;
    }

    uint32_t const frame = p->TDR;
    p->TDR               = TDR_IDLE;

    /* Frame format of the UJA113x series: 16 bit, clock idle low, sampled on the second edge, chip select of the
     * board part. A driver that configures something else gets no answer. */
    if ((((p->TCR & LPSPI_TCR_FRAMESZ_MASK) >> LPSPI_TCR_FRAMESZ_SHIFT) != 15U)
        || ((p->TCR & LPSPI_TCR_CPHA_MASK) == 0U) || ((p->TCR & LPSPI_TCR_CPOL_MASK) != 0U)
        || (((p->TCR & LPSPI_TCR_PCS_MASK) >> LPSPI_TCR_PCS_SHIFT) != BL_SBC_SPI_PCS))
    {
        frameErrors++;
        return;
    }

    if (txCount < TX_LOG_SIZE)
    {
        txLog[txCount] = frame;
    }
    bool const ready = txCount >= notReadyFrames;
    txCount++;

    uint8_t const address = (uint8_t)((frame >> UJA113X_SPI_ADDRESS_SHIFT) & 0x7FU);
    bool const readOnly   = (frame & UJA113X_SPI_RO_BIT) != 0U;
    uint8_t const answer  = ready ? slaveReg[address] : notReadyAnswer;
    if (!readOnly && ready && !wd_write(address, (uint8_t)frame, nowMs))
    {
        slaveReg[address] = (uint8_t)frame; /* a slave that is not ready takes no command */
    }
    model_set_rdr(p, SLAVE_RX_MARKER | answer);
    p->SR |= LPSPI_SR_RDF_MASK | LPSPI_SR_TCF_MASK | LPSPI_SR_TDF_MASK;
}

/* Clock of the bootloader HAL: one millisecond per poll, and the model advances (see the file header). */
uint32_t hal_millis(void)
{
    spi_model_poll();
    uint32_t const now = nowMs;
    nowMs += msPerPoll;
    wd_tick(nowMs);
    return now;
}

EXPORT void bl_test_reset(void)
{
    (void)memset(bl_model_lpspi, 0, sizeof(bl_model_lpspi));
    (void)memset(&bl_model_pcc, 0, sizeof(bl_model_pcc));
    (void)memset(bl_model_port, 0, sizeof(bl_model_port));
    (void)memset(txLog, 0, sizeof(txLog));
    (void)memset(&bl_sbc_diag, 0, sizeof(bl_sbc_diag));
    for (uint32_t i = 0U; i < SLAVE_REG_COUNT; ++i)
    {
        slaveReg[i] = (uint8_t)(0x10U + i); /* every register a different value */
    }
    bl_model_lpspi[MODEL_SPI_INSTANCE].TDR = TDR_IDLE;
    nowMs                                  = 0U;
    msPerPoll                              = 1U;
    txCount                                = 0U;
    frameErrors                            = 0U;
    stuckAfter                             = 0xFFFFFFFFUL;
    notReadyFrames                         = 0U;
    notReadyAnswer                         = 0U;
    wdModel                                = false;
    wdLocked                               = false;
    wdIntnLow                              = false;
    wdKickMs                               = 0U;
    wdOverflows                            = 0U;
    wdIllegalChanges                       = 0U;
    wdWindowViolations                     = 0U;
    wdMaxGapMs                             = 0U;
#if defined(BL_SBC_WATCHDOG_SERVICE)
    (void)memset(&bl_sbc_watchdog, 0, sizeof(bl_sbc_watchdog));
#endif
}

/* BL-28: watchdog model on; `kickAgoMs`: time since the last trigger of whoever served it before (application) */
EXPORT void bl_test_wd_model(uint32_t kickAgoMs)
{
    wdModel  = true;
    nowMs   += kickAgoMs;
    wdKickMs = nowMs - kickAgoMs;
}

EXPORT void bl_test_wd_lock(uint32_t locked) { wdLocked = locked != 0U; }

/* BL-42: INTN1 low (an interrupt pending) or high; set before the scenario starts */
EXPORT void bl_test_wd_intn_low(uint32_t low) { wdIntnLow = low != 0U; }

/* BL-42: a write of someone else (the application after the jump) through the same slave logic as the hook's frames */
EXPORT void bl_test_slave_write(uint32_t reg, uint32_t value)
{
    uint8_t const address = (uint8_t)(reg & (SLAVE_REG_COUNT - 1U));
    if (!wd_write(address, (uint8_t)value, nowMs))
    {
        slaveReg[address] = (uint8_t)value;
    }
}

/* 0 overflows, 1 illegal changes in Normal mode, 2 triggers in a closed window, 3 longest gap in ms */
EXPORT uint32_t bl_test_wd_events(uint32_t which)
{
    uint32_t const values[4] = {wdOverflows, wdIllegalChanges, wdWindowViolations, wdMaxGapMs};
    return (which < 4U) ? values[which] : 0xFFFFFFFFUL;
}

/* Time passes without anyone serving the SBC (e.g. the application starting up after the jump) */
EXPORT void bl_test_idle(uint32_t ms)
{
    for (uint32_t i = 0U; i < ms; ++i)
    {
        nowMs++;
        wd_tick(nowMs);
    }
}

#if defined(BL_SBC_WATCHDOG_SERVICE)
/* One poll of the bootloader as far as the SBC is concerned: bl_poll() calls the device service first. */
EXPORT void bl_test_service(uint32_t beforeJump) { bl_device_service(hal_millis(), beforeJump != 0U); }

EXPORT uint32_t bl_test_wd_diag(uint32_t which)
{
    sbc_watchdog_diag_t const* const w = &bl_sbc_watchdog;
    uint32_t const values[7] = {w->lastTriggerMs, w->triggers, w->errors, w->controlBefore,
                                w->controlAfter,  w->served,   w->reason};
    return (which < 7U) ? values[which] : 0xFFFFFFFFUL;
}
#endif

EXPORT void bl_test_set_ms_per_poll(uint32_t ms) { msPerPoll = ms; }

/* BL-26: the slave answers `answer` to the first `frames` frames and ignores their commands (0xFFFFFFFF: never
 * ready - SBC dead or missing, data line held low or high). */
EXPORT void bl_test_set_not_ready(uint32_t frames, uint32_t answer)
{
    notReadyFrames = frames;
    notReadyAnswer = (uint8_t)answer;
}

EXPORT void bl_test_set_stuck_after(uint32_t frames) { stuckAfter = frames; }

EXPORT void bl_test_set_slave_reg(uint32_t reg, uint32_t value)
{
    slaveReg[reg & (SLAVE_REG_COUNT - 1U)] = (uint8_t)value;
}

EXPORT uint32_t bl_test_slave_reg(uint32_t reg) { return slaveReg[reg & (SLAVE_REG_COUNT - 1U)]; }

EXPORT uint32_t bl_test_frame_errors(void) { return frameErrors; }

EXPORT uint32_t bl_test_tx_count(void) { return txCount; }

EXPORT uint32_t bl_test_tx_word(uint32_t index) { return (index < TX_LOG_SIZE) ? txLog[index] : 0xFFFFFFFFUL; }

EXPORT uint32_t bl_test_millis(void) { return nowMs; }

/* Registers of the model by byte offset, so the test names the same offsets as the reference manual. */
EXPORT uint32_t bl_test_lpspi_reg(uint32_t instance, uint32_t offset)
{
    if ((instance >= BL_MODEL_LPSPI_COUNT) || (offset > (sizeof(LPSPI_Type) - 4U)) || ((offset % 4U) != 0U))
    {
        return 0xFFFFFFFFUL;
    }
    uint8_t const* const base = (uint8_t const*)&bl_model_lpspi[instance];
    uint32_t value;
    (void)memcpy(&value, &base[offset], sizeof(value));
    return value;
}

EXPORT uint32_t bl_test_pcc(uint32_t index)
{
    return (index < PCC_PCCn_COUNT) ? bl_model_pcc.PCCn[index] : 0xFFFFFFFFUL;
}

EXPORT uint32_t bl_test_pcr(uint32_t port, uint32_t pin)
{
    return ((port < BL_MODEL_PORT_COUNT) && (pin < PORT_PCR_COUNT)) ? bl_model_port[port].PCR[pin] : 0xFFFFFFFFUL;
}

EXPORT void bl_test_spi_init(uint32_t bits, uint32_t cpol, uint32_t cpha)
{
    bl_spi_frame_t const frame = {(uint8_t)bits, cpol != 0U, cpha != 0U};
    bl_spi_init(&frame);
}

EXPORT uint32_t bl_test_spi_transfer(uint32_t tx, uint32_t* rx) { return bl_spi_transfer(tx, rx) ? 1U : 0U; }

EXPORT void bl_test_sbc_init(uint32_t configure) { sbc_init(configure != 0U); }

/* Diagnosis snapshot of the hook, field by field (all fields are bytes, order of sbc_diag_t) */
EXPORT uint32_t bl_test_diag(uint32_t index)
{
    if (index >= sizeof(sbc_diag_t))
    {
        return 0xFFFFFFFFUL;
    }
    uint8_t const* const bytes = (uint8_t const*)&bl_sbc_diag;
    return bytes[index];
}

EXPORT uint32_t bl_test_watchdog_off(void) { return sbc_watchdog_off() ? 1U : 0U; }

/* Values the family HAL derives for SCG_SOSCCFG from the crystal of the board part (BL_OSC_HZ, ARCH-09 B7).
 * The test builds this file with several crystal frequencies. */
EXPORT uint32_t bl_test_sosc_range(void) { return BL_SOSC_RANGE; }

EXPORT uint32_t bl_test_sosccfg(void)
{
    return SCG_SOSCCFG_RANGE(BL_SOSC_RANGE) | SCG_SOSCCFG_EREFS(BL_SOSC_EREFS) | SCG_SOSCCFG_HGO(BL_SOSC_HGO);
}

EXPORT uint32_t bl_test_osc_hz(void) { return BL_OSC_HZ; }
