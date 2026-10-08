#pragma once

#include "xcp/XcpSlave.h"

#include <async/Async.h>
#include <can/filter/IntervalFilter.h>
#include <can/framemgmt/ICANFrameListener.h>
#include <lifecycle/AsyncLifecycleComponent.h>

namespace can
{
class ICanSystem;
class ICanTransceiver;
} // namespace can

namespace systems
{
/**
 * XCP on CAN (TOOL-01): measurement by polling and calibration of the RTE parameters.
 *
 * Bus and CAN identifiers come from xcp/xcpCanConfiguration.h (tools/rtegen from project.yaml xcp:, the same values go
 * into the A2L). The system listens on the command identifier of its bus and answers on the response
 * identifier; it runs in the context in which the CAN system delivers received frames (TASK_CAN), so frames
 * are processed directly in frameReceived() without a queue.
 *
 * Reachable memory (xcp::IMemoryMap): the RTE buffers of rte::SIGNALS (read only), the parameter storage of
 * rte::PARAMETERS (read and write), the text of xcp::EPK at ADDR_EPK (read only, TOOL-151: the EPK check of a
 * calibration tool) and, with a calibration segment in the development profile, its flash copy (read only, SYS-89),
 * nothing else.
 *
 * Consistency of a write: parameters are read by runnables without a lock. A write is therefore not done in
 * the XCP context but in the context of the RTE task with the lowest priority, between two of its cycles, and
 * under the interrupt lock:
 *  - runnables of that task never see a half written value, the write runs between their cycles;
 *  - runnables of higher priority tasks cannot preempt the copy (lock), and while they run the lowest task
 *    cannot run at all;
 *  - lower priority tasks do not run runnables (it is the lowest RTE task).
 * The answer to DOWNLOAD is sent once the write is done. If the task does not take it over within
 * NX_XCP_WRITE_TIMEOUT_MS the write is dropped (never applied later) and the master gets ERR_CMD_BUSY.
 *
 * SYS-90: a write to a safety-relevant calibration parameter (rte::PARAMETER_CHECKS) is checked first, with the new
 * bytes and the other values of the parameter (ecu/calibration PlausibilityMonitor.h). An implausible write is
 * refused before it is scheduled - it never takes effect - and the master gets ERR_OUT_OF_RANGE.
 */
class XcpSystem
: public ::lifecycle::AsyncLifecycleComponent
, private ::can::ICANFrameListener
, private ::xcp::IMemoryMap
, private ::xcp::IMemoryAccess
, private ::xcp::ITransmitter
{
public:
    XcpSystem(::async::ContextType context, ::can::ICanSystem& canSystem);
    XcpSystem(XcpSystem const&)            = delete;
    XcpSystem& operator=(XcpSystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;

    ::xcp::XcpSlave::Counters const& getCounters() const { return _slave.getCounters(); }

private:
    enum class WriteState : uint8_t
    {
        IDLE,
        REQUESTED, // waiting for the writer context
        APPLIED    // copied, answer not yet sent
    };

    /** Runs in the writer context (lowest RTE task): the copy under the lock. */
    class CommitRunnable : public ::async::RunnableType
    {
    public:
        explicit CommitRunnable(XcpSystem& parent) : _parent(parent) {}

        void execute() override;

    private:
        XcpSystem& _parent;
    };

    /** Runs in the XCP context: answer after the copy or after the timeout. */
    class DoneRunnable : public ::async::RunnableType
    {
    public:
        DoneRunnable(XcpSystem& parent, bool timeout) : _parent(parent), _timeout(timeout) {}

        void execute() override;

    private:
        XcpSystem& _parent;
        bool const _timeout;
    };

    /** Only the command identifier (an interval of one id; small compared with a BitFieldFilter). */
    class Filter final : public ::can::IntervalFilter
    {
    public:
        Filter();

        bool match(uint32_t id) const override;
    };

    // ICANFrameListener
    void frameReceived(::can::CANFrame const& frame) override;
    ::can::IFilter& getFilter() override { return _filter; }

    // xcp::IMemoryMap, IMemoryAccess, ITransmitter
    bool find(uint32_t address, ::xcp::Region& region) const override;
    void read(uint8_t* destination, uint8_t const* source, uint16_t n) override;
    void write(uint8_t* destination, uint8_t const* source, uint16_t n) override;
    void send(uint8_t const* data, uint8_t length) override;

    void commit();
    void finishWrite(bool timeout);

    ::async::ContextType const _context;
    ::async::ContextType _writerContext;
    ::can::ICanSystem& _canSystem;
    ::can::ICanTransceiver* _transceiver;
    Filter _filter;
    ::xcp::XcpSlave _slave;
    CommitRunnable _commitRunnable;
    DoneRunnable _doneRunnable;
    DoneRunnable _timeoutRunnable;
    ::async::TimeoutType _writeTimeout;
    uint8_t* _writeDestination;
    uint8_t const* _writeSource;
    uint16_t _writeLength;
    WriteState _writeState;
    uint32_t _txFailed;
};

} // namespace systems
