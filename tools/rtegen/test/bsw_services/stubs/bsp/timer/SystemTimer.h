// Host replacement of the OpenBSW system timer (libs/bsw/bsp/include/bsp/timer/SystemTimer.h) for the RTE-05 host
// test: only the call the BSW service hand-over uses, same C linkage and signature. The test defines it.

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

uint64_t getSystemTimeUs64Bit(void);

#ifdef __cplusplus
}
#endif
