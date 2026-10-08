#pragma once

/**
 * SYS-84: start-up line of the s32k1xx family port (platforms/s32k1xx/port/main, main()) with the reset status of the
 * start instead of the RCM_SRS of the most recent reset, which after the watchdog fast test always says WDOG. The
 * value comes from the capture before main() (src/mcuResetReason/McuResetReason.cpp), the text from
 * rcm::formatStartLine() (RcmResetReason.h, host test rcm-start-line).
 */
namespace rcm
{
/** Writes "main(RCM::SRS ...)" and the line end to stdout (the console of the start-up). */
void printStartLine();
} // namespace rcm
