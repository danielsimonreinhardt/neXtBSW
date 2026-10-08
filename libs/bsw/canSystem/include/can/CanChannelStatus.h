#pragma once

#include <cstdint>

namespace can
{

/**
 * Everything "can status" shows for one bus, taken at one point in time (COM-24).
 *
 * The board port fills it in the CAN task (short: counters, a few controller registers, the names of
 * the states), the console prints it afterwards in its own, lower context (slow: the UART takes
 * 86.8 us per character). So the CAN task no longer waits for the UART, and the lines of one bus
 * belong together - before, the counters of the last line were read up to 40 ms after the first.
 * Names are pointers to string literals, chosen in the CAN task.
 */
struct CanChannelStatus
{
    // transceiver and phy (CanPhyGate)
    char const* name;
    char const* state;
    bool phyReady;
    bool transmitAllowed;
    // bus state of the controller (CanBusMonitor, COM-12)
    char const* busState;
    uint32_t busOffCount;
    uint32_t busOffNowMs; // 0 unless the bus is off now
    uint32_t longestBusOffMs;
    uint32_t recoveries;
    uint32_t passiveCount;
    bool everBusOff;
    bool automaticRecovery;
    // what the OpenBSW driver's cyclic service believes
    char const* driverState;
    bool driverDisagrees;
    uint32_t driverNotifications;
    uint32_t driverBusOffReports;
    uint32_t driverPhyErrors;
    // controller registers
    uint32_t bitrate;
    uint32_t ctrl1;
    uint32_t esr1;
    uint32_t fltconf;
    char const* fltconfName;
    uint32_t txwrn;
    uint32_t rxwrn;
    uint32_t synch;
    uint32_t tec;
    uint32_t rec;
    // frames
    uint32_t rx;
    uint32_t tx;
    uint32_t consoleOk;
    uint32_t consoleFailed;
    uint32_t consoleRefused;
    uint32_t txBufferBusy;
    uint32_t muteCount;
};

/**
 * Prints \p s as the lines of "can status" (same text as before COM-24). \p out needs
 * printf(char const*, ...) - ::util::format::StringWriter on the target.
 */
template<class Writer>
void writeChannelStatus(Writer& out, CanChannelStatus const& s)
{
    (void)out.printf(
        "%s %s, phy %s, tx %s\r\n",
        s.name,
        s.state,
        s.phyReady ? "ready" : "not ready",
        s.transmitAllowed ? "allowed" : "blocked");
    // Two sources, both named, so neither can hide behind the other (COM-12):
    //   "phy" above  = readiness of the transceiver part (role can_phy, CanPhyGate) -> muted
    //   "bus"  here  = fault confinement state of the controller -> bus off
    //   "driver"     = the state the OpenBSW cyclic service keeps; it misreads FLTCONF (see
    //                  CanSystem::BusStateTransceiver in the board port) and is shown only to keep that visible.
    (void)out.printf(
        "bus %s, bus off %u times (now %u ms, longest %u ms, %u recoveries), error passive %u times, "
        "ever bus off %s, recovery %s\r\n",
        s.busState,
        static_cast<unsigned>(s.busOffCount),
        static_cast<unsigned>(s.busOffNowMs),
        static_cast<unsigned>(s.longestBusOffMs),
        static_cast<unsigned>(s.recoveries),
        static_cast<unsigned>(s.passiveCount),
        s.everBusOff ? "yes" : "no",
        s.automaticRecovery ? "automatic" : "manual");
    (void)out.printf(
        "driver state %s, notifications %u (bus off %u), phy errors %u%s\r\n",
        s.driverState,
        static_cast<unsigned>(s.driverNotifications),
        static_cast<unsigned>(s.driverBusOffReports),
        static_cast<unsigned>(s.driverPhyErrors),
        s.driverDisagrees
            ? "  (driver disagrees, it reads FLTCONF with a setter macro: not used, COM-12)"
            : "");
    (void)out.printf(
        "bitrate %u, CTRL1 0x%08x, ESR1 0x%08x (FLTCONF %u %s, TXWRN %u, RXWRN %u, SYNCH %u), TEC %u, "
        "REC %u\r\n",
        static_cast<unsigned>(s.bitrate),
        static_cast<unsigned>(s.ctrl1),
        static_cast<unsigned>(s.esr1),
        static_cast<unsigned>(s.fltconf),
        s.fltconfName,
        static_cast<unsigned>(s.txwrn),
        static_cast<unsigned>(s.rxwrn),
        static_cast<unsigned>(s.synch),
        static_cast<unsigned>(s.tec),
        static_cast<unsigned>(s.rec));
    (void)out.printf(
        "rx %u, tx %u (console %u ok, %u failed, %u refused), tx buffer busy %u, muted %u times\r\n",
        static_cast<unsigned>(s.rx),
        static_cast<unsigned>(s.tx),
        static_cast<unsigned>(s.consoleOk),
        static_cast<unsigned>(s.consoleFailed),
        static_cast<unsigned>(s.consoleRefused),
        static_cast<unsigned>(s.txBufferBusy),
        static_cast<unsigned>(s.muteCount));
    if (s.txBufferBusy != 0U)
    {
        // OpenBSW calls this counter "overrun", but it is not a lost reception: getTransmitBuffer() found no
        // free transmit message buffer, i.e. earlier frames are still waiting for an acknowledge. Seen at the
        // board as soon as no second node answers on the bus (COM-09).
        (void)out.printf(
            "  tx buffer busy: no free transmit buffer, earlier frames still wait for an acknowledge "
            "(no other node on the bus?)\r\n");
    }
}

} // namespace can
