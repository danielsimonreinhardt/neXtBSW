// NVM-05: the ECU statistics (stats/EcuStatistics.h) on their NvM block. The block is found by its reserved name
// EcuStatistics in the block table of the NvM (rte::NVM_BLOCKS, tools/rtegen); write policy and min_interval_ms are
// the project values of that block (project.yaml nvm.blocks.EcuStatistics), so how often the record is written is
// decided in exactly one place. Without the block - or with a block of another size - the statistics only count in
// RAM for this start and say so on the console. The record goes to the image of the block under the lock of the RTE,
// the way the RTE hands over a changed port value (the NvM takes the image under the same lock).
//
// Manager: nvm::Manager<Lock> of libs/bsw/nvm (block(i), status(i), isPending(i)). Every call runs in the NvM context.

#pragma once

#include "EcuStatistics.h"

#include <nvm/Nvm.h>

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace ecustats
{
enum class Binding : uint8_t
{
    NONE,  // the project has no block EcuStatistics: counted in RAM for this start only
    SIZE,  // a block of that name with another size: not used
    BOUND, // the record lives in the block
};

inline char const* bindingName(Binding const binding)
{
    switch (binding)
    {
        case Binding::BOUND: return "block EcuStatistics";
        case Binding::SIZE: return "block EcuStatistics has another size, not persistent";
        default: return "no block EcuStatistics in this project, not persistent";
    }
}

/** What the console shows, taken in the NvM context in one go. */
struct Snapshot
{
    Record record;
    Binding binding;
    Origin origin;
    bool startCounted;
    bool supplyCounted;
    bool supplyRestarted;   // NVM-34: false after an MCU-only start (supply row not counted)
    uint32_t unsavedSeconds;
    uint32_t handovers;     // records handed to the image since the start
    // the block (binding BOUND only)
    char const* blockState; // nvm::stateName
    char const* policy;     // "change" / "shutdown"
    uint32_t minIntervalMs;
    uint32_t writes;        // successful writes since the start
    uint32_t errors;
    bool pending;
};

template<class Manager, class Lock>
class NvmStatistics
{
public:
    /**
     * After the NvM read its \p count blocks: finds the block, loads the record from its image (stored value or the
     * zero default) and begins this start with the MCU share \p mcuReasons.
     */
    void start(Manager& manager, size_t const count, uint32_t const nowMs, uint32_t const mcuReasons)
    {
        _manager = &manager;
        _index   = count;
        _binding = Binding::NONE;
        for (size_t i = 0U; i < count; ++i)
        {
            ::nvm::BlockConfig const& b = manager.block(i);
            if ((b.name != nullptr) && (std::strcmp(b.name, BLOCK_NAME) == 0))
            {
                _index   = i;
                _binding = ((b.size == RECORD_BYTES) && (b.image != nullptr) && (b.changed != nullptr))
                               ? Binding::BOUND
                               : Binding::SIZE;
                break;
            }
        }
        Record stored{};
        if (_binding == Binding::BOUND)
        {
            Lock const lock;
            (void)std::memcpy(&stored, manager.block(_index).image, RECORD_BYTES);
        }
        (void)_stats.load(stored);
        _stats.start(nowMs, mcuReasons);
    }

    /**
     * Periodic part, before the cyclic part of the NvM: \p supplyReasons 0 while the supply share is not known,
     * SUPPLY_NOT_RESTARTED after an MCU-only start (NVM-34).
     */
    void cycle(uint32_t const nowMs, uint32_t const supplyReasons)
    {
        _stats.supplyReasons(supplyReasons);
        if (_stats.cycle(nowMs))
        {
            handOver();
        }
    }

    /** Before the NvM writes every changed block (run-down, "nvm write"): the operating time up to now. */
    void flush(uint32_t const nowMs)
    {
        if (_stats.flush(nowMs))
        {
            handOver();
        }
    }

    void snapshot(Snapshot& s) const
    {
        s.record          = _stats.record();
        s.binding         = _binding;
        s.origin          = _stats.origin();
        s.startCounted    = _stats.startCounted();
        s.supplyCounted   = _stats.supplyCounted();
        s.supplyRestarted = _stats.supplyRestarted();
        s.unsavedSeconds  = _stats.unsavedSeconds();
        s.handovers       = _handovers;
        s.blockState      = "-";
        s.policy          = "-";
        s.minIntervalMs   = 0U;
        s.writes          = 0U;
        s.errors          = 0U;
        s.pending         = false;
        if ((_binding == Binding::BOUND) && (_manager != nullptr))
        {
            ::nvm::BlockConfig const& b  = _manager->block(_index);
            ::nvm::BlockStatus const& st = _manager->status(_index);
            s.blockState                 = ::nvm::stateName(st.state);
            s.policy        = (b.policy == ::nvm::WritePolicy::CHANGE) ? "change" : "shutdown";
            s.minIntervalMs = b.minIntervalMs;
            s.writes        = st.writes;
            s.errors        = st.errors;
            s.pending       = _manager->isPending(_index);
        }
    }

    Statistics const& statistics() const { return _stats; }

    Binding binding() const { return _binding; }

private:
    void handOver()
    {
        ++_handovers;
        if (_binding != Binding::BOUND)
        {
            return;
        }
        ::nvm::BlockConfig const& b = _manager->block(_index);
        Record const& record        = _stats.record();
        Lock const lock;
        (void)std::memcpy(b.image, &record, RECORD_BYTES);
        *b.changed = true;
    }

    Statistics _stats;
    Manager* _manager   = nullptr;
    size_t _index       = 0U;
    Binding _binding    = Binding::NONE;
    uint32_t _handovers = 0U;
};

} // namespace ecustats
