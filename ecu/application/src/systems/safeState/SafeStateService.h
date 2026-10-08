// SYS-141: the selection service of the family port (SYS-116) as the `Service` policy of safeState/SafeStateRte.h.
//
// The interface is the C header hardFaultHandler/safeStateSelection.h that the family port provides together with the
// service (S32K1: platforms/s32k1xx/hardFaultHandler, library nxSafeOutputSelection, linked only with
// NX_SAFE_OUTPUT_SELECT). A family that offers the active short provides the same header and functions; tools/rtegen
// generates the ports and the start test only for a project in which a group allows a short, so this header is
// included only then (RteSystem.cpp, NX_RTE_HAS_SAFE_STATE of the generated RteConfig.h).

#pragma once

#include "SafeStateRte.h"

#include <hardFaultHandler/safeStateSelection.h>

#include <cstdint>

namespace safestate
{
static_assert(
    (FREEWHEEL == NX_SAFE_STATE_FREEWHEEL) && (SHORT_LOW == NX_SAFE_STATE_SHORT_LOW)
        && (SHORT_HIGH == NX_SAFE_STATE_SHORT_HIGH) && (OK == NX_SAFE_STATE_OK),
    "SYS-141: the numbers of the RTE ports are the service's (tools/rtegen SAFE_STATE_NUMBERS)");

struct FamilyService
{
    static int32_t select(uint32_t const group, uint32_t const state) { return ::nxSafeStateSelect(group, state); }

    static uint32_t selected(uint32_t const group) { return ::nxSafeStateSelected(group); }

    static uint32_t allowed(uint32_t const group) { return ::nxSafeStateAllowed(group); }

    static void blockShort(uint32_t const group) { ::nxSafeStateBlockShort(group); }

    static int32_t testBegin(uint32_t const group, uint32_t const state)
    {
        return ::nxSafeStateShortTestBegin(group, state);
    }

    static int32_t testEnd(uint32_t const group) { return ::nxSafeStateShortTestEnd(group); }
};

} // namespace safestate
