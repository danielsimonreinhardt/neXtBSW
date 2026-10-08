#pragma once

// Host stub of the console command wrapper for HighSideSwitchBindingHostTest.cpp (ARCH-66).

#include <async/Types.h>

namespace console
{
class AsyncCommandWrapper
{
public:
    template<class Command>
    AsyncCommandWrapper(Command& /* command */, ::async::ContextType /* context */)
    {}
};
} // namespace console
