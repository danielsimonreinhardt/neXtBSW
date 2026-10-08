/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "systems/SysAdminSystem.h"

#include "LifecycleConsole.h"

#include <console/ConsoleContext.h>
#include <lifecycle/ILifecycleManager.h>

#include <etl/alignment.h>

namespace
{
constexpr uint32_t SYSTEM_CYCLE_TIME = 10;

struct RunInOwner
{
    template<class Work>
    void operator()(::async::ContextType const context, Work& work) const
    {
        ::console::runInOwnerContext(context, work);
    }
};

using ConsoleLifecycleManager = ::systems::LifecycleConsole<
    ::lifecycle::ILifecycleManager,
    ::lifecycle::ILifecycleListener,
    ::async::ContextType,
    RunInOwner>;

/**
 * SYS-13: the lifecycle manager as the "lc" command sees it (LifecycleConsole.h). One SysAdminSystem per ECU
 * (app.cpp), so one proxy; its constructor runs in the constructor of the system.
 */
::etl::typed_storage<ConsoleLifecycleManager> consoleLifecycleManager;
} // namespace

namespace systems
{

SysAdminSystem::SysAdminSystem(
    ::async::ContextType const context, ::lifecycle::ILifecycleManager& lifecycleManager)
: _context(context)
, _timeout()
// SYS-13: the command runs in the console context; a level change is requested in this system's task, the task
// of the lifecycle manager (before, the whole command ran there, above task can, and held both at the UART)
#if !defined(NX_CONSOLE) || NX_CONSOLE
, _lifecycleControlCommand(consoleLifecycleManager.create(lifecycleManager, context))
, _asyncCommandWrapperForLifecycleControlCommand(_lifecycleControlCommand, ::console::CONSOLE_CONTEXT)
#endif
{
#if defined(NX_CONSOLE) && !NX_CONSOLE
    (void)lifecycleManager; // only the console command "lc" requests level changes
#endif
    setTransitionContext(context);
}

void SysAdminSystem::init() { transitionDone(); }

void SysAdminSystem::run()
{
    ::async::scheduleAtFixedRate(
        _context, *this, _timeout, SYSTEM_CYCLE_TIME, ::async::TimeUnit::MILLISECONDS);

    transitionDone();
}

void SysAdminSystem::shutdown()
{
    _timeout.cancel();

    transitionDone();
}

void SysAdminSystem::execute()
{
    //
}

} // namespace systems
