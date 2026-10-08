/* SYS-80 host test stub: the ECC check the hard fault handler runs before its reset is recorded as 'C'. */
#pragma once

namespace test_trace
{
void add(char c);
}

namespace safety
{
namespace safe_memory
{
inline void checkEccErrors() { ::test_trace::add('C'); }
} // namespace safe_memory
} // namespace safety
