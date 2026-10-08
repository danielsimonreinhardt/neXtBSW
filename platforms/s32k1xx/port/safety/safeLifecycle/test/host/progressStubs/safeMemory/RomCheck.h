/* SYS-95 host test stub of safeMemory/RomCheck.h for ProgressSupervisionHostTest: result, period and the number of
 * passes are scripted (the real RomCheck has its own host test, safeMemory/test/host/RomCheckHostTest.cpp). */
#pragma once

#include <cstdint>

namespace safety
{
class RomCheck
{
public:
    enum class Result : uint8_t
    {
        ROMCHECK_REQUESTED       = 0,
        ROMCHECK_OK              = 1,
        ROMCHECK_FAILED          = 2,
        ROMCHECK_INVALID_LIST    = 3,
        ROMCHECK_UNIT_FAILED     = 4,
        ROMCHECK_OVERDUE         = 5,
        ROMCHECK_OWN_AREA_FAILED = 6
    };

    struct Statistics
    {
        uint32_t passes;
        uint32_t discarded;
        uint32_t postponements;
    };

    static inline Result scriptedResult     = Result::ROMCHECK_REQUESTED;
    static inline uint32_t scriptedPeriodMs = 0U;
    static inline uint32_t scriptedPasses   = 0U;

    static void init() {}

    static uint32_t periodMs() { return scriptedPeriodMs; }

    static Result result() { return scriptedResult; }

    static Statistics statistics() { return Statistics{scriptedPasses, 0U, 0U}; }
};
} // namespace safety
