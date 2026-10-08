#pragma once

// Host stub of the OpenBSW lifecycle component base for HighSideSwitchBindingHostTest.cpp (ARCH-66).

#include <async/Types.h>

namespace lifecycle
{
class SingleContextLifecycleComponent
{
public:
    explicit SingleContextLifecycleComponent(::async::ContextType const context) : _context(context) {}

    virtual void init()     = 0;
    virtual void run()      = 0;
    virtual void shutdown() = 0;

    ::async::ContextType context() const { return _context; }

protected:
    ~SingleContextLifecycleComponent() = default;

private:
    ::async::ContextType _context;
};
} // namespace lifecycle
