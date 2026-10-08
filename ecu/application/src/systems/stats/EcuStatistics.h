// NVM-05: operating time and reset statistics of the ECU - the pure core (no NvM, no RTE, no lock), host testable.
//
// What is counted (board neutral, both sources of the start reasons of SYS-18/RTE-16):
// - the operating time: time the application runs (MCU supplied and out of reset), in seconds;
// - the starts, and per start its reasons from two sources, each as ecustate::wakeup bits:
//     MCU     the reset status of the MCU itself (ecustate::mcuStartReasons(), family hook; s32k1xx RCM_SRS),
//     supply  the role power_supply (IPowerSupply::getStartupWakeupSources(); a system basis chip, a plain
//             regulator reports UNKNOWN or POWER). A board without SBC still counts both rows. NVM-34: the supply
//             row counts only starts of the supply itself; an MCU-only start (IPowerSupply::restartedForThisStart()
//             false, e.g. MCU software reset with RSTN of the SBC not connected) counts in the MCU row only.
//   Every bit of a source counts its cause once (POWER, RESET, WATCHDOG, SUPPLY, TIMER, any CAN bus, any local
//   input); a source that cannot tell (no bit, or wakeup::UNKNOWN) counts UNKNOWN. Counters saturate.
//
// Wear of the memory: the core decides when the record is handed to the RAM image of its NvM block - once per start
// (when the supply share is known, or START_SETTLE_MS after the start without it), and the operating time at most
// every IMAGE_PERIOD_MS. How often the NvM really writes the block is the project value of the block (write policy
// and min_interval_ms of project.yaml nvm.blocks.EcuStatistics): the NvM writes a CHANGE block at most once per
// min_interval_ms and every changed block at the run-down. The core never writes itself.

#pragma once

#include <ecuState/EcuState.h>

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace ecustats
{
/** Name of the NvM block that holds the record (reserved block name, tools/rtegen). */
constexpr char const BLOCK_NAME[] = "EcuStatistics";

/** First word of a valid record: "NX", task 05, record version 01. 0 is an erased/default record. */
constexpr uint32_t FORMAT = 0x4E580501UL;

/** Shortest distance between two hand-overs of the operating time to the image (the NvM limits the writes). */
constexpr uint32_t IMAGE_PERIOD_MS = 60000U;

/** Time after the start until a start is counted without the share of the role power_supply. */
constexpr uint32_t START_SETTLE_MS = 5000U;

/**
 * NVM-34: supply share "the supply did not start for this start" (IPowerSupply::restartedForThisStart() false: an
 * MCU-only start, e.g. an MCU software reset with the reset line of the SBC not connected - eval board JP1 open). The
 * supply row counts nothing for such a start: it mirrors the starts of the supply (SBC reset source), and the MCU
 * reset is counted in the MCU row. Not a wakeup bit (those end at bit 23).
 */
constexpr uint32_t SUPPLY_NOT_RESTARTED = 0x80000000UL;
static_assert(
    (SUPPLY_NOT_RESTARTED
     & (::ecustate::wakeup::CAN_BUSES | ::ecustate::wakeup::LOCALS | 0xFFUL))
        == 0U,
    "NVM-34: the marker must not be a wakeup bit");

/** Causes per source; the index is the column in the record and on the console. */
enum class Cause : uint8_t
{
    POWER    = 0U, // supply switched on (power-on reset)
    RESET    = 1U, // reset without wake-up event (pin, software, debugger, lockup, clock loss)
    WATCHDOG = 2U, // watchdog reset
    SUPPLY   = 3U, // low-voltage reset / restart after a supply fault
    TIMER    = 4U, // cyclic wake-up timer
    CAN      = 5U, // wake-up by any CAN bus
    LOCAL    = 6U, // wake-up by any local input
    UNKNOWN  = 7U, // the source cannot tell (no bit, wakeup::UNKNOWN, or not reported)
};

constexpr size_t CAUSE_COUNT = 8U;

inline char const* causeName(size_t const cause)
{
    static char const* const NAMES[CAUSE_COUNT]
        = {"power", "reset", "watchdog", "supply", "timer", "can", "local", "unknown"};
    return (cause < CAUSE_COUNT) ? NAMES[cause] : "?";
}

/**
 * The content of the NvM block EcuStatistics (52 bytes, native byte order of the MCU like every RTE block). Changing
 * it needs a new FORMAT; tools/rtegen derives the record id of the block from name and size, so a block of another
 * size starts from its default.
 */
struct Record
{
    uint32_t format;              // FORMAT
    uint32_t operatingSeconds;    // operating time in s (saturates after 136 years)
    uint32_t starts;              // counted starts (saturates)
    uint32_t lastMcuReasons;      // MCU share of the latest counted start (wakeup bits, 0: cannot tell)
    uint32_t lastSupplyReasons;   // supply share of the latest counted start (wakeup bits, 0: not reported)
    uint16_t mcu[CAUSE_COUNT];    // starts per cause, MCU share (saturate at 65535)
    uint16_t supply[CAUSE_COUNT]; // starts per cause, supply share (saturate at 65535)
};

constexpr uint16_t RECORD_BYTES = 52U;
static_assert(sizeof(Record) == RECORD_BYTES, "NVM-05: the block EcuStatistics has 52 bytes (tools/rtegen)");

inline void increment(uint16_t& counter)
{
    if (counter != 0xFFFFU)
    {
        ++counter;
    }
}

inline void increment(uint32_t& counter)
{
    if (counter != 0xFFFFFFFFUL)
    {
        ++counter;
    }
}

/** Counts the causes of one start of one source: each set cause once; none of them (or UNKNOWN) counts UNKNOWN. */
inline void countReasons(uint16_t (&counters)[CAUSE_COUNT], uint32_t const reasons)
{
    namespace wakeup = ::ecustate::wakeup;
    struct Bit
    {
        uint32_t mask;
        Cause cause;
    };
    static Bit const BITS[] = {
        {wakeup::POWER, Cause::POWER},
        {wakeup::RESET, Cause::RESET},
        {wakeup::WATCHDOG, Cause::WATCHDOG},
        {wakeup::SUPPLY, Cause::SUPPLY},
        {wakeup::TIMER, Cause::TIMER},
        {wakeup::CAN_BUSES, Cause::CAN},
        {wakeup::LOCALS, Cause::LOCAL},
    };
    bool known = false;
    for (Bit const& bit : BITS)
    {
        if ((reasons & bit.mask) != 0U)
        {
            increment(counters[static_cast<size_t>(bit.cause)]);
            known = true;
        }
    }
    if ((!known) || ((reasons & wakeup::UNKNOWN) != 0U))
    {
        increment(counters[static_cast<size_t>(Cause::UNKNOWN)]);
    }
}

/** Where the record came from at the start. */
enum class Origin : uint8_t
{
    NOT_LOADED, // load() not called yet
    FRESH,      // erased or default record (format 0): counting starts at zero
    LOADED,     // stored record of this format
    FORMAT,     // record of another format: dropped, counting starts at zero
};

inline char const* originName(Origin const origin)
{
    switch (origin)
    {
        case Origin::FRESH: return "fresh";
        case Origin::LOADED: return "loaded";
        case Origin::FORMAT: return "other format, restarted";
        default: return "not loaded";
    }
}

/**
 * The statistics of one ECU. Every call runs in one context (the NvM context of the integration). cycle() is
 * called periodically (100 ms in the application); it returns true when the record must go to the image of the
 * block.
 */
class Statistics
{
public:
    /** Takes the record of the block after the NvM read it (stored value or the zero default). */
    Origin load(Record const& stored)
    {
        if (stored.format == FORMAT)
        {
            _record = stored;
            _origin = Origin::LOADED;
        }
        else
        {
            _record        = Record{};
            _record.format = FORMAT;
            _origin        = (stored.format == 0U) ? Origin::FRESH : Origin::FORMAT;
        }
        _imageSeconds = _record.operatingSeconds;
        return _origin;
    }

    /** Begins this start at \p nowMs with the MCU share \p mcuReasons (valid from before main). */
    void start(uint32_t const nowMs, uint32_t const mcuReasons)
    {
        _startMs     = nowMs;
        _lastMs      = nowMs;
        _imageMs     = nowMs;
        _remainderMs = 0U;
        _mcuReasons  = mcuReasons;
        _started     = true;
    }

    /**
     * The share of the role power_supply, once it is known (0 is ignored: not known yet). SUPPLY_NOT_RESTARTED: the
     * supply did not start for this start - the start is counted at once, the supply row stays as it is.
     */
    void supplyReasons(uint32_t const reasons)
    {
        if ((reasons != 0U) && (!_supplyKnown))
        {
            _supplyRestarted = (reasons != SUPPLY_NOT_RESTARTED);
            _supplyReasons   = _supplyRestarted ? reasons : 0U;
            _supplyKnown     = true;
        }
    }

    /** Periodic part. true: the record changed for the image (start counted, supply share, operating time). */
    bool cycle(uint32_t const nowMs)
    {
        if (!_started)
        {
            return false;
        }
        accumulate(nowMs);
        bool changed = false;
        if (!_startCounted)
        {
            if (_supplyKnown || ((nowMs - _startMs) >= START_SETTLE_MS))
            {
                increment(_record.starts);
                countReasons(_record.mcu, _mcuReasons);
                _record.lastMcuReasons    = _mcuReasons;
                _record.lastSupplyReasons = 0U;
                _startCounted             = true;
                changed                   = true;
            }
        }
        if (_startCounted && _supplyKnown && (!_supplyCounted))
        {
            if (_supplyRestarted)
            {
                countReasons(_record.supply, _supplyReasons);
            }
            _record.lastSupplyReasons = _supplyReasons;
            _supplyCounted            = true;
            changed                   = true;
        }
        if ((!changed) && ((nowMs - _imageMs) >= IMAGE_PERIOD_MS) && (_record.operatingSeconds != _imageSeconds))
        {
            changed = true;
        }
        if (changed)
        {
            _imageMs      = nowMs;
            _imageSeconds = _record.operatingSeconds;
        }
        return changed;
    }

    /**
     * Run-down (and "nvm write"): the operating time up to \p nowMs. true: it differs from the image, hand it over
     * before the NvM writes.
     */
    bool flush(uint32_t const nowMs)
    {
        if (!_started)
        {
            return false;
        }
        accumulate(nowMs);
        if (_record.operatingSeconds == _imageSeconds)
        {
            return false;
        }
        _imageMs      = nowMs;
        _imageSeconds = _record.operatingSeconds;
        return true;
    }

    Record const& record() const { return _record; }

    Origin origin() const { return _origin; }

    bool startCounted() const { return _startCounted; }

    bool supplyCounted() const { return _supplyCounted; }

    /** NVM-34: false once the supply reported that it did not start for this start (MCU-only start). */
    bool supplyRestarted() const { return _supplyRestarted; }

    /** Operating time not yet handed to the image, in s. */
    uint32_t unsavedSeconds() const { return _record.operatingSeconds - _imageSeconds; }

private:
    void accumulate(uint32_t const nowMs)
    {
        // unsigned difference: correct over the wrap of the 32-bit millisecond time
        uint32_t const total   = _remainderMs + (nowMs - _lastMs);
        _lastMs                = nowMs;
        uint32_t const seconds = total / 1000U;
        _remainderMs           = total % 1000U;
        uint32_t const room    = 0xFFFFFFFFUL - _record.operatingSeconds;
        _record.operatingSeconds += (seconds < room) ? seconds : room;
    }

    Record _record{};
    Origin _origin          = Origin::NOT_LOADED;
    uint32_t _startMs       = 0U;
    uint32_t _lastMs        = 0U;
    uint32_t _imageMs       = 0U;
    uint32_t _imageSeconds  = 0U;
    uint32_t _remainderMs   = 0U;
    uint32_t _mcuReasons    = 0U;
    uint32_t _supplyReasons = 0U;
    bool _started           = false;
    bool _supplyKnown       = false;
    bool _startCounted      = false;
    bool _supplyCounted     = false;
    bool _supplyRestarted   = true;
};

} // namespace ecustats
