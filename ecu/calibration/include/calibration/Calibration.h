#pragma once

#include "calibration/CalibrationSegment.h"

#include <util/command/GroupCommand.h>

#include <cstddef>
#include <cstdint>

/**
 * Calibration segment of the application (SYS-89): start-up check, RAM working page, console read-back.
 *
 * Built only for a project with memory.calibration (ecu/calibration/CMakeLists.txt defines NX_CALIBRATION_SEGMENT).
 * The linker template places the segment (platforms/<family>/port/main/linkerscript: symbols __nx_cal_*), and
 * tools/calseg seals it after the link (header with layout identifier and CRC):
 *
 * - development profile: the parameters live in a RAM working page, which the start-up of the image fills with the
 *   defaults of the build; start() copies the values of a valid segment (the reference page in flash) over them.
 *   XCP calibrates the working page.
 * - production profile: the parameters live in the flash segment itself; there is no RAM copy and no write path.
 *
 * start() runs once in RteSystem::init() before the init runnables. Its result decides whether the RTE starts the
 * application functions (on_invalid refuse), is logged in one line and reported to the fault memory by RteSystem
 * (BSW event "calibration.segment").
 */
namespace calibration
{
/** What start() found and did. */
struct State
{
    bool checked;
    Status status;
    Action action;
    SegmentHeader header;
    uint32_t expectedLayout;
    uint32_t length; // bytes of values the application reads
};

/** Start-up check and, in the development profile, the copy into the working page. False: refuse. */
bool start();

State const& state();

/** Flash address and size of the segment (header + values), the reference page of XCP (read only). */
uint32_t segmentAddress();
uint32_t segmentUsedBytes();

bool isProduction();

/**
 * SYS-91: run-time check of the segment against its own reference (the CRCs of its header and the header start()
 * accepted, calibration::recheck). The segment is not in the ROM check list of the build; the family integration
 * registers this as the check of the ROM check's area with its own reference, so it runs in every pass of SM-14
 * (same period, same flash-write rules; a deviation is ROM_CHECK_FAILURE). Armed only if start() found the segment
 * valid and uses it; otherwise (refuse, defaults, warn on an invalid segment, before start()) it returns VALID and
 * checks nothing - the segment is not in use then. Must not log (the pass may still be discarded).
 */
Status recheckSegment();

/** SYS-91: what the run-time check did (console "cal status"). */
struct RunTimeCheck
{
    bool armed;
    uint32_t passes;
    Status last;
};

RunTimeCheck const& runTimeCheck();

/**
 * Console command "cal" (runs in the console context): "status" prints the header, the check and both pages,
 * "values" prints the stored value of every calibration parameter as it is in memory - integers in decimal, floats
 * as their bit pattern -, without the conversion of the A2L (assumption of use AoU-28, doc/safety).
 */
class Command : public ::util::command::GroupCommand
{
protected:
    DECLARE_COMMAND_GROUP_GET_INFO
    void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;
};

} // namespace calibration
