/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "console/console.h"

#include <console/AsyncConsole.h>
#include <console/InterleavedConsoleInput.h>

namespace console
{
bool enableStdioConsole = false;
bool enableShmemConsole = false;

// SYS-15: instead of OpenBSW StdioConsoleInput - same keys and answers, but the logger takes the input line off the
// terminal before each log line and draws it again afterwards (console/InputLine.h, libs/bsw/consoleContext).
::console::InterleavedConsoleInput stdioConsoleInput(" ", "\r\n");
::console::AsyncConsole asyncConsole;

IOutputInterleave& consoleOutputInterleave() { return stdioConsoleInput.outputInterleave(); }

void enable() { enableStdioConsole = true; }

void disable() { enableStdioConsole = false; }

void init()
{
    stdioConsoleInput.init(
        InterleavedConsoleInput::OnLineReceived::create<AsyncConsole, &AsyncConsole::onLineReceived>(
            asyncConsole));
}

void run()
{
    if (enableStdioConsole)
    {
        stdioConsoleInput.run();
    }
}

} // namespace console
