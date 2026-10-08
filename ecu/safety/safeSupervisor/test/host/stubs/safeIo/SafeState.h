/* SYS-23 host test stub of the safe state (the real one: platforms/s32k1xx/port/safety/safeIo, own host test
 * safe-state): entering is recorded as 'S', leaving as 'L'. SYS-117: the degraded walk of a subset of the table as
 * 'D', its entry mask in test_trace::degradedEntries (defined by the test that uses it). */
#pragma once

#include <cstdint>

namespace test_trace
{
void add(char c);
void degraded(uint32_t entries);
} // namespace test_trace

namespace safety
{
class SafeState
{
public:
    static void enterSafeState() { ::test_trace::add('S'); }

    static void leaveSafeState() { ::test_trace::add('L'); }

    static void enterDegradedState(uint32_t const entries) { ::test_trace::degraded(entries); }
};
} // namespace safety
