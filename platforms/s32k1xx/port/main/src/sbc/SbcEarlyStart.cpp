#include "systems/SbcSystem.h"

#include "SbcSpiPins.h"
#include "bsp/sbc/sbcConfiguration.h"
#include "mcu/mcu.h"
#include "reset/softwareSystemReset.h"

#include <bl_config.h>

/* SYS-07. Runs from main() before MemoryProtection, the watchdog fast test and the BSP.
 *
 * Why the later initialization is not affected:
 * - Clocks: boardInit() (configurPll) has already enabled the PORTs and the LPSPI of the SBC bus on the
 *   clock SPI_CONFIG expects (both generated from the project). Nothing is changed here.
 * - Pins: the four pins of the SBC bus get their default configuration from board.yaml (Io, SbcSpiPins.h),
 *   which is what the bootloader and later StaticBsp::hwInit set as well. No other pin is touched.
 * - LPSPI: SbcSystem::init() calls Lpspi::init() again, which starts with a module software reset.
 * - SBC: only RCC in register 0x02 changes. SbcSystem's startup reads the register afterwards
 *   ("RCC was") and writes its start value anyway.
 * - Only after a watchdog test reset. Power-on, pin, software and real watchdog resets skip it. */

namespace
{
::sbc::ResetCounterCompensation compensation{
    ::sbc::ResetCounterCompensation::Result::NOT_TEST_RESET, 0U, 0U};
uint32_t resetStatus      = 0U;
uint8_t watchdogTestField = 0U;

/* Failed-start counter for the board test in the application's no-init RAM (NoInit region at the end
 * of SRAM_U, not cleared by startUp.S; the bootloader only uses SRAM_L and the boot request word
 * pair right above). Content and ECC are undefined after power-on: the words are only read if the
 * sticky power-on/LVD reset flags were cleared since then, which armFailStarts() does after writing. */
uint32_t volatile* const FAIL_START_RECORD = reinterpret_cast<uint32_t volatile*>(BL_BOOT_REQUEST_ADDR - 8UL);
uint32_t const FAIL_START_MAGIC            = 0x5B5C0700UL; // count in the low byte
uint32_t const FAIL_START_MAX              = 3UL;
uint32_t const POWER_ON_FLAGS              = RCM_SSRS_SPOR_MASK | RCM_SSRS_SLVD_MASK;

bool noInitValidSincePowerOn() { return (RCM->SSRS & POWER_ON_FLAGS) == 0U; }

void writeFailStarts(uint32_t const count)
{
    uint32_t const word  = FAIL_START_MAGIC | count;
    FAIL_START_RECORD[0] = word;
    FAIL_START_RECORD[1] = ~word;
}
} // namespace

namespace systems
{

void SbcEarlyStart::compensateResetCounter()
{
    resetStatus       = RCM->SRS;
    watchdogTestField = static_cast<uint8_t>((WDOG->CS & WDOG_CS_TST_MASK) >> WDOG_CS_TST_SHIFT);
    bool const watchdogReset = (resetStatus & RCM_SRS_WDOG_MASK) != 0U;
    if (!::sbc::isMcuWatchdogTestReset(watchdogReset, watchdogTestField))
    {
        return;
    }

    muxSbcSpiPins();

    ::bsp::spi::Lpspi spi(::bsp::sbc::SPI_CONFIG);
    spi.init();
    ::sbc::Uja113x sbc(spi, ::bsp::sbc::SBC_CONFIG);
    compensation = ::sbc::compensateWatchdogTestReset(sbc, watchdogReset, watchdogTestField);
}

uint8_t SbcEarlyStart::getArmedFailStarts()
{
    if (!noInitValidSincePowerOn())
    {
        return 0U;
    }
    uint32_t const word = FAIL_START_RECORD[0];
    if ((FAIL_START_RECORD[1] != ~word) || ((word & ~0xFFUL) != FAIL_START_MAGIC)
        || ((word & 0xFFUL) > FAIL_START_MAX))
    {
        return 0U;
    }
    return static_cast<uint8_t>(word & 0xFFUL);
}

void SbcEarlyStart::failStartIfArmed()
{
    uint8_t const count = getArmedFailStarts();
    if (count == 0U)
    {
        return;
    }
    writeFailStarts(count - 1UL);
    softwareSystemReset();
}

bool SbcEarlyStart::armFailStarts(uint8_t const count)
{
    if (count > FAIL_START_MAX)
    {
        return false;
    }
    writeFailStarts(count);
    RCM->SSRS = POWER_ON_FLAGS; // write 1 to clear
    return getArmedFailStarts() == count;
}

::sbc::ResetCounterCompensation const& SbcEarlyStart::getCompensation() { return compensation; }

uint32_t SbcEarlyStart::getResetStatus() { return resetStatus; }

uint8_t SbcEarlyStart::getWatchdogTestField() { return watchdogTestField; }

} // namespace systems
