#include "calibration/PlausibilityMonitor.h"

#include <lifecycle/LifecycleLogger.h>
#include <util/logger/Logger.h>

#ifdef NX_RTE_HAS_DEM
#include <dem/IFaultMemory.h>
#endif

namespace calibration
{
namespace plausibility
{
namespace
{
using ::util::logger::LIFECYCLE;
using ::util::logger::Logger;

Summary currentSummary{false, Source::IMAGE, PLAUSIBLE, 0U, 0U, PLAUSIBLE};

/** BSW event "calibration.plausibility" (diag.yaml dtcs[].event.bsw); nothing if the project has no such DTC. */
void reportToDem(bool const passed)
{
#ifdef NX_RTE_HAS_DEM
    ::dem::IFaultMemory* const memory = ::dem::registry.memory;
    if (memory != nullptr)
    {
        int32_t const dtc = memory->findReportedEvent("calibration.plausibility");
        if (dtc >= 0)
        {
            memory->report(static_cast<uint16_t>(dtc), passed ? ::dem::Result::PASSED : ::dem::Result::FAILED);
        }
    }
#else
    (void)passed;
#endif
}

#ifdef NX_RTE_HAS_PARAMETER_CHECKS
void printRules(::util::format::SharedStringWriter& out, uint8_t const rules)
{
    if ((rules & ::rte::CHECK_FORM) != 0U)
    {
        (void)out.printf(" form");
    }
    if ((rules & ::rte::CHECK_LIMITS) != 0U)
    {
        (void)out.printf(" limits");
    }
    if ((rules & ::rte::CHECK_ASCENDING) != 0U)
    {
        (void)out.printf(" ascending");
    }
}
#endif

void printFinding(::util::format::SharedStringWriter& out, Finding const& f)
{
    if (f.rule == Rule::NONE)
    {
        (void)out.printf("plausible");
    }
    else
    {
        (void)out.printf(
            "%s, check %u row %u element %u (%s)",
            ruleText(f.rule),
            static_cast<unsigned>(f.check),
            static_cast<unsigned>(f.row),
            static_cast<unsigned>(f.element),
            parameterName(f.check));
    }
}
} // namespace

bool startUp(Overlay const& overlay, Source const source)
{
    Finding const finding = checkAll(overlay);
    currentSummary.checked = true;
    currentSummary.source  = source;
    currentSummary.startUp = finding;
    bool const plausible   = (finding.rule == Rule::NONE);
    // SYS-16: one logger entry of 128 bytes; ruleText/sourceText return literals (tools/logcheck)
    if (plausible)
    {
        Logger::info(
            LIFECYCLE,
            "Calibration: %u safety-relevant parameters plausible (%s)",
            static_cast<unsigned>(checkCount()),
            sourceText(source));
    }
    else
    {
        Logger::error(
            LIFECYCLE,
            "Calibration: IMPLAUSIBLE %s, check %u row %u element %u (%s)",
            ruleText(finding.rule),
            static_cast<unsigned>(finding.check),
            static_cast<unsigned>(finding.row),
            static_cast<unsigned>(finding.element),
            sourceText(source));
    }
    reportToDem(plausible);
    return plausible;
}

bool acceptWrite(void const* const destination, void const* const source, size_t const n)
{
    if (findCheck(destination, n) >= checkCount())
    {
        return true; // no checked parameter
    }
    ++currentSummary.writesChecked;
    Finding const finding = checkWrite(destination, source, n);
    if (finding.rule == Rule::NONE)
    {
        return true;
    }
    ++currentSummary.writesRefused;
    currentSummary.lastRefused = finding;
    Logger::error(
        LIFECYCLE,
        "Calibration: XCP write refused, %s, check %u row %u element %u",
        ruleText(finding.rule),
        static_cast<unsigned>(finding.check),
        static_cast<unsigned>(finding.row),
        static_cast<unsigned>(finding.element));
    reportToDem(false);
    return false;
}

Summary const& summary() { return currentSummary; }

void print(::util::format::SharedStringWriter& out)
{
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
    // read without a lock: a value changes only through an XCP write, which is checked before it takes effect; a
    // write applied during this output may show as a mixed state here, never in the ECU
    size_t failures = 0U;
    for (size_t i = 0U; i < ::rte::PARAMETER_CHECK_COUNT; ++i)
    {
        ::rte::ParameterCheck const& c = ::rte::PARAMETER_CHECKS[i];
        Finding const f                = checkOne(i, IN_PLACE);
        (void)out.printf("check %u row %u %s:", static_cast<unsigned>(i), static_cast<unsigned>(c.row), parameterName(i));
        printRules(out, c.rules);
        (void)out.printf(" -> ");
        printFinding(out, f);
        (void)out.printf("\r\n");
        failures += (f.rule == Rule::NONE) ? 0U : 1U;
    }
    (void)out.printf(
        "now: %u checks, %u implausible\r\n",
        static_cast<unsigned>(::rte::PARAMETER_CHECK_COUNT),
        static_cast<unsigned>(failures));
#else
    (void)out.printf("no safety-relevant calibration parameter: nothing to check\r\n");
#endif
    Summary const& s = currentSummary;
    (void)out.printf("start-up: ");
    if (s.checked)
    {
        printFinding(out, s.startUp);
        (void)out.printf(" (%s)\r\n", sourceText(s.source));
    }
    else
    {
        (void)out.printf("not run\r\n");
    }
    (void)out.printf(
        "XCP writes to checked parameters: %u, refused %u",
        static_cast<unsigned>(s.writesChecked),
        static_cast<unsigned>(s.writesRefused));
    if (s.writesRefused != 0U)
    {
        (void)out.printf(", last: ");
        printFinding(out, s.lastRefused);
    }
    (void)out.printf("\r\n");
}

char const* sourceText(Source const source)
{
    switch (source)
    {
        case Source::SEGMENT: return "segment";
        case Source::IMAGE: return "image";
        default: return "?";
    }
}

} // namespace plausibility
} // namespace calibration
