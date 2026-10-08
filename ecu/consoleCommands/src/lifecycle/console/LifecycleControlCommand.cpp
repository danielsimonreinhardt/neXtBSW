/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "lifecycle/console/LifecycleControlCommand.h"

#include "consoleCommands/CheckedNumber.h" // SYS-163

#include <async/Async.h>
#include <bl_interface.h>
#include <reset/softwareSystemReset.h>
#include <lifecycle/ILifecycleManager.h>

#include <etl/error_handler.h>

namespace
{
enum Id
{
    ID_REBOOT,
    ID_POWEROFF,
    GO_TO_LEVEL,
    ID_UDEF,
    ID_PABT,
    ID_DABT,
    ID_ASSERT,
    ID_BOOTLOADER
};

}

namespace lifecycle
{
DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(LifecycleControlCommand, "lc", "lifecycle command")
// SYS-74: the product image (NX_PRODUCT_BUILD, cmake/NxProductBuild.cmake) keeps only "bootloader". udef, pabt,
// dabt and assert inject faults; reboot, poweroff and level run the lifecycle shut-down, which cancels the safety
// cycle and disables the MCU watchdog without the safe state (doc/safety/coexistence.md CX-2).
#ifndef NX_PRODUCT_BUILD
COMMAND_GROUP_COMMAND(ID_REBOOT, "reboot", "reboot the system")
COMMAND_GROUP_COMMAND(ID_POWEROFF, "poweroff", "poweroff the system")
COMMAND_GROUP_COMMAND(GO_TO_LEVEL, "level", "switch to level")
COMMAND_GROUP_COMMAND(ID_UDEF, "udef", "forces an undefined instruction exception")
COMMAND_GROUP_COMMAND(ID_PABT, "pabt", "forces a prefetch abort exception")
COMMAND_GROUP_COMMAND(ID_DABT, "dabt", "forces a data abort exception")
COMMAND_GROUP_COMMAND(ID_ASSERT, "assert", "forces an assert")
#endif
COMMAND_GROUP_COMMAND(ID_BOOTLOADER, "bootloader", "reset into the CAN bootloader")
DEFINE_COMMAND_GROUP_GET_INFO_END

LifecycleControlCommand::LifecycleControlCommand(ILifecycleManager& lifecycleManager)
: _lifecycleManager(lifecycleManager)
{}

void LifecycleControlCommand::executeCommand(::util::command::CommandContext& context, uint8_t idx)
{
#ifdef NX_PRODUCT_BUILD
    (void)context;
#endif
    switch (idx)
    {
#ifndef NX_PRODUCT_BUILD
        case ID_REBOOT:
        {
            _lifecycleManager.transitionToLevel(0);
            break;
        }
        case ID_POWEROFF:
        {
            _lifecycleManager.transitionToLevel(0);
            break;
        }
        case GO_TO_LEVEL:
        {
            // SYS-163 (SR-110-2 a): only a complete line with a level in range runs a transition. Before, a missing
            // or invalid number ("lc level", "lc level x") and 256 (uint8_t wrap-around of scanIntToken) ran the
            // lifecycle down to level 0 - complete shut-down without the safe state (CX-2) - while the console
            // answered "error".
            uint32_t level = 0U;
            if (!::consolecommands::scanUnsigned(context, _lifecycleManager.getLevelCount(), level)
                || !context.checkEol())
            {
                return;
            }
            _lifecycleManager.transitionToLevel(static_cast<uint8_t>(level));
            break;
        }
#endif
        case ID_BOOTLOADER:
        {
            bl_set_boot_request();
            softwareSystemReset();
        }
#ifndef NX_PRODUCT_BUILD
        case ID_UDEF:
        {
            break;
        }
        case ID_PABT:
        {
            break;
        }
        case ID_DABT:
        {
            break;
        }
        case ID_ASSERT:
        {
            ETL_ASSERT_FAIL(ETL_ERROR_GENERIC("requested assert"));
            break;
        }
#endif
        default:
        {
            break;
        }
    }
}

} // namespace lifecycle
