/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

#include "bsp/can/canDeviceConfiguration.h"
#include "bsp/can/canTransceiver/CanPhy.h"
#include "can/CanBusOffRecovery.h"
#include "can/CanBusState.h"
#include "can/CanPhyReadiness.h"
#include "can/canframes/CANFrame.h"
#include "can/filter/BitFieldFilter.h"
#include "can/framemgmt/ICANFrameListener.h"
#include "can/transceiver/ICANTransceiverStateListener.h"
#include "can/transceiver/canflex2/CanFlex2Transceiver.h"
#include "console/AsyncCommandWrapper.h"
#include "lifecycle/SingleContextLifecycleComponent.h"

#include <etl/singleton_base.h>
#include <etl/span.h>
#include <systems/ICanSystem.h>
#include <util/command/GroupCommand.h>
#include <util/format/StringWriter.h>

class StaticBsp;

namespace systems
{

/**
 * CAN of the ECU: one channel per bus of NX_CAN_BUSES (bsp/can/canDeviceConfiguration.h, generated from
 * the board and the project: controller, pins, bit timing, bus id and interrupt vectors, ARCH-07).
 *
 * Every channel gets its transceiver (role can_phy) from the integration and only knows ::bios::CanPhy: a
 * SBC (Uja113xCanPhy), a transceiver with STB pin or a passive part (PassiveCanPhy). The phy readiness is
 * compared with the state of the CAN transceiver object (CanPhyGate): not ready -> mute (write() is refused,
 * pending transmit buffers are deactivated, reception continues), ready again -> unmute. So no frame is put on
 * the bus while its transceiver cannot transmit.
 *
 * When the gate is evaluated (COM-23): right after a provider reports a change of the readiness
 * (::can::CanPhyReadiness, e.g. SbcSystem after reading the transceiver status of the SBC) - the report only
 * schedules the gate runnable in this task, which runs above the RTE's CAN task - and, as the fallback for a
 * provider that does not report, in every 10 ms supervision cycle (::can::CanSupervisionCycle). A board with
 * passive transceivers only has no reports and no cycle of its own for the gate. Until COM-23 the gate ran every
 * millisecond (COM-22).
 *
 * Bus state (COM-12): the 10 ms supervision reads the fault confinement state of every
 * controller and keeps it in a ::can::CanBusMonitor, so bus off is detected, counted, logged and
 * reported to the RTE (signal qualifier QLF_ERROR, COM-03). The two sources are separate and both
 * shown by "can status": the readiness of the transceiver (CanPhyGate -> muted) and the state of the
 * bus itself (error active / error passive / bus off).
 *
 * Console "can": status [bus], send [bus] <id> [bytes], log <0|1> [bus] (for board tests; no
 * diagnostics/transport here, those pick their bus by bus id).
 */
class CanSystem
: public ::can::ICanSystem
, public ::lifecycle::SingleContextLifecycleComponent
, public ::etl::singleton_base<CanSystem>
, private ::async::RunnableType
, private ::can::ICanPhyReadinessListener
{
public:
    /** Number of CAN buses of the project (generated). */
    static constexpr uint8_t BUS_COUNT = NX_CAN_DEVICE_COUNT;

    /** Transceivers of the buses in the order of NX_CAN_BUSES, wired by the integration. */
    using PhyTable = ::etl::span<::bios::CanPhy* const>;

    CanSystem(::async::ContextType context, StaticBsp& staticBsp, PhyTable phys);
    CanSystem(CanSystem const&)            = delete;
    CanSystem& operator=(CanSystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;

    ::can::ICanTransceiver* getCanTransceiver(uint8_t busId) override;

    /** Executes the receive runnable in the CAN context (called from the receive ISR of any bus). */
    void dispatchRxTask();

private:
    /**
     * The FlexCAN transceiver of one bus with a bus state that can be trusted (COM-12).
     *
     * OpenBSW's FlexCANDevice::getBusOffState() reads FLTCONF of ESR1 with the NXP **setter** macro
     * FLEXCAN_ESR1_FLTCONF(x) (shift left, then mask), so it evaluates bits 0..1 of ESR1 (WAKINT,
     * ERRINT) instead of the fault confinement field - the same mistake COM-09 corrected in the
     * console output. The first error frame latches ERRINT, getBusOffState() then answers BUS_OFF for
     * ever (its fBusOff is sticky), and CanFlex2Transceiver::cyclicTask() writes that into the
     * transceiver state. Board evidence 2026-09-16: ESR1 0x00240082, FLTCONF really 0 (error active),
     * driver state BUS_OFF, and therefore every received message QLF_ERROR - the signal qualifier of
     * COM-03 was unusable.
     *
     * The driver stays untouched (external/openbsw is never changed). This class overrides
     * getCANTransceiverState() and answers from the fault confinement field, read correctly, at the
     * moment of the call. Every user of ::can::ICanTransceiver therefore sees the real state; the
     * value the driver keeps is still available through getDriverState() so "can status" can show
     * both and the mistake cannot hide again.
     */
    class BusStateTransceiver final : public ::bios::CanFlex2Transceiver
    {
    public:
        BusStateTransceiver(
            ::async::ContextType context,
            uint8_t busId,
            ::bios::FlexCANDevice::Config const& config,
            ::bios::CanPhy& phy,
            ::bios::IEcuPowerStateController& powerStateController);

        /** Fault confinement field FLTCONF of ESR1 (0 error active, 1 error passive, 2/3 bus off). */
        uint8_t getFaultConfinement() const;

        /** Bus state from FLTCONF; UNKNOWN while the controller is not started. */
        ::can::CanBusState getBusState() const;

        /** Real state of the bus, read from the controller (overrides the driver's latched value). */
        ::can::ICANTransceiverStateListener::CANTransceiverState
        getCANTransceiverState() const override;

        /** The value the OpenBSW driver keeps (cyclicTask); shown next to ours, see the class note. */
        ::can::ICANTransceiverStateListener::CANTransceiverState getDriverState() const;

        /** True if the controller recovers from bus off on its own (FlexCAN CTRL1[BOFFREC] == 0). */
        bool isRecoveryAutomatic() const;

        /**
         * Latched bus off interrupt flag ESR1[BOFFINT]: true if the controller went bus off since the flag
         * was last cleared, even if it recovered in between. Read only here; since COM-35
         * Channel::updateBusState() clears the flag every supervision cycle and feeds it to the bus state
         * monitor, so this tells a bus off since the last cycle - "ever bus off" comes from the monitor.
         */
        bool sawBusOffSinceInit() const;

    private:
        ::bios::FlexCANDevice::Config const& _busConfig;
    };

    /**
     * One CAN bus: transceiver object, its phy, frame counters and the receive log. Accepts every frame
     * (counts receptions, logs them on request) and decides the transmit permission with CanPhyGate.
     *
     * It is also the transceiver's state listener: the notifications come from
     * CanFlex2Transceiver::cyclicTask(), which the driver schedules itself in this task when the bus is
     * opened. Counting them proves that the cyclic service runs and makes a disagreement with our own
     * reading visible in "can status" instead of silently deciding the signal qualifier (COM-12).
     */
    class Channel
    : public ::can::ICANFrameListener
    , public ::can::ICANTransceiverStateListener
    {
    public:
        Channel(
            char const* name,
            char const* device,
            uint8_t busId,
            ::async::ContextType context,
            ::bios::FlexCANDevice::Config const& config,
            ::can::CanBusOffRecovery::Config const& busOffConfig,
            ::bios::CanPhy& phy,
            ::bios::IEcuPowerStateController& powerStateController);

        char const* getName() const { return _name; }

        char const* getDevice() const { return _device; }

        uint8_t getBusId() const { return _busId; }

        ::bios::CanFlex2Transceiver& getTransceiver() { return _transceiver; }

        /** Readiness of the transceiver of this bus (role can_phy). */
        bool isPhyReady() const;

        /** True if frames may be sent: transceiver open (not muted) and its phy ready. */
        bool isTransmitAllowed() const;

        void start();
        void stop();

        void receiveTask() { _transceiver.receiveTask(); }

        /** Mutes or unmutes the transceiver after a change of the phy readiness. */
        void updateTransmitPermission();

        /** Feeds the bus state of the controller into the monitor and logs every transition. */
        void updateBusState(uint32_t elapsedMs);

        ::can::CanBusState getBusState() const { return _busMonitor.state(); }

        ::can::ICanTransceiver::ErrorCode send(::can::CANFrame const& frame);

        void frameReceived(::can::CANFrame const& frame) override;
        ::can::IFilter& getFilter() override { return _filter; }

        // ICANTransceiverStateListener: notifications of the driver's own cyclic service, counted only
        void canTransceiverStateChanged(
            ::can::ICanTransceiver& transceiver,
            ::can::ICANTransceiverStateListener::CANTransceiverState state) override;
        void phyErrorOccurred(::can::ICanTransceiver& transceiver) override;

        void setLogging(bool const enabled) { _logging = enabled; }

        bool isLogging() const { return _logging; }

        /** Five or six lines of "can status"; snapshot in the CAN task, printed by the caller (COM-24). */
        void writeStatus(::util::format::StringWriter& out) const;

    private:
        /** Bit field (merged into the transceiver's hardware filter) opened for all standard IDs,
         *  match() also accepts extended IDs. */
        class AcceptAllFilter : public ::can::BitFieldFilter
        {
        public:
            AcceptAllFilter() { open(); }

            bool match(uint32_t /* filterId */) const override { return true; }
        };

        char const* _name;
        char const* _device;
        uint8_t _busId;
        ::bios::FlexCANDevice::Config const& _config;
        ::bios::CanPhy& _phy;
        BusStateTransceiver _transceiver;
        AcceptAllFilter _filter;
        ::can::CanBusMonitor _busMonitor;
        ::can::CanBusOffRecovery _busOffRecovery; // COM-35, values of project can.<bus>.bus_off (COM-37)
        uint32_t _rxCount;
        uint32_t _txOk;
        uint32_t _txFailed;
        uint32_t _txRefused;
        uint32_t _muteCount;
        uint32_t _driverNotifications; // state changes the OpenBSW cyclic service reported
        uint32_t _driverBusOffReports; // of those: bus off (unreliable, see BusStateTransceiver)
        uint32_t _driverPhyErrors;
        bool _logging;
    };

    class CanRxRunnable : public ::async::RunnableType
    {
    public:
        explicit CanRxRunnable(CanSystem& parent);

        void execute() override;

        void setEnabled(bool enabled) { _enabled = enabled; }

    private:
        CanSystem& _parent;
        bool _enabled;
    };

    /** Transmit gate of every bus after a report of a phy provider (COM-23), in the CAN task. */
    class GateRunnable : public ::async::RunnableType
    {
    public:
        explicit GateRunnable(CanSystem& parent) : _parent(parent), _enabled(false) {}

        void execute() override;

        void setEnabled(bool const enabled) { _enabled = enabled; }

    private:
        CanSystem& _parent;
        bool _enabled;
    };

    class Command : public ::util::command::GroupCommand
    {
    public:
        explicit Command(CanSystem& system) : _system(system) {}

    protected:
        DECLARE_COMMAND_GROUP_GET_INFO
        void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

    private:
        /** Optional bus name of a command; nullptr if no token follows, on error the context is marked. */
        Channel* scanOptionalBus(::util::command::CommandContext& context);

        CanSystem& _system;
    };

    /** 10 ms supervision: bus state of every bus, transmit gate as the fallback of the reports (COM-23). */
    void execute() override;

    /**
     * ::can::CanPhyReadiness: a provider reports a change of its readiness. Runs in the provider's task; schedules
     * the gate runnable in the CAN task (several reports before it runs collapse into one evaluation).
     */
    void phyReadinessChanged(::bios::CanPhy& phy) override;

    /** Mutes or unmutes every bus after its phy readiness. */
    void updateTransmitPermissions();

    /** The buses of the project in the order of their bus ids. */
    ::etl::span<Channel* const> buses() { return ::etl::span<Channel* const>(_buses, BUS_COUNT); }

    /** Bus with this name (console), nullptr if unknown. */
    Channel* findBus(::util::string::ConstString const& name);

    ::async::ContextType _context;
// one member per bus, e.g. _busCAN0 (the transceiver object cannot be copied or moved)
#define NX_CAN_CHANNEL_MEMBER(bus, index, busId, device, deviceName) \
    Channel _bus##bus;
    NX_CAN_BUSES(NX_CAN_CHANNEL_MEMBER)
#undef NX_CAN_CHANNEL_MEMBER
    Channel* _buses[BUS_COUNT > 0U ? BUS_COUNT : 1U];
    CanRxRunnable _canRxRunnable;
    GateRunnable _gateRunnable;
    ::async::TimeoutType _timeout;
#if !defined(NX_CONSOLE) || NX_CONSOLE
    // PLAT-106: console "can" only in an image with console (project.yaml console.enabled, NX_CONSOLE)
    Command _command;
    // registered with the console context (COM-24, SYS-13: ::console::CONSOLE_CONTEXT): the command runs there,
    // only the access to a bus runs in this system's task (CanSystem.cpp, inCanTask)
    ::console::AsyncCommandWrapper _asyncCommand;
#endif
};

} // namespace systems
