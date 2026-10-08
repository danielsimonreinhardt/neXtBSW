#pragma once

// Host stub of the OpenBSW async types for HighSideSwitchBindingHostTest.cpp (ARCH-66): only what
// highSideSwitch/HighSideSwitchSystem.h names; the system itself is never created in that test.

#include <cstdint>

namespace async
{
using ContextType = uint8_t;

class RunnableType
{
public:
    virtual void execute() = 0;

protected:
    ~RunnableType() = default;
};

struct TimeoutType
{};
} // namespace async
