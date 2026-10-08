#pragma once

/* SYS-29 (gap G5): the event box of one device package for its report-only messages (DEVICE_REPORT, S3).
 *
 * Why a box of its own per package: SYS-37 keeps one box per source, so a package that floods its box cannot push
 * the reports of another one - or of the RTE deadlines - out (separation of the base-software mechanisms from the
 * device packages, doc/safety/concept.md sections 1.4 and 13.1).
 *
 * Where it lives: in ordinary RAM with write access for the package's task - the package owns the object (a member
 * or a static of its integration, never in a libsafe* library, whose .bss the MPU protects on the S32K1). The safety
 * task hands it to SafeSupervisor::deliverDeviceReports() in its cycle; the supervisor never keeps a reference, so
 * no pointer into ordinary RAM is stored in the protected RAM.
 *
 * What it does not do (decision of the user 2026-09-21, `sys37-postfach-abschaltend`): carry a fault that must
 * switch something off. Everything delivered from a box ends as DEVICE_REPORT, which is S3 by construction; a
 * switching-off fault is detected in the safety task and raised there as DEVICE_FAULT (safeSupervisor/
 * DeviceReport.h). Whether the device packages of SYS-28 use this box at all, or only report through checks in the
 * safety task, is decided there - both paths use the same context word and the same handle().
 */

#include "safeSupervisor/DeviceReport.h"
#include "safeSupervisor/SafetyEventMailbox.h"

#include <cstdint>

namespace safety
{

class DeviceReportBox
{
public:
    /**
     * Any context: stores one report-only message. A malformed word (safeSupervisor/DeviceReport.h) is refused
     * instead of stored, so a package cannot hand the supervisor a word it cannot attribute.
     * \return false if the word was malformed or the box full (the latter is reported as CONTEXT_LOST)
     */
    bool report(uint32_t const word) { return device::isWellFormed(word) && _box.post(word); }

    bool report(
        uint8_t const bus,
        uint8_t const deviceInstance,
        device::FaultClass const fault,
        uint8_t const detail = 0U)
    {
        return report(device::context(bus, deviceInstance, fault, detail));
    }

    /** Safety context only, through SafeSupervisor::deliverDeviceReports(): see SafetyEventMailbox::drain(). */
    template<typename Deliver>
    uint32_t drain(Deliver&& deliver)
    {
        return _box.drain(static_cast<Deliver&&>(deliver));
    }

    /** Reports waiting for the safety task (diagnostics and tests). */
    uint32_t pending() const { return _box.pending(); }

    /** Reports dropped because the box was full since the last delivery (diagnostics and tests). */
    uint32_t lost() const { return _box.lost(); }

private:
    SafetyEventMailbox _box;
};

} // namespace safety
