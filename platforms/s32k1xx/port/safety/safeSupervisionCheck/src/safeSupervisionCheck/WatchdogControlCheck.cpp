// SYS-41: configuration re-check of the S32K1 WDOG beyond the driver's own check (see the header).

#include "safeSupervisionCheck/WatchdogControlCheck.h"

#include <mcu/mcu.h>

namespace safety
{
namespace supervision
{

uint32_t checkWatchdogControl(bool const interruptActive)
{
    uint32_t const cs     = WDOG->CS;
    uint32_t deviations   = 0U;
    bool const intEnabled = (cs & WDOG_CS_INT_MASK) != 0U;
    if (intEnabled != interruptActive)
    {
        deviations |= WATCHDOG_FIELD_INT;
    }
    if ((cs & WDOG_CS_WIN_MASK) != 0U)
    {
        deviations |= WATCHDOG_FIELD_WIN;
    }
    if ((cs & WDOG_CS_PRES_MASK) != 0U)
    {
        deviations |= WATCHDOG_FIELD_PRES;
    }
    if ((cs & WDOG_CS_UPDATE_MASK) == 0U)
    {
        deviations |= WATCHDOG_FIELD_UPDATE;
    }
    return deviations;
}

} // namespace supervision
} // namespace safety
