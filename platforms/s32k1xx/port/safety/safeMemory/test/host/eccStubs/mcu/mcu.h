/* SYS-41 host test stub of mcu/mcu.h for the ECC poll: register models of the S32K1 ERM and of FTFC FERSTAT with
 * their write-one-to-clear behaviour ([RM] Rev. 13, 22.3.3 and 36.4.4.1.11), and the MPU CESR SafeMemory.cpp
 * reads for its log context. Names and masks of external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/
 * nxp/S32K148_ERM.h and S32K148_FTFC.h (two ERM channels, as on the S32K14x).
 *
 * A W1C register records every write, clears exactly the bits written as one, and can make bits appear right
 * after the next read (an event that arrives between the poll's read and its clear). */

#pragma once

#include <cstdint>

template<typename T>
struct W1cRegister
{
    T value;
    T setAfterRead;
    uint32_t writes;
    uint32_t lastWrite;

    operator T()
    {
        T const v    = value;
        value        = static_cast<T>(value | setAfterRead);
        setAfterRead = 0U;
        return v;
    }

    W1cRegister& operator=(uint32_t const w)
    {
        value     = static_cast<T>(value & ~w);
        lastWrite = w;
        ++writes;
        return *this;
    }
};

#define ERM_EARn_COUNT 2u

struct ErmType
{
    uint32_t CR0;
    W1cRegister<uint32_t> SR0;

    struct
    {
        uint32_t EAR;
    } EARn[ERM_EARn_COUNT];
};

struct FtfcType
{
    W1cRegister<uint8_t> FERSTAT;
    uint8_t FERCNFG;
};

struct MpuType
{
    uint32_t CESR;
};

extern ErmType ermRegisters;
extern FtfcType ftfcRegisters;
extern MpuType mpuRegisters;

#define ERM    (&ermRegisters)
#define FTFC   (&ftfcRegisters)
#define IP_MPU (&mpuRegisters)

#define ERM_SR0_NCE1_MASK       (0x4000000U)
#define ERM_SR0_SBC1_MASK       (0x8000000U)
#define ERM_SR0_NCE0_MASK       (0x40000000U)
#define ERM_SR0_SBC0_MASK       (0x80000000U)
#define FTFC_FERSTAT_DFDIF_MASK (0x2U)
