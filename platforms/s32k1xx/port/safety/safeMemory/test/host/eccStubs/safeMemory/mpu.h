/* SYS-41 host test stub of safeMemory/mpu.h for the ECC poll: the MPU is on and complete (MpuHostTest.cpp tests
 * the real header). */
#pragma once

#include <mcu/mcu.h> // like the real header (SafeMemory.cpp reads IP_MPU through it)

namespace safety
{
namespace mpu
{
inline bool isGlobalEnabled() { return true; }

inline bool hasRegionsOfDerivative() { return true; }
} // namespace mpu
} // namespace safety
