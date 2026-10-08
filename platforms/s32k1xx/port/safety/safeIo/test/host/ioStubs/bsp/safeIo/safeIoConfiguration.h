// SYS-24: register list of the test profile of SafeIoHostTest.cpp, in the form tools/bswgen generates it
// (tools/bswgen/templates/safeIoConfiguration.h.j2) for the pin table ioStubs/bsp/io/io/ioConfiguration.h: a PWM
// group with one pair on FTM1 (PTD15/PTD16, prescaler 2, 2000 ticks, low side active low, dead time DTPS 2 DTVAL 10),
// a safe_off output with drive strength (PTA7) and the role pin PTB5. Not the eval board (FTM0, PTB12..17, PTC11,
// PTD17, PTC8), so a test that passes only for the demo list does not pass here.
// SYS-153: the last entry is the low-voltage detect reset of the family port (PMC_LVDSC1, class SUPERVISION, 8-bit
// access), as bswgen collect_safe_io() appends it to every list.

#pragma once

#include "mcu/mcu.h"

#include <stddef.h>

#define NX_SAFE_IO_REGISTER_COUNT 21

// clang-format off
#define NX_SAFE_IO_REGISTERS(X) \
    /* PCR PTD15 ALT2: PWM group TEST (FTM1 CH0) */ \
    X(PIN_FUNCTION, TEST_PWM_HS, IP_PORTD_BASE + offsetof(PORT_Type, PCR) + 4UL * 15U, 0x00008743UL, 0x00008200UL, 0x00008000UL, 0x01000000UL, 32U) \
    /* PCR PTD16 ALT2: PWM group TEST (FTM1 CH1) */ \
    X(PIN_FUNCTION, TEST_PWM_LS, IP_PORTD_BASE + offsetof(PORT_Type, PCR) + 4UL * 16U, 0x00008743UL, 0x00008200UL, 0x00008000UL, 0x01000000UL, 32U) \
    /* PCR PTA7 GPIO: safe_off output TEST_GD.drv_en */ \
    X(PIN_FUNCTION, TEST_SAFE_OFF, IP_PORTA_BASE + offsetof(PORT_Type, PCR) + 4UL * 7U, 0x00008743UL, 0x00008140UL, 0x00008000UL, 0x01000000UL, 32U) \
    /* PCR PTB5 GPIO: role pin safety.locked_pin */ \
    X(PIN_FUNCTION, TEST_ROLE_PIN, IP_PORTB_BASE + offsetof(PORT_Type, PCR) + 4UL * 5U, 0x00008743UL, 0x00008100UL, 0x00008000UL, 0x01000000UL, 32U) \
    /* PDDR of PTA7 */ \
    X(GPIO_DIRECTION, PTA_PDDR, IP_PTA_BASE + offsetof(GPIO_Type, PDDR), 0x00000080UL, 0x00000080UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PIDR of PTA7 */ \
    X(GPIO_DIRECTION, PTA_PIDR, IP_PTA_BASE + offsetof(GPIO_Type, PIDR), 0x00000080UL, 0x00000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PDDR of PTB5 */ \
    X(GPIO_DIRECTION, PTB_PDDR, IP_PTB_BASE + offsetof(GPIO_Type, PDDR), 0x00000020UL, 0x00000020UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PIDR of PTB5 */ \
    X(GPIO_DIRECTION, PTB_PIDR, IP_PTB_BASE + offsetof(GPIO_Type, PIDR), 0x00000020UL, 0x00000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PCC FTM1: CGC */ \
    X(CLOCK_GATE, PCC_FTM1, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_FTM1_INDEX, 0x40000000UL, 0x40000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PCC PORTD: CGC */ \
    X(CLOCK_GATE, PCC_PORTD, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTD_INDEX, 0x40000000UL, 0x40000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PCC PORTA: CGC */ \
    X(CLOCK_GATE, PCC_PORTA, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTA_INDEX, 0x40000000UL, 0x40000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PCC PORTB: CGC */ \
    X(CLOCK_GATE, PCC_PORTB, IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTB_INDEX, 0x40000000UL, 0x40000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 SC: CLKS system clock, PS 1, PWMEN 0x03 */ \
    X(PWM_TIMER, TEST_SC, IP_FTM1_BASE + offsetof(FTM_Type, SC), 0x00FF003FUL, 0x00030009UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 MOD: period 2000 ticks */ \
    X(PWM_TIMER, TEST_MOD, IP_FTM1_BASE + offsetof(FTM_Type, MOD), 0x0000FFFFUL, 0x000007CFUL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 CNTIN: counter start 0 */ \
    X(PWM_TIMER, TEST_CNTIN, IP_FTM1_BASE + offsetof(FTM_Type, CNTIN), 0x0000FFFFUL, 0x00000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 COMBINE: COMBINE, COMP, DTEN, SYNCEN of the used pairs */ \
    X(PWM_TIMER, TEST_COMBINE, IP_FTM1_BASE + offsetof(FTM_Type, COMBINE), 0x77777777UL, 0x00000033UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 POL: active-low channels */ \
    X(PWM_TIMER, TEST_POL, IP_FTM1_BASE + offsetof(FTM_Type, POL), 0x000000FFUL, 0x00000002UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 INVCTRL: no inverted pair */ \
    X(PWM_TIMER, TEST_INVCTRL, IP_FTM1_BASE + offsetof(FTM_Type, INVCTRL), 0x0000000FUL, 0x00000000UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 CONF: BDMMODE: outputs safe in debug halt */ \
    X(PWM_TIMER, TEST_CONF, IP_FTM1_BASE + offsetof(FTM_Type, CONF), 0x000000C0UL, 0x00000040UL, 0x00000000UL, 0x00000000UL, 32U) \
    /* FTM1 PAIR0DEADTIME: DTPS 2, DTVAL 10 */ \
    X(PWM_TIMER, TEST_PAIR0DEADTIME, IP_FTM1_BASE + offsetof(FTM_Type, PAIR0DEADTIME), 0x000F00FFUL, 0x0000008AUL, 0x00000000UL, 0x00000000UL, 32U) \
    /* PMC LVDSC1: low-voltage detect reset on (LVDRE), its interrupt off (LVDIE), 8 bit */ \
    X(SUPERVISION, PMC_LVDSC1, IP_PMC_BASE + offsetof(PMC_Type, LVDSC1), 0x00000030UL, 0x00000010UL, 0x00000000UL, 0x00000000UL, 8U)
// clang-format on
