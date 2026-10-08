/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "reset/softwareSystemReset.h"

#include <safeMemory/IntegrityEccHandler.h>
#include <safeSupervisor/SafeSupervisor.h>

extern "C"
{
// SYS-98 (finding STK-4 of doc/safety/sw-architecture.md): uncorrectable double bit ECC fault was detected in SRAM. Up to SYS-98 the
// handler printed through printf (stand-alone printf, charIo, UART driver - 1032 B on the interrupt stack at level 0)
// and spun until the watchdog reset, with the power outputs still on. Now: the sources are taken (read and cleared,
// as the poll of the safety task does), and the supervisor runs the S1 + S2 reaction of the ECC event without a log
// line - reportEccError() is const, so the closed protected RAM of a SAFE_ISR handler is no obstacle.
void ERM_double_fault_IRQHandler()
{
    ::safety::safe_memory::EccErrors const ecc = ::safety::safe_memory::takeEccErrors();
    ::safety::SafeSupervisor::getInstance().reportEccError(ecc.sources);
    // Not reached: the reaction ends in the reset. Should it ever come back, reset anyway (softwareSystemReset()
    // does not return, so no loop behind it - cppcheck unreachableCode).
    softwareSystemReset();
}

} // extern "C"
