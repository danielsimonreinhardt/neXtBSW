#pragma once

/* SHA-256 (FIPS 180-4) and HMAC-SHA256 (RFC 2104) for the security access (BL-03). Own implementation, written
 * for size (one round loop, 16-word message schedule), checked against the NIST example vectors and RFC 4231 by
 * tools/canflash/test_bl_security_host.py. No third-party code. Implementation: sha256.c. */

#include <stdint.h>

#define SHA256_LEN       32U
#define SHA256_BLOCK_LEN 64U

typedef struct
{
    uint32_t state[8];
    uint32_t count; /* bytes hashed so far; messages stay far below 512 MB */
    uint8_t block[SHA256_BLOCK_LEN];
} sha256_t;

typedef struct
{
    sha256_t sha;
    uint8_t outerPad[SHA256_BLOCK_LEN]; /* key XOR opad, kept for the final step */
} hmac_sha256_t;

void sha256_init(sha256_t* ctx);
void sha256_update(sha256_t* ctx, uint8_t const* data, uint32_t len);
void sha256_final(sha256_t* ctx, uint8_t out[SHA256_LEN]);

/* Keys longer than one block are hashed first (RFC 2104); the bootloader only uses 32-byte keys. */
void hmac_sha256_init(hmac_sha256_t* ctx, uint8_t const* key, uint32_t keyLen);
void hmac_sha256_update(hmac_sha256_t* ctx, uint8_t const* data, uint32_t len);
void hmac_sha256_final(hmac_sha256_t* ctx, uint8_t out[SHA256_LEN]);
