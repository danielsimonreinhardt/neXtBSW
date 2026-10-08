/*
 * File: TypeSink.h
 *
 * Synthetic component for the RTE-04 host test (tools/rtegen/test/types): receiver of the record and the array of
 * the Simulink test model TypeSwc in another task.
 */

#ifndef TypeSink_h_
#define TypeSink_h_

#include "Rte_TypeSink.h"

extern TypeSwc_StatBus TypeSink_Stat; /* last Stat read by Sink20ms */
extern uint16 TypeSink_Scaled[4];     /* last Scaled read by Sink20ms */
extern uint32 TypeSink_Calls10;       /* calls of Sink10ms */

#endif /* TypeSink_h_ */
