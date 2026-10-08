/* SYS-171 host test stub of app/appConfig.h for MemoryProtectionHostTest.cpp: the stack size of the safety task and a
 * stand-in of ::async::internal::Stack whose `&stack[0]` yields the 32-bit target address directly (as the stub of
 * the safety gate test, safeLifecycle/test/host/gateStubs/app/appConfig.h). */
#pragma once

#include <cstddef>
#include <cstdint>

#define safety_task_stackSize 2048U

namespace async
{
namespace internal
{
template<size_t N>
class Stack
{
public:
    struct Slot
    {
        uint32_t address;

        uint32_t operator&() const { return address; }
    };

    explicit constexpr Stack(uint32_t const base) : _base(base) {}

    Slot operator[](size_t const index) const { return Slot{_base + static_cast<uint32_t>(index * 4U)}; }

private:
    uint32_t _base;
};
} // namespace internal
} // namespace async
