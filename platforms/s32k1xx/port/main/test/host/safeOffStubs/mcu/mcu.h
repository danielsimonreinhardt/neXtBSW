/* SYS-150/SYS-151 host test stub of mcu/mcu.h: the base addresses, register layouts (as far as offsetof() of the
 * generated table needs them) and PCC indices of the S32K1 headers (S32K144_GPIO.h, S32K144_FTM.h, S32K144_PCC.h).
 * The register file itself is the model of the test (NX_SAFE_OUTPUT_REG). */
#pragma once

#include <cstdint>

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

struct FTM_Type
{
    uint32_t BEFORE_SWOCTRL[37]; // SC .. SYNCONF, SWOCTRL at 0x94
    uint32_t SWOCTRL;
};

struct PCC_Type
{
    uint32_t PCCn[122];
};

#define IP_PTB_BASE  (0x400FF040u)
#define IP_PTE_BASE  (0x400FF100u)
#define IP_FTM2_BASE (0x4003A000u)
#define IP_PCC_BASE  (0x40065000u)

#define PCC_FTM2_INDEX    58
#define PCC_PORTB_INDEX   74
#define PCC_PORTE_INDEX   77
#define PCC_PCCn_CGC_MASK 0x40000000u
