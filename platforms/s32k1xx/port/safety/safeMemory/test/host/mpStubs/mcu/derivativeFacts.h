/* SYS-171: the two MPU facts of a generated mcu/derivativeFacts.h (tools/bswgen/templates/derivativeFacts.h.j2) for
 * MemoryProtectionHostTest.cpp. Default: 8 region descriptors (eval board S32K144); the tools/test entry
 * safe-memory-mpu-regions-16 builds the same test with -DNX_MPU_REGIONS=16 -DNX_MPU_CESR_NRGD=2 (S32K148). */
#pragma once

#ifndef NX_MPU_REGIONS
#define NX_MPU_REGIONS   8
#define NX_MPU_CESR_NRGD 0
#endif
