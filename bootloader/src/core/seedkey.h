#pragma once

/* Seed and key of the security access (BL-03), method BL_SECURITY_METHOD_HMAC_SHA256 (bl_config.h).
 *
 * - Device key: 32 bytes in the key record of the key sector (BL_KEY_ADDR), provisioned per device over the
 *   debugger. The bootloader image is the same for every device; without a valid record the security access is
 *   refused.
 * - Seed: 16 bytes, the first half of HMAC-SHA256(device key, pool || counter). The pool mixes every source the
 *   bootloader has (timer states at the moment of the request, the optional hardware random generator
 *   hal_random(), the chip UID, the content of the no-init RAM after power-on) and is stirred before every seed.
 *   Pool and counter live in no-init RAM: a reset does not bring an earlier seed back, and with the key in the
 *   HMAC a tester cannot predict a seed from the sources alone.
 * - Key: HMAC-SHA256(device key, seed), the full 32 bytes, compared in constant time.
 * - The failed attempts of the lockout survive a reset in the same no-init RAM (an ECU reset does not clear them).
 *
 * Implementation: seedkey.c. */

#include "bl_config.h"

#include <stdbool.h>
#include <stdint.h>

#if BL_SECURITY_METHOD != BL_SECURITY_METHOD_HMAC_SHA256
#error "bootloader: security access method BL_SECURITY_METHOD is not implemented (bl_config.h, BL-03)"
#endif

/* State of the key record, reported by DID 0xF1A1 */
typedef enum
{
    SEEDKEY_KEY_MISSING     = 0, /* key sector erased: device not provisioned */
    SEEDKEY_KEY_PROVISIONED = 1,
    SEEDKEY_KEY_INVALID     = 2  /* something is there, but not a record of this method (CRC, method, length) */
} seedkey_state_t;

/* Once at start-up: checks the no-init state and mixes the start-up sources into the pool. */
void seedkey_init(void);
seedkey_state_t seedkey_key_state(void);
/* Next seed; false without a provisioned key (then nothing is written). */
bool seedkey_seed(uint8_t seed[BL_SECURITY_SEED_LEN]);
bool seedkey_key_matches(uint8_t const seed[BL_SECURITY_SEED_LEN], uint8_t const key[BL_SECURITY_KEY_LEN]);
void seedkey_uid(uint8_t uid[BL_UID_LEN]);
/* Failed attempts and lockout, kept across a reset */
uint8_t seedkey_failed_attempts(void);
bool seedkey_lockout_pending(void);
void seedkey_store_attempts(uint8_t failedAttempts, bool lockout);
