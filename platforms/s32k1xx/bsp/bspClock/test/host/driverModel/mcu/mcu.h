// Behaviour model of SCG, PCC, RCM and SysTick for the host test of the s32k1xx clock driver (PLAT-85).
//
// The clock driver (src/clockConfig.cpp) waits for hardware: SIRCVLD, FIRCVLD, SOSCVLD and SPLLVLD come some time
// after the enable, and SCG_CSR follows SCG_RCCR only for a valid source. Plain memory (model/mcu/mcu.h of the check)
// cannot do that, so here every register the driver touches is a Reg: a read or a write goes through the model in
// ClockConfigS32kHostTest.cpp, which
//   - lets an oscillator become valid after a set number of reads of its CSR while it is enabled (or never),
//   - makes the SPLL valid only with a valid SOSC as reference,
//   - keeps the enable of the clock that is the system clock (the SCG refuses to switch it off),
//   - copies SCG_RCCR to SCG_CSR only when the selected source is valid (or never, to model a switch that does not
//     happen),
//   - records every write in order, for the comparison with the unchanged OpenBSW driver.
// Field positions and masks come from the unchanged device headers: S32K148_*.h of the OpenBSW submodule or, with
// NX_TEST_S32K116, S32K116_*.h of platforms/s32k1xx/bsp/bspMcu (no SPLL, no LOL). Only the peripheral pointers are
// bent onto the model structures below, under the names the family header mcu/mcu.h gives them.

#pragma once

#include <cstdint>

#if defined(NX_TEST_S32K116)
#include "S32K116_PCC.h"
#include "S32K116_RCM.h"
#include "S32K116_SCG.h"
#else
#include "S32K148_PCC.h"
#include "S32K148_RCM.h"
#include "S32K148_SCG.h"
#endif

// As the family header platforms/s32k1xx/bsp/bspMcu/include/mcu/mcu.h: FIRCERR is missing in the S32K148 header
#ifndef SCG_FIRCCSR_FIRCERR_MASK
#define SCG_FIRCCSR_FIRCERR_MASK 0x4000000u
#endif

struct Reg;

namespace model
{
uint32_t read(Reg const& reg);
void write(Reg& reg, uint32_t value);
} // namespace model

// One register: value storage plus the read and write hooks of the model
struct Reg
{
    uint32_t value;

    operator uint32_t() const { return ::model::read(*this); }

    Reg& operator=(uint32_t const v)
    {
        ::model::write(*this, v);
        return *this;
    }

    Reg& operator=(Reg const& other) { return *this = static_cast<uint32_t>(other); }
};

struct ScgModel
{
    Reg CSR, RCCR, VCCR, HCCR, CLKOUTCNFG;
    Reg SOSCCSR, SOSCDIV, SOSCCFG;
    Reg SIRCCSR, SIRCDIV, SIRCCFG;
    Reg FIRCCSR, FIRCDIV, FIRCCFG;
    Reg SPLLCSR, SPLLDIV, SPLLCFG;
};

struct PccModel
{
    Reg PCCn[PCC_PCCn_COUNT];
};

struct RcmModel
{
    Reg SRS, RPC, SSRS, SRIE;
};

struct SysTickModel
{
    Reg CTRL, LOAD, VAL;
};

extern ScgModel scgModel;
extern PccModel pccModel;
extern RcmModel rcmModel;
extern SysTickModel sysTickModel;

#define SCG     (&scgModel)
#define PCC     (&pccModel)
#define RCM     (&rcmModel)
#define SysTick (&sysTickModel)

// Same as platforms/s32k1xx/CMakeLists.txt item 2 (forced include for a derivative without SPLL): the OpenBSW driver
// calls isSpllSysClk()/spllStop() in pllOff() outside its PLL guard (finding PLAT-11), and so does the copy.
#if !defined(SCG_SPLLCSR_SPLLEN_MASK)
#include "clock/nxSpllUnavailable.h"
#endif
