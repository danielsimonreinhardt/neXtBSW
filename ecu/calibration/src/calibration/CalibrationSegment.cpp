#include "calibration/CalibrationSegment.h"

namespace calibration
{
namespace
{
// CRC-32 of zlib, four bits per step: 64 bytes of table instead of 1 KB, fast enough for a start-up check of a few
// kilobytes (two table look-ups per byte)
constexpr uint32_t CRC_NIBBLE[16] = {
    0x00000000U,
    0x1DB71064U,
    0x3B6E20C8U,
    0x26D930ACU,
    0x76DC4190U,
    0x6B6B51F4U,
    0x4DB26158U,
    0x5005713CU,
    0xEDB88320U,
    0xF00F9344U,
    0xD6D6A3E8U,
    0xCB61B38CU,
    0x9B64C2B0U,
    0x86D3D2D4U,
    0xA00AE278U,
    0xBDBDF21CU,
};

uint32_t le32(uint8_t const* const p)
{
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8U) | (static_cast<uint32_t>(p[2]) << 16U)
           | (static_cast<uint32_t>(p[3]) << 24U);
}

uint16_t le16(uint8_t const* const p)
{
    return static_cast<uint16_t>(static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8U));
}

constexpr size_t HEADER_CRC_OFFSET = 28U;
} // namespace

uint32_t crc32(uint8_t const* const data, size_t const n, uint32_t const crc)
{
    uint32_t c = ~crc;
    for (size_t i = 0U; i < n; ++i)
    {
        c ^= data[i];
        c = (c >> 4U) ^ CRC_NIBBLE[c & 0x0FU];
        c = (c >> 4U) ^ CRC_NIBBLE[c & 0x0FU];
    }
    return ~c;
}

SegmentHeader readHeader(uint8_t const* const segment)
{
    SegmentHeader h{};
    h.magic      = le32(&segment[0]);
    h.format     = le16(&segment[4]);
    h.headerSize = le16(&segment[6]);
    h.layout     = le32(&segment[8]);
    h.dataset    = le32(&segment[12]);
    h.length     = le32(&segment[16]);
    h.valuesCrc  = le32(&segment[20]);
    h.reserved   = le32(&segment[24]);
    h.headerCrc  = le32(&segment[28]);
    return h;
}

Status check(
    uint8_t const* const segment,
    size_t const segmentSize,
    uint32_t const expectedLayout,
    uint32_t const expectedLength)
{
    if ((segment == nullptr) || (segmentSize < HEADER_SIZE))
    {
        return Status::LENGTH;
    }
    bool erased = true;
    for (size_t i = 0U; i < HEADER_SIZE; ++i)
    {
        erased = erased && (segment[i] == 0xFFU);
    }
    if (erased)
    {
        return Status::ERASED;
    }
    SegmentHeader const h = readHeader(segment);
    if (h.magic != MAGIC)
    {
        return Status::MAGIC;
    }
    if ((h.format != FORMAT) || (h.headerSize != HEADER_SIZE))
    {
        return Status::FORMAT;
    }
    if (crc32(segment, HEADER_CRC_OFFSET) != h.headerCrc)
    {
        return Status::HEADER_CRC;
    }
    if ((h.length != expectedLength) || (h.length > (segmentSize - HEADER_SIZE)))
    {
        return Status::LENGTH;
    }
    if (h.layout != expectedLayout)
    {
        return Status::LAYOUT;
    }
    if (crc32(&segment[HEADER_SIZE], h.length) != h.valuesCrc)
    {
        return Status::VALUES_CRC;
    }
    return Status::VALID;
}

Status recheck(
    uint8_t const* const segment,
    size_t const segmentSize,
    uint32_t const expectedLayout,
    uint32_t const expectedLength,
    SegmentHeader const& accepted)
{
    Status const status = check(segment, segmentSize, expectedLayout, expectedLength);
    if (status != Status::VALID)
    {
        return status;
    }
    SegmentHeader const h = readHeader(segment);
    bool const same = (h.magic == accepted.magic) && (h.format == accepted.format)
                      && (h.headerSize == accepted.headerSize) && (h.layout == accepted.layout)
                      && (h.dataset == accepted.dataset) && (h.length == accepted.length)
                      && (h.valuesCrc == accepted.valuesCrc) && (h.reserved == accepted.reserved)
                      && (h.headerCrc == accepted.headerCrc);
    return same ? Status::VALID : Status::CHANGED;
}

bool recheckArmed(Status const status, Action const action)
{
    return (status == Status::VALID) && (action == Action::USE_SEGMENT);
}

Action decide(Status const status, OnInvalid const onInvalid, bool const production)
{
    if (status == Status::VALID)
    {
        return Action::USE_SEGMENT;
    }
    return decideInvalid(onInvalid, production);
}

Action decideInvalid(OnInvalid const onInvalid, bool const production)
{
    switch (onInvalid)
    {
        case OnInvalid::WARN: return Action::USE_SEGMENT;
        // the production profile reads the values from the segment and has no second copy (nxconfig refuses the
        // combination; should it ever be built, the application stays safe)
        case OnInvalid::DEFAULTS: return production ? Action::REFUSE : Action::USE_DEFAULTS;
        case OnInvalid::REFUSE:
        default: return Action::REFUSE;
    }
}

char const* statusText(Status const status)
{
    switch (status)
    {
        case Status::VALID: return "valid";
        case Status::ERASED: return "erased";
        case Status::MAGIC: return "no header";
        case Status::FORMAT: return "unknown format";
        case Status::HEADER_CRC: return "header CRC wrong";
        case Status::LENGTH: return "length wrong";
        case Status::LAYOUT: return "layout wrong";
        case Status::VALUES_CRC: return "values CRC wrong";
        case Status::CHANGED: return "changed"; // tools/logcheck bounds.yaml: at most 16 characters
        default: return "?";
    }
}

char const* actionText(Action const action)
{
    switch (action)
    {
        case Action::USE_SEGMENT: return "segment values";
        case Action::USE_DEFAULTS: return "image defaults";
        case Action::REFUSE: return "functions not started";
        default: return "?";
    }
}

char const* onInvalidText(OnInvalid const onInvalid)
{
    switch (onInvalid)
    {
        case OnInvalid::REFUSE: return "refuse";
        case OnInvalid::DEFAULTS: return "defaults";
        case OnInvalid::WARN: return "warn";
        default: return "?";
    }
}

} // namespace calibration
