/* Host test of the serialized ADC access (adc::AdcAccess), no target hardware needed.
 *
 * Part 1 (deterministic): a register model of a polled SAR ADC (software trigger on SC1[0],
 * COCO, R[0], reading R clears COCO, writing SC1 aborts and restarts) with a converter that does
 * the same steps as OpenBSW Adc::getValueSync, and a scripted lock policy. A caller that cannot
 * wait is injected at every step of a task conversion.
 * Part 2 (stress): real threads, one std::mutex per instance as blocking lock and one "CPU"
 * mutex that every register step takes; the exclusive section holds it for the whole conversion.
 * Part 3 (IO-16): the polling bound of adc/AdcPollLimit.h against two synthetic ADC profiles
 * (neither of them the eval board) and a converter that never completes.
 *
 * The FreeRTOS lock policy has its own host test, FreeRtosAdcLockHostTest.cpp.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsp/bspAdcAccess/include -isystem external/openbsw/libs/bsw/bsp/include \
 *       libs/bsp/bspAdcAccess/test/host/AdcAccessHostTest.cpp -o build/host/adc_access_test.exe
 *   build/host/adc_access_test.exe
 */

#include "adc/AdcAccess.h"
#include "adc/AdcPollLimit.h"

#include <atomic>
#include <cstdio>
#include <functional>
#include <mutex>
#include <random>
#include <thread>
#include <vector>

namespace
{
std::atomic<int> failures{0};
std::atomic<int> checks{0};

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

constexpr size_t INSTANCES     = 2U;
constexpr int CONVERSION_POLLS = 4; // polls until COCO after a start
constexpr int POLL_LIMIT       = 1000;

uint16_t code(size_t const instance, uint8_t const input)
{
    return static_cast<uint16_t>(((instance + 1U) * 1000U) + input);
}

/* SC1[0]/COCO/R[0] of one ADC instance. */
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

    bool stuck      = false; // conversion never completes (dead converter clock, IO-16)
    int stuckAtStart = 0;    // from this start on, no conversion completes (0: off)

    bool pollCoco(size_t const instance)
    {
        if (stuck || ((stuckAtStart != 0) && (starts >= stuckAtStart)))
        {
            return false;
        }
        if (converting && (--remaining == 0))
        {
            converting = false;
            coco       = true;
            r          = code(instance, channel);
        }
        return coco;
    }
};

/* ---------------------------------------------------------------------------------------------
 * Part 1: deterministic
 * ------------------------------------------------------------------------------------------- */

struct ScriptedLock;

/* Converter with the steps of Adc::getValueSync and a hook between the steps. */
struct ModelConverter
{
    using Hook = std::function<void(size_t instance, int step)>;

    AdcRegisters reg[INSTANCES];
    bool started[INSTANCES] = {true, true};
    Hook hook;
    bool hookEnabled      = true; // off during an injected conversion (no nesting in nesting)
    ScriptedLock* lock    = nullptr;
    int restarts          = 0;
    int convertCalls      = 0;
    int lastDepth         = -1; // exclusive depth during the last conversion
    bool lastLocked       = false;
    uint32_t* clock       = nullptr;
    int pollTimeouts      = 0; // conversions that gave up (bounded polling, IO-16)
    int restartTimeouts   = 0;
    int maxPolls          = 0;

    void step(size_t const instance, int const s)
    {
        if (clock != nullptr)
        {
            *clock += 100U;
        }
        if (hook && hookEnabled)
        {
            hook(instance, s);
        }
    }

    bsp::BspReturnCode convert(size_t instance, uint8_t input, uint16_t& value);

    void restart(size_t const instance, uint8_t const input)
    {
        ++restarts;
        reg[instance].writeSc1(input);
        int polls = 0;
        while (!reg[instance].pollCoco(instance))
        {
            if (++polls >= POLL_LIMIT)
            {
                ++restartTimeouts;
                return;
            }
        }
    }
};

struct ScriptedLock
{
    using State = int;

    bool task          = true; // context may block
    bool tryLockResult = true;
    bool lockResult    = true;
    uint32_t waitNs    = 0U;
    uint32_t now       = 0U;
    int depth          = 0;
    int exclusiveCalls = 0;
    int tryLocks       = 0;
    int locks          = 0;
    int unlocks        = 0;
    bool held[INSTANCES] = {false, false};

    bool canBlock() const { return task; }

    bool tryLock(size_t const i)
    {
        ++tryLocks;
        if (tryLockResult)
        {
            held[i] = true;
        }
        return tryLockResult;
    }

    bool lock(size_t const i)
    {
        ++locks;
        now += waitNs;
        if (lockResult)
        {
            held[i] = true;
        }
        return lockResult;
    }

    void unlock(size_t const i)
    {
        ++unlocks;
        held[i] = false;
    }

    State enterExclusive()
    {
        ++exclusiveCalls;
        return depth++;
    }

    void exitExclusive(State const s) { depth = s; }

    uint32_t nowNs() const { return now; }
};

bsp::BspReturnCode ModelConverter::convert(size_t const instance, uint8_t const input, uint16_t& value)
{
    ++convertCalls;
    lastDepth  = lock->depth;
    lastLocked = lock->held[instance];
    if (!started[instance])
    {
        return bsp::BSP_ERROR;
    }
    AdcRegisters& a = reg[instance];
    step(instance, 0);
    (void)a.readR();
    step(instance, 1);
    a.writeSc1(input);
    step(instance, 2);
    int polls = 0;
    int s     = 3;
    while (!a.pollCoco(instance))
    {
        step(instance, s++);
        if (++polls > POLL_LIMIT)
        {
            ++pollTimeouts;
            if (polls > maxPolls)
            {
                maxPolls = polls;
            }
            return bsp::BSP_ERROR;
        }
    }
    if (polls > maxPolls)
    {
        maxPolls = polls;
    }
    step(instance, 100); // COCO seen, R[0] not read yet
    value = a.readR();
    step(instance, 101); // done, lock still held and busy still set
    return bsp::BSP_OK;
}

struct Fixture
{
    ScriptedLock lock;
    ModelConverter conv;
    adc::AdcAccess<ScriptedLock, ModelConverter, INSTANCES> access{lock, conv};

    Fixture()
    {
        conv.lock  = &lock;
        conv.clock = &lock.now;
    }

    /* Conversion from a context that cannot wait, as an ISR preempting the current caller. */
    bsp::BspReturnCode isrConvert(size_t const instance, uint8_t const input, uint16_t& value)
    {
        bool const wasTask           = lock.task;
        conv.hookEnabled             = false;
        lock.task                    = false;
        bsp::BspReturnCode const ret = access.convert(instance, input, value);
        lock.task                    = wasTask;
        conv.hookEnabled             = true;
        return ret;
    }
};

void testTaskConversion()
{
    Fixture f;
    uint16_t v                   = 0U;
    bsp::BspReturnCode const ret = f.access.convert(0U, 12U, v);
    CHECK(ret == bsp::BSP_OK);
    CHECK(v == code(0U, 12U));
    CHECK(f.lock.tryLocks == 1);
    CHECK(f.lock.locks == 0);
    CHECK(f.lock.unlocks == 1);
    CHECK(!f.lock.held[0]);
    CHECK(f.conv.lastDepth == 0); // converts with interrupts enabled
    CHECK(f.conv.lastLocked);     // but with the instance lock held
    CHECK(f.lock.depth == 0);
    auto const s = f.access.getStatistics();
    CHECK(s.conversions == 1U);
    CHECK(s.exclusive == 0U);
    CHECK(s.contended == 0U);
    CHECK(s.nested == 0U);
    CHECK(s.errors == 0U);
    CHECK(s.maxConversionNs == 800U); // 8 steps of 100 ns
    CHECK(s.maxExclusiveNs == 0U);

    // busy is cleared afterwards: a following exclusive conversion restarts nothing
    uint16_t w = 0U;
    CHECK(f.isrConvert(0U, 3U, w) == bsp::BSP_OK);
    CHECK(w == code(0U, 3U));
    CHECK(f.conv.restarts == 0);
    CHECK(f.access.getStatistics().nested == 0U);
}

void testExclusiveConversion()
{
    // ISR, interrupts suspended or scheduler not started: never touches the blocking lock
    Fixture f;
    f.lock.task  = false;
    uint16_t v   = 0U;
    CHECK(f.access.convert(1U, 5U, v) == bsp::BSP_OK);
    CHECK(v == code(1U, 5U));
    CHECK(f.lock.tryLocks == 0);
    CHECK(f.lock.locks == 0);
    CHECK(f.lock.unlocks == 0);
    CHECK(f.conv.lastDepth == 1); // whole conversion in the exclusive section
    CHECK(f.lock.depth == 0);
    auto const s = f.access.getStatistics();
    CHECK(s.conversions == 1U);
    CHECK(s.exclusive == 1U);
    CHECK(s.nested == 0U);
    CHECK(s.maxExclusiveNs == 800U);
    CHECK(s.maxConversionNs == 0U);
}

void testNestedAtEveryStep()
{
    // A caller that cannot wait interrupts a task conversion at each step, same instance.
    for (int at = 0; at <= 101; ++at)
    {
        if ((at >= 2 + CONVERSION_POLLS) && (at < 100))
        {
            continue;
        }
        Fixture f;
        uint16_t nestedValue = 0U;
        bsp::BspReturnCode nestedRet = bsp::BSP_ERROR;
        bool fired                   = false;
        f.conv.hook                  = [&](size_t const instance, int const s) {
            if ((s == at) && (!fired))
            {
                fired     = true;
                nestedRet = f.isrConvert(instance, 31U, nestedValue);
            }
        };
        uint16_t v                   = 0U;
        bsp::BspReturnCode const ret = f.access.convert(0U, 7U, v);
        CHECK(fired);
        CHECK(ret == bsp::BSP_OK);
        CHECK(v == code(0U, 7U));
        CHECK(nestedRet == bsp::BSP_OK);
        CHECK(nestedValue == code(0U, 31U));
        CHECK(f.conv.restarts == 1);
        auto const s = f.access.getStatistics();
        CHECK(s.nested == 1U);
        CHECK(s.exclusive == 1U);
        CHECK(s.conversions == 2U);
        CHECK(s.errors == 0U);
        CHECK(f.lock.depth == 0);
        CHECK(f.lock.unlocks == 1);
        if (failures != 0)
        {
            std::printf("  (nested at step %d)\n", at);
            break;
        }
    }
}

void testNestedOtherInstanceAndTwice()
{
    {
        // other instance: no restart needed
        Fixture f;
        uint16_t nv = 0U;
        f.conv.hook = [&](size_t, int const s) {
            if (s == 3)
            {
                (void)f.isrConvert(1U, 2U, nv);
            }
        };
        uint16_t v = 0U;
        CHECK(f.access.convert(0U, 9U, v) == bsp::BSP_OK);
        CHECK(v == code(0U, 9U));
        CHECK(nv == code(1U, 2U));
        CHECK(f.conv.restarts == 0);
        CHECK(f.access.getStatistics().nested == 0U);
    }
    {
        // two interruptions of one conversion
        Fixture f;
        uint16_t a  = 0U;
        uint16_t b  = 0U;
        f.conv.hook = [&](size_t const instance, int const s) {
            if (s == 2)
            {
                (void)f.isrConvert(instance, 13U, a);
            }
            if (s == 100)
            {
                (void)f.isrConvert(instance, 14U, b);
            }
        };
        uint16_t v = 0U;
        CHECK(f.access.convert(1U, 4U, v) == bsp::BSP_OK);
        CHECK(v == code(1U, 4U));
        CHECK(a == code(1U, 13U));
        CHECK(b == code(1U, 14U));
        CHECK(f.conv.restarts == 2);
        CHECK(f.access.getStatistics().nested == 2U);
    }
}

void testContention()
{
    Fixture f;
    f.lock.tryLockResult = false;
    f.lock.lockResult    = true;
    f.lock.waitNs        = 5000U;
    uint16_t v           = 0U;
    CHECK(f.access.convert(0U, 1U, v) == bsp::BSP_OK);
    CHECK(v == code(0U, 1U));
    CHECK(f.lock.tryLocks == 1);
    CHECK(f.lock.locks == 1);
    CHECK(f.lock.unlocks == 1);
    CHECK(f.conv.lastLocked);
    auto s = f.access.getStatistics();
    CHECK(s.contended == 1U);
    CHECK(s.maxWaitNs == 5000U);
    CHECK(s.lockTimeouts == 0U);

    // maximum is kept, a shorter wait does not lower it
    f.lock.waitNs = 1000U;
    CHECK(f.access.convert(0U, 1U, v) == bsp::BSP_OK);
    s = f.access.getStatistics();
    CHECK(s.contended == 2U);
    CHECK(s.maxWaitNs == 5000U);

    f.access.resetStatistics();
    s = f.access.getStatistics();
    CHECK(s.contended == 0U);
    CHECK(s.maxWaitNs == 0U);
    CHECK(s.conversions == 0U);
    CHECK(f.lock.depth == 0);
}

void testLockTimeout()
{
    Fixture f;
    f.lock.tryLockResult = false;
    f.lock.lockResult    = false;
    f.lock.waitNs        = 100000000U;
    uint16_t v           = 0xABCDU;
    CHECK(f.access.convert(1U, 1U, v) == bsp::BSP_ERROR);
    CHECK(v == 0xABCDU);
    CHECK(f.conv.convertCalls == 0);
    CHECK(f.lock.unlocks == 0); // not taken, not given
    auto const s = f.access.getStatistics();
    CHECK(s.lockTimeouts == 1U);
    CHECK(s.contended == 1U);
    CHECK(s.conversions == 0U);
    CHECK(s.maxWaitNs == 100000000U);
    // busy was never set
    uint16_t w = 0U;
    CHECK(f.isrConvert(1U, 2U, w) == bsp::BSP_OK);
    CHECK(f.conv.restarts == 0);
}

void testErrors()
{
    Fixture f;
    uint16_t v = 0U;
    CHECK(f.access.convert(2U, 1U, v) == bsp::BSP_ERROR); // instance out of range
    CHECK(f.lock.tryLocks == 0);
    CHECK(f.conv.convertCalls == 0);
    CHECK(f.access.getStatistics().errors == 1U);

    f.conv.started[0] = false; // converter error in a task conversion
    CHECK(f.access.convert(0U, 1U, v) == bsp::BSP_ERROR);
    CHECK(f.lock.unlocks == 1);
    CHECK(!f.lock.held[0]);
    auto const s = f.access.getStatistics();
    CHECK(s.errors == 2U);
    CHECK(s.conversions == 1U);
    f.conv.started[0] = true;
    uint16_t w        = 0U;
    CHECK(f.isrConvert(0U, 2U, w) == bsp::BSP_OK);
    CHECK(f.conv.restarts == 0); // busy cleared after the error

    f.conv.started[1] = false; // converter error in an exclusive conversion
    CHECK(f.isrConvert(1U, 2U, w) == bsp::BSP_ERROR);
    CHECK(f.access.getStatistics().errors == 3U);
    CHECK(f.lock.depth == 0);
}

/* ---------------------------------------------------------------------------------------------
 * Part 3 (IO-16): bounded polling
 * ------------------------------------------------------------------------------------------- */

void testStuckConverterGivesUp()
{
    // A converter that never signals completion must not poll forever: it would suspend the
    // interrupts until the watchdog fires. Both paths give up and report an error.
    {
        Fixture f;
        f.conv.reg[0].stuck = true;
        uint16_t v          = 0xBEEFU;
        CHECK(f.access.convert(0U, 3U, v) == bsp::BSP_ERROR);
        CHECK(v == 0xBEEFU); // no value written
        CHECK(f.conv.pollTimeouts == 1);
        CHECK(f.conv.maxPolls == POLL_LIMIT + 1); // bounded
        CHECK(f.lock.unlocks == 1);               // lock released despite the error
        CHECK(!f.lock.held[0]);
        CHECK(f.lock.depth == 0);
        auto const s = f.access.getStatistics();
        CHECK(s.errors == 1U);
        CHECK(s.conversions == 1U);
        // the instance is not marked busy any more, so nothing is restarted afterwards
        f.conv.reg[0].stuck = false;
        uint16_t w          = 0U;
        CHECK(f.isrConvert(0U, 4U, w) == bsp::BSP_OK);
        CHECK(f.conv.restarts == 0);
    }
    {
        Fixture f; // exclusive path (ISR, interrupts suspended)
        f.conv.reg[1].stuck = true;
        uint16_t v          = 0U;
        CHECK(f.isrConvert(1U, 3U, v) == bsp::BSP_ERROR);
        CHECK(f.conv.pollTimeouts == 1);
        CHECK(f.lock.depth == 0); // exclusive section left again
        auto const s = f.access.getStatistics();
        CHECK(s.errors == 1U);
        CHECK(s.exclusive == 1U);
    }
    {
        // The converter dies while a task conversion is preempted: the third start (the restart
        // of the preempted input) never completes, so the restart gives up too and the
        // interrupted caller reports an error instead of hanging.
        Fixture f;
        f.conv.reg[0].stuckAtStart = 3;
        uint16_t nested            = 0U;
        f.conv.hook                = [&](size_t const instance, int const s) {
            if (s == 3)
            {
                (void)f.isrConvert(instance, 31U, nested);
            }
        };
        uint16_t v = 0U;
        CHECK(f.access.convert(0U, 7U, v) == bsp::BSP_ERROR);
        CHECK(nested == code(0U, 31U)); // the ISR still got its value
        CHECK(f.conv.restarts == 1);
        CHECK(f.conv.restartTimeouts == 1);
        CHECK(f.conv.pollTimeouts == 1);
        CHECK(f.lock.depth == 0);
        CHECK(f.lock.unlocks == 1);
    }
}

void testPollLimitArithmetic()
{
    // Profile A: 12 bit, SMPLTS 41, no averaging, ADC 48 MHz, bus 40 MHz, core 80 MHz.
    {
        constexpr uint32_t clocks = ::adc::sarConversionClocks(42U, 1U, 28U, 5U, 1U);
        CHECK(clocks == 76U);
        constexpr uint32_t cycles = ::adc::pollCyclesForClocks(clocks, 48000000U, 80000000U)
                                    + ::adc::pollCyclesForClocks(5U, 40000000U, 80000000U);
        CHECK(cycles == 137U); // ceil(76 * 80 / 48) = 127, plus ceil(5 * 80 / 40) = 10
        constexpr uint32_t limit = ::adc::pollLimit(cycles, 16U);
        CHECK(limit == 2192U);
        constexpr uint32_t ns = ::adc::clocksToNs(clocks, 48000000U)
                                + ::adc::clocksToNs(5U, 40000000U);
        CHECK(ns == 1709U); // 1584 ns + 125 ns
        // The bound really outlasts the conversion by the safety factor, even for the fastest
        // conceivable loop of one iteration per core clock. ns is rounded up per term, so allow
        // the factor times the two rounding errors.
        CHECK(
            ((static_cast<uint64_t>(limit) * 1000000000ULL / 80000000ULL) + (16ULL * 2ULL))
            >= (16ULL * ns));
    }
    // Profile B (own test profile, nothing of the eval board): 10 bit, SMPLTS 11, 4 samples
    // averaged, ADC clock 12 MHz (ADIV 4 of a 48 MHz source), bus 24 MHz, core 48 MHz.
    {
        constexpr uint32_t clocks = ::adc::sarConversionClocks(12U, 1U, 24U, 5U, 4U);
        CHECK(clocks == 168U);
        constexpr uint32_t cycles = ::adc::pollCyclesForClocks(clocks, 12000000U, 48000000U)
                                    + ::adc::pollCyclesForClocks(20U, 24000000U, 48000000U);
        CHECK(cycles == 712U); // 672 + 40
        constexpr uint32_t limit = ::adc::pollLimit(cycles, 16U);
        CHECK(limit == 11392U);
        constexpr uint32_t ns = ::adc::clocksToNs(clocks, 12000000U)
                                + ::adc::clocksToNs(20U, 24000000U);
        CHECK(ns == 14834U); // 14000 ns + 834 ns
        CHECK(
            ((static_cast<uint64_t>(limit) * 1000000000ULL / 48000000ULL) + (16ULL * 2ULL))
            >= (16ULL * ns));
    }
    // A slower converter needs a larger bound, a faster core too
    CHECK(
        ::adc::pollCyclesForClocks(76U, 24000000U, 80000000U)
        > ::adc::pollCyclesForClocks(76U, 48000000U, 80000000U));
    CHECK(
        ::adc::pollCyclesForClocks(76U, 48000000U, 160000000U)
        > ::adc::pollCyclesForClocks(76U, 48000000U, 80000000U));
    // averaging: 0 and 1 mean one conversion, 32 samples cost 32 times as much
    CHECK(::adc::sarConversionClocks(42U, 1U, 28U, 5U, 0U) == 76U);
    CHECK(::adc::sarConversionClocks(42U, 1U, 28U, 5U, 32U) == 2432U);
    // rounding up, never 0, saturating
    CHECK(::adc::pollCyclesForClocks(1U, 48000000U, 80000000U) == 2U);
    CHECK(::adc::pollCyclesForClocks(1U, 80000000U, 8000000U) == 1U);
    CHECK(::adc::pollCyclesForClocks(76U, 0U, 80000000U) == UINT32_MAX); // unknown ADC clock
    CHECK(::adc::clocksToNs(76U, 0U) == UINT32_MAX);
    CHECK(::adc::pollLimit(0U, 16U) == 1U);
    CHECK(::adc::pollLimit(137U, 0U) == 137U); // factor 0 is treated as 1
    CHECK(::adc::pollLimit(UINT32_MAX / 2U, 16U) == UINT32_MAX);
}

/* ---------------------------------------------------------------------------------------------
 * Part 2: threads
 * ------------------------------------------------------------------------------------------- */

std::recursive_mutex cpu; // one register step at a time; exclusive section holds it throughout
thread_local bool isTaskThread = false;

struct ThreadConverter
{
    AdcRegisters reg[INSTANCES];
    std::atomic<int> restarts{0};

    bsp::BspReturnCode convert(size_t const instance, uint8_t const input, uint16_t& value)
    {
        AdcRegisters& a = reg[instance];
        {
            std::lock_guard<std::recursive_mutex> const g(cpu);
            (void)a.readR();
        }
        std::this_thread::yield();
        {
            std::lock_guard<std::recursive_mutex> const g(cpu);
            a.writeSc1(input);
        }
        int polls = 0;
        for (;;)
        {
            std::this_thread::yield();
            std::lock_guard<std::recursive_mutex> const g(cpu);
            if (a.pollCoco(instance))
            {
                break;
            }
            if (++polls > 100000)
            {
                return bsp::BSP_ERROR;
            }
        }
        std::this_thread::yield();
        std::lock_guard<std::recursive_mutex> const g(cpu);
        value = a.readR();
        return bsp::BSP_OK;
    }

    void restart(size_t const instance, uint8_t const input)
    {
        ++restarts;
        std::lock_guard<std::recursive_mutex> const g(cpu);
        reg[instance].writeSc1(input);
        while (!reg[instance].pollCoco(instance)) {}
    }
};

struct ThreadLock
{
    using State = int;

    std::mutex m[INSTANCES];

    bool canBlock() const { return isTaskThread; }

    bool tryLock(size_t const i) { return m[i].try_lock(); }

    bool lock(size_t const i)
    {
        m[i].lock();
        return true;
    }

    void unlock(size_t const i) { m[i].unlock(); }

    State enterExclusive()
    {
        cpu.lock();
        return 0;
    }

    void exitExclusive(State) { cpu.unlock(); }

    uint32_t nowNs() const { return 0U; }
};

void testThreads()
{
    ThreadLock lock;
    ThreadConverter conv;
    adc::AdcAccess<ThreadLock, ThreadConverter, INSTANCES> access(lock, conv);
    std::atomic<int> mismatches{0};
    std::atomic<int> errors{0};
    constexpr int PER_THREAD = 4000;

    auto worker = [&](bool const task, unsigned const seed) {
        isTaskThread = task;
        std::mt19937 rng(seed);
        for (int i = 0; i < PER_THREAD; ++i)
        {
            size_t const instance = rng() % INSTANCES;
            uint8_t const input   = static_cast<uint8_t>(rng() % 32U);
            uint16_t v            = 0U;
            if (access.convert(instance, input, v) != bsp::BSP_OK)
            {
                ++errors;
            }
            else if (v != code(instance, input))
            {
                ++mismatches;
            }
        }
    };

    std::vector<std::thread> threads;
    for (unsigned t = 0U; t < 4U; ++t)
    {
        threads.emplace_back(worker, true, 11U + t);
    }
    threads.emplace_back(worker, false, 99U); // "ISR"
    for (auto& t : threads)
    {
        t.join();
    }
    auto const s = access.getStatistics();
    std::printf(
        "  threads: %u conversions, %u exclusive, %u nested, %u contended\n",
        s.conversions,
        s.exclusive,
        s.nested,
        s.contended);
    CHECK(mismatches == 0);
    CHECK(errors == 0);
    CHECK(s.conversions == 5U * PER_THREAD);
    CHECK(s.exclusive == PER_THREAD);
    CHECK(s.contended > 0U); // tasks really competed
    CHECK(s.nested > 0U);    // the "ISR" really interrupted task conversions
    CHECK(s.nested == static_cast<uint32_t>(conv.restarts.load()));
}

} // namespace

int main()
{
    testTaskConversion();
    testExclusiveConversion();
    testNestedAtEveryStep();
    testNestedOtherInstanceAndTwice();
    testContention();
    testLockTimeout();
    testErrors();
    testStuckConverterGivesUp();
    testPollLimitArithmetic();
    testThreads();
    std::printf("%d checks, %d failures\n", checks.load(), failures.load());
    return (failures == 0) ? 0 : 1;
}
