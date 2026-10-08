#pragma once

/* Key algorithm of the application's security levels with method hmac-sha256 (COM-30).
 *
 * The same key as the bootloader's security access (BL-03), not a second one: the device key is
 * HMAC-SHA256(project master key, chip UID), provisioned once per device into the key record of the key sector
 * (bl_config.h, written by tools/canflash/provision.py) and read from there. The application image holds no key;
 * without a valid record nothing can be unlocked (requestSeed: NRC 0x22). SHA-256 and HMAC are the bootloader's
 * own implementation (bootloader/src/core/sha256.c), the record check follows bootloader/src/core/seedkey.c.
 *
 * - Key: the first key_bytes of HMAC-SHA256(device key, "NXAK" || requestSeed || seed). The level and the label
 *   bind a key to its level: it unlocks no other level and is no answer to a seed of the bootloader
 *   (HMAC-SHA256(device key, seed) there). Compared in constant time. The tester computes the same
 *   (tools/canflash/blkeys.py app_key).
 * - Seed: the first seed_bytes of HMAC-SHA256(device key, "NXSD" || pool || counter || requestSeed). The pool is
 *   a SHA-256 over the chip UID and the microsecond timer at start-up, stirred with the timer and the counter
 *   before every seed. With the key in the HMAC a tester cannot predict a seed from these sources. The
 *   application has no no-init RAM of its own: after a reset the counter starts again, and only the timer at the
 *   start and at the request tells the seeds apart (see doc/changelog.d/COM-30.md, open points).
 * - Attempts and delay of a level are kept by SecurityAccessService, not here.
 */

#include "uds/app/DiagDescriptionServices.h"

#include <etl/span.h>

#include <cstddef>
#include <cstdint>

namespace uds
{
namespace app
{
class DeviceKeySecurity final : public ISecurityKeyAlgorithm
{
public:
    /** State of the key record, the numbers of the bootloader's DID 0xF1A1. */
    enum class KeyState : uint8_t
    {
        MISSING     = 0U, // key sector erased: device not provisioned
        PROVISIONED = 1U,
        INVALID     = 2U  // something is there, but no record of this method (CRC, method, length, version)
    };

    static constexpr size_t MAC_LENGTH = 32U; // longest seed and key (tools/nxconfig: at most 32 bytes)

    /** \p keyRecord: the key record at the start of the key sector (bl_config.h BL_KEY_ADDR, BL_KEY_RECORD_LEN
     * bytes), read again at every use. \p uid: chip UID, a start value of the seed pool. */
    DeviceKeySecurity(::etl::span<uint8_t const> keyRecord, ::etl::span<uint8_t const> uid);

    KeyState keyState() const;

    bool makeSeed(SecurityLevel const& level, ::etl::span<uint8_t> seed) override;

    bool checkKey(
        SecurityLevel const& level,
        ::etl::span<uint8_t const> seed,
        ::etl::span<uint8_t const> key) override;

    /** Key of \p level for \p seed with \p deviceKey (32 bytes), key.size() bytes (at most MAC_LENGTH). */
    static void computeKey(
        uint8_t const deviceKey[],
        uint8_t requestSeed,
        ::etl::span<uint8_t const> seed,
        ::etl::span<uint8_t> key);

private:
    uint8_t const* deviceKey(KeyState& state) const;
    void stir();

    ::etl::span<uint8_t const> const _record;
    uint8_t _pool[MAC_LENGTH];
    uint32_t _counter;
};

} // namespace app
} // namespace uds
