/* Host test stub of console/AsyncCommandWrapper.h (SYS-05): registration at the console, does nothing. */

#pragma once

#include "async/Async.h"

namespace console
{

class AsyncCommandWrapper
{
public:
    template<class Command>
    AsyncCommandWrapper(Command& /*command*/, ::async::ContextType const /*context*/)
    {}
};

} // namespace console
