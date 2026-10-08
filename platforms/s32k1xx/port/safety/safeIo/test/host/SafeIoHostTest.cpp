/* SYS-42 (finding F15 of doc/safety/fmea.md, SM-21 and SM-22) and SYS-24 (finding F4): host test of the register lock
 * and the register check of SafeIo with the real code of the target - SafeIo.cpp, the register list engine
 * (safeIo/RegisterCheck.h) with the real list definition (SafeIoRegisterList.cpp), the real bspIo driver of OpenBSW
 * (external/openbsw/platforms/s32k1xx/bsp/bspIo/src/io/Io.cpp) and the real SafeSupervisor (reaction) - on a register
 * model of the PORT, GPIO, PCC and FTM blocks (ioStubs/mcu/mcu.h: the PCR lock of the silicon, stuck bits, lost
 * writes) with the pin table and the register list of a test profile (ioStubs/bsp/io/io/ioConfiguration.h,
 * ioStubs/bsp/safeIo/safeIoConfiguration.h - not the eval board). The safe state itself is the supervisor's host stub
 * (S enter, L leave); its own tests are safe-state and safe-state-hold.
 *
 * What it shows:
 * - SM-21, start-up: after the BSP configured the pins and the PWM timer, init() locks the pin control register of
 *   every pin of the list - the PWM pair, the safe_off output and the role pin SAFETY_LOCKED_PIN - and no other; a
 *   later write through the driver cannot change them. Every deviation of a locked register from the list (another
 *   function, MUX 0 ... 7; drive strength or pull; locked with a wrong value before; a MUX bit stuck; SYS-42: a lock
 *   that did not take - write lost, LK stuck at 0) is LOCK_REGISTER_ERROR with class and index of the entry as
 *   context, S1 + S2 (safe state, reset). A checked-only register (GPIO, PCC, FTM) is not judged by the start-up
 *   read-back but by the first cycle.
 * - SM-22, every cycle: every entry of the list is read once per cycle. A deviation of any of them - direction or
 *   input enable of a GPIO pin of the list, a clock gate of its peripherals, the static FTM configuration (clock
 *   and prescaler, period, combine/dead time enable, polarity, inversion, debug mode, dead time), a locked PCR
 *   whose bits are stuck after start - is CHECK_REGISTER_ERROR with the context of the entry (SYS-169, it
 *   was false before: the log line names class and entry): S1 without reset, reported once,
 *   latched until the next init() (drifting back does not release the safe state); safeIoRegisterFault() names
 *   class and index. Registers the drivers write during operation (OUTMASK, SWOCTRL, CnV) are not judged.
 * - Demonstration of the OpenBSW demo (unchanged): the direction of SAFETY_TEST is read back. A pin turned into an
 *   output (PDDR), an input disabled (PIDR) - by a register fault or by a driver call - is CHECK_REGISTER_ERROR with
 *   context false, once per change; the return to input releases the indicator (context true) - unless an entry of
 *   the register list deviated. Pins on the same port or with the same pin number on another port do not report.
 *
 * - SYS-138 (review K6): the read-back for the supervisor (safeSupervisor/SafeStateReadback.h, real SafeSupervisor.cpp
 *   with NX_SAFE_SUPERVISOR_READBACK) - nothing judged before init(); every entry resp. only the entries of a degraded
 *   mask; a gated clock is not read and not confirmed (also in SafeIo::cyclic); the hold of S1 holds while S1 reads back
 *   and ends in LOCK_REGISTER_ERROR (S1 + S2) when it does not; the S1 + S2 sequence writes its console line only for
 *   a confirmed S1. The supervisor's stub of the safe state acts on the register model here (test_io::walk).
 *
 * - SYS-153 (rest of H9): the low-voltage detect reset of the family port, PMC_LVDSC1 (class SUPERVISION, a byte
 *   register of the model; an access of the wrong width acts as a bus error). Written once by boardInit() with its real
 *   code (pmc/LowVoltageDetectReset.h), read with 8 bits in every cycle and never written by SafeIo; LVDRE cleared or
 *   LVDIE set is CHECK_REGISTER_ERROR (S1 without reset), latched; LVDF ignored; the list judges exactly what the rule
 *   lowVoltageDetectResetOn() of the family port accepts, for all 256 values; a lost start write is seen by the first
 *   cycle. The engine's static check refuses widths other than 32/8 and a byte entry with bits beyond its byte.
 *
 * Built-in faults: test_safe_io_mutations.py applies deliberate faults to SafeIo.cpp and RegisterCheck.h and expects
 * this test to fail for each of them.
 *
 * Build and run (zig c++, no hardware; tools/test entry safe-io):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -DPLATFORM_SUPPORT_IO \
 *       -Iplatforms/s32k1xx/port/safety/safeIo/test/host/ioStubs \
 *       -Iecu/safety/safeSupervisor/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/safety/safeIo/include \
 *       -Iecu/safety/safeSupervisor/include \
 *       -isystem external/openbsw/platforms/s32k1xx/bsp/bspIo/include \
 *       -isystem external/openbsw/libs/bsw/bsp/include \
 *       -isystem external/openbsw/libs/safety/safeMonitor/include \
 *       platforms/s32k1xx/port/safety/safeIo/test/host/SafeIoHostTest.cpp \
 *       platforms/s32k1xx/port/safety/safeIo/src/safeIo/SafeIo.cpp -o build/host/safeio.exe
 *   build/host/safeio.exe
 */

#include <cstdint>
#include <cstdio>
#include <map>
#include <string>
#include <vector>

namespace test_io
{
void walk(); // SYS-138: what the walk of S1 does to the model (defined below)
} // namespace test_io

namespace test_trace
{
std::string trace;
int lockDepth = 0;

/* SYS-138: the supervisor's host stub of the safe state records S1 as 'S'; here S1 also acts on the register model
 * (test_io::walk), so the read-back the supervisor now does before a reset and in the hold sees what the target sees. */
void add(char const c)
{
    trace.push_back(c);
    if (c == 'S')
    {
        ::test_io::walk();
    }
}
} // namespace test_trace

namespace test_hold
{
int left = 0; // SYS-138: rounds the hold loop of the supervisor may run (0: none, as before)

bool holding()
{
    if (left <= 0)
    {
        return false;
    }
    --left;
    return true;
}
} // namespace test_hold

// SYS-138: the supervisor reads back the safe state through the real SafeIo.cpp of this test
#define NX_SAFE_SUPERVISOR_READBACK               1
#define NX_SAFE_SUPERVISOR_HOLDING()              (::test_hold::holding())
#define NX_SAFE_SUPERVISOR_CONSOLE(format, event) ((void)(format), (void)(event), ::test_trace::add('P'))

extern "C" void softwareSystemReset(void) { ::test_trace::add('R'); }

#include "../../../../../../../ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp" // NOLINT

/* The driver of OpenBSW is compiled into this unit: it is third-party code (not held to -Wconversion here), and the
 * test must see its register accesses as they are on the target. */
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wconversion"
#pragma clang diagnostic ignored "-Wsign-conversion"
#include "../../../../../../../external/openbsw/platforms/s32k1xx/bsp/bspIo/src/io/Io.cpp" // NOLINT
#pragma clang diagnostic pop

// SYS-24: the real definition of the register list, from the generated header of the test profile
#include "../../src/registerList/SafeIoRegisterList.cpp" // NOLINT
// SYS-30: the real definition of the read-back list of the safe outputs table (ioStubs/bsp/safeOutputs)
#include "../../src/registerList/SafeOutputReadbackList.cpp" // NOLINT

// SYS-30: the latch of S1 (hardFaultHandler/safeOutputs.h; on the target in the handler library). The supervisor's
// host stub of the safe state does not set it; the read-back tests set and clear it themselves.
#include <hardFaultHandler/safeOutputs.h>

extern "C"
{
volatile uint32_t nxSafeStateLatch;
volatile uint32_t nxSafeStateEntries;
}

#include <safeIo/SafeIo.h>
#include <safeIo/SafeIoRegisters.h>

// SYS-153: the family port's start value and check rule of PMC_LVDSC1 (SYS-125, header only)
#include "../../../../main/include/pmc/LowVoltageDetectReset.h" // NOLINT

namespace safety
{
SafetyEventMailbox rteTimingMailbox;
} // namespace safety

namespace test_io
{
PORT_Type ports[PORT_COUNT];
GPIO_Type gpios[PORT_COUNT];

/** A register of the model without special behaviour (PCC, FTM): value, stuck bits, lost writes. */
struct Plain
{
    uint32_t value       = 0U;
    uint32_t stuckAtOne  = 0U;
    uint32_t stuckAtZero = 0U;
    bool dropWrites      = false;
};

std::map<uintptr_t, Plain> plain;
std::vector<uintptr_t> unknown; // addresses the list named that the model does not know
int reads = 0;

/** SYS-153: a byte register of the model (PMC_LVDSC1): only an 8-bit access reaches it. */
struct Byte
{
    uint8_t value       = 0U;
    uint8_t stuckAtOne  = 0U;
    uint8_t stuckAtZero = 0U;
    bool dropWrites     = false;
    int writes          = 0;
};

std::map<uintptr_t, Byte> bytes;
std::vector<uintptr_t> wrongWidth; // SYS-153: 32-bit accesses to a byte register, 8-bit accesses to a word

uintptr_t lvdsc1() { return IP_PMC_BASE + offsetof(PMC_Type, LVDSC1); }

Byte& byteReg(uintptr_t const address) { return bytes[address]; }
std::map<uintptr_t, int> readsOf; // SYS-138: reads per address (a gated register must not be read)
bool enableHeldHigh = false;      // SYS-138: something else holds the enable pin PTA7 high - the walk does not show

uintptr_t pcc(uint32_t const index) { return IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4U * index; }

uintptr_t ftm1(size_t const offset) { return IP_FTM1_BASE + offset; }

Plain& reg(uintptr_t const address) { return plain[address]; }
} // namespace test_io

/* The accessor of the stub safeIo/RegisterAccess.h: the addresses of the list back onto the model. */
namespace safety
{
namespace registers
{
namespace
{
test_io::Pcr* pcrAt(uintptr_t const address)
{
    if ((address >= IP_PORTA_BASE) && (address < (IP_PORTA_BASE + (test_io::PORT_COUNT * test_io::PORT_STEP))))
    {
        uintptr_t const offset = (address - IP_PORTA_BASE) % test_io::PORT_STEP;
        if ((offset < (32U * 4U)) && ((offset % 4U) == 0U))
        {
            return &test_io::ports[(address - IP_PORTA_BASE) / test_io::PORT_STEP].PCR[offset / 4U];
        }
    }
    return nullptr;
}

uint32_t* gpioAt(uintptr_t const address)
{
    if ((address >= IP_PTA_BASE) && (address < (IP_PTA_BASE + (test_io::PORT_COUNT * test_io::GPIO_STEP))))
    {
        uintptr_t const offset = (address - IP_PTA_BASE) % test_io::GPIO_STEP;
        if ((offset < sizeof(GPIO_Type)) && ((offset % 4U) == 0U))
        {
            auto* const words = reinterpret_cast<uint32_t*>(
                &test_io::gpios[(address - IP_PTA_BASE) / test_io::GPIO_STEP]);
            return &words[offset / 4U];
        }
    }
    return nullptr;
}
} // namespace

uint32_t read(uintptr_t const address)
{
    ++test_io::reads;
    ++test_io::readsOf[address];
    if (test_io::bytes.count(address) != 0U)
    {
        test_io::wrongWidth.push_back(address); // a word access to a byte register: bus error
        return 0xDEADBEEFU;
    }
    if (auto* const pcr = pcrAt(address))
    {
        return *pcr;
    }
    if (auto* const word = gpioAt(address))
    {
        return *word;
    }
    auto const it = test_io::plain.find(address);
    if (it == test_io::plain.end())
    {
        test_io::unknown.push_back(address);
        return 0xDEADBEEFU;
    }
    return (it->second.value | it->second.stuckAtOne) & ~it->second.stuckAtZero;
}

void write(uintptr_t const address, uint32_t const value)
{
    if (test_io::bytes.count(address) != 0U)
    {
        test_io::wrongWidth.push_back(address);
        return;
    }
    if (auto* const pcr = pcrAt(address))
    {
        *pcr = value;
        return;
    }
    if (auto* const word = gpioAt(address))
    {
        *word = value;
        return;
    }
    auto const it = test_io::plain.find(address);
    if (it == test_io::plain.end())
    {
        test_io::unknown.push_back(address);
        return;
    }
    if (!it->second.dropWrites)
    {
        it->second.value = value;
    }
}

uint8_t read8(uintptr_t const address)
{
    ++test_io::reads;
    ++test_io::readsOf[address];
    auto const it = test_io::bytes.find(address);
    if (it == test_io::bytes.end())
    {
        test_io::wrongWidth.push_back(address); // a byte access to a word register (or nothing): bus error
        return 0xEFU;
    }
    return static_cast<uint8_t>((it->second.value | it->second.stuckAtOne) & ~it->second.stuckAtZero);
}

void write8(uintptr_t const address, uint8_t const value)
{
    auto const it = test_io::bytes.find(address);
    if (it == test_io::bytes.end())
    {
        test_io::wrongWidth.push_back(address);
        return;
    }
    ++it->second.writes;
    if (!it->second.dropWrites)
    {
        it->second.value = value;
    }
}
} // namespace registers
} // namespace safety

namespace test_io
{
/* SYS-138: the walk of the safe outputs table of the test profile (ioStubs/bsp/safeOutputs) on the model - FTM1
 * SWOCTRL = 0x03 (a lost write stays lost), the enable pin PTA7 low (unless something holds it high) - each skipped
 * while its clock is gated, like nxSafeOutputsSwitchOff(). Directly on the model: the walk is not a read of SafeIo. */
void walk()
{
    auto const gateOn = [](uint32_t const index)
    {
        auto const it = plain.find(IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4U * index);
        return (it != plain.end()) && ((it->second.value & PCC_PCCn_CGC_MASK) != 0U);
    };
    if (gateOn(PCC_FTM1_INDEX))
    {
        Plain& swoctrl = plain[IP_FTM1_BASE + offsetof(FTM_Type, SWOCTRL)];
        if (!swoctrl.dropWrites)
        {
            swoctrl.value = 0x03U;
        }
    }
    if (gateOn(PCC_PORTA_INDEX) && !enableHeldHigh)
    {
        gpios[_PORTA_].PDIR &= ~(static_cast<uint32_t>(1U) << PA7);
    }
}
} // namespace test_io

namespace
{
using ::bios::Io;
using ::safety::SafeSupervisor;
using ::safety::registers::RegisterClass;

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

constexpr uint32_t MUX_MASK = 0x700U;
constexpr uint32_t LK       = ::test_io::PCR_LK;

// Registers of the test profile (ioStubs/bsp/io/io/ioConfiguration.h, ioStubs/bsp/safeIo/safeIoConfiguration.h)
::test_io::Pcr& rolePcr() { return ::test_io::ports[_PORTB_].PCR[PB5]; }

::test_io::Pcr& pwmHsPcr() { return ::test_io::ports[_PORTD_].PCR[PD15]; }

::test_io::Pcr& pwmLsPcr() { return ::test_io::ports[_PORTD_].PCR[PD16]; }

::test_io::Pcr& safeOffPcr() { return ::test_io::ports[_PORTA_].PCR[PA7]; }

::test_io::Pcr& neighbourPcr() { return ::test_io::ports[_PORTE_].PCR[PE8]; }

GPIO_Type& testGpio() { return ::test_io::gpios[_PORTE_]; }

constexpr uint32_t TEST_BIT = static_cast<uint32_t>(1U) << PE9;

// Index of each entry in the list of the test profile
enum Entry : uint32_t
{
    E_PWM_HS,
    E_PWM_LS,
    E_SAFE_OFF,
    E_ROLE,
    E_PTA_PDDR,
    E_PTA_PIDR,
    E_PTB_PDDR,
    E_PTB_PIDR,
    E_PCC_FTM1,
    E_PCC_PORTD,
    E_PCC_PORTA,
    E_PCC_PORTB,
    E_SC,
    E_MOD,
    E_CNTIN,
    E_COMBINE,
    E_POL,
    E_INVCTRL,
    E_CONF,
    E_PAIR0DEADTIME,
    E_PMC_LVDSC1, // SYS-153
    ENTRY_COUNT
};

static_assert(ENTRY_COUNT == NX_SAFE_IO_REGISTER_COUNT, "entries of the test profile");

constexpr uint32_t ctx(RegisterClass const c, Entry const e) { return ::safety::registers::context(c, e); }

/* The FTM values the PWM driver writes in init() for the test profile (see safeIoConfiguration.h of the stubs), plus
 * bits the list does not judge: SC.TOF, CONF.NUMTOF, and the registers the driver changes during operation. */
constexpr uint32_t FTM_SC      = 0x00030009U | 0x00000200U; // TOF set
constexpr uint32_t FTM_MOD     = 0x7CFU;
constexpr uint32_t FTM_COMBINE = 0x33U;
constexpr uint32_t FTM_POL     = 0x02U;
constexpr uint32_t FTM_CONF    = 0x40U | 0x03U; // NUMTOF 3
constexpr uint32_t FTM_PAIR0DT = 0x8AU;

/** Power-on reset: registers at their reset values, no fault, a fresh supervisor out of limp home. */
SafeSupervisor& powerOn()
{
    for (auto& port : ::test_io::ports)
    {
        for (auto& pcr : port.PCR)
        {
            pcr.reset();
            pcr.stuckAtOne  = 0U;
            pcr.stuckAtZero = 0U;
            pcr.dropWrites  = false;
        }
        port.DFER = 0U;
        port.DFCR = 0U;
        port.DFWR = 0U;
    }
    for (auto& gpio : ::test_io::gpios)
    {
        gpio = GPIO_Type{};
    }
    ::test_io::plain.clear();
    for (uint32_t index : {56U, 57U, 73U, 74U, 75U, 76U, 77U})
    {
        ::test_io::reg(::test_io::pcc(index)); // PCC words exist, clocks off (reset)
    }
    for (size_t offset = 0U; offset <= offsetof(FTM_Type, PAIR3DEADTIME); offset += 4U)
    {
        ::test_io::reg(::test_io::ftm1(offset)); // every FTM1 word exists, reset value 0
    }
    ::test_io::bytes.clear();
    ::test_io::byteReg(::test_io::lvdsc1()); // SYS-153: PMC_LVDSC1, power-on value 0 (LVDRE off)
    ::test_io::wrongWidth.clear();
    ::test_io::unknown.clear();
    ::test_io::reads = 0;
    ::test_io::readsOf.clear();
    ::test_io::enableHeldHigh = false;
    ::test_hold::left         = 0;
    ::nxSafeStateLatch        = 0U;
    ::safety::safeSupervisorConstructor.construct();
    auto& supervisor = SafeSupervisor::getInstance();
    supervisor.leaveLimpHome();
    ::test_trace::trace.clear();
    return supervisor;
}

void setPlain(uintptr_t const address, uint32_t const value) { ::test_io::reg(address).value = value; }

/** The start-up of the target before the safety task: clocks of the ports and the timer, the PWM driver
 * (ComplementaryPwm::init: FTM configuration, outputs masked) and every pin of the table through the Io driver. */
void bspStart()
{
    // SYS-153: boardInit() of the family port switches the low-voltage detect reset on (SYS-125) - its real code
    ::test_io::Byte& pmc = ::test_io::byteReg(::test_io::lvdsc1());
    if (!pmc.dropWrites)
    {
        ::platform::pmc::enableLowVoltageDetectReset(pmc.value);
    }
    pmc.writes = 0;
    for (uint32_t index : {57U, 73U, 74U, 75U, 76U, 77U})
    {
        setPlain(::test_io::pcc(index), 0xC0000000U); // PR (present) + CGC
    }
    setPlain(::test_io::ftm1(offsetof(FTM_Type, SC)), FTM_SC);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, MOD)), FTM_MOD);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, CNTIN)), 0U);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, COMBINE)), FTM_COMBINE);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, POL)), FTM_POL);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, CONF)), FTM_CONF);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, PAIR0DEADTIME)), FTM_PAIR0DT);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, DEADTIME)), FTM_PAIR0DT);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, OUTMASK)), 0x03U);
    for (uint16_t io = 0U; io < Io::NUMBER_OF_IOS; ++io)
    {
        CHECK(Io::setDefaultConfiguration(io) == ::bsp::BSP_OK);
    }
}

bool lockReported(SafeSupervisor& supervisor, uint32_t const context)
{
    // SYS-59: safe state, the SAFETY RESET console line (P), reset
    return (::test_trace::trace == "ESPR") && (supervisor.lockRegisterMonitor.getContext() == context)
           && supervisor.inLimpHomeState();
}

void cycles(::safety::SafeIo& safeIo, int const n)
{
    for (int i = 0; i < n; ++i)
    {
        safeIo.cyclic();
    }
}

// ---- the list itself -------------------------------------------------------------------------------------------

void testListOfTheProfile()
{
    CHECK(::safety::registers::safeIoRegisterCount == ENTRY_COUNT);
    CHECK(::safety::registers::wellFormed(
        ::safety::registers::safeIoRegisters, ::safety::registers::safeIoRegisterCount));
    CHECK(::safety::registers::safeIoRegisters[ENTRY_COUNT].registerClass == RegisterClass::NONE);
    CHECK(::safety::registers::safeIoRegisters[E_ROLE].registerClass == RegisterClass::PIN_FUNCTION);
    CHECK(::safety::registers::safeIoRegisters[E_PTB_PIDR].registerClass == RegisterClass::GPIO_DIRECTION);
    CHECK(::safety::registers::safeIoRegisters[E_PCC_PORTB].registerClass == RegisterClass::CLOCK_GATE);
    CHECK(::safety::registers::safeIoRegisters[E_PAIR0DEADTIME].registerClass == RegisterClass::PWM_TIMER);
    CHECK(ctx(RegisterClass::PWM_TIMER, E_POL) == 0x04000010U);
    // a malformed entry is refused by the static check of the list definition
    using ::safety::registers::WIDTH_32;
    using ::safety::registers::WIDTH_8;
    ::safety::registers::Entry const expectedOutsideMask[]
        = {{0x1000U, 0x0FU, 0x10U, 0U, 0U, RegisterClass::PWM_TIMER, WIDTH_32}};
    ::safety::registers::Entry const lockOutsideExpected[]
        = {{0x1000U, 0x8743U, 0x0100U, 0x8000U, 0x01000000U, RegisterClass::PIN_FUNCTION, WIDTH_32}};
    ::safety::registers::Entry const noClass[] = {{0x1000U, 0x0FU, 0x01U, 0U, 0U, RegisterClass::NONE, WIDTH_32}};
    CHECK(!::safety::registers::wellFormed(expectedOutsideMask, 1U));
    CHECK(!::safety::registers::wellFormed(lockOutsideExpected, 1U));
    CHECK(!::safety::registers::wellFormed(noClass, 1U));

    // SYS-153: the access width - 32 or 8 bits, a word aligned, a byte register judged and cleared in its 8 bits
    ::safety::registers::Entry const byteEntry[]
        = {{0x4007D001U, 0x30U, 0x10U, 0U, 0U, RegisterClass::SUPERVISION, WIDTH_8}};
    ::safety::registers::Entry const width16[]
        = {{0x1000U, 0x30U, 0x10U, 0U, 0U, RegisterClass::SUPERVISION, static_cast<uint8_t>(16U)}};
    ::safety::registers::Entry const width0[]
        = {{0x1000U, 0x30U, 0x10U, 0U, 0U, RegisterClass::SUPERVISION, static_cast<uint8_t>(0U)}};
    ::safety::registers::Entry const byteMaskTooWide[]
        = {{0x4007D000U, 0x130U, 0x10U, 0U, 0U, RegisterClass::SUPERVISION, WIDTH_8}};
    ::safety::registers::Entry const byteClearTooWide[]
        = {{0x4007D000U, 0x30U, 0x10U, 0x10U, 0x100U, RegisterClass::SUPERVISION, WIDTH_8}};
    ::safety::registers::Entry const wordMisaligned[]
        = {{0x1002U, 0x0FU, 0x01U, 0U, 0U, RegisterClass::PWM_TIMER, WIDTH_32}};
    CHECK(::safety::registers::wellFormed(byteEntry, 1U));
    CHECK(!::safety::registers::wellFormed(width16, 1U));
    CHECK(!::safety::registers::wellFormed(width0, 1U));
    CHECK(!::safety::registers::wellFormed(byteMaskTooWide, 1U));
    CHECK(!::safety::registers::wellFormed(byteClearTooWide, 1U));
    CHECK(!::safety::registers::wellFormed(wordMisaligned, 1U));

    // SYS-153: the entry of the low-voltage detect reset, the only byte register of the list
    ::safety::registers::Entry const& pmc = ::safety::registers::safeIoRegisters[E_PMC_LVDSC1];
    CHECK(pmc.registerClass == RegisterClass::SUPERVISION);
    CHECK(pmc.address == ::test_io::lvdsc1());
    CHECK(pmc.width == WIDTH_8);
    CHECK((pmc.mask == 0x30U) && (pmc.expected == 0x10U) && (pmc.lockBits == 0U) && (pmc.lockClear == 0U));
    for (size_t i = 0U; i < E_PMC_LVDSC1; ++i)
    {
        CHECK(::safety::registers::safeIoRegisters[i].width == WIDTH_32);
    }
    CHECK(ctx(RegisterClass::SUPERVISION, E_PMC_LVDSC1) == 0x06000014U);
    // the list judges exactly what the rule of the family port accepts (LowVoltageDetectReset.h, SYS-125), for every
    // byte value - LVDF and LVDACK ignored - and the start value of boardInit() passes
    int disagreements = 0;
    for (uint32_t value = 0U; value <= 0xFFU; ++value)
    {
        if (::safety::registers::deviates(pmc, value)
            == ::platform::pmc::lowVoltageDetectResetOn(static_cast<uint8_t>(value)))
        {
            ++disagreements;
        }
    }
    CHECK(disagreements == 0);
    CHECK(!::safety::registers::deviates(pmc, ::platform::pmc::LVDSC1_START_VALUE));
}

// ---- SM-21: register lock at start-up ------------------------------------------------------------------------

void testHealthyStartLocksTheListAndNothingElse()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    CHECK(::test_trace::trace.empty());
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::safety::safeIoRegisterFault() == 0U);
    CHECK(rolePcr() == (static_cast<uint32_t>(Io::GPIO) | LK));
    CHECK(pwmHsPcr() == (static_cast<uint32_t>(Io::ALT2) | LK));
    CHECK(pwmLsPcr() == (static_cast<uint32_t>(Io::ALT2) | LK));
    CHECK(safeOffPcr() == (static_cast<uint32_t>(Io::GPIO) | static_cast<uint32_t>(Io::STRENGTH_ON) | LK));
    // nothing else is locked: the neighbour, the checked pin, the indicator (the safe state writes its PCR)
    CHECK((neighbourPcr() & LK) == 0U);
    CHECK((::test_io::ports[_PORTE_].PCR[PE9] & LK) == 0U);
    CHECK((::test_io::ports[_PORTA_].PCR[PA3] & LK) == 0U);
    CHECK((::test_io::ports[_PORTB_].PCR[PB9] & LK) == 0U);
    // every address of the list is a register of the model
    CHECK(::test_io::unknown.empty());
    CHECK(::test_io::wrongWidth.empty());

    // what the lock is for: a later write through the driver cannot give a pin of the list another function
    for (uint16_t const io : {static_cast<uint16_t>(Io::SAFETY_LOCKED_PIN), static_cast<uint16_t>(Io::TEST_PWM_HS),
                              static_cast<uint16_t>(Io::TEST_SAFE_OFF)})
    {
        Io::PinConfiguration cfg{};
        CHECK(Io::getConfiguration(io, cfg) == ::bsp::BSP_OK);
        uint32_t const before = cfg.pinCfg;
        cfg.pinCfg            = static_cast<uint32_t>(Io::PINDISABLE);
        CHECK(Io::setConfiguration(io, cfg) == ::bsp::BSP_OK);
        CHECK(Io::getConfiguration(io, cfg) == ::bsp::BSP_OK);
        CHECK(cfg.pinCfg == before);
    }
    // an unlocked pin of the same driver does change (the model is not simply read-only)
    Io::PinConfiguration cfg{};
    CHECK(Io::getConfiguration(Io::TEST_NEIGHBOUR, cfg) == ::bsp::BSP_OK);
    cfg.pinCfg = static_cast<uint32_t>(Io::ALT2);
    CHECK(Io::setConfiguration(Io::TEST_NEIGHBOUR, cfg) == ::bsp::BSP_OK);
    CHECK((neighbourPcr() & MUX_MASK) == static_cast<uint32_t>(Io::ALT2));
}

void testLockKeepsTheInterruptFlag()
{
    // PCR[ISF] is write-1-to-clear: the lock write must not write it as 1. The model keeps what is written, so a
    // write that copied the flag back would show it; the lock write clears it in the written value.
    powerOn();
    bspStart();
    rolePcr() = static_cast<uint32_t>(Io::GPIO) | 0x01000000U;
    ::safety::SafeIo safeIo;
    safeIo.init();
    CHECK(rolePcr() == (static_cast<uint32_t>(Io::GPIO) | LK));
    CHECK(::test_trace::trace.empty());
}

void testDriveStrengthAndPullAreJudged()
{
    // SYS-24: the read-back judges drive strength and pull as well (before: demo scope, MUX only). A wrong one on
    // any locked pin is reported with class and index of that entry.
    struct Case
    {
        ::test_io::Pcr& (*pcr)();
        uint32_t value;
        Entry entry;
    };
    Case const cases[] = {
        {&rolePcr, static_cast<uint32_t>(Io::GPIO) | static_cast<uint32_t>(Io::STRENGTH_ON), E_ROLE},
        {&rolePcr, static_cast<uint32_t>(Io::GPIO) | static_cast<uint32_t>(Io::PULLUP), E_ROLE},
        {&safeOffPcr, static_cast<uint32_t>(Io::GPIO), E_SAFE_OFF}, // drive strength missing
        {&pwmLsPcr, static_cast<uint32_t>(Io::ALT2) | static_cast<uint32_t>(Io::PULLDOWN), E_PWM_LS},
    };
    for (auto const& c : cases)
    {
        auto& supervisor = powerOn();
        bspStart();
        c.pcr() = c.value;
        ::safety::SafeIo safeIo;
        safeIo.init();
        CHECK(lockReported(supervisor, ctx(RegisterClass::PIN_FUNCTION, c.entry)));
    }
}

void testEveryOtherFunctionIsReported()
{
    // Another driver (or a corrupted table) gave a pin of the list a function before the lock: locking it would
    // freeze the wrong function, so the read-back after the lock reports it.
    struct Pin
    {
        ::test_io::Pcr& (*pcr)();
        uint32_t attributes;
        uint32_t rightMux;
        Entry entry;
    };
    Pin const pins[] = {
        {&rolePcr, 0U, 1U, E_ROLE},
        {&pwmHsPcr, 0U, 2U, E_PWM_HS},
        {&pwmLsPcr, 0U, 2U, E_PWM_LS},
        {&safeOffPcr, static_cast<uint32_t>(Io::STRENGTH_ON), 1U, E_SAFE_OFF},
    };
    for (auto const& pin : pins)
    {
        for (uint32_t mux = 0U; mux <= 7U; ++mux)
        {
            if (mux == pin.rightMux)
            {
                continue;
            }
            auto& supervisor = powerOn();
            bspStart();
            pin.pcr() = (mux << 8U) | pin.attributes;
            ::safety::SafeIo safeIo;
            safeIo.init();
            bool const reported = lockReported(supervisor, ctx(RegisterClass::PIN_FUNCTION, pin.entry));
            CHECK(reported);
            if (!reported)
            {
                std::printf(
                    "  entry %u MUX %u: trace '%s', context 0x%08X\n",
                    static_cast<unsigned>(pin.entry),
                    static_cast<unsigned>(mux),
                    ::test_trace::trace.c_str(),
                    static_cast<unsigned>(supervisor.lockRegisterMonitor.getContext()));
            }
            CHECK((pin.pcr() & LK) == LK); // locked all the same - the reset clears it
        }
    }
}

void testPinLockedEarlierWithWrongFunctionIsReported()
{
    auto& supervisor = powerOn();
    bspStart();
    pwmHsPcr() = static_cast<uint32_t>(Io::GPIO) | LK; // the write of init() cannot change it any more
    ::safety::SafeIo safeIo;
    safeIo.init();
    CHECK(lockReported(supervisor, ctx(RegisterClass::PIN_FUNCTION, E_PWM_HS)));
    CHECK((pwmHsPcr() & MUX_MASK) == static_cast<uint32_t>(Io::GPIO));
}

void testStuckMuxBitIsReported()
{
    for (uint32_t bit = 8U; bit <= 10U; ++bit)
    {
        auto& supervisor = powerOn();
        rolePcr().stuckAtOne  = (bit == 8U) ? 0U : (static_cast<uint32_t>(1U) << bit);
        rolePcr().stuckAtZero = (bit == 8U) ? (static_cast<uint32_t>(1U) << bit) : 0U;
        bspStart();
        ::safety::SafeIo safeIo;
        safeIo.init();
        CHECK(lockReported(supervisor, ctx(RegisterClass::PIN_FUNCTION, E_ROLE)));
    }
    {
        auto& supervisor       = powerOn();
        pwmLsPcr().stuckAtZero = static_cast<uint32_t>(1U) << 9U; // ALT2 reads as PINDISABLE
        bspStart();
        ::safety::SafeIo safeIo;
        safeIo.init();
        CHECK(lockReported(supervisor, ctx(RegisterClass::PIN_FUNCTION, E_PWM_LS)));
    }
}

void testLockThatDidNotTakeIsReported()
{
    // SYS-42: the pin has the right function, but the lock write is lost or LK is stuck at 0 - the pin stays
    // reconfigurable while SM-21 claims it is locked. SYS-24: for every locked pin, not only the role pin.
    Entry const entries[]                   = {E_PWM_HS, E_SAFE_OFF, E_ROLE};
    ::test_io::Pcr& (*const pcrs[])() = {&pwmHsPcr, &safeOffPcr, &rolePcr};
    for (size_t i = 0U; i < 3U; ++i)
    {
        {
            auto& supervisor = powerOn();
            bspStart();
            pcrs[i]().dropWrites = true;
            ::safety::SafeIo safeIo;
            safeIo.init();
            CHECK((pcrs[i]() & LK) == 0U);
            CHECK(lockReported(supervisor, ctx(RegisterClass::PIN_FUNCTION, entries[i])));
        }
        {
            auto& supervisor      = powerOn();
            pcrs[i]().stuckAtZero = LK;
            bspStart();
            ::safety::SafeIo safeIo;
            safeIo.init();
            CHECK(lockReported(supervisor, ctx(RegisterClass::PIN_FUNCTION, entries[i])));
        }
    }
}

void testCheckedOnlyRegistersAreJudgedByTheFirstCycle()
{
    // A wrong FTM polarity before init(): the start-up read-back covers the locked registers only (a reset would not
    // repair a wrong timer configuration); the first cycle reports it - S1 without reset.
    auto& supervisor = powerOn();
    bspStart();
    setPlain(::test_io::ftm1(offsetof(FTM_Type, POL)), 0x00U);
    ::safety::SafeIo safeIo;
    safeIo.init();
    CHECK(::test_trace::trace.empty());
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ES");
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::safety::safeIoRegisterFault() == ctx(RegisterClass::PWM_TIMER, E_POL));
}

// ---- SM-22: cyclic register check ----------------------------------------------------------------------------

void testHealthyCyclesReportNothingAndReadEveryEntry()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    ::test_io::reads = 0;
    cycles(safeIo, 100);
    CHECK(::test_trace::trace.empty());
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::test_io::reads == (100 * static_cast<int>(ENTRY_COUNT)));
    CHECK(::test_io::unknown.empty());
    CHECK(::test_io::wrongWidth.empty()); // SYS-153: every register with its own width
    CHECK(::test_io::readsOf[::test_io::lvdsc1()] == 100);
    CHECK(::safety::safeIoRegisterFault() == 0U);
}

void testRegistersTheDriversChangeAreNotJudged()
{
    // OUTMASK (first duty, disableOutputs), SWOCTRL (safe outputs walk and its release), CnV (every duty), the
    // counter, the overflow flag and the drive level of the safe_off output change during operation.
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    safeIo.cyclic();
    setPlain(::test_io::ftm1(offsetof(FTM_Type, OUTMASK)), 0x00U);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, SWOCTRL)), 0x03U);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, CONTROLS) + 4U), 0x123U);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, CNT)), 0x456U);
    setPlain(::test_io::ftm1(offsetof(FTM_Type, SC)), FTM_SC & ~0x00000200U);
    ::test_io::gpios[_PORTA_].PDOR |= static_cast<uint32_t>(1U) << PA7;
    cycles(safeIo, 5);
    CHECK(::test_trace::trace.empty());
    CHECK(!supervisor.inLimpHomeState());
}

struct Drift
{
    char const* what;
    uintptr_t address;
    uint32_t value; // new value of the whole word
    Entry entry;
    RegisterClass registerClass;
};

void checkDrift(Drift const& d, bool const viaGpio)
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    cycles(safeIo, 3);
    uint32_t before = 0U;
    if (viaGpio)
    {
        before = ::safety::registers::read(d.address);
        ::safety::registers::write(d.address, d.value);
    }
    else
    {
        before = ::test_io::reg(d.address).value;
        setPlain(d.address, d.value);
    }
    safeIo.cyclic();
    bool const reported = (::test_trace::trace == "ES")
                          && (supervisor.checkRegisterMonitor.getContext() == ctx(d.registerClass, d.entry))
                          && (::safety::safeIoRegisterFault() == ctx(d.registerClass, d.entry));
    CHECK(reported);
    // SYS-169: the log line of the report names class and entry of the deviating register
    char expectedLine[160];
    (void)std::snprintf(
        expectedLine,
        sizeof(expectedLine),
        "Event: CHECK_REGISTER_ERROR (enter safe state, register class %u, entry %u)",
        static_cast<unsigned>(d.registerClass),
        static_cast<unsigned>(d.entry));
    CHECK(::test_trace::lastError == expectedLine);
    if (::test_trace::lastError != expectedLine)
    {
        std::printf("  %s: line '%s'\n", d.what, ::test_trace::lastError.c_str());
    }
    if (!reported)
    {
        std::printf(
            "  %s: trace '%s', fault 0x%08X\n",
            d.what,
            ::test_trace::trace.c_str(),
            static_cast<unsigned>(::safety::safeIoRegisterFault()));
    }
    CHECK(!supervisor.inLimpHomeState()); // S1 without reset
    // latched: once, and drifting back does not release the safe state
    cycles(safeIo, 10);
    ::safety::registers::write(d.address, before);
    if (!viaGpio)
    {
        setPlain(d.address, before);
    }
    cycles(safeIo, 10);
    CHECK(::test_trace::trace == "ES");
    CHECK(::safety::safeIoRegisterFault() == ctx(d.registerClass, d.entry));
}

void testEveryCheckedClassIsReportedOnceAndLatched()
{
    uint32_t const pta7 = static_cast<uint32_t>(1U) << PA7;
    uint32_t const ptb5 = static_cast<uint32_t>(1U) << PB5;
    Drift const gpio[] = {
        {"safe_off output turned into input", IP_PTA_BASE + offsetof(GPIO_Type, PDDR), 0U, E_PTA_PDDR,
         RegisterClass::GPIO_DIRECTION},
        {"safe_off output input disabled", IP_PTA_BASE + offsetof(GPIO_Type, PIDR), pta7, E_PTA_PIDR,
         RegisterClass::GPIO_DIRECTION},
        {"role pin turned into input", IP_PTB_BASE + offsetof(GPIO_Type, PDDR), 0U, E_PTB_PDDR,
         RegisterClass::GPIO_DIRECTION},
        {"role pin input disabled", IP_PTB_BASE + offsetof(GPIO_Type, PIDR), ptb5, E_PTB_PIDR,
         RegisterClass::GPIO_DIRECTION},
    };
    for (auto const& d : gpio)
    {
        checkDrift(d, true);
    }
    Drift const plain[] = {
        {"FTM1 clock gated", ::test_io::pcc(57U), 0x80000000U, E_PCC_FTM1, RegisterClass::CLOCK_GATE},
        {"PORTD clock gated", ::test_io::pcc(76U), 0x80000000U, E_PCC_PORTD, RegisterClass::CLOCK_GATE},
        {"PORTA clock gated", ::test_io::pcc(73U), 0x80000000U, E_PCC_PORTA, RegisterClass::CLOCK_GATE},
        {"PORTB clock gated", ::test_io::pcc(74U), 0x80000000U, E_PCC_PORTB, RegisterClass::CLOCK_GATE},
        {"FTM counter stopped", ::test_io::ftm1(offsetof(FTM_Type, SC)), FTM_SC & ~0x18U, E_SC,
         RegisterClass::PWM_TIMER},
        {"FTM prescaler", ::test_io::ftm1(offsetof(FTM_Type, SC)), (FTM_SC & ~0x7U) | 0x2U, E_SC,
         RegisterClass::PWM_TIMER},
        {"FTM PWM output disabled", ::test_io::ftm1(offsetof(FTM_Type, SC)), FTM_SC & ~0x00020000U, E_SC,
         RegisterClass::PWM_TIMER},
        {"FTM period", ::test_io::ftm1(offsetof(FTM_Type, MOD)), FTM_MOD + 1U, E_MOD, RegisterClass::PWM_TIMER},
        {"FTM counter start", ::test_io::ftm1(offsetof(FTM_Type, CNTIN)), 0x10U, E_CNTIN, RegisterClass::PWM_TIMER},
        {"FTM dead time insertion off", ::test_io::ftm1(offsetof(FTM_Type, COMBINE)), FTM_COMBINE & ~0x10U,
         E_COMBINE, RegisterClass::PWM_TIMER},
        {"FTM complement off", ::test_io::ftm1(offsetof(FTM_Type, COMBINE)), FTM_COMBINE & ~0x02U, E_COMBINE,
         RegisterClass::PWM_TIMER},
        {"FTM fault control on pair 1", ::test_io::ftm1(offsetof(FTM_Type, COMBINE)), FTM_COMBINE | 0x4000U,
         E_COMBINE, RegisterClass::PWM_TIMER},
        {"FTM polarity high side", ::test_io::ftm1(offsetof(FTM_Type, POL)), FTM_POL | 0x01U, E_POL,
         RegisterClass::PWM_TIMER},
        {"FTM polarity low side", ::test_io::ftm1(offsetof(FTM_Type, POL)), 0x00U, E_POL, RegisterClass::PWM_TIMER},
        {"FTM pair inverted", ::test_io::ftm1(offsetof(FTM_Type, INVCTRL)), 0x01U, E_INVCTRL,
         RegisterClass::PWM_TIMER},
        {"FTM debug mode", ::test_io::ftm1(offsetof(FTM_Type, CONF)), FTM_CONF & ~0xC0U, E_CONF,
         RegisterClass::PWM_TIMER},
        {"FTM dead time value", ::test_io::ftm1(offsetof(FTM_Type, PAIR0DEADTIME)), FTM_PAIR0DT - 1U,
         E_PAIR0DEADTIME, RegisterClass::PWM_TIMER},
        {"FTM dead time prescaler", ::test_io::ftm1(offsetof(FTM_Type, PAIR0DEADTIME)), FTM_PAIR0DT & 0x3FU,
         E_PAIR0DEADTIME, RegisterClass::PWM_TIMER},
        {"FTM dead time extension", ::test_io::ftm1(offsetof(FTM_Type, PAIR0DEADTIME)), FTM_PAIR0DT | 0x10000U,
         E_PAIR0DEADTIME, RegisterClass::PWM_TIMER},
    };
    for (auto const& d : plain)
    {
        checkDrift(d, false);
    }
}

// ---- SYS-153: low-voltage detect reset (PMC_LVDSC1, rest of H9) ---------------------------------------------------

void testLowVoltageDetectResetIsCheckedNeverWritten()
{
    // Healthy: boardInit() wrote 0x10; LVDF (the supply state, bit 7) does not count. SafeIo reads the byte register
    // once per cycle with an 8-bit access and never writes it - not in init() (no lock bits), not in a cycle.
    auto& supervisor = powerOn();
    bspStart();
    ::test_io::Byte& pmc = ::test_io::byteReg(::test_io::lvdsc1());
    CHECK(pmc.value == 0x10U);
    ::safety::SafeIo safeIo;
    safeIo.init();
    ::test_io::readsOf.clear();
    cycles(safeIo, 5);
    pmc.value = 0x90U; // VDD below VLVD for a moment (LVDF set) - the reset acts, not SafeIo
    cycles(safeIo, 5);
    CHECK(::test_trace::trace.empty());
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::safety::safeIoRegisterFault() == 0U);
    CHECK(::test_io::readsOf[::test_io::lvdsc1()] == 10);
    CHECK(pmc.writes == 0);
    CHECK(::test_io::wrongWidth.empty());
}

void testLowVoltageDetectResetDriftIsReportedOnceAndLatched()
{
    // A bit flip (or a write of other code) that clears LVDRE or sets LVDIE: up to SYS-153 unseen until the next
    // start. Now CHECK_REGISTER_ERROR in the next cycle - S1 without reset, latched like every entry of the list.
    struct Case
    {
        char const* what;
        uint8_t value;
    };
    Case const cases[] = {
        {"LVDRE cleared", 0x00U},
        {"LVDRE cleared, LVDF set", 0x80U},
        {"LVDIE set", 0x30U},
        {"LVDIE set, LVDRE cleared", 0x20U},
        {"LVDIE set, LVDF set", 0xB0U},
    };
    for (auto const& c : cases)
    {
        auto& supervisor = powerOn();
        bspStart();
        ::safety::SafeIo safeIo;
        safeIo.init();
        cycles(safeIo, 3);
        CHECK(::test_trace::trace.empty());
        ::test_io::byteReg(::test_io::lvdsc1()).value = c.value;
        safeIo.cyclic();
        bool const reported = (::test_trace::trace == "ES")
                              && (supervisor.checkRegisterMonitor.getContext()
                                  == ctx(RegisterClass::SUPERVISION, E_PMC_LVDSC1))
                              && (::safety::safeIoRegisterFault() == ctx(RegisterClass::SUPERVISION, E_PMC_LVDSC1));
        CHECK(reported);
        if (!reported)
        {
            std::printf(
                "  %s: trace '%s', fault 0x%08X\n",
                c.what,
                ::test_trace::trace.c_str(),
                static_cast<unsigned>(::safety::safeIoRegisterFault()));
        }
        CHECK(!supervisor.inLimpHomeState()); // S1 without reset
        cycles(safeIo, 10);
        ::test_io::byteReg(::test_io::lvdsc1()).value = 0x10U; // drifting back does not release the safe state
        cycles(safeIo, 10);
        CHECK(::test_trace::trace == "ES");
        CHECK(::safety::safeIoRegisterFault() == ctx(RegisterClass::SUPERVISION, E_PMC_LVDSC1));
        CHECK(::test_io::byteReg(::test_io::lvdsc1()).writes == 0); // SafeIo does not repair it
    }
}

void testLowVoltageDetectResetNotOnAtStartIsSeenByTheFirstCycle()
{
    // The write of boardInit() was lost, or LVDRE is stuck at 0: not a lockable entry, so the start-up read-back does
    // not judge it (no reset, which would not help either); the first cycle reports it.
    for (int variant = 0; variant < 2; ++variant)
    {
        auto& supervisor     = powerOn();
        ::test_io::Byte& pmc = ::test_io::byteReg(::test_io::lvdsc1());
        if (variant == 0)
        {
            pmc.dropWrites = true;
        }
        else
        {
            pmc.stuckAtZero = 0x10U;
        }
        bspStart();
        ::safety::SafeIo safeIo;
        safeIo.init();
        CHECK(::test_trace::trace.empty());
        safeIo.cyclic();
        CHECK(::test_trace::trace == "ES");
        CHECK(!supervisor.inLimpHomeState());
        CHECK(::safety::safeIoRegisterFault() == ctx(RegisterClass::SUPERVISION, E_PMC_LVDSC1));
    }
}

void testByteEntryIsLockedAndCheckedWithByteAccesses()
{
    // The engine for a lockable byte register (none in the list today): the lock write is an 8-bit read-modify-write
    // that clears the lockClear bits, and the read-back of the locked entries reads 8 bits.
    powerOn();
    ::test_io::Byte& reg = ::test_io::byteReg(::test_io::lvdsc1());
    reg.value            = 0x44U;
    ::safety::registers::Entry const entry[] = {
        {::test_io::lvdsc1(), 0xFFU, 0x10U, 0x10U, 0x40U, RegisterClass::SUPERVISION, ::safety::registers::WIDTH_8}};
    ::safety::registers::lock(entry, 1U);
    CHECK(reg.writes == 1);
    CHECK(reg.value == 0x14U);
    CHECK(::safety::registers::firstDeviation(entry, 1U, 0U, true) == 0U); // 0x14 under mask 0xFF is not 0x10
    reg.value = 0x10U;
    CHECK(::safety::registers::firstDeviation(entry, 1U, 0U, true) == 1U);
    CHECK(::test_io::wrongWidth.empty());
}

void testLockedPinThatBreaksAfterStartIsReported()
{
    // The lock keeps software away, not a hardware fault: a PCR bit that sticks after the start is seen by the cycle.
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    cycles(safeIo, 2);
    pwmHsPcr().stuckAtZero = static_cast<uint32_t>(1U) << 9U;
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ES");
    CHECK(::safety::safeIoRegisterFault() == ctx(RegisterClass::PIN_FUNCTION, E_PWM_HS));
    CHECK(!supervisor.inLimpHomeState());
}

void testInitClearsTheLatch()
{
    powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    setPlain(::test_io::ftm1(offsetof(FTM_Type, POL)), 0x00U);
    safeIo.cyclic();
    CHECK(::safety::safeIoRegisterFault() != 0U);
    // the next start (after the reset) begins without a fault
    powerOn();
    bspStart();
    ::safety::SafeIo next;
    next.init();
    CHECK(::safety::safeIoRegisterFault() == 0U);
    cycles(next, 5);
    CHECK(::test_trace::trace.empty());
}

// ---- demonstration of the OpenBSW demo: SAFETY_TEST as input --------------------------------------------------

void testPinTurnedIntoOutputEntersTheSafeStateWithoutReset()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    cycles(safeIo, 3);
    testGpio().PDDR |= TEST_BIT; // register fault: the input became an output
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ES"); // S1, no reset (R)
    CHECK(supervisor.checkRegisterMonitor.getContext() == 0U);
    CHECK(::test_trace::lastError == "Event: CHECK_REGISTER_ERROR (enter safe state, SAFETY_TEST pin)"); // SYS-169
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::safety::safeIoRegisterFault() == 0U); // not an entry of the register list
    // once per change: the fault persists, the next cycles say nothing
    cycles(safeIo, 50);
    CHECK(::test_trace::trace == "ES");
    // back to input: the indicator is released (the power outputs stay off, SYS-36)
    testGpio().PDDR &= ~TEST_BIT;
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ESEL");
    CHECK(supervisor.checkRegisterMonitor.getContext() == 1U);
    cycles(safeIo, 10);
    CHECK(::test_trace::trace == "ESEL");
}

void testTestPinReturnDoesNotReleaseARegisterFault()
{
    powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    safeIo.cyclic();
    testGpio().PDDR |= TEST_BIT;
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ES");
    setPlain(::test_io::pcc(57U), 0U); // and then the timer loses its clock
    safeIo.cyclic();
    testGpio().PDDR &= ~TEST_BIT; // the demo pin returns
    cycles(safeIo, 5);
    CHECK(::test_trace::trace == "ES"); // no L: the register fault keeps S1
    CHECK(::safety::safeIoRegisterFault() == ctx(RegisterClass::CLOCK_GATE, E_PCC_FTM1));
}

void testInputDisabledIsReported()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    safeIo.cyclic();
    testGpio().PIDR |= TEST_BIT; // input buffer disabled: the pin reads as disabled
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ES");
    CHECK(supervisor.checkRegisterMonitor.getContext() == 0U);
}

void testReconfigurationThroughTheDriverIsReported()
{
    // A software fault rather than a register fault: some code reconfigures the checked pin through the driver.
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    safeIo.cyclic();
    Io::PinConfiguration cfg{};
    CHECK(Io::getConfiguration(Io::SAFETY_TEST, cfg) == ::bsp::BSP_OK);
    cfg.dir = Io::_OUT;
    CHECK(Io::setConfiguration(Io::SAFETY_TEST, cfg) == ::bsp::BSP_OK);
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ES");
    CHECK(!supervisor.inLimpHomeState());
}

void testFaultBeforeTheFirstCycleIsSeenInTheFirstCycle()
{
    powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    testGpio().PDDR |= TEST_BIT;
    safeIo.cyclic();
    CHECK(::test_trace::trace == "ES");
}

void testOtherPinsDoNotReport()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    safeIo.cyclic();
    // the neighbour on the same port changes direction back and forth
    testGpio().PDDR &= ~(static_cast<uint32_t>(1U) << PE8);
    safeIo.cyclic();
    testGpio().PIDR |= static_cast<uint32_t>(1U) << PE8;
    safeIo.cyclic();
    // the same pin number on another port becomes an output and is disabled
    ::test_io::gpios[_PORTB_].PDDR |= static_cast<uint32_t>(1U) << PB9;
    safeIo.cyclic();
    ::test_io::gpios[_PORTB_].PIDR |= static_cast<uint32_t>(1U) << PB9;
    safeIo.cyclic();
    // neighbours of the list: PTA3 (indicator) and PTB4 on the ports of the list, the clock of an unused port
    ::test_io::gpios[_PORTA_].PDDR &= ~(static_cast<uint32_t>(1U) << PA3);
    ::test_io::gpios[_PORTB_].PDDR |= static_cast<uint32_t>(1U) << PB4;
    setPlain(::test_io::pcc(77U), 0U);
    cycles(safeIo, 3);
    CHECK(::test_trace::trace.empty());
    CHECK(!supervisor.inLimpHomeState());
}
// ---- SYS-30: read-back of the safe state (gap G1) ----------------------------------------------------------------

constexpr uint32_t SWOCTRL_OFF = 0x03U; // CHnOC of channels 0/1, CHnOCV 0: the walk's value for the group TEST
constexpr uint32_t DRV_EN_BIT  = static_cast<uint32_t>(1U) << PA7;

uintptr_t swoctrl() { return ::test_io::ftm1(offsetof(FTM_Type, SWOCTRL)); }

/** Start, then S1 as the walk leaves it: output control forced, enable pin low; the latch of S1 set. */
::safety::SafeIo& startInS1(::safety::SafeIo& safeIo)
{
    safeIo.init();
    safeIo.cyclic();
    setPlain(swoctrl(), SWOCTRL_OFF);
    ::test_io::gpios[_PORTA_].PDIR &= ~DRV_EN_BIT;
    ::nxSafeStateLatch = NX_SAFE_STATE_LATCHED;
    ::test_trace::trace.clear();
    return safeIo;
}

void testReadbackListOfTheProfile()
{
    CHECK(::safety::registers::safeOutputReadbackCount == 2U);
    CHECK(::safety::registers::safeOutputReadbacks[0].address == swoctrl());
    CHECK(::safety::registers::safeOutputReadbacks[0].mask == 0x0303U);
    CHECK(::safety::registers::safeOutputReadbacks[1].address == IP_PTA_BASE + offsetof(GPIO_Type, PDIR));
    CHECK(::safety::registers::safeOutputReadbacks[1].expected == 0U);
    CHECK(::safety::registers::safeOutputReadbacks[2].address == 0U);
    CHECK(ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(1)) == 0x07000001U);
}

void testS1ThatTookEffectReportsNothing()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    startInS1(safeIo);
    // bits of other channels of the timer are not the group's: outside the mask
    setPlain(swoctrl(), SWOCTRL_OFF | 0x0C0CU);
    ::test_io::gpios[_PORTA_].PDIR |= static_cast<uint32_t>(1U) << PA3; // a neighbour pin high
    cycles(safeIo, 5);
    CHECK(::test_trace::trace.empty());
    CHECK(!supervisor.inLimpHomeState());
    CHECK(::safety::safeIoOutputFault() == 0U);
    ::nxSafeStateLatch = 0U;
}

void testOutputsAreNotJudgedOutsideS1()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    setPlain(swoctrl(), 0U);                       // PWM running
    ::test_io::gpios[_PORTA_].PDIR |= DRV_EN_BIT; // gate driver enabled
    cycles(safeIo, 3);
    CHECK(::test_trace::trace.empty());
    // leaving S1 stops the read-back as well
    startInS1(safeIo);
    ::nxSafeStateLatch = 0U;
    setPlain(swoctrl(), 0U);
    cycles(safeIo, 3);
    CHECK(::test_trace::trace.empty());
    CHECK(!supervisor.inLimpHomeState());
}

void testS1ThatDidNotTakeEffectIsReportedOnce()
{
    struct Case
    {
        char const* what;
        void (*fault)();
        uint32_t entry;
    };
    Case const cases[] = {
        {"output control released (PWM runs)", [] { setPlain(swoctrl(), 0U); }, 0U},
        {"one channel not forced", [] { setPlain(swoctrl(), 0x01U); }, 0U},
        {"a channel forced to its active level (CHnOCV)", [] { setPlain(swoctrl(), 0x0103U); }, 0U},
        {"enable pin still high", [] { ::test_io::gpios[_PORTA_].PDIR |= DRV_EN_BIT; }, 1U},
    };
    for (Case const& c : cases)
    {
        std::printf("  S1 not effective: %s\n", c.what);
        auto& supervisor = powerOn();
        bspStart();
        ::safety::SafeIo safeIo;
        startInS1(safeIo);
        c.fault();
        safeIo.cyclic();
        CHECK(lockReported(supervisor, ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(c.entry))));
        CHECK(::safety::safeIoOutputFault() == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(c.entry)));
        // latched: reported once, drifting back does not clear it; only init() does. SYS-138: the walk of the reaction
        // acts on the model and restored the state - the fault is injected again, it must not be reported again
        ::test_trace::trace.clear();
        c.fault();
        cycles(safeIo, 3);
        setPlain(swoctrl(), SWOCTRL_OFF);
        ::test_io::gpios[_PORTA_].PDIR &= ~DRV_EN_BIT;
        cycles(safeIo, 2);
        CHECK(::test_trace::trace.empty());
        CHECK(::safety::safeIoOutputFault() != 0U);
        safeIo.init();
        CHECK(::safety::safeIoOutputFault() == 0U);
        ::nxSafeStateLatch = 0U;
    }
}

void testGatedPeripheralIsNotReadAndNotConfirmed()
{
    // SYS-138 (K6): up to SYS-138 a gated entry was not judged. A timer whose clock is gated keeps its outputs frozen
    // where the walk cannot reach them, and the register list expects the gate on - the entry is not confirmed now.
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    startInS1(safeIo);
    setPlain(swoctrl(), 0U);
    setPlain(::test_io::pcc(57U), 0U); // FTM1 clock off: the walk skipped it, the read-back must not read it
    ::test_io::unknown.clear();
    ::test_io::readsOf.clear();
    safeIo.cyclic();
    CHECK(::test_io::readsOf[swoctrl()] == 0);
    // not confirmed: LOCK_REGISTER_ERROR with the timer entry, S1 + S2 - without the console line, because the walk
    // before the reset cannot reach the gated timer either (the reset follows at once)
    CHECK(::safety::safeIoOutputFault() == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(0U)));
    CHECK(supervisor.lockRegisterMonitor.getContext() == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(0U)));
    CHECK(::test_trace::trace.find("SR") != std::string::npos);
    CHECK(::test_trace::trace.find('P') == std::string::npos);
    CHECK(::test_io::readsOf[swoctrl()] == 0);
    ::nxSafeStateLatch = 0U;
}

// ---- SYS-138: read-back for the supervisor (hold of S1, degraded groups, S1 + S2) ------------------------------------

/** Rounds of the hold loop and the trace: the event's log line (E), the walk (S), the hold line (P), then per round the
 * read-back and the walk (S). */
std::string holdTrace(int const rounds) { return "ESP" + std::string(static_cast<size_t>(rounds), 'S'); }

/* Must run before every other test that calls SafeIo::init(): the flag it sets lives until the next reset. */
void testNothingIsJudgedBeforeInit()
{
    auto& supervisor = powerOn();
    bspStart();
    setPlain(swoctrl(), 0U);
    ::test_io::gpios[_PORTA_].PDIR |= DRV_EN_BIT;
    // the PWM runs, the enable is high: before init() (hold from main() before the BSP) nothing is judged
    CHECK(::safety::safeStateReadback() == 0U);
    CHECK(::safety::degradedStateReadback(0x3U) == 0U);
    CHECK(::test_io::reads == 0);
    // the hold of S1 before init(): the walk does not take effect (lost write), the hold still holds - no reset
    ::test_io::reg(swoctrl()).dropWrites = true;
    ::test_hold::left                    = 3;
    supervisor.romCheckMonitor.trigger(0U);
    CHECK(::test_trace::trace == holdTrace(3));
    CHECK(supervisor.holdRounds() == 3U);
    // an S1 + S2 reaction before init(): the console line as before SYS-138
    auto& other = powerOn();
    bspStart();
    ::test_io::reg(swoctrl()).dropWrites = true;
    other.deadmanConfigMonitor.trigger(1U);
    CHECK(::test_trace::trace == "ESPR");
}

void testReadbackOfEveryEntryAndOfTheDegradedEntries()
{
    powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    startInS1(safeIo);
    CHECK(::safety::safeStateReadback() == 0U);
    CHECK(::safety::degradedStateReadback(0x3U) == 0U);
    setPlain(swoctrl(), 0U); // entry 0 (the PWM group) released
    CHECK(::safety::safeStateReadback() == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(0U)));
    CHECK(::safety::degradedStateReadback(0x1U) == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(0U)));
    CHECK(::safety::degradedStateReadback(0x3U) == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(0U)));
    // only the entries of the mask: the enable alone is confirmed, no entry at all is confirmed, and the enable is
    // not judged through bits of entries the table does not have
    ::test_io::readsOf.clear();
    CHECK(::safety::degradedStateReadback(0x2U) == 0U);
    CHECK(::test_io::readsOf[swoctrl()] == 0);
    CHECK(::safety::degradedStateReadback(0U) == 0U);
    CHECK(::safety::degradedStateReadback(0xFFFFFFFCU) == 0U);
    ::test_io::gpios[_PORTA_].PDIR |= DRV_EN_BIT; // entry 1 (the enable) high
    CHECK(::safety::degradedStateReadback(0x2U) == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(1U)));
    setPlain(swoctrl(), SWOCTRL_OFF);
    CHECK(::safety::safeStateReadback() == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(1U)));
    CHECK(::safety::degradedStateReadback(0x1U) == 0U);
    // a gated clock is not confirmed, and not read
    ::test_io::gpios[_PORTA_].PDIR &= ~DRV_EN_BIT;
    setPlain(::test_io::pcc(PCC_PORTA_INDEX), 0U);
    ::test_io::readsOf.clear();
    CHECK(::safety::degradedStateReadback(0x2U) == ctx(RegisterClass::SAFE_OUTPUT, static_cast<Entry>(1U)));
    CHECK(::test_io::readsOf[IP_PTA_BASE + offsetof(GPIO_Type, PDIR)] == 0);
    CHECK(::safety::degradedStateReadback(0x1U) == 0U);
    // reads only, writes nothing: no report, no trace, the latch of SafeIo untouched
    CHECK(::test_trace::trace.empty());
    CHECK(::safety::safeIoOutputFault() == 0U);
    ::nxSafeStateLatch = 0U;
}

void testHoldConfirmedByReadback()
{
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    safeIo.cyclic();
    setPlain(swoctrl(), 0U); // PWM running when the fault comes: the walk of the hold switches it off
    ::test_io::gpios[_PORTA_].PDIR |= DRV_EN_BIT;
    ::test_trace::trace.clear();
    ::test_hold::left = 5;
    supervisor.romCheckMonitor.trigger(0U);
    CHECK(::test_trace::trace == holdTrace(5)); // S1 took effect: held, no reset
    CHECK(supervisor.holdRounds() == 5U);
    CHECK(supervisor.inLimpHomeState());
}

void testHoldEndsInResetWhenS1DoesNotTakeEffect()
{
    struct Case
    {
        char const* what;
        void (*fault)();
        uint32_t entry;
    };
    Case const cases[] = {
        {"output control write lost", [] { ::test_io::reg(swoctrl()).dropWrites = true; }, 0U},
        {"output control bit stuck at 0", [] { ::test_io::reg(swoctrl()).stuckAtZero = 0x02U; }, 0U},
        {"enable pin held high", [] { ::test_io::enableHeldHigh = true; }, 1U},
        {"timer clock gated", [] { setPlain(::test_io::pcc(PCC_FTM1_INDEX), 0U); }, 0U},
    };
    for (Case const& c : cases)
    {
        std::printf("  hold, S1 not effective: %s\n", c.what);
        auto& supervisor = powerOn();
        bspStart();
        ::safety::SafeIo safeIo;
        safeIo.init();
        safeIo.cyclic();
        setPlain(swoctrl(), 0U);
        ::test_io::gpios[_PORTA_].PDIR |= DRV_EN_BIT;
        c.fault();
        ::test_trace::trace.clear();
        ::test_hold::left = 5;
        supervisor.romCheckMonitor.trigger(0U);
        // first round: the read-back of what the walk before the line left -> S1 + S2 at once, the walk again, no
        // second line (S1 not confirmed), reset; the hold loop is left (on the target the reset does not return)
        CHECK(::test_trace::trace == "ESPSR");
        CHECK(supervisor.holdRounds() == 0U);
        CHECK(::test_hold::left == 4);
        CHECK(supervisor.inLimpHomeState());
    }
}

void testResetLineOnlyForConfirmedS1()
{
    // S1 + S2 (every resetting event, the kernel faults, ETL_ASSERT): the walk took effect - the line as before
    auto& supervisor = powerOn();
    bspStart();
    ::safety::SafeIo safeIo;
    safeIo.init();
    safeIo.cyclic();
    setPlain(swoctrl(), 0U);
    ::test_trace::trace.clear();
    supervisor.deadmanConfigMonitor.trigger(1U);
    CHECK(::test_trace::trace == "ESPR");
    // the walk did not take effect: no line, the reset at once
    struct Case
    {
        char const* what;
        void (*fault)();
    };
    Case const cases[] = {
        {"output control write lost", [] { ::test_io::reg(swoctrl()).dropWrites = true; }},
        {"enable pin held high", [] { ::test_io::enableHeldHigh = true; }},
        {"port clock gated", [] { setPlain(::test_io::pcc(PCC_PORTA_INDEX), 0U); }},
    };
    for (Case const& c : cases)
    {
        std::printf("  S1 + S2, S1 not effective: %s\n", c.what);
        auto& other = powerOn();
        bspStart();
        ::safety::SafeIo io;
        io.init();
        io.cyclic();
        setPlain(swoctrl(), 0U);
        ::test_io::gpios[_PORTA_].PDIR |= DRV_EN_BIT;
        c.fault();
        ::test_trace::trace.clear();
        other.deadmanConfigMonitor.trigger(1U);
        CHECK(::test_trace::trace == "ESR");
        ::test_trace::trace.clear();
        other.reportEtlAssert(0x1234U);
        CHECK(::test_trace::trace == "SR");
    }
}
} // namespace

int main()
{
    testNothingIsJudgedBeforeInit(); // SYS-138: first - no test before it may have called SafeIo::init()
    testListOfTheProfile();
    testReadbackListOfTheProfile();
    testS1ThatTookEffectReportsNothing();
    testOutputsAreNotJudgedOutsideS1();
    testS1ThatDidNotTakeEffectIsReportedOnce();
    testGatedPeripheralIsNotReadAndNotConfirmed();
    testReadbackOfEveryEntryAndOfTheDegradedEntries();
    testHoldConfirmedByReadback();
    testHoldEndsInResetWhenS1DoesNotTakeEffect();
    testResetLineOnlyForConfirmedS1();
    testHealthyStartLocksTheListAndNothingElse();
    testLockKeepsTheInterruptFlag();
    testDriveStrengthAndPullAreJudged();
    testEveryOtherFunctionIsReported();
    testPinLockedEarlierWithWrongFunctionIsReported();
    testStuckMuxBitIsReported();
    testLockThatDidNotTakeIsReported();
    testCheckedOnlyRegistersAreJudgedByTheFirstCycle();
    testHealthyCyclesReportNothingAndReadEveryEntry();
    testRegistersTheDriversChangeAreNotJudged();
    testEveryCheckedClassIsReportedOnceAndLatched();
    testLowVoltageDetectResetIsCheckedNeverWritten();
    testLowVoltageDetectResetDriftIsReportedOnceAndLatched();
    testLowVoltageDetectResetNotOnAtStartIsSeenByTheFirstCycle();
    testByteEntryIsLockedAndCheckedWithByteAccesses();
    testLockedPinThatBreaksAfterStartIsReported();
    testInitClearsTheLatch();
    testPinTurnedIntoOutputEntersTheSafeStateWithoutReset();
    testTestPinReturnDoesNotReleaseARegisterFault();
    testInputDisabledIsReported();
    testReconfigurationThroughTheDriverIsReported();
    testFaultBeforeTheFirstCycleIsSeenInTheFirstCycle();
    testOtherPinsDoNotReport();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
