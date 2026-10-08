#pragma once

/* SYS-150 (safety review night 2026-10-01, SR-147-2 a): the OpenBSW console group "out" without a way around the
 * drivers of the safe_off outputs.
 *
 * Finding SYS-147: `out set <id> 1` (bios::OutputTester, in every build including the product build) wrote any
 * digital output through bios::Output::set - also MOT1_GD_DRV_EN and EN_SW_KL30B - past every rule of their drivers
 * (phase rule and latched faults of the gate driver, switch-on lock of S1, the state machine of the high-side switch).
 *
 * This wrapper is the OpenBSW tester with one change: "set" refuses every safe_off output of the project
 * (integration/SafeOffOutputs.h, derived from the generated safe outputs table - no list by hand), for both values.
 * A 1 would bypass the driver; a 0 would pull the pin behind the driver's back, which the driver then reports as a
 * fault of its own (DRV_EN mismatch). Such an output is switched through its driver's own commands (`gd enable` /
 * `gd disable`, `kl30b on` / `kl30b off`, each with its rules). The output id is read exactly as OpenBSW reads it
 * (CommandContext::scanIntToken<uint32_t> on the same arguments: decimal, 0x hex, leading-0 octal, sign), so no
 * spelling of a number reaches Output::set past the check. "all", "get" and every other output stay as they were.
 *
 * Found while testing: OpenBSW checks the range of the id only after the cast to Output::OutputId, and that enum is one
 * byte on the target (AAPCS bare-metal short enums, arm-none-eabi-gcc: sizeof 1, the cast is a uxtb). `out set 259 1`
 * therefore wrote output 3, `out get 259` read it - any id plus a multiple of 256 reached every output, past a check
 * on the id alone. "set" and "get" with an id at or above TOTAL_NUMBER_OF_OUTPUTS are refused here on the number
 * itself, with the answer OpenBSW gives for an id out of range (no text, BAD_VALUE: "error"). */

#include "outputManager/OutputTester.h"

#include <cstdint>

namespace systems
{

class SafeOffOutputTester : public ::bios::OutputTester
{
public:
    /** Sub command "set" of bios::OutputTester (COMMAND_GROUP_COMMAND(2U, "set", ...) in OutputTester.cpp). */
    static constexpr uint8_t SET_COMMAND = 2U;
    /** Sub command "get" (COMMAND_GROUP_COMMAND(3U, "get", ...)). */
    static constexpr uint8_t GET_COMMAND = 3U;

    SafeOffOutputTester() = default;

protected:
#ifdef NX_PRODUCT_BUILD
    /* SYS-119: the product image lists "all" and "get" only - "set" writes any digital output past the function that
     * drives it (like "pwm", SYS-162) and is refused in executeCommand() as well. */
    DECLARE_COMMAND_GROUP_GET_INFO
#endif
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

} // namespace systems
