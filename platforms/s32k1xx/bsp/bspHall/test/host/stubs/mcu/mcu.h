// Host stub of mcu/mcu.h for the hall sensor tests (IO-07): the real GPIO and PORT register layout of the
// family (NXP S32K148_GPIO.h, S32K148_PORT.h), every port a RAM model, plus a model of the NVIC. The driver
// sees the same registers and the same bit macros as on the target; the test drives the pin levels and the
// interrupt flags (see HallSensorsHostTest.cpp).
//
// One register needs behaviour, not just storage: PORT.ISFR is write-1-to-clear. A model that simply stored
// what the driver wrote would hide the very mistake that matters here - clearing the flags of pins that do not
// belong to a hall signal. The layout of the model is compared with the vendor header field by field below.
#pragma once

#include <cstddef>
#include <cstdint>

#define __IO volatile
#define __O volatile
// PDIR is read only for the driver; the model writes it, so the const of the vendor header is dropped here.
#define __I volatile

#include "S32K148_GPIO.h"
#include "S32K148_PORT.h"

using NxVendorPort = PORT_Type;

/** PORT.ISFR: a read gives the flags, a write clears every bit written as 1 (S32K1xx RM, PORT chapter). */
struct NxTestIsfr
{
    uint32_t value;

    operator uint32_t() const { return value; }

    NxTestIsfr& operator=(uint32_t const written)
    {
        value &= ~written;
        return *this;
    }

    /** The hardware setting a flag on an edge (only the model uses this). */
    void set(uint32_t const bits) { value |= bits; }

    /** Reset of the model. */
    void clear() { value = 0U; }
};

/** Model of a PORT: the fields and offsets of the vendor header, ISFR with its write-1-to-clear behaviour. */
struct NxTestPort
{
    __IO uint32_t PCR[PORT_PCR_COUNT];
    __O uint32_t GPCLR;
    __O uint32_t GPCHR;
    __O uint32_t GICLR;
    __O uint32_t GICHR;
    uint8_t RESERVED_0[16];
    NxTestIsfr ISFR;
    uint8_t RESERVED_1[28];
    __IO uint32_t DFER;
    __IO uint32_t DFCR;
    __IO uint32_t DFWR;
};

static_assert(sizeof(NxTestPort) == sizeof(NxVendorPort), "PORT model has another size than the vendor header");
static_assert(
    offsetof(NxTestPort, PCR) == offsetof(NxVendorPort, PCR), "PCR of the PORT model is at another offset");
static_assert(
    offsetof(NxTestPort, ISFR) == offsetof(NxVendorPort, ISFR), "ISFR of the PORT model is at another offset");
static_assert(
    offsetof(NxTestPort, DFER) == offsetof(NxVendorPort, DFER), "DFER of the PORT model is at another offset");

#define PORT_Type NxTestPort

// The s32k1xx family has the ports A..E; the tables are what mcu/mcu.h remaps for the drivers.
constexpr unsigned NX_TEST_PORTS = 5U;
extern GPIO_Type fakeGpio[NX_TEST_PORTS];
extern NxTestPort fakePort[NX_TEST_PORTS];

#define GPIO_BASE_PTRS                                                       \
    {                                                                        \
        &fakeGpio[0], &fakeGpio[1], &fakeGpio[2], &fakeGpio[3], &fakeGpio[4] \
    }
#define PORT_BASE_PTRS                                                       \
    {                                                                        \
        &fakePort[0], &fakePort[1], &fakePort[2], &fakePort[3], &fakePort[4] \
    }

// IRQn_Type with PORTA_IRQn..PORTE_IRQn comes from the device header itself (S32K148_COMMON.h, included by
// S32K148_GPIO.h), so the test uses the real interrupt numbers of the family.

// Model of the NVIC: what the driver enabled and with which level (checked by the test)
struct NvicModel
{
    uint32_t enabled; // bit per port interrupt (IRQ - PORTA_IRQn)
    uint32_t pending;
    uint8_t priority[8];
    bool priorityWritten[8];
};

extern NvicModel fakeNvic;

inline unsigned nxTestIrqIndex(IRQn_Type const irq)
{
    return static_cast<unsigned>(static_cast<int>(irq) - static_cast<int>(PORTA_IRQn));
}

inline void NVIC_EnableIRQ(IRQn_Type const irq) { fakeNvic.enabled |= (1UL << nxTestIrqIndex(irq)); }

inline void NVIC_DisableIRQ(IRQn_Type const irq) { fakeNvic.enabled &= ~(1UL << nxTestIrqIndex(irq)); }

inline void NVIC_ClearPendingIRQ(IRQn_Type const irq) { fakeNvic.pending &= ~(1UL << nxTestIrqIndex(irq)); }

inline void NVIC_SetPriority(IRQn_Type const irq, uint32_t const priority)
{
    fakeNvic.priority[nxTestIrqIndex(irq)]        = static_cast<uint8_t>(priority);
    fakeNvic.priorityWritten[nxTestIrqIndex(irq)] = true;
}
