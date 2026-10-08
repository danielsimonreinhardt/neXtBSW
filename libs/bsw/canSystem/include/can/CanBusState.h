#pragma once

#include <cstdint>

namespace can
{

/**
 * Fault confinement state of one CAN bus (ISO 11898-1). The controller counts transmit and receive
 * errors and takes itself off the bus when it is alone with them:
 *
 *   UNKNOWN        controller not started (closed): nothing is known about the bus
 *   ERROR_ACTIVE   normal operation
 *   ERROR_PASSIVE  more than 127 errors counted; the node still transmits and receives
 *   BUS_OFF        more than 255 transmit errors; the node is off the bus
 *
 * For the signal qualifier (doc/architecture/signal-qualifier.md) only BUS_OFF is a bus error
 * (QLF_ERROR), next to a transceiver that may not transmit (CanPhyGate: muted). ERROR_PASSIVE is
 * deliberately **not** an error: the bus still works, and a node that is alone on the bus reaches
 * that state regularly (eval board without a second node: TEC 128, COM-02 board test). Reporting it
 * as a bus error would hide timeout, checksum and counter states again (COM-12).
 */
enum class CanBusState : uint8_t
{
    UNKNOWN = 0U,
    ERROR_ACTIVE,
    ERROR_PASSIVE,
    BUS_OFF,
};

/**
 * Bus state of one CAN bus with the transitions and counters a board test needs.
 *
 * Hardware independent: the caller reads the state of its controller and passes it in; this class
 * only interprets, remembers and counts. One instance per bus, so several buses keep independent
 * counters. It does not touch any register and does not trigger a recovery - whether recovering from
 * bus off is automatic is a property of the controller (FlexCAN: CTRL1[BOFFREC] == 0, the default of
 * the OpenBSW driver) and is reported by the caller.
 *
 * Time comes from the caller as the elapsed time of its supervision cycle, so the class needs no
 * clock and is testable on the host.
 */
class CanBusMonitor
{
public:
    /** Edge of the last update(); the caller logs it and the console counts it. */
    enum class Event : uint8_t
    {
        NONE,
        STARTED,       // first state after the controller was opened
        WENT_BUS_OFF,  // the node left the bus
        RECOVERED,     // the node is back on the bus
        WENT_PASSIVE,  // error passive: many errors, bus still usable (no bus error)
        WENT_ACTIVE,   // back to normal from error passive
        STOPPED,       // controller closed
    };

    CanBusMonitor() = default;

    /** FLTCONF of a FlexCAN ESR1 and of every controller with the CAN encoding: 00, 01, 1x. */
    static CanBusState fromFaultConfinement(uint8_t const faultConfinement)
    {
        if (faultConfinement == 0U)
        {
            return CanBusState::ERROR_ACTIVE;
        }
        if (faultConfinement == 1U)
        {
            return CanBusState::ERROR_PASSIVE;
        }
        return CanBusState::BUS_OFF; // 2 and 3 both mean bus off
    }

    /** Controllers that report the two states as separate flags (e.g. bxCAN ESR: EPVF, BOFF). */
    static CanBusState fromFlags(bool const errorPassive, bool const busOff)
    {
        if (busOff)
        {
            return CanBusState::BUS_OFF;
        }
        return errorPassive ? CanBusState::ERROR_PASSIVE : CanBusState::ERROR_ACTIVE;
    }

    /** Plain text for the console; short and stable, board tests match on it. */
    static char const* name(CanBusState const state)
    {
        switch (state)
        {
            case CanBusState::ERROR_ACTIVE: return "error active";
            case CanBusState::ERROR_PASSIVE: return "error passive";
            case CanBusState::BUS_OFF: return "bus off";
            default: return "unknown";
        }
    }

    /** True if this state makes every received message of the bus invalid (QLF_ERROR). */
    static bool isBusError(CanBusState const state) { return state == CanBusState::BUS_OFF; }

    /**
     * One supervision cycle.
     * \param state       state read from the controller (never UNKNOWN; use stop() for that)
     * \param elapsedMs   time since the last update() of this monitor
     */
    Event update(CanBusState const state, uint32_t const elapsedMs)
    {
        CanBusState const previous = _state;
        if (_state == CanBusState::BUS_OFF)
        {
            _busOffMs      = addSaturating(_busOffMs, elapsedMs);
            _totalBusOffMs = addSaturating(_totalBusOffMs, elapsedMs);
            if (_busOffMs > _longestBusOffMs)
            {
                _longestBusOffMs = _busOffMs;
            }
        }
        _state = state;
        if (state == previous)
        {
            return Event::NONE;
        }
        if (state == CanBusState::BUS_OFF)
        {
            _busOffMs = 0U;
            ++_busOffCount;
            return Event::WENT_BUS_OFF;
        }
        if (previous == CanBusState::BUS_OFF)
        {
            ++_recoveries;
            return Event::RECOVERED;
        }
        if (state == CanBusState::ERROR_PASSIVE)
        {
            ++_passiveCount;
            return Event::WENT_PASSIVE;
        }
        // ERROR_ACTIVE from UNKNOWN (the bus was just opened) or from ERROR_PASSIVE
        return (previous == CanBusState::UNKNOWN) ? Event::STARTED : Event::WENT_ACTIVE;
    }

    /** The controller was closed: the state is unknown again, the counters stay. */
    Event stop()
    {
        if (_state == CanBusState::UNKNOWN)
        {
            return Event::NONE;
        }
        _state = CanBusState::UNKNOWN;
        return Event::STOPPED;
    }

    CanBusState state() const { return _state; }

    bool isBusOff() const { return _state == CanBusState::BUS_OFF; }

    /** Transitions into bus off since the start (a bus off that is still going counts). */
    uint32_t busOffCount() const { return _busOffCount; }

    /** Returns from bus off; one less than busOffCount() while the node is off the bus. */
    uint32_t recoveries() const { return _recoveries; }

    /** Transitions into error passive (not a bus error, but worth seeing in a board test). */
    uint32_t passiveCount() const { return _passiveCount; }

    /** Duration of the running bus off phase, or of the last one (saturating). */
    uint32_t busOffMs() const { return _busOffMs; }

    uint32_t longestBusOffMs() const { return _longestBusOffMs; }

    uint32_t totalBusOffMs() const { return _totalBusOffMs; }

private:
    static uint32_t addSaturating(uint32_t const value, uint32_t const add)
    {
        return (value > (UINT32_MAX - add)) ? UINT32_MAX : (value + add);
    }

    CanBusState _state        = CanBusState::UNKNOWN;
    uint32_t _busOffCount     = 0U;
    uint32_t _recoveries      = 0U;
    uint32_t _passiveCount    = 0U;
    uint32_t _busOffMs        = 0U;
    uint32_t _longestBusOffMs = 0U;
    uint32_t _totalBusOffMs   = 0U;
};

} // namespace can
