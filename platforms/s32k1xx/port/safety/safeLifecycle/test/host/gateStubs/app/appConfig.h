/* SYS-42 host test stub of app/appConfig.h for SafetyGateHostTest.cpp: what safeMemory/MemoryLocations.h needs to
 * compile MemoryProtection.cpp on the host - the stack size of the safety task and a stand-in of
 * ::async::internal::Stack.
 *
 * MemoryProtection::init() takes the address of the safety stack as `reinterpret_cast<uint32_t>(&safetyStack[0])`,
 * which is only well-formed where a pointer has 32 bits (the target). On the 64-bit host `&safetyStack[0]` yields
 * the 32-bit address of the stand-in directly (a cast of an integer to its own type), so init() runs unchanged and
 * writes the region descriptors it writes on the target. */
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
