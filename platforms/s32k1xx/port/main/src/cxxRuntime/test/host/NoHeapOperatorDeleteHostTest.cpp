/* DOC-28 (dev-environment.md 4.6 P5 (a), finding DE5): host test of the platform's own operator delete - the real
 * platforms/s32k1xx/port/main/src/cxxRuntime/NoHeapOperatorDelete.cpp with stubs for the safe supervisor, the reset
 * and the loop behind it (stubs/).
 *
 * One case per process run (argument), because the reaction ends the process like the reset ends the target:
 *   plain       ::operator delete(p)                 -> "REPORT", exit code 3
 *   sized       ::operator delete(p, size)           -> "REPORT", exit code 3
 *   destructor  delete of a class with a virtual destructor (the deleting destructor of DE5) -> "REPORT", exit 3
 *   fallback    as sized, but the stubbed reaction returns: the reset is requested and the loop reached
 *               -> "REPORT", "RESET", "LOOP", exit code 5 - operator delete never returns to its caller
 * "RETURNED" (exit code 0) is a finding: operator delete came back, the call went to a library version or the
 * reaction is missing. The reported call site must not be 0.
 *
 * Driver with the mutants: test_no_heap_operator_delete.py next to this file (zig c++, no hardware).
 */

#include <safeSupervisor/SafeSupervisor.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

namespace
{
bool reactionReturns = false;

void line(char const* const text)
{
    (void)std::fputs(text, stdout);
    (void)std::fputs("\n", stdout);
    (void)std::fflush(stdout);
}

struct Base
{
    virtual ~Base() = default;
    virtual int value() const { return 1; }
};

struct Derived : Base
{
    ~Derived() override = default;
    int value() const override { return 2; }
};

alignas(Derived) unsigned char storage[sizeof(Derived)];
int object = 0;
} // namespace

namespace safety
{
SafeSupervisor& SafeSupervisor::getInstance()
{
    static SafeSupervisor instance;
    return instance;
}

void SafeSupervisor::reportEtlAssert(uint32_t const callSite) const
{
    line(callSite != 0U ? "REPORT" : "REPORT without call site");
    if (!reactionReturns)
    {
        std::_Exit(3);
    }
}
} // namespace safety

extern "C" void softwareSystemReset(void) { line("RESET"); }

namespace test_loop
{
void reached()
{
    line("LOOP");
    std::_Exit(5);
}
} // namespace test_loop

int main(int const argc, char const* const* const argv)
{
    char const* const which = (argc > 1) ? argv[1] : "";
    if (std::strcmp(which, "plain") == 0)
    {
        ::operator delete(&object);
    }
    else if (std::strcmp(which, "sized") == 0)
    {
        ::operator delete(&object, sizeof(object));
    }
    else if (std::strcmp(which, "destructor") == 0)
    {
        Base* const base = new (storage) Derived();
        if (base->value() != 2)
        {
            return 2;
        }
        delete base; // the deleting destructor: destructor, then operator delete
    }
    else if (std::strcmp(which, "fallback") == 0)
    {
        reactionReturns = true;
        ::operator delete(&object, sizeof(object));
    }
    else
    {
        line("unknown case");
        return 2;
    }
    line("RETURNED");
    return 0;
}
