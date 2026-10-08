/* Host test stub of lifecycle/AsyncLifecycleComponent.h (SYS-05): the transitions are called directly. */

#pragma once

#include "async/Async.h"

namespace lifecycle
{

class AsyncLifecycleComponent
{
public:
    virtual ~AsyncLifecycleComponent() = default;

    virtual void init()     = 0;
    virtual void run()      = 0;
    virtual void shutdown() = 0;

    ::async::ContextType transitionContext = ::async::CONTEXT_INVALID;
    unsigned transitionsDone               = 0U;

protected:
    void setTransitionContext(::async::ContextType const context) { transitionContext = context; }

    void transitionDone() { ++transitionsDone; }
};

} // namespace lifecycle
