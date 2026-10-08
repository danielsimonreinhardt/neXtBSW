#pragma once

/* PLAT-110 (safety review 2026-10-01, SR-150-1 b): the OpenBSW console group "pwm" with complete argument checks.
 *
 * Audit of the OpenBSW testers with a number argument (doc/architecture/openbsw-console-number-arguments.md), two
 * findings in bios::OutputPwmTester (OutputPwmTester.cpp at 3b6e702):
 *
 *   P1  "setPeriod <channel> <period>": a channel out of range prints "nOk wrong channel" but does not return -
 *       OutputPwm::setPeriod() is called all the same, with the channel narrowed to its uint16_t parameter, so
 *       65536 + k reaches channel k (the same kind of alias as `out set 259 1`, SYS-150). Harmless in this family
 *       port today (ComplementaryPwm::setPeriod() answers BSP_NOT_SUPPORTED, the static dummy channel does
 *       nothing), but a future PWM client with a period would be reached past the check.
 *   P2  "set <channel> <duty>": the scan results are not checked. A missing or invalid duty ("pwm set 1",
 *       "pwm set 1 x") scans as 0 and OutputPwm::setDuty(channel, 0) runs although the console answers "error";
 *       an invalid channel ("pwm set x 5000") scans as 0 and reaches channel 0. In this family port the first case
 *       unmasks a complementary pair of the power stage at duty 0 (ComplementaryPwm::setDuty: high side off, low
 *       side on) - a write nobody asked for.
 *
 * This wrapper is the OpenBSW tester with one change: "set" and "setPeriod" run only for a complete, valid line -
 * two numbers read with the same OpenBSW scanner on the same arguments (decimal, 0x hex, leading-0 octal, sign) and
 * nothing after them - and a channel below OutputPwm::outputPwmNumberAll (or 0xFF, "all", for "set"). Otherwise
 * nothing is called: BAD_TOKEN / UNEXPECTED_TOKEN for the line (as the OpenBSW scanner reports them), BAD_VALUE for
 * a channel out of range (OpenBSW: "set" answers nothing and OK, "setPeriod" calls the driver), no text. Every valid
 * line, "all" and the clamping of duty and period stay exactly as in OpenBSW.
 *
 * SYS-162 (safety review group A 2026-10-01, SR-110-1 b): finding PLAT-110 - `pwm set <pair of MOT1> <duty>` wrote
 * duties of the power stage past the motor control (the function that owns the group), in every build. Like "out"
 * for the safe_off outputs (SafeOffOutputTester, SYS-150), "set" and "setPeriod" refuse every channel of a PWM group
 * of the safe outputs table (bsp/safeOutputs/safeOutputsConfiguration.h: its first block, NX_SAFE_OUTPUT_COUNT -
 * NX_SAFE_OFF_OUTPUT_COUNT entries), and "set 0xFF" (every channel) as soon as there is one. Line:
 * `<channel> : <name> -> refused: channel of a PWM group of the safe outputs table, driven by its function only`,
 * result BAD_VALUE ("error"), OutputPwm is not called. Which channels: tools/bswgen generates the OutputPwm channels
 * after OpenBSW's static ones only for the pairs of the PWM groups the project uses (ftmPwmConfiguration.h:
 * PAIR_COUNT == _pwmChannelMax - _pwmDynamicStart), and puts every used PWM group into the safe outputs table
 * (collect_safe_outputs) - so the channels of these groups are exactly the generated channels. A static_assert in the
 * source stops the build if a project ever has generated PWM channels but no PWM group in the safe outputs table;
 * a mapping per group comes with a generator that knows PWM channels outside the table. The static channels
 * (dummyPwm) and "all" stay as they were.
 *
 * SR-110-1 c: the command is not part of the product image (NX_PRODUCT_BUILD, cmake/NxProductBuild.cmake; BspSystem
 * leaves it out, tools/elfcheck product finds it in an image). */

#include "outputPwm/OutputPwmTester.h"

#include <cstdint>

namespace systems
{

class CheckedOutputPwmTester : public ::bios::OutputPwmTester
{
public:
    /** Sub command "set" of bios::OutputPwmTester (COMMAND_GROUP_COMMAND(2, "set", ...) in OutputPwmTester.cpp). */
    static constexpr uint8_t SET_COMMAND = 2U;
    /** Sub command "setPeriod" (COMMAND_GROUP_COMMAND(3, "setPeriod", ...)). */
    static constexpr uint8_t SET_PERIOD_COMMAND = 3U;
    /** Channel number of "set" for every channel. */
    static constexpr uint32_t ALL_CHANNELS = 0xFFU;

    /** SYS-162: true for a channel of a PWM group of the safe outputs table (see above). */
    static bool isSafetyPwmChannel(uint32_t channel);

    CheckedOutputPwmTester() = default;

protected:
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

} // namespace systems
