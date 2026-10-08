// Host stub of mcu/mcu.h for the sample group test (IO-62): the real bit macros and index names of the family (NXP
// S32K148_ADC/_PDB/_TRGMUX/_SIM/_FTM.h) with RAM models of ADC0/1, PDB0/1, TRGMUX, SIM and FTM3.
//
// Registers that need behaviour, not just storage (the test would otherwise not see the mistakes that matter):
//  - ADC SC1: reads are counted (bounded waits); SC3: a write with CAL finishes the calibration at once (COCO of SC1[0]); ADC R[m]: reading it clears COCO of
//    SC1[m] ([RM13] 44: COCO is cleared when R is read) - the PDB needs that rising edge again for its lock.
//  - PDB SC: LDOK takes MOD, IDLY and the delays over from their buffers ([RM13] 46.3.1); the model keeps what was
//    loaded, so a delay written after LDOK is not in effect. Every SC write is logged.
//  - TRGMUX: every write is logged, so the test sees the order of routing and sequencer configuration.
#pragma once

#include <cstddef>
#include <cstdint>

#define __IO volatile
#define __O volatile
#define __I volatile

#include "S32K148_ADC.h"
#include "S32K148_FTM.h"
#include "S32K148_PDB.h"
#include "S32K148_SIM.h"
#include "S32K148_TRGMUX.h"

// ---------------------------------------------------------------------------------------------- event log
enum NxTestWhat : uint8_t
{
    NX_TRGMUX_WRITE,
    NX_PDB_SC_WRITE,
};

struct NxTestEvent
{
    NxTestWhat what;
    uint8_t index; // TRGMUX register or PDB number
    uint32_t value;
};

void nxTestLog(NxTestWhat what, uint8_t index, uint32_t value);

// ---------------------------------------------------------------------------------------------- ADC
struct NxTestAdc;

struct NxTestAdcSc3
{
    uint32_t value;

    operator uint32_t() const { return value; }

    NxTestAdcSc3& operator=(uint32_t written);
};

struct NxTestAdcResult
{
    uint32_t value;

    operator uint32_t() const; // clears COCO of its slot
};

/** SC1n: every read is counted, so the test sees how long a wait for COCO lasts. */
struct NxTestAdcSc1
{
    uint32_t value;

    operator uint32_t() const;

    NxTestAdcSc1& operator=(uint32_t const written)
    {
        value = written;
        return *this;
    }

    NxTestAdcSc1& operator|=(uint32_t const bits)
    {
        value |= bits;
        return *this;
    }

    NxTestAdcSc1& operator&=(uint32_t const bits)
    {
        value &= bits;
        return *this;
    }
};

extern uint32_t nxTestSc1Reads;

struct NxTestAdc
{
    NxTestAdcSc1 SC1[ADC_SC1_COUNT];
    uint32_t CFG1;
    uint32_t CFG2;
    NxTestAdcResult R[ADC_R_COUNT];
    uint32_t SC2;
    NxTestAdcSc3 SC3;
    uint32_t calibrations; // test only
};

#define ADC_Type NxTestAdc
extern NxTestAdc fakeAdc[2];
#undef IP_ADC0
#define IP_ADC0 (&fakeAdc[0])
#undef IP_ADC1
#define IP_ADC1 (&fakeAdc[1])

// ---------------------------------------------------------------------------------------------- PDB
struct NxTestPdbSc
{
    uint32_t value;

    operator uint32_t() const { return value; }

    NxTestPdbSc& operator=(uint32_t written);
};

struct NxTestPdbChannel
{
    uint32_t C1;
    uint32_t S;
    uint32_t DLY[PDB_DLY_COUNT];
};

struct NxTestPdb
{
    NxTestPdbSc SC;
    uint32_t MOD;
    uint32_t CNT;
    uint32_t IDLY;
    NxTestPdbChannel CH[PDB_CH_COUNT];
    // loaded by LDOK (test only)
    bool loaded;
    uint32_t loadedMod;
    uint32_t loadedIdly;
    uint32_t loadedDly[PDB_DLY_COUNT];
};

#define PDB_Type NxTestPdb
extern NxTestPdb fakePdb[2];
#undef IP_PDB0
#define IP_PDB0 (&fakePdb[0])
#undef IP_PDB1
#define IP_PDB1 (&fakePdb[1])

// ---------------------------------------------------------------------------------------------- TRGMUX
struct NxTestTrgmuxReg
{
    uint32_t value;

    operator uint32_t() const { return value; }

    NxTestTrgmuxReg& operator=(uint32_t written);
};

struct NxTestTrgmux
{
    NxTestTrgmuxReg TRGMUXn[TRGMUX_TRGMUXn_COUNT];
};

#define TRGMUX_Type NxTestTrgmux
extern NxTestTrgmux fakeTrgmux;
#undef IP_TRGMUX
#define IP_TRGMUX (&fakeTrgmux)

// ---------------------------------------------------------------------------------------------- SIM, FTM
extern SIM_Type fakeSim;
#undef IP_SIM
#define IP_SIM (&fakeSim)

extern FTM_Type fakeFtm3;
#undef IP_FTM3
#define IP_FTM3 (&fakeFtm3)
