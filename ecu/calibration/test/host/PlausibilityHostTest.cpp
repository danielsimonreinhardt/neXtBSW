/* Host test of the plausibility check of the safety-relevant calibration parameters (SYS-90), no target needed.
 *
 * Built and run by tools/rtegen/test_rtegen_plausibility.py (tools/test calibration-plausibility): tools/rtegen
 * generates the RTE of the test profile tools/rtegen/test/calib (C component CalMapSwc: a shared axis, a map with
 * an own Y axis and a value of class asil_b, the other parameters QM) on buildtest_s32k148_min, and this test is
 * compiled with that RteConfig.cpp, Rte.c, CalMapSwc.c and the production sources ecu/calibration Plausibility.cpp,
 * PlausibilityMonitor.cpp and CalibrationSegment.cpp. Only the logger, the console writer (test/host/stubs) and the
 * fault memory (a recording dem::IFaultMemory in dem::registry) are replaced.
 *
 * What is checked:
 *   1. The generated table: four checks (axis, own axis, map, value) with their rules; the defaults are plausible.
 *   2. LIMITS: every limit is inclusive, one step beyond it is found (value, map element, both axes), the element
 *      index is reported; QM parameters are not checked.
 *   3. ASCENDING: equal and falling breakpoints of the shared and the own axis.
 *   4. Overlay: a candidate (segment or XCP write) is judged without touching the stored values; an XCP write of one
 *      breakpoint that would break the order is refused, the whole axis in one write passes; a write to a QM
 *      parameter is not checked; findCheck().
 *   5. Start-up (PlausibilityMonitor): plausible -> log line and DEM PASSED; an implausible segment candidate and an
 *      implausible stored value -> error line, DEM FAILED; the reaction of on_invalid to implausible values
 *      (decideInvalid: refuse, defaults only in development, warn); without a DTC for the event nothing is reported.
 *   6. XCP (acceptWrite): refused write -> false, counters, log line, DEM FAILED; plausible and QM writes pass.
 *   7. Console "cal check" text.
 *   Variants (macros of the Python driver):
 *   FLOAT_GAIN   the profile with Gain (float32) as an asil_b value 0 .. 2: NaN and infinity are outside the limits
 *   FORM_BROKEN  a generated check table with a wrong axis element count (map) and a wrong count (value): FORM
 */

#include "calibration/CalibrationSegment.h"
#include "calibration/Plausibility.h"
#include "calibration/PlausibilityMonitor.h"

#include <dem/IFaultMemory.h>
#include <util/logger/Logger.h> // host stub: the last line logged

#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>

namespace
{
using namespace ::calibration::plausibility;

unsigned checks   = 0U;
unsigned failures = 0U;

void expect(bool const condition, char const* const what)
{
    ++checks;
    if (!condition)
    {
        ++failures;
        std::printf("FAIL %s\n", what);
    }
}

[[maybe_unused]] bool contains(char const* const text, char const* const part) { return std::strstr(text, part) != nullptr; }

/** Recording fault memory: which DTC got which result. */
class FaultMemory : public ::dem::IFaultMemory
{
public:
    uint16_t dtcCount() const override { return 3U; }

    ::dem::DtcConfig const& dtcConfig(uint16_t) const override { return _config; }

    bool dtcInfo(uint16_t, ::dem::DtcInfo&) const override { return false; }

    int32_t findDtc(uint32_t) const override { return -1; }

    uint16_t countByStatusMask(uint8_t) const override { return 0U; }

    ::dem::MemoryInfo memoryInfo() const override { return ::dem::MemoryInfo{}; }

    int32_t readSnapshot(uint16_t, uint8_t, uint8_t*, size_t) const override { return -1; }

    int32_t readExtended(uint16_t, uint8_t, uint8_t*, size_t) const override { return -1; }

    bool clear(uint32_t) override { return true; }

    void setDtcSetting(bool) override {}

    int32_t findReportedEvent(char const* const name) const override
    {
        return (hasEvent && (std::strcmp(name, "calibration.plausibility") == 0)) ? 2 : -1;
    }

    void report(uint16_t const dtc, ::dem::Result const result) override
    {
        ++reports;
        lastDtc    = dtc;
        lastResult = result;
    }

    bool hasEvent        = true;
    unsigned reports     = 0U;
    uint16_t lastDtc     = 0xFFFFU;
    ::dem::Result lastResult{};

private:
    ::dem::DtcConfig _config{};
};

FaultMemory faultMemory;

[[maybe_unused]] size_t rowOf(char const* const name)
{
    for (size_t i = 0U; i < ::rte::PARAMETER_COUNT; ++i)
    {
        if (std::strcmp(::rte::PARAMETERS[i].name, name) == 0)
        {
            return i;
        }
    }
    std::printf("no parameter %s\n", name);
    return 0U;
}

template<typename T>
T* storage(char const* const name)
{
    return static_cast<T*>(::rte::PARAMETERS[rowOf(name)].value);
}

[[maybe_unused]] constexpr char const* AXIS   = "CalMapSwc.CalPrm.SpeedAxis_rpm";
[[maybe_unused]] constexpr char const* OWN_Y  = "CalMapSwc.CalPrm.TorqueLimit_Nm_Y";
[[maybe_unused]] constexpr char const* MAP    = "CalMapSwc.CalPrm.TorqueLimit_Nm";
[[maybe_unused]] constexpr char const* VALUE  = "CalMapSwc.CalPrm.MaxTorque_Nm";
[[maybe_unused]] constexpr char const* QM_ARR = "CalMapSwc.CalPrm.Offsets_Nm";

bool is(Finding const& f, Rule const rule, size_t const check, size_t const element)
{
    return (f.rule == rule) && (f.check == check) && (f.element == element)
           && (f.row == ::rte::PARAMETER_CHECKS[check].row);
}

#ifndef FORM_BROKEN
void generatedTable()
{
#ifdef FLOAT_GAIN
    expect(checkCount() == 5U, "table: five checks (Gain asil_b)");
#else
    expect(checkCount() == 4U, "table: four checks");
#endif
    expect(std::strcmp(parameterName(0U), AXIS) == 0, "table: check 0 is the shared axis");
    expect(std::strcmp(parameterName(1U), OWN_Y) == 0, "table: check 1 is the own Y axis");
    expect(std::strcmp(parameterName(2U), MAP) == 0, "table: check 2 is the map");
    expect(std::strcmp(parameterName(3U), VALUE) == 0, "table: check 3 is the value");
    expect(::rte::PARAMETER_CHECKS[0].rules == (::rte::CHECK_FORM | ::rte::CHECK_LIMITS | ::rte::CHECK_ASCENDING),
           "table: axis rules form, limits, ascending");
    expect(::rte::PARAMETER_CHECKS[2].rules == (::rte::CHECK_FORM | ::rte::CHECK_LIMITS), "table: map rules");
    expect((::rte::PARAMETER_CHECKS[2].shape[0] == 5U) && (::rte::PARAMETER_CHECKS[2].shape[1] == 3U),
           "table: map form 5 x 3");
    expect(findCheck(storage<void>(QM_ARR), 2U) == checkCount(), "table: a QM parameter has no check");
    expect(checkAll(IN_PLACE).rule == Rule::NONE, "defaults plausible");
    expect(std::strcmp(parameterName(99U), "") == 0, "name of a check out of range");
    expect(checkOne(99U, IN_PLACE).rule == Rule::FORM, "check out of range is FORM");
}

void limits()
{
    int16_t* const value = storage<int16_t>(VALUE); // stored 0 .. 3000 (0.1 Nm)
    int16_t const old    = *value;
    *value               = 3000;
    expect(checkOne(3U, IN_PLACE).rule == Rule::NONE, "value: upper limit inclusive");
    *value = 0;
    expect(checkOne(3U, IN_PLACE).rule == Rule::NONE, "value: lower limit inclusive");
    *value = 3001;
    expect(is(checkOne(3U, IN_PLACE), Rule::LIMITS, 3U, 0U), "value: one above the limit");
    expect(is(checkAll(IN_PLACE), Rule::LIMITS, 3U, 0U), "checkAll finds the value");
    *value = -1;
    expect(is(checkOne(3U, IN_PLACE), Rule::LIMITS, 3U, 0U), "value: one below the limit");
    *value = old;

    int16_t* const map = storage<int16_t>(MAP); // stored 0 .. 2500
    int16_t const m7   = map[7];
    map[7]             = 2501;
    expect(is(checkOne(2U, IN_PLACE), Rule::LIMITS, 2U, 7U), "map: element 7 above the limit");
    map[7] = 2500;
    expect(checkOne(2U, IN_PLACE).rule == Rule::NONE, "map: element 7 at the limit");
    map[7] = m7;

    uint16_t* const axis = storage<uint16_t>(AXIS); // 0 .. 8000
    uint16_t const a4    = axis[4];
    axis[4]              = 8001U;
    expect(is(checkOne(0U, IN_PLACE), Rule::LIMITS, 0U, 4U), "axis: last breakpoint above the limit");
    axis[4] = a4;

    int8_t* const own = storage<int8_t>(OWN_Y); // -40 .. 125
    int8_t const y0   = own[0];
    own[0]            = -41;
    expect(is(checkOne(1U, IN_PLACE), Rule::LIMITS, 1U, 0U), "own axis: below the limit");
    own[0] = y0;

    int16_t* const qm = storage<int16_t>(QM_ARR); // QM, -20 .. 20: not a runtime check
    int16_t const q0  = qm[0];
    qm[0]             = 30000;
    expect(checkAll(IN_PLACE).rule == Rule::NONE, "QM parameter outside its limits is not checked");
    qm[0] = q0;
    expect(checkAll(IN_PLACE).rule == Rule::NONE, "restored");
}

void ascending()
{
    uint16_t* const axis = storage<uint16_t>(AXIS); // 0 1000 2000 4000 6000
    uint16_t const a2    = axis[2];
    axis[2]              = 1000U;
    expect(is(checkOne(0U, IN_PLACE), Rule::ASCENDING, 0U, 2U), "axis: equal breakpoints");
    axis[2] = 999U;
    expect(is(checkOne(0U, IN_PLACE), Rule::ASCENDING, 0U, 2U), "axis: falling breakpoint");
    axis[2] = a2;
    int8_t* const own = storage<int8_t>(OWN_Y); // -20 25 90
    int8_t const y2   = own[2];
    own[2]            = 25;
    expect(is(checkOne(1U, IN_PLACE), Rule::ASCENDING, 1U, 2U), "own axis: equal breakpoints");
    own[2] = y2;
    expect(checkAll(IN_PLACE).rule == Rule::NONE, "restored");
}

void overlay()
{
    uint16_t* const axis = storage<uint16_t>(AXIS);
    uint16_t before[5];
    std::memcpy(before, axis, sizeof(before));
    // XCP: one breakpoint moved behind the next one
    uint16_t const one = 5000U;
    expect(is(checkWrite(&axis[1], &one, sizeof(one)), Rule::ASCENDING, 0U, 2U), "write: one breakpoint refused");
    expect(std::memcmp(before, axis, sizeof(before)) == 0, "write check leaves the stored values alone");
    // the whole axis in one write (one DOWNLOAD block) keeps the order
    uint16_t const all[5] = {0U, 1500U, 2500U, 4500U, 7000U};
    expect(checkWrite(axis, all, sizeof(all)).rule == Rule::NONE, "write: whole axis passes");
    uint16_t const high[5] = {0U, 1500U, 2500U, 4500U, 9000U};
    expect(is(checkWrite(axis, high, sizeof(high)), Rule::LIMITS, 0U, 4U), "write: whole axis above the limit");
    // a write to a QM parameter is not checked
    int16_t const wild = 30000;
    expect(checkWrite(storage<int16_t>(QM_ARR), &wild, sizeof(wild)).rule == Rule::NONE, "write: QM not checked");
    expect(findCheck(&axis[3], 2U) == 0U, "findCheck: element of the axis");
    expect(findCheck(storage<int16_t>(MAP) + 14, 2U) == 2U, "findCheck: last element of the map");
    // segment candidate: the value in the segment is bad, the one in RAM fine
    int16_t const segmentValue = 3500;
    Overlay const segment{storage<int16_t>(VALUE), &segmentValue, sizeof(segmentValue)};
    expect(is(checkAll(segment), Rule::LIMITS, 3U, 0U), "segment candidate found implausible");
    expect(checkAll(IN_PLACE).rule == Rule::NONE, "stored values untouched by the candidate");
}

void startUpAndDem()
{
    using ::util::logger::hostLog;
    faultMemory.reports = 0U;
    expect(startUp(IN_PLACE, Source::IMAGE), "start-up: plausible");
    expect(!hostLog.error && contains(hostLog.line, "safety-relevant parameters plausible (image)"),
           "start-up: info line");
    expect((faultMemory.reports == 1U) && (faultMemory.lastDtc == 2U) && (faultMemory.lastResult == ::dem::Result::PASSED),
           "start-up: DEM PASSED");
    int16_t const segmentValue = -5;
    Overlay const segment{storage<int16_t>(VALUE), &segmentValue, sizeof(segmentValue)};
    expect(!startUp(segment, Source::SEGMENT), "start-up: implausible segment");
    expect(hostLog.error && contains(hostLog.line, "Calibration: IMPLAUSIBLE outside limits, check 3 row")
               && contains(hostLog.line, "element 0 (segment)"),
           "start-up: error line");
    expect((faultMemory.reports == 2U) && (faultMemory.lastResult == ::dem::Result::FAILED), "start-up: DEM FAILED");
    expect(summary().checked && (summary().source == Source::SEGMENT) && (summary().startUp.rule == Rule::LIMITS),
           "start-up: summary");
    // the reaction of the project to implausible values (calibration::start: decideInvalid)
    using ::calibration::Action;
    using ::calibration::OnInvalid;
    expect(::calibration::decideInvalid(OnInvalid::REFUSE, false) == Action::REFUSE, "reaction refuse");
    expect(::calibration::decideInvalid(OnInvalid::DEFAULTS, false) == Action::USE_DEFAULTS, "reaction defaults");
    expect(::calibration::decideInvalid(OnInvalid::DEFAULTS, true) == Action::REFUSE, "reaction defaults production");
    expect(::calibration::decideInvalid(OnInvalid::WARN, false) == Action::USE_SEGMENT, "reaction warn");
    // a stored value out of its limits (no segment: the image values in place)
    uint16_t* const axis = storage<uint16_t>(AXIS);
    uint16_t const a1    = axis[1];
    axis[1]              = 0U;
    expect(!startUp(IN_PLACE, Source::IMAGE), "start-up: implausible image value");
    expect(contains(hostLog.line, "IMPLAUSIBLE not ascending, check 0 row 0 element 1 (image)"), "start-up: axis line");
    axis[1] = a1;
    // no DTC for the event: nothing reported
    faultMemory.hasEvent = false;
    unsigned const reports = faultMemory.reports;
    expect(startUp(IN_PLACE, Source::IMAGE), "start-up without DTC");
    expect(faultMemory.reports == reports, "no DTC: nothing reported");
    faultMemory.hasEvent = true;
}

void xcpWrites()
{
    using ::util::logger::hostLog;
    Summary const before = summary();
    uint16_t* const axis = storage<uint16_t>(AXIS);
    uint16_t const bad   = 1U;
    expect(!acceptWrite(&axis[2], &bad, sizeof(bad)), "xcp: implausible write refused");
    expect((summary().writesChecked == before.writesChecked + 1U) && (summary().writesRefused == before.writesRefused + 1U),
           "xcp: counters");
    expect(hostLog.error && contains(hostLog.line, "Calibration: XCP write refused, not ascending, check 0 row 0 element 2"),
           "xcp: error line");
    expect(faultMemory.lastResult == ::dem::Result::FAILED, "xcp: DEM FAILED");
    uint16_t const good = 1500U;
    expect(acceptWrite(&axis[1], &good, sizeof(good)), "xcp: plausible write accepted");
    int16_t const wild = 30000;
    unsigned const checked = summary().writesChecked;
    expect(acceptWrite(storage<int16_t>(QM_ARR), &wild, sizeof(wild)), "xcp: QM write accepted");
    expect(summary().writesChecked == checked, "xcp: QM write not counted");
}

void console()
{
    ::util::format::SharedStringWriter out;
    print(out);
    char const* const text = out.text.c_str();
    expect(contains(text, "check 0 row 0 CalMapSwc.CalPrm.SpeedAxis_rpm: form limits ascending -> plausible"),
           "console: axis line");
    expect(contains(text, "CalMapSwc.CalPrm.TorqueLimit_Nm: form limits -> plausible"), "console: map line");
    expect(contains(text, "implausible\r\nstart-up: plausible (image)"), "console: summary and start-up");
    expect(contains(text, "XCP writes to checked parameters: 2, refused 1, last: not ascending"), "console: xcp");
}

#ifdef FLOAT_GAIN
void floats()
{
    float* const gain = storage<float>("CalMapSwc.CalPrm.Gain"); // asil_b 0 .. 2 in this variant
    float const old   = *gain;
    *gain             = 2.0F;
    expect(checkOne(4U, IN_PLACE).rule == Rule::NONE, "float: at the limit");
    *gain = std::nextafter(2.0F, 3.0F);
    expect(is(checkOne(4U, IN_PLACE), Rule::LIMITS, 4U, 0U), "float: one step above the limit");
    *gain = std::numeric_limits<float>::quiet_NaN();
    expect(is(checkOne(4U, IN_PLACE), Rule::LIMITS, 4U, 0U), "float: NaN outside the limits");
    *gain = std::numeric_limits<float>::infinity();
    expect(is(checkOne(4U, IN_PLACE), Rule::LIMITS, 4U, 0U), "float: infinity outside the limits");
    *gain = old;
    float const nan = std::numeric_limits<float>::quiet_NaN();
    expect(!acceptWrite(gain, &nan, sizeof(nan)), "float: NaN write refused");
    expect(checkAll(IN_PLACE).rule == Rule::NONE, "float: restored");
}
#endif
#else
void formBroken()
{
    expect(is(checkOne(2U, IN_PLACE), Rule::FORM, 2U, 0U), "form: wrong axis element count of the map");
    expect(is(checkOne(3U, IN_PLACE), Rule::FORM, 3U, 0U), "form: wrong count of the value");
    expect(checkOne(0U, IN_PLACE).rule == Rule::NONE, "form: the axis row is fine");
    expect(is(checkAll(IN_PLACE), Rule::FORM, 2U, 0U), "form: checkAll reports the first");
    expect(!startUp(IN_PLACE, Source::IMAGE), "form: start-up refuses");
}
#endif
} // namespace

int main()
{
    ::dem::registry.memory = &faultMemory;
#ifndef FORM_BROKEN
    generatedTable();
    limits();
    ascending();
    overlay();
    startUpAndDem();
    xcpWrites();
    console();
#ifdef FLOAT_GAIN
    floats();
#endif
#else
    formBroken();
#endif
    std::printf("plausibility host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
