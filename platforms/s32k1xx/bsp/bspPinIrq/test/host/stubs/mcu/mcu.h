// Host stub of mcu/mcu.h for the pin-interrupt dispatcher test (IO-52): the real PORT register layout of the family
// (NXP S32K148_PORT.h), every port a RAM model, plus a model of the NVIC. The dispatcher sees the same registers and
// bit macros as on the target; the test drives the pin levels and the interrupt flags (PinIrqDispatcherHostTest.cpp).
//
// PORT.ISFR needs behaviour, not just storage: it is write-1-to-clear. A model that simply stored what the
// dispatcher wrote would hide the very mistake that matters here - clearing the flags of pins without a consumer.
// PCR is plain storage: a flag bit written back as 1 stays visible, so the test sees an ISF bit written back.
#pragma once

#include <cstddef>
#include <cstdint>

#define __IO volatile
#define __O volatile
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

#define PORT_Type NxTestPort

// The s32k1xx family has the ports A..E.
constexpr unsigned NX_TEST_PORTS = 5U;
extern NxTestPort fakePort[NX_TEST_PORTS];

#define PORT_BASE_PTRS                                                       \
    {                                                                        \
        &fakePort[0], &fakePort[1], &fakePort[2], &fakePort[3], &fakePort[4] \
    }

// IO-54: the data input registers for the pin-event adapter (GPIO PDIR); plain storage the test drives. Only the
// adapter test defines them.
extern GPIO_Type fakeGpio[NX_TEST_PORTS];

#define GPIO_BASE_PTRS                                                       \
    {                                                                        \
        &fakeGpio[0], &fakeGpio[1], &fakeGpio[2], &fakeGpio[3], &fakeGpio[4] \
    }

// Model of the NVIC: what the dispatcher enabled and with which level. IRQn_Type with PORTA_IRQn..PORTE_IRQn
// comes from the device header (S32K148_COMMON.h); the S32K11x vector PORT_IRQn is not in that header, so the
// test lets a shared vector use one of these numbers - the model only needs a distinct index.
struct NvicModel
{
    uint32_t enabled; // bit per port interrupt (IRQ - PORTA_IRQn)
    uint32_t pending;
    uint8_t priority[8];
    bool priorityWritten[8];
    unsigned disableCalls;
};

extern NvicModel fakeNvic;

inline unsigned nxTestIrqIndex(IRQn_Type const irq)
{
    return static_cast<unsigned>(static_cast<int>(irq) - static_cast<int>(PORTA_IRQn));
}

inline void NVIC_EnableIRQ(IRQn_Type const irq) { fakeNvic.enabled |= (1UL << nxTestIrqIndex(irq)); }

inline void NVIC_DisableIRQ(IRQn_Type const irq)
{
    fakeNvic.enabled &= ~(1UL << nxTestIrqIndex(irq));
    ++fakeNvic.disableCalls;
}

inline void NVIC_ClearPendingIRQ(IRQn_Type const irq) { fakeNvic.pending &= ~(1UL << nxTestIrqIndex(irq)); }

inline void NVIC_SetPriority(IRQn_Type const irq, uint32_t const priority)
{
    fakeNvic.priority[nxTestIrqIndex(irq)]        = static_cast<uint8_t>(priority);
    fakeNvic.priorityWritten[nxTestIrqIndex(irq)] = true;
}
