/* Bootloader HAL of the family s32k1xx: watchdog, SysTick on the reset clock, SOSC, FlexCAN (polling), FTFC flash,
 * boot request, jump. Board values (CAN instance, pins, CTRL1, device hooks) come from the generated bl_board.h. */

#include "bootloader/bl_board.h"
#include "bl_config.h"
#include "bl_hal.h"
#include "bl_lvd_s32k1xx.h"   /* low-voltage detect reset (BL-34) */
#include "bl_reset_s32k1xx.h" /* resets of the watchdog fast test (BL-33) */
#include "bl_sectors.h"      /* sector table of the generated memory layout (PLAT-16) */
#include "bl_sosc_s32k1xx.h" /* RANGE/EREFS/HGO from the crystal of the board (BL_OSC_HZ, ARCH-09 B7) */

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define WDOG_UNLOCK_VALUE  0xD928C520UL
#define WDOG_DISABLE_VALUE 0x00002924UL /* same value as OpenBSW Watchdog::disableWatchdog() */

#define CAN_RX_MB_FIRST  0U
#define CAN_RX_MB_FUNC   6U /* MB 0..5: physical request id, MB 6..7: functional id */
#define CAN_RX_MB_COUNT  8U
#define CAN_TX_MB        8U
#define CAN_MB_WORDS     4U
/* BL-39: the HAL owns buffers 0..CAN_TX_MB (MCR[MAXMB] = CAN_TX_MB) and touches no other message buffer RAM. An
 * instance has 16 or 32 buffers ([RM] Rev. 13, Table 55-1): FlexCAN1/2 of the S32K144 (FlexCAN1 of the S32K142,
 * FlexCAN2 of the S32K146) have 16, and an access above them (offset 0x180 on) is an access error ([RM] p. 1942).
 * FLEXCAN_RAMn_COUNT of the device header is the RAM of a 32-buffer instance and must not size a loop here. */
#define CAN_MB_USED           (CAN_TX_MB + 1U)
#define CAN_MB_FEWEST_BUFFERS 16U /* smallest FlexCAN instance of the family */
typedef char bl_can_buffers_fit_every_instance[(CAN_MB_USED <= CAN_MB_FEWEST_BUFFERS) ? 1 : -1];
#define CAN_CODE_RX_EMPTY 0x4UL
#define CAN_CODE_TX_INACTIVE 0x8UL
#define CAN_CODE_TX_DATA  0xCUL
#define CAN_STD_ID_SHIFT 18U
#define CAN_STD_ID_MASK  (0x7FFUL << CAN_STD_ID_SHIFT)

#define SOSC_TIMEOUT_MS 500UL
#define CAN_TIMEOUT_MS  100UL

#define FTFC_CMD_PROGRAM_PHRASE 0x07U
#define FTFC_CMD_ERASE_SECTOR   0x09U

static uint32_t volatile ticks;
static bool canReady;

void SysTick_Handler(void);
void Fault_Handler(void);
void bl_system_init(void);

void SysTick_Handler(void) { ticks++; }

void Fault_Handler(void) { NVIC_SystemReset(); }

void bl_system_init(void)
{
    IP_WDOG->CNT   = WDOG_UNLOCK_VALUE;
    IP_WDOG->TOVAL = 0xFFFFUL;
    IP_WDOG->CS    = (IP_WDOG->CS & WDOG_CS_TST_MASK) | WDOG_DISABLE_VALUE;
    /* BL-34: reset below VLVD from here on, in the bootloader as in the application (SYS-125) */
    bl_lvd_reset_enable();
}

uint32_t hal_millis(void) { return ticks; }

uint32_t hal_entropy(void) { return (SysTick->VAL << 16) ^ ticks ^ BOARD_CAN->TIMER; }

static bool wait_until(uint32_t volatile* reg, uint32_t mask, uint32_t value, uint32_t timeoutMs)
{
    uint32_t const start = ticks;
    while ((*reg & mask) != value)
    {
        if ((ticks - start) > timeoutMs)
        {
            return false;
        }
    }
    return true;
}

static bool sosc_init(void)
{
    if ((IP_SCG->SOSCCSR & SCG_SOSCCSR_SOSCVLD_MASK) != 0U)
    {
        return true;
    }
    IP_SCG->SOSCCSR = 0U;
    IP_SCG->SOSCDIV = SCG_SOSCDIV_SOSCDIV1(1U) | SCG_SOSCDIV_SOSCDIV2(1U);
    /* Range from the crystal frequency, not fixed to the high range (bl_sosc_s32k1xx.h, ARCH-09 B7) */
    IP_SCG->SOSCCFG = SCG_SOSCCFG_RANGE(BL_SOSC_RANGE) | SCG_SOSCCFG_EREFS(BL_SOSC_EREFS)
                      | SCG_SOSCCFG_HGO(BL_SOSC_HGO);
    IP_SCG->SOSCCSR = SCG_SOSCCSR_SOSCEN_MASK;
    return wait_until(&IP_SCG->SOSCCSR, SCG_SOSCCSR_SOSCVLD_MASK, SCG_SOSCCSR_SOSCVLD_MASK, SOSC_TIMEOUT_MS);
}

static bool can_init(void)
{
    FLEXCAN_Type* const can = BOARD_CAN;

    bl_board_can_pins();
    IP_PCC->PCCn[BOARD_CAN_PCC_INDEX] |= PCC_PCCn_CGC_MASK;

    can->MCR |= FLEXCAN_MCR_MDIS_MASK;
    can->CTRL1 &= ~FLEXCAN_CTRL1_CLKSRC_MASK; /* oscillator clock (SOSCDIV2) */
    can->MCR &= ~FLEXCAN_MCR_MDIS_MASK;
    if (!wait_until(&can->MCR, FLEXCAN_MCR_LPMACK_MASK, 0U, CAN_TIMEOUT_MS))
    {
        return false;
    }

    can->MCR |= FLEXCAN_MCR_SOFTRST_MASK;
    if (!wait_until(&can->MCR, FLEXCAN_MCR_SOFTRST_MASK, 0U, CAN_TIMEOUT_MS))
    {
        return false;
    }
    can->MCR |= FLEXCAN_MCR_FRZ_MASK | FLEXCAN_MCR_HALT_MASK;
    if (!wait_until(&can->MCR, FLEXCAN_MCR_FRZACK_MASK, FLEXCAN_MCR_FRZACK_MASK, CAN_TIMEOUT_MS))
    {
        return false;
    }

    can->MCR = (can->MCR & ~FLEXCAN_MCR_MAXMB_MASK) | FLEXCAN_MCR_MAXMB(CAN_TX_MB) | FLEXCAN_MCR_SRXDIS_MASK
               | FLEXCAN_MCR_IRMQ_MASK;
    can->CTRL1 = BOARD_CAN_CTRL1;
    can->CTRL2 |= FLEXCAN_CTRL2_MRP_MASK;

    /* BL-39: only the buffers in use - clearing all FLEXCAN_RAMn_COUNT words hit unimplemented RAM on a 16-buffer
     * instance (FlexCAN2 of the eval board, Diag-CAN): HardFault, Fault_Handler reset, reset loop of bootloader and
     * updater. Buffers above MAXMB take no part in matching or arbitration. */
    for (uint32_t i = 0U; i < (CAN_MB_USED * CAN_MB_WORDS); ++i)
    {
        can->RAMn[i] = 0U;
    }
    for (uint32_t mb = 0U; mb < CAN_RX_MB_COUNT; ++mb)
    {
        uint32_t const id = (mb < CAN_RX_MB_FUNC) ? BL_CAN_ID_REQUEST_PHYS : BL_CAN_ID_REQUEST_FUNC;
        can->RXIMR[mb]                      = CAN_STD_ID_MASK;
        can->RAMn[(mb * CAN_MB_WORDS) + 1U] = id << CAN_STD_ID_SHIFT;
        can->RAMn[mb * CAN_MB_WORDS]        = CAN_CODE_RX_EMPTY << 24;
    }
    can->RAMn[CAN_TX_MB * CAN_MB_WORDS] = CAN_CODE_TX_INACTIVE << 24;
    can->IMASK1                         = 0U;
    can->IFLAG1                         = 0xFFFFFFFFUL;

    can->MCR &= ~(FLEXCAN_MCR_FRZ_MASK | FLEXCAN_MCR_HALT_MASK);
    return wait_until(&can->MCR, FLEXCAN_MCR_FRZACK_MASK, 0U, CAN_TIMEOUT_MS)
           && wait_until(&can->MCR, FLEXCAN_MCR_NOTRDY_MASK, 0U, CAN_TIMEOUT_MS);
}

void hal_init(void)
{
    SysTick->LOAD = (BL_CORE_CLOCK_HZ / 1000UL) - 1UL; /* core keeps the reset clock */
    SysTick->VAL  = 0U;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_TICKINT_Msk | SysTick_CTRL_ENABLE_Msk;

    bool const soscOk = sosc_init();
    if (soscOk)
    {
        bl_board_init();
    }
    canReady = soscOk && can_init();
}

bool hal_can_tx_idle(void)
{
    if (!canReady)
    {
        return true;
    }
    return ((BOARD_CAN->RAMn[CAN_TX_MB * CAN_MB_WORDS] >> 24) & 0xFUL) != CAN_CODE_TX_DATA;
}

bool hal_can_send(uint32_t id, uint8_t const* data, uint8_t len)
{
    if (!canReady || (len > 8U) || !hal_can_tx_idle())
    {
        return false;
    }
    uint8_t buf[8] = {0U};
    (void)memcpy(buf, data, len);
    uint32_t const base = CAN_TX_MB * CAN_MB_WORDS;
    FLEXCAN_Type* const can = BOARD_CAN;
    can->IFLAG1           = 1UL << CAN_TX_MB;
    can->RAMn[base]       = CAN_CODE_TX_INACTIVE << 24;
    can->RAMn[base + 1U]  = (id & 0x7FFUL) << CAN_STD_ID_SHIFT;
    can->RAMn[base + 2U]  = ((uint32_t)buf[0] << 24) | ((uint32_t)buf[1] << 16) | ((uint32_t)buf[2] << 8) | buf[3];
    can->RAMn[base + 3U]  = ((uint32_t)buf[4] << 24) | ((uint32_t)buf[5] << 16) | ((uint32_t)buf[6] << 8) | buf[7];
    can->RAMn[base]       = (CAN_CODE_TX_DATA << 24) | ((uint32_t)len << 16);
    return true;
}

bool hal_can_receive(uint32_t* id, uint8_t* data, uint8_t* len)
{
    if (!canReady)
    {
        return false;
    }
    FLEXCAN_Type* const can = BOARD_CAN;
    uint32_t const pending  = can->IFLAG1 & ((1UL << CAN_RX_MB_COUNT) - 1UL);
    if (pending == 0U)
    {
        return false;
    }

    /* Several mailboxes may hold frames; take the oldest one to keep ISO-TP order. */
    uint32_t const now = can->TIMER & 0xFFFFUL;
    uint32_t oldest    = 0U;
    uint32_t maxAge    = 0U;
    bool found         = false;
    for (uint32_t mb = 0U; mb < CAN_RX_MB_COUNT; ++mb)
    {
        if ((pending & (1UL << mb)) != 0U)
        {
            uint32_t const age = (now - (can->RAMn[mb * CAN_MB_WORDS] & 0xFFFFUL)) & 0xFFFFUL;
            if (!found || (age > maxAge))
            {
                oldest = mb;
                maxAge = age;
                found  = true;
            }
        }
    }

    uint32_t const base = oldest * CAN_MB_WORDS;
    uint32_t const cs   = can->RAMn[base]; /* locks the mailbox */
    uint32_t const w2   = can->RAMn[base + 2U];
    uint32_t const w3   = can->RAMn[base + 3U];
    *id                 = (can->RAMn[base + 1U] & CAN_STD_ID_MASK) >> CAN_STD_ID_SHIFT;
    *len                = (uint8_t)((cs >> 16) & 0xFUL);
    if (*len > 8U)
    {
        *len = 8U;
    }
    for (uint8_t i = 0U; i < 4U; ++i)
    {
        data[i]      = (uint8_t)(w2 >> (24U - (8U * i)));
        data[4U + i] = (uint8_t)(w3 >> (24U - (8U * i)));
    }
    can->IFLAG1     = 1UL << oldest;
    (void)can->TIMER; /* unlocks the mailbox */
    return true;
}

uint8_t const* hal_flash_ptr(uint32_t address) { return (uint8_t const*)address; }

/* Runs from RAM: the program flash is not readable while an FTFC command is executing. */
__attribute__((section(".ramfunc"), long_call, noinline)) static uint8_t ftfc_launch(void)
{
    IP_FTFC->FSTAT = FTFC_FSTAT_CCIF_MASK;
    while ((IP_FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
    }
    return IP_FTFC->FSTAT;
}

static bool ftfc_command(uint8_t cmd, uint32_t address, uint8_t const* data)
{
    if (address < BL_APP_START)
    {
        return false; /* never touch the bootloader */
    }
    while ((IP_FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
    }
    IP_FTFC->FSTAT    = FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK;
    IP_FTFC->FCCOB[3] = cmd;
    IP_FTFC->FCCOB[2] = (uint8_t)(address >> 16);
    IP_FTFC->FCCOB[1] = (uint8_t)(address >> 8);
    IP_FTFC->FCCOB[0] = (uint8_t)address;
    if (data != NULL)
    {
        for (uint8_t i = 0U; i < BL_PHRASE_SIZE; ++i)
        {
            IP_FTFC->FCCOB[4U + i] = data[i];
        }
    }
    uint32_t const primask = __get_PRIMASK();
    __disable_irq();
    uint8_t const status = ftfc_launch();
    __set_PRIMASK(primask);
    return (status & (FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK | FTFC_FSTAT_MGSTAT0_MASK)) == 0U;
}

/* FTFC erases the sector the address lies in; the sector geometry comes from the table, not from a mask, because
 * a region may have sectors of different sizes (PLAT-16). S32K1 has one size per derivative (2 or 4 KB). */
bool hal_flash_erase_sector(uint32_t address)
{
    return bl_sector_aligned(address) && ftfc_command(FTFC_CMD_ERASE_SECTOR, address, NULL);
}

bool hal_flash_program(uint32_t address, uint8_t const* data, uint32_t len)
{
    if (((address % BL_PHRASE_SIZE) != 0U) || ((len % BL_PHRASE_SIZE) != 0U))
    {
        return false;
    }
    for (uint32_t off = 0U; off < len; off += BL_PHRASE_SIZE)
    {
        if (!ftfc_command(FTFC_CMD_PROGRAM_PHRASE, address + off, &data[off]))
        {
            return false;
        }
    }
    return memcmp((uint8_t const*)address, data, len) == 0;
}

bool hal_boot_request_consume(void)
{
    /* Only after a software reset: SRAM_U content and ECC are undefined after power-on. */
    if ((IP_RCM->SRS & RCM_SRS_SW_MASK) == 0U)
    {
        return false;
    }
    uint32_t* const flag = (uint32_t*)BL_BOOT_REQUEST_ADDR;
    bool const request   = (flag[0] == BL_BOOT_REQUEST_MAGIC) && (flag[1] == ~BL_BOOT_REQUEST_MAGIC);
    flag[0]              = 0U;
    flag[1]              = 0U;
    return request;
}

/* BL-33: the two resets of the watchdog fast test pass without the startup window (rule in bl_reset_s32k1xx.h). TST
 * survives bl_system_init(), which writes it back unchanged. */
bool hal_reset_skips_startup_window(void) { return bl_s32k1xx_fast_test_reset(IP_RCM->SRS, IP_WDOG->CS); }

void hal_reset(void) { NVIC_SystemReset(); }

void hal_jump_to_app(uint32_t appStart)
{
    __disable_irq();
    SysTick->CTRL = 0U;
    SysTick->VAL  = 0U;
    SCB->ICSR     = SCB_ICSR_PENDSTCLR_Msk;

    if (canReady)
    {
        BOARD_CAN->MCR |= FLEXCAN_MCR_FRZ_MASK | FLEXCAN_MCR_HALT_MASK;
        BOARD_CAN->MCR |= FLEXCAN_MCR_MDIS_MASK;
    }
    IP_PCC->PCCn[BOARD_CAN_PCC_INDEX] &= ~PCC_PCCn_CGC_MASK;

    /* number of NVIC enable/pending registers of the core (ARMv7-M: 8, ARMv6-M: 1), sized by CMSIS (PLAT-12) */
    for (uint32_t i = 0U; i < (sizeof(NVIC->ICER) / sizeof(NVIC->ICER[0])); ++i)
    {
        NVIC->ICER[i] = 0xFFFFFFFFUL;
        NVIC->ICPR[i] = 0xFFFFFFFFUL;
    }

    uint32_t const sp    = *(uint32_t const*)appStart; /* Cortex-M: the application starts with its vector table */
    uint32_t const entry = *(uint32_t const*)(appStart + 4U);
    SCB->VTOR            = appStart;
    __set_MSP(sp);
    ((void (*)(void))entry)();
    for (;;)
    {
    }
}
