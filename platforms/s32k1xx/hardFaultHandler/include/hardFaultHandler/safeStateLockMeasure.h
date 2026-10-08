/* SYS-172 board proof: duration of the interrupt lock of the safe state selection (hardFaultHandler/
 * safeStateSelection.h), measured per call site.
 *
 * Test builds only: the CMake option NX_SHORT_LOCK_MEASURE_COMMAND (platforms/s32k1xx/port/main/CMakeLists.txt,
 * default OFF, refused by a product build) compiles src/SafeOutputSelection.cpp with NX_SAFE_STATE_LOCK_MEASURE and
 * links the console group "shortlock" that reads these functions. Every other build has neither the measured lock
 * nor the functions.
 *
 * Measured are the cycles between taking and giving back the lock (DWT cycle counter of an ARMv7-M core; a core
 * without it counts 0), from the line after "cpsid i" to the line before "msr primask": the lock itself is a few
 * cycles longer (the save of PRIMASK, the probe pin and the bookkeeping). With a probe pin
 * (NX_SHORT_LOCK_PROBE_PIN) the pin is high while the outermost lock is held - the oscilloscope sees the whole lock.
 */

#ifndef HARD_FAULT_HANDLER_SAFE_STATE_LOCK_MEASURE_H
#define HARD_FAULT_HANDLER_SAFE_STATE_LOCK_MEASURE_H

#include <stdint.h>

/* Call sites of the lock */
#define NX_SAFE_STATE_LOCK_SITE_SELECT     0U /* nxSafeStateSelect */
#define NX_SAFE_STATE_LOCK_SITE_BLOCK      1U /* nxSafeStateBlockShort */
#define NX_SAFE_STATE_LOCK_SITE_TEST_BEGIN 2U /* nxSafeStateShortTestBegin */
#define NX_SAFE_STATE_LOCK_SITE_TEST_END   3U /* nxSafeStateShortTestEnd */
#define NX_SAFE_STATE_LOCK_SITE_COUNT      4U

typedef struct
{
    uint32_t count;      /* locks taken at this site since start or reset */
    uint32_t lastCycles; /* core cycles of the last one */
    uint32_t maxCycles;  /* core cycles of the longest one */
} NxSafeStateLockStat;

#ifdef __cplusplus
extern "C"
{
#endif

/** Copies the figures of `site` (NX_SAFE_STATE_LOCK_SITE_*); 0, or -1 for an unknown site. */
int32_t nxSafeStateLockStatRead(uint32_t site, NxSafeStateLockStat* stat);

/** Clears the figures of every site. */
void nxSafeStateLockStatReset(void);

/** 1 if the cycle counter runs (switched on if needed), 0 if the core has none. */
uint32_t nxSafeStateLockCycleCounter(void);

#ifdef __cplusplus
}
#endif

#endif /* HARD_FAULT_HANDLER_SAFE_STATE_LOCK_MEASURE_H */
