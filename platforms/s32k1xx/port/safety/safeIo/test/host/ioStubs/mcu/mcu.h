/* SYS-42 host test stub of mcu/mcu.h for SafeIoHostTest.cpp: a register model of the S32K1 PORT and GPIO blocks, as
 * far as the real bspIo driver (external/openbsw/platforms/s32k1xx/bsp/bspIo/src/io/Io.cpp) touches them.
 *
 * Register layout and names follow external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/S32K148_PORT.h
 * and S32K148_GPIO.h. What the model adds is the behaviour of a pin control register (S32K1xx Reference Manual,
 * chapter "Port Control and Interrupts", PCR field LK): once LK is set, the fields in bits 15..0 (PS, PE, PFE, DSE,
 * MUX, LK) keep their value until the next reset; IRQC and ISF stay writable. On top of that every PCR can be given
 * a fault: bits stuck at 1 or 0 (seen on read and write) and writes that are lost altogether (a write that never
 * reaches the register, e.g. a wrong bus address or a peripheral that ignores it). */
#pragma once

#include <cstdint>

namespace test_io
{
constexpr uint32_t PCR_LK            = 0x8000U; // PORT_PCR_LK_MASK
constexpr uint32_t PCR_LOCKED_FIELDS = 0xFFFFU; // bits 15..0 are frozen by LK

/** One pin control register with the lock of the silicon and injectable faults. */
class Pcr
{
public:
    Pcr() = default;

    Pcr(Pcr const&)            = delete;
    Pcr& operator=(Pcr const&) = delete;

    Pcr& operator=(uint32_t written)
    {
        ++writes;
        if (dropWrites)
        {
            return *this;
        }
        if ((_value & PCR_LK) != 0U)
        {
            written = (written & ~PCR_LOCKED_FIELDS) | (_value & PCR_LOCKED_FIELDS);
        }
        _value = written;
        return *this;
    }

    operator uint32_t() const { return (_value | stuckAtOne) & ~stuckAtZero; }

    /** Power-on reset of the register (the faults stay: they belong to the silicon or the bus, not the value). */
    void reset(uint32_t const value = 0U)
    {
        _value = value;
        writes = 0;
    }

    uint32_t stuckAtOne  = 0U;
    uint32_t stuckAtZero = 0U;
    bool dropWrites      = false;
    int writes           = 0;

    // SYS-24: public like every other member, so that PORT_Type stays a standard-layout type for offsetof() in the
    // generated register list (the test reads and writes the register through operator= and the conversion only)
    uint32_t _value = 0U;
};
} // namespace test_io

struct PORT_Type
{
    test_io::Pcr PCR[32];
    uint32_t GPCLR;
    uint32_t GPCHR;
    uint32_t GICLR;
    uint32_t GICHR;
    uint32_t RESERVED_0[4];
    uint32_t ISFR;
    uint32_t RESERVED_1[7];
    uint32_t DFER;
    uint32_t DFCR;
    uint32_t DFWR;
};

struct GPIO_Type
{
    uint32_t PDOR;
    uint32_t PSOR;
    uint32_t PCOR;
    uint32_t PTOR;
    uint32_t PDIR;
    uint32_t PDDR;
    uint32_t PIDR;
};

namespace test_io
{
constexpr int PORT_COUNT = 5; // PORTA ... PORTE of the family
extern PORT_Type ports[PORT_COUNT];
extern GPIO_Type gpios[PORT_COUNT];
} // namespace test_io

#define PORT_BASE_PTRS                                                                                   \
    {                                                                                                    \
        &::test_io::ports[0], &::test_io::ports[1], &::test_io::ports[2], &::test_io::ports[3],          \
            &::test_io::ports[4]                                                                         \
    }
#define GPIO_BASE_PTRS                                                                                   \
    {                                                                                                    \
        &::test_io::gpios[0], &::test_io::gpios[1], &::test_io::gpios[2], &::test_io::gpios[3],          \
            &::test_io::gpios[4]                                                                         \
    }

/* SYS-24: the register list of SafeIo (bsp/safeIo/safeIoConfiguration.h) names registers by address, as the device
 * header of the family does: base address of the instance + offsetof(<type>, <register>). The addresses below are
 * those of the S32K1xx reference manual (the same values as S32K148_PORT.h, _GPIO.h, _PCC.h, _FTM.h); the host
 * accessor (safeIo/RegisterAccess.h of this stub directory) maps them back onto the model: PORT PCRs onto the
 * locking Pcr objects above, GPIO onto gpios[], everything else (PCC, FTM) onto plain model words. */
#include <cstddef>

#define IP_PORTA_BASE (0x40049000u)
#define IP_PORTB_BASE (0x4004A000u)
#define IP_PORTC_BASE (0x4004B000u)
#define IP_PORTD_BASE (0x4004C000u)
#define IP_PORTE_BASE (0x4004D000u)
#define IP_PTA_BASE   (0x400FF000u)
#define IP_PTB_BASE   (0x400FF040u)
#define IP_PTC_BASE   (0x400FF080u)
#define IP_PTD_BASE   (0x400FF0C0u)
#define IP_PTE_BASE   (0x400FF100u)
#define IP_PCC_BASE   (0x40065000u)
#define IP_FTM0_BASE  (0x40038000u)
#define IP_FTM1_BASE  (0x40039000u)

#define PCC_FTM0_INDEX  56
#define PCC_FTM1_INDEX  57
#define PCC_PORTA_INDEX 73
#define PCC_PORTB_INDEX 74
#define PCC_PORTC_INDEX 75
#define PCC_PORTD_INDEX 76
#define PCC_PORTE_INDEX 77
#define PCC_PCCn_CGC_MASK (0x40000000u) // SYS-30: clock gate bit, used by the read-back list of the safe outputs

namespace test_io
{
constexpr uint32_t PORT_STEP = 0x1000U; // PORTA ... PORTE
constexpr uint32_t GPIO_STEP = 0x40U;   // PTA ... PTE
} // namespace test_io

struct PCC_Type
{
    uint32_t PCCn[122];
};

struct FTM_Type
{
    uint32_t SC;
    uint32_t CNT;
    uint32_t MOD;
    struct
    {
        uint32_t CnSC;
        uint32_t CnV;
    } CONTROLS[8];
    uint32_t CNTIN;
    uint32_t STATUS;
    uint32_t MODE;
    uint32_t SYNC;
    uint32_t OUTINIT;
    uint32_t OUTMASK;
    uint32_t COMBINE;
    uint32_t DEADTIME;
    uint32_t EXTTRIG;
    uint32_t POL;
    uint32_t FMS;
    uint32_t FILTER;
    uint32_t FLTCTRL;
    uint32_t QDCTRL;
    uint32_t CONF;
    uint32_t FLTPOL;
    uint32_t SYNCONF;
    uint32_t INVCTRL;
    uint32_t SWOCTRL;
    uint32_t PWMLOAD;
    uint32_t HCR;
    uint32_t PAIR0DEADTIME;
    uint8_t RESERVED_0[4];
    uint32_t PAIR1DEADTIME;
    uint8_t RESERVED_1[4];
    uint32_t PAIR2DEADTIME;
    uint8_t RESERVED_2[4];
    uint32_t PAIR3DEADTIME;
};

static_assert(offsetof(FTM_Type, CNTIN) == 0x4CU, "FTM layout of the reference manual");
static_assert(offsetof(FTM_Type, COMBINE) == 0x64U, "FTM layout of the reference manual");
static_assert(offsetof(FTM_Type, POL) == 0x70U, "FTM layout of the reference manual");
static_assert(offsetof(FTM_Type, CONF) == 0x84U, "FTM layout of the reference manual");
static_assert(offsetof(FTM_Type, INVCTRL) == 0x90U, "FTM layout of the reference manual");
static_assert(offsetof(FTM_Type, SWOCTRL) == 0x94U, "FTM layout of the reference manual"); // SYS-30
static_assert(offsetof(FTM_Type, PAIR0DEADTIME) == 0xA0U, "FTM layout of the reference manual");
static_assert(offsetof(FTM_Type, PAIR3DEADTIME) == 0xB8U, "FTM layout of the reference manual");
static_assert(offsetof(GPIO_Type, PDDR) == 0x14U, "GPIO layout of the reference manual");
static_assert(offsetof(GPIO_Type, PIDR) == 0x18U, "GPIO layout of the reference manual");
static_assert(offsetof(PORT_Type, PCR) == 0U, "PORT layout of the reference manual");

/* SYS-153: PMC of the reference manual (41.7.1, the same as S32K148_PMC.h / S32K116_PMC.h): byte registers. The host
 * accessor maps LVDSC1 onto a byte model word (SafeIoHostTest.cpp) - an access of 32 bits to it acts as a bus error. */
#define IP_PMC_BASE (0x4007D000u)

struct PMC_Type
{
    uint8_t LVDSC1;
    uint8_t LVDSC2;
    uint8_t REGSC;
    uint8_t RESERVED_0[1];
    uint8_t LPOTRIM;
};

static_assert(offsetof(PMC_Type, LVDSC1) == 0x0U, "PMC layout of the reference manual");
static_assert(offsetof(PMC_Type, LPOTRIM) == 0x4U, "PMC layout of the reference manual");
