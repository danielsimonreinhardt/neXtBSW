#include "calibration/Plausibility.h"

#include <cstring>

namespace calibration
{
namespace plausibility
{
#ifdef NX_RTE_HAS_PARAMETER_CHECKS
namespace
{
using ::rte::Parameter;
using ::rte::ParameterCheck;
using ::rte::ParameterKind;
using ::rte::SignalType;

uintptr_t address(void const* const pointer) { return reinterpret_cast<uintptr_t>(pointer); }

uint8_t elementSize(SignalType const type)
{
    switch (type)
    {
        case SignalType::BOOLEAN:
        case SignalType::UINT8:
        case SignalType::SINT8: return 1U;
        case SignalType::UINT16:
        case SignalType::SINT16: return 2U;
        case SignalType::UINT32:
        case SignalType::SINT32:
        case SignalType::FLOAT32: return 4U;
        case SignalType::FLOAT64: return 8U;
        default: return 0U;
    }
}

bool isFloat(SignalType const type) { return (type == SignalType::FLOAT32) || (type == SignalType::FLOAT64); }

bool isAxis(ParameterKind const kind) { return (kind == ParameterKind::AXIS) || (kind == ParameterKind::OWN_AXIS); }

/** One stored value as a number that keeps every value of its type: integers exactly, floats as double. */
struct Number
{
    int64_t integer;
    double real;
};

Number decode(SignalType const type, uint8_t const* const bytes)
{
    Number n{0, 0.0};
    switch (type)
    {
        case SignalType::BOOLEAN:
        case SignalType::UINT8: n.integer = bytes[0]; break;
        case SignalType::SINT8:
        {
            int8_t v = 0;
            (void)::std::memcpy(&v, bytes, sizeof(v));
            n.integer = v;
            break;
        }
        case SignalType::UINT16:
        {
            uint16_t v = 0U;
            (void)::std::memcpy(&v, bytes, sizeof(v));
            n.integer = v;
            break;
        }
        case SignalType::SINT16:
        {
            int16_t v = 0;
            (void)::std::memcpy(&v, bytes, sizeof(v));
            n.integer = v;
            break;
        }
        case SignalType::UINT32:
        {
            uint32_t v = 0U;
            (void)::std::memcpy(&v, bytes, sizeof(v));
            n.integer = v;
            break;
        }
        case SignalType::SINT32:
        {
            int32_t v = 0;
            (void)::std::memcpy(&v, bytes, sizeof(v));
            n.integer = v;
            break;
        }
        case SignalType::FLOAT32:
        {
            float v = 0.0F;
            (void)::std::memcpy(&v, bytes, sizeof(v));
            n.real = static_cast<double>(v);
            break;
        }
        case SignalType::FLOAT64:
        {
            double v = 0.0;
            (void)::std::memcpy(&v, bytes, sizeof(v));
            n.real = v;
            break;
        }
        default: break;
    }
    return n;
}

/** Element \p index of \p p as the ECU would read it with \p overlay applied. */
Number element(Parameter const& p, uint8_t const size, size_t const index, Overlay const& overlay)
{
    uint8_t bytes[8] = {0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
    uintptr_t const at     = address(p.value) + (index * size);
    uintptr_t const target = address(overlay.target);
    auto const* const source = static_cast<uint8_t const*>(overlay.source);
    for (uint8_t i = 0U; i < size; ++i)
    {
        uintptr_t const byte = at + i;
        bool const replaced  = (overlay.length != 0U) && (byte >= target) && ((byte - target) < overlay.length);
        bytes[i] = replaced ? source[byte - target] : *reinterpret_cast<uint8_t const*>(byte);
    }
    return decode(p.type, bytes);
}

/** lo <= v <= hi, false for NaN - as the generator compares (tools/rtegen check_stored_values). */
bool inside(SignalType const type, Number const& v, Number const& lo, Number const& hi)
{
    if (isFloat(type))
    {
        return (lo.real <= v.real) && (v.real <= hi.real);
    }
    return (lo.integer <= v.integer) && (v.integer <= hi.integer);
}

/** v <= previous: not strictly ascending, as the generator compares (NaN is caught by LIMITS). */
bool notAbove(SignalType const type, Number const& v, Number const& previous)
{
    if (isFloat(type))
    {
        return v.real <= previous.real;
    }
    return v.integer <= previous.integer;
}

bool formFits(ParameterCheck const& c)
{
    if (c.row >= ::rte::PARAMETER_COUNT)
    {
        return false;
    }
    Parameter const& p = ::rte::PARAMETERS[c.row];
    if ((p.count != c.count) || (p.count == 0U) || (elementSize(p.type) == 0U))
    {
        return false;
    }
    if (((c.rules & ::rte::CHECK_LIMITS) != 0U) && (p.limits == nullptr))
    {
        return false;
    }
    if (((c.rules & ::rte::CHECK_ASCENDING) != 0U) && !isAxis(p.kind))
    {
        return false;
    }
    bool const curveOrMap = (p.kind == ParameterKind::CURVE) || (p.kind == ParameterKind::MAP);
    if (c.shape[0] == 0U)
    {
        return !curveOrMap && (c.shape[1] == 0U);
    }
    size_t const axes = (c.shape[1] == 0U) ? 1U : 2U;
    if (!curveOrMap || ((p.kind == ParameterKind::MAP) != (axes == 2U)))
    {
        return false;
    }
    size_t product = 1U;
    for (size_t k = 0U; k < axes; ++k)
    {
        uint16_t const axis = p.axis[k];
        if ((axis >= ::rte::PARAMETER_COUNT) || !isAxis(::rte::PARAMETERS[axis].kind)
            || (::rte::PARAMETERS[axis].count != c.shape[k]))
        {
            return false;
        }
        product *= c.shape[k];
    }
    return product == p.count;
}
} // namespace

size_t checkCount() { return ::rte::PARAMETER_CHECK_COUNT; }

Finding checkOne(size_t const check, Overlay const& overlay)
{
    if (check >= ::rte::PARAMETER_CHECK_COUNT)
    {
        return Finding{Rule::FORM, static_cast<uint16_t>(check), 0xFFFFU, 0U};
    }
    ParameterCheck const& c = ::rte::PARAMETER_CHECKS[check];
    Finding finding{Rule::NONE, static_cast<uint16_t>(check), c.row, 0U};
    if (!formFits(c))
    {
        finding.rule = Rule::FORM; // the form is always checked: the rules below rely on it
        return finding;
    }
    Parameter const& p   = ::rte::PARAMETERS[c.row];
    uint8_t const size   = elementSize(p.type);
    bool const limits    = (c.rules & ::rte::CHECK_LIMITS) != 0U;
    bool const ascending = (c.rules & ::rte::CHECK_ASCENDING) != 0U;
    Number lo{0, 0.0};
    Number hi{0, 0.0};
    if (limits)
    {
        auto const* const stored = static_cast<uint8_t const*>(p.limits); // const, never part of an overlay
        lo = decode(p.type, stored);
        hi = decode(p.type, &stored[size]);
    }
    Number previous{0, 0.0};
    for (size_t i = 0U; i < p.count; ++i)
    {
        Number const v = element(p, size, i, overlay);
        if (limits && !inside(p.type, v, lo, hi))
        {
            finding.rule    = Rule::LIMITS;
            finding.element = static_cast<uint16_t>(i);
            return finding;
        }
        if (ascending && (i > 0U) && notAbove(p.type, v, previous))
        {
            finding.rule    = Rule::ASCENDING;
            finding.element = static_cast<uint16_t>(i);
            return finding;
        }
        previous = v;
    }
    return finding;
}

Finding checkAll(Overlay const& overlay)
{
    for (size_t i = 0U; i < ::rte::PARAMETER_CHECK_COUNT; ++i)
    {
        Finding const finding = checkOne(i, overlay);
        if (finding.rule != Rule::NONE)
        {
            return finding;
        }
    }
    return PLAUSIBLE;
}

size_t findCheck(void const* const at, size_t const n)
{
    uintptr_t const begin = address(at);
    for (size_t i = 0U; i < ::rte::PARAMETER_CHECK_COUNT; ++i)
    {
        uint16_t const row = ::rte::PARAMETER_CHECKS[i].row;
        if (row >= ::rte::PARAMETER_COUNT)
        {
            continue;
        }
        Parameter const& p    = ::rte::PARAMETERS[row];
        uintptr_t const start = address(p.value);
        uintptr_t const end   = start + (static_cast<uintptr_t>(elementSize(p.type)) * p.count);
        if ((begin < end) && (start < (begin + n)))
        {
            return i;
        }
    }
    return ::rte::PARAMETER_CHECK_COUNT;
}

Finding checkWrite(void const* const destination, void const* const source, size_t const n)
{
    Overlay const overlay{destination, source, n};
    uintptr_t const begin = address(destination);
    // a write covers one region of the XCP map (one row of rte::PARAMETERS); every overlapping check anyway
    for (size_t i = 0U; i < ::rte::PARAMETER_CHECK_COUNT; ++i)
    {
        uint16_t const row = ::rte::PARAMETER_CHECKS[i].row;
        if (row < ::rte::PARAMETER_COUNT)
        {
            Parameter const& p    = ::rte::PARAMETERS[row];
            uintptr_t const start = address(p.value);
            uintptr_t const end   = start + (static_cast<uintptr_t>(elementSize(p.type)) * p.count);
            if (!((begin < end) && (start < (begin + n))))
            {
                continue;
            }
        }
        Finding const finding = checkOne(i, overlay);
        if (finding.rule != Rule::NONE)
        {
            return finding;
        }
    }
    return PLAUSIBLE;
}

char const* parameterName(size_t const check)
{
    if ((check >= ::rte::PARAMETER_CHECK_COUNT) || (::rte::PARAMETER_CHECKS[check].row >= ::rte::PARAMETER_COUNT))
    {
        return "";
    }
    return ::rte::PARAMETERS[::rte::PARAMETER_CHECKS[check].row].name;
}

#else // no parameter with a safety class other than QM: nothing to check

size_t checkCount() { return 0U; }

Finding checkOne(size_t const check, Overlay const&)
{
    return Finding{Rule::FORM, static_cast<uint16_t>(check), 0xFFFFU, 0U};
}

Finding checkAll(Overlay const&) { return PLAUSIBLE; }

Finding checkWrite(void const*, void const*, size_t) { return PLAUSIBLE; }

size_t findCheck(void const*, size_t) { return 0U; }

char const* parameterName(size_t) { return ""; }

#endif

char const* ruleText(Rule const rule)
{
    switch (rule)
    {
        case Rule::NONE: return "plausible";
        case Rule::FORM: return "form";
        case Rule::LIMITS: return "outside limits";
        case Rule::ASCENDING: return "not ascending";
        default: return "?";
    }
}

} // namespace plausibility
} // namespace calibration
