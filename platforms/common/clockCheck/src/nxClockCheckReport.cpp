/********************************************************************************
 * Copyright (c) 2026 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

// PLAT-72: start-up report of the clock check (include/clock/nxClockCheckReport.h), C linkage.

#include "clock/nxClockCheckReport.h"

#include <cstdio>

extern "C"
{
char const* nxClockCheckFaultText(uint32_t const fault)
{
    switch (fault)
    {
        case NX_CLOCK_CHECK_OK: return "ok";
        case NX_CLOCK_CHECK_OSC_NOT_READY: return "oscillator not ready";
        case NX_CLOCK_CHECK_PLL_NOT_LOCKED: return "PLL not locked";
        case NX_CLOCK_CHECK_SOURCE_MISMATCH: return "clock source or dividers differ from the plan";
        default: return "unknown fault";
    }
}

char const* nxClockSourceText(uint32_t const source, uint32_t const pllInput)
{
    switch (source)
    {
        case NX_CLOCK_SOURCE_INTERNAL_SLOW: return "internal slow RC";
        case NX_CLOCK_SOURCE_INTERNAL_FAST: return "internal fast RC";
        case NX_CLOCK_SOURCE_EXTERNAL: return "external oscillator";
        case NX_CLOCK_SOURCE_PLL:
            switch (pllInput)
            {
                case NX_CLOCK_SOURCE_EXTERNAL: return "PLL on external oscillator";
                case NX_CLOCK_SOURCE_INTERNAL_FAST: return "PLL on internal RC";
                default: return "PLL";
            }
        default: return "unknown";
    }
}

int nxClockCheckFormat(nxClockCheckResult const* const result, char* const buffer, size_t const size)
{
    bool const monitorEvent   = (result->monitor != NX_CLOCK_CHECK_MONITOR_NONE);
    char const* const monitor = monitorEvent ? ", clock monitor event" : "";
    char const* const source  = nxClockSourceText(result->source, result->pllInput);
    if (result->fault == NX_CLOCK_CHECK_OK)
    {
        return snprintf(
            buffer,
            size,
            "clock: %s, core %lu Hz, source %s%s",
            monitorEvent ? "WARNING" : "ok",
            static_cast<unsigned long>(result->actualCoreHz),
            source,
            monitor);
    }
    return snprintf(
        buffer,
        size,
        "clock: FAULT %s - core %lu Hz instead of %lu Hz, source %s%s (regs 0x%08lx 0x%08lx 0x%08lx)",
        nxClockCheckFaultText(result->fault),
        static_cast<unsigned long>(result->actualCoreHz),
        static_cast<unsigned long>(result->plannedCoreHz),
        source,
        monitor,
        static_cast<unsigned long>(result->registers[0]),
        static_cast<unsigned long>(result->registers[1]),
        static_cast<unsigned long>(result->registers[2]));
}

int nxClockCheckReport(nxClockCheckResult* const result)
{
    nxClockCheckResult check;
    char line[256]; // the longest line (every field at its maximum) has 200 characters
    nxClockCheck(&check);
    (void)nxClockCheckFormat(&check, line, sizeof(line));
    printf("%s\r\n", line);
    if (result != nullptr)
    {
        *result = check;
    }
    return ((check.fault != NX_CLOCK_CHECK_OK) || (check.monitor != NX_CLOCK_CHECK_MONITOR_NONE)) ? 1 : 0;
}
} // extern "C"
