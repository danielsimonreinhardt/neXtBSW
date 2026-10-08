/* SYS-139 (decision C2 (a), doc/safety/partner-and-degraded.md 5.4 and 5.7): host test of the hold of an active short
 * in the family port - the enable adapter, the PWM inhibit of the debug halt guard and the group lock write what the
 * walks write, instead of taking the short away.
 *
 * Real code on a register model, compiled as a project with an active short builds it (NX_SAFE_OUTPUT_SELECT):
 *   - the extended table, the selection words and the selection service (hardFaultHandler/src/SafeOutputSelection.cpp
 *     with the table of hardFaultHandler/test/host/selectStubs: entry 0 FTM0 SWOCTRL of group 1, the enable on PTC11
 *     bound to it), the S1 latch and safety::SafeState (safeIo) with the selection-aware walk,
 *   - integration::SafeStateHold, DebugHaltGuard, FtmPwmInhibit (with the stub FTM of shortHoldStubs, whose output
 *     control is the model's register of the table entry), ShortHold.h,
 *   - gd::Tmc6200 with Tmc6200PowerStageOutput, attached as Integration.cpp attaches a bound gate driver: the lock
 *     shortHold::lock(<S1 lock of the hold>, enabled) and the kept enable shortHold::keepEnable(enabled).
 * The gate driver model: a store of bit 11 to PTC PCOR drives DRV_EN low (the walk), the driver writes it through its
 * I/O; the driver runs every 10 ms, the hold and the guard every 1 ms, and the application asks for "enable" every
 * driver cycle.
 *
 * What it shows:
 *   - freewheel selected: S1 exactly as before (DRV_EN low, output control freewheel, enable refused);
 *   - short selected, driver enabled: S1 keeps DRV_EN high and the short pattern for 10 s of cycles, no write of DRV_EN
 *     high, no ENABLE_MISMATCH, every output reported held; the debug halt guard's inhibit (on the entry into S1 and
 *     after a halt) writes the short pattern, not freewheel, and leaves DRV_EN on;
 *   - short selected, driver off at S1: stays off, enable() refused - nothing is switched on in S1;
 *   - the short removed during S1 (bridge fault, hardware path), the timer's clock gated, a corrupted word: freewheel
 *     and DRV_EN low within one poll, the driver locked;
 *   - after S1 is left nothing is switched on; the guard releases the PWM only after an explicit enable;
 *   - the group lock of SYS-123 (release of a locked group) writes the selected value.
 * Meaningfulness: test_short_hold_mutations.py.
 *
 * Build and run (zig c++, no hardware): tools/test -k short-hold
 */

#include <cstdint>
#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------ register model of the table */

namespace test_registers
{
volatile uint32_t* reg(uint32_t address);
void settle();
} // namespace test_registers

#define NX_SAFE_OUTPUT_REG(address) (::test_registers::reg(address))

#include "../../../../../../hardFaultHandler/src/SafeOutputSelection.cpp" // NOLINT: selection service, table
#include "../../../../../../hardFaultHandler/src/SafeStateLatch.cpp"      // NOLINT: latch of S1
#include "../../../../../safety/safeIo/src/safeIo/SafeState.cpp"         // NOLINT: S1

#include "DebugHaltGuard.h"
#include "FtmPwmInhibit.h"
#include "SafeStateHold.h"
#include "ShortHold.h"
#include "Tmc6200PowerStageOutput.h"

namespace bios
{
Io::PinConfiguration Io::configuration[Io::NUMBER_OF_INPUTS_AND_OUTPUTS] = {};
int Io::setCalls                                                         = 0;
std::string* Io::trace                                                   = nullptr;
} // namespace bios

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

constexpr uint32_t FTM0_SWOCTRL = 0x40038094U;
constexpr uint32_t FTM3_SWOCTRL = 0x40026094U;
constexpr uint32_t PTC_PCOR     = 0x400FF088U;
constexpr uint32_t PTD_PSOR     = 0x400FF0C4U;
constexpr uint32_t PTB_PCOR     = 0x400FF048U;
constexpr uint32_t PTE_PCOR     = 0x400FF108U;
constexpr uint32_t PCC_FTM0     = 0x40065060U;
constexpr uint32_t CGC          = 0x40000000U;
constexpr uint32_t DRV_EN_BIT   = 1UL << 11U;

constexpr uint32_t FREEWHEEL  = 0x3FU;   // all six channels forced inactive
constexpr uint32_t SHORT_LOW  = 0x2A3FU; // + CHnOCV of the low sides 1/3/5 (selectStubs)
constexpr uint32_t SHORT_HIGH = 0x153FU;

bool drvEnPin = false; // DRV_EN of the gate driver model
bool locked   = false; // SYS-123 group lock (the supervisor's latch on the target)
} // namespace

namespace bios
{
namespace ftmPwm
{
bool groupLocked() { return locked; }
} // namespace ftmPwm
} // namespace bios

namespace test_registers
{
namespace
{
struct Register
{
    uint32_t address;
    uint32_t value;
};

Register file[] = {
    {FTM0_SWOCTRL, 0U},     {FTM3_SWOCTRL, 0U},  {PTC_PCOR, 0U},       {PTD_PSOR, 0U},       {PTB_PCOR, 0U},
    {PTE_PCOR, 0U},         {PCC_FTM0, CGC},     {0x40065098U, CGC},   {0x40065120U, CGC},   {0x40065124U, CGC},
    {0x40065128U, CGC},     {0x4006512CU, 0U},
};
uint32_t sink = 0U;
} // namespace

int unknownAccesses = 0;

/* A store becomes visible at the next access: PCOR bit 11 drives DRV_EN low and reads 0 again. */
void settle()
{
    if ((file[2].value & DRV_EN_BIT) != 0U)
    {
        drvEnPin = false;
    }
    file[2].value = 0U;
}

volatile uint32_t* reg(uint32_t const address)
{
    settle();
    for (Register& r : file)
    {
        if (r.address == address)
        {
            return &r.value;
        }
    }
    ++unknownAccesses;
    return &sink;
}

uint32_t& at(uint32_t const address)
{
    settle();
    for (Register& r : file)
    {
        if (r.address == address)
        {
            return r.value;
        }
    }
    return sink;
}
} // namespace test_registers

using test_registers::at;

namespace
{
/* TMC6200 register file with IOIN DRV_EN from the pin (as SafeStateHoldHostTest.cpp). */
class FakeTmc6200
: public ::gd::ITmc6200Spi
, public ::gd::ITmc6200Io
{
public:
    uint32_t reg[128];
    int enableHighWrites = 0;

    FakeTmc6200()
    {
        std::memset(reg, 0, sizeof(reg));
        reg[TMC6200_REG_GCONF] = TMC6200_GCONF_RESET;
        reg[TMC6200_REG_IOIN]  = static_cast<uint32_t>(TMC6200_VERSION) << 24U;
    }

    bool transfer(uint64_t const tx, uint64_t& rx) override
    {
        ::test_registers::settle();
        uint8_t const addressByte = static_cast<uint8_t>(tx >> 32U);
        uint8_t const address     = addressByte & TMC6200_ADDRESS_MASK;
        uint32_t const data       = static_cast<uint32_t>(tx);
        if ((addressByte & TMC6200_WRITE_FLAG) != 0U)
        {
            reg[address] = (address == TMC6200_REG_GSTAT) ? (reg[address] & ~data) : data;
            rx           = 0U;
            return true;
        }
        uint32_t value = reg[address];
        if (address == TMC6200_REG_IOIN)
        {
            value = (value & ~TMC6200_IOIN_DRV_EN_MASK) | (drvEnPin ? TMC6200_IOIN_DRV_EN_MASK : 0U);
        }
        rx = value;
        return true;
    }

    bool setDriverEnable(bool const on) override
    {
        ::test_registers::settle();
        if (on)
        {
            ++enableHighWrites;
        }
        drvEnPin = on;
        return true;
    }

    bool readFault(bool& active) override
    {
        active = false;
        return true;
    }
};

::gd::Tmc6200Config const GD_CONFIG = ::gd::keepAllConfig();

using GdStatus = ::gd::Tmc6200::Status;
using GdState  = ::gd::Tmc6200::State;

bool latched() { return ::nxSafeStateIsLatched() != 0; }

void walk() { ::nxSafeOutputsSwitchOff(); }

uint32_t entries() { return ::nxSafeStateEntryCount(); }

struct Bench;
Bench* bench = nullptr;

bool gdLock();
bool gdKeep();

struct Bench
{
    FakeTmc6200 dev;
    ::gd::Tmc6200 driver{dev, dev, GD_CONFIG}; // the driver keeps a reference to its configuration
    ::integration::Tmc6200PowerStageOutput gdOutput;
    ::integration::IPowerStageOutput* outputs[1] = {&gdOutput};
    ::integration::FtmPwmInhibit inhibit;
    ::integration::DebugHaltGuard guard{outputs, 1U, &inhibit};
    ::integration::SafeStateHold hold{outputs, 1U, &latched, &walk, &entries, guard};
    uint32_t nowMs    = 0U;
    bool appEnables   = true;
    int heldBadMs     = 0; // ms in S1 with DRV_EN or the pattern not as expected
    int unlockedPolls = 0;

    Bench()
    {
        bench              = this;
        ::nxSafeStateLatch = 0U; // a reset
        ::nxSafeStateEntries = 0U;
        for (uint32_t g = 0U; g < NX_SAFE_STATE_GROUP_COUNT; ++g)
        {
            ::nxSafeStateSelection[g] = 0U;
            shortBlocked[g]           = 0U;
        }
        drvEnPin          = false;
        locked            = false;
        at(FTM0_SWOCTRL)  = 0U; // PWM running
        at(PCC_FTM0)      = CGC;
        gdOutput.attach(driver, &gdLock, &gdKeep); // as Integration.cpp for a bound gate driver
        CHECK(driver.init(0U) == GdStatus::OK);
    }

    ~Bench() { bench = nullptr; }

    Bench(Bench const&)            = delete;
    Bench& operator=(Bench const&) = delete;

    void enable()
    {
        CHECK(driver.enable() == GdStatus::OK);
        CHECK(drvEnPin);
    }

    void ms()
    {
        ++nowMs;
        if ((nowMs % 10U) == 0U)
        {
            driver.cyclic(nowMs);
            if (appEnables)
            {
                (void)driver.enable();
            }
        }
        (void)hold.cyclic();
        if (hold.isHeld() && (hold.getUnlockedOutputs() != 0U))
        {
            ++unlockedPolls;
        }
        (void)guard.cyclic(0U);
        ::test_registers::settle();
    }

    void run(uint32_t const count, bool const expectDrvEn, uint32_t const expectPattern)
    {
        for (uint32_t i = 0U; i < count; ++i)
        {
            ms();
            if ((drvEnPin != expectDrvEn) || (at(FTM0_SWOCTRL) != expectPattern))
            {
                ++heldBadMs;
            }
        }
    }
};

// Integration.cpp GATE_DRIVER_SHORT_HOLD with the S1 lock of the hold as base lock (a project without SD mapping)
bool gdLock()
{
    return ::integration::shortHold::lock(bench->hold.isLocking(), bench->driver.isEnabled());
}

bool gdKeep() { return ::integration::shortHold::keepEnable(bench->driver.isEnabled()); }

void select(uint32_t const state) { CHECK(::nxSafeStateSelect(0U, state) == NX_SAFE_STATE_OK); }

/* ------------------------------------------------------------------------------------------------ S1 */

void testFreewheelAsBefore()
{
    std::printf("S1 with freewheel selected: enable off, output control freewheel, enable refused\n");
    Bench b;
    b.enable();
    b.run(5U, true, 0U);
    ::safety::SafeState::enterSafeState();
    ::test_registers::settle();
    CHECK(!drvEnPin);
    CHECK(at(FTM0_SWOCTRL) == FREEWHEEL);
    int const writes = b.dev.enableHighWrites;
    b.run(1000U, false, FREEWHEEL);
    CHECK(b.heldBadMs == 0);
    CHECK(b.dev.enableHighWrites == writes);
    CHECK(b.driver.enable() == GdStatus::LOCKED);
    CHECK(b.unlockedPolls == 0);
    CHECK((b.driver.getLatchedFaults() & ::gd::fault::ENABLE_MISMATCH) == 0U);
}

void testShortHeldInS1(uint32_t const state, uint32_t const pattern)
{
    std::printf("S1 with short %u selected: enable and pattern held\n", static_cast<unsigned>(state));
    Bench b;
    b.enable();
    b.run(5U, true, 0U);
    select(state);
    ::safety::SafeState::enterSafeState();
    ::test_registers::settle();
    CHECK(drvEnPin); // the walk left the enable as it is
    CHECK(at(FTM0_SWOCTRL) == pattern);
    int const writes = b.dev.enableHighWrites;
    b.run(10000U, true, pattern); // 1000 driver cycles, the hold and the guard every ms
    CHECK(b.heldBadMs == 0);
    CHECK(b.dev.enableHighWrites == writes); // held, never switched on again
    CHECK(b.driver.getState() == GdState::ENABLED);
    CHECK(b.driver.getLatchedFaults() == 0U);
    CHECK(b.unlockedPolls == 0); // the adapter reports the driver as held
    CHECK(b.hold.isHeld());
    CHECK(b.guard.isInhibited());
}

void testDriverOffStaysOff()
{
    std::printf("S1 with short selected, driver off: nothing is switched on\n");
    Bench b;
    b.appEnables = true; // the application insists in every cycle
    select(NX_SAFE_STATE_SHORT_LOW);
    ::safety::SafeState::enterSafeState();
    b.run(2000U, false, SHORT_LOW);
    CHECK(b.heldBadMs == 0);
    CHECK(b.dev.enableHighWrites == 0);
    CHECK(b.driver.enable() == GdStatus::LOCKED);
    CHECK(b.driver.getState() == GdState::DISABLED);
}

void testShortRemovedDuringS1()
{
    std::printf("S1 in a short, then the short removed: freewheel and enable off within one poll\n");
    Bench b;
    b.enable();
    select(NX_SAFE_STATE_SHORT_LOW);
    ::safety::SafeState::enterSafeState();
    b.run(33U, true, SHORT_LOW);
    ::nxSafeStateBlockShort(0U); // SYS-140 bridge fault
    b.ms();
    CHECK(!drvEnPin);
    CHECK(at(FTM0_SWOCTRL) == FREEWHEEL);
    b.run(1000U, false, FREEWHEEL);
    CHECK(b.heldBadMs == 0);
    CHECK(b.driver.getState() == GdState::DISABLED);
    CHECK(b.driver.enable() == GdStatus::LOCKED);
    CHECK((b.driver.getLatchedFaults() & ::gd::fault::ENABLE_MISMATCH) == 0U);
    CHECK(::nxSafeStateSelect(0U, NX_SAFE_STATE_SHORT_LOW) == NX_SAFE_STATE_ERROR_BLOCKED);

    std::printf("hardware threshold path removes the short of the group (removeForHardwarePath)\n");
    Bench h;
    select(NX_SAFE_STATE_SHORT_HIGH);
    ::integration::shortHold::removeForHardwarePath();
    CHECK(::nxSafeStateAllowed(0U) == NX_SAFE_STATE_BIT(NX_SAFE_STATE_FREEWHEEL));
    CHECK(!::integration::shortHold::groupInShort());
    CHECK(::nxSafeStateAllowed(1U) == 0x3U); // the other group keeps its shorts
}

void testClockGatedAndCorruptedWord()
{
    std::printf("timer clock gated / corrupted word: freewheel, enable off\n");
    {
        Bench b;
        b.enable();
        select(NX_SAFE_STATE_SHORT_LOW);
        ::safety::SafeState::enterSafeState();
        b.run(15U, true, SHORT_LOW);
        at(PCC_FTM0) = 0U; // the walk skips the timer entry, the enable goes off with the group's gate
        b.ms();
        CHECK(!drvEnPin);
        CHECK(!::integration::shortHold::groupInShort());
        b.run(100U, false, SHORT_LOW); // the register keeps what was there; no clock, no switching
        CHECK(b.heldBadMs == 0);
        CHECK(b.driver.enable() == GdStatus::LOCKED);
    }
    {
        Bench b;
        b.enable();
        select(NX_SAFE_STATE_SHORT_LOW);
        ::safety::SafeState::enterSafeState();
        b.run(15U, true, SHORT_LOW);
        ::nxSafeStateSelection[0] = NX_SAFE_STATE_WORD_SHORT_LOW ^ 0x100U; // one bit flipped
        b.ms();
        CHECK(!drvEnPin);
        CHECK(at(FTM0_SWOCTRL) == FREEWHEEL);
        b.run(100U, false, FREEWHEEL);
        CHECK(b.heldBadMs == 0);
    }
}

/* -------------------------------------------------------------------------------------- halt and return */

void testDebugHaltInShort()
{
    std::printf("debug halt with a short selected: pattern, enable kept, release only after an explicit enable\n");
    Bench b;
    b.enable();
    b.appEnables = false;
    b.run(5U, true, 0U);
    select(NX_SAFE_STATE_SHORT_HIGH);
    CHECK(b.guard.cyclic(::integration::DebugHaltGuard::HALT_TIMER) == ::integration::DebugHaltGuard::Event::HALT);
    CHECK(drvEnPin);
    CHECK(at(FTM0_SWOCTRL) == SHORT_HIGH);
    b.run(100U, true, SHORT_HIGH);
    CHECK(b.heldBadMs == 0);
    CHECK(b.driver.getLatchedFaults() == 0U);
    CHECK(b.guard.isInhibited());
    CHECK(b.driver.enable() == GdStatus::OK); // already enabled: no explicit enable, nothing released
    b.run(20U, true, SHORT_HIGH);
    CHECK(b.guard.isInhibited());
    CHECK(b.driver.disable() == GdStatus::OK);
    ::nxSafeStateSelect(0U, NX_SAFE_STATE_FREEWHEEL);
    b.enable();
    CHECK(b.guard.cyclic(0U) == ::integration::DebugHaltGuard::Event::RELEASED);
    CHECK(at(FTM0_SWOCTRL) == 0U);

    std::printf("debug halt with freewheel: enable off as before\n");
    Bench f;
    f.enable();
    f.appEnables = false;
    CHECK(f.guard.cyclic(::integration::DebugHaltGuard::HALT_TIMER) == ::integration::DebugHaltGuard::Event::HALT);
    ::test_registers::settle();
    CHECK(!drvEnPin);
    CHECK(at(FTM0_SWOCTRL) == FREEWHEEL);
}

void testLeavingS1SwitchesNothingOn()
{
    std::printf("S1 left in a short: nothing switched on, PWM released only after an explicit enable\n");
    Bench b;
    b.enable();
    b.appEnables = false;
    select(NX_SAFE_STATE_SHORT_LOW);
    ::safety::SafeState::enterSafeState();
    b.run(50U, true, SHORT_LOW);
    int const writes = b.dev.enableHighWrites;
    ::safety::SafeState::leaveSafeState();
    b.run(200U, true, SHORT_LOW); // the short stays until an explicit enable of the output
    CHECK(b.heldBadMs == 0);
    CHECK(b.dev.enableHighWrites == writes);
    CHECK(b.guard.isInhibited());
    CHECK(b.driver.disable() == GdStatus::OK);
    b.enable();
    b.ms();
    CHECK(!b.guard.isInhibited());
    CHECK(at(FTM0_SWOCTRL) == 0U);
}

/* ------------------------------------------------------------------------------------- group lock, inhibit */

void testGroupLockWritesTheSelection()
{
    std::printf("group lock (SYS-123) and inhibit write the selected output control\n");
    Bench b;
    ::integration::FtmPwmInhibit inhibit;
    locked = true;
    inhibit.release();
    CHECK(at(FTM0_SWOCTRL) == FREEWHEEL);
    select(NX_SAFE_STATE_SHORT_LOW);
    inhibit.release();
    CHECK(at(FTM0_SWOCTRL) == SHORT_LOW);
    select(NX_SAFE_STATE_SHORT_HIGH);
    inhibit.inhibit();
    CHECK(at(FTM0_SWOCTRL) == SHORT_HIGH);
    CHECK(::integration::FtmPwmInhibit::forcedValue() == SHORT_HIGH);
    locked = false;
    inhibit.release();
    CHECK(at(FTM0_SWOCTRL) == 0U);
    // the value is taken only from the entry of that register - any other register gets freewheel
    CHECK(::integration::shortHold::outputControl(::test_registers::reg(FTM3_SWOCTRL), 0x55U) == 0x55U);
    CHECK(::integration::shortHold::outputControl(::test_registers::reg(FTM0_SWOCTRL), 0x55U) == SHORT_HIGH);
}
} // namespace

int main()
{
    testFreewheelAsBefore();
    testShortHeldInS1(NX_SAFE_STATE_SHORT_LOW, SHORT_LOW);
    testShortHeldInS1(NX_SAFE_STATE_SHORT_HIGH, SHORT_HIGH);
    testDriverOffStaysOff();
    testShortRemovedDuringS1();
    testClockGatedAndCorruptedWord();
    testDebugHaltInShort();
    testLeavingS1SwitchesNothingOn();
    testGroupLockWritesTheSelection();
    CHECK(::test_registers::unknownAccesses == 0);
    std::printf("short hold (SYS-139): %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
