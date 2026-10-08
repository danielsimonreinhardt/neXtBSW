/* Host test of the start-up check of the calibration segment (SYS-89), no target hardware needed.
 *
 * The production ecu/calibration/src/calibration/CalibrationSegment.cpp is compiled as it is. Own test data, not
 * a build of a project: a segment of 12 value bytes with layout identifier 0x3F2A9C1E and dataset 7, whose header
 * bytes were written by tools/calseg (make_header, CRC of zlib) - so the C++ check and the Python tool that seals
 * the images agree on byte layout and CRC, not just each with itself.
 *
 * What is checked:
 *   1. CRC-32: check value of "123456789" (0xCBF43926), empty input, continuation over two pieces.
 *   2. The sealed test segment is VALID; header fields read back.
 *   3. Every status in the order of the check: erased flash, other magic, other format and header size, one
 *      header byte changed (header CRC), expected length other than stored, stored length beyond the segment, a
 *      dataset for another layout, one value bit flipped (values CRC), a segment shorter than its header, nullptr.
 *   4. The reaction: VALID uses the segment; refuse refuses; warn uses the segment; defaults uses the image
 *      defaults in the development profile and refuses in the production profile (no second copy there).
 *   5. SYS-91 (segment out of the ROM check list): a flipped header bit at start-up refuses and does not arm the
 *      run-time check (so the ROM check cannot hold the ECU); no invalid segment is ever re-checked; after a valid
 *      start every single bit flip of header and values is found by the run-time check; the segment of another
 *      dataset is valid at start-up and used, and a different valid segment during operation is CHANGED.
 *   Deliberate faults: a check without the header CRC fails case 3 (changed dataset byte), one that compares the
 *   layout before the length reports LAYOUT for a short dataset, a CRC with another polynomial fails case 1 and 2.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Werror -Iecu/calibration/include \
 *       ecu/calibration/test/host/CalibrationSegmentHostTest.cpp \
 *       ecu/calibration/src/calibration/CalibrationSegment.cpp -o build/host/calibration_segment_test.exe
 *   build/host/calibration_segment_test.exe
 */

#include "calibration/CalibrationSegment.h"

#include <cstdio>
#include <cstring>
#include <initializer_list>

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

void expect(bool const condition, char const* const what)
{
    ++checks;
    if (!condition)
    {
        ++failures;
        (void)printf("FAIL %s\n", what);
    }
}

using ::calibration::Action;
using ::calibration::OnInvalid;
using ::calibration::Status;

constexpr uint32_t LAYOUT = 0x3F2A9C1EU;
constexpr uint32_t LENGTH = 12U;

// tools/calseg/calseg.py make_header(0x3f2a9c1e, 7, values) + values
uint8_t const SEALED[] = {
    0x43U, 0x41U, 0x4CU, 0x56U, 0x01U, 0x00U, 0x20U, 0x00U, 0x1EU, 0x9CU, 0x2AU, 0x3FU, 0x07U, 0x00U, 0x00U,
    0x00U, 0x0CU, 0x00U, 0x00U, 0x00U, 0x66U, 0x7BU, 0x9EU, 0xCDU, 0xFFU, 0xFFU, 0xFFU, 0xFFU, 0x7FU, 0x69U,
    0x9FU, 0xD1U, 0x01U, 0x02U, 0x03U, 0x04U, 0x05U, 0x06U, 0x07U, 0x08U, 0xA0U, 0xB0U, 0xC0U, 0xD0U};

// segment of the flash area: the sealed bytes, the rest erased
constexpr size_t AREA = 64U;
uint8_t area[AREA];

void reset()
{
    (void)memset(area, 0xFF, sizeof(area));
    (void)memcpy(area, SEALED, sizeof(SEALED));
}

/** Writes a new header CRC, so that a changed field is seen by the check behind the header CRC. */
void rehash()
{
    uint32_t const crc = ::calibration::crc32(area, 28U);
    for (size_t i = 0U; i < 4U; ++i)
    {
        area[28U + i] = static_cast<uint8_t>(crc >> (8U * i));
    }
}

Status status(uint32_t const layout = LAYOUT, uint32_t const length = LENGTH, size_t const size = AREA)
{
    return ::calibration::check(area, size, layout, length);
}

void testCrc()
{
    uint8_t const text[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    expect(::calibration::crc32(text, sizeof(text)) == 0xCBF43926U, "crc32 check value of 123456789");
    expect(::calibration::crc32(text, 0U) == 0U, "crc32 of nothing is 0");
    uint32_t const first = ::calibration::crc32(text, 4U);
    expect(::calibration::crc32(&text[4], 5U, first) == 0xCBF43926U, "crc32 continued over two pieces");
    expect(::calibration::crc32(&SEALED[32], LENGTH) == 0xCD9E7B66U, "crc32 of the values as tools/calseg wrote it");
}

void testValid()
{
    reset();
    expect(status() == Status::VALID, "sealed segment is valid");
    ::calibration::SegmentHeader const h = ::calibration::readHeader(area);
    expect(h.magic == ::calibration::MAGIC, "magic read back");
    expect((h.format == 1U) && (h.headerSize == 32U), "format and header size read back");
    expect((h.layout == LAYOUT) && (h.dataset == 7U) && (h.length == LENGTH), "layout, dataset, length read back");
    expect((h.reserved == 0xFFFFFFFFU) && (h.valuesCrc == 0xCD9E7B66U), "reserved word and values CRC read back");
    expect(status(LAYOUT, LENGTH, 32U + LENGTH) == Status::VALID, "segment exactly as long as header and values");
}

void testInvalid()
{
    reset();
    (void)memset(area, 0xFF, sizeof(area));
    expect(status() == Status::ERASED, "erased flash");

    reset();
    area[3] = 'W';
    expect(status() == Status::MAGIC, "other magic");

    reset();
    area[4] = 2U;
    rehash();
    expect(status() == Status::FORMAT, "other format");
    reset();
    area[6] = 16U;
    rehash();
    expect(status() == Status::FORMAT, "other header size");

    reset();
    area[12] = 8U; // dataset changed, header CRC not
    expect(status() == Status::HEADER_CRC, "changed header byte");

    reset();
    expect(status(LAYOUT, LENGTH + 4U) == Status::LENGTH, "application expects more values");
    expect(status(0x12345678U, LENGTH - 1U) == Status::LENGTH, "length checked before the layout");
    expect(status(LAYOUT, LENGTH, 32U + LENGTH - 1U) == Status::LENGTH, "stored length beyond the segment");

    reset();
    expect(status(LAYOUT ^ 1U) == Status::LAYOUT, "dataset of another layout");

    reset();
    area[32U + 5U] ^= 0x10U;
    expect(status() == Status::VALUES_CRC, "one value bit flipped");
    reset();
    area[32U + LENGTH] = 0x00U; // behind the values: not part of the segment
    expect(status() == Status::VALID, "byte behind the values does not count");

    reset();
    expect(status(LAYOUT, LENGTH, 31U) == Status::LENGTH, "segment shorter than its header");
    expect(::calibration::check(nullptr, AREA, LAYOUT, LENGTH) == Status::LENGTH, "no segment at all");
}

void testDecide()
{
    Status const invalid[] = {Status::ERASED,
                              Status::MAGIC,
                              Status::FORMAT,
                              Status::HEADER_CRC,
                              Status::LENGTH,
                              Status::LAYOUT,
                              Status::VALUES_CRC};
    for (bool const production : {false, true})
    {
        for (OnInvalid const on : {OnInvalid::REFUSE, OnInvalid::DEFAULTS, OnInvalid::WARN})
        {
            expect(
                ::calibration::decide(Status::VALID, on, production) == Action::USE_SEGMENT,
                "a valid segment is always used");
        }
        for (Status const s : invalid)
        {
            expect(::calibration::decide(s, OnInvalid::REFUSE, production) == Action::REFUSE, "refuse refuses");
            expect(::calibration::decide(s, OnInvalid::WARN, production) == Action::USE_SEGMENT, "warn uses it");
            expect(
                ::calibration::decide(s, OnInvalid::DEFAULTS, production)
                    == (production ? Action::REFUSE : Action::USE_DEFAULTS),
                "defaults: image defaults in development, refuse in production");
        }
    }
    expect(::calibration::decide(Status::VALID, static_cast<OnInvalid>(7U), false) == Action::USE_SEGMENT,
           "unknown reaction with a valid segment");
    expect(::calibration::decide(Status::LAYOUT, static_cast<OnInvalid>(7U), false) == Action::REFUSE,
           "unknown reaction refuses");
}

/**
 * SYS-91: the start-up and the run-time check of the three board situations. The segment is no longer in the ROM
 * check list (rom-check-image: its content leaves the list green); this is what decides instead.
 */
void testRecheck()
{
    // (1) a damaged header at start-up (the board test of SYS-89 point 3): refuse, and the run-time check is not armed
    //     - the ROM check's area check returns VALID, so no ROM_CHECK_FAILURE, no safe-state hold, UDS stays up
    reset();
    area[9] ^= 0x04U; // one bit of the layout identifier, header CRC not updated
    Status const damaged = status();
    expect(damaged == Status::HEADER_CRC, "flipped header bit found at start-up");
    Action const refused = ::calibration::decide(damaged, OnInvalid::REFUSE, false);
    expect(refused == Action::REFUSE, "damaged header: refuse");
    expect(!::calibration::recheckArmed(damaged, refused), "refused segment: run-time check not armed");
    for (bool const production : {false, true})
    {
        for (Status const s : {Status::ERASED, Status::HEADER_CRC, Status::LAYOUT, Status::VALUES_CRC})
        {
            for (OnInvalid const on : {OnInvalid::REFUSE, OnInvalid::DEFAULTS, OnInvalid::WARN})
            {
                expect(!::calibration::recheckArmed(s, ::calibration::decide(s, on, production)),
                       "an invalid segment is never re-checked (refuse, defaults, warn)");
            }
        }
    }
    expect(!::calibration::recheckArmed(Status::VALID, Action::REFUSE), "valid but implausible (SYS-90): not armed");
    expect(!::calibration::recheckArmed(Status::VALID, Action::USE_DEFAULTS), "valid, defaults used: not armed");
    expect(::calibration::recheckArmed(Status::VALID, Action::USE_SEGMENT), "valid and used: armed");

    // (2) a valid segment accepted at start-up, then every single bit flip of header and values during operation:
    //     the run-time check reports it (RomCheck: ROMCHECK_OWN_AREA_FAILED -> ROM_CHECK_FAILURE, S1 held)
    reset();
    ::calibration::SegmentHeader const accepted = ::calibration::readHeader(area);
    expect(::calibration::recheck(area, AREA, LAYOUT, LENGTH, accepted) == Status::VALID, "unchanged segment");
    unsigned flips = 0U, found = 0U;
    for (size_t i = 0U; i < (32U + LENGTH); ++i)
    {
        for (unsigned bit = 0U; bit < 8U; ++bit)
        {
            area[i] = static_cast<uint8_t>(area[i] ^ (1U << bit));
            ++flips;
            found += (::calibration::recheck(area, AREA, LAYOUT, LENGTH, accepted) != Status::VALID) ? 1U : 0U;
            area[i] = static_cast<uint8_t>(area[i] ^ (1U << bit));
        }
    }
    expect(flips == (32U + LENGTH) * 8U, "every bit of header and values flipped");
    expect(found == flips, "every single bit flip found by the run-time check");
    area[32U + 3U] ^= 0x80U;
    expect(::calibration::recheck(area, AREA, LAYOUT, LENGTH, accepted) == Status::VALUES_CRC, "value bit: values CRC");

    // (3) the segment of another dataset for the same layout: valid at start-up and used (the start-up check and
    //     SYS-90 decide, not the ROM check); during operation a different valid segment is CHANGED, not VALID
    reset();
    area[12] = 8U; // dataset 8 instead of 7 ...
    area[32U] = 0x11U; // ... with other values, both CRCs written for them
    uint32_t const values = ::calibration::crc32(&area[32], LENGTH);
    for (size_t i = 0U; i < 4U; ++i)
    {
        area[20U + i] = static_cast<uint8_t>(values >> (8U * i));
    }
    rehash();
    expect(status() == Status::VALID, "segment of another dataset is valid at start-up");
    expect(::calibration::decide(status(), OnInvalid::REFUSE, true) == Action::USE_SEGMENT, "and used");
    expect(::calibration::recheck(area, AREA, LAYOUT, LENGTH, accepted) == Status::CHANGED,
           "a different valid segment during operation is CHANGED");
    expect(::calibration::recheck(area, AREA, LAYOUT, LENGTH, ::calibration::readHeader(area)) == Status::VALID,
           "and valid against its own accepted header");
    expect(strcmp(::calibration::statusText(Status::CHANGED), "changed") == 0, "status text changed");
}

void testTexts()
{
    expect(strcmp(::calibration::statusText(Status::VALID), "valid") == 0, "status text");
    expect(strcmp(::calibration::statusText(Status::LAYOUT), "layout wrong") == 0, "status text layout");
    expect(strcmp(::calibration::actionText(Action::REFUSE), "functions not started") == 0, "action text");
    expect(strcmp(::calibration::onInvalidText(OnInvalid::DEFAULTS), "defaults") == 0, "reaction text");
}
} // namespace

int main()
{
    testCrc();
    testValid();
    testInvalid();
    testDecide();
    testRecheck();
    testTexts();
    (void)printf("calibration segment host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
