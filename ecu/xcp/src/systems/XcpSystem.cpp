#include "systems/XcpSystem.h"

#include <RteConfig.h>
#include <async/Types.h>
#include <busid/BusId.h>
#include <can/CanFrameLog.h>
#include <can/CanLogger.h>
#include <can/canframes/CANFrame.h>
#include <can/canframes/CanId.h>
#include <can/transceiver/ICanTransceiver.h>
#include <systems/ICanSystem.h>
#include <util/logger/Logger.h>
#include <xcp/xcpCanConfiguration.h> // bus and identifiers (tools/rtegen, project.yaml xcp:), same values as the A2L

#include <cstddef>

#if __has_include("can/canRxFilterConfiguration.h")
#include "can/canRxFilterConfiguration.h" // hardware receive filter of the buses (tools/rtegen, COM-08)
#define NX_XCP_RX_FILTER_KNOWN 1
#else
#define NX_XCP_RX_FILTER_KNOWN 0
#endif

// SYS-89: with a calibration segment (development profile) the flash copy of the parameters is the reference page;
// XCP may read it at its flash address, never write it
#ifdef NX_CALIBRATION_SEGMENT
#include <calibration/Calibration.h>
#endif

// SYS-90: a write to a safety-relevant calibration parameter is checked before it takes effect
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
#include <calibration/PlausibilityMonitor.h>
#endif

#ifndef NX_XCP_WRITE_TIMEOUT_MS
#define NX_XCP_WRITE_TIMEOUT_MS 100U
#endif

namespace rte
{
// Declared by RteConfig.h only if the project has parameters; referenced below only if PARAMETER_COUNT > 0.
extern Parameter const PARAMETERS[];
} // namespace rte

namespace xcp
{
// RTE-26: identity of this build (XcpEpk.cpp, tools/a2lgen/epk.py), EPK/ADDR_EPK of the A2L
extern char const EPK[];
extern uint32_t const EPK_LENGTH; // TOOL-151: bytes of the text, readable over XCP at ADDR_EPK
} // namespace xcp

namespace
{
using ::util::logger::CAN;
using ::util::logger::Logger;

// tools/a2lgen reads rte::SIGNALS and rte::PARAMETERS from the ELF with exactly this layout (32 bit targets).
static_assert(
    (sizeof(void*) != 4U)
        || ((sizeof(::rte::Signal) == 16U) && (offsetof(::rte::Signal, name) == 0U)
            && (offsetof(::rte::Signal, type) == 8U) && (offsetof(::rte::Signal, value) == 12U)),
    "rte::Signal layout differs from tools/a2lgen (SIGNAL_LAYOUT)");
static_assert(
    (sizeof(void*) != 4U)
        || ((offsetof(::rte::Parameter, unit) == 4U)
            && (offsetof(::rte::Parameter, value) == 8U) && (offsetof(::rte::Parameter, type) == 12U)
            && (offsetof(::rte::Parameter, count) == 14U) && (offsetof(::rte::Parameter, numerator) == 16U)
            && (offsetof(::rte::Parameter, denominator) == 24U)),
    "rte::Parameter layout differs from tools/a2lgen (PARAMETER_LAYOUT)");
// RTE-26: fields of the calibration definition appended behind the RTE-03 part
static_assert(
    (sizeof(void*) != 4U)
        || ((sizeof(::rte::Parameter) == 40U) && (offsetof(::rte::Parameter, limits) == 28U)
            && (offsetof(::rte::Parameter, axis) == 32U) && (offsetof(::rte::Parameter, kind) == 36U)
            && (offsetof(::rte::Parameter, safety) == 37U)),
    "rte::Parameter layout differs from tools/a2lgen (PARAMETER_LAYOUT, RTE-26)");

constexpr uint32_t CAN_ID_MASK = ::can::CanId::EXTENDED_QUALIFIER_BIT | ::can::CanId::MAX_RAW_EXTENDED_ID;
constexpr uint32_t MASTER_ID   = static_cast<uint32_t>(NX_XCP_CAN_ID_MASTER) & CAN_ID_MASK;
constexpr uint32_t SLAVE_ID    = static_cast<uint32_t>(NX_XCP_CAN_ID_SLAVE) & CAN_ID_MASK;
static_assert(MASTER_ID != SLAVE_ID, "XCP: command and response need different CAN identifiers");

// ARCH-43: the bus name is part of the start lines of init(), which are sized for the bus names of the CAN log lines
// (SYS-16: one logger entry of 128 bytes); tools/nxconfig reports a longer name in the project before the build.
static_assert(
    sizeof(NX_XCP_BUS_NAME) - 1U <= ::can::CanFrameLog::BUS_NAME_CHARS,
    "XCP bus name " NX_XCP_BUS_NAME " longer than CanFrameLog::BUS_NAME_CHARS (log lines)");

template<size_t Count>
struct ParameterTable
{
    static ::rte::Parameter const* get() { return ::rte::PARAMETERS; }
};

template<>
struct ParameterTable<0U>
{
    static ::rte::Parameter const* get() { return nullptr; }
};

uint8_t elementSize(::rte::SignalType const type)
{
    switch (type)
    {
        case ::rte::SignalType::BOOLEAN:
        case ::rte::SignalType::UINT8:
        case ::rte::SignalType::SINT8: return 1U;
        case ::rte::SignalType::UINT16:
        case ::rte::SignalType::SINT16: return 2U;
        case ::rte::SignalType::UINT32:
        case ::rte::SignalType::SINT32:
        case ::rte::SignalType::FLOAT32: return 4U;
        case ::rte::SignalType::FLOAT64: return 8U;
        default: return 0U;
    }
}

uint32_t addressOf(void const* const pointer)
{
    return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(pointer));
}

bool contains(::xcp::Region const& region, uint32_t const address)
{
    return (address >= region.address) && ((address - region.address) < region.size);
}

void copy(uint8_t* const destination, uint8_t const* const source, uint16_t const n)
{
    for (uint16_t i = 0U; i < n; ++i)
    {
        destination[i] = source[i];
    }
}

/** Application task with the lowest priority (context = FreeRTOS priority): the context of every write. */
::async::ContextType lowestRteTask()
{
    ::async::ContextType lowest = ::async::CONTEXT_INVALID;
    for (size_t i = 0U; i != ::rte::TASK_COUNT; ++i)
    {
        if ((lowest == ::async::CONTEXT_INVALID) || (::rte::TASKS[i].context < lowest))
        {
            lowest = ::rte::TASKS[i].context;
        }
    }
    return lowest;
}

#if NX_XCP_RX_FILTER_KNOWN
struct RxFilterEntry
{
    uint8_t bus;
    uint32_t id;
    uint32_t mask;
    bool extended;
};

/** True if the generated hardware receive filter lets the command identifier through on the XCP bus. */
constexpr bool commandIdInReceiveFilter()
{
#define NX_XCP_FILTER_ENTRY(bus, buffer, id, mask, extended) {::busid::bus, (id), (mask), (extended)},
    RxFilterEntry const entries[] = {NX_CAN_RX_FILTERS(NX_XCP_FILTER_ENTRY){0xFFU, 0U, 0U, false}};
#undef NX_XCP_FILTER_ENTRY
    bool const extended = (MASTER_ID & ::can::CanId::EXTENDED_QUALIFIER_BIT) != 0U;
    uint32_t const raw  = MASTER_ID & ::can::CanId::MAX_RAW_EXTENDED_ID;
    for (auto const& entry : entries)
    {
        if ((entry.bus == ::busid::NX_XCP_BUS) && (entry.extended == extended)
            && ((raw & entry.mask) == (entry.id & entry.mask)))
        {
            return true;
        }
    }
    return false;
}
#else
constexpr bool commandIdInReceiveFilter() { return true; } // no generated filter: the bus receives every id
#endif
// TOOL-26: tools/rtegen puts xcp.master_id into the filter of the XCP bus (can_rx_ids). Without it the board
// is deaf to XCP (seen 2026-09-18 with the TOOL-01 transition), so this is a build error, not a log line.
static_assert(
    commandIdInReceiveFilter(),
    "XCP: the command id is not in the generated hardware receive filter of the XCP bus (tools/rtegen can_rx_ids)");
} // namespace

namespace systems
{
XcpSystem::Filter::Filter() : ::can::IntervalFilter(MASTER_ID, MASTER_ID) {}

bool XcpSystem::Filter::match(uint32_t const id) const { return (id & CAN_ID_MASK) == MASTER_ID; }

XcpSystem::XcpSystem(::async::ContextType const context, ::can::ICanSystem& canSystem)
: _context(context)
, _writerContext(::async::CONTEXT_INVALID)
, _canSystem(canSystem)
, _transceiver(nullptr)
, _filter()
, _slave(
      *this,
      *this,
      *this,
#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)
      ::xcp::ByteOrder::BIG_ENDIAN_ORDER
#else
      ::xcp::ByteOrder::LITTLE_ENDIAN_ORDER
#endif
      )
, _commitRunnable(*this)
, _doneRunnable(*this, false)
, _timeoutRunnable(*this, true)
, _writeTimeout()
, _writeDestination(nullptr)
, _writeSource(nullptr)
, _writeLength(0U)
, _writeState(WriteState::IDLE)
, _txFailed(0U)
{
    setTransitionContext(context);
}

void XcpSystem::init()
{
    _writerContext = lowestRteTask();
    _transceiver   = _canSystem.getCanTransceiver(::busid::NX_XCP_BUS);
    if (_transceiver != nullptr)
    {
        _transceiver->addCANFrameListener(*this);
        Logger::info(
            CAN,
            "XCP on " NX_XCP_BUS_NAME " (command 0x%x, response 0x%x): %u signals, %u parameters",
            static_cast<unsigned>(MASTER_ID),
            static_cast<unsigned>(SLAVE_ID),
            static_cast<unsigned>(::rte::SIGNAL_COUNT),
            static_cast<unsigned>(::rte::PARAMETER_COUNT));
        Logger::info(CAN, "XCP EPK %s", ::xcp::EPK); // RTE-26: the build the A2L has to match
    }
    else
    {
        Logger::error(CAN, "XCP: no " NX_XCP_BUS_NAME " transceiver, XCP disabled");
    }
    transitionDone();
}

void XcpSystem::run() { transitionDone(); }

void XcpSystem::shutdown()
{
    if (_transceiver != nullptr)
    {
        _transceiver->removeCANFrameListener(*this);
    }
    {
        ::async::LockType const lock;
        _writeState = WriteState::IDLE; // a queued commit finds nothing to do
    }
    _writeTimeout.cancel();
    _slave.writeDone(false);
    _slave.reset();
    transitionDone();
}

void XcpSystem::frameReceived(::can::CANFrame const& frame)
{
    // CAN task (receive context of the CAN system) = _context: processed here, answered from here
    if ((frame.getId() & CAN_ID_MASK) == MASTER_ID)
    {
        _slave.receive(frame.getPayload(), frame.getPayloadLength());
    }
}

bool XcpSystem::find(uint32_t const address, ::xcp::Region& region) const
{
    for (size_t i = 0U; i < ::rte::SIGNAL_COUNT; ++i)
    {
        ::rte::Signal const& signal = ::rte::SIGNALS[i];
        uint8_t const size          = elementSize(signal.type);
        // the RTE buffers are plain variables; the engine never writes a region that is not writable
        region = {addressOf(signal.value), static_cast<uint8_t*>(const_cast<void*>(signal.value)), size, size, false};
        if ((size != 0U) && contains(region, address))
        {
            return true;
        }
    }
    ::rte::Parameter const* const parameters = ParameterTable<::rte::PARAMETER_COUNT>::get();
    for (size_t i = 0U; i != ::rte::PARAMETER_COUNT; ++i)
    {
        ::rte::Parameter const& parameter = parameters[i];
        uint8_t const size                = elementSize(parameter.type);
        region                            = {
            addressOf(parameter.value),
            static_cast<uint8_t*>(parameter.value),
            static_cast<uint32_t>(size) * parameter.count,
            size,
            true};
        if ((size != 0U) && contains(region, address))
        {
            return true;
        }
    }
    // TOOL-151: the EPK, read only, so a calibration tool can compare the ECU with EPK/ADDR_EPK of its A2L
    if (::xcp::findReadOnly(
            address,
            addressOf(::xcp::EPK),
            reinterpret_cast<uint8_t const*>(::xcp::EPK),
            ::xcp::EPK_LENGTH,
            region))
    {
        return true;
    }
#ifdef NX_CALIBRATION_SEGMENT
    // reference page: header and values of the flash segment, read only (one page is switched by nothing here: the
    // working page is the only one the ECU uses, a master compares or restores through UPLOAD and DOWNLOAD)
    uint32_t const segment = ::calibration::segmentAddress();
    region = {segment,
              reinterpret_cast<uint8_t*>(static_cast<uintptr_t>(segment)),
              ::calibration::segmentUsedBytes(),
              0U,
              false};
    if (!::calibration::isProduction() && contains(region, address))
    {
        return true;
    }
#endif
    return false;
}

void XcpSystem::read(uint8_t* const destination, uint8_t const* const source, uint16_t const n)
{
    // one snapshot; single RTE values up to 32 bit are written atomically anyway
    ::async::LockType const lock;
    copy(destination, source, n);
}

void XcpSystem::write(uint8_t* const destination, uint8_t const* const source, uint16_t const n)
{
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
    // SYS-90: the parameter as it would be after the write - refused here, before anything is scheduled, so an
    // implausible value never takes effect. No lock is needed: the stored values change only through the one write
    // the engine has pending at a time (this one), and the check reads the new bytes from source.
    if (!::calibration::plausibility::acceptWrite(destination, source, n))
    {
        _slave.writeRefused(::xcp::XcpSlave::ERR_OUT_OF_RANGE);
        return;
    }
#endif
    if (_writerContext == ::async::CONTEXT_INVALID)
    {
        // no application task reads anything: write here
        {
            ::async::LockType const lock;
            copy(destination, source, n);
        }
        _slave.writeDone(true);
        return;
    }
    {
        ::async::LockType const lock;
        _writeDestination = destination;
        _writeSource      = source;
        _writeLength      = n;
        _writeState       = WriteState::REQUESTED;
    }
    ::async::schedule(
        _context, _timeoutRunnable, _writeTimeout, NX_XCP_WRITE_TIMEOUT_MS, ::async::TimeUnit::MILLISECONDS);
    ::async::execute(_writerContext, _commitRunnable);
}

void XcpSystem::commit()
{
    bool applied = false;
    {
        ::async::LockType const lock;
        if (_writeState == WriteState::REQUESTED)
        {
            copy(_writeDestination, _writeSource, _writeLength);
            _writeState = WriteState::APPLIED;
            applied     = true;
        }
    }
    if (applied)
    {
        ::async::execute(_context, _doneRunnable);
    }
}

void XcpSystem::finishWrite(bool const timeout)
{
    bool report = false;
    bool ok     = false;
    {
        ::async::LockType const lock;
        if (_writeState == WriteState::APPLIED)
        {
            _writeState = WriteState::IDLE;
            report      = true;
            ok          = true;
        }
        else if (timeout && (_writeState == WriteState::REQUESTED))
        {
            _writeState = WriteState::IDLE; // withdrawn: the commit will find nothing to do
            report      = true;
        }
        else
        {
            // nothing pending (already answered)
        }
    }
    if (report)
    {
        _writeTimeout.cancel();
        _slave.writeDone(ok);
    }
}

void XcpSystem::send(uint8_t const* const data, uint8_t const length)
{
    // padded to 8 bytes (A2L: MAX_DLC_REQUIRED)
    uint8_t payload[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
    copy(payload, data, (length < 8U) ? length : 8U);
    ::can::CANFrame const frame(SLAVE_ID, payload, 8U);
    if ((_transceiver == nullptr)
        || (_transceiver->write(frame) != ::can::ICanTransceiver::ErrorCode::CAN_ERR_OK))
    {
        ++_txFailed;
    }
}

void XcpSystem::CommitRunnable::execute() { _parent.commit(); }

void XcpSystem::DoneRunnable::execute() { _parent.finishWrite(_timeout); }

} // namespace systems
