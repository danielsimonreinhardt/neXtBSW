#pragma once

/* COM-44: identification values of the application for the diagnostic description (diag.yaml `source: {bsw: ...}`,
 * values of the diagnostic stack, tools/diaggen STACK_VALUES):
 *
 *   hardware.number     "<board>/<derivative>" - BL_HARDWARE_ID of the generated memory layout (BL-10), the text the
 *                       bootloader answers for 0xF191; derived from the board of the project
 *   nextbsw.version     neXtBSW version of the tree (VERSION, ARCH-55) and the commit of the build (git describe,
 *                       generated at build time: diag/DiagBuildId.h, tools/diaggen/buildid.py)
 *   compatibility.*     the fields of the record the bootloader answers for 0xF1A0 (BL-02): version, target, layout,
 *                       app_start, app_end, record_address, state - in this order and 32 bit each (version and state
 *                       8 bit) they are the same 22 bytes
 *   configuration.identity  PLAT-79: identity of configuration and generators of the image (64 hex digits, the
 *                       same text the console command "info" shows; tools/nxconfig/identity.py)
 *
 * Board and family neutral: the layout values are the generated macros of bl_memory.h, the record is read from
 * wherever the caller says it lies (target: BL_APP_RECORD_ADDR, host test: a buffer). */

#include <etl/span.h>

#include <cstddef>
#include <cstdint>

namespace uds
{
namespace app
{
/** The record of DID 0xF1A0 of the bootloader (bootloader/src/core/uds.c), field by field. */
struct CompatibilityRecord
{
    uint8_t version;        // version of the compatibility block (APP_COMPAT_VERSION)
    uint32_t target;        // BL_TARGET_ID: CRC-32 of "<board>/<derivative>"
    uint32_t layout;        // identity of the generated memory layout (app_layout_id())
    uint32_t appStart;      // BL_APP_START
    uint32_t appEnd;        // BL_APP_END
    uint32_t recordAddress; // BL_APP_RECORD_ADDR
    uint8_t state;          // app_compat_result_t of the installed validity record (0x00 OK, 0xFF no valid record)
};

/** What DiagStack hands to the values of the diagnostic stack. A text that is not known answers NRC 0x22. */
struct DiagIdentification
{
    char const* hardwareNumber;                   // hardware.number, nullptr: not known
    char const* nextbswVersion;                   // nextbsw.version, nullptr: not known
    ::etl::span<uint8_t const> applicationRecord; // validity record of the application, empty: not readable
    // PLAT-79: configuration.identity - SHA-256 (64 hex digits) over the configuration files of the project and the
    // generators (tools/nxconfig/identity.py, NX_IDENTITY_CONFIGURATION of identity/nxIdentityFields.h);
    // nullptr: not known
    char const* configurationIdentity;
    // PLAT-93: workspace.version - git describe of the customer workspace ("-": none, "nogit": no repository), from
    // NX_DIAG_WORKSPACE_VERSION of diag/DiagBuildId.h; nullptr: not known
    char const* workspaceVersion;
};

/** Bytes of the validity record the compatibility reads (sizeof app_record_t of bootloader/src/core/app_image.h). */
size_t applicationRecordBytes();

/**
 * The record of DID 0xF1A0 as the bootloader builds it: the values of this build and the state of
 * \p installedRecord (app_compat_result_t: 0x00 OK, 0x10 no compatibility block, 0xFF no valid record, ...). A record
 * shorter than applicationRecordBytes() counts as no valid record.
 */
CompatibilityRecord compatibilityRecord(::etl::span<uint8_t const> installedRecord);

} // namespace app
} // namespace uds
