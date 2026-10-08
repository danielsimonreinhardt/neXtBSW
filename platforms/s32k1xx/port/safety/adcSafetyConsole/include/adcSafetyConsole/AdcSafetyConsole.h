/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 ********************************************************************************/

#pragma once

#include <util/command/GroupCommand.h>

namespace safety
{

/**
 * Console group "safeadc": the ADC diagnostics of the SafeSystem (IO-16, made visible in IO-19).
 *
 * A failed ADC reading of the reference voltage or of an internal supply does not enter the safe state on its
 * own any more, it is counted (SafeSystem::AdcDiagnostics). Without this group the counters would only be
 * visible after a reset that no longer happens, so a permanently failing reading would look like silence.
 *
 * "reset" clears the counters (not the last reference voltage and not the series of failures in progress, which
 * belongs to the supervision, not to the statistics). Since SYS-37 the safety task clears them in its next cycle
 * (takeAdcDiagnosticsResetRequest below); the command itself writes nothing in the protected region.
 *
 * Why an own library and not a part of safeSystem: the application linker script collects the data of every
 * libsafe* archive into the MPU protected safety region and asserts that safety code has no preinitialized
 * data. A console command object has a vtable pointer, which the compiler initializes statically (.data). The
 * command is diagnosis, not safety code: it reads the counters through SafeSystem::getAdcDiagnostics and must
 * not live in the protected region.
 */
class AdcSafetyCommand : public ::util::command::GroupCommand
{
public:
    AdcSafetyCommand() = default;

protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

/**
 * Makes sure the group is part of the image. The command registers itself at the console registry during
 * static initialization; a library that nothing refers to would be dropped by the linker, so the safety
 * lifecycle calls this (empty) function once during its init.
 */
void registerAdcSafetyConsole();

/**
 * SYS-37: "reset" runs in the console task, and safeSystem lies in the MPU protected safety RAM - a direct
 * SafeSystem::resetAdcDiagnostics() from there is a bus fault. The command leaves a request in ordinary RAM (this
 * library is not a libsafe* archive), and the safety task carries it out in its next cycle.
 * \return true once per request; clears it (safety task only)
 */
bool takeAdcDiagnosticsResetRequest();

} // namespace safety
