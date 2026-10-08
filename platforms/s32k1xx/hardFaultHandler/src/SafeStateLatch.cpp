/* SYS-36: the latch of the safe state S1 (hardFaultHandler/safeOutputs.h). One definition for the whole image,
 * next to the weak table of the safe outputs, because every user of the latch - the safe state (safeIo) and the
 * integration of the family port - already links this library. Zero initialized: a reset clears it. */

#include "hardFaultHandler/safeOutputs.h"

extern "C"
{
volatile uint32_t nxSafeStateLatch;
volatile uint32_t nxSafeStateEntries; // SYS-35: entries into S1 (hold of the integration, PWM inhibit)
}
