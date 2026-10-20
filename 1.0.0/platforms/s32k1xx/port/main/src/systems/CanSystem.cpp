/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "systems/CanSystem.h"

#include "async/Async.h"
#include "async/AsyncBinding.h"
#include "async/Config.h"
#include "async/Hook.h"
#include "bsp/can/canBusOffConfiguration.h"
#include "busid/BusId.h"
#include "can/CanBusOffRecovery.h"
#include "can/CanChannelStatus.h"
#include "can/CanFrameLog.h"
#include "can/CanLogger.h"
#include "can/canframes/CanId.h"
#include "can/CanPhyGate.h"
#include "can/CanSupervisionCycle.h"
#include "can/filter/IntervalFilter.h"
#include "can/framemgmt/IFilteredCANFrameSentListener.h"
#include "console/ConsoleContext.h"
#include "lifecycle/StaticBsp.h"

#include "interrupt_manager.h"
#include "interrupts/interruptPriorities.h"

// MaxEntrySize of the buffered logger: every "can log" line must fit one entry (COM-25, static_assert below).
#include <logger/Config.h>

// Safe_<handler> of every message buffer vector (declared in IsrWrapper.h, defined in IsrWrapper.cpp); the
// handlers themselves are in os/isr/isr_can.cpp, both as in the OpenBSW reference application (COM-08).
#include <safeLifecycle/IsrWrapper.h>
#include <util/format/SharedStringWriter.h>
#include <util/logger/Logger.h>

// COM-08: receive filter of every bus, derived by the generator from what the project receives (DBC messages of
// the ECU description and the diagnostic ids, tools/rtegen). Without the generated header the hardware keeps
// accepting every identifier; the software filter of the transceiver still keeps foreign ids out of the queue.
#if __has_include("can/canRxFilterConfiguration.h")
#include "can/canRxFilterConfiguration.h"
#define NX_CAN_RX_FILTER_GENERATED 1
#else
#define NX_CAN_RX_FILTERS(X)
#define NX_CAN_RX_FILTER_GENERATED 0
#endif

// COM-46/NVM-08: bus-off of every bus whose DTC the diagnostic description names (event can.busoff.<BUS>, generated
// dem/demBswReporters.h NX_DEM_CAN_BUSOFF, tools/rtegen). Without such a DTC nothing of it is compiled.
#if __has_include("dem/demBswReporters.h")
#include "dem/demBswReporters.h"
#endif
#ifdef NX_DEM_CAN_BUSOFF
#include "can/CanBusOffReport.h"
#endif

namespace
{
/**
 * Cycle of the transceiver and bus supervision (::can::CanSupervisionCycle), 10 ms.
 *
 * Transmit gate on report (COM-23): the phy readiness passes three samplers (phy provider, e.g. the SBC driver
 * in task bsp; this gate; the RTE in its CAN task). With a gate of its own 10 ms cycle the RTE saw a transceiver
 * that is not ready up to just under 20 ms after the provider reported it and could still write a frame in
 * between; COM-22 therefore ran the gate every millisecond (+1.15 % CPU at the eval board, on every board). Now
 * the provider reports the change (::can::CanPhyReadiness -> phyReadinessChanged), the gate runnable runs in this
 * task right away - above the RTE task - and mutes before the RTE's next cycle: QLF_ERROR within one RTE cycle
 * after the report and no frame written after it (host model in
 * libs/bsw/canSystem/test/host/CanPhyGateHostTest.cpp). The 10 ms cycle checks the gate as well, as the fallback
 * for a provider that does not report; one check costs a virtual call per bus that returns a value the provider
 * keeps - no SPI, no register access.
 *
 * Bus state every 10 ms, as before: that is the cycle of the consumers (rte::CAN_PERIOD_MS, 10 ms in the
 * demo project), and entering bus off is not a fast event - the transmit error counter has to pass 255.
 * A bus off that is over again inside one cycle would be missed by the poll, which is why the latched
 * flag ESR1[BOFFINT] is read as well; since COM-35 Channel::updateBusState() clears it every cycle and feeds it to
 * the monitor, so "ever bus off" comes from the monitor's counter (BusStateTransceiver::sawBusOffSinceInit()
 * only tells a bus off since the last cycle).
 */
::can::CanSupervisionCycle supervisionCycle;

#ifdef NX_DEM_CAN_BUSOFF
/**
 * COM-46/NVM-08: one bus-off report per bus with a DTC, fed by Channel::updateBusState() in the task of the CAN system
 * (can/CanBusOffReport.h): FAILED while the bus is off, PASSED while it is on the bus, a bus-off seen only through the
 * latched BOFFINT as FAILED and PASSED. Only recording - the recovery (COM-35) is not touched. The fault memory starts
 * later (RteSystem); what happened before is reported once it runs.
 */
struct BusOffDtc
{
    uint8_t busId;
    ::can::CanBusOffReport report;
};

#define NX_CAN_BUSOFF_DTC(bus, event) BusOffDtc{::busid::bus, ::can::CanBusOffReport(event)},
BusOffDtc busOffDtcs[] = {NX_DEM_CAN_BUSOFF(NX_CAN_BUSOFF_DTC)};
#undef NX_CAN_BUSOFF_DTC

void reportBusOff(uint8_t const busId, ::can::CanBusState const state, bool const enteredBusOff)
{
    for (BusOffDtc& dtc : busOffDtcs)
    {
        if (dtc.busId == busId)
        {
            dtc.report.update(state, enteredBusOff);
        }
    }
}
#endif

/**
 * Where the console commands of the CAN system run (COM-24; since SYS-13 the general ::console::ContextCall of
 * libs/bsw/consoleContext, shared by every system with a console command).
 *
 * The command itself - parsing, formatting, the synchronous UART output of 86.8 us per character - runs
 * in the console context (TASK_IDLE, ::console::CONSOLE_CONTEXT), the context of the console input and the
 * logger output, which exists in every async binding and is the lowest one in every project. Only the part
 * that reads or changes the state of a bus (the status snapshot, a send with its gate check, the log flag)
 * runs in the task of the CAN system, as before, so nothing here needs a lock. Until COM-24 the whole command
 * ran in task can: "can status" held it - and the gate, reception and DoCAN/UDS in the same task - for
 * 40-50 ms.
 *
 * consoleOwner is the task of the CAN system (set by its constructor, there is one instance).
 */
::async::ContextType consoleOwner = ::async::CONTEXT_INVALID;

/** Runs \p work (short, no output) in the task of the CAN system and waits for it. */
template<class Work>
void inCanTask(Work& work)
{
    ::console::runInOwnerContext(consoleOwner, work);
}

enum Id
{
    ID_STATUS,
    ID_SEND,
    ID_LOG,
};

char const* stateName(::can::ICanTransceiver::State const state)
{
    using State = ::can::ICanTransceiver::State;
    switch (state)
    {
        case State::CLOSED: return "closed";
        case State::INITIALIZED: return "initialized";
        case State::WAKING: return "waking";
        case State::OPEN: return "open";
        case State::MUTED: return "muted";
        default: return "?";
    }
}

char const* errorName(::can::ICanTransceiver::ErrorCode const error)
{
    using ErrorCode = ::can::ICanTransceiver::ErrorCode;
    switch (error)
    {
        case ErrorCode::CAN_ERR_OK: return "ok";
        case ErrorCode::CAN_ERR_TX_FAIL: return "tx failed";
        case ErrorCode::CAN_ERR_TX_HW_QUEUE_FULL: return "hw queue full";
        case ErrorCode::CAN_ERR_TX_OFFLINE: return "offline";
        case ErrorCode::CAN_ERR_ILLEGAL_STATE: return "transceiver not ready";
        case ErrorCode::CAN_ERR_INIT_FAILED: return "init failed";
        default: return "?";
    }
}

/** State the OpenBSW driver keeps for a transceiver (::can::ICANTransceiverStateListener). */
char const* driverStateName(::can::ICANTransceiverStateListener::CANTransceiverState const state)
{
    using CANTransceiverState = ::can::ICANTransceiverStateListener::CANTransceiverState;
    switch (state)
    {
        case CANTransceiverState::ACTIVE: return "active";
        case CANTransceiverState::PASSIVE: return "passive";
        case CANTransceiverState::BUS_OFF: return "bus off";
        default: return "?";
    }
}

/**
 * Transceiver of a bus, or the dummy phy (always "no status", i.e. ready) if the integration wired none.
 * Keeps the CAN system working when a board describes a transceiver no module serves.
 */
::bios::CanPhy& phyOf(::systems::CanSystem::PhyTable const phys, uint8_t const index)
{
    if ((index < phys.size()) && (phys[index] != nullptr))
    {
        return *phys[index];
    }
    return ::bios::CanPhyDummy::getInstance();
}

/**
 * Enables one CAN interrupt, but only if the vector table points to \p handler. A board port writes its
 * vector table for its derivative and the buses it knows; a bus whose vectors are missing must keep its
 * interrupts disabled, they would reach DefaultISR. The thumb bit is not part of the comparison.
 */
void enableCanIrq(char const* const bus, IRQn_Type const irq, uint8_t const level, void (*handler)())
{
    auto const* const table = reinterpret_cast<uint32_t const*>(SCB->VTOR);
    uint32_t const entry    = table[16U + static_cast<uint32_t>(irq)];
    if ((entry & ~1UL) == (reinterpret_cast<uint32_t>(handler) & ~1UL))
    {
        SYS_SetPriority(irq, level);
        SYS_EnableIRQ(irq);
    }
    else
    {
        ::util::logger::Logger::error(
            ::util::logger::CAN,
            "%s: interrupt %d has no vector in the board port, it stays disabled",
            bus,
            static_cast<int>(irq));
    }
}

/**
 * Acceptance filter of one receive message buffer, as generated (NX_CAN_RX_FILTERS). \p id and \p mask are
 * right aligned (11 or 29 bit); mask bit 1 = identifier bit is compared, 0 = don't care.
 */
struct RxFilter
{
    uint8_t busId;
    uint8_t buffer;
    uint32_t id;
    uint32_t mask;
    bool extended;
};

// clang-format off
#define NX_CAN_RX_FILTER_ENTRY(bus, buffer, id, mask, extended) {::busid::bus, buffer, id, mask, extended},
RxFilter const RX_FILTERS[] = {NX_CAN_RX_FILTERS(NX_CAN_RX_FILTER_ENTRY) {0xFFU, 0xFFU, 0U, 0U, false}};
#undef NX_CAN_RX_FILTER_ENTRY
// clang-format on

// The generator and the FlexCAN driver agree on the buffer split: buffers 0..numRxBufsStd-1 receive standard ids,
// the next numRxBufsExt extended ids (FlexCANDevice::init() sets IDE per buffer this way). A table that disagrees
// stops the build here instead of silently receiving nothing.
#define NX_CAN_RX_FILTER_CHECK(bus, buffer, id, mask, extended)                                              \
    static_assert(                                                                                            \
        (extended) ? (((buffer) >= ::bsp::can::bus##_DEVICE_CONFIG.numRxBufsStd)                              \
                      && ((buffer) < (::bsp::can::bus##_DEVICE_CONFIG.numRxBufsStd                            \
                                      + ::bsp::can::bus##_DEVICE_CONFIG.numRxBufsExt)))                       \
                   : ((buffer) < ::bsp::can::bus##_DEVICE_CONFIG.numRxBufsStd),                               \
        "receive filter of " #bus ": buffer " #buffer " is no receive buffer of this identifier type");      \
    static_assert(                                                                                            \
        (((id) | (mask)) & ~((extended) ? 0x1FFFFFFFUL : 0x7FFUL)) == 0U,                                     \
        "receive filter of " #bus ": identifier or mask " #id "/" #mask " wider than the identifier");
NX_CAN_RX_FILTERS(NX_CAN_RX_FILTER_CHECK)
#undef NX_CAN_RX_FILTER_CHECK
#if NX_CAN_RX_FILTER_GENERATED
// Both generators must use the buffer split of the driver: fewer buffers in the table would silently switch buffers
// off, more would be refused above only for the buffers that exist. COM-26: the split is per bus (message buffers
// of its FlexCAN instance), and every bus of NX_CAN_BUSES must have one - a missing one does not compile.
#define NX_CAN_RX_FILTER_SPLIT_CHECK(bus, index, busId, device, deviceName)                                  \
    static_assert(                                                                                            \
        (NX_CAN_RX_FILTER_BUFFERS_STD_##bus == ::bsp::can::bus##_DEVICE_CONFIG.numRxBufsStd)                  \
            && (NX_CAN_RX_FILTER_BUFFERS_EXT_##bus == ::bsp::can::bus##_DEVICE_CONFIG.numRxBufsExt),          \
        "receive filter of " #bus ": buffer split differs from the device configuration");
NX_CAN_BUSES(NX_CAN_RX_FILTER_SPLIT_CHECK)
#undef NX_CAN_RX_FILTER_SPLIT_CHECK
#endif

/**
 * Message buffer layout of the FlexCAN RAM with 8 byte payload ([RM] Rev. 13, 55.4.3 "Message buffer structure",
 * table 55-10, p. 1877): control/status word, identifier word, two data words. The standard identifier sits in
 * bits 28..18 of the identifier word, the extended one in bits 28..0; the individual mask RXIMRn uses the same
 * alignment ([RM] 55.4.2.5.4 RXMGMASK table, p. 1818: MG[28:0] masks MB[ID]).
 */
constexpr uint32_t MB_WORDS          = 4U;
constexpr uint32_t MB_ID_WORD        = 1U;
constexpr uint32_t MB_STD_ID_SHIFT   = 18U;
constexpr uint32_t MB_CODE_INACTIVE  = 0U; // control/status word 0: CODE 0000 = receive buffer not active

/**
 * Writes the receive filter of one bus into the message buffers and the individual masks.
 *
 * Only in Freeze mode: RXIMRn "can only be accessed by the CPU when the module is in Freeze mode; otherwise, they
 * are blocked by hardware" ([RM] Rev. 13, 55.4.2.18.2, p. 1845, repeated on p. 1900), which is why this runs between
 * CanFlex2Transceiver::init() (FlexCANDevice::init() leaves the controller frozen, all masks 0 = accept every id)
 * and open() (leaves Freeze mode); [RM] 55.6.1 p. 1944 orders it the same way (message buffers, then RXIMRn, then
 * HALT negated). The driver sets MCR[IRMQ], so the individual masks are in effect ([RM] p. 1817). CTRL2[EACEN]
 * stays 0: IDE is always compared, RTR never ([RM] p. 1818, notes 2 and 3), so a standard buffer never takes an
 * extended frame. Several buffers with the same filter form a reception queue ([RM] p. 1900).
 *
 * A receive buffer without an entry is deactivated: the project receives nothing that it could hold.
 * Returns the number of filtered buffers, -1 if the controller was not frozen (nothing written).
 */
int32_t applyReceiveFilter(::bios::FlexCANDevice::Config const& config, uint8_t const busId)
{
    auto* const reg = reinterpret_cast<FLEXCAN_Type*>(config.baseAddress);
    if ((reg->MCR & FLEXCAN_MCR_FRZACK_MASK) == 0U)
    {
        return -1;
    }
    uint32_t const buffers = static_cast<uint32_t>(config.numRxBufsStd) + config.numRxBufsExt;
    int32_t filtered       = 0;
    for (uint32_t mb = 0U; mb < buffers; ++mb)
    {
        RxFilter const* entry = nullptr;
        for (RxFilter const& filter : RX_FILTERS)
        {
            if ((filter.busId == busId) && (filter.buffer == mb))
            {
                entry = &filter;
            }
        }
        if (entry == nullptr)
        {
            reg->RAMn[mb * MB_WORDS] = MB_CODE_INACTIVE;
            continue;
        }
        uint32_t const shift                    = entry->extended ? 0U : MB_STD_ID_SHIFT;
        reg->RXIMR[mb]                          = entry->mask << shift;
        reg->RAMn[(mb * MB_WORDS) + MB_ID_WORD] = entry->id << shift;
        ++filtered;
    }
    return filtered;
}

/**
 * Value of a register field. The NXP device headers only offer **setter** macros
 * (FLEXCAN_ESR1_FLTCONF(x) shifts x left and masks it), so passing a whole register into them returns
 * nonsense: the board reported "FLTCONF 32" for ESR1 0x00240082 and TEC << 8 as REC (COM-09). Reading a
 * field is mask first, then shift back.
 */
constexpr uint32_t fieldValue(uint32_t const reg, uint32_t const mask, uint32_t const shift)
{
    return (reg & mask) >> shift;
}

/**
 * Compile time guard for the field this project decides the signal qualifier with (COM-12). ESR1
 * 0x00240082 was measured at the board on 2026-09-16 with FLTCONF 0 (error active, TEC 30, REC 0, the
 * bus was never off); the setter macro answers 0x20 for the very same register, which is how a healthy
 * bus looked like "bus off" to the OpenBSW driver and made every received message QLF_ERROR. Should
 * anyone replace the field read by the macro again, the target build stops here.
 */
constexpr uint32_t BOARD_ESR1_ERROR_ACTIVE = 0x00240082U; // FLTCONF 0, ERRINT and IDLE set
constexpr uint32_t BOARD_ESR1_BUS_OFF      = 0x002400A6U; // same register with FLTCONF 2
static_assert(
    fieldValue(BOARD_ESR1_ERROR_ACTIVE, FLEXCAN_ESR1_FLTCONF_MASK, FLEXCAN_ESR1_FLTCONF_SHIFT) == 0U,
    "FLTCONF of the ESR1 measured at the board is 0 (error active), not a bus error");
static_assert(
    fieldValue(BOARD_ESR1_BUS_OFF, FLEXCAN_ESR1_FLTCONF_MASK, FLEXCAN_ESR1_FLTCONF_SHIFT) == 2U,
    "FLTCONF 2 is bus off and has to be read as such");
static_assert(
    FLEXCAN_ESR1_FLTCONF(BOARD_ESR1_ERROR_ACTIVE) == 0x20U,
    "FLEXCAN_ESR1_FLTCONF is a setter macro: it re-encodes the register instead of reading the field");

/** FLTCONF of ESR1 (fault confinement state of the CAN protocol machine). */
char const* faultConfinementName(uint32_t const fltconf)
{
    switch (fltconf)
    {
        case 0U: return "error active";
        case 1U: return "error passive";
        default: return "bus off"; // 2 and 3 both mean bus off
    }
}

/**
 * Counts every frame a bus writes, whoever wrote it. The RTE (rte::CAN_TX_STATUS) and the diagnostic stack
 * write to the transceiver object directly; only the console command "can send" goes through
 * CanSystem::Channel::send(), so its counters saw a fraction of the traffic (COM-09). The transceiver
 * notifies its sent listeners for every write it accepted from any user.
 *
 * One counter per bus, kept in this translation unit and found by bus id: the class CanSystem::Channel is
 * declared in the board port's header, which this task may not change (a tidier variant would be a member,
 * diff proposal in doc/changelog.d/COM-09.md).
 *
 * \warning canFrameSent() may run in interrupt context (transmit interrupt). The counter is a plain 32 bit
 * increment for diagnostics only: a lost increment costs one frame in the console output, nothing else.
 */
class TxCounter final : public ::can::IFilteredCANFrameSentListener
{
public:
    TxCounter()
    : _filter(0U, ::can::IntervalFilter::MAX_ID) // every id; notifySentListeners() does not ask anyway
    , _busId(0xFFU)
    , _count(0U)
    {}

    void canFrameSent(::can::CANFrame const& /* frame */) override { ++_count; }

    ::can::IFilter& getFilter() override { return _filter; }

    uint8_t getBusId() const { return _busId; }

    void setBusId(uint8_t const busId) { _busId = busId; }

    uint32_t count() const { return _count; }

private:
    // Interval over the whole id range, not a bit field: a BitFieldFilter would cost 256 byte of RAM per bus
    // for a filter nobody evaluates.
    ::can::IntervalFilter _filter;
    uint8_t _busId;
    uint32_t _count;
};

TxCounter txCounters[(NX_CAN_DEVICE_COUNT > 0) ? NX_CAN_DEVICE_COUNT : 1];

/** Counter of the bus with this id; claims a free slot on the first call (CanSystem::Channel::start()). */
TxCounter& txCounterOf(uint8_t const busId)
{
    for (TxCounter& counter : txCounters)
    {
        if (counter.getBusId() == busId)
        {
            return counter;
        }
    }
    for (TxCounter& counter : txCounters)
    {
        if (counter.getBusId() == 0xFFU)
        {
            counter.setBusId(busId);
            return counter;
        }
    }
    return txCounters[0];
}

/**
 * FlexCAN part of the pause after bus off (COM-35). [RM] Rev. 13, CTRL1[BOFFREC] (p. 1815): with the bit set the
 * controller "remains in Bus Off state until the bit is negated by the user"; negated after the 128 x 11
 * recessive bits it rejoins after 11 recessive bits, negated earlier it recovers as if the bit had never been
 * set; "after negation, the BOFFREC bit can be reasserted again during Bus Off, but it will be effective only the
 * next time the module enters Bus Off". The field is not one of the CTRL1 fields that are blocked outside Freeze
 * mode, so it is written while the controller runs. The OpenBSW driver writes CTRL1 only in init() (Freeze mode:
 * 0, then the generated bit timing), which starts every bus with automatic recovery; start() sets the bit after
 * open().
 */
void holdBusOffRecovery(FLEXCAN_Type* const reg) { reg->CTRL1 = reg->CTRL1 | FLEXCAN_CTRL1_BOFFREC_MASK; }

/** Lets a held controller leave bus off and holds the next bus off again (see holdBusOffRecovery()). */
void releaseBusOff(FLEXCAN_Type* const reg)
{
    reg->CTRL1 = reg->CTRL1 & ~FLEXCAN_CTRL1_BOFFREC_MASK;
    reg->CTRL1 = reg->CTRL1 | FLEXCAN_CTRL1_BOFFREC_MASK;
}

#ifndef NX_PRODUCT_BUILD // SYS-119: only "can send" parses numbers itself
/** Decimal, hexadecimal (0x...) or octal (0...) number of a console token, like scanIntToken(). */
bool parseUnsigned(::util::string::ConstString const& token, uint32_t& value)
{
    size_t const length = token.length();
    char const* const p = token.data();
    size_t i            = 0U;
    uint32_t base       = 10U;
    if (length == 0U)
    {
        return false;
    }
    if ((length > 2U) && (p[0] == '0') && ((p[1] == 'x') || (p[1] == 'X')))
    {
        base = 16U;
        i    = 2U;
    }
    else if ((length > 1U) && (p[0] == '0'))
    {
        base = 8U;
        i    = 1U;
    }
    uint32_t result = 0U;
    for (; i < length; ++i)
    {
        char const c = p[i];
        uint32_t digit;
        if ((c >= '0') && (c <= '9'))
        {
            digit = static_cast<uint32_t>(c - '0');
        }
        else if ((c >= 'a') && (c <= 'f'))
        {
            digit = static_cast<uint32_t>(c - 'a') + 10U;
        }
        else if ((c >= 'A') && (c <= 'F'))
        {
            digit = static_cast<uint32_t>(c - 'A') + 10U;
        }
        else
        {
            return false;
        }
        if (digit >= base)
        {
            return false;
        }
        result = (result * base) + digit;
    }
    value = result;
    return true;
}
#endif
} // namespace

namespace
{
/** MaxEntrySize, T and Timestamp of a logger::declare::BufferedLoggerOutput (logger/BufferedLoggerOutput.h). */
template<class Output>
struct LoggerEntryLimits;

template<
    uint32_t BufferSize,
    class Lock,
    uint8_t MaxEntrySize,
    class T,
    class E,
    class Timestamp,
    class ReadOnlyPredicate>
struct LoggerEntryLimits<
    ::logger::declare::BufferedLoggerOutput<BufferSize, Lock, MaxEntrySize, T, E, Timestamp, ReadOnlyPredicate>>
{
    static constexpr size_t MAX_ENTRY_BYTES = MaxEntrySize;
    static constexpr size_t LENGTH_BYTES    = sizeof(T);
    static constexpr size_t TIMESTAMP_BYTES = sizeof(Timestamp);
};

using LoggerLimits = LoggerEntryLimits<::logger::BufferedLoggerOutputType>;

// COM-25: every "can log" line must fit one entry of the buffered logger, otherwise its end prints as "<?>".
static_assert(
    ::can::CanFrameLog::entryBytes(LoggerLimits::LENGTH_BYTES, LoggerLimits::TIMESTAMP_BYTES)
        <= LoggerLimits::MAX_ENTRY_BYTES,
    "a can log line does not fit one logger entry: shorten CanFrameLog::LINE_CAPACITY (libs/bsw/canSystem)");

// SYS-16: bus and controller names are "%s" arguments of the log lines of this file (and of the UDS/XCP start
// lines, busid names); the lines are sized for these lengths, tools/logcheck (bounds.yaml) relies on the asserts.
constexpr size_t CAN_DEVICE_NAME_MAX_CHARS = 16U;

constexpr size_t textLength(char const* const text)
{
    size_t length = 0U;
    while (text[length] != '\0')
    {
        ++length;
    }
    return length;
}

#define NX_CAN_CHECK_NAMES(bus, index, busId, device, deviceName)                                             \
    static_assert(textLength(#bus) <= ::can::CanFrameLog::BUS_NAME_CHARS,                                    \
                  "CAN bus name " #bus " longer than CanFrameLog::BUS_NAME_CHARS (log lines)");              \
    static_assert(textLength(deviceName) <= CAN_DEVICE_NAME_MAX_CHARS,                                       \
                  "CAN controller name of " #bus " longer than CAN_DEVICE_NAME_MAX_CHARS (log lines)");
NX_CAN_BUSES(NX_CAN_CHECK_NAMES)
#undef NX_CAN_CHECK_NAMES
#define NX_BUSID_CHECK_NAME(bus, id, index)                                                                   \
    static_assert(textLength(#bus) <= ::can::CanFrameLog::BUS_NAME_CHARS,                                    \
                  "bus id name " #bus " longer than CanFrameLog::BUS_NAME_CHARS (log lines)");
NX_BUSID_CAN_BUSES(NX_BUSID_CHECK_NAME)
#undef NX_BUSID_CHECK_NAME
} // namespace

namespace systems
{
using ::util::logger::CAN;
using ::util::logger::Logger;

// -------------------------------------------------------------------- transceiver with a real bus state

CanSystem::BusStateTransceiver::BusStateTransceiver(
    ::async::ContextType const context,
    uint8_t const busId,
    ::bios::FlexCANDevice::Config const& config,
    ::bios::CanPhy& phy,
    ::bios::IEcuPowerStateController& powerStateController)
: ::bios::CanFlex2Transceiver(context, busId, config, phy, powerStateController)
, _busConfig(config)
{}

uint8_t CanSystem::BusStateTransceiver::getFaultConfinement() const
{
    auto const* const reg = reinterpret_cast<FLEXCAN_Type const*>(_busConfig.baseAddress);
    return static_cast<uint8_t>(
        fieldValue(reg->ESR1, FLEXCAN_ESR1_FLTCONF_MASK, FLEXCAN_ESR1_FLTCONF_SHIFT));
}

::can::CanBusState CanSystem::BusStateTransceiver::getBusState() const
{
    // Only a started controller says anything about the bus; before that ESR1 is not meaningful.
    ::can::ICanTransceiver::State const state = getState();
    if ((state != ::can::ICanTransceiver::State::OPEN)
        && (state != ::can::ICanTransceiver::State::MUTED))
    {
        return ::can::CanBusState::UNKNOWN;
    }
    return ::can::CanBusMonitor::fromFaultConfinement(getFaultConfinement());
}

::can::ICANTransceiverStateListener::CANTransceiverState
CanSystem::BusStateTransceiver::getCANTransceiverState() const
{
    using CANTransceiverState = ::can::ICANTransceiverStateListener::CANTransceiverState;
    switch (getBusState())
    {
        case ::can::CanBusState::BUS_OFF: return CANTransceiverState::BUS_OFF;
        case ::can::CanBusState::ERROR_PASSIVE: return CANTransceiverState::PASSIVE;
        // ERROR_ACTIVE, and UNKNOWN while the controller is not started: whether CAN is up at all is
        // answered by getState(), not by this state (the RTE checks both, COM-03).
        default: return CANTransceiverState::ACTIVE;
    }
}

::can::ICANTransceiverStateListener::CANTransceiverState
CanSystem::BusStateTransceiver::getDriverState() const
{
    // Qualified call: the driver's latched value, not our override (no recursion).
    return ::can::AbstractCANTransceiver::getCANTransceiverState();
}

bool CanSystem::BusStateTransceiver::isRecoveryAutomatic() const
{
    auto const* const reg = reinterpret_cast<FLEXCAN_Type const*>(_busConfig.baseAddress);
    // BOFFREC == 0 enables automatic recovery (reference manual). The OpenBSW driver writes CTRL1 from
    // the generated bit timing only (clockSetupRegister), so the reset value 0 stays and the controller
    // leaves bus off by itself after 128 sequences of 11 recessive bits.
    return fieldValue(reg->CTRL1, FLEXCAN_CTRL1_BOFFREC_MASK, FLEXCAN_CTRL1_BOFFREC_SHIFT) == 0U;
}

bool CanSystem::BusStateTransceiver::sawBusOffSinceInit() const
{
    auto const* const reg = reinterpret_cast<FLEXCAN_Type const*>(_busConfig.baseAddress);
    return fieldValue(reg->ESR1, FLEXCAN_ESR1_BOFFINT_MASK, FLEXCAN_ESR1_BOFFINT_SHIFT) != 0U;
}

// ------------------------------------------------------------------------------------------ one bus

CanSystem::Channel::Channel(
    char const* const name,
    char const* const device,
    uint8_t const busId,
    ::async::ContextType const context,
    ::bios::FlexCANDevice::Config const& config,
    ::can::CanBusOffRecovery::Config const& busOffConfig,
    ::bios::CanPhy& phy,
    ::bios::IEcuPowerStateController& powerStateController)
: _name(name)
, _device(device)
, _busId(busId)
, _config(config)
, _phy(phy)
, _transceiver(context, busId, config, phy, powerStateController)
, _filter()
, _busMonitor()
, _busOffRecovery(busOffConfig)
, _rxCount(0U)
, _txOk(0U)
, _txFailed(0U)
, _txRefused(0U)
, _muteCount(0U)
, _driverNotifications(0U)
, _driverBusOffReports(0U)
, _driverPhyErrors(0U)
, _logging(false)
{}

bool CanSystem::Channel::isPhyReady() const
{
    return ::can::CanPhyGate::isReady(_phy, _config.BusId);
}

bool CanSystem::Channel::isTransmitAllowed() const
{
    return ::can::CanPhyGate::isTransmitAllowed(
        _transceiver.getState() == ::can::ICanTransceiver::State::OPEN, isPhyReady());
}

void CanSystem::Channel::start()
{
    // COM-08: the console listener no longer opens the transceiver's filter for every standard id. The filter bit
    // field is the union of all listeners (RTE: received DBC messages, DoCAN: diagnostic ids); the interrupt only
    // queues ids from it, so foreign traffic cannot fill the 32 frame queue. "can log" shows what the bus'
    // users receive. The class keeps its name and match() from the board port header (diff proposal COM-08).
    _filter.clear();
    _transceiver.addCANFrameListener(*this);
    // Counts every frame written to this bus, whoever wrote it: the RTE (rte::CAN_TX_STATUS) and the
    // diagnostic stack talk to the transceiver directly, only the console command goes through send().
    _transceiver.addCANFrameSentListener(txCounterOf(_busId));
    // Notifications of the driver's own cyclic service. CanFlex2Transceiver::open() schedules
    // cyclicTask() in this task every 10 ms by itself, so nothing has to call it here; listening makes
    // that service and its (unreliable) verdict visible in "can status" (COM-12).
    _transceiver.setStateListener(*this);

    ::can::ICanTransceiver::ErrorCode const initResult = _transceiver.init();
    // Hardware receive filter while the controller is still frozen after init() (see applyReceiveFilter).
    if (NX_CAN_RX_FILTER_GENERATED == 0)
    {
        Logger::warn(CAN, "%s: no generated receive filter, the hardware accepts every id", _name);
    }
    else if (initResult == ::can::ICanTransceiver::ErrorCode::CAN_ERR_OK)
    {
        int32_t const filtered = applyReceiveFilter(_config, _busId);
        if (filtered < 0)
        {
            Logger::error(CAN, "%s: controller not frozen, receive filter not written", _name);
        }
        else
        {
            // COM-26: with the message buffers of the instance (16 or 32), the split the driver uses
            Logger::info(
                CAN,
                "%s receive filter: %d of %u buffers, %u message buffers",
                _name,
                static_cast<int>(filtered),
                static_cast<unsigned>(_config.numRxBufsStd + _config.numRxBufsExt),
                static_cast<unsigned>(::bios::FlexCANDevice::messageBufferLayout(_config).count));
        }
    }
    ::can::ICanTransceiver::ErrorCode const openResult = _transceiver.open();
    ::can::CanBusOffRecovery const& recovery = _busOffRecovery;
    // BOFFREC as the bit timing left it, before COM-35 sets it for the pause after bus off
    bool const recoveryAutomatic = _transceiver.isRecoveryAutomatic();
    bool const opened            = (initResult == ::can::ICanTransceiver::ErrorCode::CAN_ERR_OK)
                        && (openResult == ::can::ICanTransceiver::ErrorCode::CAN_ERR_OK);
    if (opened && recovery.holdsRecovery())
    {
        holdBusOffRecovery(reinterpret_cast<FLEXCAN_Type*>(_config.baseAddress));
    }
    if (!opened)
    {
        Logger::error(
            CAN,
            "%s start failed: init %s, open %s",
            _name,
            errorName(initResult),
            errorName(openResult));
    }
    else
    {
        // SYS-16: with "CTRL1 timing" the line needed 135 bytes of a 128-byte logger entry (16-character names)
        Logger::info(
            CAN,
            "%s open: %s %u bit/s, CTRL1 0x%08x, bus off recovery %s",
            _name,
            _device,
            static_cast<unsigned>(_transceiver.getBaudrate()),
            static_cast<unsigned>(_config.clockSetupRegister),
            recovery.holdsRecovery() ? "paused" : (recoveryAutomatic ? "automatic" : "manual"));
    }
    if (!recoveryAutomatic && !recovery.holdsRecovery())
    {
        // Apart from BOFFREC for the pause after bus off (COM-35) nothing here writes CTRL1 - the register belongs
        // to the driver. A bit timing that disables the automatic recovery without the pause of COM-35 has to say
        // who restarts the controller (no such project today).
        Logger::error(
            CAN,
            "%s: CTRL1 BOFFREC is set, the controller will not leave bus off by itself",
            _name);
    }
    // Mute before the first frame can be written if the transceiver is not ready yet.
    updateTransmitPermission();
    // First bus state right after opening, so "can status" does not show "unknown" until the next cycle.
    updateBusState(0U);
}

void CanSystem::Channel::stop()
{
    (void)_transceiver.close();
    _transceiver.shutdown();
    (void)_busMonitor.stop();
    _busOffRecovery.stop();
    _transceiver.removeStateListener();
    _transceiver.removeCANFrameSentListener(txCounterOf(_busId));
    _transceiver.removeCANFrameListener(*this);
}

void CanSystem::Channel::updateTransmitPermission()
{
    ::can::ICanTransceiver::State const state = _transceiver.getState();
    switch (::can::CanPhyGate::decide(
        state == ::can::ICanTransceiver::State::OPEN,
        state == ::can::ICanTransceiver::State::MUTED,
        isPhyReady()))
    {
        case ::can::CanPhyGate::Action::MUTE:
        {
            (void)_transceiver.mute();
            ++_muteCount;
            Logger::warn(CAN, "%s muted: transceiver not ready", _name);
            break;
        }
        case ::can::CanPhyGate::Action::UNMUTE:
        {
            (void)_transceiver.unmute();
            Logger::info(CAN, "%s unmuted: transceiver ready", _name);
            break;
        }
        default:
        {
            // no change
            break;
        }
    }
}

void CanSystem::Channel::updateBusState(uint32_t const elapsedMs)
{
    auto* const reg                    = reinterpret_cast<FLEXCAN_Type*>(_config.baseAddress);
    ::can::CanBusOffRecovery& recovery = _busOffRecovery;
    // One read of ESR1 for the fault confinement state and the latched bus off flag, so both belong together.
    uint32_t const esr1 = reg->ESR1;
    ::can::CanBusState const state
        = (_transceiver.getBusState() == ::can::CanBusState::UNKNOWN)
              ? ::can::CanBusState::UNKNOWN
              : ::can::CanBusMonitor::fromFaultConfinement(static_cast<uint8_t>(
                  fieldValue(esr1, FLEXCAN_ESR1_FLTCONF_MASK, FLEXCAN_ESR1_FLTCONF_SHIFT)));
    // COM-35: BOFFINT latches every entry into bus off. While bus off is held it tells a new bus off after a release
    // from the one before (both read FLTCONF 3); without holding it catches a bus off that was over before this
    // reading. It is cleared here (write 1 to that flag only; the other flags of ESR1 are not touched), so
    // "ever bus off" in "can status" comes from the monitor's counter as well.
    bool enteredBusOff = false;
    if ((state != ::can::CanBusState::UNKNOWN)
        && (fieldValue(esr1, FLEXCAN_ESR1_BOFFINT_MASK, FLEXCAN_ESR1_BOFFINT_SHIFT) != 0U))
    {
        reg->ESR1     = FLEXCAN_ESR1_BOFFINT_MASK;
        enteredBusOff = true;
    }
    ::can::CanBusMonitor::Event events[2];
    uint8_t const eventCount
        = ::can::CanBusOffRecovery::feedMonitor(_busMonitor, state, enteredBusOff, elapsedMs, events);
#ifdef NX_DEM_CAN_BUSOFF
    reportBusOff(_busId, state, enteredBusOff); // COM-46: the fault memory, recording only
#endif
    uint32_t const ecr = reg->ECR;
    unsigned const tec
        = static_cast<unsigned>(fieldValue(ecr, FLEXCAN_ECR_TXERRCNT_MASK, FLEXCAN_ECR_TXERRCNT_SHIFT));
    unsigned const rec
        = static_cast<unsigned>(fieldValue(ecr, FLEXCAN_ECR_RXERRCNT_MASK, FLEXCAN_ECR_RXERRCNT_SHIFT));
    for (uint8_t i = 0U; i < eventCount; ++i)
    {
        switch (events[i])
        {
            case ::can::CanBusMonitor::Event::WENT_BUS_OFF:
            {
                // COM-35: only the first bus off of a series; the rest is counted and summarised below
                if (recovery.wentBusOff())
                {
                    Logger::error(
                        CAN,
                        "%s bus off (%u times), TEC %u, REC %u, recovery %s",
                        _name,
                        static_cast<unsigned>(_busMonitor.busOffCount()),
                        tec,
                        rec,
                        recovery.holdsRecovery()
                            ? "paused"
                            : (_transceiver.isRecoveryAutomatic() ? "automatic" : "manual"));
                }
                break;
            }
            case ::can::CanBusMonitor::Event::RECOVERED:
            {
                if (recovery.recovered())
                {
                    Logger::info(
                        CAN,
                        "%s back on the bus after %u ms bus off (%u recoveries), TEC %u, REC %u",
                        _name,
                        static_cast<unsigned>(_busMonitor.busOffMs()),
                        static_cast<unsigned>(_busMonitor.recoveries()),
                        tec,
                        rec);
                }
                break;
            }
            case ::can::CanBusMonitor::Event::WENT_PASSIVE:
            {
                // Not a bus error for the signal qualifier: the bus still works. A node alone on the bus
                // reaches this state regularly (no acknowledge for its own frames). Within a series of bus offs
                // it is the way into the next one: not logged (COM-35).
                if (!recovery.inSeries())
                {
                    Logger::warn(
                        CAN,
                        "%s error passive (%u times), TEC %u, REC %u",
                        _name,
                        static_cast<unsigned>(_busMonitor.passiveCount()),
                        tec,
                        rec);
                }
                break;
            }
            case ::can::CanBusMonitor::Event::WENT_ACTIVE:
            {
                if (!recovery.inSeries())
                {
                    Logger::info(CAN, "%s error active again, TEC %u, REC %u", _name, tec, rec);
                }
                break;
            }
            case ::can::CanBusMonitor::Event::STOPPED:
            {
                recovery.stop();
                break;
            }
            default:
            {
                // NONE, STARTED (already logged by start())
                break;
            }
        }
    }

    ::can::CanBusOffRecovery::Step const step = recovery.cycle(_busMonitor.isBusOff(), elapsedMs);
    if (step.discard)
    {
        // The frames waiting in the transmit buffers (application and transport buffer) are dropped the way the
        // transmit gate drops them (CanFlex2Transceiver::mute(): buffers inactive, queue of the transport cleared,
        // its sender runs into its own timeout), and the bus is open again right away. Held: the controller is
        // still off the bus, no frame is in transmission. A muted bus has no pending frames.
        if (_transceiver.getState() == ::can::ICanTransceiver::State::OPEN)
        {
            (void)_transceiver.mute();
            (void)_transceiver.unmute();
        }
    }
    if (step.logDiscard)
    {
        Logger::warn(
            CAN,
            "%s: pending frames dropped at bus off %u of a series",
            _name,
            static_cast<unsigned>(recovery.seriesBusOffs()));
    }
    if (step.release)
    {
        releaseBusOff(reg);
    }
    if (step.logSummary)
    {
        Logger::warn(
            CAN,
            "%s: %u more bus offs in %u ms, %u in series, pause %u ms",
            _name,
            static_cast<unsigned>(recovery.summaryBusOffs()),
            static_cast<unsigned>(recovery.summaryWindowMs()),
            static_cast<unsigned>(recovery.seriesBusOffs()),
            static_cast<unsigned>(recovery.pauseMs()));
    }
    if (step.logSeriesEnd)
    {
        Logger::info(
            CAN,
            "%s: bus off series over, %u bus offs in %u ms, %u drops",
            _name,
            static_cast<unsigned>(recovery.seriesBusOffs()),
            static_cast<unsigned>(recovery.seriesMs()),
            static_cast<unsigned>(recovery.seriesDiscards()));
    }
}

void CanSystem::Channel::canTransceiverStateChanged(
    ::can::ICanTransceiver& /* transceiver */,
    ::can::ICANTransceiverStateListener::CANTransceiverState const state)
{
    ++_driverNotifications;
    if (state == ::can::ICANTransceiverStateListener::CANTransceiverState::BUS_OFF)
    {
        ++_driverBusOffReports;
    }
    // Deliberately not logged per notification: the driver reports a spurious bus off as soon as ERRINT
    // is latched (see BusStateTransceiver) and would flood the console. "can status" shows the counters
    // next to our own reading, which is what makes the difference visible.
}

void CanSystem::Channel::phyErrorOccurred(::can::ICanTransceiver& /* transceiver */)
{
    ++_driverPhyErrors;
}

::can::ICanTransceiver::ErrorCode CanSystem::Channel::send(::can::CANFrame const& frame)
{
    // Check again here: the transceiver may have reported "not ready" since the last supervision cycle.
    updateTransmitPermission();
    if (!isTransmitAllowed())
    {
        ++_txRefused;
        return ::can::ICanTransceiver::ErrorCode::CAN_ERR_ILLEGAL_STATE;
    }
    ::can::ICanTransceiver::ErrorCode const result = _transceiver.write(frame);
    if (result == ::can::ICanTransceiver::ErrorCode::CAN_ERR_OK)
    {
        ++_txOk;
    }
    else
    {
        ++_txFailed;
    }
    return result;
}

void CanSystem::Channel::frameReceived(::can::CANFrame const& frame)
{
    ++_rxCount;
    if (_logging)
    {
        // One logger entry per line of at most 16 bytes (COM-25): the former single call with twelve arguments
        // did not fit one entry and lost the 8th byte ("<?>"); CAN FD frames give up to four lines.
        uint32_t const id   = ::can::CanId::rawId(frame.getId());
        bool const extended = ::can::CanId::isExtended(frame.getId());
        size_t const length = frame.getPayloadLength();
        size_t const lines  = ::can::CanFrameLog::lineCount(length);
        for (size_t line = 0U; line < lines; ++line)
        {
            char text[::can::CanFrameLog::LINE_CAPACITY];
            (void)::can::CanFrameLog::formatLine(text, _name, id, extended, frame.getPayload(), length, line);
            Logger::info(CAN, ::can::CanFrameLog::FORMAT, text);
        }
    }
}

void CanSystem::Channel::writeStatus(::util::format::StringWriter& out) const
{
    // Snapshot in the task of the CAN system (a few register and member reads), output in the console
    // context (COM-24): the task no longer waits for the UART, and all lines of a bus show one moment.
    ::can::CanChannelStatus status{};
    auto const take = [this, &status]()
    {
        auto const* const reg = reinterpret_cast<FLEXCAN_Type const*>(_config.baseAddress);
        uint32_t const esr1   = reg->ESR1;
        uint32_t const ecr    = reg->ECR;
        status.name            = _name;
        status.state           = stateName(_transceiver.getState());
        status.phyReady        = isPhyReady();
        status.transmitAllowed = isTransmitAllowed();

        status.busState          = ::can::CanBusMonitor::name(_busMonitor.state());
        status.busOffCount       = _busMonitor.busOffCount();
        status.busOffNowMs       = _busMonitor.isBusOff() ? _busMonitor.busOffMs() : 0U;
        status.longestBusOffMs   = _busMonitor.longestBusOffMs();
        status.recoveries        = _busMonitor.recoveries();
        status.passiveCount      = _busMonitor.passiveCount();
        // COM-35: while bus off is held, updateBusState() clears BOFFINT after each bus off - the counter of the
        // monitor remembers it; "recovery automatic" also when the pause of COM-35 releases the controller
        ::can::CanBusOffRecovery const& recovery = _busOffRecovery;
        status.everBusOff = _transceiver.sawBusOffSinceInit() || (_busMonitor.busOffCount() > 0U);
        status.automaticRecovery = _transceiver.isRecoveryAutomatic() || recovery.holdsRecovery();

        ::can::ICANTransceiverStateListener::CANTransceiverState const driverState
            = _transceiver.getDriverState();
        status.driverState = driverStateName(driverState);
        status.driverDisagrees
            = (driverState != ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE)
              && !_busMonitor.isBusOff();
        status.driverNotifications = _driverNotifications;
        status.driverBusOffReports = _driverBusOffReports;
        status.driverPhyErrors     = _driverPhyErrors;

        status.bitrate     = _transceiver.getBaudrate();
        status.ctrl1       = reg->CTRL1;
        status.esr1        = esr1;
        status.fltconf     = fieldValue(esr1, FLEXCAN_ESR1_FLTCONF_MASK, FLEXCAN_ESR1_FLTCONF_SHIFT);
        status.fltconfName = faultConfinementName(status.fltconf);
        status.txwrn       = fieldValue(esr1, FLEXCAN_ESR1_TXWRN_MASK, FLEXCAN_ESR1_TXWRN_SHIFT);
        status.rxwrn       = fieldValue(esr1, FLEXCAN_ESR1_RXWRN_MASK, FLEXCAN_ESR1_RXWRN_SHIFT);
        status.synch       = fieldValue(esr1, FLEXCAN_ESR1_SYNCH_MASK, FLEXCAN_ESR1_SYNCH_SHIFT);
        status.tec         = fieldValue(ecr, FLEXCAN_ECR_TXERRCNT_MASK, FLEXCAN_ECR_TXERRCNT_SHIFT);
        status.rec         = fieldValue(ecr, FLEXCAN_ECR_RXERRCNT_MASK, FLEXCAN_ECR_RXERRCNT_SHIFT);

        status.rx             = _rxCount;
        status.tx             = txCounterOf(_busId).count();
        status.consoleOk      = _txOk;
        status.consoleFailed  = _txFailed;
        status.consoleRefused = _txRefused;
        status.txBufferBusy   = _transceiver.getOverrunCount();
        status.muteCount      = _muteCount;
    };
    inCanTask(take);
    ::can::writeChannelStatus(out, status);
}

// ------------------------------------------------------------------------------------------ the system

CanSystem::CanSystem(
    ::async::ContextType const context, StaticBsp& staticBsp, PhyTable const phys)
: ::lifecycle::SingleContextLifecycleComponent(context)
, ::etl::singleton_base<CanSystem>(*this)
, _context(context)
// one channel per bus of the project, with the transceiver the integration wired to it
#define NX_CAN_CHANNEL_INIT(bus, index, busId, device, deviceName) \
    , _bus##bus(                                                                               \
          #bus,                                                                                \
          deviceName,                                                                          \
          ::busid::bus,                                                                        \
          context,                                                                             \
          ::bsp::can::bus##_DEVICE_CONFIG,                                                     \
          ::bsp::can::bus##_BUS_OFF_CONFIG,                                                    \
          phyOf(phys, index),                                                                  \
          staticBsp.getPowerStateController())
NX_CAN_BUSES(NX_CAN_CHANNEL_INIT)
#undef NX_CAN_CHANNEL_INIT
, _buses{
#define NX_CAN_CHANNEL_POINTER(bus, index, busId, device, deviceName) \
    &_bus##bus,
      NX_CAN_BUSES(NX_CAN_CHANNEL_POINTER)
#undef NX_CAN_CHANNEL_POINTER
  }
, _canRxRunnable(*this)
, _gateRunnable(*this)
, _timeout()
#if !defined(NX_CONSOLE) || NX_CONSOLE
, _command(*this)
, _asyncCommand(_command, ::console::CONSOLE_CONTEXT) // COM-24: the command runs in the console context
#endif
{
    consoleOwner = context;
}

void CanSystem::init() { transitionDone(); }

void CanSystem::run()
{
    _canRxRunnable.setEnabled(true);
    // Interrupts per bus (receive and transmit message buffers) with the level of the bus' interrupt rank
    // (project interrupts.ranks, PLAT-21), enabled only where the board port has the vectors. Before ARCH-07
    // main() did this for CAN0 with a fixed priority.
#define NX_CAN_ENABLE_IRQ_RX(bus, device, isr, irq)                                                      \
    static_assert(textLength(#bus) <= ::can::CanFrameLog::BUS_NAME_CHARS, "CAN bus name " #bus " too long"); \
    enableCanIrq(#bus, irq, NX_IRQ_LEVEL_CAN_##bus, &Safe_##isr);
#define NX_CAN_ENABLE_IRQ_TX(bus, device, isr, irq)    NX_CAN_ENABLE_IRQ_RX(bus, device, isr, irq)
#define NX_CAN_ENABLE_IRQ_RX_TX(bus, device, isr, irq) NX_CAN_ENABLE_IRQ_RX(bus, device, isr, irq)
    NX_CAN_MB_VECTORS(NX_CAN_ENABLE_IRQ)
#undef NX_CAN_ENABLE_IRQ_RX
#undef NX_CAN_ENABLE_IRQ_TX
#undef NX_CAN_ENABLE_IRQ_RX_TX
    // COM-23: listen to the phy providers before the buses start, so no change between the first gate decision
    // (Channel::start) and the registration is lost; a report during the start runs the gate once more afterwards.
    _gateRunnable.setEnabled(true);
    if (!::can::CanPhyReadiness::setListener(*this))
    {
        Logger::error(CAN, "phy readiness has another listener, transmit gate every %u ms only",
                      static_cast<unsigned>(::can::CanSupervisionCycle::GATE_CYCLE_MS));
    }
    for (Channel* const bus : buses())
    {
        bus->start();
    }

    supervisionCycle = ::can::CanSupervisionCycle();
    ::async::scheduleAtFixedRate(
        _context,
        *this,
        _timeout,
        supervisionCycle.gateCycleMs(),
        ::async::TimeUnit::MILLISECONDS);
    transitionDone();
}

void CanSystem::shutdown()
{
    ::can::CanPhyReadiness::removeListener(*this);
    _gateRunnable.setEnabled(false);
    _timeout.cancel();
    for (Channel* const bus : buses())
    {
        bus->stop();
    }
    _canRxRunnable.setEnabled(false);

    transitionDone();
}

::can::ICanTransceiver* CanSystem::getCanTransceiver(uint8_t const busId)
{
    for (Channel* const bus : buses())
    {
        if (bus->getBusId() == busId)
        {
            return &bus->getTransceiver();
        }
    }
    return nullptr;
}

void CanSystem::dispatchRxTask() { ::async::execute(_context, _canRxRunnable); }

void CanSystem::execute()
{
    // Every 10 ms: bus state, and the gate as the fallback of the reports (see supervisionCycle above).
    bool const busStateDue = supervisionCycle.run();
    for (Channel* const bus : buses())
    {
        bus->updateTransmitPermission();
        if (busStateDue)
        {
            bus->updateBusState(supervisionCycle.busStateCycleMs());
        }
    }
}

void CanSystem::phyReadinessChanged(::bios::CanPhy& /* phy */)
{
    // Task of the provider (e.g. bsp). The gate itself runs in the CAN task, like every other access to a bus; all
    // buses are checked there, one virtual call each, so a provider serving several buses needs one report.
    ::async::execute(_context, _gateRunnable);
}

void CanSystem::updateTransmitPermissions()
{
    for (Channel* const bus : buses())
    {
        bus->updateTransmitPermission();
    }
}

void CanSystem::GateRunnable::execute()
{
    if (_enabled)
    {
        _parent.updateTransmitPermissions();
    }
}

CanSystem::Channel* CanSystem::findBus(::util::string::ConstString const& name)
{
    for (Channel* const bus : buses())
    {
        if (name.compareIgnoreCase(::util::string::ConstString(bus->getName())) == 0)
        {
            return bus;
        }
    }
    return nullptr;
}

CanSystem::CanRxRunnable::CanRxRunnable(CanSystem& parent) : _parent(parent), _enabled(false) {}

void CanSystem::CanRxRunnable::execute()
{
    if (_enabled)
    {
        for (Channel* const bus : _parent.buses())
        {
            bus->receiveTask();
        }
    }
}

// ------------------------------------------------------------------------------------------ console

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(CanSystem::Command, "can", "CAN buses of the project")
COMMAND_GROUP_COMMAND(
    ID_STATUS,
    "status",
    "transceiver and phy state, bus state with bus off counters (FLTCONF/TEC/REC), frames of all "
    "users and transmit buffer (param: [<bus>])")
#ifndef NX_PRODUCT_BUILD // SYS-119: any frame with any id on any bus, not in the product image
COMMAND_GROUP_COMMAND(
    ID_SEND,
    "send",
    "send a standard frame if the transceiver is ready (param: [<bus>] <id> [<byte> ...], up to 8 bytes)")
#endif
COMMAND_GROUP_COMMAND(ID_LOG, "log", "log received frames (param: 0|1 [<bus>])")
DEFINE_COMMAND_GROUP_GET_INFO_END

CanSystem::Channel* CanSystem::Command::scanOptionalBus(::util::command::CommandContext& context)
{
    if (!context.hasToken())
    {
        return nullptr;
    }
    ::util::string::ConstString const token = context.scanToken();
    Channel* const bus                      = _system.findBus(token);
    (void)context.check(bus != nullptr);
    return bus;
}

void CanSystem::Command::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    CanSystem& s = _system;
    switch (idx)
    {
        case ID_STATUS:
        {
            Channel* const only = scanOptionalBus(context);
            if (context.checkEol())
            {
                ::util::format::SharedStringWriter out(context);
                for (Channel* const bus : s.buses())
                {
                    if ((only == nullptr) || (only == bus))
                    {
                        bus->writeStatus(out);
                    }
                }
            }
            break;
        }
#ifndef NX_PRODUCT_BUILD // SYS-119
        case ID_SEND:
        {
            // [<bus>] <id> [<byte> ...]: the first token is the bus if it is not a number
            Channel* bus                            = s.buses()[0];
            ::util::string::ConstString const first = context.scanToken();
            uint32_t id                             = 0U;
            bool valueOk                            = parseUnsigned(first, id);
            if (!valueOk)
            {
                Channel* const named = s.findBus(first);
                if (context.check(named != nullptr) && context.hasToken())
                {
                    bus     = named;
                    valueOk = parseUnsigned(context.scanToken(), id);
                    (void)context.check(valueOk);
                }
            }
            uint8_t payload[::can::CANFrame::MAX_FRAME_LENGTH] = {0U};
            uint8_t length                                     = 0U;
            while (valueOk && context.hasToken() && (length < 8U))
            {
                uint32_t value  = 0U;
                valueOk         = parseUnsigned(context.scanToken(), value) && (value <= 0xFFU);
                payload[length] = static_cast<uint8_t>(value);
                ++length;
            }
            if (context.check(valueOk && (id <= ::can::CANFrame::MAX_FRAME_ID)) && context.checkEol())
            {
                ::can::CANFrame const frame(id, payload, length);
                // Gate check and write in the task of the CAN system (COM-24): no race with the
                // supervision's mute()/unmute(), counters written by one task only.
                ::can::ICanTransceiver::ErrorCode result
                    = ::can::ICanTransceiver::ErrorCode::CAN_ERR_ILLEGAL_STATE;
                auto const sendFrame = [bus, &frame, &result]() { result = bus->send(frame); };
                inCanTask(sendFrame);
                ::util::format::SharedStringWriter out(context);
                (void)out.printf(
                    "%s tx 0x%x [%u]: %s\r\n",
                    bus->getName(),
                    static_cast<unsigned>(id),
                    static_cast<unsigned>(length),
                    errorName(result));
            }
            break;
        }
#endif
        case ID_LOG:
        {
            uint32_t const enable = context.scanIntToken<uint32_t>();
            Channel* const only   = scanOptionalBus(context);
            if (context.check(enable <= 1U) && context.checkEol())
            {
                ::util::format::SharedStringWriter out(context);
                for (Channel* const bus : s.buses())
                {
                    if ((only == nullptr) || (only == bus))
                    {
                        bool on             = false;
                        auto const setLog = [bus, enable, &on]()
                        {
                            bus->setLogging(enable == 1U);
                            on = bus->isLogging();
                        };
                        inCanTask(setLog); // flag read by the receive path in task can (COM-24)
                        (void)out.printf("%s rx log %s\r\n", bus->getName(), on ? "on" : "off");
                    }
                }
            }
            break;
        }
        default:
        {
            break;
        }
    }
}

} // namespace systems

extern "C"
{
// Receive and transmit part of the message buffer interrupts, called by the handlers in os/isr/isr_can.cpp as in
// the OpenBSW reference application (CanSystem.cpp there), with the device as parameter: CanFlex2Transceiver
// dispatches over the FlexCAN instance, so one function serves every bus. The safety wrappers Safe_<handler> are in
// safeLifecycle/IsrWrapper.cpp; a bus without vectors keeps its interrupts disabled (CanSystem::run).
void call_can_isr_RX(uint8_t const device)
{
    ::asyncEnterIsrGroup(ISR_GROUP_CAN);

    uint8_t framesReceived;
    {
        ::async::LockType const lock;
        framesReceived = ::bios::CanFlex2Transceiver::receiveInterrupt(device);
    }

    if (framesReceived > 0U)
    {
        ::systems::CanSystem::instance().dispatchRxTask();
    }

    ::asyncLeaveIsrGroup(ISR_GROUP_CAN);
}

void call_can_isr_TX(uint8_t const device)
{
    ::asyncEnterIsrGroup(ISR_GROUP_CAN);
    ::bios::CanFlex2Transceiver::transmitInterrupt(device);
    ::asyncLeaveIsrGroup(ISR_GROUP_CAN);
}
}
