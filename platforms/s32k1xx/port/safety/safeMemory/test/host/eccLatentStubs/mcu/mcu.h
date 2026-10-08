/* SYS-73 host test stub of mcu/mcu.h for the latent-fault test of the RAM ECC (EccLatentTestHostTest.cpp): register
 * models of the EIM ([RM] Rev. 13, chapter 21) and the ERM (chapter 22), and FTFC FERSTAT for the ECC poll that runs
 * after the test. Names and masks of the S32K148 headers of OpenBSW (S32K148_EIM.h, S32K148_ERM.h: two channels);
 * with NX_TEST_ONE_ECC_CHANNEL the one-channel layout of the S32K11x (RTD S32K116_EIM.h/_ERM.h: no *1 macros).
 *
 * Model behaviour:
 * - ERM SR0 is write-one-to-clear; `stuck` makes it ignore the clear (a broken record);
 * - a write to an EIM descriptor word clears the enable of its channel ([RM] 21.3.4/21.3.5);
 * - what a read of RAM does while the EIM is on is modelled by the test's eimInjectedRead(). */

#pragma once

#include <cstdint>

#ifdef NX_TEST_ONE_ECC_CHANNEL
#define ERM_EARn_COUNT   1u
#define EIM_EICHDn_COUNT 1u
#else
#define ERM_EARn_COUNT   2u
#define EIM_EICHDn_COUNT 2u
#endif

struct ErmStatus
{
    uint32_t value;
    bool stuck;
    uint32_t writes;

    operator uint32_t() const { return value; }

    ErmStatus& operator=(uint32_t const w)
    {
        ++writes;
        if (!stuck)
        {
            value &= ~w;
        }
        return *this;
    }
};

struct ErmType
{
    uint32_t CR0;
    ErmStatus SR0;

    struct
    {
        uint32_t EAR;
    } EARn[ERM_EARn_COUNT];
};

void eimDescriptorWritten(uint32_t channel);

struct EimDescriptorWord
{
    uint32_t value;
    uint32_t channel;

    operator uint32_t() const { return value; }

    EimDescriptorWord& operator=(uint32_t const w)
    {
        value = w;
        eimDescriptorWritten(channel);
        return *this;
    }
};

struct EimType
{
    uint32_t EIMCR;
    uint32_t EICHEN;

    struct
    {
        EimDescriptorWord WORD0;
        EimDescriptorWord WORD1;
    } EICHDn[EIM_EICHDn_COUNT];
};

struct FtfcType
{
    uint8_t FERSTAT;
    uint8_t FERCNFG;
};

extern ErmType ermRegisters;
extern EimType eimRegisters;
extern FtfcType ftfcRegisters;

#define ERM    (&ermRegisters)
#define IP_EIM (&eimRegisters)
#define FTFC   (&ftfcRegisters)

#define ERM_SR0_SBC0_MASK       (0x80000000U)
#define ERM_SR0_NCE0_MASK       (0x40000000U)
#define ERM_CR0_ESCIE0_MASK     (0x80000000U)
#define ERM_CR0_ENCIE0_MASK     (0x40000000U)
#define EIM_EIMCR_GEIEN_MASK    (0x1U)
#define EIM_EICHEN_EICH0EN_MASK (0x80000000U)
#ifndef NX_TEST_ONE_ECC_CHANNEL
#define ERM_SR0_SBC1_MASK       (0x8000000U)
#define ERM_SR0_NCE1_MASK       (0x4000000U)
#define ERM_CR0_ESCIE1_MASK     (0x8000000U)
#define ERM_CR0_ENCIE1_MASK     (0x4000000U)
#define EIM_EICHEN_EICH1EN_MASK (0x40000000U)
#endif
#define FTFC_FERSTAT_DFDIF_MASK (0x2U)
