/* SYS-169 host test stub of the hard fault dump reader (the real one: platforms/s32k1xx/hardFaultHandler, own host
 * test hard-fault-dump): the exception number of the dump the test has "written" - 0 for no valid dump, 3 for a hard
 * fault, 38 for the watchdog's pre-reset interrupt. */
#pragma once

#include <cstdint>

namespace test_dump
{
inline uint32_t exception = 3U;
inline uint32_t region[1] = {0U};
} // namespace test_dump

namespace hardfault
{
inline uint32_t* getDumpRegion() { return ::test_dump::region; }

inline uint32_t getDumpException(uint32_t const* const /* dump */) { return ::test_dump::exception; }
} // namespace hardfault
