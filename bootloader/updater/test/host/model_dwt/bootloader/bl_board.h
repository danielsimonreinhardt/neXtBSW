#pragma once

/* Stand-in for the generated bl_board.h in the host test of upd_micros.c (PLAT-86): only the counter clock. The test
 * builds one DLL per profile with -DUPD_TEST_CORE_HZ=... (default: FIRC 48 MHz, the reset clock of the S32K1). */

#ifndef UPD_TEST_CORE_HZ
#define UPD_TEST_CORE_HZ 48000000UL
#endif
#define BL_CORE_CLOCK_HZ UPD_TEST_CORE_HZ
