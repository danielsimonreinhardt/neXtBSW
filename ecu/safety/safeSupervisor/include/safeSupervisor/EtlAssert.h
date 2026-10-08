/* SYS-98 (finding STK-3 of doc/safety/sw-architecture.md, SYS-83, CX-3 of doc/safety/coexistence.md): the handler
 * for a failed ETL_ASSERT, to be registered with etl::set_assert_function() before anything else runs (ETL profile:
 * ETL_USE_ASSERT_FUNCTION).
 *
 * Up to SYS-98 the application's handler (ecu/application/src/main.cpp, etl_assert_function) formatted its line with
 * etl::print on the terminal UART and reset: a 200 B frame plus the format parser and the UART driver - which can
 * assert themselves - on whatever stack the failed assert ran on, reachable from every context (the async runtime
 * hooks and every singleton check the ETL asserts in), and no safe state before the reset. This handler is the
 * S1 + S2 reaction of the supervisor instead (SafeSupervisor::reportEtlAssert): outputs off, reset-loop mark, one
 * fixed console line with the address through safeConsoleWrite(), reset. Nothing on that path formats with printf or
 * etl::print, logs, calls the RTOS or asserts, so it cannot re-enter itself.
 *
 * Kernel and family neutral; the family port only supplies safeConsoleWrite()/safeConsoleFlush() and the reset.
 * Defined in SafeSupervisor.cpp (unit C-SUP); a header of its own, so that only the registering code needs the ETL
 * exception type. */

#pragma once

#include <etl/exception.h>

namespace safety
{
/**
 * Handler for etl::set_assert_function(). ETL_MINIMAL_ERRORS leaves the exception without text, file and line; the
 * console line names the return address instead ("SAFETY RESET (ETL_ASSERT, at 0x...): outputs off"), which
 * arm-none-eabi-addr2line -f -e <elf> resolves to the ETL_ASSERT call site. Does not return on the target.
 */
void etlAssertFailed(::etl::exception const& exception);
} // namespace safety
