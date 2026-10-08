// Host replacement of the FreeRTOS async types: one context, simulated time (HostAsync.cpp).

#pragma once

#include "async/IRunnable.h"

#include <cstdint>

namespace async
{
using RunnableType = IRunnable;
using ContextType  = uint8_t;

ContextType const CONTEXT_INVALID = 0xFFU;

class Lock
{
public:
    Lock() {}

    ~Lock() {}
};

class ModifiableLock
{
public:
    ModifiableLock() {}

    ~ModifiableLock() {}

    void unlock() {}

    void lock() {}
};

using LockType           = Lock;
using ModifiableLockType = ModifiableLock;

struct TimeoutType
{
    TimeoutType();
    ~TimeoutType();

    void cancel();

    IRunnable* _runnable;
    ContextType _context;
    uint64_t _dueUs;
    uint32_t _periodUs;
    bool _active;
    TimeoutType* _nextTimeout;
};

struct TimeUnit
{
    enum Type
    {
        MICROSECONDS = 1,
        MILLISECONDS = 1000,
        SECONDS      = 1000000
    };
};

using TimeUnitType = TimeUnit::Type;

} // namespace async
