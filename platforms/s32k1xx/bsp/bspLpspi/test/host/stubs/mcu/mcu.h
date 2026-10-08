// SPDX-License-Identifier: Apache-2.0
// Host stub of mcu/mcu.h for the LPSPI host test (IO-25): the LPSPI register layout and bit macros of the family
// (NXP S32K148_LPSPI.h) with a behavioural model behind the registers the driver uses. The model follows the
// S32K1xx Reference Manual Rev. 13 (doc/hardware/s32k1xx/S32K-RM.pdf), chapter 51 LPSPI, independent of the driver:
// - TDR write pushes a word into the transmit FIFO; a frame of TCR[FRAMESZ] + 1 bits above 32 bits consists of
//   32-bit words with the remainder bits in the last word (51.3.1.15.4), each word MSB first (TCR[LSBF] 0, 51.4)
// - RDR read pops the receive FIFO, FSR[RXCOUNT] counts it
// - SR[TCF], SR[TEF], SR[REF] are write-1-to-clear, CR[RTF]/CR[RRF] reset the FIFOs
// The layout of the model is compared with the vendor header below; the test owns the model (LpspiHostTest.cpp).
#pragma once

#include <cstddef>
#include <cstdint>

#define __IO volatile
#define __O volatile
#define __I volatile

#include "S32K148_LPSPI.h"

using NxVendorLpspi = LPSPI_Type;

// Implemented by the test (LpspiHostTest.cpp), one model instance.
uint32_t nxTestLpspiReadSr();
void nxTestLpspiWriteSr(uint32_t value);
uint32_t nxTestLpspiReadFsr();
void nxTestLpspiWriteCr(uint32_t value);
uint32_t nxTestLpspiReadCr();
void nxTestLpspiWriteTcr(uint32_t value);
uint32_t nxTestLpspiReadTcr();
void nxTestLpspiWriteTdr(uint32_t value);
uint32_t nxTestLpspiReadRdr();

struct NxTestCr
{
    uint32_t unused;
    operator uint32_t() const { return nxTestLpspiReadCr(); }
    NxTestCr& operator=(uint32_t const value)
    {
        nxTestLpspiWriteCr(value);
        return *this;
    }
};

struct NxTestSr
{
    uint32_t unused;
    operator uint32_t() const { return nxTestLpspiReadSr(); }
    NxTestSr& operator=(uint32_t const value)
    {
        nxTestLpspiWriteSr(value);
        return *this;
    }
};

struct NxTestFsr
{
    uint32_t unused;
    operator uint32_t() const { return nxTestLpspiReadFsr(); }
};

struct NxTestTcr
{
    uint32_t unused;
    operator uint32_t() const { return nxTestLpspiReadTcr(); }
    NxTestTcr& operator=(uint32_t const value)
    {
        nxTestLpspiWriteTcr(value);
        return *this;
    }
};

struct NxTestTdr
{
    uint32_t unused;
    NxTestTdr& operator=(uint32_t const value)
    {
        nxTestLpspiWriteTdr(value);
        return *this;
    }
};

struct NxTestRdr
{
    uint32_t unused;
    operator uint32_t() const { return nxTestLpspiReadRdr(); }
};

/** Model of one LPSPI: fields and offsets of the vendor header, behaviour in the functions above. */
struct NxTestLpspi
{
    uint32_t VERID;
    uint32_t PARAM;
    uint8_t RESERVED_0[8];
    NxTestCr CR;
    NxTestSr SR;
    uint32_t IER;
    uint32_t DER;
    uint32_t CFGR0;
    uint32_t CFGR1;
    uint8_t RESERVED_1[8];
    uint32_t DMR0;
    uint32_t DMR1;
    uint8_t RESERVED_2[8];
    uint32_t CCR;
    uint8_t RESERVED_3[20];
    uint32_t FCR;
    NxTestFsr FSR;
    NxTestTcr TCR;
    NxTestTdr TDR;
    uint8_t RESERVED_4[8];
    uint32_t RSR;
    NxTestRdr RDR;
};

static_assert(sizeof(NxTestLpspi) == sizeof(NxVendorLpspi), "LPSPI model has another size than the vendor header");
static_assert(offsetof(NxTestLpspi, CR) == offsetof(NxVendorLpspi, CR), "CR of the LPSPI model at another offset");
static_assert(offsetof(NxTestLpspi, SR) == offsetof(NxVendorLpspi, SR), "SR of the LPSPI model at another offset");
static_assert(offsetof(NxTestLpspi, CCR) == offsetof(NxVendorLpspi, CCR), "CCR of the LPSPI model at another offset");
static_assert(offsetof(NxTestLpspi, FSR) == offsetof(NxVendorLpspi, FSR), "FSR of the LPSPI model at another offset");
static_assert(offsetof(NxTestLpspi, TCR) == offsetof(NxVendorLpspi, TCR), "TCR of the LPSPI model at another offset");
static_assert(offsetof(NxTestLpspi, TDR) == offsetof(NxVendorLpspi, TDR), "TDR of the LPSPI model at another offset");
static_assert(offsetof(NxTestLpspi, RDR) == offsetof(NxVendorLpspi, RDR), "RDR of the LPSPI model at another offset");

#define LPSPI_Type NxTestLpspi
