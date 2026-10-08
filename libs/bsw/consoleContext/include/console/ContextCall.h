#pragma once

#include <cstdint>

namespace console
{

/**
 * Runs a short piece of work in the task that owns the data, called from a task of lower priority,
 * and waits until it is done (COM-24, general since SYS-13).
 *
 * Why: OpenBSW's AsyncCommandWrapper executes a console command completely in the context it was
 * given, and the console output goes to the UART synchronously, byte by byte (86.8 us per character at
 * 115200 Bd). A system that registers its command with its own context lends that task to the UART:
 * "can status" (450-570 characters per bus) held task can - and with it the transmit gate, reception
 * and DoCAN/UDS - for 40-50 ms; "rte json" held the runnables of the first application task for several
 * cycles and falsified the statistics it printed; "sbc dump" held the task with the SBC watchdog trigger.
 * The fix splits a command:
 *
 *   caller (console) context  parse the arguments, format and print the output   (slow, UART bound)
 *   owner context             read or change the system's state: a snapshot, a send, a flag  (short)
 *
 * The console context must be the lowest one (TASK_IDLE: it exists in every async binding, and the
 * console input and the logger output run there as well). The owner then preempts the caller as soon as
 * the work is queued, so the wait loop below normally does not spin at all; the owner is busy only for
 * the work itself, independent of how long the output is. Data of the owner is touched in the owner's
 * task only, as before, so no lock is needed and the state seen by the command is consistent.
 *
 * Preconditions:
 *  - the caller runs at a lower priority than the owner, or is the owner (then the work runs inline);
 *    a caller above the owner would wait forever
 *  - one call at a time: the console executes one command after the other (AsyncConsole)
 *  - no timeout: a call left behind would let the owner run work that refers to the caller's stack
 *  - the work does not print: output belongs to the caller (it would hold the owner at the UART again)
 *
 * Hardware and OS independent: the runnable interface, the context type and the function that queues
 * a runnable are template parameters (::async::RunnableType, ::async::ContextType, ::async::execute on
 * the target, console/ConsoleContext.h; a scheduler model in the host tests under test/host).
 *
 * \tparam RunnableBase  interface with a virtual void execute()
 * \tparam ContextT      context number
 * \tparam Execute       queues a runnable in a context
 */
template<class RunnableBase, class ContextT, void (*Execute)(ContextT, RunnableBase&)>
class ContextCall final : private RunnableBase
{
public:
    /** \param caller  context the calls come from (the console: TASK_IDLE) */
    explicit ContextCall(ContextT const caller)
    : RunnableBase(), _caller(caller), _work(nullptr), _thunk(nullptr), _done(true), _queued(0U)
    {}

    ContextCall(ContextCall const&)            = delete;
    ContextCall& operator=(ContextCall const&) = delete;

    ContextT callerContext() const { return _caller; }

    /** Number of calls that went through the owner's queue (host test, diagnostics). */
    uint32_t queuedCalls() const { return _queued; }

    /**
     * Runs \p work (callable without parameters) in \p owner and returns when it is done. Inline if the
     * owner is the caller's own context. Returns false without running anything if a call is still in
     * progress (not expected, see the preconditions).
     */
    template<class Work>
    bool run(ContextT const owner, Work& work)
    {
        return run(owner, erase(work), &invoke<Work>);
    }

    /**
     * Same with the work type erased: \p thunk(\p work) is called once. Every command calls this one function,
     * the templates above only add the small invoke<Work> per piece of work (flash, SYS-13).
     */
    bool run(ContextT const owner, void* const work, void (*const thunk)(void*))
    {
        if (!_done)
        {
            return false;
        }
        if (owner == _caller)
        {
            thunk(work);
            return true;
        }
        _work  = work;
        _thunk = thunk;
        _done  = false;
        ++_queued;
        Execute(owner, *this);
        while (!_done)
        {
            // The owner has the higher priority and preempts this loop as soon as the work is queued.
        }
        _work  = nullptr;
        _thunk = nullptr;
        return true;
    }

    /** Work may be const (a lambda); invoke<Work> restores the exact type. */
    template<class Work>
    static void* erase(Work& work)
    {
        return const_cast<void*>(static_cast<void const*>(&work));
    }

    template<class Work>
    static void invoke(void* const work)
    {
        (*static_cast<Work*>(work))();
    }

private:
    void execute() override
    {
        if (_thunk != nullptr)
        {
            _thunk(_work);
        }
        _done = true;
    }

    ContextT const _caller;
    void* _work;
    void (*_thunk)(void*);
    bool volatile _done;
    uint32_t _queued;
};

} // namespace console
