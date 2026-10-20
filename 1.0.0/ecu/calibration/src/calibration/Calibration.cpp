#include "calibration/Calibration.h"

#include <RteConfig.h>                // rte::PARAMETERS (tools/rtegen)
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
#include "calibration/PlausibilityMonitor.h" // SYS-90: safety-relevant parameters checked before use
#endif
#include <lifecycle/LifecycleLogger.h>
#include <memory/memoryLayout.h>      // NX_MEMORY_CAL_* (tools/bswgen/blgen.py from memory.calibration)
#include <util/format/SharedStringWriter.h>
#include <util/logger/Logger.h>

#include <cstring>

#ifndef NX_MEMORY_CAL_START
#error "calibration: the project has no memory.calibration (ecu/calibration is built only with it)"
#endif
#ifndef NX_CAL_LAYOUT_ID
#error "calibration: NX_CAL_LAYOUT_ID missing (layout identifier of RteCalibration.json, ecu/calibration/CMakeLists.txt)"
#endif

// Linker template (platforms/<family>/port/main/linkerscript/application.dld.in, SYS-89)
extern "C"
{
extern uint8_t const __nx_cal_segment[];    // header, NX_MEMORY_CAL_START
extern uint8_t const __nx_cal_values[];     // values behind the header (reference page)
extern uint8_t const __nx_cal_values_end[]; // exclusive
#if !NX_MEMORY_CAL_PRODUCTION
extern uint8_t __nx_cal_ram_start[]; // working page: the parameters of the RTE (Rte_Prm_*_data)
extern uint8_t __nx_cal_ram_end[];
#endif
}

namespace rte
{
// Declared by RteConfig.h only if the project has parameters; referenced below only if PARAMETER_COUNT > 0.
extern Parameter const PARAMETERS[];
} // namespace rte

namespace calibration
{
namespace
{
using ::util::logger::LIFECYCLE;
using ::util::logger::Logger;

constexpr OnInvalid ON_INVALID = static_cast<OnInvalid>(NX_MEMORY_CAL_ON_INVALID);
constexpr bool PRODUCTION      = (NX_MEMORY_CAL_PRODUCTION != 0);

State currentState{false, Status::ERASED, Action::REFUSE, SegmentHeader{}, 0U, 0U};
RunTimeCheck runTime{false, 0U, Status::VALID};

uintptr_t address(void const* const pointer) { return reinterpret_cast<uintptr_t>(pointer); }

uint32_t valuesLength()
{
    return static_cast<uint32_t>(address(__nx_cal_values_end) - address(__nx_cal_values));
}

template<size_t Count>
struct ParameterTable
{
    static ::rte::Parameter const* get() { return ::rte::PARAMETERS; }
};

template<>
struct ParameterTable<0U>
{
    static ::rte::Parameter const* get() { return nullptr; }
};

uint8_t elementSize(::rte::SignalType const type)
{
    switch (type)
    {
        case ::rte::SignalType::BOOLEAN:
        case ::rte::SignalType::UINT8:
        case ::rte::SignalType::SINT8: return 1U;
        case ::rte::SignalType::UINT16:
        case ::rte::SignalType::SINT16: return 2U;
        case ::rte::SignalType::UINT32:
        case ::rte::SignalType::SINT32:
        case ::rte::SignalType::FLOAT32: return 4U;
        case ::rte::SignalType::FLOAT64: return 8U;
        default: return 0U;
    }
}

char const* typeName(::rte::SignalType const type)
{
    switch (type)
    {
        case ::rte::SignalType::BOOLEAN: return "boolean";
        case ::rte::SignalType::UINT8: return "uint8";
        case ::rte::SignalType::SINT8: return "sint8";
        case ::rte::SignalType::UINT16: return "uint16";
        case ::rte::SignalType::SINT16: return "sint16";
        case ::rte::SignalType::UINT32: return "uint32";
        case ::rte::SignalType::SINT32: return "sint32";
        case ::rte::SignalType::FLOAT32: return "float32";
        case ::rte::SignalType::FLOAT64: return "float64";
        default: return "?";
    }
}

char const* safetyName(::rte::SafetyClass const safety)
{
    switch (safety)
    {
        case ::rte::SafetyClass::QM: return "qm";
        case ::rte::SafetyClass::ASIL_A: return "asil_a";
        case ::rte::SafetyClass::ASIL_B: return "asil_b";
        case ::rte::SafetyClass::ASIL_C: return "asil_c";
        case ::rte::SafetyClass::ASIL_D: return "asil_d";
        default: return "?";
    }
}

/** One stored element as it is in memory: integers in decimal, floats as bit pattern (no conversion). */
void printElement(::util::format::SharedStringWriter& out, ::rte::SignalType const type, uint8_t const* const p)
{
    switch (type)
    {
        case ::rte::SignalType::BOOLEAN:
        case ::rte::SignalType::UINT8: (void)out.printf(" %u", static_cast<unsigned>(p[0])); break;
        case ::rte::SignalType::SINT8:
        {
            int8_t v = 0;
            (void)::std::memcpy(&v, p, sizeof(v));
            (void)out.printf(" %d", static_cast<int>(v));
            break;
        }
        case ::rte::SignalType::UINT16:
        {
            uint16_t v = 0U;
            (void)::std::memcpy(&v, p, sizeof(v));
            (void)out.printf(" %u", static_cast<unsigned>(v));
            break;
        }
        case ::rte::SignalType::SINT16:
        {
            int16_t v = 0;
            (void)::std::memcpy(&v, p, sizeof(v));
            (void)out.printf(" %d", static_cast<int>(v));
            break;
        }
        case ::rte::SignalType::UINT32:
        {
            uint32_t v = 0U;
            (void)::std::memcpy(&v, p, sizeof(v));
            (void)out.printf(" %u", static_cast<unsigned>(v));
            break;
        }
        case ::rte::SignalType::SINT32:
        {
            int32_t v = 0;
            (void)::std::memcpy(&v, p, sizeof(v));
            (void)out.printf(" %d", static_cast<int>(v));
            break;
        }
        case ::rte::SignalType::FLOAT32:
        {
            uint32_t v = 0U;
            (void)::std::memcpy(&v, p, sizeof(v));
            (void)out.printf(" 0x%08x", static_cast<unsigned>(v));
            break;
        }
        case ::rte::SignalType::FLOAT64:
        {
            uint32_t v[2] = {0U, 0U};
            (void)::std::memcpy(&v[0], p, sizeof(v));
            (void)out.printf(" 0x%08x%08x", static_cast<unsigned>(v[1]), static_cast<unsigned>(v[0]));
            break;
        }
        default: (void)out.printf(" ?"); break;
    }
}

/** Address of the reference (flash) copy of the stored value at \p value, nullptr outside the working page. */
uint8_t const* referenceOf(void const* const value, size_t const bytes)
{
#if NX_MEMORY_CAL_PRODUCTION
    (void)bytes;
    return static_cast<uint8_t const*>(value); // the values are read from the segment itself
#else
    uintptr_t const at = address(value);
    if ((at < address(__nx_cal_ram_start)) || ((at + bytes) > address(__nx_cal_ram_end)))
    {
        return nullptr;
    }
    return &__nx_cal_values[at - address(__nx_cal_ram_start)];
#endif
}

void printValues(::util::format::SharedStringWriter& out)
{
    ::rte::Parameter const* const parameters = ParameterTable<::rte::PARAMETER_COUNT>::get();
    for (size_t i = 0U; i != ::rte::PARAMETER_COUNT; ++i)
    {
        ::rte::Parameter const& p = parameters[i];
        uint8_t const size        = elementSize(p.type);
        size_t const bytes        = static_cast<size_t>(size) * p.count;
        auto const* const stored  = static_cast<uint8_t const*>(p.value);
        (void)out.printf(
            "%s %s %s[%u] @0x%08x:",
            p.name,
            safetyName(p.safety),
            typeName(p.type),
            static_cast<unsigned>(p.count),
            static_cast<unsigned>(address(p.value)));
        for (size_t e = 0U; (size != 0U) && (e < p.count); ++e)
        {
            printElement(out, p.type, &stored[e * size]);
        }
        uint8_t const* const reference = referenceOf(p.value, bytes);
        if (reference == nullptr)
        {
            (void)out.printf("  (outside the segment)\r\n");
        }
        else if ((reference != stored) && (::std::memcmp(reference, stored, bytes) != 0))
        {
            (void)out.printf("  (changed, reference:");
            for (size_t e = 0U; (size != 0U) && (e < p.count); ++e)
            {
                printElement(out, p.type, &reference[e * size]);
            }
            (void)out.printf(")\r\n");
        }
        else
        {
            (void)out.printf("\r\n");
        }
    }
}

void printStatus(::util::format::SharedStringWriter& out)
{
    State const& s = currentState;
    (void)out.printf(
        "segment 0x%08x..0x%08x (%u bytes), profile %s, on_invalid %s\r\n",
        static_cast<unsigned>(NX_MEMORY_CAL_START),
        static_cast<unsigned>(NX_MEMORY_CAL_START + NX_MEMORY_CAL_SIZE - 1U),
        static_cast<unsigned>(NX_MEMORY_CAL_SIZE),
        PRODUCTION ? "production" : "development",
        onInvalidText(ON_INVALID));
    (void)out.printf(
        "header: magic 0x%08x format %u layout 0x%08x dataset 0x%08x length %u crc 0x%08x\r\n",
        static_cast<unsigned>(s.header.magic),
        static_cast<unsigned>(s.header.format),
        static_cast<unsigned>(s.header.layout),
        static_cast<unsigned>(s.header.dataset),
        static_cast<unsigned>(s.header.length),
        static_cast<unsigned>(s.header.valuesCrc));
    (void)out.printf(
        "check: %s (expected layout 0x%08x, %u bytes) -> %s\r\n",
        s.checked ? statusText(s.status) : "not run",
        static_cast<unsigned>(s.expectedLayout),
        static_cast<unsigned>(s.length),
        actionText(s.action));
    uint32_t const reference = crc32(__nx_cal_values, valuesLength());
#if NX_MEMORY_CAL_PRODUCTION
    (void)out.printf(
        "values in flash 0x%08x, CRC 0x%08x (no working page)\r\n",
        static_cast<unsigned>(address(__nx_cal_values)),
        static_cast<unsigned>(reference));
#else
    uint32_t const bytes   = static_cast<uint32_t>(address(__nx_cal_ram_end) - address(__nx_cal_ram_start));
    uint32_t const working = crc32(__nx_cal_ram_start, bytes);
    (void)out.printf(
        "working page RAM 0x%08x CRC 0x%08x, reference page flash 0x%08x CRC 0x%08x%s\r\n",
        static_cast<unsigned>(address(__nx_cal_ram_start)),
        static_cast<unsigned>(working),
        static_cast<unsigned>(address(__nx_cal_values)),
        static_cast<unsigned>(reference),
        (working == reference) ? "" : " (changed)");
#endif
    // SYS-91: the segment is checked against its own header in every pass of the ROM check, not by the list
    (void)out.printf(
        "run-time check: %s, %u passes, last %s\r\n",
        runTime.armed ? "armed (ROM check pass)" : "not armed (segment not in use)",
        static_cast<unsigned>(runTime.passes),
        statusText(runTime.last));
}

enum CommandId
{
    ID_STATUS,
    ID_VALUES,
    ID_CHECK,
};
} // namespace

bool start()
{
    uint32_t const length = valuesLength();
    Status const status   = check(__nx_cal_segment, NX_MEMORY_CAL_SIZE, NX_CAL_LAYOUT_ID, length);
    Action action         = decide(status, ON_INVALID, PRODUCTION);
#if !NX_MEMORY_CAL_PRODUCTION
    size_t const page = static_cast<size_t>(address(__nx_cal_ram_end) - address(__nx_cal_ram_start));
    size_t const used = (page < length) ? page : length;
#endif
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
    // SYS-90: the safety-relevant parameters are checked on the values the ECU is about to use, before they take
    // effect - the segment as it would become the working page (production: read in place) - and implausible values
    // get the reaction of on_invalid like a segment that is not valid (nxconfig: refuse with a checked parameter)
    bool plausible = true;
    if (action == Action::USE_SEGMENT)
    {
#if NX_MEMORY_CAL_PRODUCTION
        plausibility::Overlay const overlay = plausibility::IN_PLACE;
#else
        plausibility::Overlay const overlay{__nx_cal_ram_start, __nx_cal_values, used};
#endif
        plausible = plausibility::startUp(overlay, plausibility::Source::SEGMENT);
        if (!plausible)
        {
            action = decideInvalid(ON_INVALID, PRODUCTION);
        }
    }
    if (action == Action::USE_DEFAULTS)
    {
        // development profile: the image defaults in the working page (the build checked them, the ECU checks
        // what it really holds)
        if (!plausibility::startUp(plausibility::IN_PLACE, plausibility::Source::IMAGE))
        {
            plausible = false;
            action    = Action::REFUSE;
        }
    }
#endif
    currentState = State{true, status, action, readHeader(__nx_cal_segment), NX_CAL_LAYOUT_ID, length};
    // SYS-91: from here on the ROM check re-checks the segment in use against the header accepted now
    runTime.armed = recheckArmed(status, action);
#if !NX_MEMORY_CAL_PRODUCTION
    // development profile: the start-up of the image put the defaults of the build into the working page; a valid
    // segment (or on_invalid warn) overwrites them with the reference page
    if (action == Action::USE_SEGMENT)
    {
        (void)::std::memcpy(__nx_cal_ram_start, __nx_cal_values, used);
    }
#endif
    // SYS-16: one logger entry of 128 bytes; the texts are string literals of the functions (tools/logcheck)
    if (status == Status::VALID)
    {
        Logger::info(
            LIFECYCLE,
            "Calibration: segment valid, layout 0x%x, dataset 0x%x, %u bytes, CRC 0x%x",
            static_cast<unsigned>(currentState.header.layout),
            static_cast<unsigned>(currentState.header.dataset),
            static_cast<unsigned>(length),
            static_cast<unsigned>(currentState.header.valuesCrc));
    }
    else
    {
        Logger::error(
            LIFECYCLE,
            "Calibration: INVALID %s, layout 0x%x (expected 0x%x): %s",
            statusText(status),
            static_cast<unsigned>(currentState.header.layout),
            static_cast<unsigned>(NX_CAL_LAYOUT_ID),
            actionText(action));
    }
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
    if (!plausible)
    {
        Logger::error(LIFECYCLE, "Calibration: implausible values, on_invalid %s: %s", onInvalidText(ON_INVALID),
                      actionText(action));
    }
#endif
    return action != Action::REFUSE;
}

State const& state() { return currentState; }

uint32_t segmentAddress() { return static_cast<uint32_t>(address(__nx_cal_segment)); }

uint32_t segmentUsedBytes() { return static_cast<uint32_t>(HEADER_SIZE) + valuesLength(); }

bool isProduction() { return PRODUCTION; }

Status recheckSegment()
{
    if (!runTime.armed)
    {
        return Status::VALID;
    }
    Status const status = recheck(
        __nx_cal_segment, NX_MEMORY_CAL_SIZE, currentState.expectedLayout, currentState.length, currentState.header);
    runTime.passes = runTime.passes + 1U;
    runTime.last   = status;
    return status;
}

RunTimeCheck const& runTimeCheck() { return runTime; }

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(Command, "cal", "calibration segment and plausibility (SYS-89, SYS-90)")
COMMAND_GROUP_COMMAND(ID_STATUS, "status", "header, start-up check, working and reference page")
COMMAND_GROUP_COMMAND(ID_VALUES, "values", "stored value of every parameter as in memory, no A2L conversion (AoU-28)")
COMMAND_GROUP_COMMAND(ID_CHECK, "check", "plausibility of the safety-relevant parameters now, at start-up, XCP (SYS-90)")
DEFINE_COMMAND_GROUP_GET_INFO_END

void Command::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    ::util::format::SharedStringWriter out(context);
    if (idx == ID_VALUES)
    {
        printValues(out);
    }
    else if (idx == ID_CHECK)
    {
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
        plausibility::print(out);
#else
        (void)out.printf("no safety-relevant calibration parameter: nothing to check\r\n");
#endif
    }
    else
    {
        printStatus(out);
    }
}

} // namespace calibration
