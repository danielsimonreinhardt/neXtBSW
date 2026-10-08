/*
 * File: ServiceSwc.h
 *
 * Synthetic component for the RTE-05 host test (tools/rtegen/test/bsw_services): reads the BSW service ports.
 */

#ifndef ServiceSwc_h_
#define ServiceSwc_h_

#include "Rte_ServiceSwc.h"

/* last values read by ServiceSwc_Run10ms (task app) */
extern uint8 ServiceSwc_PowerState;
extern boolean ServiceSwc_PowerOk;
extern uint16 ServiceSwc_SupplyMv;
extern uint8 ServiceSwc_PowerQlf;
extern uint16 ServiceSwc_ResetReason;
extern boolean ServiceSwc_ResetWatchdog;
extern boolean ServiceSwc_ResetPowerOn;
extern uint32 ServiceSwc_ResetQlf;
extern uint32 ServiceSwc_TimeMs;
extern uint32 ServiceSwc_TimeUs;
extern uint32 ServiceSwc_TimeUsAgain; /* TimeUs read a second time in the same call */
/* last values read by ServiceSwc_Run1ms (task motor) */
extern uint32 ServiceSwc_FastUs;
extern uint8 ServiceSwc_FastReason;

/* test harness: called between the two reads of TimeUs */
extern void Test_Hook(uint8 where);

#endif /* ServiceSwc_h_ */
