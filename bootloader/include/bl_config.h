#pragma once

#include <stdint.h>

/* Memory layout (flash, sectors, application area, validity record, RAM range, boot request address) and the
 * board/derivative identity BL_TARGET_ID (BL-02): generated from derivative + project memory by
 * tools/bswgen/blgen.py into <generated>/bootloader/bl_memory.h (ARCH-08). */
#include "bootloader/bl_memory.h"

/* Diagnostic addresses (CAN ids, ISO-TP parameters, bootloader identification, search area of the application):
 * generated from project diagnostics by tools/bswgen/blgen.py into <generated>/bootloader/bl_diag.h (COM-11).
 * The application UDS stack (ecu/uds/AppDiagConfig.h) and tools/canflash read the same values. */
#include "bootloader/bl_diag.h"

/* Security access (BL-03): method, chip UID and the name of the master key on the workstation, generated from the
 * project and the derivative by tools/bswgen/blgen.py into <generated>/bootloader/bl_security.h. No key material:
 * the device key lives in the key sector (BL_KEY_ADDR, bl_memory.h), provisioned per device. */
#include "bootloader/bl_security.h"

/* Version of the bootloader software. The prefix in front of it identifies the build and comes from the project
 * (BL_VERSION_PREFIX, default nextbsw-bl). The application searches the flash for this string (DID 0xF180);
 * canflash and the updater compare the major version. */
/* 0.2.0: HMAC security access (BL-03), DIDs and fingerprint (BL-04, BL-10)
 * 0.3.0: transport interface (BL-14), preprogramming step (BL-21), numberOfModules in 0xF180 (BL-22), messages of
 *        maximum block length received again and response pending spaced (BL-25)
 * 0.3.1: SBC hook reads the UJA113x again until it answers plausibly after power-on (BL-26)
 * 0.3.2: S3server stopped while a request is received or processed, started with the final response (BL-29)
 * 0.3.3: startup code Thumb-1 only, the same for every Cortex-M core (PLAT-31)
 * 0.3.4: compatibility check as routine 0xF1A0 in the system supplier range (BL-24); 0xFF02 is still answered, and
 *        canflash keeps sending it to older bootloaders - it decides by this number
 * 0.3.5: no functional change - library members linked sorted by section name (BL-32), so the image no longer
 *        depends on the member order inside the toolchain's archives; 0.3.4 exists as two images (NXP GCC 11.4 and
 *        xPack GCC 11.3.1 with memset/memcpy swapped).
 * 0.3.6: no startup window after the resets of the watchdog fast test (family hook hal_reset_skips_startup_window,
 *        BL-33); low-voltage detect reset on from the reset handler (s32k1xx, BL-34)
 * 0.3.7: a record without compatibility block (written before BL-02) is not started any more, and the image check
 *        runs again right before the jump into the application (BL-36, SYS69-S10/S11)
 * 0.3.8: the check right before the jump re-reads record, compatibility block and entry and compares the record's
 *        CRC field with the CRC computed at the start, without a second CRC over the image (BL-38: 0.3.7 cost one
 *        CRC per start, ~160 ms on the eval board)
 * 0.3.9: the FlexCAN HAL clears only its own message buffers 0..8 - the loop over the whole MB RAM of a 32-buffer
 *        instance faulted on the 16-buffer FlexCAN1/2 of the S32K144 (BL-39, VER-04); first image with BL-38 and BL-39
 * 0.3.10: the SBC hook (UJA113x) serves the SBC watchdog whenever the SBC is not in software development mode (SDMS
 *        0), also with bootloader.sbc_watchdog: leave - leave takes effect only with SDMS 1 (BL-41); new field
 *        bl_sbc_watchdog.reason
 * 0.3.11: the same with the SBC only read (sbc_init(false), not in bootloader.init): with SDMS 0 the hook sets up and
 *        serves only the watchdog, the SBC stays in Standby, V2 and CAN untouched (BL-42); reason 3 (SDMS0_READ)
 * Every change of the image of the reference preset needs a new number here: bootloader/test/image_version.sha256
 * holds the image hash per version, tools/test entry bl-image-version turns red otherwise (BL-32). */
#define BL_VERSION_NUMBER "0.3.11"
#define BL_VERSION_STRING BL_VERSION_PREFIX " " BL_VERSION_NUMBER

/* Identification of this bootloader software before the rename to neXtBSW (PLAT-08). Bootloaders flashed before
 * report "S32K14x-BL 0.1.0": the application (DID 0xF180), the updater (embedded image) and canflash accept this
 * prefix besides BL_VERSION_PREFIX. A property of the software's history, not of a project. */
#define BL_VERSION_PREFIX_FORMER "S32K14x-BL"

/* Boot request from the application: magic + inverted magic in no-init RAM at BL_BOOT_REQUEST_ADDR */
#define BL_BOOT_REQUEST_MAGIC 0xB0071EADUL

/* UDS */
#define BL_MAX_BLOCK_LENGTH   1026U /* SID + block sequence counter + 1024 data bytes */
#define BL_UDS_BUFFER_SIZE    BL_MAX_BLOCK_LENGTH
#define BL_P2_SERVER_MS       50UL
#define BL_P2STAR_SERVER_MS   5000UL
#define BL_PENDING_MARGIN_MS  30UL
/* BL-25: shortest pause between two "response pending" (0x78) of one request, 0.3 x P2* (ISO 14229-2:2021 clause
 * 9.2, Table 4, note b). Derived, not set. Before, the erase job sent one before every sector: every 4 ms on S32K144. */
#define BL_PENDING_MIN_GAP_MS ((BL_P2STAR_SERVER_MS * 3UL) / 10UL)
#define BL_S3_SERVER_MS       5000UL
#define BL_SECURITY_MAX_TRIES 3U
#define BL_SECURITY_DELAY_MS  10000UL

/* Security access (BL-03)
 *
 * Methods of the key calculation (project diagnostics.bootloader.security.method -> BL_SECURITY_METHOD of the
 * generated bl_security.h; tools/nxconfig/diag.py SECURITY_METHOD_IDS holds the same numbers):
 *   1 hmac-sha256  key = HMAC-SHA256(device key, seed), 32 bytes; device key = HMAC-SHA256(master key, chip UID)
 * A later method (e.g. Ed25519, the device holds a public key only) gets the next number.
 *
 * The seed has 16 bytes, the key the full 32 bytes of the HMAC. canflash tells this method from the XOR/rotl13 of
 * the bootloaders before BL-03 (4-byte seed and key) by the identification: the former prefix
 * BL_VERSION_PREFIX_FORMER (the bootloader flashed before PLAT-08) or a bootloader that does not know DID 0xF1A1
 * is answered with XOR/rotl13, every other one with the method it reports in DID 0xF1A1. */
#define BL_SECURITY_METHOD_HMAC_SHA256 1U
#define BL_SECURITY_SEED_LEN           16U
#define BL_SECURITY_KEY_LEN            32U
#define BL_SECURITY_DEVICE_KEY_LEN     32U

/* Key record in the key sector (BL_KEY_ADDR), written by tools/canflash/provision.py over the debugger:
 *   [0..3]   magic "NXKY"
 *   [4]      record format version BL_KEY_RECORD_VERSION
 *   [5]      method (BL_SECURITY_METHOD)
 *   [6]      length of the device key (BL_SECURITY_DEVICE_KEY_LEN)
 *   [7]      0xFF
 *   [8..39]  device key
 *   [40..43] CRC-32 (as crc32.c, little endian) over bytes 0..39
 *   [44..47] 0xFF
 * An erased, half written or foreign record counts as "no key": the security access is refused (NRC 0x22) and
 * DID 0xF1A1 says why. */
#define BL_KEY_RECORD_VERSION 1U
#define BL_KEY_RECORD_LEN     48U

/* Time after reset during which a tester can stop the jump into a valid application. BL-33: 0 after a reset the
 * family hook hal_reset_skips_startup_window() lets pass (s32k1xx: watchdog fast test); never after power-on. */
#define BL_STARTUP_WINDOW_MS 200UL

/* Identification and fingerprint (BL-04)
 *
 * ReadDataByIdentifier answers three more identifiers, each from exactly one source (BL-03 adds 0xF18C
 * ECUSerialNumber = chip UID and 0xF1A1 = security access state, see uds.c):
 *   0xF189 vehicleManufacturerECUSoftwareVersionNumber - BL_VERSION_NUMBER above, ASCII, without the prefix
 *   0xF191 vehicleManufacturerECUHardwareNumber        - BL_HARDWARE_ID of the generated bl_memory.h, ASCII
 *                                                        "<board>/<derivative>"; its CRC-32 is BL_TARGET_ID (BL-10)
 *   0xF184 applicationSoftwareFingerprint              - what the tester wrote with WriteDataByIdentifier
 * No board, derivative or version literal is added anywhere for them.
 *
 * The fingerprint documents who programmed the application and when. Its layout is a convention of this
 * bootloader and of tools/canflash; ISO 14229-1 leaves the content open:
 *   [0..3]  programming date, BCD: century, year, month, day (0x20 0x26 0x09 0x17 = 2026-09-17)
 *   [4..15] tester identification, bytes as the tester writes them (canflash: ASCII, padded with 0x00)
 * The bootloader does not interpret the bytes; it insists on the exact length, so half a fingerprint cannot
 * be written unnoticed. Held in uds_t for the programming session and written into the validity record by routine
 * 0xFF01 (fingerprint block behind the compatibility block, app_image.h), so it survives the reset (BL-10). */
#define BL_FINGERPRINT_DATE_LEN   4U
#define BL_FINGERPRINT_TESTER_LEN 12U
#define BL_FINGERPRINT_LEN        16U /* = date + tester, held together by a compile time check in uds.c */
