/* Host test stub of console/ConsoleContext.h (SYS-05): the call into the owning task runs inline. What SYS-13
 * guarantees (the snapshot really runs in the owner) is covered by the host test of RteConsole.h; here only the
 * deadline supervision of RteSystem.cpp is under test. */

#pragma once

#include "async/Async.h"

namespace console
{

constexpr ::async::ContextType CONSOLE_CONTEXT = 0U;

/** Counts the calls, so a test can see that the command did not read the values itself. */
inline unsigned ownerCalls = 0U;

template<class Work>
void runInOwnerContext(::async::ContextType const /*owner*/, Work& work)
{
    ++ownerCalls;
    work();
}

} // namespace console
