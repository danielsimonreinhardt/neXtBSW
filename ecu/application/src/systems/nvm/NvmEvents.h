// NVM-19: the events of the NvM start on their way to the fault memory.
//
// nvm::Manager (libs/bsw/nvm) reports from readAll() when an ASIL copy was repaired (COPY_REPAIRED), could not be
// repaired (COPY_REPAIR_FAILED), or a block lost its value and starts from its default (DEFAULT_LOADED), and (NVM-30)
// when one place of a QM block was defective and the value of the other one was loaded (PLACE_DEFECTIVE). readAll()
// runs in RteSystem::init before the fault memory is up - the fault memory loads its own state from two NvM blocks -
// so EventLog only records the events per block, and reportToFaultMemory() hands them over once the fault memory runs.
//
// DEM_EVENTS is the one place that ties an event of the NvM to the fault memory: the BSW event name of a DTC in the
// project's diag.yaml (dtcs[].event.bsw) is "<prefix>.<Block>", e.g. nvm.default_loaded.Trim - one DTC per event and
// block. The DTC numbers are not here but in diag.yaml: the neXtBSW range (bsw_dtc_range.base, default 0x160000)
// plus the offset of the event, low byte = index of the block (TOOL-178, tools/nxconfig/bswdtc.py PLAN):
// +0x06nn default loaded, +0x07nn copy repair failed, +0x08nn copy repaired, +0x09nn place defective (QM, NVM-30).
// An event without a DTC of its name is only counted (console "nvm status").
//
// Storage, board and family neutral; host test: test/host/NvmIntegrationHostTest.cpp.

#pragma once

#include <dem/IFaultMemory.h>
#include <nvm/Nvm.h>

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace nvm
{

/** Number of kinds of nvm::BlockEvent. */
constexpr size_t EVENT_KINDS = 4U;

/** Records the events of readAll() per block (one bit per kind), with a count and the first block per kind. */
template<size_t N>
class EventLog final : public IEventSink
{
public:
    void onBlockEvent(size_t const index, BlockConfig const& /* block */, BlockEvent const event) override
    {
        size_t const kind = static_cast<size_t>(event);
        if (kind >= EVENT_KINDS)
        {
            return;
        }
        if (index < N)
        {
            _raised[index] = static_cast<uint8_t>(_raised[index] | (1U << kind));
        }
        if (_count[kind] == 0U)
        {
            _firstBlock[kind] = index;
        }
        ++_count[kind];
    }

    /** true if block \p index raised \p event at this start. */
    bool raised(size_t const index, BlockEvent const event) const
    {
        size_t const kind = static_cast<size_t>(event);
        return (index < N) && (kind < EVENT_KINDS) && ((_raised[index] & (1U << kind)) != 0U);
    }

    uint32_t count(BlockEvent const event) const
    {
        size_t const kind = static_cast<size_t>(event);
        return (kind < EVENT_KINDS) ? _count[kind] : 0U;
    }

    /** Index of the first block that raised \p event (meaningful while count(event) > 0). */
    size_t firstBlock(BlockEvent const event) const
    {
        size_t const kind = static_cast<size_t>(event);
        return (kind < EVENT_KINDS) ? _firstBlock[kind] : 0U;
    }

private:
    uint8_t _raised[(N > 0U) ? N : 1U]  = {};
    uint32_t _count[EVENT_KINDS]    = {};
    size_t _firstBlock[EVENT_KINDS] = {};
};

/** An event of the NvM and the prefix of the BSW event names of its DTCs ("<prefix>.<Block>"). */
struct DemEvent
{
    BlockEvent event;
    char const* prefix;
};

constexpr DemEvent DEM_EVENTS[EVENT_KINDS] = {
    {BlockEvent::DEFAULT_LOADED, "nvm.default_loaded"},
    {BlockEvent::COPY_REPAIR_FAILED, "nvm.copy_repair_failed"},
    {BlockEvent::COPY_REPAIRED, "nvm.copy_repaired"},
    {BlockEvent::PLACE_DEFECTIVE, "nvm.place_defective"}, // NVM-30, QM
};

/** Longest BSW event name reportToFaultMemory() builds (a longer one cannot be a DTC of the block and is skipped). */
constexpr size_t DEM_EVENT_NAME_MAX = 95U;

/** "<prefix>.<block>" into \p out (\p size bytes with the terminator); false if it does not fit. */
inline bool demEventName(char const* const prefix, char const* const block, char* const out, size_t const size)
{
    size_t const p = std::strlen(prefix);
    size_t const b = std::strlen(block);
    if ((p + 1U + b) >= size)
    {
        return false;
    }
    (void)std::memcpy(out, prefix, p);
    out[p] = '.';
    (void)std::memcpy(&out[p + 1U], block, b);
    out[p + 1U + b] = '\0';
    return true;
}

/**
 * Hands the events of one start to the fault memory: for every block that was read (not without a memory, not
 * beyond it) and every kind of DEM_EVENTS, a DTC whose event is "<prefix>.<Block>" gets FAILED if the block raised
 * that event and PASSED if it did not - one result per start, so such a DTC should confirm on one report (like the
 * start-up check of the calibration segment). Call it after readAll(). Returns the number of results reported.
 */
template<size_t N, class Lock>
size_t reportToFaultMemory(EventLog<N> const& log, Manager<Lock> const& manager, ::dem::IFaultMemory& memory)
{
    size_t reported = 0U;
    char name[DEM_EVENT_NAME_MAX + 1U];
    size_t const count = (manager.count() < N) ? manager.count() : N;
    for (size_t i = 0U; i < count; ++i)
    {
        Quality const quality = manager.status(i).quality;
        if ((quality == Quality::NOT_READ) || (quality == Quality::NO_MEMORY))
        {
            continue; // not checked at this start: no result
        }
        for (DemEvent const& e : DEM_EVENTS)
        {
            if (!demEventName(e.prefix, manager.block(i).name, name, sizeof(name)))
            {
                continue;
            }
            int32_t const dtc = memory.findReportedEvent(name);
            if (dtc >= 0)
            {
                memory.report(
                    static_cast<uint16_t>(dtc), log.raised(i, e.event) ? ::dem::Result::FAILED : ::dem::Result::PASSED);
                ++reported;
            }
        }
    }
    return reported;
}

inline char const* eventName(BlockEvent const event)
{
    switch (event)
    {
        case BlockEvent::COPY_REPAIRED: return "copy repaired";
        case BlockEvent::COPY_REPAIR_FAILED: return "copy repair failed";
        case BlockEvent::DEFAULT_LOADED: return "default loaded";
        case BlockEvent::PLACE_DEFECTIVE: return "place defective";
        default: return "?";
    }
}

inline char const* qualityName(Quality const quality)
{
    switch (quality)
    {
        case Quality::NOT_READ: return "not read";
        case Quality::STORED: return "stored";
        case Quality::DEFAULT: return "default";
        case Quality::UNSECURED: return "unsecured";
        case Quality::NO_MEMORY: return "no memory";
        default: return "?";
    }
}

} // namespace nvm
