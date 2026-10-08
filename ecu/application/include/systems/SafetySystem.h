/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include <lifecycle/AsyncLifecycleComponent.h>
#include <lifecycle/ILifecycleManager.h>

#if PLATFORM_SUPPORT_IO
#ifndef NX_PRODUCT_BUILD // SYS-74: no test command "safety" in the product image
#include <console/AsyncCommandWrapper.h>
#include <safety/console/SafetyCommand.h>
#endif
#endif

namespace systems
{
class SafetySystem final
: public ::lifecycle::AsyncLifecycleComponent
, private ::async::IRunnable
{
public:
    explicit SafetySystem(
        ::async::ContextType context, ::lifecycle::ILifecycleManager& lifecycleManager);
    SafetySystem(SafetySystem const&)            = delete;
    SafetySystem& operator=(SafetySystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;
    void cyclic();

private:
    void execute() override;

private:
    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
#if PLATFORM_SUPPORT_IO
#if !defined(NX_PRODUCT_BUILD) && (!defined(NX_CONSOLE) || NX_CONSOLE) // SYS-74, PLAT-106: test command "safety"
    ::safety::SafetyCommand _safetyCommand;
    ::console::AsyncCommandWrapper _asyncCommandWrapperForSafetyCommand;
#endif
#endif
};

} // namespace systems
