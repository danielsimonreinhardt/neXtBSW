/* ARCH-14 host test stub for mcu/mcu.h: register model of DWT, CoreDebug and LPIT0 as used by SystemTimer.cpp.
 * The DWT cycle counter is a class so that the test sees every read and write (SystemTimerHostTest.cpp). */

#pragma once

#include <cstdint>

// ARCH-24: the real mcu/mcu.h includes the generated mcu/derivativeFacts.h with the core architecture; the profiles
// of this test count DWT cycles (ARMv7-M) unless the command line selects another architecture.
#if !defined(NX_CORE_ARCHITECTURE_ARMV6M) && !defined(NX_CORE_ARCHITECTURE_ARMV7M) \
    && !defined(NX_CORE_ARCHITECTURE_ARMV8M) && !defined(NX_CORE_ARCHITECTURE_ARMV7R)
#define NX_CORE_ARCHITECTURE_ARMV7M 1
#endif

namespace systemTimerModel
{
// Reads and writes of DWT->CYCCNT go to the simulation in SystemTimerHostTest.cpp
uint32_t readCycCnt();
void writeCycCnt(uint32_t value);

struct CycCntRegister
{
    operator uint32_t() const { return readCycCnt(); }

    CycCntRegister& operator=(uint32_t const value)
    {
        writeCycCnt(value);
        return *this;
    }
};

struct PlainRegister
{
    uint32_t value;

    operator uint32_t() const { return value; }

    PlainRegister& operator=(uint32_t const v)
    {
        value = v;
        return *this;
    }
};

struct DwtModel
{
    PlainRegister CTRL;
    CycCntRegister CYCCNT;
};

struct CoreDebugModel
{
    PlainRegister DEMCR;
};

struct LpitTimerModel
{
    uint32_t TVAL;
    uint32_t CVAL;
    uint32_t TCTRL;
};

struct LpitModel
{
    uint32_t MCR;
    uint32_t SETTEN;
    LpitTimerModel TMR[4];
};

extern DwtModel dwt;
extern CoreDebugModel coreDebug;
extern LpitModel lpit0;
} // namespace systemTimerModel

#define DWT       (&::systemTimerModel::dwt)
#define CoreDebug (&::systemTimerModel::coreDebug)
#define LPIT0     (&::systemTimerModel::lpit0)
