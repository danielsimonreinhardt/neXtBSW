/* Seed and key of the security access (BL-03), see seedkey.h. */

#include "bl_hal.h"
#include "crc32.h"
#include "seedkey.h"
#include "sha256.h"

#include <stddef.h>
#include <string.h>

/* Placement of the state that survives a reset: section .noinit of the family linker scripts (NOLOAD, not
 * cleared by the startup code). Host builds keep it in a normal static variable, which a simulated reset
 * (host_power_on) does not clear either. */
#ifndef BL_NOINIT
#if defined(__arm__)
#define BL_NOINIT __attribute__((section(".noinit")))
#else
#define BL_NOINIT
#endif
#endif

/* The chip UID: memory mapped 32-bit words (derivative uid). The host build reads a simulated UID instead
 * (tools/canflash/host/bl_host_security.c). */
#ifdef BL_HOST_BUILD
uint32_t host_uid_word(uint32_t index);
#define BL_UID_WORD(i) host_uid_word(i)
#else
#define BL_UID_WORD(i) (*(uint32_t const volatile*)(BL_UID_ADDR + (4UL * (i))))
#endif

/* The key record at the start of the key sector. The host build keeps its key sector apart from the simulated flash
 * array: host_erase_all() of the host HAL clears the whole array, and tests of other modules that flash through
 * the bootloader find a device provisioned with the insecure test key (bl_host_security.c). */
#ifdef BL_HOST_BUILD
uint8_t const* host_key_record(void);
#define SEEDKEY_RECORD() host_key_record()
#else
#define SEEDKEY_RECORD() hal_flash_ptr(BL_KEY_ADDR)
#endif

#if ((BL_UID_LEN % 4U) != 0U) || (BL_UID_LEN == 0U) || (BL_UID_LEN > 32U)
#error "BL_UID_LEN must be a multiple of 4 between 4 and 32 (mcudb uid.size)"
#endif

#define SEEDKEY_NOINIT_MAGIC 0x4E58534BUL /* "NXSK" */
#define SEEDKEY_POOL_LEN     SHA256_LEN

typedef struct
{
    uint32_t magic;
    uint32_t counter; /* seeds issued since the state was valid; part of every seed's input */
    uint8_t pool[SEEDKEY_POOL_LEN];
    uint8_t failedAttempts;
    uint8_t lockout;
    uint8_t reserved[2];
    uint32_t crc; /* CRC-32 over everything before it */
} seedkey_noinit_t;

static seedkey_noinit_t seedkeyState BL_NOINIT;

#ifdef BL_HOST_BUILD
/* Host tests: the no-init RAM after a power loss (any content) instead of after a reset (content kept). */
void seedkey_host_power_loss(uint8_t fill);
void seedkey_host_power_loss(uint8_t fill) { (void)memset(&seedkeyState, fill, sizeof(seedkeyState)); }
#endif

/* Optional HAL function (bl_hal.h): a family with a hardware random generator defines it; without one, this
 * default reports "no random bytes" and the other sources carry the seed alone. */
__attribute__((weak)) bool hal_random(uint8_t* out, uint32_t len)
{
    (void)out;
    (void)len;
    return false;
}

static uint32_t seedkey_state_crc(void)
{
    return crc32_update(0U, (uint8_t const*)&seedkeyState, (uint32_t)offsetof(seedkey_noinit_t, crc));
}

static void seedkey_save(void) { seedkeyState.crc = seedkey_state_crc(); }

/* Sources available at this moment: timers (their value at a request depends on when the tester sent it), the
 * hardware random generator if the family has one. */
static void seedkey_stir(sha256_t* sha)
{
    uint32_t sources[4];
    sources[0] = hal_entropy();
    sources[1] = hal_millis();
    sources[2] = seedkeyState.counter;
    sources[3] = hal_entropy();
    sha256_update(sha, (uint8_t const*)sources, sizeof(sources));
    uint8_t random[16];
    if (hal_random(random, sizeof(random)))
    {
        sha256_update(sha, random, sizeof(random));
    }
}

void seedkey_uid(uint8_t uid[BL_UID_LEN])
{
    for (uint32_t i = 0U; i < (BL_UID_LEN / 4U); ++i)
    {
        uint32_t const word = BL_UID_WORD(i);
        uid[4U * i]         = (uint8_t)(word >> 24);
        uid[(4U * i) + 1U]  = (uint8_t)(word >> 16);
        uid[(4U * i) + 2U]  = (uint8_t)(word >> 8);
        uid[(4U * i) + 3U]  = (uint8_t)word;
    }
}

void seedkey_init(void)
{
    bool const valid = (seedkeyState.magic == SEEDKEY_NOINIT_MAGIC) && (seedkeyState.crc == seedkey_state_crc());
    sha256_t sha;
    sha256_init(&sha);
    /* After power-on the no-init RAM holds whatever the cells came up with: hashed in, never trusted. */
    sha256_update(&sha, (uint8_t const*)&seedkeyState, sizeof(seedkeyState));
    uint8_t uid[BL_UID_LEN];
    seedkey_uid(uid);
    sha256_update(&sha, uid, sizeof(uid));
    seedkey_stir(&sha);
    sha256_final(&sha, seedkeyState.pool);
    if (!valid)
    {
        seedkeyState.magic          = SEEDKEY_NOINIT_MAGIC;
        seedkeyState.counter        = 0U;
        seedkeyState.failedAttempts = 0U;
        seedkeyState.lockout        = 0U;
        seedkeyState.reserved[0]    = 0U;
        seedkeyState.reserved[1]    = 0U;
    }
    seedkey_save();
}

/* Device key of the key record, NULL if there is no valid record */
static uint8_t const* seedkey_device_key(seedkey_state_t* state)
{
    uint8_t const* const record = SEEDKEY_RECORD();
    bool erased                 = true;
    for (uint32_t i = 0U; i < BL_KEY_RECORD_LEN; ++i)
    {
        erased = erased && (record[i] == 0xFFU);
    }
    uint32_t const crc = (uint32_t)record[40] | ((uint32_t)record[41] << 8) | ((uint32_t)record[42] << 16)
                         | ((uint32_t)record[43] << 24);
    if ((record[0] == (uint8_t)'N') && (record[1] == (uint8_t)'X') && (record[2] == (uint8_t)'K')
        && (record[3] == (uint8_t)'Y') && (record[4] == BL_KEY_RECORD_VERSION) && (record[5] == BL_SECURITY_METHOD)
        && (record[6] == BL_SECURITY_DEVICE_KEY_LEN) && (crc == crc32_update(0U, record, 40U)))
    {
        *state = SEEDKEY_KEY_PROVISIONED;
        return &record[8];
    }
    *state = erased ? SEEDKEY_KEY_MISSING : SEEDKEY_KEY_INVALID;
    return NULL;
}

seedkey_state_t seedkey_key_state(void)
{
    seedkey_state_t state;
    (void)seedkey_device_key(&state);
    return state;
}

bool seedkey_seed(uint8_t seed[BL_SECURITY_SEED_LEN])
{
    seedkey_state_t state;
    uint8_t const* const key = seedkey_device_key(&state);
    if (key == NULL)
    {
        return false;
    }
    seedkeyState.counter++;
    sha256_t sha;
    sha256_init(&sha);
    sha256_update(&sha, seedkeyState.pool, SEEDKEY_POOL_LEN);
    seedkey_stir(&sha);
    sha256_final(&sha, seedkeyState.pool);
    seedkey_save();

    hmac_sha256_t mac;
    uint8_t out[SHA256_LEN];
    hmac_sha256_init(&mac, key, BL_SECURITY_DEVICE_KEY_LEN);
    hmac_sha256_update(&mac, seedkeyState.pool, SEEDKEY_POOL_LEN);
    hmac_sha256_update(&mac, (uint8_t const*)&seedkeyState.counter, sizeof(seedkeyState.counter));
    hmac_sha256_final(&mac, out);
    (void)memcpy(seed, out, BL_SECURITY_SEED_LEN);
    /* An all-zero seed means "already unlocked" (ISO 14229-1): never hand one out as a challenge. */
    uint8_t any = 0U;
    for (uint32_t i = 0U; i < BL_SECURITY_SEED_LEN; ++i)
    {
        any |= seed[i];
    }
    if (any == 0U)
    {
        seed[BL_SECURITY_SEED_LEN - 1U] = 1U;
    }
    return true;
}

bool seedkey_key_matches(uint8_t const seed[BL_SECURITY_SEED_LEN], uint8_t const key[BL_SECURITY_KEY_LEN])
{
    seedkey_state_t state;
    uint8_t const* const deviceKey = seedkey_device_key(&state);
    if (deviceKey == NULL)
    {
        return false;
    }
    hmac_sha256_t mac;
    uint8_t expected[SHA256_LEN];
    hmac_sha256_init(&mac, deviceKey, BL_SECURITY_DEVICE_KEY_LEN);
    hmac_sha256_update(&mac, seed, BL_SECURITY_SEED_LEN);
    hmac_sha256_final(&mac, expected);
    /* constant time: every byte is compared, whatever the first difference */
    uint8_t diff = 0U;
    for (uint32_t i = 0U; i < BL_SECURITY_KEY_LEN; ++i)
    {
        diff |= (uint8_t)(expected[i] ^ key[i]);
    }
    (void)memset(expected, 0, sizeof(expected));
    return diff == 0U;
}

uint8_t seedkey_failed_attempts(void) { return seedkeyState.failedAttempts; }

bool seedkey_lockout_pending(void) { return seedkeyState.lockout != 0U; }

void seedkey_store_attempts(uint8_t failedAttempts, bool lockout)
{
    seedkeyState.failedAttempts = failedAttempts;
    seedkeyState.lockout        = lockout ? 1U : 0U;
    seedkey_save();
}
