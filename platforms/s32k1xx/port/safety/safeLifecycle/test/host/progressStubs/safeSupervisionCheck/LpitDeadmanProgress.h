/* SYS-95 host test stub of safeSupervisionCheck/LpitDeadmanProgress.h for ProgressSupervisionHostTest: the verdict is
 * scripted and the budget the caller passes is recorded (the real check has its own host test,
 * safeSupervisionCheck/test/host/LpitDeadmanProgressHostTest.cpp). The configuration comes from the stub
 * LpitDeadmanCheck.h of the safety cycle tests. */
#pragma once

#include <safeSupervisionCheck/LpitDeadmanCheck.h>

#include <cstdint>

namespace safety
{
namespace supervision
{
enum class LpitCounterProgress : uint8_t
{
    MOVING      = 0U,
    STOPPED     = 1U,
    NOT_RUNNING = 2U,
};

constexpr uint32_t DEADMAN_FIELD_COUNTER_STOPPED = 0x100U;

constexpr uint32_t lpitProgressPolls(uint32_t const coreHz, uint32_t const lpitHz)
{
    return (lpitHz == 0U) ? 0U : ((2U * ((coreHz + lpitHz - 1U) / lpitHz)) + 2U);
}

namespace test
{
inline LpitCounterProgress progress = LpitCounterProgress::MOVING;
inline uint32_t polls               = 0U;
inline int progressChecks           = 0;
} // namespace test

inline LpitCounterProgress checkLpitDeadmanProgress(LpitDeadmanConfiguration const&, uint32_t const maxPolls)
{
    ++test::progressChecks;
    test::polls = maxPolls;
    return test::progress;
}
} // namespace supervision
} // namespace safety
