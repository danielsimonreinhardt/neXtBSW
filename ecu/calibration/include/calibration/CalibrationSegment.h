#pragma once

#include <cstddef>
#include <cstdint>

/**
 * Calibration segment (SYS-89, stage K3 of doc/architecture/kalibrierung.md): the values of all calibration
 * parameters (rte::PARAMETERS) in a flash area of their own behind a header, checked at start-up.
 *
 * This part is the pure logic - header layout, CRC, the check and the reaction chosen by the project - without
 * OpenBSW, linker symbols or console, so the host test (ecu/calibration/test) runs the same code as the target.
 * Calibration.h is the target side.
 *
 * The header is written after the link by tools/calseg (seal), never by the application. Byte layout (little
 * endian, the targets of D11), rules of bootloader/src/core/app_image.h: fields never move, additions use the
 * reserved word or a new format behind the header.
 *
 *   offset  size  field
 *        0     4  magic       "CALV" (0x564C4143)
 *        4     2  format      FORMAT
 *        6     2  headerSize  HEADER_SIZE
 *        8     4  layout      layout identifier of the parameters (RteCalibration.json, tools/rtegen)
 *       12     4  dataset     identifier of the dataset the values come from, 0: the defaults of the build
 *       16     4  length      bytes of values behind the header
 *       20     4  valuesCrc   CRC-32 over the values
 *       24     4  reserved    0xFFFFFFFF
 *       28     4  headerCrc   CRC-32 over bytes 0..27
 *
 * CRC-32: the one of zlib (reflected polynomial 0xEDB88320, start and final XOR 0xFFFFFFFF), the same as the CRC of
 * the validity record of the bootloader, so tools compute it with zlib.crc32.
 */
namespace calibration
{
constexpr uint32_t MAGIC       = 0x564C4143U; // "CALV" in memory order
constexpr uint16_t FORMAT      = 1U;
constexpr uint16_t HEADER_SIZE = 32U;

struct SegmentHeader
{
    uint32_t magic;
    uint16_t format;
    uint16_t headerSize;
    uint32_t layout;
    uint32_t dataset;
    uint32_t length;
    uint32_t valuesCrc;
    uint32_t reserved;
    uint32_t headerCrc;
};

/** Result of the start-up check, in the order the check runs. */
enum class Status : uint8_t
{
    VALID,      // header, layout identifier and CRC fit the application
    ERASED,     // the header reads erased flash: no segment was written
    MAGIC,      // something else than a CALV header
    FORMAT,     // unknown format or header size
    HEADER_CRC, // header damaged
    LENGTH,     // the values do not have the length the application expects (or exceed the segment)
    LAYOUT,     // written for another layout of the parameters (dataset of another build)
    VALUES_CRC, // values damaged
    CHANGED,    // SYS-91, run-time check only: valid, but not the header the start-up check accepted
};

/** memory.calibration.on_invalid of the project (generated NX_MEMORY_CAL_ON_INVALID). */
enum class OnInvalid : uint8_t
{
    REFUSE   = 0U,
    DEFAULTS = 1U,
    WARN     = 2U,
};

/** What the application does with the segment. */
enum class Action : uint8_t
{
    USE_SEGMENT,  // values of the segment (valid, or on_invalid warn)
    USE_DEFAULTS, // values built into the image (development profile only)
    REFUSE,       // application functions are not started
};

/** CRC-32 of zlib over n bytes; \p crc continues an earlier result (0 to start). */
uint32_t crc32(uint8_t const* data, size_t n, uint32_t crc = 0U);

/** The header as stored in the first HEADER_SIZE bytes (little endian). */
SegmentHeader readHeader(uint8_t const* segment);

/**
 * Checks the segment of \p segmentSize bytes against the application: \p expectedLayout (layout identifier the
 * application was built with) and \p expectedLength (bytes of values the application reads).
 */
Status check(uint8_t const* segment, size_t segmentSize, uint32_t expectedLayout, uint32_t expectedLength);

/**
 * SYS-91: run-time check of a segment the start-up check accepted (\p accepted: its header then). The segment left
 * the ROM check list of the build (its content may come from a dataset flashed without a build); this is its own
 * reference instead: check() once more, and the header must still be the accepted one - a different but valid
 * segment would hold values that never passed the start-up checks. VALID or the first deviation.
 */
Status recheck(
    uint8_t const* segment,
    size_t segmentSize,
    uint32_t expectedLayout,
    uint32_t expectedLength,
    SegmentHeader const& accepted);

/**
 * SYS-91: whether the run-time check runs after a start-up with \p status and \p action: only for a valid segment
 * that is used. A segment the start-up refused or replaced by the defaults is not in use, and one used under
 * on_invalid warn is known to be invalid - re-checking either would turn the start-up reaction into a safe-state hold.
 */
bool recheckArmed(Status status, Action action);

/** Reaction to \p status: a valid segment is used, otherwise what the project chose. */
Action decide(Status status, OnInvalid onInvalid, bool production);

/**
 * Reaction of the project to values that must not be used - a segment that is not valid, or (SYS-90) values that
 * fail the plausibility check of the safety-relevant parameters (calibration/Plausibility.h): on_invalid warn uses
 * them, defaults the image defaults (development profile only), refuse and everything else refuses.
 */
Action decideInvalid(OnInvalid onInvalid, bool production);

char const* statusText(Status status);
char const* actionText(Action action);
char const* onInvalidText(OnInvalid onInvalid);

} // namespace calibration
