/* Host build of the bootloader core: simulated flash, CAN queues and clock, driven from Python. */

#include "bl_config.h"
#include "bl_hal.h"
#include "bl_main.h"
#include "bl_sectors.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

#define QUEUE_SIZE 256U

typedef struct
{
    uint32_t id;
    uint8_t len;
    uint8_t data[8];
    uint32_t ms; /* BL-25: simulated time the core handed the frame over (host_last_tx_ms()) */
} frame_t;

typedef struct
{
    frame_t frames[QUEUE_SIZE];
    uint32_t head;
    uint32_t tail;
} queue_t;

static uint8_t flash[BL_FLASH_SIZE]; /* flash[0] is address BL_FLASH_START (generated memory layout) */
#define FLASH_END (BL_FLASH_START + BL_FLASH_SIZE)
static queue_t rxQueue;
static queue_t txQueue;
#define ERASE_LOG_SIZE 512U

static uint32_t nowMs;
static uint32_t eraseCostMs      = 20U;
static uint32_t eraseCostPerKbMs = 0U; /* PLAT-16: a 128-KB sector costs much more than a 16-KB one */
static uint32_t programCostMs    = 1U;
/* Erased sectors in order (start, size): the tests check that the core follows the sector table. */
static uint32_t eraseLog[ERASE_LOG_SIZE][2];
static uint32_t eraseLogLen;
static uint32_t entropy       = 0x12345678UL;
static bool bootRequest;
static bool skipsWindow; /* BL-33: answer of hal_reset_skips_startup_window() for this power-on */
static uint32_t skipQueries;
static bool resetRequested;
static uint32_t jumpAddress;
static bool failNextProgram;
static uint32_t lastTxMs;

static bool queue_push(queue_t* q, frame_t const* f)
{
    uint32_t const next = (q->head + 1U) % QUEUE_SIZE;
    if (next == q->tail)
    {
        return false;
    }
    q->frames[q->head] = *f;
    q->head            = next;
    return true;
}

static bool queue_pop(queue_t* q, frame_t* f)
{
    if (q->tail == q->head)
    {
        return false;
    }
    *f      = q->frames[q->tail];
    q->tail = (q->tail + 1U) % QUEUE_SIZE;
    return true;
}

/* BL-25: the millisecond tick of a target is an interrupt (SysTick). It can fall between any two hal_millis() calls of
 * one bl_poll(), so two readings of the same cycle may differ by one. Without this model the host clock only moved
 * between polls, and a core that mixed an older reading with a newer one (isotp.c before BL-25: timer check against
 * the poll's time, frame stamped with a later reading) passed every host test and lost 1026-byte messages at the
 * board. With the model on, every poll carries one tick at a rotating reading position (1 .. ASYNC_TICK_SPAN). */
#define ASYNC_TICK_SPAN 8U
static bool asyncTick;
static bool inPoll;
static uint32_t pollReadings;
static uint32_t tickAtReading;
static bool tickApplied;

void hal_init(void) {}

uint32_t hal_millis(void)
{
    if (inPoll && asyncTick && !tickApplied)
    {
        pollReadings++;
        if (pollReadings >= tickAtReading)
        {
            nowMs++;
            tickApplied = true;
        }
    }
    return nowMs;
}

uint32_t hal_entropy(void)
{
    entropy = entropy * 1103515245UL + 12345UL;
    return entropy;
}

bool hal_can_send(uint32_t id, uint8_t const* data, uint8_t len)
{
    frame_t f = {id, len, {0}, nowMs};
    (void)memcpy(f.data, data, len);
    return queue_push(&txQueue, &f);
}

bool hal_can_tx_idle(void) { return true; }

bool hal_can_receive(uint32_t* id, uint8_t* data, uint8_t* len)
{
    frame_t f;
    if (!queue_pop(&rxQueue, &f))
    {
        return false;
    }
    *id  = f.id;
    *len = f.len;
    (void)memcpy(data, f.data, f.len);
    return true;
}

/* BL-38: reads of the application start, to count the CRC-32 runs over the image per start. The Cortex-M entry check
 * (hal_app_entry_plausible()) reads the start and start + 4 as a pair; a CRC over the image (app_image_valid(), the
 * check-memory job) reads the start alone. CRC runs = reads of the start - reads of start + 4. */
static uint32_t appStartReads;
static uint32_t appEntryReads;

uint8_t const* hal_flash_ptr(uint32_t address)
{
    if (address == BL_APP_START)
    {
        appStartReads++;
    }
    else if (address == (BL_APP_START + 4U))
    {
        appEntryReads++;
    }
    return &flash[address - BL_FLASH_START];
}

/* CRC-32 runs over the application since the last power-on (BL-38: one per start). */
EXPORT uint32_t host_image_crc_runs(void) { return appStartReads - appEntryReads; }

/* Like the real flash: one command erases the sector the address starts, whatever size the table gives it
 * (PLAT-16). The record sector may lie below the application area (STM32F4 proposal), so both are allowed. */
bool hal_flash_erase_sector(uint32_t address)
{
    bl_sector_t sector;
    bl_sector_t record;
    if (!bl_sector_at(address, &sector) || (sector.start != address) || !bl_record_sector(&record))
    {
        return false;
    }
    if ((address < BL_APP_START) && (address != record.start))
    {
        return false; /* never the bootloader area */
    }
    (void)memset(&flash[address - BL_FLASH_START], 0xFF, sector.size);
    if (eraseLogLen < ERASE_LOG_SIZE)
    {
        eraseLog[eraseLogLen][0] = sector.start;
        eraseLog[eraseLogLen][1] = sector.size;
        eraseLogLen++;
    }
    nowMs += eraseCostMs + ((eraseCostPerKbMs * sector.size) / 1024U);
    return true;
}

bool hal_flash_program(uint32_t address, uint8_t const* data, uint32_t len)
{
    bl_sector_t record;
    if (((len % BL_PHRASE_SIZE) != 0U) || ((address % BL_PHRASE_SIZE) != 0U)
        || !bl_range_inside(address, len, BL_FLASH_START, BL_FLASH_SIZE))
    {
        return false;
    }
    /* Application area or the record sector (which may lie below the application, PLAT-16), never the bootloader. */
    if (!bl_record_sector(&record)
        || ((address < BL_APP_START) && !bl_range_inside(address, len, record.start, record.size)))
    {
        return false;
    }
    if (failNextProgram)
    {
        failNextProgram = false;
        return false;
    }
    uint32_t const index = address - BL_FLASH_START;
    for (uint32_t i = 0U; i < len; ++i)
    {
        if (flash[index + i] != 0xFFU)
        {
            return false; /* like the real flash: programming a non-erased phrase fails */
        }
    }
    (void)memcpy(&flash[index], data, len);
    nowMs += programCostMs;
    return true;
}

bool hal_boot_request_consume(void)
{
    bool const r = bootRequest;
    bootRequest  = false;
    return r;
}

/* BL-33: the family hook, as host_power_on_after_reset() sets it; counts how often the core asks. */
bool hal_reset_skips_startup_window(void)
{
    skipQueries++;
    return skipsWindow;
}

void hal_reset(void) { resetRequested = true; }

void hal_jump_to_app(uint32_t appStart) { jumpAddress = appStart; }

#if defined(BL_DEVICE_SERVICE)
/* BL-28: device service of the core (bl_main.h), recorded instead of served. Built only by
 * bootloader/test/host/test_bl_sbc_watchdog_host.py, which defines BL_DEVICE_SERVICE like the bootloader.cmake of a
 * serving device hook does. */
static uint32_t serviceCalls;
static uint32_t serviceBeforeJump;
static uint32_t serviceAfterJump; /* calls made when the jump had already happened: must stay 0 */
static uint32_t serviceLastMs;

void bl_device_service(uint32_t nowMs, bool beforeJump)
{
    serviceCalls++;
    serviceLastMs = nowMs;
    if (beforeJump)
    {
        serviceBeforeJump++;
    }
    if (jumpAddress != 0U)
    {
        serviceAfterJump++;
    }
}

EXPORT uint32_t host_service_calls(void) { return serviceCalls; }

EXPORT uint32_t host_service_before_jump(void) { return serviceBeforeJump; }

EXPORT uint32_t host_service_after_jump(void) { return serviceAfterJump; }

EXPORT uint32_t host_service_last_ms(void) { return serviceLastMs; }
#endif

static void power_on(int bootRequestFlag, int skipsWindowFlag)
{
    rxQueue        = (queue_t){0};
    txQueue        = (queue_t){0};
    bootRequest    = (bootRequestFlag != 0);
    resetRequested = false;
    jumpAddress    = 0U;
    skipsWindow    = (skipsWindowFlag != 0);
    skipQueries    = 0U;
    appStartReads  = 0U;
    appEntryReads  = 0U;
#if defined(BL_DEVICE_SERVICE)
    serviceCalls      = 0U;
    serviceBeforeJump = 0U;
    serviceAfterJump  = 0U;
#endif
    bl_init();
}

EXPORT void host_power_on(int bootRequestFlag) { power_on(bootRequestFlag, 0); }

/* BL-33: a start whose reset the family hook lets pass without the startup window (s32k1xx: watchdog fast test) */
EXPORT void host_power_on_after_reset(int bootRequestFlag, int skipsWindowFlag)
{
    power_on(bootRequestFlag, skipsWindowFlag);
}

EXPORT uint32_t host_skip_queries(void) { return skipQueries; }

EXPORT void host_erase_all(void)
{
    (void)memset(flash, 0xFF, sizeof(flash));
    eraseLogLen = 0U;
}

EXPORT uint8_t* host_flash(void) { return flash; }

EXPORT uint32_t host_flash_size(void) { return BL_FLASH_SIZE; }

EXPORT uint32_t host_flash_start(void) { return BL_FLASH_START; }

EXPORT void host_set_costs(uint32_t eraseMs, uint32_t programMs)
{
    eraseCostMs      = eraseMs;
    eraseCostPerKbMs = 0U;
    programCostMs    = programMs;
}

/* Erase cost of one sector: fixed part plus a part per KB, so a 128-KB sector really costs more than a 16-KB one. */
EXPORT void host_set_erase_cost(uint32_t fixedMs, uint32_t perKbMs)
{
    eraseCostMs      = fixedMs;
    eraseCostPerKbMs = perKbMs;
}

EXPORT void host_clear_erase_log(void) { eraseLogLen = 0U; }

/* Copies the erased sectors (start, size) into `out`; returns how many were erased (may exceed `max`). */
EXPORT uint32_t host_erase_log(uint32_t* out, uint32_t max)
{
    uint32_t const n = (eraseLogLen < max) ? eraseLogLen : max;
    for (uint32_t i = 0U; i < n; ++i)
    {
        out[2U * i]        = eraseLog[i][0];
        out[(2U * i) + 1U] = eraseLog[i][1];
    }
    return eraseLogLen;
}

/* Sector table of this build (PLAT-16): self check, number of sectors, and one sector by index. */
EXPORT int host_sector_map_ok(void) { return bl_sector_map_ok() ? 1 : 0; }

EXPORT uint32_t host_sector_count(void) { return bl_sector_count(); }

EXPORT int host_sector_by_index(uint32_t index, uint32_t* start, uint32_t* size)
{
    bl_sector_t sector;
    if (!bl_sector_by_index(index, &sector))
    {
        return 0;
    }
    *start = sector.start;
    *size  = sector.size;
    return 1;
}

EXPORT int host_sector_at(uint32_t address, uint32_t* start, uint32_t* size, uint32_t* index)
{
    bl_sector_t sector;
    if (!bl_sector_at(address, &sector))
    {
        return 0;
    }
    *start = sector.start;
    *size  = sector.size;
    *index = sector.index;
    return 1;
}

EXPORT void host_fail_next_program(void) { failNextProgram = true; }

EXPORT int host_can_rx(uint32_t id, uint8_t const* data, uint8_t len)
{
    frame_t f = {id, len, {0}, nowMs};
    (void)memcpy(f.data, data, (len > 8U) ? 8U : len);
    return queue_push(&rxQueue, &f) ? 1 : 0;
}

EXPORT int host_can_tx(uint32_t* id, uint8_t* data, uint8_t* len)
{
    frame_t f;
    if (!queue_pop(&txQueue, &f))
    {
        return 0;
    }
    *id       = f.id;
    *len      = f.len;
    lastTxMs  = f.ms;
    (void)memcpy(data, f.data, f.len);
    return 1;
}

/* BL-25: when the core sent the frame host_can_tx() returned last. The time a test pops a frame can be much later:
 * a flash step runs inside one host_poll(), and the frame sent before it waits in the queue meanwhile. */
EXPORT uint32_t host_last_tx_ms(void) { return lastTxMs; }

EXPORT void host_poll(uint32_t advanceMs)
{
    nowMs += advanceMs;
    if ((jumpAddress == 0U) && !resetRequested)
    {
        pollReadings = 0U;
        tickApplied  = false;
        inPoll       = true;
        bl_poll();
        inPoll = false;
        if (asyncTick)
        {
            if (!tickApplied)
            {
                nowMs++; /* the tick of this poll came after its last reading */
            }
            tickAtReading = (tickAtReading % ASYNC_TICK_SPAN) + 1U;
        }
    }
}

/* BL-25: switches the asynchronous tick model on (1) or off (0), see hal_millis(). */
EXPORT void host_set_async_tick(int enable)
{
    asyncTick     = (enable != 0);
    tickAtReading = 1U;
}

EXPORT uint32_t host_millis(void) { return nowMs; }

EXPORT int host_reset_requested(void) { return resetRequested ? 1 : 0; }

EXPORT uint32_t host_jump_address(void) { return jumpAddress; }
