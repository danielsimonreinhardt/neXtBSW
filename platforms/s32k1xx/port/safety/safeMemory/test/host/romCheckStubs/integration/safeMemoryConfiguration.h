/* Test profile of RomCheckHostTest (SYS-44) in place of the generated integration/safeMemoryConfiguration.h: a period
 * other than the module default (60000 ms), so the test shows that RomCheck::idle() takes the generated value. */

#pragma once

#define NX_MODULE_SAFEMEMORY_PARAM_ROM_CHECK_PERIOD_MS 2500U // ms
