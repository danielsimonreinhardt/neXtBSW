#pragma once

// Host stub of the OpenBSW lifecycle component (ARCH-52 binding test): the three phases, nothing else.

namespace lifecycle
{
class ILifecycleComponent
{
public:
    virtual void init()     = 0;
    virtual void run()      = 0;
    virtual void shutdown() = 0;

protected:
    ~ILifecycleComponent() = default;
};
} // namespace lifecycle
