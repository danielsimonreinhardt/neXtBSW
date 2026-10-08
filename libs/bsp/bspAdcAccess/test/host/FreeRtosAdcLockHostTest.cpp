/* Host test of adc::FreeRtosAdcLock (IO-16), the alternative lock policy of adc::AdcAccess
 * (BSP_ADC_ACCESS_RTOS_MUTEX), no target hardware and no FreeRTOS needed.
 *
 * The production header is compiled against a FreeRTOS model in stubs/: scheduler state and a
 * statically allocated mutex with an owner and priority inheritance. Handler mode, PRIMASK and
 * BASEPRI reach the policy only through the interrupt context of the platform (ARCH-26), here
 * ModelInterruptContext; the stubs have no core register accessors, so the contract compiles
 * without them. Tested are the context detection of canBlock(), the mapping of the policy onto
 * the FreeRTOS calls (including the wait limit), the exclusive section, and AdcAccess on top of
 * the real policy: task conversion with the mutex held and interrupts enabled, conversion from a
 * context that cannot wait without touching the mutex, contention, wait timeout, and the restart
 * of a preempted task conversion.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsp/bspAdcAccess/include -Ilibs/bsp/bspAdcAccess/test/host/stubs \
 *       -isystem external/openbsw/libs/bsw/bsp/include \
 *       -isystem external/openbsw/libs/bsp/bspInterrupts/mock/include \
 *       libs/bsp/bspAdcAccess/test/host/FreeRtosAdcLockHostTest.cpp -o build/host/adc_freertos_lock_test.exe
 *   build/host/adc_freertos_lock_test.exe
 */

#include <cstdint>
#include <cstdio>

#include "adc/AdcAccess.h"
#include "adc/FreeRtosAdcLock.h"

namespace freertosStub
{
State state;

int indexOf(void const* const handle)
{
    return static_cast<StaticSemaphore_t const*>(handle)->index;
}
} // namespace freertosStub

extern "C"
{
uint32_t getMachineStateRegisterValueAndSuspendAllInterrupts(void)
{
    auto& s = freertosStub::state;
    ++s.suspendCalls;
    uint32_t const old = s.primask;
    ++s.suspendDepth;
    s.primask = 1U;
    return old;
}

OldIntEnabledStatusValueType getOldIntEnabledStatusValueAndSuspendAllInterrupts()
{
    return getMachineStateRegisterValueAndSuspendAllInterrupts();
}

void resumeAllInterrupts(uint32_t const old)
{
    auto& s = freertosStub::state;
    ++s.resumeCalls;
    --s.suspendDepth;
    s.primask = old;
}

uint64_t getSystemTimeNs(void) { return freertosStub::state.timeNs; }
}

namespace
{
/* Interrupt context of the platform (ARCH-26), here over the model: handler mode, PRIMASK and
 * BASEPRI as a Cortex-M port reports them. The policy only sees the answer. */
struct ModelInterruptContext
{
    static uint32_t queries;

    static bool interruptsLockedOrInIsr()
    {
        ++queries;
        auto const& s = freertosStub::state;
        return (s.insideInterrupt != pdFALSE) || (s.primask != 0U) || (s.basepri != 0U);
    }
};

uint32_t ModelInterruptContext::queries = 0U;
} // namespace

namespace
{
int failures = 0;
int checks   = 0;

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

namespace fs = freertosStub;

constexpr size_t INSTANCES     = 2U;
constexpr int CONVERSION_POLLS = 4;
constexpr int POLL_LIMIT       = 1000;
constexpr TickType_t WAIT_MS   = 100U;

using Lock = ::adc::FreeRtosAdcLock<INSTANCES, ModelInterruptContext>;

uint16_t code(size_t const instance, uint8_t const input)
{
    return static_cast<uint16_t>(((instance + 1U) * 1000U) + input);
}

/* Same register model as AdcAccessHostTest.cpp: SC1[0] write starts and clears COCO, reading
 * R[0] clears COCO. */
struct AdcRegisters
{
    uint8_t channel = 0U;
    bool converting = false;
    bool coco       = false;
    uint16_t r      = 0U;
    int remaining   = 0;
    int starts      = 0;

    uint16_t readR()
    {
        coco = false;
        return r;
    }

    void writeSc1(uint8_t const ch)
    {
        channel    = ch;
        converting = true;
        coco       = false;
        remaining  = CONVERSION_POLLS;
        ++starts;
    }

    bool pollCoco(size_t const instance)
    {
        if (converting && (--remaining == 0))
        {
            converting = false;
            coco       = true;
            r          = code(instance, channel);
        }
        return coco;
    }
};

struct ModelConverter
{
    AdcRegisters reg[INSTANCES];
    int restarts        = 0;
    int convertCalls    = 0;
    int suspendDepthSeen = -1; ///< exclusive depth while the last conversion ran
    int ownerSeen        = fs::NO_OWNER;
    void (*hook)()       = nullptr;

    bsp::BspReturnCode convert(size_t const instance, uint8_t const input, uint16_t& value)
    {
        ++convertCalls;
        suspendDepthSeen = fs::state.suspendDepth;
        ownerSeen        = fs::state.mutex[instance].owner;
        AdcRegisters& a  = reg[instance];
        (void)a.readR();
        a.writeSc1(input);
        int polls = 0;
        while (!a.pollCoco(instance))
        {
            if (hook != nullptr)
            {
                void (*const h)() = hook;
                hook              = nullptr; // fire once
                h();
            }
            if (++polls > POLL_LIMIT)
            {
                return bsp::BSP_ERROR;
            }
        }
        value = a.readR();
        return bsp::BSP_OK;
    }

    void restart(size_t const instance, uint8_t const input)
    {
        ++restarts;
        reg[instance].writeSc1(input);
        int polls = 0;
        while (!reg[instance].pollCoco(instance))
        {
            if (++polls > POLL_LIMIT)
            {
                return;
            }
        }
    }
};

struct Fixture
{
    Lock lock{pdMS_TO_TICKS(WAIT_MS)};
    ModelConverter conv;
    ::adc::AdcAccess<Lock, ModelConverter, INSTANCES> access{lock, conv};

    Fixture()
    {
        fs::state.reset();
        lock.init();
    }
};

void testCanBlock()
{
    fs::state.reset();
    Lock lock(pdMS_TO_TICKS(WAIT_MS));
    // before init(): the mutexes do not exist yet, nobody may wait
    CHECK(!lock.canBlock());
    lock.init();
    CHECK(lock.canBlock());

    fs::state.schedulerState = taskSCHEDULER_NOT_STARTED;
    CHECK(!lock.canBlock());
    fs::state.schedulerState = taskSCHEDULER_SUSPENDED;
    CHECK(!lock.canBlock());
    fs::state.schedulerState = taskSCHEDULER_RUNNING;
    CHECK(lock.canBlock());

    fs::state.insideInterrupt = pdTRUE; // handler mode
    CHECK(!lock.canBlock());
    fs::state.insideInterrupt = pdFALSE;

    fs::state.primask = 1U; // SuspendResumeAllInterruptsScopedLock
    CHECK(!lock.canBlock());
    fs::state.primask = 0U;

    fs::state.basepri = 0x40U; // taskENTER_CRITICAL on ARMv7-M
    CHECK(!lock.canBlock());
    fs::state.basepri = 0U;
    CHECK(lock.canBlock());

    // ARCH-26: the platform is asked only where its answer matters (mutexes exist, scheduler
    // running), and a caller inside the policy's own exclusive section cannot wait either.
    ModelInterruptContext::queries = 0U;
    fs::state.schedulerState       = taskSCHEDULER_NOT_STARTED;
    CHECK(!lock.canBlock());
    CHECK(ModelInterruptContext::queries == 0U);
    fs::state.schedulerState = taskSCHEDULER_RUNNING;
    CHECK(lock.canBlock());
    CHECK(ModelInterruptContext::queries == 1U);
    auto const state = lock.enterExclusive();
    CHECK(!lock.canBlock());
    lock.exitExclusive(state);
    CHECK(lock.canBlock());
}

void testInitCreatesOneMutexPerInstance()
{
    Fixture f;
    for (size_t i = 0U; i < INSTANCES; ++i)
    {
        CHECK(fs::state.mutex[i].created);
        CHECK(fs::state.mutex[i].owner == fs::NO_OWNER);
    }
}

void testLockMapping()
{
    Fixture f;
    CHECK(f.lock.tryLock(0U));
    CHECK(fs::state.mutex[0].takes == 1U);
    CHECK(fs::state.mutex[0].lastWait == 0U); // tryLock never waits
    CHECK(fs::state.mutex[0].owner == fs::state.currentTask);
    f.lock.unlock(0U);
    CHECK(fs::state.mutex[0].gives == 1U);
    CHECK(fs::state.mutex[0].owner == fs::NO_OWNER);

    // lock() passes the configured wait limit on
    fs::state.mutex[1].owner       = 1; // held by another task
    fs::state.currentTask          = 0;
    fs::state.ownerReleasesOnWait  = false;
    CHECK(!f.lock.lock(1U));
    CHECK(fs::state.mutex[1].lastWait == pdMS_TO_TICKS(WAIT_MS));
    CHECK(fs::state.mutex[1].failedTakes == 1U);
}

void testExclusiveSectionAndTime()
{
    Fixture f;
    CHECK(fs::state.suspendDepth == 0);
    auto const outer = f.lock.enterExclusive();
    CHECK(fs::state.suspendDepth == 1);
    auto const inner = f.lock.enterExclusive(); // nestable
    CHECK(fs::state.suspendDepth == 2);
    f.lock.exitExclusive(inner);
    CHECK(fs::state.suspendDepth == 1);
    CHECK(fs::state.primask == 1U); // still suspended
    f.lock.exitExclusive(outer);
    CHECK(fs::state.suspendDepth == 0);
    CHECK(fs::state.primask == 0U); // restored
    CHECK(fs::state.suspendCalls == 2U);
    CHECK(fs::state.resumeCalls == 2U);

    fs::state.timeNs = 0x1234ABCDU;
    CHECK(f.lock.nowNs() == 0x1234ABCDU);
}

void testTaskConversionUsesMutex()
{
    Fixture f;
    uint16_t v = 0U;
    CHECK(f.access.convert(0U, 12U, v) == bsp::BSP_OK);
    CHECK(v == code(0U, 12U));
    CHECK(fs::state.mutex[0].takes == 1U);
    CHECK(fs::state.mutex[0].gives == 1U);
    CHECK(fs::state.mutex[0].owner == fs::NO_OWNER);
    // the conversion itself ran with the interrupts enabled, but with the mutex held
    CHECK(f.conv.suspendDepthSeen == 0);
    CHECK(f.conv.ownerSeen == 0);
    CHECK(fs::state.suspendDepth == 0);
    auto const s = f.access.getStatistics();
    CHECK(s.conversions == 1U);
    CHECK(s.exclusive == 0U);
    CHECK(s.contended == 0U);
    CHECK(s.errors == 0U);
}

void testIsrConversionDoesNotTouchMutex()
{
    Fixture f;
    fs::state.insideInterrupt = pdTRUE;
    uint16_t v                = 0U;
    CHECK(f.access.convert(1U, 5U, v) == bsp::BSP_OK);
    CHECK(v == code(1U, 5U));
    CHECK(fs::state.mutex[1].takes == 0U);
    CHECK(fs::state.mutex[1].gives == 0U);
    CHECK(f.conv.suspendDepthSeen == 1); // whole conversion in the exclusive section
    CHECK(fs::state.suspendDepth == 0);
    auto const s = f.access.getStatistics();
    CHECK(s.exclusive == 1U);
    CHECK(s.nested == 0U);
}

void testContentionInheritsPriority()
{
    Fixture f;
    fs::state.mutex[0].owner      = 0; // low priority task 0 holds the converter
    fs::state.currentTask         = 3; // high priority task 3 wants it
    fs::state.ownerReleasesOnWait = true;
    uint16_t v                    = 0U;
    CHECK(f.access.convert(0U, 1U, v) == bsp::BSP_OK);
    CHECK(v == code(0U, 1U));
    CHECK(fs::state.mutex[0].failedTakes == 1U); // tryLock first
    CHECK(fs::state.mutex[0].takes == 1U);       // then the blocking take
    CHECK(fs::state.mutex[0].maxInheritedFrom == 3);
    CHECK(fs::state.effectivePriority[0] == fs::state.priority[0]); // given back again
    auto const s = f.access.getStatistics();
    CHECK(s.contended == 1U);
    CHECK(s.lockTimeouts == 0U);
    CHECK(s.conversions == 1U);
}

void testWaitTimeoutDoesNotConvert()
{
    Fixture f;
    fs::state.mutex[1].owner      = 1;
    fs::state.currentTask         = 0;
    fs::state.ownerReleasesOnWait = false;
    uint16_t v                    = 0xABCDU;
    CHECK(f.access.convert(1U, 7U, v) == bsp::BSP_ERROR);
    CHECK(v == 0xABCDU);
    CHECK(f.conv.convertCalls == 0);
    CHECK(fs::state.mutex[1].gives == 0U); // not taken, not given
    CHECK(fs::state.mutex[1].owner == 1);  // still the other task's
    auto const s = f.access.getStatistics();
    CHECK(s.lockTimeouts == 1U);
    CHECK(s.contended == 1U);
    CHECK(s.conversions == 0U);
}

void testPreemptedTaskConversionIsRestarted()
{
    // An ISR converts the same instance in the middle of a task conversion: it must not wait for
    // the mutex (canBlock is false in handler mode) and must restart the preempted input.
    Fixture f;
    static Fixture* fixture = nullptr;
    static uint16_t nested  = 0U;
    static bsp::BspReturnCode nestedRet = bsp::BSP_ERROR;
    fixture                 = &f;
    nested                  = 0U;
    f.conv.hook             = []() {
        fs::state.insideInterrupt = pdTRUE;
        nestedRet                 = fixture->access.convert(0U, 31U, nested);
        fs::state.insideInterrupt = pdFALSE;
    };
    uint16_t v = 0U;
    CHECK(f.access.convert(0U, 7U, v) == bsp::BSP_OK);
    CHECK(nestedRet == bsp::BSP_OK);
    CHECK(nested == code(0U, 31U));
    CHECK(v == code(0U, 7U)); // the task still gets the value of its own input
    CHECK(f.conv.restarts == 1);
    CHECK(fs::state.mutex[0].takes == 1U); // the ISR did not take the mutex
    CHECK(fs::state.mutex[0].gives == 1U);
    auto const s = f.access.getStatistics();
    CHECK(s.nested == 1U);
    CHECK(s.exclusive == 1U);
    CHECK(s.conversions == 2U);
    CHECK(s.errors == 0U);
}

void testBeforeSchedulerStartRunsExclusive()
{
    Fixture f;
    fs::state.schedulerState = taskSCHEDULER_NOT_STARTED;
    uint16_t v               = 0U;
    CHECK(f.access.convert(0U, 2U, v) == bsp::BSP_OK);
    CHECK(v == code(0U, 2U));
    CHECK(fs::state.mutex[0].takes == 0U);
    CHECK(f.access.getStatistics().exclusive == 1U);
}

} // namespace

int main()
{
    testCanBlock();
    testInitCreatesOneMutexPerInstance();
    testLockMapping();
    testExclusiveSectionAndTime();
    testTaskConversionUsesMutex();
    testIsrConversionDoesNotTouchMutex();
    testContentionInheritsPriority();
    testWaitTimeoutDoesNotConvert();
    testPreemptedTaskConversionIsRestarted();
    testBeforeSchedulerStartRunsExclusive();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
