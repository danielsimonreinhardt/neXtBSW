#pragma once

#include "console/ContextCall.h"

#include <async/Async.h>
#include <async/AsyncBinding.h>

namespace console
{

/**
 * Console context of the ECU (SYS-13): every console command of a neXtBSW system is registered here
 * (AsyncCommandWrapper(command, ::console::CONSOLE_CONTEXT)) and reads or changes the state of its system
 * only through runInOwnerContext(). TASK_IDLE exists in every async binding and is the lowest context; the
 * console input (StdioConsoleInput) and the logger output run there as well, so the output of a command,
 * its "ok"/prompt and the log lines are written by one task and cannot interleave on the UART.
 */
constexpr ::async::ContextType CONSOLE_CONTEXT = ::async::AsyncBinding::AdapterType::TASK_IDLE;

using AsyncContextCall = ContextCall<::async::RunnableType, ::async::ContextType, &::async::execute>;

/** The one call of the console: commands run one after the other, so one instance serves every owner. */
inline AsyncContextCall consoleContextCall(CONSOLE_CONTEXT);

/**
 * Runs \p work (short, no output) in \p owner - the task of the system whose data it touches - and waits for
 * it. Inline if the owner is the console context itself, if the owner is not known yet (CONTEXT_INVALID),
 * or if the caller is not the console context: a caller above the owner would wait forever (a system that
 * calls this outside a console command, e.g. from its own cyclic code, keeps working as before).
 * Not inlined: one copy for every command (with inlining the demo application grew by another 4.3 KB of flash).
 */
__attribute__((noinline)) inline void
runInOwnerContext(::async::ContextType const owner, void* const work, void (*const thunk)(void*))
{
    bool const fromConsole
        = (::async::AsyncBinding::AdapterType::getCurrentTaskContext() == CONSOLE_CONTEXT);
    if ((owner == ::async::CONTEXT_INVALID) || !fromConsole || !consoleContextCall.run(owner, work, thunk))
    {
        thunk(work); // before the owner is known / not from the console / call in progress: never expected
    }
}

template<class Work>
void runInOwnerContext(::async::ContextType const owner, Work& work)
{
    runInOwnerContext(owner, AsyncContextCall::erase(work), &AsyncContextCall::invoke<Work>);
}

} // namespace console
