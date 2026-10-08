#pragma once

/* Register model of the family s32k1xx for the host test of the CAN path of the bootloader HAL (BL-39,
 * bootloader/test/host/test_bl_can_host.py).
 *
 * Register layout and bit masks come from the vendor headers of the submodule (S32K148 rev. 1.1a), as in
 * bootloader/test/host/model. The FlexCAN instances stay at their real base addresses (IP_FLEXCAN0..2 of the vendor
 * header): bl_can_host.c reserves that memory and guards every access to it, so an access to message buffer RAM an
 * instance does not have is seen as the access error it is on the target. Everything else the HAL touches (SCG,
 * PCC, PORT, WDOG, RCM, FTFC, PMC and the core registers) is plain memory. */

#include "S32K148_COMMON.h"
#include "S32K148_FLEXCAN.h"
#include "S32K148_FTFC.h"
#include "S32K148_LPSPI.h"
#include "S32K148_PCC.h"
#include "S32K148_PORT.h"
#include "S32K148_RCM.h"
#include "S32K148_SCG.h"
#include "S32K148_WDOG.h"

#include <stdint.h>

#define BL_MODEL_PORT_COUNT 5

extern SCG_Type bl_model_scg;
extern PCC_Type bl_model_pcc;
extern PORT_Type bl_model_port[BL_MODEL_PORT_COUNT];
extern WDOG_Type bl_model_wdog;
extern RCM_Type bl_model_rcm;
extern FTFC_Type bl_model_ftfc;
extern LPSPI_Type bl_model_lpspi[3];

#undef IP_SCG
#define IP_SCG (&bl_model_scg)
#undef IP_PCC
#define IP_PCC (&bl_model_pcc)
#undef IP_WDOG
#define IP_WDOG (&bl_model_wdog)
#undef IP_RCM
#define IP_RCM (&bl_model_rcm)
#undef IP_FTFC
#define IP_FTFC (&bl_model_ftfc)
#undef IP_PORTA
#undef IP_PORTB
#undef IP_PORTC
#undef IP_PORTD
#undef IP_PORTE
#define IP_PORTA (&bl_model_port[0])
#define IP_PORTB (&bl_model_port[1])
#define IP_PORTC (&bl_model_port[2])
#define IP_PORTD (&bl_model_port[3])
#define IP_PORTE (&bl_model_port[4])
#undef IP_LPSPI0
#undef IP_LPSPI1
#undef IP_LPSPI2
#define IP_LPSPI0 (&bl_model_lpspi[0])
#define IP_LPSPI1 (&bl_model_lpspi[1])
#define IP_LPSPI2 (&bl_model_lpspi[2])

/* Core registers (CMSIS names), only the members the HAL uses */
typedef struct
{
    __IO uint32_t CTRL;
    __IO uint32_t LOAD;
    __IO uint32_t VAL;
} bl_model_systick_t;

typedef struct
{
    __IO uint32_t ICER[8];
    __IO uint32_t ICPR[8];
} bl_model_nvic_t;

typedef struct
{
    __IO uint32_t ICSR;
    __IO uint32_t VTOR;
} bl_model_scb_t;

extern bl_model_systick_t bl_model_systick;
extern bl_model_nvic_t bl_model_nvic;
extern bl_model_scb_t bl_model_scb;

#define SysTick (&bl_model_systick)
#define NVIC    (&bl_model_nvic)
#define SCB     (&bl_model_scb)

#define SysTick_CTRL_CLKSOURCE_Msk (1UL << 2)
#define SysTick_CTRL_TICKINT_Msk   (1UL << 1)
#define SysTick_CTRL_ENABLE_Msk    (1UL << 0)
#define SCB_ICSR_PENDSTCLR_Msk     (1UL << 25)

void NVIC_SystemReset(void);
void __disable_irq(void);
uint32_t __get_PRIMASK(void);
void __set_PRIMASK(uint32_t primask);
void __set_MSP(uint32_t sp);
