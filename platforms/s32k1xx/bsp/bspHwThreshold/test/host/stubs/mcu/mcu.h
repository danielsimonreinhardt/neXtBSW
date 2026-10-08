// Host stub of mcu/mcu.h for the hardware threshold test (SYS-86): the real register layouts and bit macros of the
// family (NXP S32K148_FTM/_CMP/_TRGMUX/_SIM.h) with RAM models of FTM0..FTM3, CMP0, TRGMUX and SIM.
//
// Two registers need behaviour, not just storage:
//  - TRGMUX: LK (bit 31) can be written once and freezes the whole register until reset. A model that stored every
//    write would hide the one ordering mistake that matters - locking before the selection is written.
//  - CMP0 C0: the write that switches the comparator on takes a snapshot of the FTM and SIM state, so the test can
//    tell whether the comparator was prepared before the FTM configuration enabled fault control.
#pragma once

#include <cstddef>
#include <cstdint>

#define __IO volatile
#define __O volatile
#define __I volatile

#include "S32K148_CMP.h"
#include "S32K148_FTM.h"
#include "S32K148_SIM.h"
#include "S32K148_TRGMUX.h"

using NxVendorTrgmux = TRGMUX_Type;
using NxVendorCmp    = CMP_Type;

extern FTM_Type fakeFtm[4];
#define FTM0 (&fakeFtm[0])
#define FTM1 (&fakeFtm[1])
#define FTM2 (&fakeFtm[2])
#define FTM3 (&fakeFtm[3])

extern SIM_Type fakeSim;
#define IP_SIM (&fakeSim)

/** One TRGMUX register: LK written once freezes SEL0..SEL3 and LK until reset ([RM13] 19.4.1.2.4). */
struct NxTestTrgmuxReg
{
    uint32_t value;
    uint32_t writes;
    uint32_t ignoredWrites;

    operator uint32_t() const { return value; }

    NxTestTrgmuxReg& operator=(uint32_t const written)
    {
        ++writes;
        if ((value & TRGMUX_TRGMUXn_LK_MASK) != 0U)
        {
            ++ignoredWrites;
        }
        else
        {
            value = written & (TRGMUX_TRGMUXn_LK_MASK | TRGMUX_TRGMUXn_SEL3_MASK | TRGMUX_TRGMUXn_SEL2_MASK
                               | TRGMUX_TRGMUXn_SEL1_MASK | TRGMUX_TRGMUXn_SEL0_MASK);
        }
        return *this;
    }
};

struct NxTestTrgmux
{
    NxTestTrgmuxReg TRGMUXn[TRGMUX_TRGMUXn_COUNT];
};

#define TRGMUX_Type NxTestTrgmux
extern NxTestTrgmux fakeTrgmux;
#define IP_TRGMUX (&fakeTrgmux)

/** CMP state the last time the comparator was switched on (C0 written with EN). SYS-128: the start test switches it
 * on twice before prepare() does, so the snapshot is the last one - the configured switch-on of prepare(). */
struct NxTestCmpEnable
{
    bool seen;
    uint32_t c1;         // C1 at that moment: inputs and DAC level already set
    uint32_t ftmFltctrl; // FLTCTRL of every FTM at that moment (0: fault control not configured yet)
    uint32_t ftmSc;
    uint32_t count;      // switch-ons so far (SYS-128: two of the start test, one of prepare())
};

extern NxTestCmpEnable cmpEnable;

/** SYS-128: COUT as the hardware forms it from C0/C1 and the pin (defined by the test: comparator model). */
bool nxTestCout();

struct NxTestCmpC0
{
    uint32_t value;

    // COUT (bit 24) is read only: formed by the model at every read, writes to it are ignored ([RM13] 45.8.1)
    operator uint32_t() const { return (value & ~0x01000000U) | (nxTestCout() ? 0x01000000U : 0U); }

    NxTestCmpC0& operator=(uint32_t const written);
};

struct NxTestCmp
{
    NxTestCmpC0 C0;
    uint32_t C1;
    uint32_t C2;
};

static_assert(sizeof(NxTestCmp) == sizeof(NxVendorCmp), "CMP model has another size than the vendor header");
static_assert(offsetof(NxTestCmp, C1) == offsetof(NxVendorCmp, C1), "C1 at another offset");

#define CMP_Type NxTestCmp
extern NxTestCmp fakeCmp0;
#define IP_CMP0 (&fakeCmp0)
