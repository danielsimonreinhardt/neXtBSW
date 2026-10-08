/* SHA-256 and HMAC-SHA256, see sha256.h. */

#include "sha256.h"

#include <string.h>

static uint32_t const sha256K[64] = {
    0x428a2f98UL, 0x71374491UL, 0xb5c0fbcfUL, 0xe9b5dba5UL, 0x3956c25bUL, 0x59f111f1UL, 0x923f82a4UL, 0xab1c5ed5UL,
    0xd807aa98UL, 0x12835b01UL, 0x243185beUL, 0x550c7dc3UL, 0x72be5d74UL, 0x80deb1feUL, 0x9bdc06a7UL, 0xc19bf174UL,
    0xe49b69c1UL, 0xefbe4786UL, 0x0fc19dc6UL, 0x240ca1ccUL, 0x2de92c6fUL, 0x4a7484aaUL, 0x5cb0a9dcUL, 0x76f988daUL,
    0x983e5152UL, 0xa831c66dUL, 0xb00327c8UL, 0xbf597fc7UL, 0xc6e00bf3UL, 0xd5a79147UL, 0x06ca6351UL, 0x14292967UL,
    0x27b70a85UL, 0x2e1b2138UL, 0x4d2c6dfcUL, 0x53380d13UL, 0x650a7354UL, 0x766a0abbUL, 0x81c2c92eUL, 0x92722c85UL,
    0xa2bfe8a1UL, 0xa81a664bUL, 0xc24b8b70UL, 0xc76c51a3UL, 0xd192e819UL, 0xd6990624UL, 0xf40e3585UL, 0x106aa070UL,
    0x19a4c116UL, 0x1e376c08UL, 0x2748774cUL, 0x34b0bcb5UL, 0x391c0cb3UL, 0x4ed8aa4aUL, 0x5b9cca4fUL, 0x682e6ff3UL,
    0x748f82eeUL, 0x78a5636fUL, 0x84c87814UL, 0x8cc70208UL, 0x90befffaUL, 0xa4506cebUL, 0xbef9a3f7UL, 0xc67178f2UL};

static uint32_t sha256_ror(uint32_t x, uint32_t n) { return (x >> n) | (x << (32U - n)); }

static uint32_t sha256_be32(uint8_t const* p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static void sha256_put_be32(uint8_t* p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

/* One block. The message schedule lives in a ring of 16 words: W[t-16] is w[t & 15], W[t-15] w[(t + 1) & 15],
 * W[t-7] w[(t + 9) & 15] and W[t-2] w[(t + 14) & 15]. */
static void sha256_block(uint32_t state[8], uint8_t const* block)
{
    uint32_t w[16];
    uint32_t v[8];
    (void)memcpy(v, state, sizeof(v));
    for (uint32_t t = 0U; t < 64U; ++t)
    {
        uint32_t wt;
        if (t < 16U)
        {
            wt = sha256_be32(&block[4U * t]);
        }
        else
        {
            uint32_t const w15 = w[(t + 1U) & 15U];
            uint32_t const w2  = w[(t + 14U) & 15U];
            wt = w[t & 15U] + (sha256_ror(w15, 7U) ^ sha256_ror(w15, 18U) ^ (w15 >> 3))
                 + w[(t + 9U) & 15U] + (sha256_ror(w2, 17U) ^ sha256_ror(w2, 19U) ^ (w2 >> 10));
        }
        w[t & 15U]        = wt;
        uint32_t const e  = v[4];
        uint32_t const a  = v[0];
        uint32_t const t1 = v[7] + (sha256_ror(e, 6U) ^ sha256_ror(e, 11U) ^ sha256_ror(e, 25U))
                            + ((e & v[5]) ^ (~e & v[6])) + sha256K[t] + wt;
        uint32_t const t2 = (sha256_ror(a, 2U) ^ sha256_ror(a, 13U) ^ sha256_ror(a, 22U))
                            + ((a & v[1]) ^ (a & v[2]) ^ (v[1] & v[2]));
        for (uint32_t j = 7U; j > 0U; --j)
        {
            v[j] = v[j - 1U];
        }
        v[4] += t1;
        v[0] = t1 + t2;
    }
    for (uint32_t j = 0U; j < 8U; ++j)
    {
        state[j] += v[j];
    }
}

void sha256_init(sha256_t* ctx)
{
    static uint32_t const initial[8] = {0x6a09e667UL, 0xbb67ae85UL, 0x3c6ef372UL, 0xa54ff53aUL,
                                        0x510e527fUL, 0x9b05688cUL, 0x1f83d9abUL, 0x5be0cd19UL};
    (void)memcpy(ctx->state, initial, sizeof(initial));
    ctx->count = 0U;
}

void sha256_update(sha256_t* ctx, uint8_t const* data, uint32_t len)
{
    for (uint32_t i = 0U; i < len; ++i)
    {
        uint32_t const used = ctx->count % SHA256_BLOCK_LEN;
        ctx->block[used]    = data[i];
        ctx->count++;
        if (used == (SHA256_BLOCK_LEN - 1U))
        {
            sha256_block(ctx->state, ctx->block);
        }
    }
}

void sha256_final(sha256_t* ctx, uint8_t out[SHA256_LEN])
{
    uint32_t const bytes = ctx->count;
    uint8_t pad          = 0x80U;
    /* 0x80, zeros up to 56 bytes of the last block, then the length in bits as 64-bit big endian */
    do
    {
        sha256_update(ctx, &pad, 1U);
        pad = 0U;
    } while ((ctx->count % SHA256_BLOCK_LEN) != 56U);
    uint8_t length[8];
    sha256_put_be32(&length[0], bytes >> 29);
    sha256_put_be32(&length[4], bytes << 3);
    sha256_update(ctx, length, sizeof(length));
    for (uint32_t j = 0U; j < 8U; ++j)
    {
        sha256_put_be32(&out[4U * j], ctx->state[j]);
    }
}

/* ipad/opad of RFC 2104: the key block XOR 0x36 goes into the inner hash, XOR 0x5C into the outer one */
void hmac_sha256_init(hmac_sha256_t* ctx, uint8_t const* key, uint32_t keyLen)
{
    uint8_t block[SHA256_BLOCK_LEN];
    (void)memset(block, 0, sizeof(block));
    if (keyLen > SHA256_BLOCK_LEN)
    {
        sha256_init(&ctx->sha);
        sha256_update(&ctx->sha, key, keyLen);
        sha256_final(&ctx->sha, block);
    }
    else
    {
        (void)memcpy(block, key, keyLen);
    }
    for (uint32_t i = 0U; i < SHA256_BLOCK_LEN; ++i)
    {
        ctx->outerPad[i] = (uint8_t)(block[i] ^ 0x5CU);
        block[i]         = (uint8_t)(block[i] ^ 0x36U);
    }
    sha256_init(&ctx->sha);
    sha256_update(&ctx->sha, block, SHA256_BLOCK_LEN);
    (void)memset(block, 0, sizeof(block));
}

void hmac_sha256_update(hmac_sha256_t* ctx, uint8_t const* data, uint32_t len) { sha256_update(&ctx->sha, data, len); }

void hmac_sha256_final(hmac_sha256_t* ctx, uint8_t out[SHA256_LEN])
{
    uint8_t inner[SHA256_LEN];
    sha256_final(&ctx->sha, inner);
    sha256_init(&ctx->sha);
    sha256_update(&ctx->sha, ctx->outerPad, SHA256_BLOCK_LEN);
    sha256_update(&ctx->sha, inner, SHA256_LEN);
    sha256_final(&ctx->sha, out);
    (void)memset(ctx->outerPad, 0, sizeof(ctx->outerPad));
}
