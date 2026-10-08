/* SYS-95 host test stub of safeLifecycle/ProgressSupervision.h for the tests of the safety cycle (SafetyCycleHostTest,
 * SafetyGateHostTest, CycleTimerOffHostTest): the progress checks record nothing in the trace, they only count their
 * calls. The real checks have their own host test (ProgressSupervisionHostTest.cpp). */
#pragma once

#include <cstdint>

namespace safety
{
namespace progress
{
constexpr uint32_t ROM_CHECK_CONTEXT_NOT_CALLED = 7U;

namespace test
{
inline int calls = 0;
} // namespace test

inline void cyclic() { ++test::calls; }
} // namespace progress
} // namespace safety
