#pragma once

/* SYS-150 / SYS-151 (safety review night 2026-10-01, SR-147-2 a and SR-147-3 b): the safe_off outputs of the project
 * as digital outputs of the OpenBSW output manager, and the level their drivers command.
 *
 * Which outputs: the second block of the generated safe outputs table (bsp/safeOutputs/safeOutputsConfiguration.h,
 * tools/bswgen collect_safe_outputs) - every output a module connection declares safe_off (module.yaml
 * connect.<name>.safe_off, e.g. DRV_EN of a gate driver, IN of a high-side switch). tools/bswgen puts the PWM groups
 * first and the safe_off outputs after them (NX_SAFE_OFF_OUTPUT_COUNT of them), and names each safe_off entry after
 * its board entry exactly as it names the digital output (modules.identifier), so the entry name is the enumerator of
 * ::bios::Output::OutputId. Nothing new is configured: the list is derived from the same generat as the table walk.
 *
 * The name is resolved at compile time without knowing which entries are PWM groups: every entry name is declared
 * once as a fallback enumerator (-1) of an enclosing class, and looked up again inside a class derived from
 * ::bios::Output. Unqualified lookup searches the class and its base before the enclosing class, so a name that is an
 * output resolves to its OutputId and any other name (a PWM group) to -1. Only the safe_off block is used; a
 * static_assert stops the build if one of its names is no digital output of the project. A PWM group that happens to
 * share its name with an ordinary output does not count: only the position in the table does.
 *
 * Commanded level (SYS-151): the drivers of these outputs write them through setCommandedOutput() below (the binder
 * of the generated module binding, FamilyBinders::set in ModuleBinders.h, and the DRV_EN of the TMC6200,
 * BiosTmc6200Io in systems/GateDriverSystem.h). It records the level first and writes the pin second, so a reader
 * that sees the pin active and then the record inactive knows that the active level was not written by the driver.
 * Every other writer of the pin - the OpenBSW console `out set` (refused for these outputs since SYS-150), a debugger,
 * a stray store to the port - leaves the record as it was, and the hold of the integration (SafeOffCommandHold.h)
 * takes such a level back within one poll. Single byte stores, written by the driver tasks, read by the poll.
 *
 * Family port only (the OpenBSW output manager and the generated table of this family); no Qt, no board value. */

#include "bsp/safeOutputs/safeOutputsConfiguration.h"
#include "outputManager/Output.h"

#include <cstdint>

namespace integration
{
namespace safeoff
{
static_assert(
    (NX_SAFE_OFF_OUTPUT_COUNT >= 0) && (NX_SAFE_OFF_OUTPUT_COUNT <= NX_SAFE_OUTPUT_COUNT),
    "the safe_off outputs are a block of the safe outputs table");

/** Number of safe_off outputs of the project. */
constexpr uint8_t COUNT = static_cast<uint8_t>(NX_SAFE_OFF_OUTPUT_COUNT);

/** Table index of the first safe_off output (the PWM groups come first). */
constexpr int32_t FIRST = static_cast<int32_t>(NX_SAFE_OUTPUT_COUNT) - static_cast<int32_t>(NX_SAFE_OFF_OUTPUT_COUNT);

namespace detail
{
// clang-format off
#define NX_SAFE_OFF_FALLBACK_(name, address, value, gate, gateMask) name = -1,
#define NX_SAFE_OFF_OUTPUT_ID_(name, address, value, gate, gateMask) static_cast<int32_t>(name),
// clang-format on

/** Every entry name of the table as -1; found only if the name is no enumerator of ::bios::Output. */
struct Names
{
    enum : int32_t
    {
        NX_SAFE_OUTPUTS(NX_SAFE_OFF_FALLBACK_) NX_SAFE_OFF_NO_ENTRY_ = -1
    };

    /** Per table entry the OutputId of the output of that name, -1 for a name that is no output. */
    struct Ids : ::bios::Output
    {
        static constexpr int32_t TABLE[NX_SAFE_OUTPUT_COUNT + 1] = {NX_SAFE_OUTPUTS(NX_SAFE_OFF_OUTPUT_ID_) -1};
    };
};

#undef NX_SAFE_OFF_FALLBACK_
#undef NX_SAFE_OFF_OUTPUT_ID_
} // namespace detail

/** OutputId of safe_off output \p index (0 .. COUNT - 1). */
constexpr int32_t outputOf(uint8_t const index)
{
    return detail::Names::Ids::TABLE[static_cast<uint32_t>(FIRST + static_cast<int32_t>(index))];
}

/** Index of \p outputId among the safe_off outputs, -1 if it is none. */
constexpr int32_t indexOf(uint32_t const outputId)
{
    for (int32_t entry = FIRST; entry < static_cast<int32_t>(NX_SAFE_OUTPUT_COUNT); ++entry)
    {
        int32_t const id = detail::Names::Ids::TABLE[static_cast<uint32_t>(entry)];
        if ((id >= 0) && (static_cast<uint32_t>(id) == outputId))
        {
            return entry - FIRST;
        }
    }
    return -1;
}

/** true if \p outputId is a safe_off output: only its driver switches it (SYS-150, SYS-151). */
constexpr bool isSafeOff(uint32_t const outputId) { return indexOf(outputId) >= 0; }

/** Every safe_off entry of the table names a digital output of the project. */
constexpr bool everySafeOffIsAnOutput()
{
    for (int32_t entry = FIRST; entry < static_cast<int32_t>(NX_SAFE_OUTPUT_COUNT); ++entry)
    {
        int32_t const id = detail::Names::Ids::TABLE[static_cast<uint32_t>(entry)];
        if ((id < 0) || (id >= static_cast<int32_t>(::bios::Output::NUMBER_OF_INTERNAL_OUTPUTS)))
        {
            return false;
        }
    }
    return true;
}

static_assert(
    everySafeOffIsAnOutput(),
    "a safe_off entry of the safe outputs table is no digital output of the project (bios::Output) - configure "
    "again (tools/bswgen names both after the board entry)");

/** The level the drivers command for the safe_off outputs (SYS-151), see above. */
class Commanded
{
public:
    /** Records \p on for \p outputId if it is a safe_off output; any other output is not recorded. */
    static void note(uint32_t const outputId, bool const on)
    {
        int32_t const index = indexOf(outputId);
        if (index >= 0)
        {
            _active[static_cast<uint32_t>(index)] = on ? 1U : 0U;
        }
    }

    /** true while the driver of safe_off output \p index commands it active. */
    static bool isActive(uint8_t const index) { return (index < COUNT) && (_active[index] != 0U); }

private:
    // zero after every reset: no driver has commanded anything, Output::init() drives every output inactive
    static inline uint8_t volatile _active[(COUNT > 0U) ? COUNT : 1U] = {};
};

/**
 * The write of a digital output by its driver: the commanded level first (SYS-151), then the pin. The only way the
 * family port writes an output on behalf of a driver (binder of the module binding, DRV_EN of the TMC6200).
 */
inline bool setCommandedOutput(::bios::Output::OutputId const output, bool const on)
{
    Commanded::note(static_cast<uint32_t>(output), on);
    return ::bios::Output::set(output, on ? 1U : 0U) == ::bsp::BSP_OK;
}

} // namespace safeoff
} // namespace integration
