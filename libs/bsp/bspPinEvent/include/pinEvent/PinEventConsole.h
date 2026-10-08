// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "pinEvent/PinEvent.h"

/**
 * Text of the console group "pinevent" (IO-53), independent of OpenBSW: the lines go through any object with a
 * printf member (util::format::SharedStringWriter on the target, PinEventCommand), host test
 * test/host/PinEventHostTest.cpp. Reading the values is lock free (PinEvents::statistics), so the command may run
 * in the console context without a hop into the owning task.
 *
 * Numbers go out as %u with uint32_t, never %lu (IO-60): util::format reads 'l' as 64 bit, on the 32-bit target
 * that shifted every following argument (board: "debounce 85899347920 us", "qlf <NULL>"). Host test with the real
 * formatter and the target's argument layout: test/host/PinEventConsoleFormatHostTest.cpp; rule for all sources:
 * tools/fmtcheck.
 */
namespace pinevent
{
namespace console
{

inline char const* qualityName(uint8_t const quality)
{
    switch (quality)
    {
        case QUALITY_OK:    return "OK";
        case QUALITY_ERROR: return "ERROR";
        default:            return "DEBOUNCE";
    }
}

inline char const* detectName(Detect const edges)
{
    switch (edges)
    {
        case Detect::ACTIVATE:   return "activate";
        case Detect::DEACTIVATE: return "deactivate";
        case Detect::BOTH:       return "both";
        default:                 return "none";
    }
}

/** "pinevent show": per input counter, last time stamp, qualifier, trips, bounces, glitches and arm state. */
template<class Out>
void writeShow(Out& out, PinEvents const& events)
{
    (void)out.printf("pin events: %u\r\n", static_cast<unsigned>(events.count()));
    for (uint8_t i = 0U; i < events.count(); ++i)
    {
        char const* const name = events.config(i).name;
        Statistics s{};
        if (!events.statistics(i, s))
        {
            (void)out.printf("%s: busy (an edge during every copy)\r\n", name);
            continue;
        }
        (void)out.printf(
            "%s: count %u, last %u us, qlf %s (%u), trips %u, bounces %u, glitches %u, ",
            name,
            static_cast<uint32_t>(s.reading.count),
            static_cast<uint32_t>(s.reading.timeUs),
            qualityName(s.reading.quality),
            static_cast<uint32_t>(s.reading.quality),
            static_cast<uint32_t>(s.trips),
            static_cast<uint32_t>(s.bounces),
            static_cast<uint32_t>(s.glitches));
        if (s.armed)
        {
            (void)out.printf("armed\r\n");
        }
        else if (s.reading.quality == QUALITY_ERROR)
        {
            (void)out.printf("off, re-arm in %u ms\r\n", static_cast<uint32_t>((s.reArmInUs + 999U) / 1000U));
        }
        else
        {
            (void)out.printf("not started\r\n");
        }
    }
}

/** "pinevent config": the generated parameters per input and what the edge detector reports. */
template<class Out>
void writeConfig(Out& out, PinEvents const& events)
{
    for (uint8_t i = 0U; i < events.count(); ++i)
    {
        PinEventConfig const& c = events.config(i);
        (void)out.printf(
            "%s: edge %s, debounce %u us, limit %u per %u us, re-arm after %u us, detector %s\r\n",
            c.name,
            detectName(static_cast<Detect>(static_cast<uint8_t>(c.edge))),
            static_cast<uint32_t>(c.debounceUs),
            static_cast<uint32_t>(c.maxEvents),
            static_cast<uint32_t>(c.windowUs),
            static_cast<uint32_t>(c.rearmUs),
            detectName(detectorEdges(c)));
    }
}

} // namespace console
} // namespace pinevent
