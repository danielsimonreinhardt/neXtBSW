/*
 * File: NvmSwc.h
 *
 * Synthetic component for the NVM-02 host test (tools/rtegen/test/nvm): a boot counter and a trim value in
 * non-volatile blocks.
 */

#ifndef NvmSwc_h_
#define NvmSwc_h_

#include "Rte_NvmSwc.h"

/* value of StoredCount seen by the init runnable (the stored boot count), and the count written back */
extern uint32 NvmSwc_StoredAtInit;
extern uint32 NvmSwc_BootCount;
/* last Trim read by Run10ms, and the request of the test: != 0 writes it to TrimOut */
extern uint16 NvmSwc_Trim;
extern uint16 NvmSwc_TrimRequest;

extern void NvmSwc_Init(void);
extern void NvmSwc_Run10ms(void);

#endif /* NvmSwc_h_ */
