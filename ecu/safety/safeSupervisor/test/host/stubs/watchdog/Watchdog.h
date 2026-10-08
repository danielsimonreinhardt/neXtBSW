/* SYS-23 host test stub of the MCU watchdog of the family (OpenBSW safeBspMcuWatchdog): a service is recorded
 * as 'W'. */
#pragma once

namespace test_trace
{
void add(char c);
}

namespace safety
{
namespace bsp
{
class Watchdog
{
public:
    static void serviceWatchdog() { ::test_trace::add('W'); }
};
} // namespace bsp
} // namespace safety
