/* Host part of the bootloader's security access (BL-03), linked into the host DLL next to
 * bootloader/test/host/bl_host.c (tools/canflash/hostbuild.py): simulated chip UID, key sector and hardware random
 * generator, and the SHA-256/HMAC functions of the core as DLL entry points for the NIST/RFC test vectors.
 *
 * Key sector: kept apart from the simulated flash array of bl_host.c (whose host_erase_all() clears everything), and
 * provisioned by default with the device key of the insecure test key (tools/canflash/keys, passed in by hostbuild.py
 * as BL_HOST_TEST_MASTER_KEY) for the current UID - so tests that flash through the bootloader without knowing BL-03
 * (ecu/uds app-uds-host) find a provisioned device. The BL-03 tests replace it with host_set_key_record(). */

#include "bl_config.h"
#include "crc32.h"
#include "sha256.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

void seedkey_host_power_loss(uint8_t fill); /* bootloader/src/core/seedkey.c, host builds only */

/* Default UID: 0x00 0x01 0x02 ... in the order the core reads the words (most significant byte first). */
static uint32_t uid[8] = {0x00010203UL, 0x04050607UL, 0x08090A0BUL, 0x0C0D0E0FUL,
                          0x10111213UL, 0x14151617UL, 0x18191A1BUL, 0x1C1D1E1FUL};

static bool randomAvailable;
static uint8_t randomByte;
static uint32_t randomCalls;

uint32_t host_uid_word(uint32_t index) { return (index < 8U) ? uid[index] : 0UL; }

#ifndef BL_HOST_TEST_MASTER_KEY
#error "tools/canflash/hostbuild.py passes the insecure test key as BL_HOST_TEST_MASTER_KEY"
#endif
static uint8_t const testMasterKey[32] = {BL_HOST_TEST_MASTER_KEY};
static uint8_t keyRecord[BL_KEY_RECORD_LEN];
static bool keyRecordSet;

/* Key record as tools/canflash/blkeys.py key_record() writes it, for the insecure test key and the current UID */
static void provision_test_key(void)
{
    uint8_t uidBytes[BL_UID_LEN];
    for (uint32_t i = 0U; i < BL_UID_LEN; ++i)
    {
        uidBytes[i] = (uint8_t)(uid[i / 4U] >> (24U - (8U * (i % 4U))));
    }
    hmac_sha256_t mac;
    (void)memset(keyRecord, 0xFF, sizeof(keyRecord));
    keyRecord[0] = (uint8_t)'N';
    keyRecord[1] = (uint8_t)'X';
    keyRecord[2] = (uint8_t)'K';
    keyRecord[3] = (uint8_t)'Y';
    keyRecord[4] = BL_KEY_RECORD_VERSION;
    keyRecord[5] = BL_SECURITY_METHOD;
    keyRecord[6] = BL_SECURITY_DEVICE_KEY_LEN;
    hmac_sha256_init(&mac, testMasterKey, sizeof(testMasterKey));
    hmac_sha256_update(&mac, uidBytes, sizeof(uidBytes));
    hmac_sha256_final(&mac, &keyRecord[8]);
    uint32_t const crc = crc32_update(0U, keyRecord, 40U);
    keyRecord[40]      = (uint8_t)crc;
    keyRecord[41]      = (uint8_t)(crc >> 8);
    keyRecord[42]      = (uint8_t)(crc >> 16);
    keyRecord[43]      = (uint8_t)(crc >> 24);
}

uint8_t const* host_key_record(void)
{
    if (!keyRecordSet)
    {
        provision_test_key();
        keyRecordSet = true;
    }
    return keyRecord;
}

/* Content of the key sector (BL_KEY_RECORD_LEN bytes; 0xFF: erased, i.e. not provisioned) */
EXPORT void host_set_key_record(uint8_t const* data, uint32_t len)
{
    (void)memset(keyRecord, 0xFF, sizeof(keyRecord));
    (void)memcpy(keyRecord, data, (len < sizeof(keyRecord)) ? len : sizeof(keyRecord));
    keyRecordSet = true;
}

/* Back to the default: provisioned with the insecure test key for the UID at the next access */
EXPORT void host_provision_test_key(void) { keyRecordSet = false; }

EXPORT void host_key_record_copy(uint8_t* out) { (void)memcpy(out, host_key_record(), BL_KEY_RECORD_LEN); }

/* The family HAL of a derivative with a random generator; the core's weak default is replaced here. */
bool hal_random(uint8_t* out, uint32_t len)
{
    randomCalls++;
    if (!randomAvailable)
    {
        return false;
    }
    for (uint32_t i = 0U; i < len; ++i)
    {
        out[i] = randomByte++;
    }
    return true;
}

EXPORT uint32_t host_uid_len(void) { return BL_UID_LEN; }

/* UID as the bytes DID 0xF18C reports (`len` = BL_UID_LEN) */
EXPORT void host_set_uid(uint8_t const* bytes, uint32_t len)
{
    for (uint32_t i = 0U; (i < 8U) && ((4U * i) + 3U < len); ++i)
    {
        uid[i] = ((uint32_t)bytes[4U * i] << 24) | ((uint32_t)bytes[(4U * i) + 1U] << 16)
                 | ((uint32_t)bytes[(4U * i) + 2U] << 8) | (uint32_t)bytes[(4U * i) + 3U];
    }
}

EXPORT void host_set_random(int available, uint8_t first)
{
    randomAvailable = (available != 0);
    randomByte      = first;
}

EXPORT uint32_t host_random_calls(void) { return randomCalls; }

EXPORT void host_power_loss(uint8_t fill) { seedkey_host_power_loss(fill); }

EXPORT void host_sha256(uint8_t const* data, uint32_t len, uint8_t* out)
{
    sha256_t sha;
    sha256_init(&sha);
    sha256_update(&sha, data, len);
    sha256_final(&sha, out);
}

/* The same message fed in pieces of `chunk` bytes: block boundaries inside an update call */
EXPORT void host_sha256_chunked(uint8_t const* data, uint32_t len, uint32_t chunk, uint8_t* out)
{
    sha256_t sha;
    sha256_init(&sha);
    for (uint32_t pos = 0U; pos < len; pos += chunk)
    {
        sha256_update(&sha, &data[pos], ((len - pos) < chunk) ? (len - pos) : chunk);
    }
    sha256_final(&sha, out);
}

EXPORT void host_hmac_sha256(uint8_t const* key, uint32_t keyLen, uint8_t const* data, uint32_t len, uint8_t* out)
{
    hmac_sha256_t mac;
    hmac_sha256_init(&mac, key, keyLen);
    hmac_sha256_update(&mac, data, len);
    hmac_sha256_final(&mac, out);
}
