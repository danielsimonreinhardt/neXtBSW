/* SYS-40 host test stub of safeSupervisionCheck/LptmrCycleTimer.h: the real header (its constants are what the
 * bounds of SafetyManager.cpp are derived from), with the three functions defined by the test instead of the
 * register access - the counter value and the result of the configuration check are scripted, the check records
 * 'p' (SafetyCycleHostTest.cpp). The real timer has its own host test
 * (safeSupervisionCheck/test/host/LptmrCycleTimerHostTest.cpp). */
#pragma once

#include "../../../../../safeSupervisionCheck/include/safeSupervisionCheck/LptmrCycleTimer.h" // NOLINT

namespace safety
{
namespace supervision
{
namespace test
{
extern uint32_t counter;     // what readCycleTimer() returns
extern uint32_t timerFields; // what checkCycleTimer() reports
extern int timerReads;       // calls of readCycleTimer()
extern int timerStarts;      // calls of startCycleTimer()
} // namespace test
} // namespace supervision
} // namespace safety
