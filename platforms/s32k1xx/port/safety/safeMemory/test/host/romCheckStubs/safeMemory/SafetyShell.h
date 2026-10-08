/* Host test stub of safeMemory/SafetyShell.h for RomCheckHostTest (SYS-38): counts the open shells, so the test can
 * check that the safety RAM (RomCheck::_result) and the supervisor are only written inside one. SYS-44: also counts
 * the shells opened, so the test can show that a waiting check does not open one in every round of the idle loop. */

#pragma once

namespace romcheck_test
{
inline int& shellDepth()
{
    static int depth = 0;
    return depth;
}

inline int& shellsOpened()
{
    static int count = 0;
    return count;
}
} // namespace romcheck_test

namespace safety
{
class SafetyShell final
{
public:
    SafetyShell()
    {
        ++::romcheck_test::shellDepth();
        ++::romcheck_test::shellsOpened();
    }

    ~SafetyShell() { --::romcheck_test::shellDepth(); }

    SafetyShell(SafetyShell const&)            = delete;
    SafetyShell& operator=(SafetyShell const&) = delete;
};
} // namespace safety
