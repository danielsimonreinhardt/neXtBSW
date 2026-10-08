/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "app/app.h"

#include <safeSupervisor/EtlAssert.h>

#include <etl/error_handler.h>

void app_main()
{
    // SYS-98 (finding STK-3 of doc/safety/sw-architecture.md, CX-3): a failed ETL_ASSERT is an S1 + S2 reaction of
    // the safe supervisor - outputs off, reset-loop mark, one SAFETY RESET line with the address of the assert
    // (addr2line against the ELF of the same build), reset. The explicit reset rather than the watchdog stays: the
    // watchdog is serviced from TASK_SAFETY, which keeps running when a lower priority task stops (#575).
    etl::set_assert_function(::safety::etlAssertFailed);
    ::app::run();
}
