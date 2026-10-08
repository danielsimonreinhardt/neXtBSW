/* DOC-44 (clauses SSR-020(b) and SSR-020(c)): host test of the scoped unlock of the protected RAM with the real header
 * platforms/s32k1xx/port/safety/safeMemory/include/safeMemory/ProtectedRamScopedUnlock.h.
 *
 * SSR-020(b): the safety shell shall open write access only with all interrupts suspended.
 * SSR-020(c): the safety shell shall close write access again on every path that leaves it.
 *
 * The gate of the protected RAM is a model here (a flag and a counter per call of the four functions the unlock uses);
 * the real MemoryProtection.cpp, with its MPU register model, is exercised by safety-gate. This test shows what that
 * one does not: at the moment the gate is opened and at the moment it is restored, the interrupt lock is held; and the
 * old state comes back on a normal exit, an early return, a nested unlock and an unlock that was taken while the gate
 * was already open. The variant without the interrupt lock that the header also contains
 * (ProtectedRamScopedUnlock_WithoutIsrLock, only for code that has suspended the interrupts itself) is the negative
 * control: the observer must see its gate change without a lock.
 *
 * Build and run (zig c++, no hardware; tools/test entry safety-shell-unlock):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Itools/test/clause_evidence/stubs -Iplatforms/s32k1xx/port/safety/safeMemory/include \
 *       tools/test/clause_evidence/SafetyShellUnlockHostTest.cpp -o build/host/safetyshellunlock.exe
 */

#include <cstdint>
#include <cstdio>

#include <safeMemory/ProtectedRamScopedUnlock.h>

namespace
{
int checks   = 0;
int failures = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

// The model of the gate: true = closed (read only), false = open (full access)
bool gateClosed = true;
// What the observer saw at each change of the gate
int changesWithLock    = 0;
int changesWithoutLock = 0;

void observeChange()
{
    if (::unlock_test::lockDepth() > 0)
    {
        ++changesWithLock;
    }
    else
    {
        ++changesWithoutLock;
    }
}

void reset()
{
    gateClosed         = true;
    changesWithLock    = 0;
    changesWithoutLock = 0;
}
} // namespace

namespace safety
{
// The four functions of the real class that the unlock uses, on the model of the gate (same contract as
// MemoryProtection.cpp: GetLockAndUnlock returns the old state and opens, RestoreLock puts the old state back)
bool MemoryProtection::fusaGateGetLockAndUnlock()
{
    bool const old = gateClosed;
    if (old)
    {
        observeChange();
        gateClosed = false;
    }
    return old;
}

void MemoryProtection::fusaGateRestoreLock(bool const gateLocked)
{
    if (gateLocked != gateClosed)
    {
        observeChange();
        gateClosed = gateLocked;
    }
}

bool MemoryProtection::fusaGateIsLocked() { return gateClosed; }
} // namespace safety

namespace
{
using ::safety::ProtectedRamScopedUnlock;
using ::safety::ProtectedRamScopedUnlock_WithoutIsrLock;

void scopeLeftNormally()
{
    {
        ProtectedRamScopedUnlock const unlock;
        CHECK(!gateClosed);
        CHECK(::unlock_test::lockDepth() == 0); // the lock is held for the change only, not for the open phase
    }
    CHECK(gateClosed);
}

bool earlyReturn(bool const leaveEarly)
{
    ProtectedRamScopedUnlock const unlock;
    if (leaveEarly)
    {
        return false;
    }
    CHECK(!gateClosed);
    return true;
}

void nested()
{
    ProtectedRamScopedUnlock const outer;
    {
        ProtectedRamScopedUnlock const inner;
        CHECK(!gateClosed);
    }
    CHECK(!gateClosed); // the inner unlock found the gate open and leaves it open
}

void testOpensAndClosesUnderTheInterruptLock()
{
    std::printf("gate opened and restored with all interrupts suspended\n");
    reset();
    scopeLeftNormally();
    CHECK(gateClosed);
    CHECK(changesWithLock == 2); // one opening, one restoring
    CHECK(changesWithoutLock == 0);
    CHECK(::unlock_test::lockDepth() == 0); // the lock is balanced
}

void testEveryPathThatLeavesRestoresTheGate()
{
    std::printf("gate closed again on every path that leaves the scope\n");
    reset();
    (void)earlyReturn(true);
    CHECK(gateClosed);
    (void)earlyReturn(false);
    CHECK(gateClosed);
    nested();
    CHECK(gateClosed);
    CHECK(changesWithoutLock == 0);
    CHECK(::unlock_test::lockDepth() == 0);
    // 3 scopes that opened the gate (the nested inner one changed nothing), each opens once and restores once
    CHECK(changesWithLock == 6);
}

void testGateThatWasOpenStaysOpen()
{
    std::printf("an unlock taken while the gate is open leaves it open\n");
    reset();
    gateClosed = false; // opened by an outer shell of another path
    {
        ProtectedRamScopedUnlock const unlock;
        CHECK(!gateClosed);
    }
    CHECK(!gateClosed); // restored to what it was, not closed behind the outer shell
    CHECK(changesWithLock == 0);
    gateClosed = true;
}

void testNegativeControlWithoutTheLockIsSeen()
{
    std::printf("negative control: the variant without the interrupt lock is seen by the observer\n");
    reset();
    {
        ProtectedRamScopedUnlock_WithoutIsrLock const unlock;
        CHECK(!gateClosed);
    }
    CHECK(gateClosed);
    CHECK(changesWithoutLock == 2);
    CHECK(changesWithLock == 0);
    // under a lock of the caller the same variant is accepted (what its comment demands)
    reset();
    {
        ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
        ProtectedRamScopedUnlock_WithoutIsrLock const unlock;
    }
    CHECK(changesWithLock == 2);
    CHECK(changesWithoutLock == 0);
}
} // namespace

int main()
{
    testOpensAndClosesUnderTheInterruptLock();
    testEveryPathThatLeavesRestoresTheGate();
    testGateThatWasOpenStaysOpen();
    testNegativeControlWithoutTheLockIsSeen();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
