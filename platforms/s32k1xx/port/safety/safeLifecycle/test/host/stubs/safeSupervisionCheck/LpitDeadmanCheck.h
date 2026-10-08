/* SYS-41 host test stub of safeSupervisionCheck/LpitDeadmanCheck.h: the expected configuration and the result of
 * the check are scripted by the test; the check records 'd'. The real check has its own host test
 * (safeSupervisionCheck/test/host/LpitDeadmanCheckHostTest.cpp). */
#pragma once

#include "../cycleStubs.h"

#include <cstdint>

namespace safety
{
namespace supervision
{
struct LpitDeadmanConfiguration
{
    uint32_t channel;
};

namespace test
{
extern LpitDeadmanConfiguration const* expected; // nullptr = project without deadman
extern uint32_t result;                          // what the check reports
} // namespace test

inline LpitDeadmanConfiguration const* deadmanExpectedConfiguration() { return test::expected; }

inline uint32_t checkLpitDeadman(LpitDeadmanConfiguration const&)
{
    ::test_trace::add('d');
    return test::result;
}
} // namespace supervision
} // namespace safety
