// Host test of the FlexNVM EEPROM emulation of the S32K1 family (NVM-02/NVM-07, include/flexnvm/FlexNvmEeprom.h)
// against a register model (stubs/mcu/mcu.h: SIM FCFG1, FTFC FSTAT/FCNFG), a system time that advances per read
// (stubs/bsp/timer/SystemTimer.h) and a FlexRAM window in host memory. No board.
// Checked: EEPROM size decode, the states of a chip as delivered (not partitioned), partitioned for data flash only,
// partitioned but FlexRAM not loaded, ready; usable size limited by window and partition; reads and writes refused
// unless ready and inside; only differing bytes stored, aligned words as one store, one EEERDY wait per store, wait
// bounded in time (NVM-07: limit in microseconds, not in polls; wrap of the 32-bit time; longest write kept);
// read collision reported and cleared; init() refuses and no flash command register is ever written.
// NVM-12: the wait of a store for the end of a data flash command: test/host/FlexNvmDataFlashHostTest.cpp.
// Build and run: platforms/s32k1xx/bsp/bspFlexNvm/test/test_flexnvm.py.

#include "flexnvm/FlexNvmEeprom.h"

#include <cstdio>
#include <cstring>

FakeFtfc fakeFtfc;
FakeSim fakeSim;
FakeCore fakeCore; // NVM-12: stores are made with interrupts suspended
uint32_t fakeTimeUs     = 0U;
uint32_t fakeTimeStepUs = 0U;

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

alignas(4) uint8_t flexRam[4096];

using platform::FlexNvmEeprom;

uint32_t fcfg1(uint32_t const depart, uint32_t const eeeSize)
{
    return 0xFF000FFFU | (eeeSize << 16U) | (depart << 12U);
}

void reset()
{
    fakeFtfc      = FakeFtfc();
    fakeFtfc.FSTAT.value = 0x80U; // NVM-12: CCIF, no flash command runs
    fakeSim.FCFG1 = 0xFFFFFFFFU; // IFR erased: DEPART 0xF, EEERAMSIZE 0xF (chip as delivered)
    std::memset(flexRam, 0x00, sizeof(flexRam));
}

// FCNFG script: EEERDY for the first `readyReads` reads, then clear (a store that never finishes)
uint32_t readyReads = 0xFFFFFFFFU;

uint8_t scriptedReady(FakeReg8 const& reg)
{
    return (reg.reads <= readyReads) ? 1U : 0U;
}

void testDecode()
{
    CHECK(FlexNvmEeprom::eeeSizeBytes(2U) == 4096U);
    CHECK(FlexNvmEeprom::eeeSizeBytes(3U) == 2048U);
    CHECK(FlexNvmEeprom::eeeSizeBytes(4U) == 1024U);
    CHECK(FlexNvmEeprom::eeeSizeBytes(9U) == 32U);
    CHECK(FlexNvmEeprom::eeeSizeBytes(0xFU) == 0U);
    CHECK(FlexNvmEeprom::eeeSizeBytes(0U) == 0U);
    CHECK(FlexNvmEeprom::eeeSizeBytes(1U) == 0U);
    CHECK(FlexNvmEeprom::eeeSizeBytes(0xAU) == 0U);
    fakeSim.FCFG1 = fcfg1(0xBU, 0x2U);
    CHECK(FlexNvmEeprom::departCode() == 0xBU);
    CHECK(FlexNvmEeprom::eeeSizeCode() == 0x2U);
}

void testStates()
{
    reset();
    FlexNvmEeprom eeprom;
    uint8_t buffer[4] = {1U, 2U, 3U, 4U};
    CHECK(eeprom.state() == FlexNvmEeprom::State::NO_WINDOW);
    CHECK(eeprom.read(0U, buffer, 4U) == ::bsp::BSP_ERROR);
    eeprom.setWindow(reinterpret_cast<uintptr_t>(flexRam), sizeof(flexRam));
    fakeFtfc.FCNFG.value = 1U; // FlexRAM as EEPROM would be ready, but ...
    CHECK(eeprom.state() == FlexNvmEeprom::State::NOT_PARTITIONED); // ... the chip as delivered
    CHECK(eeprom.usableBytes() == 0U);
    CHECK(eeprom.write(0U, buffer, 4U) == ::bsp::BSP_ERROR);
    CHECK(flexRam[0] == 0U);
    fakeSim.FCFG1 = fcfg1(0x0U, 0xFU); // partitioned: 64 KB data flash, no EEPROM
    CHECK(eeprom.state() == FlexNvmEeprom::State::NOT_PARTITIONED);
    fakeSim.FCFG1        = fcfg1(0xBU, 0x2U); // 32 KB data flash, 32 KB backup, 4 KB EEPROM
    fakeFtfc.FCNFG.value = 0U;               // not loaded (no load on reset, no SETRAM)
    CHECK(eeprom.state() == FlexNvmEeprom::State::NOT_LOADED);
    CHECK(eeprom.read(0U, buffer, 4U) == ::bsp::BSP_ERROR);
    fakeFtfc.FCNFG.value = 1U;
    CHECK(eeprom.state() == FlexNvmEeprom::State::READY);
    CHECK(eeprom.usableBytes() == 4096U);
    eeprom.setWindow(reinterpret_cast<uintptr_t>(flexRam), 2048U); // smaller window: limits
    CHECK(eeprom.usableBytes() == 2048U);
    eeprom.setWindow(reinterpret_cast<uintptr_t>(flexRam), sizeof(flexRam));
    fakeSim.FCFG1 = fcfg1(0xBU, 0x3U); // 2 KB EEPROM
    CHECK(eeprom.usableBytes() == 2048U);
    CHECK(std::strcmp(FlexNvmEeprom::stateText(FlexNvmEeprom::State::NOT_PARTITIONED),
                      "FlexNVM not partitioned for EEPROM (no PGMPART yet)")
          == 0);
    // init() never prepares anything
    CHECK(eeprom.init() == ::bsp::BSP_ERROR);
    CHECK(fakeFtfc.FSTAT.writes == 0U);
}

void testReadWrite()
{
    reset();
    fakeSim.FCFG1        = fcfg1(0xBU, 0x2U);
    fakeFtfc.FCNFG.value = 1U;
    FlexNvmEeprom eeprom;
    eeprom.setWindow(reinterpret_cast<uintptr_t>(flexRam), sizeof(flexRam));
    uint8_t const data[8] = {1U, 2U, 3U, 4U, 5U, 6U, 7U, 8U};
    uint32_t reads        = fakeFtfc.FCNFG.reads;
    CHECK(eeprom.write(8U, data, 8U) == ::bsp::BSP_OK);
    CHECK(std::memcmp(&flexRam[8], data, 8U) == 0);
    CHECK((fakeFtfc.FCNFG.reads - reads) == (1U + 2U)); // state check + one wait per word store
    reads = fakeFtfc.FCNFG.reads;
    CHECK(eeprom.write(8U, data, 8U) == ::bsp::BSP_OK); // same data: no store at all
    CHECK((fakeFtfc.FCNFG.reads - reads) == 1U);
    uint8_t changed[8];
    std::memcpy(changed, data, 8U);
    changed[5] = 0x55U;
    reads      = fakeFtfc.FCNFG.reads;
    CHECK(eeprom.write(8U, changed, 8U) == ::bsp::BSP_OK); // one word differs: one store
    CHECK((fakeFtfc.FCNFG.reads - reads) == 2U);
    CHECK(flexRam[13] == 0x55U);
    // unaligned: byte stores for the differing bytes only
    uint8_t const three[3] = {0xA0U, 0x00U, 0xA2U};
    reads                  = fakeFtfc.FCNFG.reads;
    CHECK(eeprom.write(1U, three, 3U) == ::bsp::BSP_OK);
    CHECK((fakeFtfc.FCNFG.reads - reads) == (1U + 2U)); // flexRam[2] already 0x00
    CHECK((flexRam[1] == 0xA0U) && (flexRam[2] == 0x00U) && (flexRam[3] == 0xA2U));
    uint8_t back[8] = {};
    CHECK(eeprom.read(8U, back, 8U) == ::bsp::BSP_OK);
    CHECK(std::memcmp(back, changed, 8U) == 0);
    // bounds: the usable size, not the window
    fakeSim.FCFG1 = fcfg1(0xBU, 0x3U); // 2 KB
    CHECK(eeprom.read(2044U, back, 4U) == ::bsp::BSP_OK);
    CHECK(eeprom.read(2045U, back, 4U) == ::bsp::BSP_ERROR);
    CHECK(eeprom.write(2048U, data, 1U) == ::bsp::BSP_ERROR);
    CHECK(eeprom.read(0U, back, 0U) == ::bsp::BSP_ERROR);
    CHECK(eeprom.read(0U, nullptr, 1U) == ::bsp::BSP_ERROR);
    CHECK(fakeFtfc.FSTAT.writes == 0U);
    // read collision: reported and cleared (write 1 to RDCOLERR only)
    fakeFtfc.FSTAT.value = 0x40U;
    CHECK(eeprom.read(8U, back, 4U) == ::bsp::BSP_ERROR);
    CHECK(fakeFtfc.FSTAT.writes == 1U);
    CHECK(fakeFtfc.FSTAT.lastWritten == 0x40U);
    CHECK(eeprom.read(8U, back, 4U) == ::bsp::BSP_OK);
}

void testBoundedWait()
{
    reset();
    fakeSim.FCFG1 = fcfg1(0xBU, 0x2U);
    FlexNvmEeprom eeprom(4000U); // S32K144, 32 KB backup: teewr32b 2000 us x 2
    CHECK(eeprom.writeLimitUs() == 4000U);
    eeprom.setWindow(reinterpret_cast<uintptr_t>(flexRam), sizeof(flexRam));
    fakeFtfc.FCNFG.onRead = &scriptedReady;
    // a store that never finishes: given up after the time limit, not after a number of polls
    readyReads     = 1U; // the state check sees EEERDY, after the store it never comes back
    fakeTimeUs     = 1000U;
    fakeTimeStepUs = 100U;
    uint8_t const value = 0x77U;
    CHECK(eeprom.write(3U, &value, 1U) == ::bsp::BSP_ERROR);
    uint32_t const polls = fakeFtfc.FCNFG.reads - 1U;
    CHECK(polls == 41U); // 100 us per time read: 0 .. 4100 us, the 41st poll is the first above 4000 us
    CHECK(eeprom.timeouts() == 1U);
    // the same limit with a slower time source (another core clock): fewer polls, same time
    reset();
    fakeSim.FCFG1         = fcfg1(0xBU, 0x2U);
    fakeFtfc.FCNFG.onRead = &scriptedReady;
    readyReads            = 1U;
    fakeTimeStepUs        = 1000U;
    CHECK(eeprom.write(3U, &value, 1U) == ::bsp::BSP_ERROR);
    CHECK((fakeFtfc.FCNFG.reads - 1U) == 5U);
    // across the wrap of the 32-bit microsecond time
    reset();
    fakeSim.FCFG1         = fcfg1(0xBU, 0x2U);
    fakeFtfc.FCNFG.onRead = &scriptedReady;
    readyReads            = 1U;
    fakeTimeUs            = 0xFFFFFF00U;
    fakeTimeStepUs        = 100U;
    CHECK(eeprom.write(3U, &value, 1U) == ::bsp::BSP_ERROR);
    CHECK((fakeFtfc.FCNFG.reads - 1U) == 41U);
    // a store that finishes after 3 polls of 700 us: accepted, the longest write is kept
    reset();
    fakeSim.FCFG1         = fcfg1(0xBU, 0x2U);
    fakeFtfc.FCNFG.onRead = &scriptedReady;
    readyReads            = 1U + 3U;
    fakeTimeStepUs        = 700U;
    FlexNvmEeprom second(4000U);
    second.setWindow(reinterpret_cast<uintptr_t>(flexRam), sizeof(flexRam));
    // readyReads counts reads from 1: read 1 = state check, reads 2..4 see EEERDY; script "late": clear then set
    fakeFtfc.FCNFG.onRead = [](FakeReg8 const& reg) -> uint8_t { return ((reg.reads == 1U) || (reg.reads >= 4U)) ? 1U : 0U; };
    CHECK(second.write(5U, &value, 1U) == ::bsp::BSP_OK);
    CHECK(second.longestWriteUs() == 2100U); // time read before each poll: 700, 1400, 2100 us -> ready at 2100
    CHECK(second.timeouts() == 0U);
    fakeFtfc.FCNFG.onRead = nullptr;
    fakeTimeStepUs        = 0U;
}
} // namespace

int main()
{
    testDecode();
    testStates();
    testReadWrite();
    testBoundedWait();
    std::printf("FlexNvmEepromHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
