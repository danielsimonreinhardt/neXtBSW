// Host replacement: no interrupts in the single-threaded host simulation.

#pragma once

#include <cstdint>

typedef uint32_t OldIntEnabledStatusValueType;

inline uint32_t getMachineStateRegisterValueAndSuspendAllInterrupts(void) { return 0U; }

inline OldIntEnabledStatusValueType getOldIntEnabledStatusValueAndSuspendAllInterrupts()
{
    return 0U;
}

inline void resumeAllInterrupts(uint32_t /* oldMachineStateRegisterValue */) {}
