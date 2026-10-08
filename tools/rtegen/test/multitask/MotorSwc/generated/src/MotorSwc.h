/*
 * File: MotorSwc.h
 *
 * Synthetic component for the RTE-02 host test (tools/rtegen/test/multitask).
 */

#ifndef MotorSwc_h_
#define MotorSwc_h_

#include "Rte_MotorSwc.h"

extern uint32 MotorSwc_Cycles;
extern uint16 MotorSwc_SetpointSeen;
extern boolean MotorSwc_EnableSeen;
extern uint16 MotorSwc_DiagSetpoint;

/* test harness */
extern void Test_Hook(uint8 where);
extern void Test_Trace(uint8 id);

#endif /* MotorSwc_h_ */
