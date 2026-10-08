/* SYS-41 host test stub of the OpenBSW S32K1 watchdog driver (safeBspMcuWatchdog, watchdog/Watchdog.h).
 *
 * enableWatchdog() writes the control word and TOVAL exactly as the driver does, and checkWatchdogConfiguration()
 * is the driver's expression (CS[EN], CS[CLK] and TOVAL only) - on the register model of stubs/mcu/mcu.h. A service
 * is recorded as 'W' and counted like the driver's service counter. */

#pragma once

#include <mcu/mcu.h>

#include <cstdint>

namespace test_trace
{
void add(char c);
}

namespace safety
{
namespace bsp
{
class Watchdog
{
public:
    static uint32_t const DEFAULT_CLOCK_SPEED = 128000U;

    static void enableWatchdog(uint32_t const timeout, bool const interruptActive = false,
                               uint32_t const clockSpeed = DEFAULT_CLOCK_SPEED)
    {
        WDOG->TOVAL = (clockSpeed / 1000U) * timeout;
        uint32_t cs = WDOG_CS_UPDATE_MASK | WDOG_CS_EN_MASK | (1U << WDOG_CS_CLK_SHIFT) | WDOG_CS_CMD32EN_MASK
                      | WDOG_CS_STOP_MASK | WDOG_CS_WAIT_MASK;
        if (interruptActive)
        {
            cs |= WDOG_CS_INT_MASK;
        }
        cs |= (WDOG->CS & WDOG_CS_TST_MASK);
        WDOG->CS = cs;
    }

    static void serviceWatchdog()
    {
        ++serviceCounter;
        ::test_trace::add('W');
    }

    static bool checkWatchdogConfiguration(uint32_t const timeout, uint32_t const clockSpeed = DEFAULT_CLOCK_SPEED)
    {
        uint32_t const MCU_WD_TIMEOUT = (clockSpeed / 1000U) * timeout;
        uint32_t const WD_CONFIG_MASK = WDOG_CS_EN_MASK | WDOG_CS_CLK_MASK;
        bool const isWdConfigValid    = ((WDOG->CS & WD_CONFIG_MASK) == (WDOG_CS_EN_MASK | (1U << WDOG_CS_CLK_SHIFT)));
        bool const isWdTimeoutValid   = ((WDOG->TOVAL & 0x0000FFFFU) == MCU_WD_TIMEOUT);
        return (isWdConfigValid && isWdTimeoutValid);
    }

    static uint32_t getWatchdogServiceCounter() { return serviceCounter; }

    static inline uint32_t serviceCounter = 0U;
};
} // namespace bsp
} // namespace safety
