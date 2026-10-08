#include "uds/app/DeviceKeySecurity.h"

#include <bl_config.h>
#include <time/TimestampProvider.h>

// SHA-256, HMAC-SHA256 and CRC-32 of the bootloader core (BL-03), compiled into the application as they are
extern "C"
{
#include <crc32.h>
#include <sha256.h>
}

#include <algorithm>
#include <cstring>

static_assert(
    ::uds::app::SECURITY_METHOD_HMAC_SHA256 == BL_SECURITY_METHOD_HMAC_SHA256,
    "method numbers of the description and of the bootloader differ (bl_config.h BL_SECURITY_METHOD_*)");
static_assert(
    ::uds::app::DeviceKeySecurity::MAC_LENGTH == SHA256_LEN, "seed and key are cut from one HMAC-SHA256");

namespace uds
{
namespace app
{
namespace
{
// Layout of the key record, see bl_config.h
constexpr size_t RECORD_METHOD     = 5U;
constexpr size_t RECORD_VERSION    = 4U;
constexpr size_t RECORD_KEY_LENGTH = 6U;
constexpr size_t RECORD_KEY        = 8U;
constexpr size_t RECORD_CRC        = RECORD_KEY + BL_SECURITY_DEVICE_KEY_LEN;
constexpr uint8_t const RECORD_MAGIC[] = {'N', 'X', 'K', 'Y'};

// Labels that keep the inputs of the application's HMACs apart from each other and from the bootloader's
constexpr uint8_t const KEY_LABEL[]  = {'N', 'X', 'A', 'K'};
constexpr uint8_t const SEED_LABEL[] = {'N', 'X', 'S', 'D'};
constexpr uint8_t const POOL_LABEL[] = {'N', 'X', 'P', 'L'};

void putUint32(uint8_t out[4], uint32_t const value)
{
    out[0] = static_cast<uint8_t>(value >> 24U);
    out[1] = static_cast<uint8_t>(value >> 16U);
    out[2] = static_cast<uint8_t>(value >> 8U);
    out[3] = static_cast<uint8_t>(value);
}

void hashTimer(sha256_t& sha)
{
    uint8_t timer[4];
    putUint32(timer, ::bsw::time::TimestampProvider::getTimestampUs32Bit());
    sha256_update(&sha, timer, sizeof(timer));
}
} // namespace

DeviceKeySecurity::DeviceKeySecurity(
    ::etl::span<uint8_t const> const keyRecord, ::etl::span<uint8_t const> const uid)
: _record(keyRecord), _pool(), _counter(0U)
{
    sha256_t sha;
    sha256_init(&sha);
    sha256_update(&sha, POOL_LABEL, sizeof(POOL_LABEL));
    sha256_update(&sha, uid.data(), static_cast<uint32_t>(uid.size()));
    hashTimer(sha);
    sha256_final(&sha, _pool);
}

uint8_t const* DeviceKeySecurity::deviceKey(KeyState& state) const
{
    if (_record.size() < BL_KEY_RECORD_LEN)
    {
        state = KeyState::INVALID;
        return nullptr;
    }
    uint8_t const* const record = _record.data();
    bool erased                 = true;
    for (size_t i = 0U; i < BL_KEY_RECORD_LEN; ++i)
    {
        erased = erased && (record[i] == 0xFFU);
    }
    uint32_t const crc = static_cast<uint32_t>(record[RECORD_CRC])
                         | (static_cast<uint32_t>(record[RECORD_CRC + 1U]) << 8U)
                         | (static_cast<uint32_t>(record[RECORD_CRC + 2U]) << 16U)
                         | (static_cast<uint32_t>(record[RECORD_CRC + 3U]) << 24U);
    if ((::memcmp(record, RECORD_MAGIC, sizeof(RECORD_MAGIC)) == 0)
        && (record[RECORD_VERSION] == BL_KEY_RECORD_VERSION)
        && (record[RECORD_METHOD] == SECURITY_METHOD_HMAC_SHA256)
        && (record[RECORD_KEY_LENGTH] == BL_SECURITY_DEVICE_KEY_LEN)
        && (crc == crc32_update(0U, record, static_cast<uint32_t>(RECORD_CRC))))
    {
        state = KeyState::PROVISIONED;
        return &record[RECORD_KEY];
    }
    state = erased ? KeyState::MISSING : KeyState::INVALID;
    return nullptr;
}

DeviceKeySecurity::KeyState DeviceKeySecurity::keyState() const
{
    KeyState state = KeyState::MISSING;
    (void)deviceKey(state);
    return state;
}

void DeviceKeySecurity::stir()
{
    ++_counter;
    uint8_t counter[4];
    putUint32(counter, _counter);
    sha256_t sha;
    sha256_init(&sha);
    sha256_update(&sha, _pool, sizeof(_pool));
    sha256_update(&sha, counter, sizeof(counter));
    hashTimer(sha);
    sha256_final(&sha, _pool);
}

bool DeviceKeySecurity::makeSeed(SecurityLevel const& level, ::etl::span<uint8_t> const seed)
{
    KeyState state = KeyState::MISSING;
    uint8_t const* const key = deviceKey(state);
    if ((key == nullptr) || (level.method != SECURITY_METHOD_HMAC_SHA256) || seed.empty()
        || (seed.size() > MAC_LENGTH))
    {
        return false;
    }
    stir();
    uint8_t counter[4];
    putUint32(counter, _counter);
    hmac_sha256_t mac;
    uint8_t out[SHA256_LEN];
    hmac_sha256_init(&mac, key, BL_SECURITY_DEVICE_KEY_LEN);
    hmac_sha256_update(&mac, SEED_LABEL, sizeof(SEED_LABEL));
    hmac_sha256_update(&mac, _pool, sizeof(_pool));
    hmac_sha256_update(&mac, counter, sizeof(counter));
    hmac_sha256_update(&mac, &level.requestSeed, 1U);
    hmac_sha256_final(&mac, out);
    (void)::memcpy(seed.data(), out, seed.size());
    if (std::all_of(seed.begin(), seed.end(), [](uint8_t const byte) { return byte == 0U; }))
    {
        seed[seed.size() - 1U] = 1U; // an all zero seed means "already unlocked" (ISO 14229-1, 9.4)
    }
    return true;
}

void DeviceKeySecurity::computeKey(
    uint8_t const deviceKey[],
    uint8_t const requestSeed,
    ::etl::span<uint8_t const> const seed,
    ::etl::span<uint8_t> const key)
{
    hmac_sha256_t mac;
    uint8_t out[SHA256_LEN];
    hmac_sha256_init(&mac, deviceKey, BL_SECURITY_DEVICE_KEY_LEN);
    hmac_sha256_update(&mac, KEY_LABEL, sizeof(KEY_LABEL));
    hmac_sha256_update(&mac, &requestSeed, 1U);
    hmac_sha256_update(&mac, seed.data(), static_cast<uint32_t>(seed.size()));
    hmac_sha256_final(&mac, out);
    size_t const length = (key.size() < sizeof(out)) ? key.size() : sizeof(out);
    (void)::memcpy(key.data(), out, length);
    (void)::memset(out, 0, sizeof(out));
}

bool DeviceKeySecurity::checkKey(
    SecurityLevel const& level,
    ::etl::span<uint8_t const> const seed,
    ::etl::span<uint8_t const> const key)
{
    KeyState state = KeyState::MISSING;
    uint8_t const* const deviceKeyBytes = deviceKey(state);
    if ((deviceKeyBytes == nullptr) || (level.method != SECURITY_METHOD_HMAC_SHA256) || key.empty()
        || (key.size() > MAC_LENGTH))
    {
        return false;
    }
    uint8_t expected[MAC_LENGTH];
    computeKey(deviceKeyBytes, level.requestSeed, seed, ::etl::span<uint8_t>(expected, key.size()));
    // constant time: every byte is compared, whatever the first difference
    uint8_t diff = 0U;
    for (size_t i = 0U; i < key.size(); ++i)
    {
        diff = static_cast<uint8_t>(diff | static_cast<uint8_t>(expected[i] ^ key[i]));
    }
    (void)::memset(expected, 0, sizeof(expected));
    return diff == 0U;
}

} // namespace app
} // namespace uds
