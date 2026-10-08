// SPDX-License-Identifier: Apache-2.0
// Host stub of mcu/mcu.h for the FlexIO I2C host test (IO-09): the FLEXIO register layout and bit macros of the
// family (NXP S32K148_FLEXIO.h) with every register behind a proxy, so the test's model (FlexioI2cHostTest.cpp)
// sees each read and write with its offset. The model follows the S32K1xx Reference Manual Rev. 13 chapter 54,
// independent of the driver. The layout is compared with the vendor header below.
#pragma once

#include <cstddef>
#include <cstdint>

#define __IO volatile
#define __O volatile
#define __I volatile

#include "S32K148_FLEXIO.h"

using NxVendorFlexio = FLEXIO_Type;

struct NxTestFlexioRegister;

// Implemented by the test, one model instance.
uint32_t nxTestFlexioRead(NxTestFlexioRegister const* reg);
void nxTestFlexioWrite(NxTestFlexioRegister const* reg, uint32_t value);

struct NxTestFlexioRegister
{
    uint32_t unused;
    operator uint32_t() const { return nxTestFlexioRead(this); }
    NxTestFlexioRegister& operator=(uint32_t const value)
    {
        nxTestFlexioWrite(this, value);
        return *this;
    }
};

/** Model of the FlexIO: fields and offsets of the vendor header, behaviour in the test. */
struct NxTestFlexio
{
    NxTestFlexioRegister VERID;
    NxTestFlexioRegister PARAM;
    NxTestFlexioRegister CTRL;
    NxTestFlexioRegister PIN;
    NxTestFlexioRegister SHIFTSTAT;
    NxTestFlexioRegister SHIFTERR;
    NxTestFlexioRegister TIMSTAT;
    uint8_t RESERVED_0[4];
    NxTestFlexioRegister SHIFTSIEN;
    NxTestFlexioRegister SHIFTEIEN;
    NxTestFlexioRegister TIMIEN;
    uint8_t RESERVED_1[4];
    NxTestFlexioRegister SHIFTSDEN;
    uint8_t RESERVED_2[76];
    NxTestFlexioRegister SHIFTCTL[FLEXIO_SHIFTCTL_COUNT];
    uint8_t RESERVED_3[112];
    NxTestFlexioRegister SHIFTCFG[FLEXIO_SHIFTCFG_COUNT];
    uint8_t RESERVED_4[240];
    NxTestFlexioRegister SHIFTBUF[FLEXIO_SHIFTBUF_COUNT];
    uint8_t RESERVED_5[112];
    NxTestFlexioRegister SHIFTBUFBIS[FLEXIO_SHIFTBUFBIS_COUNT];
    uint8_t RESERVED_6[112];
    NxTestFlexioRegister SHIFTBUFBYS[FLEXIO_SHIFTBUFBYS_COUNT];
    uint8_t RESERVED_7[112];
    NxTestFlexioRegister SHIFTBUFBBS[FLEXIO_SHIFTBUFBBS_COUNT];
    uint8_t RESERVED_8[112];
    NxTestFlexioRegister TIMCTL[FLEXIO_TIMCTL_COUNT];
    uint8_t RESERVED_9[112];
    NxTestFlexioRegister TIMCFG[FLEXIO_TIMCFG_COUNT];
    uint8_t RESERVED_10[112];
    NxTestFlexioRegister TIMCMP[FLEXIO_TIMCMP_COUNT];
};

#define NX_TEST_FLEXIO_SAME_OFFSET(field)                                   \
    static_assert(offsetof(NxTestFlexio, field) == offsetof(NxVendorFlexio, field), \
                  #field " of the FlexIO model at another offset")
static_assert(sizeof(NxTestFlexio) == sizeof(NxVendorFlexio), "FlexIO model has another size than the vendor header");
NX_TEST_FLEXIO_SAME_OFFSET(PARAM);
NX_TEST_FLEXIO_SAME_OFFSET(CTRL);
NX_TEST_FLEXIO_SAME_OFFSET(PIN);
NX_TEST_FLEXIO_SAME_OFFSET(SHIFTSTAT);
NX_TEST_FLEXIO_SAME_OFFSET(SHIFTERR);
NX_TEST_FLEXIO_SAME_OFFSET(TIMSTAT);
NX_TEST_FLEXIO_SAME_OFFSET(SHIFTCTL);
NX_TEST_FLEXIO_SAME_OFFSET(SHIFTCFG);
NX_TEST_FLEXIO_SAME_OFFSET(SHIFTBUF);
NX_TEST_FLEXIO_SAME_OFFSET(SHIFTBUFBIS);
NX_TEST_FLEXIO_SAME_OFFSET(SHIFTBUFBBS);
NX_TEST_FLEXIO_SAME_OFFSET(TIMCTL);
NX_TEST_FLEXIO_SAME_OFFSET(TIMCFG);
NX_TEST_FLEXIO_SAME_OFFSET(TIMCMP);
#undef NX_TEST_FLEXIO_SAME_OFFSET

#define FLEXIO_Type NxTestFlexio
