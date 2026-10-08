/* Host test stub of async/Async.h (SYS-05): no RTOS. scheduleAtFixedRate() only records which runnable was
 * scheduled in which context and at which period, so the test can run the task cycles itself. */

#pragma once

#include "async/IRunnable.h"

#include <cstddef>
#include <cstdint>

namespace async
{

using ContextType = uint8_t;

constexpr ContextType CONTEXT_INVALID = 0xFFU;

enum class TimeUnit : uint8_t
{
    MICROSECONDS,
    MILLISECONDS,
    SECONDS
};

class TimeoutType
{
public:
    bool cancelled = false;

    void cancel() { cancelled = true; }
};

/** One entry of the fake scheduler; the test drives these runnables by hand. */
struct Scheduled
{
    ContextType context  = 0U;
    IRunnable* runnable  = nullptr;
    uint32_t periodMs    = 0U;
};

constexpr size_t MAX_SCHEDULED = 8U;

inline Scheduled scheduled[MAX_SCHEDULED]{};
inline size_t scheduledCount = 0U;

inline void scheduleAtFixedRate(
    ContextType const context,
    IRunnable& runnable,
    TimeoutType& timeout,
    uint32_t const period,
    TimeUnit const /*unit*/)
{
    timeout.cancelled = false;
    if (scheduledCount < MAX_SCHEDULED)
    {
        scheduled[scheduledCount] = Scheduled{context, &runnable, period};
        ++scheduledCount;
    }
}

} // namespace async
