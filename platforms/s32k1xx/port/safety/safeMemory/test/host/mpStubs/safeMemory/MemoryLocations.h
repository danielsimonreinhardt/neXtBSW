/* SYS-171 host test stub of safeMemory/MemoryLocations.h for MemoryProtectionHostTest.cpp: the linker symbols as
 * fixed addresses of a target-like layout, so that MemoryProtection::init() writes the descriptors of the eval board
 * and not those of host addresses (the include path puts this directory before safeMemory/include).
 *   protected RAM (.mpu_bss)  0x20000400 .. 0x200007FF   descriptor 2 (gate)
 *   safety stack              0x20001000 .. 0x200017FF   descriptor 4 (stand-in in the test)
 *   guard below the MSP       0x20006000 .. 0x2000601F   descriptor 6, rest of descriptor 5 in descriptor 7
 */
#pragma once

#include <app/appConfig.h>

#include <cstdint>

#define __MPU_BSS_START   (reinterpret_cast<uint32_t const*>(uintptr_t{0x20000400U}))
#define __MPU_BSS_END     (reinterpret_cast<uint32_t const*>(uintptr_t{0x20000800U}))
#define __StackGuardStart (reinterpret_cast<uint32_t const*>(uintptr_t{0x20006000U}))
#define __StackGuardEnd   (reinterpret_cast<uint32_t const*>(uintptr_t{0x20006020U}))

extern ::async::internal::Stack<safety_task_stackSize> safetyStack;
