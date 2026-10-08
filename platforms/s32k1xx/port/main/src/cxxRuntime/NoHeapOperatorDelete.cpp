/* DOC-28 (doc/safety/dev-environment.md section 4.6 P5 (a), finding DE5, rule NXCPP-05): the platform's own
 * operator delete - no heap in the image.
 *
 * Every class with a virtual destructor gets a deleting destructor that its vtable names, also when no delete
 * expression exists (for example lifecycle::LifecycleManager::ComponentTransitionExecutor of OpenBSW). The deleting
 * destructor calls the sized operator delete(void*, std::size_t). Without a definition of our own the linker takes
 * libstdc++'s (del_ops.o -> del_op.o), which calls free() and so links newlib's allocator: malloc, _malloc_r, free,
 * _free_r, _sbrk_r and libnosys _sbrk, 488 bytes on the demo, with an _sbrk that grows from the end of .bss without a
 * limit. Dead at run time - but a future delete expression or a library function that allocates would link and run
 * it silently.
 *
 * Both replaceable forms are defined here. neXtBSW has no dynamic memory (NXCPP-05), so a call can only come from a
 * programming error: it ends in the fault reaction of the safe supervisor, S1 + S2 with its console line
 * "SAFETY RESET (ETL_ASSERT, at 0x...): outputs off" (SafeSupervisor::reportEtlAssert - usable from every context,
 * before the supervisor is constructed and without a SafetyShell). The address is the return address of the call of
 * operator delete; arm-none-eabi-addr2line -f -e app.ecu.elf <address> names the caller. The call never returns.
 *
 * tools/elfcheck checks the linked images for the allocator (code `heap-symbol`): the definitions here only work if
 * they are really taken, and a new path to malloc (another operator, strdup, an allocating library call) turns it red.
 *
 * Linked as an object on the link line of the executable (main/CMakeLists.txt, like nxNewlibSyscalls), not as an
 * archive member: a deleting destructor in an archive scanned after this one would otherwise pull libstdc++'s member.
 */

#include "reset/softwareSystemReset.h"

#include <safeSupervisor/SafeSupervisor.h>

#include <etl/infinite_loop.h>

#include <cstddef>
#include <cstdint>
#include <new>

namespace
{
// noinline and noreturn: the reaction has one place, and the compiler knows nothing follows the call
__attribute__((noinline, noreturn)) void heapReleaseFailed(uintptr_t const returnAddress)
{
    ::safety::SafeSupervisor::getInstance().reportEtlAssert(static_cast<uint32_t>(returnAddress));
    // Not reached: ETL_ASSERT is an S1 + S2 reaction. Should it ever come back, reset anyway.
    softwareSystemReset();
    etl::infinite_loop();
}
} // namespace

// noinline: __builtin_return_address(0) is the return address into the caller of operator delete
__attribute__((noinline)) void operator delete(void* const pointer) noexcept
{
    (void)pointer;
    heapReleaseFailed(reinterpret_cast<uintptr_t>(__builtin_return_address(0)));
}

__attribute__((noinline)) void operator delete(void* const pointer, std::size_t const size) noexcept
{
    (void)pointer;
    (void)size;
    heapReleaseFailed(reinterpret_cast<uintptr_t>(__builtin_return_address(0)));
}
