#pragma once

#include <RteConfig.h> // rte::PARAMETERS, rte::PARAMETER_CHECKS (tools/rtegen)

#include <cstddef>
#include <cstdint>

/**
 * Plausibility of the safety-relevant calibration parameters (SYS-90, stage K6 of doc/architecture/kalibrierung.md).
 *
 * tools/rtegen generates one row of rte::PARAMETER_CHECKS for every calibration parameter with a safety class other
 * than QM (NX_RTE_HAS_PARAMETER_CHECKS in RteConfig.h, only then this part is compiled). A row names the row of
 * rte::PARAMETERS, the form of swc.yaml and the rules; the rules are the ones the build and tools/calgen check apply
 * to the values of a dataset (tools/rtegen value_rules), here applied to the stored values in the ECU:
 *
 *   FORM       the row of rte::PARAMETERS has the number of values of swc.yaml, a known element type and stored
 *              limits; a curve/map has axis rows of an axis kind with the element counts of swc.yaml
 *   LIMITS     every stored value inside the stored limits (Rte_Lim_*: min/max rounded inside onto the grid)
 *   ASCENDING  the breakpoints of an axis strictly ascending as stored values
 *
 * This part is pure logic - no OpenBSW, no logger, no lock -, so the host test runs the same code as the target.
 * A check reads the values through an Overlay: the bytes of [target, target + length) are taken from source
 * instead. That is how a value is checked before it takes effect: the calibration segment before it is copied into
 * the working page (target: the working page, source: the segment), and an XCP write before it is applied (target:
 * the destination, source: the received bytes). PlausibilityMonitor.h is the target side (start-up, XCP, log, DEM,
 * console).
 */
namespace calibration
{
namespace plausibility
{
enum class Rule : uint8_t
{
    NONE,      // plausible
    FORM,
    LIMITS,
    ASCENDING,
};

/** Result of a check: the first violation, rule NONE if there is none. */
struct Finding
{
    Rule rule;
    uint16_t check;   // row of rte::PARAMETER_CHECKS
    uint16_t row;     // row of rte::PARAMETERS
    uint16_t element; // index of the value (LIMITS, ASCENDING), 0 for FORM
};

/** Bytes of [target, target + length) are read from source instead; length 0: the values in place. */
struct Overlay
{
    void const* target;
    void const* source;
    size_t length;
};

constexpr Overlay IN_PLACE{nullptr, nullptr, 0U};
constexpr Finding PLAUSIBLE{Rule::NONE, 0U, 0U, 0U};

/** Number of generated checks (0 without NX_RTE_HAS_PARAMETER_CHECKS). */
size_t checkCount();

/** Check \p check (row of rte::PARAMETER_CHECKS) with its rules. */
Finding checkOne(size_t check, Overlay const& overlay);

/** Every check in the order of the table; the first violation. */
Finding checkAll(Overlay const& overlay);

/**
 * The check an XCP write of \p n bytes at \p destination with the bytes of \p source must pass, before it takes
 * effect: every checked parameter whose storage overlaps the write, with the write as overlay. PLAUSIBLE for a write
 * that touches no checked parameter.
 */
Finding checkWrite(void const* destination, void const* source, size_t n);

/** Index of the check whose parameter storage overlaps [address, address + n), checkCount() if none. */
size_t findCheck(void const* address, size_t n);

/** Name of the parameter of a check (Component.Port.Element), "" if out of range. */
char const* parameterName(size_t check);

/** Literal of \p rule (log lines and console: tools/logcheck bounds). */
char const* ruleText(Rule rule);

} // namespace plausibility
} // namespace calibration
