/* SYS-80 host test stub: on the target the loop after a reset request is never reached. Here reaching it is a finding
 * (the reset did not come), reported by the exception the test catches instead of hanging. */
#pragma once

namespace test_trace
{
struct LoopReached
{};
} // namespace test_trace

namespace etl
{
[[noreturn]] inline void infinite_loop() { throw ::test_trace::LoopReached{}; }
} // namespace etl
