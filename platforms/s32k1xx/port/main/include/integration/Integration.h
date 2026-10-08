#pragma once

#include <async/Types.h>
#include <ecuState/IPowerSupply.h>
#include <ecuState/IShutdownStep.h>

namespace integration
{

/**
 * Hooks of the modules with the role early_init, called first in main(): before memory protection,
 * the MCU watchdog fast test and the BSP. Empty if the project uses no such module.
 */
void earlyInit();

/** Hooks called after the MCU watchdog is enabled, right before the BSP initialization. */
void preBspInit();

/**
 * Provider of the role power_supply for the ECU state management (SYS-01), chosen from the modules of the
 * project (integration/moduleConfiguration.h): the UJA113x if the project uses one, otherwise a permanent
 * supply that refuses every sleep request. Valid after platformLifecycleAdd(level 1).
 */
::ecustate::IPowerSupply& powerSupply();

/** Task context the power supply has to be called from (the task of its provider). */
::async::ContextType powerSupplyContext();

/**
 * Providers of the run-down steps before the sleep (SYS-02): the CAN buses of the project are logged off by
 * systems::CanShutdownStep; the NvM step ShutdownStep::NVM is provided by the application through nvmShutdownStep()
 * (NVM-07) and reported as "not present" if it provides none. Valid after platformLifecycleAdd(level 2) (the CAN
 * system is created there); the run-down only runs in operation, long after that.
 */
::ecustate::ShutdownSequence const& shutdownSequence();

/**
 * NVM-07: provider of ShutdownStep::NVM - the NvM of the application writes its changed blocks in the run-down,
 * while application, RTE and CAN are still alive (before, the blocks were written when the lifecycle was taken down,
 * after the run-down). Weak default in Integration.cpp: nullptr, the step is "not present". The application
 * (ecu/application, NvM of the RTE) defines it; it is called during static initialisation and must only return the
 * address of an object with static storage, without using it.
 */
::ecustate::IShutdownStep* nvmShutdownStep();

#ifdef NX_FLEXNVM_PARTITION_COMMAND
/**
 * NVM-17 (test build, console "csec"): how long the core may block with interrupts suspended before a watchdog
 * OUTSIDE the MCU resets it, ms. 0xFFFFFFFF if the project has none or it does not run (a board without SBC: only
 * the MCU's own watchdog, which the CSEc module keeps quiet itself), 0 if its state is not known (yet). The MCU
 * watchdog is not part of this value.
 */
uint32_t externalWatchdogAllowanceMs();
#endif

} // namespace integration
