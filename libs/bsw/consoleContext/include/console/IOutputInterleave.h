// SPDX-License-Identifier: Apache-2.0
#pragma once

/* SYS-15: output that shares the console terminal with the input line of the console.
 *
 * The console echoes every key right away, and the logger prints its entries in the same context (TASK_IDLE, one
 * entry per run of the idle handler). Without coordination an echoed character ends up at the start of the next
 * log line ("s170158: RefApp: ...", board 2026-09-18). A writer of whole lines (the logger) therefore calls
 * beginOutput() before each line and outputIdle() when it has nothing to print; the console input
 * (console/InputLine.h) takes its line off the terminal and draws it again below. Both calls come from the context
 * the console input runs in, so the implementation needs no lock.
 */

namespace console
{

class IOutputInterleave
{
public:
    IOutputInterleave(IOutputInterleave const&)            = delete;
    IOutputInterleave& operator=(IOutputInterleave const&) = delete;

    /** A line of other output follows: take the input line (prompt and typed text) off the terminal. */
    virtual void beginOutput() = 0;

    /** No other output is pending: draw the input line again if it has text and is not on the terminal. */
    virtual void outputIdle() = 0;

protected:
    IOutputInterleave()  = default;
    ~IOutputInterleave() = default;
};

/**
 * The input line of the application's console; defined next to the console input (the OpenBSW application:
 * ecu/application/src/console/console.cpp), used by the logger. Valid from static initialisation on.
 */
IOutputInterleave& consoleOutputInterleave();

} // namespace console
