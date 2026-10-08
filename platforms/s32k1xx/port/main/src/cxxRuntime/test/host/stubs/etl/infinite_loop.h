/* DOC-28 host test stub: the loop after the reset request ends the test process with its own exit code (defined by
 * the test) instead of hanging. */
#pragma once

namespace test_loop
{
[[noreturn]] void reached();
}

namespace etl
{
[[noreturn]] inline void infinite_loop() { ::test_loop::reached(); }
} // namespace etl
