#pragma once

#include "calibration/Plausibility.h"

#include <util/format/SharedStringWriter.h>

#include <cstddef>
#include <cstdint>

/**
 * Target side of the plausibility check of the safety-relevant calibration parameters (SYS-90): when it runs, what
 * it logs and reports, and the console output. The checks themselves are Plausibility.h.
 *
 * - start-up: calibration::start() (project with memory.calibration) checks the values the ECU is about to use - the
 *   segment before it becomes the working page, or the image defaults -, RteSystem::init() checks the values in place
 *   in a project without a segment. A violation leads to the reaction of on_invalid (refuse for a project with a
 *   checked parameter, tools/nxconfig), in a project without a segment always to refuse.
 * - XCP: XcpSystem::write() asks acceptWrite() before a write is scheduled; a refused write never takes effect, the
 *   master gets ERR_OUT_OF_RANGE.
 * - no cyclic check: in the development profile the values change only through XCP (checked before they take
 *   effect); in the production profile they are const in the flash segment, whose integrity the CRC at start-up and
 *   the ROM check cover. Memory faults of the RAM working page are the matter of the RAM test, not of this check.
 *
 * Every result is one log line (SYS-16: texts are literals, tools/logcheck) and a report of the BSW event
 * "calibration.plausibility" to the fault memory if the project has a DTC for it (diag.yaml event.bsw): PASSED after
 * a plausible start-up check, FAILED after an implausible one and after every refused write.
 */
namespace calibration
{
namespace plausibility
{
/** Where the start-up check read the values. */
enum class Source : uint8_t
{
    SEGMENT, // the calibration segment, before it becomes the working page (or in place: production profile)
    IMAGE,   // the values built into the image (no segment, or the defaults after an invalid segment)
};

struct Summary
{
    bool checked;           // start-up check ran
    Source source;
    Finding startUp;        // its result
    uint32_t writesChecked; // XCP writes to a checked parameter
    uint32_t writesRefused;
    Finding lastRefused;
};

/** Start-up check of every generated check with \p overlay; one log line, DEM report. False: implausible. */
bool startUp(Overlay const& overlay, Source source);

/** XCP write of \p n bytes at \p destination (context of XcpSystem): true if it may take effect. */
bool acceptWrite(void const* destination, void const* source, size_t n);

Summary const& summary();

/** Console "cal check": every check with its rules and its result now, the start-up result, the XCP counters. */
void print(::util::format::SharedStringWriter& out);

char const* sourceText(Source source);

} // namespace plausibility
} // namespace calibration
