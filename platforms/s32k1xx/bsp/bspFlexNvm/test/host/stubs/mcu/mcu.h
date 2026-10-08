// Host stub of mcu/mcu.h for the FlexNVM tests (NVM-02, NVM-07, NVM-10): register model of SIM FCFG1/SDID and FTFC
// FSTAT/FCNFG/FCCOB. FCNFG answers from a script (EEERDY per read); FSTAT is write-1-to-clear, and writing CCIF launches the
// command in FCCOB through a hook (the flash module model of FlexNvmPartitionHostTest.cpp). Masks as in the NXP
// headers (S32K148.h).

#pragma once

#include <cstdint>

struct FakeReg8
{
    // value of the next read; the test sets a hook to script it
    uint8_t (*onRead)(FakeReg8 const&) = nullptr;
    // called after a write with the written value (launch of a flash command)
    void (*onWrite)(FakeReg8&, uint8_t)  = nullptr;
    uint8_t value                        = 0U;
    mutable uint32_t reads               = 0U;
    uint32_t writes                      = 0U;
    uint8_t lastWritten                  = 0U;

    operator uint8_t() const
    {
        ++reads;
        return (onRead != nullptr) ? onRead(*this) : value;
    }

    FakeReg8& operator=(uint8_t const v)
    {
        ++writes;
        lastWritten = v;
        value       = static_cast<uint8_t>(value & static_cast<uint8_t>(~v)); // write 1 to clear
        if (onWrite != nullptr)
        {
            onWrite(*this, v);
        }
        return *this;
    }
};

#define FTFC_FCCOB_COUNT 12U

struct FakeFtfc
{
    FakeReg8 FSTAT;
    FakeReg8 FCNFG;
    uint8_t volatile FCCOB[FTFC_FCCOB_COUNT] = {};
};

struct FakeSim
{
    uint32_t SDID  = 0U;
    uint32_t FCFG1 = 0xFFFFFFFFU;
};

extern FakeFtfc fakeFtfc;
extern FakeSim fakeSim;

#define FTFC (&fakeFtfc)
#define SIM  (&fakeSim)

#define FTFC_FSTAT_MGSTAT0_MASK    (0x1U)
#define FTFC_FSTAT_FPVIOL_MASK     (0x10U)
#define FTFC_FSTAT_ACCERR_MASK     (0x20U)
#define FTFC_FSTAT_RDCOLERR_MASK   (0x40U)
#define FTFC_FSTAT_CCIF_MASK       (0x80U)
#define FTFC_FSTAT_FPVIOL(x)       (static_cast<uint8_t>(((x) << 4U) & FTFC_FSTAT_FPVIOL_MASK))
#define FTFC_FSTAT_ACCERR(x)       (static_cast<uint8_t>(((x) << 5U) & FTFC_FSTAT_ACCERR_MASK))
#define FTFC_FSTAT_RDCOLERR(x)     (static_cast<uint8_t>(((x) << 6U) & FTFC_FSTAT_RDCOLERR_MASK))
#define FTFC_FSTAT_CCIF(x)         (static_cast<uint8_t>(((x) << 7U) & FTFC_FSTAT_CCIF_MASK))
#define FTFC_FCNFG_EEERDY_MASK     (0x1U)
#define FTFC_FCNFG_RAMRDY_MASK     (0x2U)
#define SIM_FCFG1_DEPART_MASK      (0xF000U)
#define SIM_FCFG1_DEPART_SHIFT     (12U)
#define SIM_FCFG1_EEERAMSIZE_MASK  (0xF0000U)
#define SIM_FCFG1_EEERAMSIZE_SHIFT (16U)
#define SIM_SDID_FEATURES_MASK     (0xFFU)
#define SIM_SDID_FEATURES_SHIFT    (0U)
#define SIM_SDID_PACKAGE_MASK      (0xF00U)
#define SIM_SDID_PACKAGE_SHIFT     (8U)
#define SIM_SDID_REVID_MASK        (0xF000U)
#define SIM_SDID_REVID_SHIFT       (12U)
#define SIM_SDID_RAMSIZE_MASK      (0xF0000U)
#define SIM_SDID_RAMSIZE_SHIFT     (16U)
#define SIM_SDID_DERIVATE_MASK     (0xF00000U)
#define SIM_SDID_DERIVATE_SHIFT    (20U)
#define SIM_SDID_SUBSERIES_MASK    (0xF000000U)
#define SIM_SDID_SUBSERIES_SHIFT   (24U)

// NVM-29: MSCM OCMDRn (on-chip memory descriptors, [RM13] 34.4.2.18-20) - plain read/write with a hook after a write
// (the FMC model of FlexNvmDataFlashHostTest.cpp clears its speculation buffer there); RO (bit 16) locks bits 11:0.
struct FakeReg32
{
    void (*onWrite)(FakeReg32&) = nullptr;
    uint32_t value              = 0U;
    uint32_t writes             = 0U;

    operator uint32_t() const { return value; }

    FakeReg32& operator=(uint32_t const v)
    {
        ++writes;
        if ((value & 0x10000U) == 0U)
        {
            value = (value & ~0x10FFFU) | (v & 0x10FFFU);
        }
        if (onWrite != nullptr)
        {
            onWrite(*this);
        }
        return *this;
    }
};

struct FakeMscm
{
    FakeReg32 OCMDR[3];
};

inline FakeMscm fakeMscm;

#define IP_MSCM (&fakeMscm)

#define MSCM_OCMDR_OCM1_MASK  (0x30U)
#define MSCM_OCMDR_OCM1_SHIFT (4U)
#define MSCM_OCMDR_OCMT_MASK  (0xE000U)
#define MSCM_OCMDR_OCMT_SHIFT (13U)
#define MSCM_OCMDR_RO_MASK    (0x10000U)

// NVM-29: reads of the FlexNVM go through the model of the FMC buffers when a test sets fakeFlexNvmRead (else plain)
#define NX_FLEXNVM_READ_MODEL

inline uint8_t (*fakeFlexNvmRead)(uintptr_t) = nullptr;

namespace platform
{
inline uint8_t readFlexNvm(uintptr_t const address)
{
    return (fakeFlexNvmRead != nullptr) ? fakeFlexNvmRead(address)
                                        : *reinterpret_cast<uint8_t const volatile*>(address);
}
} // namespace platform
