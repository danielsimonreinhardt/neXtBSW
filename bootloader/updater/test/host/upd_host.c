/* Host build of the updater core: flash model with power-loss injection, driven from Python
 * (tools/canflash/test_bl_update_host.py). */

#include "bl_config.h"
#include "bl_sectors.h"
#include "bl_update.h"
#include "upd_hal.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

#define OP_LOG_SIZE     4096U
#define REPORT_LOG_SIZE 64U
#define CUT_NONE        0xFFFFFFFFUL

typedef struct
{
    uint8_t kind; /* 'E' erase, 'P' program phrase */
    uint32_t address;
} op_t;

static uint8_t flash[BL_FLASH_SIZE];
static uint64_t nowUs;
static uint32_t eraseCostUs   = 20000UL;
static uint32_t programCostUs = 100UL;

static uint32_t ops; /* executed (or interrupted) flash operations since host_arm */
static op_t opLog[OP_LOG_SIZE];
static uint32_t cutAt = CUT_NONE;
static bool cutDuring;
static bool powerLost;
static uint32_t guardViolations;
static uint32_t corruptAddress = CUT_NONE; /* programs of this phrase get one flipped bit (verify test) */
static bool corruptOnce;
static uint32_t corruptCount;
static uint32_t rng = 1UL;

static uint8_t reportLog[REPORT_LOG_SIZE][2];
static uint32_t reports;

static uint8_t rand8(void)
{
    rng = (rng * 1103515245UL) + 12345UL;
    return (uint8_t)(rng >> 16);
}

/* Bootloader area or exactly the record sector (from the sector table, PLAT-16). */
static bool allowed(uint32_t address, uint32_t len)
{
    bl_sector_t record;
    bool ok = bl_range_inside(address, len, BL_FLASH_START, BL_APP_START - BL_FLASH_START);
    if (!ok && bl_record_sector(&record))
    {
        ok = bl_range_inside(address, len, record.start, record.size);
    }
    if (!ok)
    {
        guardViolations++;
    }
    return ok;
}

/* Returns false if the power is (or just went) off; a cut during the operation leaves partial content. */
static bool begin_op(uint8_t kind, uint32_t address, uint32_t len, uint8_t const* data)
{
    if (powerLost)
    {
        return false;
    }
    if (ops < OP_LOG_SIZE)
    {
        opLog[ops] = (op_t){kind, address};
    }
    if (ops == cutAt)
    {
        powerLost = true;
        if (cutDuring)
        {
            for (uint32_t i = 0U; i < len; ++i)
            {
                if (kind == 'E')
                {
                    uint8_t const r = rand8();
                    flash[address + i] = (uint8_t)(flash[address + i] | r); /* some bits erased */
                }
                else
                {
                    flash[address + i] = (uint8_t)(flash[address + i] & (data[i] | rand8())); /* some bits programmed */
                }
            }
        }
        ops++;
        return false;
    }
    ops++;
    return true;
}

uint8_t const* upd_flash_ptr(uint32_t address) { return &flash[address]; }

bool upd_flash_erase_sector(uint32_t address)
{
    bl_sector_t sector;
    if (!bl_sector_at(address, &sector) || (sector.start != address) || !allowed(address, sector.size))
    {
        return false;
    }
    if (!begin_op('E', address, sector.size, NULL))
    {
        return false;
    }
    (void)memset(&flash[address], 0xFF, sector.size);
    nowUs += eraseCostUs;
    return true;
}

bool upd_flash_program_phrase(uint32_t address, uint8_t const* data)
{
    if (((address % BL_PHRASE_SIZE) != 0U) || !allowed(address, BL_PHRASE_SIZE))
    {
        return false;
    }
    bool erased = true;
    for (uint32_t i = 0U; i < BL_PHRASE_SIZE; ++i)
    {
        erased = erased && (flash[address + i] == 0xFFU);
    }
    if (!begin_op('P', address, BL_PHRASE_SIZE, data))
    {
        return false;
    }
    nowUs += programCostUs;
    for (uint32_t i = 0U; i < BL_PHRASE_SIZE; ++i)
    {
        flash[address + i] = (uint8_t)(flash[address + i] & data[i]); /* bits can only be cleared */
    }
    if ((address == corruptAddress) && (!corruptOnce || (corruptCount == 0U)))
    {
        corruptCount++;
        flash[address] = (uint8_t)(flash[address] ^ 0x01U);
    }
    return erased; /* like FTFC: programming a non-erased phrase reports an error */
}

uint32_t upd_micros(void) { return (uint32_t)nowUs; }

void upd_report(uint8_t step, uint8_t detail, upd_stats_t const* stats)
{
    (void)stats;
    if (reports < REPORT_LOG_SIZE)
    {
        reportLog[reports][0] = step;
        reportLog[reports][1] = detail;
    }
    reports++;
}

EXPORT uint8_t* host_flash(void) { return flash; }

EXPORT uint32_t host_flash_size(void) { return BL_FLASH_SIZE; }

EXPORT void host_erase_all(void) { (void)memset(flash, 0xFF, sizeof(flash)); }

EXPORT void host_set_costs(uint32_t eraseUs, uint32_t programUs)
{
    eraseCostUs   = eraseUs;
    programCostUs = programUs;
}

/* Arms a power loss before (during = 0) or in the middle of (during = 1) flash operation `at`,
 * counted from this call; CUT_NONE disables it. Power is on again afterwards. */
EXPORT void host_arm(uint32_t at, int during, uint32_t seed)
{
    ops       = 0U;
    cutAt     = at;
    cutDuring = (during != 0);
    powerLost = false;
    rng       = seed;
    reports   = 0U;
}

EXPORT void host_corrupt(uint32_t address, int once)
{
    corruptAddress = address;
    corruptOnce    = (once != 0);
    corruptCount   = 0U;
}

EXPORT uint32_t host_corrupt_count(void) { return corruptCount; }

EXPORT int host_run(uint8_t const* blob, uint32_t blobLen, int environmentOk, int forceRewrite, upd_stats_t* stats)
{
    return (int)upd_run(blob, blobLen, environmentOk != 0, forceRewrite != 0, stats);
}

EXPORT int host_check(uint8_t const* blob, uint32_t blobLen) { return (int)upd_check_blob(blob, blobLen); }

EXPORT uint32_t host_ops(void) { return ops; }

EXPORT int host_power_lost(void) { return powerLost ? 1 : 0; }

EXPORT uint32_t host_op(uint32_t index, uint8_t* kind)
{
    *kind = (index < OP_LOG_SIZE) ? opLog[index].kind : 0U;
    return (index < OP_LOG_SIZE) ? opLog[index].address : 0U;
}

EXPORT uint32_t host_guard_violations(void) { return guardViolations; }

EXPORT uint32_t host_reports(void) { return reports; }

EXPORT uint32_t host_report(uint32_t index)
{
    return (index < REPORT_LOG_SIZE) ? (((uint32_t)reportLog[index][0] << 8) | reportLog[index][1]) : 0U;
}
