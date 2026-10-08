#pragma once

// Host stub of the OpenBSW lifecycle manager (ARCH-52 binding test): records every addComponent() call.

#include "lifecycle/ILifecycleComponent.h"

#include <cstddef>
#include <cstdint>

namespace lifecycle
{
class LifecycleManager
{
public:
    struct Entry
    {
        char const* name;
        ILifecycleComponent* component;
        uint8_t level;
    };

    static constexpr size_t CAPACITY = 16U;

    void addComponent(char const* const name, ILifecycleComponent& component, uint8_t const level)
    {
        if (_count < CAPACITY)
        {
            _entries[_count] = Entry{name, &component, level};
        }
        ++_count;
    }

    size_t count() const { return _count; }

    Entry const& entry(size_t const i) const { return _entries[i]; }

private:
    Entry _entries[CAPACITY] = {};
    size_t _count            = 0U;
};
} // namespace lifecycle
