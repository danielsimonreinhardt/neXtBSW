// SPDX-License-Identifier: Apache-2.0
/* Host test of the FlexCAN driver of the family port (COM-26, copy of OpenBSW FlexCANDevice) against a register
 * model of the S32K1xx FlexCAN with 16 and with 32 message buffers.
 *
 * The model follows the S32K1xx Reference Manual Rev. 13 (doc/hardware/s32k1xx/S32K-RM.pdf, chapter 55), not the
 * driver:
 * - an instance has 16 or 32 message buffers (Table 55-1, p. 1787); message buffer n lies at 0x80 + 16 n, its
 *   individual mask RXIMRn at 0x880 + 4 n (55.4.2.1, Table 55-16 p. 1884). An access to a buffer or mask the instance
 *   does not have, or to any other unimplemented offset, is an access error ("Read and write access to
 *   unimplemented address space results in access error", p. 1942) - the model records it with offset and
 *   direction instead of raising a bus fault;
 * - MCR reset value 0xD890_000F (p. 1806), MCR[SOFTRST] resets IMASK1/IFLAG1/ESR1/ECR and clears itself, freeze
 *   mode (FRZACK, NOTRDY) follows MDIS/FRZ/HALT, MCR[MAXMB] may only change in freeze mode (p. 1811);
 * - IFLAG1 and ESR1 are write 1 to clear (55.4.2.1 access "W1C");
 * - a transmit buffer whose CODE is written to 0b1100 is handed to the bus; the test completes it (CODE back to
 *   0b1000, IFLAG1 bit set) or delivers a received frame (CODE 0b0010, identifier, DLC, data, IFLAG1 bit).
 *
 * How the model sees every access: the registers are real memory at the real base addresses (0x4002_4000,
 * 0x4002_5000, 0x4002_B000, VirtualAlloc), each instance on its own page, which is kept PAGE_NOACCESS. A vectored
 * exception handler takes the access violation, classifies the offset, opens the page and single-steps the
 * instruction (trap flag); after the step it applies the register semantics of a write and closes the page again.
 * The driver runs unchanged and cannot bypass the model. Windows (x86-64) only, like the workplace of this project.
 *
 * Build and run (zig c++, no hardware; tools/test entry flexcan-device):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Werror -DETL_USE_ASSERT_FUNCTION \
 *       -Iplatforms/s32k1xx/port/bspConfiguration/bspFlexCan/test/host/stubs \
 *       -Iplatforms/s32k1xx/port/bspConfiguration/bspFlexCan/include \
 *       -isystemexternal/openbsw/libs/bsw/bsp/include -isystemexternal/openbsw/libs/bsw/cpp2can/include \
 *       -isystemexternal/openbsw/libs/3rdparty/etl/include \
 *       -isystemexternal/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp \
 *       platforms/s32k1xx/port/bspConfiguration/bspFlexCan/src/can/FlexCANDevice.cpp \
 *       external/openbsw/libs/bsw/cpp2can/src/can/canframes/CANFrame.cpp \
 *       platforms/s32k1xx/port/bspConfiguration/bspFlexCan/test/host/FlexCanDeviceHostTest.cpp -o flexcan_test.exe
 */

#include "can/FlexCANDevice.h"

#include <etl/error_handler.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <vector>

#if !defined(_WIN32) || !defined(_M_X64) && !defined(__x86_64__)
#error "the FlexCAN register model traps register accesses with Windows guard pages (x86-64)"
#endif

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

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

#define CHECK_EQ(a, b)                                                                         \
    do                                                                                         \
    {                                                                                          \
        ++checks;                                                                              \
        unsigned long long const va_ = static_cast<unsigned long long>(a);                     \
        unsigned long long const vb_ = static_cast<unsigned long long>(b);                     \
        if (va_ != vb_)                                                                        \
        {                                                                                      \
            ++failures;                                                                        \
            std::printf("  FAIL %s:%d: %s == %s (0x%llx != 0x%llx)\n", __FILE__, __LINE__, #a, #b, va_, vb_); \
        }                                                                                      \
    } while (false)

// ------------------------------------------------------------------------------------------ register model

constexpr uintptr_t REGION_BASE  = 0x40020000U; // allocation granularity 64 KiB, holds all three instances
constexpr size_t REGION_SIZE     = 0x10000U;
constexpr uint32_t PAGE_BYTES    = 0x1000U;
constexpr uint32_t MCR           = 0x00U;
constexpr uint32_t CTRL1         = 0x04U;
constexpr uint32_t ECR           = 0x1CU;
constexpr uint32_t ESR1          = 0x20U;
constexpr uint32_t IMASK1        = 0x28U;
constexpr uint32_t IFLAG1        = 0x30U;
constexpr uint32_t CTRL2         = 0x34U;
constexpr uint32_t RAM_OFFSET    = 0x80U;
constexpr uint32_t MB_BYTES      = 16U;
constexpr uint32_t RXIMR_OFFSET  = 0x880U;
constexpr uint32_t MCR_RESET     = 0xD890000FU; // [RM] p. 1806
constexpr uint32_t MCR_READ_ONLY = FLEXCAN_MCR_NOTRDY_MASK | FLEXCAN_MCR_FRZACK_MASK | FLEXCAN_MCR_LPMACK_MASK;
constexpr uint32_t CODE_TX_INACTIVE = 8U;
constexpr uint32_t CODE_TX_DATA     = 12U;
constexpr uint32_t CODE_RX_EMPTY    = 4U;
constexpr uint32_t CODE_RX_FULL     = 2U;

// Registers of the S32K1xx FlexCAN below the message buffer RAM ([RM] 55.4.2.1); everything else there is reserved.
constexpr uint32_t REGISTERS[] = {0x00U, 0x04U, 0x08U, 0x10U, 0x14U, 0x18U, 0x1CU, 0x20U,
                                  0x28U, 0x30U, 0x34U, 0x38U, 0x44U, 0x48U, 0x4CU, 0x50U};

struct Access
{
    uint32_t offset;
    bool write;
};

struct Instance
{
    explicit Instance(uintptr_t const address) : base(address) {}

    uintptr_t base;
    uint32_t buffers  = 0U; // 0: not modelled (page stays open)
    bool guarded      = false;
    uint32_t accesses = 0U;
    int highestBuffer = -1;          // highest message buffer (or its RXIMR) the driver touched
    std::vector<Access> errors;      // accesses to unimplemented address space
    std::vector<uint32_t> transmits; // buffers whose CODE the driver set to "transmit data frame"
    bool maxmbOutsideFreeze = false;
};

Instance instances[] = {Instance(0x40024000U), Instance(0x40025000U), Instance(0x4002B000U)};

struct Pending
{
    Instance* instance = nullptr;
    uint32_t offset    = 0U; // word aligned
    bool write         = false;
    uint32_t before    = 0U;
};

Pending pending;

uint32_t volatile& word(Instance const& inst, uint32_t const offset)
{
    return *reinterpret_cast<uint32_t volatile*>(inst.base + offset);
}

void protect(Instance const& inst, bool const open)
{
    DWORD old = 0U;
    (void)VirtualProtect(
        reinterpret_cast<void*>(inst.base), PAGE_BYTES, open ? PAGE_READWRITE : PAGE_NOACCESS, &old);
}

/** Opens the pages of all modelled instances while the test itself reads or writes registers. */
struct Unguard
{
    Unguard()
    {
        for (Instance const& inst : instances)
        {
            if (inst.guarded)
            {
                protect(inst, true);
            }
        }
    }

    ~Unguard()
    {
        for (Instance const& inst : instances)
        {
            if (inst.guarded)
            {
                protect(inst, false);
            }
        }
    }
};

bool isImplemented(Instance& inst, uint32_t const offset)
{
    for (uint32_t const reg : REGISTERS)
    {
        if (offset == reg)
        {
            return true;
        }
    }
    if ((offset >= RAM_OFFSET) && (offset < (RAM_OFFSET + (inst.buffers * MB_BYTES))))
    {
        int const mb = static_cast<int>((offset - RAM_OFFSET) / MB_BYTES);
        inst.highestBuffer = (mb > inst.highestBuffer) ? mb : inst.highestBuffer;
        return true;
    }
    if ((offset >= RXIMR_OFFSET) && (offset < (RXIMR_OFFSET + (inst.buffers * 4U))))
    {
        int const mb = static_cast<int>((offset - RXIMR_OFFSET) / 4U);
        inst.highestBuffer = (mb > inst.highestBuffer) ? mb : inst.highestBuffer;
        return true;
    }
    return false;
}

bool isFrozen(uint32_t const mcr)
{
    return ((mcr & FLEXCAN_MCR_MDIS_MASK) == 0U) && ((mcr & FLEXCAN_MCR_FRZ_MASK) != 0U)
           && ((mcr & FLEXCAN_MCR_HALT_MASK) != 0U);
}

/** Register semantics of a completed write (page open). */
void afterWrite(Instance& inst, uint32_t const offset, uint32_t const before)
{
    uint32_t const written = word(inst, offset);
    if (offset == MCR)
    {
        uint32_t value = (written & ~MCR_READ_ONLY) | (before & MCR_READ_ONLY);
        if (((written & FLEXCAN_MCR_SOFTRST_MASK) != 0U) && ((before & FLEXCAN_MCR_SOFTRST_MASK) == 0U))
        {
            // soft reset: MCR except MDIS back to its reset value, flags and masks cleared ([RM] p. 1810)
            value = (MCR_RESET & ~FLEXCAN_MCR_MDIS_MASK) | (written & FLEXCAN_MCR_MDIS_MASK)
                    | FLEXCAN_MCR_SOFTRST_MASK | (before & MCR_READ_ONLY);
            word(inst, IMASK1) = 0U;
            word(inst, IFLAG1) = 0U;
            word(inst, ESR1)   = 0U;
            word(inst, ECR)    = 0U;
        }
        else if (((value ^ before) & FLEXCAN_MCR_MAXMB_MASK) != 0U)
        {
            if ((before & FLEXCAN_MCR_FRZACK_MASK) == 0U)
            {
                inst.maxmbOutsideFreeze = true;
            }
        }
        word(inst, MCR) = value;
    }
    else if ((offset == IFLAG1) || (offset == ESR1))
    {
        word(inst, offset) = before & ~written; // write 1 to clear
    }
    else if ((offset >= RAM_OFFSET) && (offset < (RAM_OFFSET + (inst.buffers * MB_BYTES)))
             && (((offset - RAM_OFFSET) % MB_BYTES) == 0U))
    {
        uint32_t const code = (written >> 24U) & 0xFU;
        if ((code == CODE_TX_DATA) && (((before >> 24U) & 0xFU) != CODE_TX_DATA))
        {
            inst.transmits.push_back((offset - RAM_OFFSET) / MB_BYTES);
        }
    }
}

LONG CALLBACK trap(EXCEPTION_POINTERS* const info)
{
    EXCEPTION_RECORD const& record = *info->ExceptionRecord;
    if ((record.ExceptionCode == EXCEPTION_ACCESS_VIOLATION) && (record.NumberParameters >= 2U)
        && (pending.instance == nullptr))
    {
        uintptr_t const address = static_cast<uintptr_t>(record.ExceptionInformation[1]);
        for (Instance& inst : instances)
        {
            if (inst.guarded && (address >= inst.base) && (address < (inst.base + PAGE_BYTES)))
            {
                uint32_t const offset = static_cast<uint32_t>(address - inst.base) & ~3U;
                bool const write      = (record.ExceptionInformation[0] == 1U);
                ++inst.accesses;
                if (!isImplemented(inst, offset))
                {
                    inst.errors.push_back({offset, write});
                }
                protect(inst, true);
                pending = {&inst, offset, write, word(inst, offset)};
                info->ContextRecord->EFlags |= 0x100U; // trap flag: back here after this one instruction
                return EXCEPTION_CONTINUE_EXECUTION;
            }
        }
        return EXCEPTION_CONTINUE_SEARCH;
    }
    if ((record.ExceptionCode == EXCEPTION_SINGLE_STEP) && (pending.instance != nullptr))
    {
        Instance& inst = *pending.instance;
        if (pending.write)
        {
            afterWrite(inst, pending.offset, pending.before);
        }
        protect(inst, false);
        pending = Pending{};
        info->ContextRecord->EFlags &= ~0x100U;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

/** What the controller does on its own between two driver accesses: reset done, freeze entered or left. */
void settle()
{
    Unguard const open;
    for (Instance& inst : instances)
    {
        if (inst.buffers == 0U)
        {
            continue;
        }
        uint32_t mcr = word(inst, MCR) & ~FLEXCAN_MCR_SOFTRST_MASK;
        if ((mcr & FLEXCAN_MCR_MDIS_MASK) != 0U)
        {
            mcr |= FLEXCAN_MCR_LPMACK_MASK | FLEXCAN_MCR_NOTRDY_MASK;
            mcr &= ~FLEXCAN_MCR_FRZACK_MASK;
        }
        else if (isFrozen(mcr))
        {
            mcr = (mcr | FLEXCAN_MCR_FRZACK_MASK | FLEXCAN_MCR_NOTRDY_MASK) & ~FLEXCAN_MCR_LPMACK_MASK;
        }
        else
        {
            mcr &= ~(FLEXCAN_MCR_FRZACK_MASK | FLEXCAN_MCR_NOTRDY_MASK | FLEXCAN_MCR_LPMACK_MASK);
        }
        word(inst, MCR) = mcr;
    }
}

/** Instance `index` with `buffers` message buffers, in reset state; the other instances are not modelled. */
Instance& model(size_t const index, uint32_t const buffers)
{
    for (Instance& inst : instances)
    {
        if (inst.guarded)
        {
            protect(inst, true);
        }
        inst.buffers  = 0U;
        inst.guarded  = false;
        inst.accesses = 0U;
        inst.highestBuffer = -1;
        inst.errors.clear();
        inst.transmits.clear();
        inst.maxmbOutsideFreeze = false;
    }
    Instance& inst = instances[index];
    inst.buffers   = buffers;
    // whatever the RAM holds after power-up; registers at their reset values
    for (uint32_t offset = 0U; offset < PAGE_BYTES; offset += 4U)
    {
        word(inst, offset) = 0xA5A5A5A5U;
    }
    for (uint32_t const reg : REGISTERS)
    {
        word(inst, reg) = 0U;
    }
    word(inst, MCR) = MCR_RESET;
    inst.guarded    = true;
    protect(inst, false);
    return inst;
}

uint32_t reg(Instance const& inst, uint32_t const offset)
{
    Unguard const open;
    return word(inst, offset);
}

uint32_t csWord(Instance const& inst, uint32_t const mb) { return reg(inst, RAM_OFFSET + (mb * MB_BYTES)); }

uint32_t codeOf(Instance const& inst, uint32_t const mb) { return (csWord(inst, mb) >> 24U) & 0xFU; }

/** A frame arrives in receive buffer `mb` ([RM] 55.4.3: CS with CODE FULL, IDE, DLC; ID; data big endian). */
void receive(Instance& inst, uint32_t const mb, uint32_t const id, bool const extended, uint8_t const (&data)[8])
{
    Unguard const open;
    uint32_t const base = RAM_OFFSET + (mb * MB_BYTES);
    word(inst, base)    = (CODE_RX_FULL << 24U) | (extended ? (1U << 21U) : 0U) | (8U << 16U);
    word(inst, base + 4U) = extended ? id : (id << 18U);
    word(inst, base + 8U) = (static_cast<uint32_t>(data[0]) << 24U) | (static_cast<uint32_t>(data[1]) << 16U)
                            | (static_cast<uint32_t>(data[2]) << 8U) | data[3];
    word(inst, base + 12U) = (static_cast<uint32_t>(data[4]) << 24U) | (static_cast<uint32_t>(data[5]) << 16U)
                             | (static_cast<uint32_t>(data[6]) << 8U) | data[7];
    word(inst, IFLAG1) = word(inst, IFLAG1) | (1UL << mb);
}

/** The frame of transmit buffer `mb` went out: CODE back to inactive, flag set ([RM] 55.4.3 Table 55-11). */
void transmitted(Instance& inst, uint32_t const mb)
{
    Unguard const open;
    uint32_t const base = RAM_OFFSET + (mb * MB_BYTES);
    word(inst, base)    = (word(inst, base) & ~0x0F000000U) | (CODE_TX_INACTIVE << 24U);
    word(inst, IFLAG1)  = word(inst, IFLAG1) | (1UL << mb);
}

// ------------------------------------------------------------------------------------------ environment stubs

uint64_t nowUs         = 0U;
uint32_t pinSetups     = 0U;
uint32_t framesSent    = 0U;
uint32_t etlAsserts    = 0U;
uint32_t wakeupCleared = 0U;

void onFrameSent() { ++framesSent; }

void onEtlAssert(etl::exception const&) { ++etlAsserts; }

class Phy : public bios::CanPhy
{
public:
    void init(uint32_t) override { ++inits; }

    bool setMode(Mode const mode, uint32_t) override
    {
        fMode = mode;
        return true;
    }

    ErrorCode getPhyErrorStatus(uint32_t) override { return CAN_PHY_ERROR_NONE; }

    uint32_t inits = 0U;
};

class Power : public bios::IEcuPowerStateController
{
public:
    void startPreSleep() override {}

    uint32_t powerDown(uint8_t, tCheckWakeupDelegate) override { return 0U; }

    uint32_t powerDown(uint8_t) override { return 0U; }

    void fullPowerUp() override {}

    void setWakeupSourceMonitoring(uint32_t, bool, bool) override {}

    void clearWakeupSourceMonitoring(uint32_t) override { ++wakeupCleared; }

    bool setWakeupDelegate(tCheckWakeupDelegate&) override { return true; }

    uint32_t getWakeupSource() override { return 0U; }
};

} // namespace

namespace bios
{
void Io::setDefaultConfiguration(uint16_t) { ++pinSetups; }
} // namespace bios

extern "C"
{
uint64_t getSystemTimeUs64Bit(void)
{
    settle(); // the controller finishes its reset and mode changes while the driver waits
    return ++nowUs;
}

uint32_t getSystemTimeUs32Bit(void) { return static_cast<uint32_t>(nowUs); }
}

namespace
{
using bios::FlexCANDevice;
using Config = FlexCANDevice::Config;
using ErrorCode = ::can::ICanTransceiver::ErrorCode;

constexpr uint32_t CTRL1_500K = 0x04620006U; // eval board, 500 kbit/s (value is not interpreted by the model)

// Configurations as tools/bswgen generates them (canDeviceConfiguration.h): 32 buffers 14/2/15, 16 buffers 6/2/7.
constexpr Config CONFIG_32{0x40024000U, 500000U, CTRL1_500K, 1U, 2U, 14U, 2U, 15U, 0U, 0x100U, 32U};
constexpr Config CONFIG_16{0x4002B000U, 500000U, CTRL1_500K, 3U, 4U, 6U, 2U, 7U, 0U, 0x200U, 16U};
// Initialisers written for the OpenBSW driver (without numMessageBuffers) must keep their meaning: 0 = 32.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wmissing-field-initializers"
constexpr Config CONFIG_32_OPENBSW{0x40024000U, 500000U, CTRL1_500K, 1U, 2U, 14U, 2U, 15U, 0U, 0x100U};
// the split of the OpenBSW driver on FlexCAN2 (16 buffers): what COM-05 found
constexpr Config CONFIG_16_WITH_OPENBSW_SPLIT{0x4002B000U, 500000U, CTRL1_500K, 3U, 4U, 14U, 2U, 15U, 0U, 0x200U};
#pragma clang diagnostic pop

// ------------------------------------------------------------------------------ layout (compile time as well)

static_assert(FlexCANDevice::messageBufferLayout(CONFIG_32).count == 32U, "32 buffers");
static_assert(FlexCANDevice::messageBufferLayout(CONFIG_32_OPENBSW).count == 32U, "0 means 32");
static_assert(FlexCANDevice::messageBufferLayout(CONFIG_16).firstTransmit == 8U, "16 buffers: transmit from 8");
static_assert(FlexCANDevice::messageBufferLayout(CONFIG_16).callback == 15U, "16 buffers: callback in 15");
static_assert(FlexCANDevice::isValidLayout(CONFIG_32) && FlexCANDevice::isValidLayout(CONFIG_16), "generated");

Config with(Config config, uint8_t const std, uint8_t const ext, uint8_t const app, uint8_t const buffers)
{
    config.numRxBufsStd      = std;
    config.numRxBufsExt      = ext;
    config.numTxBufsApp      = app;
    config.numMessageBuffers = buffers;
    return config;
}

void testLayout()
{
    std::printf("layout per instance\n");
    FlexCANDevice::MessageBufferLayout const l32 = FlexCANDevice::messageBufferLayout(CONFIG_32);
    FlexCANDevice::MessageBufferLayout const l0  = FlexCANDevice::messageBufferLayout(CONFIG_32_OPENBSW);
    FlexCANDevice::MessageBufferLayout const l16 = FlexCANDevice::messageBufferLayout(CONFIG_16);
    CHECK_EQ(l32.count, 32U);
    CHECK_EQ(l32.firstTransmit, 16U); // same as e_TRANSMIT_BUFFER_START of OpenBSW
    CHECK_EQ(l32.callback, 31U);      // same as CALLBACK_TRANSMIT_BUFFER of OpenBSW
    CHECK(l0.count == l32.count && l0.firstTransmit == l32.firstTransmit && l0.callback == l32.callback);
    CHECK_EQ(l16.count, 16U);
    CHECK_EQ(l16.firstTransmit, 8U);
    CHECK_EQ(l16.callback, 15U);
    CHECK(FlexCANDevice::isValidLayout(CONFIG_32));
    CHECK(FlexCANDevice::isValidLayout(CONFIG_32_OPENBSW));
    CHECK(FlexCANDevice::isValidLayout(CONFIG_16));
    // the OpenBSW split does not fit 16 buffers; neither does one buffer more on either side
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_16, 14U, 2U, 15U, 16U)));
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_16, 7U, 2U, 7U, 16U)));
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_16, 6U, 2U, 8U, 16U)));
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_32, 14U, 3U, 15U, 32U)));
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_32, 14U, 2U, 16U, 32U)));
    // counts the flag registers cannot hold or that do not split
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_16, 0U, 0U, 0U, 34U)));
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_16, 1U, 0U, 0U, 15U)));
    CHECK(!FlexCANDevice::isValidLayout(with(CONFIG_16, 0U, 0U, 0U, 2U)));
    CHECK(FlexCANDevice::isValidLayout(with(CONFIG_16, 1U, 1U, 0U, 4U)));

    // the constructor checks the same at run time (ETL assert)
    Phy phy;
    Power power;
    model(2U, 16U);
    etlAsserts = 0U;
    {
        FlexCANDevice const ok(CONFIG_16, phy, power);
        CHECK_EQ(etlAsserts, 0U);
        CHECK_EQ(ok.getIndex(), 2U);
        CHECK_EQ(ok.getMessageBufferLayout().callback, 15U);
    }
    {
        FlexCANDevice const bad(CONFIG_16_WITH_OPENBSW_SPLIT, phy, power);
        CHECK_EQ(etlAsserts, 0U); // 0 means 32: nothing in the configuration knows the instance has 16
    }
    {
        Config const tooMany = with(CONFIG_16, 14U, 2U, 15U, 16U);
        FlexCANDevice const bad(tooMany, phy, power);
        CHECK_EQ(etlAsserts, 2U); // reception and transmission buffers
    }
    etlAsserts = 0U;
}

/** Initialises and starts `device` on `inst`; checks the message buffers against the instance. */
void startDevice(FlexCANDevice& device, Instance& inst, Config const& config)
{
    FlexCANDevice::MessageBufferLayout const layout = FlexCANDevice::messageBufferLayout(config);
    CHECK(device.init() == ErrorCode::CAN_ERR_OK);
    CHECK(inst.errors.empty());
    CHECK_EQ(inst.highestBuffer, layout.count - 1U); // the callback buffer is the last one the instance has
    CHECK(!inst.maxmbOutsideFreeze);
    uint32_t const mcr = reg(inst, MCR);
    CHECK_EQ((mcr & FLEXCAN_MCR_MAXMB_MASK) >> FLEXCAN_MCR_MAXMB_SHIFT, layout.count - 1U);
    CHECK((mcr & FLEXCAN_MCR_SRXDIS_MASK) != 0U);
    CHECK((mcr & FLEXCAN_MCR_IRMQ_MASK) != 0U);
    CHECK((mcr & FLEXCAN_MCR_FRZACK_MASK) != 0U); // still frozen: the port writes the receive filter now
    CHECK_EQ(reg(inst, CTRL1), config.clockSetupRegister);
    CHECK((reg(inst, CTRL2) & FLEXCAN_CTRL2_MRP_MASK) != 0U);
    uint32_t const rx = static_cast<uint32_t>(config.numRxBufsStd) + config.numRxBufsExt;
    for (uint32_t mb = 0U; mb < rx; ++mb)
    {
        CHECK_EQ(codeOf(inst, mb), CODE_RX_EMPTY);
        CHECK_EQ((csWord(inst, mb) >> 21U) & 1U, (mb >= config.numRxBufsStd) ? 1U : 0U); // IDE
        CHECK_EQ(reg(inst, RXIMR_OFFSET + (4U * mb)), 0U);
    }
    for (uint32_t mb = layout.firstTransmit; mb < (layout.firstTransmit + config.numTxBufsApp); ++mb)
    {
        CHECK_EQ(codeOf(inst, mb), CODE_TX_INACTIVE);
    }
    CHECK_EQ(codeOf(inst, layout.callback), CODE_TX_INACTIVE);
    CHECK_EQ(reg(inst, IMASK1), 0U);
    CHECK_EQ(pinSetups, 2U);

    CHECK(device.start() == ErrorCode::CAN_ERR_OK);
    CHECK(inst.errors.empty());
    CHECK_EQ(reg(inst, IMASK1), (1UL << rx) - 1U); // receive interrupts only
    CHECK_EQ(reg(inst, MCR) & (FLEXCAN_MCR_FRZ_MASK | FLEXCAN_MCR_HALT_MASK | FLEXCAN_MCR_NOTRDY_MASK), 0U);
    CHECK_EQ(wakeupCleared, 1U);
}

::can::CANFrame frame(uint32_t const id, uint8_t const first)
{
    uint8_t const payload[8] = {first, 1U, 2U, 3U, 4U, 5U, 6U, 7U};
    return ::can::CANFrame(id, payload, 8U);
}

/** One instance with `buffers` buffers: init/start, callback frame, shared vector, application buffers, mute. */
void testInstance(size_t const index, Config const& config, char const* const name)
{
    std::printf("%s\n", name);
    Instance& inst = model(index, config.numMessageBuffers == 0U ? 32U : config.numMessageBuffers);
    FlexCANDevice::MessageBufferLayout const layout = FlexCANDevice::messageBufferLayout(config);
    Phy phy;
    Power power;
    pinSetups = framesSent = wakeupCleared = etlAsserts = 0U;
    FlexCANDevice device(config, phy, ::etl::delegate<void()>::create<&onFrameSent>(), power);
    CHECK_EQ(etlAsserts, 0U);
    CHECK_EQ(device.getIndex(), index);
    startDevice(device, inst, config);
    uint32_t const rxMask = reg(inst, IMASK1);
    uint8_t const top     = static_cast<uint8_t>(config.numRxBufsStd + config.numRxBufsExt - 1U);

    // (1) a frame before anything was sent, in the highest (extended) receive buffer, through the handler of a
    // shared vector (transmit part first, as os/isr/isr_can.cpp does): received, nothing reported as sent, the
    // buffer keeps its interrupt. The OpenBSW driver started with the transmit mask of buffer 15 - a receive buffer
    // of the 32-buffer split, cleared and disabled here.
    uint8_t const d1[8] = {0x11U, 0x22U, 0x33U, 0x44U, 0x55U, 0x66U, 0x77U, 0x88U};
    receive(inst, top, 0x18DA00F1U, true, d1);
    device.transmitISR();
    CHECK_EQ(framesSent, 0U);
    CHECK_EQ(reg(inst, IMASK1), rxMask);
    CHECK_EQ(device.receiveISR(), 1U);
    CHECK(!device.isRxQueueEmpty());
    if (!device.isRxQueueEmpty())
    {
        ::can::CANFrame const& rx = device.getRxFrameQueueFront();
        CHECK_EQ(rx.getId(), ::can::CanId::extended(0x18DA00F1U));
        CHECK_EQ(rx.getPayloadLength(), 8U);
        CHECK(std::memcmp(rx.getPayload(), d1, 8U) == 0);
        device.dequeueRxFrame();
    }
    CHECK_EQ(reg(inst, IFLAG1) & (1UL << top), 0U);

    // (2) frame with sent callback: the last buffer of the instance
    ::can::CANFrame const request = frame(0x7E8U, 0x10U);
    uint8_t const callbackBuffer  = device.getTransmitBuffer(request, true);
    CHECK_EQ(callbackBuffer, layout.callback);
    CHECK(device.transmit(request, callbackBuffer, true) == ErrorCode::CAN_ERR_OK);
    CHECK_EQ(inst.transmits.size(), 1U);
    CHECK(!inst.transmits.empty() && (inst.transmits.back() == layout.callback));
    CHECK_EQ(reg(inst, IMASK1), rxMask | (1UL << layout.callback));
    CHECK_EQ((reg(inst, RAM_OFFSET + (layout.callback * MB_BYTES) + 4U) >> 18U) & 0x7FFU, 0x7E8U);
    CHECK_EQ(reg(inst, RAM_OFFSET + (layout.callback * MB_BYTES) + 8U), 0x10010203U);
    CHECK_EQ(device.getTransmitBuffer(request, true), FlexCANDevice::TRANSMIT_BUFFER_UNAVAILABLE); // busy

    // (3) while it is on the bus a frame arrives: the shared handler must neither report the pending frame as sent
    // nor clear its flag or mask
    uint8_t const d2[8] = {1U, 2U, 3U, 4U, 5U, 6U, 7U, 8U};
    receive(inst, 0U, 0x7E0U, false, d2);
    device.transmitISR();
    CHECK_EQ(framesSent, 0U);
    CHECK_EQ(reg(inst, IMASK1), rxMask | (1UL << layout.callback));
    CHECK_EQ(device.receiveISR(), 1U);
    if (!device.isRxQueueEmpty())
    {
        CHECK_EQ(device.getRxFrameQueueFront().getId(), 0x7E0U);
        device.dequeueRxFrame();
    }
    // now it went out, together with the next received frame: both are handled by one pass of the handler
    transmitted(inst, layout.callback);
    receive(inst, 1U, 0x7DFU, false, d2);
    device.transmitISR();
    CHECK_EQ(framesSent, 1U);
    CHECK_EQ(reg(inst, IMASK1), rxMask); // transmit interrupt off again
    CHECK_EQ(reg(inst, IFLAG1) & (1UL << layout.callback), 0U);
    CHECK_EQ(device.receiveISR(), 1U);
    device.dequeueRxFrame();
    device.transmitISR(); // nothing pending any more
    CHECK_EQ(framesSent, 1U);
    CHECK_EQ(device.getTransmitBuffer(request, true), layout.callback);

    // (4) application buffers without callback: exactly firstTransmit .. firstTransmit + numTxBufsApp - 1
    std::vector<uint8_t> used;
    for (uint32_t i = 0U; i < 40U; ++i)
    {
        ::can::CANFrame const f = frame(0x100U + i, static_cast<uint8_t>(i));
        uint8_t const mb        = device.getTransmitBuffer(f, false);
        if (mb == FlexCANDevice::TRANSMIT_BUFFER_UNAVAILABLE)
        {
            break;
        }
        CHECK(device.transmit(f, mb, false) == ErrorCode::CAN_ERR_OK);
        used.push_back(mb);
    }
    CHECK_EQ(used.size(), config.numTxBufsApp);
    for (size_t i = 0U; i < used.size(); ++i)
    {
        CHECK_EQ(used[i], layout.firstTransmit + i);
    }
    CHECK_EQ(reg(inst, IMASK1), rxMask); // no transmit interrupt for them
    // a buffer outside the application range is refused, the callback buffer only as such
    CHECK(device.transmit(frame(0x200U, 0U), static_cast<uint8_t>(layout.firstTransmit - 1U), false)
          == ErrorCode::CAN_ERR_TX_FAIL);
    CHECK(device.transmit(frame(0x200U, 0U), layout.count, false) == ErrorCode::CAN_ERR_TX_FAIL);

    // (5) mute with a callback frame still pending: every transmit buffer inactive, nothing outside the instance
    CHECK(device.transmit(request, layout.callback, true) == ErrorCode::CAN_ERR_OK);
    CHECK_EQ(codeOf(inst, layout.callback), CODE_TX_DATA);
    device.mute();
    for (uint32_t mb = layout.firstTransmit; mb < layout.count; ++mb)
    {
        CHECK_EQ(codeOf(inst, mb), CODE_TX_INACTIVE);
    }
    device.stop();
    CHECK_EQ(reg(inst, IMASK1), 0U);
    CHECK(inst.errors.empty());
    CHECK_EQ(inst.highestBuffer, layout.count - 1U);
    CHECK_EQ(etlAsserts, 0U);
    for (Access const& a : inst.errors)
    {
        std::printf("  access error at offset 0x%03x (%s)\n", a.offset, a.write ? "write" : "read");
    }
}

/** Gegenprobe: the split of the OpenBSW driver on FlexCAN2 with 16 buffers - the model reports the access error. */
void testOpenBswSplitOn16Buffers()
{
    std::printf("OpenBSW split (receive 0..15, transmit 16..31) on an instance with 16 buffers\n");
    Instance& inst = model(2U, 16U);
    Phy phy;
    Power power;
    pinSetups = 0U;
    FlexCANDevice device(CONFIG_16_WITH_OPENBSW_SPLIT, phy, power);
    (void)device.init();
    CHECK(!inst.errors.empty());
    if (!inst.errors.empty())
    {
        // first access beyond the instance: buffer 16 at 0x180 (COM-05: 0x4002B180), a write of init()
        CHECK_EQ(inst.errors.front().offset, 0x180U);
        CHECK(inst.errors.front().write);
        std::printf("  first access error at 0x%08llx (%s), %u in total\n",
                    static_cast<unsigned long long>(inst.base + inst.errors.front().offset),
                    inst.errors.front().write ? "write" : "read", static_cast<unsigned>(inst.errors.size()));
    }
    // and MAXMB 31 on an instance whose last buffer is 15
    CHECK_EQ((reg(inst, MCR) & FLEXCAN_MCR_MAXMB_MASK) >> FLEXCAN_MCR_MAXMB_SHIFT, 31U);
}

// Accesses of the test itself through the guard, as the driver would make them. The fences tell the compiler that the
// exception handler changes the model (like a signal handler); the driver's accesses sit behind calls anyway.
uint32_t trappedRead(Instance const& inst, uint32_t const offset)
{
    std::atomic_signal_fence(std::memory_order_seq_cst);
    uint32_t const value = word(inst, offset);
    std::atomic_signal_fence(std::memory_order_seq_cst);
    return value;
}

void trappedWrite(Instance const& inst, uint32_t const offset, uint32_t const value)
{
    std::atomic_signal_fence(std::memory_order_seq_cst);
    word(inst, offset) = value;
    std::atomic_signal_fence(std::memory_order_seq_cst);
}

/** The model itself: accesses outside the instance are seen, W1C and soft reset behave as the manual says. */
void testModel()
{
    std::printf("register model\n");
    Instance& inst = model(1U, 16U);
    trappedWrite(inst, RAM_OFFSET + (16U * MB_BYTES), 0U); // buffer 16 of an instance with 16: access error
    CHECK_EQ(inst.errors.size(), 1U);
    CHECK(!inst.errors.empty() && inst.errors.back().write && (inst.errors.back().offset == 0x180U));
    (void)trappedRead(inst, RXIMR_OFFSET + (15U * 4U)); // RXIMR15 exists
    CHECK_EQ(inst.errors.size(), 1U);
    (void)trappedRead(inst, RXIMR_OFFSET + (16U * 4U)); // RXIMR16 does not
    CHECK_EQ(inst.errors.size(), 2U);
    CHECK(!inst.errors.empty() && !inst.errors.back().write && (inst.errors.back().offset == 0x8C0U));
    (void)trappedRead(inst, 0x24U); // IMASK2: not on an instance with at most 32 buffers
    CHECK_EQ(inst.errors.size(), 3U);
    {
        Unguard const open;
        word(inst, IFLAG1) = 0x0000F00FU;
    }
    trappedWrite(inst, IFLAG1, 0x00000003U); // write 1 to clear
    CHECK_EQ(reg(inst, IFLAG1), 0x0000F00CU);
    trappedWrite(inst, MCR, trappedRead(inst, MCR) & ~FLEXCAN_MCR_MDIS_MASK);
    trappedWrite(inst, MCR, trappedRead(inst, MCR) | FLEXCAN_MCR_SOFTRST_MASK);
    CHECK((reg(inst, MCR) & FLEXCAN_MCR_SOFTRST_MASK) != 0U);
    CHECK_EQ(reg(inst, IFLAG1), 0U);
    settle();
    CHECK_EQ(reg(inst, MCR) & FLEXCAN_MCR_SOFTRST_MASK, 0U);
    CHECK((reg(inst, MCR) & FLEXCAN_MCR_FRZACK_MASK) != 0U); // FRZ and HALT are set after reset
    CHECK_EQ(reg(inst, MCR) & FLEXCAN_MCR_MAXMB_MASK, 0x0FU);
    CHECK_EQ(inst.accesses, 9U);
}

} // namespace

int main()
{
    void* const region = VirtualAlloc(
        reinterpret_cast<void*>(REGION_BASE), REGION_SIZE, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (region != reinterpret_cast<void*>(REGION_BASE))
    {
        std::printf("FAIL: cannot map the FlexCAN register model at 0x%08llx (error %lu)\n",
                    static_cast<unsigned long long>(REGION_BASE), GetLastError());
        return 1;
    }
    (void)AddVectoredExceptionHandler(1U, &trap);
    etl::set_assert_function(&onEtlAssert);

    testModel();
    testLayout();
    testInstance(0U, CONFIG_32, "FlexCAN0 with 32 message buffers (eval board CAN0, S32K14x)");
    testInstance(0U, CONFIG_32_OPENBSW, "FlexCAN0 with 32 message buffers, initialiser without numMessageBuffers");
    testInstance(2U, CONFIG_16, "FlexCAN2 with 16 message buffers (S32K144 diagnostic CAN)");
    Config config16on1 = CONFIG_16;
    config16on1.baseAddress = 0x40025000U;
    testInstance(1U, config16on1, "FlexCAN1 with 16 message buffers (S32K142/S32K144)");
    // the smallest split tools/bswgen generates (CAN_MIN_MESSAGE_BUFFERS): MAXMB 7 differs from the reset value 0x0F
    // in its low bits, so a MAXMB ORed into the reset value (OpenBSW) shows here
    Config config8 = CONFIG_16;
    config8.baseAddress       = 0x40025000U;
    config8.numRxBufsStd      = 2U;
    config8.numTxBufsApp      = 3U;
    config8.numMessageBuffers = 8U;
    testInstance(1U, config8, "FlexCAN1 modelled with 8 message buffers (smallest split of tools/bswgen)");
    testOpenBswSplitOn16Buffers();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
