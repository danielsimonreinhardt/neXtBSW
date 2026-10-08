// COM-46: CAN bus-off of one bus as a source of the fault memory (TOOL-131 follow-up, numbering plan TOOL-178).
//
// Every used CAN bus can have a DTC whose event is the BSW name "can.busoff.<BUS>" (<BUS>: board name of the bus,
// diag.yaml dtcs[].event.bsw); its number is base + 0x0300 + index of the bus among the used CAN buses
// (tools/nxconfig/bswdtc.py, bsw_instance of the DTC). The CAN system of the board port feeds the bus state it reads
// every supervision cycle (CanBusMonitor, COM-12/COM-35) into one CanBusOffReport per bus:
//   bus off now                                        FAILED
//   error active or error passive (the bus works)      PASSED  (a bus-off since the last cycle: FAILED first)
//   state unknown (bus not open, controller stopped)   no result
// A series of bus-offs with the recovery pause of COM-35 stays FAILED while each one lasts; the DTC matures by its
// debouncing in diag.yaml (standard description: at the first bus-off, like the other reported BSW events).
// Only recording: the recovery stays with CanBusOffRecovery, the transmit gate with CanPhyGate.
//
// Header only, family neutral (the port reads its controller); host test test/host/CanBusOffReportHostTest.cpp.

#pragma once

#include "can/CanBusState.h"

#include <dem/EventReporter.h>

namespace can
{

class CanBusOffReport
{
public:
    /** \p event: "can.busoff.<BUS>", must outlive the report (the generated string literal of the bus). */
    explicit constexpr CanBusOffReport(char const* const event) : _reporter(event) {}

    /** Level of the bus in \p state (a bus-off latched since the last reading is passed to update() separately). */
    static constexpr ::dem::Result resultOf(CanBusState const state)
    {
        return (state == CanBusState::UNKNOWN)   ? ::dem::Result::NONE
               : (state == CanBusState::BUS_OFF) ? ::dem::Result::FAILED
                                                 : ::dem::Result::PASSED;
    }

    /** One supervision cycle of the bus (task of the CAN system); \p memory: the fault memory (nullptr: none yet). */
    void update(CanBusState const state, bool const enteredBusOff, ::dem::IFaultMemory* const memory)
    {
        // a bus-off that was over before this reading (latched flag) is a failure of this cycle as well
        bool const missed = enteredBusOff && (state != CanBusState::BUS_OFF) && (state != CanBusState::UNKNOWN);
        _reporter.update(resultOf(state), missed, memory);
    }

    /** The same with the registered fault memory of the ECU (dem::registry.memory). */
    void update(CanBusState const state, bool const enteredBusOff)
    {
        update(state, enteredBusOff, ::dem::registry.memory);
    }

    ::dem::EventReporter const& reporter() const { return _reporter; }

private:
    ::dem::EventReporter _reporter;
};

} // namespace can
