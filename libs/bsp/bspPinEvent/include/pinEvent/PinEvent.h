// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>

namespace pinevent
{
/**
 * Events on digital inputs (IO-53, concept doc/architecture/pin-interrupts.md, variant A): per input a counter of
 * accepted edges, the time stamp of the last one and a qualifier - with software debounce and a rate limit against
 * interrupt storms. Family neutral: no register, no pin, no clock, no heap, no RTOS.
 *
 * Two sides per input:
 *   - ISR side, onEdge(): called by the pin interrupt dispatcher of the family (IO-52) for every edge of an armed
 *     input, with the time of the edge and the logical level sampled right after the interrupt flag was cleared.
 *     Constant time, no loop, no call out; it writes only the input's own state and returns false when the input
 *     must be disarmed (rate limit tripped).
 *   - Task side, PinEvents::cycle() and read(): re-arms a tripped input after rearmUs (task context only), and
 *     copies a consistent snapshot for the RTE and the console.
 * The hand-over is lock free: the ISR side is the only writer while the input is armed and publishes through a
 * sequence counter (odd while writing); readers copy between two reads of the counter and retry a bounded number
 * of times. The task side writes the state only while the input is disarmed (start, re-arm after a trip), so there
 * is never more than one writer. Single core: the ISR never interrupts itself for the same input and a task never
 * interrupts an ISR; compiler barriers are enough, no exclusive access instructions (ARMv6-M has none).
 *
 * Debounce (debounceUs > 0): an edge outside the lock-out opens it (a provisional edge towards the other level);
 * further edges within debounceUs are bounces. After the lock-out the level is confirmed: the last level reported
 * inside the lock-out is the level at its end, because every change raises an interrupt. That needs both edges in
 * the edge detector (detectorEdges()). Confirmed towards the configured edge: counted, time stamp = time of the
 * opening edge. Back on the old level: a glitch, not counted. The confirmation is committed by the next onEdge()
 * and already visible in read() as soon as the lock-out has passed.
 *
 * Rate limit: every candidate edge towards the configured edge (the edge that opens a lock-out; every edge without
 * debounce) counts in a window that opens with the first such edge and lasts windowUs. The candidate that makes
 * it maxEvents + 1 disarms the input: QUALITY_ERROR, trip counter + 1, re-armed by cycle() rearmUs later with a
 * fresh window. Candidates count even when they turn out to be glitches, so a storm of short spikes trips too.
 * Edges towards the other level are bounded by the lock-out itself (at most one per debounceUs), and more than
 * MAX_BOUNCES bounces inside one lock-out trip the limit as well. So the interrupt load of one input is bounded
 * by maxEvents per window (plus MAX_BOUNCES + 1 per lock-out with debounce) whatever the line does.
 *
 * Time: a free-running 32-bit microsecond time that wraps (the same base for the dispatcher and the task side).
 * All intervals are unsigned differences. History older than EXPIRE_AFTER_US is expired by the task side, so an
 * input that was quiet for longer than one wrap (71.6 min) cannot alias into an old lock-out or window; cycle()
 * must run at least every (2^32 - EXPIRE_AFTER_US) us, about 53 min.
 */

/** Signal qualifier values of the RTE (QLF_* of Rte_Qlf.h, tools/rtegen QLF_VALUES). */
constexpr uint8_t QUALITY_DEBOUNCE = 0U; // no accepted edge yet (or the input was never started): time not valid
constexpr uint8_t QUALITY_OK       = 1U;
constexpr uint8_t QUALITY_ERROR    = 16U; // rate limit tripped: the input is disarmed until rearmUs has passed

/** Logical edge relative to the active level of the board input (project pin_events.<input>.edge). */
enum class Edge : uint8_t
{
    ACTIVATE   = 1U, // the input becomes active
    DEACTIVATE = 2U, // the input becomes inactive
    BOTH       = 3U
};

/** What the edge detector of the family has to report, logical like Edge; NONE: disarmed. */
enum class Detect : uint8_t
{
    NONE       = 0U,
    ACTIVATE   = 1U,
    DEACTIVATE = 2U,
    BOTH       = 3U
};

/** Largest values the project allows (tools/nxconfig/model.py PIN_EVENT_MAX_*), in the units of this module. */
constexpr uint32_t MAX_DEBOUNCE_US = 1000000U;
constexpr uint32_t MAX_EVENTS      = 65535U;
constexpr uint32_t MAX_WINDOW_US   = 60000000U;
constexpr uint32_t MAX_REARM_US    = 600000000U;
/** Bounces inside one lock-out that count as a storm (a contact bounces a few to a few dozen times). */
constexpr uint32_t MAX_BOUNCES     = 32U;
/** History of an input that was quiet this long is expired; larger than every window, lock-out and hold-off. */
constexpr uint32_t EXPIRE_AFTER_US = 1UL << 30U;
/** Attempts of a reader to get a copy no interrupt wrote into. */
constexpr uint8_t READ_ATTEMPTS    = 4U;

/** One input, generated per project from pin_events (IO-54). */
struct PinEventConfig
{
    char const* name;    // board input name, for the console
    Edge edge;           // edge that counts
    uint32_t debounceUs; // lock-out after an edge, then level confirmation; 0: off
    uint32_t maxEvents;  // at most this many candidate edges per window
    uint32_t windowUs;   // window of the rate limit (window_ms * 1000)
    uint32_t rearmUs;    // hold-off after a trip (rearm_ms * 1000)
};

/** The edges the edge detector must report: both while debouncing (level confirmation), else the configured. */
constexpr Detect detectorEdges(PinEventConfig const& c)
{
    return (c.debounceUs != 0U) ? Detect::BOTH : static_cast<Detect>(static_cast<uint8_t>(c.edge));
}

/** The same rules as tools/nxconfig (pinevent.py): ranges, and the debounce shorter than the limit's spacing. */
constexpr bool isValidConfig(PinEventConfig const& c)
{
    return (c.name != nullptr) && (static_cast<uint8_t>(c.edge) >= 1U) && (static_cast<uint8_t>(c.edge) <= 3U)
           && (c.debounceUs <= MAX_DEBOUNCE_US) && (c.maxEvents >= 1U) && (c.maxEvents <= MAX_EVENTS)
           && (c.windowUs >= 1U) && (c.windowUs <= MAX_WINDOW_US) && (c.rearmUs >= 1U)
           && (c.rearmUs <= MAX_REARM_US)
           && ((static_cast<uint64_t>(c.debounceUs) * c.maxEvents) < static_cast<uint64_t>(c.windowUs));
}

/** What the RTE offers per input: pinevent.<NAME>.count / .time_us / .qlf. */
struct Reading
{
    uint32_t count;  // accepted edges, wraps from 0xFFFFFFFF to 0
    uint32_t timeUs; // time of the last accepted edge; only valid with a count, see quality
    uint8_t quality; // QUALITY_*
};

/** Reading plus the counters of the robustness measures, for the console and a diagnosis. */
struct Statistics
{
    Reading reading;
    uint32_t trips;       // times the rate limit disarmed the input
    uint32_t bounces;     // edges inside a lock-out
    uint32_t glitches;    // provisional edges taken back: the level was back on the old one after the lock-out
    uint32_t reArmInUs;   // time until the automatic re-arm while tripped, else 0
    bool armed;           // started and not tripped
};

/** Pin access of the family, task side only: sample the level, program the edge detector. */
class IPinEventHardware
{
public:
    /** Logical level of the input now (true = active; the inversion of the board applied). */
    virtual bool level(uint8_t input) const = 0;

    /** Programs the edge detector of the input; Detect::NONE disarms it. Clears a pending flag of the pin. */
    virtual void arm(uint8_t input, Detect edges) = 0;

protected:
    ~IPinEventHardware() = default;
};

/** Free-running 32-bit microsecond time of the task side; the same base as the dispatcher's time stamps. */
using TimeSource = uint32_t (*)();

/**
 * One input: the state both sides share. Instances live in a static table of the integration (IO-54) and are
 * used through PinEvents; the class is public for the host test and for a generated table.
 */
class PinEvent
{
public:
    explicit constexpr PinEvent(PinEventConfig const& config) : _config(config), _state(), _seq(0U), _idleMark(1U)
    {}

    PinEventConfig const& config() const { return _config; }

    /** ISR side: an edge at nowUs, `level` sampled after the flag was cleared. false: disarm the input. */
    bool onEdge(uint32_t nowUs, bool level);

    /** Task side, input disarmed: starts counting from the level it stands on. */
    void start(bool level, uint32_t nowUs);

    /**
     * Task side, every cycle: expires a quiet history, and says whether a tripped input's hold-off has passed
     * (true: the caller calls rearm() with the level sampled then and arms the pin afterwards).
     */
    bool service(TimeSource now);

    /** Task side, input disarmed (tripped): fresh window and lock-out from `level`, armed again. */
    void rearm(bool level, uint32_t nowUs);

    /** Any context below the ISR: consistent copy at the time `now()` returns inside the copy; false: busy. */
    bool read(TimeSource now, Reading& out) const;

    bool statistics(TimeSource now, Statistics& out) const;

private:
    // ISR-owned while armed, task-owned while disarmed; published through _seq
    struct State
    {
        uint32_t count;
        uint32_t timeUs;       // time of the last accepted edge
        uint32_t lastUs;       // time of the last onEdge() (activity), for the expiry
        uint32_t edgeUs;       // time of the provisional edge (opening the lock-out)
        uint32_t windowUs;     // start of the rate window
        uint32_t windowCount;  // candidates in the window; 0: no window open
        uint32_t lockBounces;  // bounces in the current lock-out
        uint32_t trips;
        uint32_t bounces;
        uint32_t glitches;
        uint32_t tripUs;       // time of the trip
        bool started;
        bool tripped;
        bool accepted;         // an edge was accepted since the start (quality OK)
        bool level;            // stable (confirmed) logical level
        bool pending;          // a provisional edge waits for the end of its lock-out
        bool lastSeen;         // last level reported inside the lock-out
        bool counts;           // the provisional edge is one of the configured edge
    };

    struct Snapshot
    {
        State state;
        uint32_t seq;
        uint32_t nowUs;
    };

    bool snapshot(TimeSource now, Snapshot& out) const;
    void beginWrite();
    void endWrite();
    bool countsTowards(bool newLevel) const;
    static void settle(State& s, bool levelAtEnd);
    /** The provisional edge as read() sees it: settled when its lock-out has passed or its history expired. */
    void settledView(Snapshot& snap) const;
    static Reading readingOf(State const& s);

    PinEventConfig const& _config;
    State _state;
    uint32_t _seq;      // even: stable, odd: a writer is inside (accessed as volatile)
    uint32_t _idleMark; // task-owned: the _seq value whose history is expired; odd: none (volatile)

    friend struct PinEventTestAccess;
};

/** The table of one project and the entry points of both sides. */
class PinEvents
{
public:
    // constexpr: a static table of the integration is constant initialized (no static constructor, IO-54)
    constexpr PinEvents(PinEvent* inputs, uint8_t count, IPinEventHardware& hardware, TimeSource now)
    : _inputs(inputs), _count(count), _hardware(hardware), _now(now)
    {}

    uint8_t count() const { return _count; }

    PinEventConfig const& config(uint8_t input) const { return _inputs[input].config(); }

    /** Task side, once, before the dispatcher's vectors are enabled: samples each level, starts and arms. */
    void start();

    /** Task side, every cycle of the owning task: re-arms tripped inputs after their hold-off; expiry. */
    void cycle();

    /**
     * ISR side, the consumer entry of the dispatcher: an edge of `input` at nowUs with the logical level sampled
     * after the flag was cleared. false: the dispatcher disarms the pin (edge detector off) - also for an
     * unknown input.
     */
    bool onEdge(uint8_t input, uint32_t nowUs, bool level)
    {
        return (input < _count) && _inputs[input].onEdge(nowUs, level);
    }

    /** RTE/console: consistent copy; false (out unchanged) if an interrupt wrote each time or input unknown. */
    bool read(uint8_t input, Reading& out) const
    {
        return (input < _count) && _inputs[input].read(_now, out);
    }

    bool statistics(uint8_t input, Statistics& out) const
    {
        return (input < _count) && _inputs[input].statistics(_now, out);
    }

private:
    PinEvent* _inputs;
    uint8_t _count;
    IPinEventHardware& _hardware;
    TimeSource _now;
};

} // namespace pinevent
