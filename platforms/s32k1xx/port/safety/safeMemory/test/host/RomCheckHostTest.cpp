/* SYS-38: host test of the ROM check (SM-14) - src/safeMemory/RomCheck.cpp and include/safeMemory/RomCheckList.h,
 * the real code, against a register model of the S32K1 CRC unit (romCheckStubs/mcu/mcu.h).
 *
 * Board finding 2026-09-21 (session d3): __checksum_list_start and __checksum_result were not in the image, the
 * flash read 0xFF there. RomCheck took word 0 as the number of areas (0xFFFFFFFF) and would have walked addresses
 * 0xFFFFFFFF - it only did not because nothing called RomCheck::idle(). What this test shows:
 * - the CRC unit as RomCheck configures it gives the CRC the post-link step computes (tools/romCheckImage.py): a
 *   known vector with the same value as its Python test, and a CRC-32 over byte-swapped words as reference;
 * - a valid image passes, with exactly one CRC write per word of the areas and none outside;
 * - every single bit flipped in any area -> ROMCHECK_FAILED (RomCheck::idle() reports it as ROM_CHECK_FAILURE);
 * - an implausible list (erased, 0 or too many areas, length that does not fit, unaligned, reversed or outside the
 *   application flash) -> ROMCHECK_INVALID_LIST without a single access to the CRC unit or to an address of the list;
 * - RomCheck::idle() on the erased list of the board finding triggers the ROM check monitor once, inside the safety
 *   shell, and does not check again until the next init().
 * The reaction to ROM_CHECK_FAILURE (S1 held without reset, SYS-23) is the safe supervisor's own host test.
 *
 * SYS-44 (re-check period, known-answer test, suspension during flash writes; fmea.md F3/5.1, SSR-025, SSR-067):
 * - nothing runs before init(); the period of the generated configuration (own test profile, 2500 ms) is used;
 * - the first pass runs at once, the next only after the period (start to start, also across the wrap-around of
 *   the millisecond counter), period 0 = once per start; a corruption after the first pass is found by the re-check;
 * - the known-answer test gives the value of the Python model, and a CRC unit stuck at the reference (which the CRC
 *   over the areas alone would accept) or with a lost polynomial bit fails it -> ROMCHECK_UNIT_FAILED;
 * - an open flash write or a running flash command postpones the pass without touching the CRC unit; a write that
 *   begins during the pass (even one that changes a word) discards it without a report and it runs again;
 * - a due pass kept off for more than one period -> ROMCHECK_OVERDUE, reported once; with period 0 never;
 * - the pass runs outside the safety shell; a waiting check opens no shell in every round of the idle loop;
 * - the flash write indicator counts under the interrupt lock and nests.
 *
 * SYS-91 (area with its own reference, the calibration segment): registering the owner's check opens one shell and
 * survives init(); a different content of the area leaves the list green; a bit flip in it after the first pass is
 * found by the re-check -> ROMCHECK_OWN_AREA_FAILED, reported once inside the shell; a failing list is reported first
 * without asking the owner; a flash write during the owner's check discards the pass; without a check nothing looks.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Werror \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/test/host/romCheckStubs \
 *       -Iplatforms/s32k1xx/port/safety/safeMemory/include \
 *       platforms/s32k1xx/port/safety/safeMemory/test/host/RomCheckHostTest.cpp -o build/host/romcheck.exe
 */

#include <cstdint>
#include <cstdio>

#include "../../src/romCheckFlashWrites/RomCheckFlashWrites.cpp" // NOLINT: the indicator (SYS-44)
#include "../../src/safeMemory/RomCheck.cpp"                     // NOLINT: the unit under test

// Linker symbols of an image flashed without the list (the board finding): erased flash everywhere
uintptr_t const __checksum_list_start[11] = {
    UINTPTR_MAX, UINTPTR_MAX, UINTPTR_MAX, UINTPTR_MAX, UINTPTR_MAX, UINTPTR_MAX,
    UINTPTR_MAX, UINTPTR_MAX, UINTPTR_MAX, UINTPTR_MAX, UINTPTR_MAX};
uintptr_t const __checksum_list_end[1] = {UINTPTR_MAX};
uint32_t const __checksum_result       = 0xFFFFFFFFU;
uint32_t const __FLASH_START[1]        = {0xFFFFFFFFU};

namespace
{
int checks   = 0;
int failures = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);   \
        }                                                                 \
    } while (false)

using ::safety::RomCheck;
using Result = RomCheck::Result;

/** CRC-32 (zlib) over every word with its bytes in big endian order - what romCheckImage.py computes. */
uint32_t referenceCrc(uint32_t const* const words, size_t const count, uint32_t crc = 0xFFFFFFFFU)
{
    for (size_t i = 0U; i < count; ++i)
    {
        for (int shift = 24; shift >= 0; shift -= 8)
        {
            crc ^= (words[i] >> static_cast<unsigned>(shift)) & 0xFFU;
            for (int bit = 0; bit < 8; ++bit)
            {
                crc = ((crc & 1U) != 0U) ? ((crc >> 1U) ^ 0xEDB88320U) : (crc >> 1U);
            }
        }
    }
    return crc;
}

/** Application flash of the test: IVT, empty flash configuration field, startup, a gap, code and data ROM,
 * followed by the list (areas must end at or below it). Word indices of the areas: */
constexpr size_t IVT_END = 8U, STARTUP_END = 10U, CODE_START = 12U, CODE_END = 40U, WORDS = 48U;

struct FakeFlash
{
    alignas(8) uint32_t words[WORDS];
    uintptr_t list[11];
};

FakeFlash flash;

uintptr_t at(size_t const index) { return reinterpret_cast<uintptr_t>(&flash.words[index]); }

void buildImage()
{
    for (size_t i = 0U; i < WORDS; ++i)
    {
        flash.words[i] = (static_cast<uint32_t>(i) * 0x01010101U) ^ 0xA5C3E10FU;
    }
    uintptr_t const list[11] = {5U, at(0U), at(IVT_END), at(IVT_END), at(IVT_END), at(IVT_END), at(STARTUP_END),
                                at(CODE_START), at(CODE_END), at(CODE_END), at(WORDS)};
    for (size_t i = 0U; i < 11U; ++i)
    {
        flash.list[i] = list[i];
    }
}

uint32_t imageReference()
{
    uint32_t crc = referenceCrc(&flash.words[0], STARTUP_END);                  // IVT, FCF (empty), startup
    crc          = referenceCrc(&flash.words[CODE_START], WORDS - CODE_START, crc); // code, data ROM
    return ~crc;
}

Result check(uintptr_t const* const list, size_t const words, uint32_t const reference)
{
    ::crc_model::state() = ::crc_model::State{};
    return ::safety::checkPass(list, words, at(0U), reference).result;
}

void testKnownVector()
{
    // Same vector and value as test_rom_check_image.py (CrcTest.test_known_vector)
    static uint32_t const vector[5] = {0x04030201U, 0x08070605U, 0xDEADBEEFU, 0x00000000U, 0xFFFFFFFFU};
    uintptr_t const list[3] = {1U, reinterpret_cast<uintptr_t>(&vector[0]), reinterpret_cast<uintptr_t>(&vector[5])};
    ::crc_model::state() = ::crc_model::State{};
    CHECK(::safety::crcOverAreas(list) == 0x498CFAA3U);
    CHECK(~referenceCrc(vector, 5U) == 0x498CFAA3U);
    CHECK(!::crc_model::state().unsupported);
    CHECK(::crc_model::state().seedWrites == 1U);
    CHECK(::crc_model::state().dataWrites == 5U);
}

void testValidImagePasses()
{
    buildImage();
    int const errors = ::util::logger::Logger::errors();
    CHECK(check(flash.list, 11U, imageReference()) == Result::ROMCHECK_OK);
    CHECK(::crc_model::state().dataWrites == STARTUP_END + (WORDS - CODE_START));
    CHECK(!::crc_model::state().unsupported);
    CHECK(::util::logger::Logger::errors() == errors);
}

void testEveryBitFlipInTheAreasFails()
{
    buildImage();
    uint32_t const reference = imageReference();
    int detected = 0, flips = 0;
    for (size_t i = 0U; i < WORDS; ++i)
    {
        if ((i >= STARTUP_END) && (i < CODE_START))
        {
            continue; // the gap between the areas
        }
        for (unsigned bit = 0U; bit < 32U; ++bit)
        {
            flash.words[i] ^= (1U << bit);
            ++flips;
            detected += (check(flash.list, 11U, reference) == Result::ROMCHECK_FAILED) ? 1 : 0;
            flash.words[i] ^= (1U << bit);
        }
    }
    CHECK(flips == static_cast<int>((WORDS - 2U) * 32U));
    CHECK(detected == flips);
    CHECK(check(flash.list, 11U, reference) == Result::ROMCHECK_OK);
}

void testContentOutsideTheAreasIsNotChecked()
{
    // Why romCheckImage.py refuses an image with flash content outside every area: the run-time check cannot see it
    buildImage();
    uint32_t const reference = imageReference();
    flash.words[STARTUP_END] ^= 0xFFFFFFFFU;
    CHECK(check(flash.list, 11U, reference) == Result::ROMCHECK_OK);
}

void expectRefused(uintptr_t const* const list, size_t const words, char const* const what)
{
    Result const result = check(list, words, 0U);
    if (result != Result::ROMCHECK_INVALID_LIST)
    {
        std::printf("list not refused: %s\n", what);
    }
    CHECK(result == Result::ROMCHECK_INVALID_LIST);
    CHECK(::crc_model::state().seedWrites == 0U); // no access to the CRC unit, no address of the list read
    CHECK(::crc_model::state().dataWrites == 0U);
}

void testImplausibleListsAreRefused()
{
    // The list is changed in place: its own address is the upper bound of the areas (a copy elsewhere, e.g. on the
    // stack, would be refused for that reason alone and hide every other rule)
    uintptr_t* const list = flash.list;
    auto reset            = []() { buildImage(); };
    reset();
    CHECK(::safety::romcheck::checkList(list, 11U, at(0U), reinterpret_cast<uintptr_t>(flash.list))
          == ::safety::romcheck::ListStatus::VALID);
    CHECK(check(list, 11U, imageReference()) == Result::ROMCHECK_OK);

    expectRefused(__checksum_list_start, 11U, "erased flash (board finding)");
    list[0] = 0U;
    expectRefused(list, 11U, "no area");
    list[0] = ::safety::romcheck::MAX_AREAS + 1U;
    expectRefused(list, 11U, "too many areas");
    reset();
    expectRefused(list, 10U, "list shorter than its areas");
    expectRefused(list, 13U, "list longer than its areas");
    expectRefused(list, 0U, "empty list");
    list[1] += 1U;
    expectRefused(list, 11U, "unaligned start");
    reset();
    list[10] -= 2U;
    expectRefused(list, 11U, "unaligned end");
    reset();
    list[7] = at(CODE_END + 1U);
    expectRefused(list, 11U, "start above end");
    reset();
    list[1] = at(0U) - 4U;
    expectRefused(list, 11U, "below the application flash");
    reset();
    list[10] = reinterpret_cast<uintptr_t>(&list[0]) + 64U;
    expectRefused(list, 11U, "beyond the list");
}

void testIdleWithAnErasedListTriggersTheMonitor()
{
    auto& monitor = ::safety::SafeSupervisor::getInstance().romCheckMonitor;
    ::crc_model::state() = ::crc_model::State{};
    int const errors = ::util::logger::Logger::errors();
    RomCheck::init();
    CHECK(RomCheck::result() == Result::ROMCHECK_REQUESTED);
    RomCheck::idle();
    CHECK(RomCheck::result() == Result::ROMCHECK_INVALID_LIST);
    CHECK(monitor.triggers == 1);
    CHECK(monitor.outsideShell == 0);
    CHECK(::romcheck_test::shellDepth() == 0);
    CHECK(::util::logger::Logger::errors() == errors + 1);
    CHECK(::crc_model::state().dataWrites == 5U); // SYS-44: only the known-answer test, no address of the list
    CHECK(::crc_model::state().seedWrites == 1U);
    RomCheck::idle(); // once per request: no second report until the next init()
    CHECK(monitor.triggers == 1);
    RomCheck::init();
    RomCheck::idle();
    CHECK(monitor.triggers == 2);
}
// ---- SYS-44: re-check period, known-answer test, suspension during flash writes ----

using ::safety::romCheckFlashWrites;

constexpr uint32_t PERIOD = 1000U;

::safety::romcheck::Image image()
{
    return ::safety::romcheck::Image{flash.list, 11U, at(0U), imageReference()};
}

::safety::SafeSupervisor::RomCheckMonitor& monitor() { return ::safety::SafeSupervisor::getInstance().romCheckMonitor; }

/** Fresh request with a valid image and a healthy CRC unit, no flash write open, no flash command running. */
void restart()
{
    buildImage();
    ::crc_model::state() = ::crc_model::State{};
    FTFC->FSTAT          = FTFC_FSTAT_CCIF_MASK;
    RomCheck::init();
    monitor() = ::safety::SafeSupervisor::RomCheckMonitor{};
}

/** Words fed to the CRC unit by one full pass: known-answer test plus the areas. */
constexpr uint32_t PASS_WORDS = 5U + STARTUP_END + (WORDS - CODE_START);

void testNothingBeforeInit()
{
    // Must run first: the idle handler may run before SafetyManager::init() requested the check
    buildImage();
    ::crc_model::state() = ::crc_model::State{};
    int const shells     = ::romcheck_test::shellsOpened();
    RomCheck::idleOn(0U, PERIOD, image());
    RomCheck::idle();
    CHECK(::crc_model::state().seedWrites == 0U);
    CHECK(::crc_model::state().dataWrites == 0U);
    CHECK(RomCheck::result() == Result::ROMCHECK_REQUESTED);
    CHECK(::romcheck_test::shellsOpened() == shells);
}

void testGeneratedPeriodIsUsed()
{
    // Own test profile (romCheckStubs/integration/safeMemoryConfiguration.h), not the module default of 60000 ms
    CHECK(RomCheck::periodMs() == 2500U);
    restart();
    ::romcheck_test::nowMs() = 100U;
    RomCheck::idle(); // the linker symbols of this test are the erased list: the first pass reports it
    CHECK(RomCheck::result() == Result::ROMCHECK_INVALID_LIST);
    CHECK(monitor().triggers == 1);
}

void testKnownAnswer()
{
    ::crc_model::state() = ::crc_model::State{};
    CHECK(::safety::crcKnownAnswer() == ::safety::KNOWN_ANSWER);
    CHECK(::safety::KNOWN_ANSWER == 0x498CFAA3U); // test_rom_check_image.py CrcTest.test_known_vector
    CHECK(::crc_model::state().dataWrites == 5U);
    CHECK(::crc_model::state().seedWrites == 1U);
    CHECK(!::crc_model::state().unsupported);
}

void testFirstPassThenPeriod()
{
    restart();
    RomCheck::idleOn(1000U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    CHECK(RomCheck::statistics().passes == 1U);
    CHECK(::crc_model::state().dataWrites == PASS_WORDS);
    CHECK(::crc_model::state().seedWrites == 2U); // known-answer test and areas, each from the seed

    RomCheck::idleOn(1999U, PERIOD, image()); // 999 ms after the start of the first pass: not due
    CHECK(::crc_model::state().dataWrites == PASS_WORDS);
    RomCheck::idleOn(2000U, PERIOD, image()); // start to start
    CHECK(::crc_model::state().dataWrites == 2U * PASS_WORDS);
    CHECK(RomCheck::statistics().passes == 2U);
    CHECK(monitor().triggers == 0);

    // wrap-around of the 32-bit millisecond counter
    restart();
    RomCheck::idleOn(0xFFFFFF00U, PERIOD, image());
    RomCheck::idleOn(0x000002E7U, PERIOD, image()); // 999 ms later
    CHECK(RomCheck::statistics().passes == 1U);
    RomCheck::idleOn(0x000002E8U, PERIOD, image()); // 1000 ms later
    CHECK(RomCheck::statistics().passes == 2U);
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
}

void testPeriodZeroIsOncePerStart()
{
    restart();
    RomCheck::idleOn(0U, 0U, image());
    CHECK(RomCheck::statistics().passes == 1U);
    for (uint32_t t = 1U; t < 100000U; t += 997U)
    {
        RomCheck::idleOn(t, 0U, image());
    }
    CHECK(RomCheck::statistics().passes == 1U);
    CHECK(::crc_model::state().dataWrites == PASS_WORDS);
    RomCheck::init(); // next start
    RomCheck::idleOn(200000U, 0U, image());
    CHECK(RomCheck::statistics().passes == 1U);
    CHECK(::crc_model::state().dataWrites == 2U * PASS_WORDS);
}

void testRecheckFindsALaterCorruption()
{
    restart();
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    uint32_t const reference = imageReference();
    flash.words[CODE_START + 3U] ^= 0x00010000U; // one bit, after the first pass
    int const errors = ::util::logger::Logger::errors();
    RomCheck::idleOn(999U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(monitor().triggers == 0);
    RomCheck::idleOn(1000U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(RomCheck::result() == Result::ROMCHECK_FAILED);
    CHECK(monitor().triggers == 1);
    CHECK(monitor().outsideShell == 0);
    CHECK(::util::logger::Logger::errors() == errors + 1);
    // the ECU is held; no further pass and no second report
    RomCheck::idleOn(5000U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(monitor().triggers == 1);
    CHECK(RomCheck::statistics().passes == 2U);
}

void testKnownAnswerCatchesAFaultyUnit()
{
    // A unit that always returns the reference: the CRC over the areas alone accepts every image
    restart();
    uint32_t const reference = imageReference();
    flash.words[1] ^= 0xFFFFFFFFU;
    ::crc_model::state().stuck      = true;
    ::crc_model::state().stuckValue = reference;
    CHECK(check(flash.list, 11U, reference) == Result::ROMCHECK_FAILED); // healthy model after the reset in check()
    ::crc_model::state()            = ::crc_model::State{};
    ::crc_model::state().stuck      = true;
    ::crc_model::state().stuckValue = reference;
    CHECK(::safety::checkPass(flash.list, 11U, at(0U), reference).result == Result::ROMCHECK_OK); // blind
    int const errors = ::util::logger::Logger::errors();
    RomCheck::idleOn(0U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(RomCheck::result() == Result::ROMCHECK_UNIT_FAILED);
    CHECK(monitor().triggers == 1);
    CHECK(monitor().outsideShell == 0);
    CHECK(::util::logger::Logger::errors() == errors + 1);

    // A polynomial bit lost in the unit
    restart();
    ::crc_model::state().polyFault = 0x00000100U;
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_UNIT_FAILED);
    CHECK(monitor().triggers == 1);
    CHECK(::crc_model::state().dataWrites == 5U); // the areas are not read after a failed known-answer test
}

void testOpenFlashWritePostpones()
{
    restart();
    romCheckFlashWrites.begin();
    int const shells = ::romcheck_test::shellsOpened();
    for (uint32_t t = 0U; t < 500U; t += 7U)
    {
        RomCheck::idleOn(t, PERIOD, image());
    }
    CHECK(::crc_model::state().seedWrites == 0U); // the CRC unit is not touched while a write is open
    CHECK(RomCheck::result() == Result::ROMCHECK_REQUESTED);
    CHECK(RomCheck::statistics().postponements == 1U);
    CHECK(::romcheck_test::shellsOpened() == shells + 1); // one shell to note the postponement, not one per round
    romCheckFlashWrites.begin(); // nested
    romCheckFlashWrites.end();
    RomCheck::idleOn(600U, PERIOD, image());
    CHECK(RomCheck::statistics().passes == 0U);
    romCheckFlashWrites.end();
    RomCheck::idleOn(700U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    CHECK(RomCheck::statistics().passes == 1U);
    CHECK(monitor().triggers == 0);
    CHECK(!romCheckFlashWrites.read().writing());
}

void testFlashCommandPostpones()
{
    restart();
    FTFC->FSTAT = 0U; // CCIF clear: a command runs
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(::crc_model::state().seedWrites == 0U);
    CHECK(RomCheck::statistics().postponements == 1U);
    FTFC->FSTAT = FTFC_FSTAT_CCIF_MASK;
    RomCheck::idleOn(10U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
}

void writeDuringPass(uint32_t const dataWrites)
{
    if (dataWrites == 20U)
    {
        // a flash write that begins and ends inside the pass and disturbs what the pass reads
        romCheckFlashWrites.begin();
        flash.words[CODE_START + 20U] ^= 0x80000000U;
        romCheckFlashWrites.end();
    }
}

int shellDepthInPass = 0;

void recordShellDepth(uint32_t) { shellDepthInPass += ::romcheck_test::shellDepth(); }

void commandDuringPass(uint32_t const dataWrites)
{
    if (dataWrites == 30U)
    {
        FTFC->FSTAT = 0U;
    }
}

void testWriteDuringPassDiscardsIt()
{
    restart();
    uint32_t const reference     = imageReference();
    int const errors             = ::util::logger::Logger::errors();
    ::crc_model::state().onData  = &writeDuringPass;
    RomCheck::idleOn(0U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(RomCheck::result() == Result::ROMCHECK_REQUESTED); // not evaluated, not reported
    CHECK(monitor().triggers == 0);
    CHECK(::util::logger::Logger::errors() == errors);
    CHECK(RomCheck::statistics().discarded == 1U);
    CHECK(RomCheck::statistics().passes == 0U);
    CHECK(RomCheck::statistics().postponements == 1U);
    flash.words[CODE_START + 20U] ^= 0x80000000U; // the disturbance is gone
    ::crc_model::state().onData = nullptr;
    RomCheck::idleOn(1U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    CHECK(RomCheck::statistics().passes == 1U);

    // a flash command that starts during a later pass
    ::crc_model::state().onData = &commandDuringPass;
    ::crc_model::state().dataWrites = 0U;
    RomCheck::idleOn(1001U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(RomCheck::statistics().discarded == 2U);
    CHECK(RomCheck::statistics().passes == 1U);
    ::crc_model::state().onData = nullptr;
    FTFC->FSTAT                 = FTFC_FSTAT_CCIF_MASK;
    RomCheck::idleOn(1002U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), reference});
    CHECK(RomCheck::statistics().passes == 2U);
    CHECK(monitor().triggers == 0);
}

void testPassRunsOutsideTheShell()
{
    restart();
    shellDepthInPass            = 0;
    ::crc_model::state().onData = &recordShellDepth;
    int const shells            = ::romcheck_test::shellsOpened();
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    CHECK(shellDepthInPass == 0);                          // no fed word inside a shell (SSR-025)
    CHECK(::romcheck_test::shellsOpened() == shells + 1); // one for the bookkeeping at the end
    CHECK(::romcheck_test::shellDepth() == 0);
}

void testOverdueAfterOnePeriod()
{
    restart();
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    romCheckFlashWrites.begin(); // a write that never ends (or a wild write into the indicator)
    RomCheck::idleOn(1000U, PERIOD, image()); // due, postponed: the deadline runs from here
    RomCheck::idleOn(2000U, PERIOD, image()); // exactly one period: still waiting
    CHECK(monitor().triggers == 0);
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    int const errors = ::util::logger::Logger::errors();
    RomCheck::idleOn(2001U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OVERDUE);
    CHECK(monitor().triggers == 1);
    CHECK(monitor().outsideShell == 0);
    CHECK(::util::logger::Logger::errors() == errors + 1);
    RomCheck::idleOn(9000U, PERIOD, image());
    CHECK(monitor().triggers == 1);
    romCheckFlashWrites.end();

    // passes discarded again and again count against the same deadline
    restart();
    RomCheck::idleOn(0U, PERIOD, image());
    ::crc_model::state().onData = &writeDuringPass; // the hook counts from 0 in every pass below
    ::crc_model::state().dataWrites = 0U;
    RomCheck::idleOn(1000U, PERIOD, image());
    flash.words[CODE_START + 20U] ^= 0x80000000U;
    ::crc_model::state().dataWrites = 0U;
    RomCheck::idleOn(1500U, PERIOD, image());
    flash.words[CODE_START + 20U] ^= 0x80000000U;
    CHECK(RomCheck::statistics().discarded == 2U);
    CHECK(monitor().triggers == 0);
    ::crc_model::state().dataWrites = 0U;
    RomCheck::idleOn(2001U, PERIOD, image());
    flash.words[CODE_START + 20U] ^= 0x80000000U;
    CHECK(RomCheck::result() == Result::ROMCHECK_OVERDUE);
    CHECK(monitor().triggers == 1);
    ::crc_model::state().onData = nullptr;

    // period 0: the first pass waits for the write as long as it lasts
    restart();
    romCheckFlashWrites.begin();
    RomCheck::idleOn(0U, 0U, image());
    RomCheck::idleOn(0x7FFFFFFFU, 0U, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_REQUESTED);
    CHECK(monitor().triggers == 0);
    romCheckFlashWrites.end();
    RomCheck::idleOn(0x80000000U, 0U, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
}

void testFlashWriteIndicator()
{
    ::safety::RomCheckFlashWrites indicator;
    int const locks = ::romcheck_test::locks();
    CHECK(!indicator.read().writing());
    {
        ::safety::FlashWriteScope const outer;
        CHECK(romCheckFlashWrites.read().writing());
        {
            ::safety::FlashWriteScope const inner;
            CHECK(romCheckFlashWrites.read().begun == romCheckFlashWrites.read().ended + 2U);
        }
        CHECK(romCheckFlashWrites.read().writing());
    }
    CHECK(!romCheckFlashWrites.read().writing());
    indicator.begin();
    indicator.end();
    CHECK(indicator.read().begun == 1U);
    CHECK(indicator.read().ended == 1U);
    CHECK(::romcheck_test::locks() >= locks + 12); // every begin, end and read under the lock
}

// ---- SYS-91: an area with its own reference (the calibration segment), outside the list ----

// The gap words [STARTUP_END, CODE_START) stand for the area: no area of the list covers them, so its content may
// differ from the build (another dataset) without touching the reference of the list; the owner checks it against
// its own reference (here a CRC the "owner" took of the area, like the CRC in the CALV header).
uint32_t ownReference = 0U;
int ownCalls          = 0;
bool ownWritesFlash   = false;

uint32_t ownCrc() { return ~referenceCrc(&flash.words[STARTUP_END], CODE_START - STARTUP_END); }

RomCheck::OwnArea ownCheck()
{
    ++ownCalls;
    if (ownWritesFlash)
    {
        romCheckFlashWrites.begin(); // a flash write that begins while the owner reads its area
        romCheckFlashWrites.end();
    }
    bool const ok = (ownCrc() == ownReference);
    return RomCheck::OwnArea{ok, static_cast<uint32_t>(at(STARTUP_END)), ok ? 0U : 7U};
}

void testOwnAreaCheck()
{
    restart();
    ownReference   = ownCrc();
    ownCalls       = 0;
    ownWritesFlash = false;
    int const shells = ::romcheck_test::shellsOpened();
    RomCheck::setOwnAreaCheck(&ownCheck);
    CHECK(::romcheck_test::shellsOpened() == shells + 1); // the pointer lives in the safety RAM
    CHECK(::romcheck_test::shellDepth() == 0);
    RomCheck::init(); // init() keeps the registration (the owner may register before SafetyManager::init())

    // a valid area passes, called once per pass after the list
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    CHECK(ownCalls == 1);

    // another dataset in the area: the list and its build reference stay green, the owner decides (here: it took
    // the new content as its reference, as the start-up check of a valid segment does)
    restart();
    flash.words[STARTUP_END] ^= 0x00FF00FFU;
    ownReference = ownCrc();
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    CHECK(monitor().triggers == 0);

    // a bit flip in the area after the first pass: found by the re-check after one period, ROM_CHECK_FAILURE
    flash.words[STARTUP_END + 1U] ^= 0x00000100U;
    int const errors = ::util::logger::Logger::errors();
    RomCheck::idleOn(999U, PERIOD, image());
    CHECK(monitor().triggers == 0);
    RomCheck::idleOn(1000U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OWN_AREA_FAILED);
    CHECK(monitor().triggers == 1);
    CHECK(monitor().outsideShell == 0);
    CHECK(::util::logger::Logger::errors() == errors + 1);
    RomCheck::idleOn(5000U, PERIOD, image()); // held: no further pass, no second report
    CHECK(monitor().triggers == 1);
    flash.words[STARTUP_END + 1U] ^= 0x00000100U;

    // the list first: a failing list is reported as such and the owner is not asked
    restart();
    ownCalls = 0;
    flash.words[CODE_START] ^= 1U;
    RomCheck::idleOn(0U, PERIOD, ::safety::romcheck::Image{flash.list, 11U, at(0U), imageReference() ^ 1U});
    CHECK(RomCheck::result() == Result::ROMCHECK_FAILED);
    CHECK(ownCalls == 0);
    flash.words[CODE_START] ^= 1U;

    // a flash write during the owner's check discards the pass like one during the list
    restart();
    ownReference   = ownCrc() ^ 1U; // would fail if it were evaluated
    ownWritesFlash = true;
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_REQUESTED);
    CHECK(RomCheck::statistics().discarded == 1U);
    CHECK(monitor().triggers == 0);
    ownWritesFlash = false;
    ownReference   = ownCrc();
    RomCheck::idleOn(1U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);

    // without a registered check the area is not looked at
    RomCheck::setOwnAreaCheck(nullptr);
    restart();
    ownCalls = 0;
    flash.words[STARTUP_END] ^= 0xFFFFFFFFU;
    RomCheck::idleOn(0U, PERIOD, image());
    CHECK(RomCheck::result() == Result::ROMCHECK_OK);
    CHECK(ownCalls == 0);
}

} // namespace

int main()
{
    testNothingBeforeInit(); // first: nothing may have called RomCheck::init() yet
    testKnownVector();
    testValidImagePasses();
    testEveryBitFlipInTheAreasFails();
    testContentOutsideTheAreasIsNotChecked();
    testImplausibleListsAreRefused();
    testIdleWithAnErasedListTriggersTheMonitor();
    testGeneratedPeriodIsUsed();
    testKnownAnswer();
    testFirstPassThenPeriod();
    testPeriodZeroIsOncePerStart();
    testRecheckFindsALaterCorruption();
    testKnownAnswerCatchesAFaultyUnit();
    testOpenFlashWritePostpones();
    testFlashCommandPostpones();
    testWriteDuringPassDiscardsIt();
    testPassRunsOutsideTheShell();
    testOverdueAfterOnePeriod();
    testFlashWriteIndicator();
    testOwnAreaCheck();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
