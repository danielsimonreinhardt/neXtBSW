// Console command "csec" (NVM-17): the CSEc way back to the factory state (platforms/s32k1xx/bsp/bspCsec,
// CsecRecovery) for the board run of NVM-07. TEST BUILDS ONLY (CMake option NX_FLEXNVM_PARTITION_COMMAND, the build
// that can also write the partition with its key space): never in the standard or the product build (SYS-74, checked
// on the ELF by tools/elfcheck product). User at the board only.
//
//   csec status                         CSEc, FlexRAM, FCSESTAT, watchdogs, staged M1..M3, journal of the last command
//   csec m1 <32 hex> / m2 <64 hex> / m3 <32 hex>   stage M1..M3 (a console line holds 127 characters, M1..M3 with
//                                       the word are 166 - tools/csec prints exactly these lines)
//   csec loadkey LOADKEY-MASTER-ECU-KEY CMD_LOAD_KEY of the MASTER_ECU_KEY into its empty slot, answers M4/M5
//   csec rng                            arms CMD_INIT_RNG for the next start and resets (INIT_RNG blocks for several
//                                       hundred ms: at start-up, before the safety task, csecInitRngAtStart())
//   csec challenge                      CMD_GET_ID, CMD_DBG_CHAL: UID, CHALLENGE and the word for dbgauth
//   csec dbgauth <32 hex> DBGAUTH-XXXXXXXX   CMD_DBG_AUTH: ERASES ALL KEYS AND THE PARTITION; the safety task's period
//                                       check resets the ECU afterwards (the journal shows the result after the reset)
//
// Every command that launches one checks first that the watchdog outside the MCU (SBC) allows its budget
// (integration::externalWatchdogAllowanceMs(), CsecRecovery::budgetMs()); the MCU watchdog is kept quiet by the CSEc
// module itself. Runs in the console context.

#pragma once

#include "csec/CsecRecovery.h"

#include <util/command/GroupCommand.h>

#include <cstdint>

namespace systems
{

class CsecCommand : public ::util::command::GroupCommand
{
public:
    CsecCommand();

protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

private:
    ::platform::CsecRecovery _csec;
    uint8_t _m1[16];
    uint8_t _m2[32];
    uint8_t _m3[16];
    uint8_t _staged; // bit 0 M1, bit 1 M2, bit 2 M3
};

/**
 * NVM-17: hook before the BSP initialization (integration::preBspInit). Runs CMD_INIT_RNG if "csec rng" armed it
 * before the reset - the request is cleared first, so a reset during the command does not repeat it. Nothing
 * otherwise.
 */
void csecInitRngAtStart();

} // namespace systems
