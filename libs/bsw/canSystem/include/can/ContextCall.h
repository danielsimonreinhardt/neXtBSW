#pragma once

// COM-24 introduced the call into the owner's task for the "can" commands; SYS-13 moved it to the general
// module libs/bsw/consoleContext (console/ContextCall.h, target binding console/ConsoleContext.h), which
// every system with a console command uses. This alias keeps the name of COM-24 (host test
// test/host/CanConsoleHostTest.cpp).
#include "console/ContextCall.h"

namespace can
{

template<class RunnableBase, class ContextT, void (*Execute)(ContextT, RunnableBase&)>
using ContextCall = ::console::ContextCall<RunnableBase, ContextT, Execute>;

} // namespace can
