/* Host test stub of async/IRunnable.h (SYS-05): the interface RteSystem::TaskRunner implements. */

#pragma once

namespace async
{

class IRunnable
{
public:
    virtual ~IRunnable() = default;

    virtual void execute() = 0;
};

} // namespace async
