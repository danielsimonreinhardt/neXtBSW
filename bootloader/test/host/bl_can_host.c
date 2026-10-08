/* Host harness of the CAN path of the bootloader HAL s32k1xx (BL-39): bootloader/platforms/s32k1xx/hal_s32k1xx.c,
 * unchanged, against a FlexCAN register model with 16 or 32 message buffers per instance.
 *
 * Board 2026-10-02 (VER-04): bootloader and updater built for the Diag-CAN of the eval board (FlexCAN2, 16 buffers)
 * never sent a frame. can_init() cleared FLEXCAN_RAMn_COUNT (128) words of message buffer RAM - the RAM of a
 * 32-buffer instance. On FlexCAN2 the word at offset 0x180 does not exist: access error ([RM] Rev. 13, p. 1942),
 * HardFault, Fault_Handler resets, and the next start runs into the same fault.
 *
 * The model follows the S32K1xx Reference Manual Rev. 13, chapter 55, like the model of the application driver
 * (platforms/s32k1xx/port/bspConfiguration/bspFlexCan/test/host/FlexCanDeviceHostTest.cpp):
 * - message buffer n at 0x80 + 16 n, its mask RXIMRn at 0x880 + 4 n; a buffer or mask the instance does not have,
 *   or any other unimplemented offset, is an access error - recorded with offset and direction;
 * - MCR reset value 0xD890_000F, SOFTRST clears itself and resets the flags, freeze mode (FRZACK, NOTRDY) and low
 *   power acknowledge (LPMACK) follow MDIS/FRZ/HALT - the controller settles before the HAL reads MCR again;
 * - IFLAG1 and ESR1 are write 1 to clear.
 * The registers are real memory at the real base addresses of FlexCAN0..2 (vendor header), each instance on its
 * own page kept PAGE_NOACCESS; a vectored exception handler classifies every access, opens the page and single-steps
 * the instruction. Every access to the model advances the HAL's millisecond tick, so no wait can hang.
 *
 * Usage: bl_can_host.exe <buffers FlexCAN0> <buffers FlexCAN1> <buffers FlexCAN2> (0: no such instance)
 * Prints "key value" lines (read by test_bl_can_host.py). Windows x86-64 only, like the workplace of this project. */

/* before the vendor headers: their __I/__O macros would break the compiler intrinsics windows.h pulls in */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include "bootloader/bl_board.h"
#include "bl_config.h"
#include "bl_hal.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------------------------------- plain registers */

SCG_Type bl_model_scg;
PCC_Type bl_model_pcc;
PORT_Type bl_model_port[BL_MODEL_PORT_COUNT];
WDOG_Type bl_model_wdog;
RCM_Type bl_model_rcm;
FTFC_Type bl_model_ftfc;
LPSPI_Type bl_model_lpspi[3];
bl_model_systick_t bl_model_systick;
bl_model_nvic_t bl_model_nvic;
bl_model_scb_t bl_model_scb;

static unsigned resets;
static unsigned sbcInits;
static int sbcConfigure = -1;

void NVIC_SystemReset(void) { ++resets; }
void __disable_irq(void) {}
uint32_t __get_PRIMASK(void) { return 0U; }
void __set_PRIMASK(uint32_t primask) { (void)primask; }
void __set_MSP(uint32_t sp) { (void)sp; }
void bl_lvd_reset_enable(void) {}

void sbc_init(bool configure)
{
    ++sbcInits;
    sbcConfigure = configure ? 1 : 0;
}

bool sbc_watchdog_off(void) { return true; }

void SysTick_Handler(void); /* of the HAL: the millisecond tick */

/* ------------------------------------------------------------------------------------------- FlexCAN model */

#define REGION_BASE   0x40020000UL /* allocation granularity 64 KiB, holds all three instances */
#define REGION_SIZE   0x10000UL
#define PAGE_BYTES    0x1000UL
#define OFF_MCR       0x00U
#define OFF_ECR       0x1CU
#define OFF_ESR1      0x20U
#define OFF_IMASK1    0x28U
#define OFF_IFLAG1    0x30U
#define OFF_RAM       0x80U
#define MB_BYTES      16U
#define OFF_RXIMR     0x880U
#define MCR_RESET     0xD890000FUL
#define MCR_READ_ONLY (FLEXCAN_MCR_NOTRDY_MASK | FLEXCAN_MCR_FRZACK_MASK | FLEXCAN_MCR_LPMACK_MASK)
#define CODE_TX_DATA  0xCUL
#define CODE_RX_FULL  0x2UL
#define MAX_ERRORS    256U

/* Registers below the message buffer RAM ([RM] 55.4.2.1); everything else there is reserved. */
static uint32_t const registers[] = {0x00U, 0x04U, 0x08U, 0x10U, 0x14U, 0x18U, 0x1CU, 0x20U,
                                     0x28U, 0x30U, 0x34U, 0x38U, 0x44U, 0x48U, 0x4CU, 0x50U};

typedef struct
{
    uintptr_t base;
    uint32_t buffers; /* 0: the derivative has no such instance - every access is an error */
    unsigned accesses;
    int highestBuffer;
    unsigned errors;
    uint32_t errorOffset[MAX_ERRORS];
    bool errorWrite[MAX_ERRORS];
    unsigned transmits;
    int lastTransmit;
} instance_t;

static instance_t instances[3] = {{0x40024000UL, 0U, 0U, -1, 0U, {0U}, {false}, 0U, -1},
                                  {0x40025000UL, 0U, 0U, -1, 0U, {0U}, {false}, 0U, -1},
                                  {0x4002B000UL, 0U, 0U, -1, 0U, {0U}, {false}, 0U, -1}};

static struct
{
    instance_t* inst;
    uint32_t offset;
    bool write;
    uint32_t before;
} pending;

static uint32_t volatile* word(instance_t const* inst, uint32_t offset)
{
    return (uint32_t volatile*)(inst->base + offset);
}

static void protect(instance_t const* inst, bool open)
{
    DWORD old = 0U;
    (void)VirtualProtect((void*)inst->base, PAGE_BYTES, open ? PAGE_READWRITE : PAGE_NOACCESS, &old);
}

static bool is_implemented(instance_t* inst, uint32_t offset)
{
    if (inst->buffers == 0U)
    {
        return false;
    }
    for (unsigned i = 0U; i < (sizeof(registers) / sizeof(registers[0])); ++i)
    {
        if (offset == registers[i])
        {
            return true;
        }
    }
    if ((offset >= OFF_RAM) && (offset < (OFF_RAM + (inst->buffers * MB_BYTES))))
    {
        int const mb        = (int)((offset - OFF_RAM) / MB_BYTES);
        inst->highestBuffer = (mb > inst->highestBuffer) ? mb : inst->highestBuffer;
        return true;
    }
    if ((offset >= OFF_RXIMR) && (offset < (OFF_RXIMR + (inst->buffers * 4U))))
    {
        int const mb        = (int)((offset - OFF_RXIMR) / 4U);
        inst->highestBuffer = (mb > inst->highestBuffer) ? mb : inst->highestBuffer;
        return true;
    }
    return false;
}

static bool is_frozen(uint32_t mcr)
{
    return ((mcr & FLEXCAN_MCR_MDIS_MASK) == 0U) && ((mcr & FLEXCAN_MCR_FRZ_MASK) != 0U)
           && ((mcr & FLEXCAN_MCR_HALT_MASK) != 0U);
}

/* What the controller does on its own until the HAL reads MCR again (page open). */
static void settle(instance_t const* inst)
{
    uint32_t mcr = *word(inst, OFF_MCR) & ~FLEXCAN_MCR_SOFTRST_MASK;
    if ((mcr & FLEXCAN_MCR_MDIS_MASK) != 0U)
    {
        mcr = (mcr | FLEXCAN_MCR_LPMACK_MASK | FLEXCAN_MCR_NOTRDY_MASK) & ~FLEXCAN_MCR_FRZACK_MASK;
    }
    else if (is_frozen(mcr))
    {
        mcr = (mcr | FLEXCAN_MCR_FRZACK_MASK | FLEXCAN_MCR_NOTRDY_MASK) & ~FLEXCAN_MCR_LPMACK_MASK;
    }
    else
    {
        mcr &= ~(FLEXCAN_MCR_FRZACK_MASK | FLEXCAN_MCR_NOTRDY_MASK | FLEXCAN_MCR_LPMACK_MASK);
    }
    *word(inst, OFF_MCR) = mcr;
}

/* Register semantics of a completed write (page open). */
static void after_write(instance_t* inst, uint32_t offset, uint32_t before)
{
    uint32_t const written = *word(inst, offset);
    if (offset == OFF_MCR)
    {
        uint32_t value = (written & ~MCR_READ_ONLY) | (before & MCR_READ_ONLY);
        if (((written & FLEXCAN_MCR_SOFTRST_MASK) != 0U) && ((before & FLEXCAN_MCR_SOFTRST_MASK) == 0U))
        {
            value = (MCR_RESET & ~FLEXCAN_MCR_MDIS_MASK) | (written & FLEXCAN_MCR_MDIS_MASK)
                    | FLEXCAN_MCR_SOFTRST_MASK | (before & MCR_READ_ONLY);
            *word(inst, OFF_IMASK1) = 0U;
            *word(inst, OFF_IFLAG1) = 0U;
            *word(inst, OFF_ESR1)   = 0U;
            *word(inst, OFF_ECR)    = 0U;
        }
        *word(inst, OFF_MCR) = value;
    }
    else if ((offset == OFF_IFLAG1) || (offset == OFF_ESR1))
    {
        *word(inst, offset) = before & ~written; /* write 1 to clear */
    }
    else if ((offset >= OFF_RAM) && (offset < (OFF_RAM + (inst->buffers * MB_BYTES)))
             && (((offset - OFF_RAM) % MB_BYTES) == 0U))
    {
        if ((((written >> 24) & 0xFUL) == CODE_TX_DATA) && (((before >> 24) & 0xFUL) != CODE_TX_DATA))
        {
            ++inst->transmits;
            inst->lastTransmit = (int)((offset - OFF_RAM) / MB_BYTES);
        }
    }
}

static LONG CALLBACK trap(EXCEPTION_POINTERS* info)
{
    EXCEPTION_RECORD const* const record = info->ExceptionRecord;
    if ((record->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) && (record->NumberParameters >= 2U)
        && (pending.inst == NULL))
    {
        uintptr_t const address = (uintptr_t)record->ExceptionInformation[1];
        for (unsigned i = 0U; i < 3U; ++i)
        {
            instance_t* const inst = &instances[i];
            if ((address >= inst->base) && (address < (inst->base + PAGE_BYTES)))
            {
                uint32_t const offset = (uint32_t)(address - inst->base) & ~3U;
                bool const write      = (record->ExceptionInformation[0] == 1U);
                ++inst->accesses;
                if (!is_implemented(inst, offset) && (inst->errors < MAX_ERRORS))
                {
                    inst->errorOffset[inst->errors] = offset;
                    inst->errorWrite[inst->errors]  = write;
                    ++inst->errors;
                }
                protect(inst, true);
                if (!write && (offset == OFF_MCR) && (inst->buffers != 0U))
                {
                    settle(inst);
                }
                SysTick_Handler(); /* time passes with every register access */
                pending.inst   = inst;
                pending.offset = offset;
                pending.write  = write;
                pending.before = *word(inst, offset);
                info->ContextRecord->EFlags |= 0x100U; /* trap flag: back here after this one instruction */
                return EXCEPTION_CONTINUE_EXECUTION;
            }
        }
        return EXCEPTION_CONTINUE_SEARCH;
    }
    if ((record->ExceptionCode == EXCEPTION_SINGLE_STEP) && (pending.inst != NULL))
    {
        instance_t* const inst = pending.inst;
        /* a read-modify-write instruction may be reported as read: a changed word is a write */
        if ((pending.write || (*word(inst, pending.offset) != pending.before)) && (inst->buffers != 0U))
        {
            after_write(inst, pending.offset, pending.before);
        }
        protect(inst, false);
        pending.inst = NULL;
        info->ContextRecord->EFlags &= ~0x100U;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void model_reset(void)
{
    for (unsigned i = 0U; i < 3U; ++i)
    {
        instance_t* const inst = &instances[i];
        protect(inst, true);
        for (uint32_t offset = 0U; offset < PAGE_BYTES; offset += 4U)
        {
            *word(inst, offset) = 0xA5A5A5A5UL; /* whatever the RAM holds after power-up */
        }
        for (unsigned r = 0U; r < (sizeof(registers) / sizeof(registers[0])); ++r)
        {
            *word(inst, registers[r]) = 0U;
        }
        *word(inst, OFF_MCR) = MCR_RESET;
        inst->accesses       = 0U;
        inst->highestBuffer  = -1;
        inst->errors         = 0U;
        inst->transmits      = 0U;
        inst->lastTransmit   = -1;
        protect(inst, false);
    }
}

static uint32_t peek(instance_t const* inst, uint32_t offset)
{
    protect(inst, true);
    uint32_t const value = *word(inst, offset);
    protect(inst, false);
    return value;
}

static void poke(instance_t const* inst, uint32_t offset, uint32_t value)
{
    protect(inst, true);
    *word(inst, offset) = value;
    protect(inst, false);
}

static int board_instance(void)
{
    uintptr_t const base = (uintptr_t)BOARD_CAN;
    for (int i = 0; i < 3; ++i)
    {
        if (instances[i].base == base)
        {
            return i;
        }
    }
    return -1;
}

static void report_instances(char const* stage)
{
    for (unsigned i = 0U; i < 3U; ++i)
    {
        instance_t const* const inst = &instances[i];
        printf("%s.can%u.accesses %u\n", stage, i, inst->accesses);
        printf("%s.can%u.errors %u\n", stage, i, inst->errors);
        printf("%s.can%u.highest_buffer %d\n", stage, i, inst->highestBuffer);
        if (inst->errors > 0U)
        {
            printf("%s.can%u.first_error 0x%03X %s\n", stage, i, (unsigned)inst->errorOffset[0],
                   inst->errorWrite[0] ? "write" : "read");
        }
    }
}

int main(int argc, char** argv)
{
    if (argc < 4)
    {
        fprintf(stderr, "usage: %s <buffers0> <buffers1> <buffers2>\n", argv[0]);
        return 2;
    }
    if (VirtualAlloc((void*)REGION_BASE, REGION_SIZE, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)
        != (void*)REGION_BASE)
    {
        fprintf(stderr, "cannot reserve the FlexCAN region at 0x%08lX\n", REGION_BASE);
        return 3;
    }
    for (unsigned i = 0U; i < 3U; ++i)
    {
        instances[i].buffers = (uint32_t)strtoul(argv[1 + i], NULL, 0);
    }
    (void)AddVectoredExceptionHandler(1U, trap);
    model_reset();

    /* oscillator already valid: the SOSC start of the HAL has its own test (test_bl_platform_host.py) */
    bl_model_scg.SOSCCSR = SCG_SOSCCSR_SOSCVLD_MASK;
    int const board      = board_instance();
    printf("board_instance %d\n", board);
    printf("board_ctrl1 0x%08lX\n", (unsigned long)BOARD_CAN_CTRL1);
    printf("board_pcc_index %u\n", (unsigned)BOARD_CAN_PCC_INDEX);
    printf("id_phys 0x%03lX\n", (unsigned long)BL_CAN_ID_REQUEST_PHYS);
    printf("id_func 0x%03lX\n", (unsigned long)BL_CAN_ID_REQUEST_FUNC);
    fflush(stdout);
    if (board < 0)
    {
        return 4;
    }
    instance_t const* const can = &instances[board];

    hal_init();
    report_instances("init");
    uint32_t const mcr = peek(can, OFF_MCR);
    printf("mcr 0x%08lX\n", (unsigned long)mcr);
    printf("ctrl1 0x%08lX\n", (unsigned long)peek(can, 0x04U));
    printf("ctrl2 0x%08lX\n", (unsigned long)peek(can, 0x34U));
    printf("pcc_can 0x%08lX\n", (unsigned long)bl_model_pcc.PCCn[BOARD_CAN_PCC_INDEX]);
    for (uint32_t port = 0U; port < BL_MODEL_PORT_COUNT; ++port)
    {
        for (uint32_t pin = 0U; pin < 32U; ++pin)
        {
            if (bl_model_port[port].PCR[pin] != 0U)
            {
                printf("pcr %c%u 0x%08lX\n", (char)('A' + port), (unsigned)pin,
                       (unsigned long)bl_model_port[port].PCR[pin]);
            }
        }
    }
    if (can->buffers != 0U)
    {
        uint32_t const shown = (can->buffers < 16U) ? can->buffers : 16U;
        for (uint32_t mb = 0U; mb < shown; ++mb)
        {
            printf("mb %u 0x%08lX 0x%08lX 0x%08lX\n", (unsigned)mb,
                   (unsigned long)peek(can, OFF_RAM + (mb * MB_BYTES)),
                   (unsigned long)peek(can, OFF_RAM + (mb * MB_BYTES) + 4U),
                   (unsigned long)peek(can, OFF_RXIMR + (mb * 4U)));
        }
    }
    printf("sbc_inits %u\n", sbcInits);
    printf("sbc_configure %d\n", sbcConfigure);
    printf("resets %u\n", resets);
    fflush(stdout);

    /* transmit: one frame of the response id */
    uint8_t const tx[8] = {0x06U, 0x50U, 0x02U, 0x00U, 0x32U, 0x01U, 0xF4U, 0xAAU};
    bool const sent     = hal_can_send(0x7E8UL, tx, 8U);
    printf("send %d\n", sent ? 1 : 0);
    printf("send.transmits %u\n", can->transmits);
    printf("send.buffer %d\n", can->lastTransmit);
    if (can->lastTransmit >= 0)
    {
        uint32_t const base = OFF_RAM + ((uint32_t)can->lastTransmit * MB_BYTES);
        printf("send.mb 0x%08lX 0x%08lX 0x%08lX 0x%08lX\n", (unsigned long)peek(can, base),
               (unsigned long)peek(can, base + 4U), (unsigned long)peek(can, base + 8U),
               (unsigned long)peek(can, base + 12U));
    }
    printf("send.tx_idle %d\n", hal_can_tx_idle() ? 1 : 0);

    /* receive: a request in receive buffer 2 ([RM] 55.4.3: CS with CODE FULL and DLC, ID, data big endian) */
    if (can->buffers != 0U)
    {
        uint32_t const base = OFF_RAM + (2U * MB_BYTES);
        poke(can, base, (CODE_RX_FULL << 24) | (8UL << 16) | 0x0010UL);
        poke(can, base + 4U, BL_CAN_ID_REQUEST_PHYS << 18);
        poke(can, base + 8U, 0x02101122UL);
        poke(can, base + 12U, 0x33445566UL);
        poke(can, OFF_IFLAG1, peek(can, OFF_IFLAG1) | (1UL << 2));
    }
    uint32_t id     = 0U;
    uint8_t data[8] = {0U};
    uint8_t len     = 0U;
    bool const got  = hal_can_receive(&id, data, &len);
    printf("receive %d\n", got ? 1 : 0);
    printf("receive.id 0x%03lX\n", (unsigned long)id);
    printf("receive.len %u\n", (unsigned)len);
    printf("receive.data %02X%02X%02X%02X%02X%02X%02X%02X\n", data[0], data[1], data[2], data[3], data[4], data[5],
           data[6], data[7]);
    printf("receive.iflag1 0x%08lX\n", (unsigned long)peek(can, OFF_IFLAG1));
    report_instances("end");
    printf("resets_end %u\n", resets);
    printf("done 1\n");
    return 0;
}
