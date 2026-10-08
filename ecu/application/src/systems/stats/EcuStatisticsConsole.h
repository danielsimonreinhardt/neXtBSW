// NVM-05: text of the console command "ecustat" (operating time and reset statistics). The snapshot is taken in the
// NvM context (stats/EcuStatisticsNvm.h), the text is written in the console context. Out: anything with
// printf(char const*, ...) (util::format::SharedStringWriter on the target, a string in the host test). Lines stay
// below 128 bytes, numbers and constant names only.

#pragma once

#include "EcuStatisticsNvm.h"

#include <cstdint>

namespace ecustats
{
template<class Out>
void writeStatistics(Out& out, Snapshot const& s)
{
    Record const& r      = s.record;
    uint32_t const hours = r.operatingSeconds / 3600U;
    uint32_t const mins  = (r.operatingSeconds % 3600U) / 60U;
    (void)out.printf(
        "operating time %u h %02u min (%u s), starts %u\r\n",
        static_cast<unsigned>(hours),
        static_cast<unsigned>(mins),
        static_cast<unsigned>(r.operatingSeconds),
        static_cast<unsigned>(r.starts));
    (void)out.printf(
        "record: %s, %s, %u s not handed over, %u hand-overs\r\n",
        bindingName(s.binding),
        originName(s.origin),
        static_cast<unsigned>(s.unsavedSeconds),
        static_cast<unsigned>(s.handovers));
    if (s.binding == Binding::BOUND)
    {
        (void)out.printf(
            "block: %s, write %s min interval %u ms, writes %u, errors %u%s\r\n",
            s.blockState,
            s.policy,
            static_cast<unsigned>(s.minIntervalMs),
            static_cast<unsigned>(s.writes),
            static_cast<unsigned>(s.errors),
            s.pending ? ", pending" : "");
    }
    (void)out.printf(
        "this start: %s, MCU 0x%08x, supply 0x%08x%s\r\n",
        s.startCounted ? "counted" : "not counted yet",
        static_cast<unsigned>(r.lastMcuReasons),
        static_cast<unsigned>(r.lastSupplyReasons),
        (!s.supplyCounted)    ? " (supply share not reported yet)"
        : s.supplyRestarted ? ""
                            : " (supply did not restart: MCU-only start)");
    (void)out.printf("cause   ");
    for (size_t c = 0U; c < CAUSE_COUNT; ++c)
    {
        (void)out.printf(" %8s", causeName(c));
    }
    (void)out.printf("\r\nMCU     ");
    for (size_t c = 0U; c < CAUSE_COUNT; ++c)
    {
        (void)out.printf(" %8u", static_cast<unsigned>(r.mcu[c]));
    }
    (void)out.printf("\r\nsupply  ");
    for (size_t c = 0U; c < CAUSE_COUNT; ++c)
    {
        (void)out.printf(" %8u", static_cast<unsigned>(r.supply[c]));
    }
    (void)out.printf("\r\n");
}

} // namespace ecustats
