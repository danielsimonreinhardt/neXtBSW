#pragma once

/* SYS-37: event report from a context without write access to the protected safety RAM.
 *
 * The safe supervisor and its monitors live in the RAM the MPU keeps read-only outside the safety shell (fusa gate,
 * SM-10: on the S32K1 every .bss of a libsafe* library, platforms/s32k1xx/port/main/linkerscript). A monitor of
 * safeMonitor stores its context in itself and then calls SafeSupervisor::handle() - both writes into that RAM.
 * From the safety task (gate open) or inside a SafetyShell that is correct; from any other task it is a bus fault
 * (board 2026-09-21: the RTE task reported a deadline violation, IMPRECISERR, HardFault, reset loop).
 *
 * Instead of opening the gate for the reporting task, the report goes the other way round:
 * - the reporter writes the context word into a small box in ordinary RAM (post(), any task, any interrupt);
 * - the safety task empties the box in its cycle, with the gate open, and triggers the real monitor there
 *   (drain()). The supervisor decides the reaction as before, only up to one safety cycle later.
 * No code outside the safety task ever gets write access to the protected RAM, and nothing in the box is trusted:
 * every slot is stored with its bit-inverted copy and the indices are checked, so a wild write into the box is
 * reported (CONTEXT_CORRUPTED) instead of forging or swallowing an event silently. A full box drops the newest
 * report and says so (CONTEXT_LOST) after the ones it kept.
 *
 * One box per monitor, never one shared queue: a source that floods its box cannot push the reports of another
 * source out (separation of the base-software mechanisms from the later device-driver packages, doc/safety/
 * concept.md section 1.4 and 13.1).
 *
 * Where the box lives is the platform's decision (it owns the memory map): the box object is defined by the family
 * port, outside the protected region; the supervisor only keeps a reference to it (in its protected RAM, so the
 * reference itself cannot be bent by a wild write). */

#include <safeMonitor/Trigger.h>

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>

#include <cstdint>

namespace safety
{

class SafetyEventMailbox
{
public:
    /** Reports one box keeps between two safety cycles. The only reporter today (RTE, SM-05) latches every source
     * once per measurement window, so a burst is at most two per task (cycle and runnable) in one cycle. */
    static constexpr uint32_t CAPACITY = 8U;

    /** Context delivered after the kept reports when reports were dropped because the box was full. */
    static constexpr uint32_t CONTEXT_LOST = 0xFFFFFFFEU;
    /** Context delivered when the box failed its check (slot and inverted copy differ, or indices out of range). */
    static constexpr uint32_t CONTEXT_CORRUPTED = 0xFFFFFFFFU;

    /**
     * Stores one report. Callable from any task and from interrupts; never writes the protected RAM. The interrupt
     * lock covers a few instructions only (a single core, several producers, the safety task as consumer).
     * \return false if the box was full and the report was dropped (counted, reported as CONTEXT_LOST)
     */
    bool post(uint32_t const context)
    {
        ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
        uint32_t const used = _head - _tail;
        if (used >= CAPACITY)
        {
            if (used == CAPACITY)
            {
                _lost = _lost + 1U;
            }
            // used > CAPACITY: the indices are broken; drain() reports that, nothing is written here
            return false;
        }
        Slot& slot   = _slots[_head % CAPACITY];
        slot.context = context;
        slot.inverse = ~context;
        _head        = _head + 1U;
        return true;
    }

    /**
     * Hands every stored report to `deliver` (in the order of post()), then CONTEXT_LOST once if reports were
     * dropped, or CONTEXT_CORRUPTED once (and nothing else) if the box failed its check. Safety context only: the
     * delivery triggers a monitor in the protected RAM. `deliver` runs without the interrupt lock of the box.
     * \return number of contexts handed to `deliver`
     */
    template<typename Deliver>
    uint32_t drain(Deliver&& deliver)
    {
        uint32_t delivered = 0U;
        for (uint32_t i = 0U; i < CAPACITY; ++i)
        {
            uint32_t context = 0U;
            Take const take  = takeOne(context);
            if (take == Take::EMPTY)
            {
                break;
            }
            deliver(context);
            ++delivered;
            if (take == Take::CORRUPTED)
            {
                return delivered;
            }
        }
        if (takeLost())
        {
            deliver(CONTEXT_LOST);
            ++delivered;
        }
        return delivered;
    }

    /** Reports dropped since the last drain() (diagnostics and tests). */
    uint32_t lost() const { return _lost; }

    /** Reports waiting for the safety task (diagnostics and tests). */
    uint32_t pending() const { return _head - _tail; }

private:
    enum class Take : uint8_t
    {
        EMPTY,
        OK,
        CORRUPTED
    };

    struct Slot
    {
        uint32_t context;
        uint32_t inverse;
    };

    Take takeOne(uint32_t& context)
    {
        ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
        uint32_t const used = _head - _tail;
        if (used == 0U)
        {
            return Take::EMPTY;
        }
        if (used > CAPACITY)
        {
            resetLocked();
            context = CONTEXT_CORRUPTED;
            return Take::CORRUPTED;
        }
        Slot const& slot = _slots[_tail % CAPACITY];
        if (slot.inverse != static_cast<uint32_t>(~slot.context))
        {
            resetLocked();
            context = CONTEXT_CORRUPTED;
            return Take::CORRUPTED;
        }
        context = slot.context;
        _tail   = _tail + 1U;
        return Take::OK;
    }

    bool takeLost()
    {
        ::interrupts::SuspendResumeAllInterruptsScopedLock const lock;
        bool const wasLost = (_lost != 0U);
        _lost              = 0U;
        return wasLost;
    }

    void resetLocked()
    {
        _tail = _head;
        _lost = 0U;
    }

    Slot _slots[CAPACITY]{};
    uint32_t volatile _head{0U};
    uint32_t volatile _tail{0U};
    uint32_t volatile _lost{0U};
};

/**
 * A trigger monitor that may be triggered from any context (SYS-37). trigger() only posts into the box of the
 * monitor (ordinary RAM); deliver() - called by the safe supervisor in the safety task, gate open - empties the
 * box into the real safeMonitor::Trigger, which stores the context and calls the handler as usual. Same trigger()
 * and getContext() as safeMonitor::Trigger, so a reporter does not change.
 */
template<typename Handler, typename Event>
class DeferredTrigger
{
public:
    DeferredTrigger(Handler& handler, Event const& event, SafetyEventMailbox& mailbox)
    : _monitor(handler, event), _mailbox(mailbox)
    {}

    /** Any context: stores the report in the box, writes nothing in the protected RAM. */
    void trigger(uint32_t const context) { (void)_mailbox.post(context); }

    /** Safety context only (protected RAM writable): hands every waiting report to the monitor. */
    uint32_t deliver()
    {
        return _mailbox.drain([this](uint32_t const context) { _monitor.trigger(context); });
    }

    /** Context of the report the handler is currently processing (or processed last). */
    uint32_t const& getContext() const { return _monitor.getContext(); }

private:
    ::safeMonitor::Trigger<Handler, Event, ::safeMonitor::DefaultMutex, uint32_t> _monitor;
    SafetyEventMailbox& _mailbox;
};

} // namespace safety
