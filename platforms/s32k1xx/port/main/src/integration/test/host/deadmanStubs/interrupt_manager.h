/* SYS-48 host test stub of interrupt_manager.h (OpenBSW bspInterruptsImpl): SYS_SetPriority/SYS_EnableIRQ write the
 * NVIC model of mcu/mcu.h, as the target functions forward to NVIC_SetPriority/NVIC_EnableIRQ. */

#pragma once

#include "mcu/mcu.h"

#include <cstdint>

inline void SYS_SetPriority(IRQn_Type const irq, uint8_t const priority) { nvicModel.level[irq] = priority; }

inline void SYS_EnableIRQ(IRQn_Type const irq) { nvicModel.enabled[irq] = true; }
