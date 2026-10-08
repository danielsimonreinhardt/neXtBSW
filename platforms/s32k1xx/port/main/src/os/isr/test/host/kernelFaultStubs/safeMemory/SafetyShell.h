/* SYS-80 host test stub of the SafetyShell of the family port (platforms/s32k1xx/port/safety/safeMemory): opening it
 * is recorded as 'O'; while it is open the protected RAM is writable and all interrupts are suspended. The test checks
 * that the supervisor is reached only inside it (on the target a write outside it is a bus fault, SYS-37). */
#pragma once

namespace test_trace
{
void add(char c);
extern int lockDepth;
} // namespace test_trace

namespace test_shell
{
extern int depth;
}

namespace safety
{
class SafetyShell final
{
public:
    SafetyShell()
    {
        ::test_trace::add('O');
        ++::test_shell::depth;
        ++::test_trace::lockDepth;
    }

    ~SafetyShell()
    {
        --::test_shell::depth;
        --::test_trace::lockDepth;
    }

    SafetyShell(SafetyShell const&)            = delete;
    SafetyShell& operator=(SafetyShell const&) = delete;
};
} // namespace safety
