// NVM-12: host test of the data flash areas (libs/bsw/nvmDataFlash/include/dataflash/DataFlashAreas.h) with its own
// test profile - a simulated data flash of 16 KB with 1 KB sectors and a program unit of 8 bytes (no derivative):
// erase sets whole sectors to 0xFF, program may only move bits 1 -> 0, a command stays busy for a number of polls,
// another command of the block (an EEPROM store) can hold it off, faults can be injected (command error, time-out,
// read-back differs, power loss in the middle of an erase).
// Checked: layout check against the geometry, find by name, read inside the area only, erase job over every sector
// of the area and nothing else, write job (blank check, skip what is already there, NOT_ERASED over data, read-back,
// program unit alignment), one job at a time, BUSY of the block only delays, errors end the job once, write hooks
// balanced on every path (SYS-44), power loss repaired by erasing again.
// NVM-26: the admission refuses new jobs (counted, before any other check), a job accepted before runs on, reads stay.
// NVM-25: DirectRead holds once and releases once, gives addresses only while held and inside the data flash;
// areaAddress() only for a scope of the service's own flash and inside the area.
// Build and run: libs/bsw/nvmDataFlash/test/test_nvm_data_flash.py.

#include "dataflash/DataFlashAreas.h"

#include <cstdio>
#include <cstring>

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

using ::dataflash::Area;
using ::dataflash::AreaService;
using ::dataflash::Geometry;
using ::dataflash::IDataFlash;
using ::dataflash::Purpose;
using ::dataflash::Status;
using ::dataflash::WriteHooks;

constexpr uint32_t SECTOR = 1024U;
constexpr uint32_t SIZE   = 16U * SECTOR;
constexpr uint32_t UNIT   = 8U;

uint32_t begun = 0U;
uint32_t ended = 0U;

class FakeFlash final : public IDataFlash
{
public:
    uint8_t mem[SIZE];
    uint32_t size        = SIZE;
    uint32_t unit        = UNIT;
    uint32_t busyPolls   = 2U;  // polls a command stays busy
    uint32_t blockedFor  = 0U;  // starts/reads refused as BUSY (another command of the block)
    Status nextResult    = Status::OK;
    bool corruptProgram  = false; // program one bit less than asked (read-back differs)
    uint32_t powerLossAt = 0xFFFFFFFFU; // erase: stop after this many bytes, leave the rest as it was
    uint32_t erases      = 0U;
    uint32_t programs    = 0U;
    uint32_t reads       = 0U;
    uint32_t erasedSectors[32] = {};
    bool overProgrammed = false; // a 0 bit programmed again (not allowed by the module)
    bool hooked         = false; // SYS-44: every launch must find the write hook open
    bool launchedClosed = false;

    FakeFlash() { std::memset(mem, 0x3C, sizeof(mem)); }

    Geometry geometry() const override { return Geometry{0x10000000U, size, SECTOR, unit}; }

    Status read(uint32_t const offset, uint8_t* const buffer, uint32_t const length) override
    {
        if ((length == 0U) || (offset >= size) || (length > (size - offset)) || (buffer == nullptr))
        {
            return Status::RANGE;
        }
        if (_left != 0U || blocked())
        {
            return Status::BUSY;
        }
        ++reads;
        std::memcpy(buffer, &mem[offset], length);
        return Status::OK;
    }

    Status startErase(uint32_t const offset) override
    {
        if ((offset >= size) || ((offset % SECTOR) != 0U))
        {
            return Status::ALIGNMENT;
        }
        if ((_left != 0U) || _pending || blocked())
        {
            return Status::BUSY;
        }
        ++erases;
        launchedClosed = launchedClosed || (hooked && (begun == ended));
        erasedSectors[offset / SECTOR]++;
        uint32_t const n = (powerLossAt < SECTOR) ? powerLossAt : SECTOR;
        std::memset(&mem[offset], 0xFF, n);
        powerLossAt = 0xFFFFFFFFU;
        begin();
        return Status::OK;
    }

    Status startProgram(uint32_t const offset, uint8_t const* const data) override
    {
        if ((offset >= size) || ((offset % unit) != 0U))
        {
            return Status::ALIGNMENT;
        }
        if ((_left != 0U) || _pending || blocked())
        {
            return Status::BUSY;
        }
        ++programs;
        launchedClosed = launchedClosed || (hooked && (begun == ended));
        for (uint32_t i = 0U; i < unit; ++i)
        {
            uint8_t const want = data[i];
            if ((static_cast<uint8_t>(~mem[offset + i]) & static_cast<uint8_t>(~want)) != 0U)
            {
                overProgrammed = true;
            }
            mem[offset + i] = static_cast<uint8_t>(mem[offset + i] & want);
        }
        if (corruptProgram)
        {
            mem[offset] = static_cast<uint8_t>(mem[offset] | 0x80U);
            corruptProgram = false;
        }
        begin();
        return Status::OK;
    }

    Status poll() override
    {
        if (!_pending)
        {
            return Status::OK;
        }
        if (_left != 0U)
        {
            --_left;
            return Status::BUSY;
        }
        _pending = false;
        Status const r = nextResult;
        nextResult     = Status::OK;
        return r;
    }

private:
    void begin()
    {
        _pending = true;
        _left    = busyPolls;
    }

    bool blocked()
    {
        if (blockedFor != 0U)
        {
            --blockedFor;
            return true;
        }
        return false;
    }

public:
    // NVM-25: hold for a read in place - granted while no command runs and the block is not blocked
    uint32_t holds    = 0U;
    uint32_t releases = 0U;

    bool holdForDirectRead() override
    {
        if ((_left != 0U) || _pending || (blockedFor != 0U))
        {
            return false;
        }
        ++holds;
        return true;
    }

    void releaseDirectRead() override { ++releases; }

private:
    bool _pending  = false;
    uint32_t _left = 0U;
};

void onBegin() { ++begun; }

void onEnd() { ++ended; }

Area const AREAS[] = {
    {"CalMain", 0U, 4U * SECTOR, Purpose::CALIBRATION},
    {"CalBackup", 4U * SECTOR, 4U * SECTOR, Purpose::CALIBRATION},
    {"Trip", 12U * SECTOR, 2U * SECTOR, Purpose::DATA},
};

Status run(AreaService& s, uint32_t* steps = nullptr)
{
    Status r = Status::BUSY;
    uint32_t n = 0U;
    while ((r == Status::BUSY) && (n < 100000U))
    {
        r = s.cyclic();
        ++n;
    }
    if (steps != nullptr)
    {
        *steps = n;
    }
    return r;
}

void testLayout()
{
    FakeFlash flash;
    AreaService s(flash, AREAS, 3U);
    CHECK(s.check() == Status::OK);
    CHECK(s.count() == 3U);
    CHECK(s.find("CalBackup") == 1U);
    CHECK(s.find("Trip") == 2U);
    CHECK(s.find("Cal") == AreaService::NO_AREA);
    CHECK(s.find("CalMainX") == AreaService::NO_AREA);
    CHECK(s.find(nullptr) == AreaService::NO_AREA);
    CHECK(s.area(2U)->purpose == Purpose::DATA);
    CHECK(s.area(3U) == nullptr);
    flash.size = 13U * SECTOR; // the chip offers less (another partition): Trip does not fit
    CHECK(s.check() == Status::RANGE);
    CHECK(s.erase(0U) == Status::RANGE);
    flash.size = 0U;
    CHECK(s.check() == Status::NOT_AVAILABLE);
    uint8_t b[4];
    CHECK(s.erase(0U) == Status::NOT_AVAILABLE);
    flash.size = SIZE;
    flash.unit = 3U; // program unit not dividing the sector
    CHECK(s.check() == Status::ALIGNMENT);
    flash.unit = 64U; // larger than the service supports
    CHECK(s.check() == Status::ALIGNMENT);
    flash.unit = UNIT;
    Area const odd[] = {{"Odd", 100U, SECTOR, Purpose::DATA}};
    AreaService o(flash, odd, 1U);
    CHECK(o.check() == Status::ALIGNMENT);
    Area const part[] = {{"Part", 0U, SECTOR + 8U, Purpose::DATA}};
    AreaService p(flash, part, 1U);
    CHECK(p.check() == Status::ALIGNMENT);
    Area const empty[] = {{"Empty", 0U, 0U, Purpose::DATA}};
    AreaService e(flash, empty, 1U);
    CHECK(e.check() == Status::RANGE);
    CHECK(s.read(0U, 0U, b, 4U) == Status::OK); // reading needs no valid layout of the others
}

void testRead()
{
    FakeFlash flash;
    for (uint32_t i = 0U; i < SIZE; ++i)
    {
        flash.mem[i] = static_cast<uint8_t>(i >> 4U);
    }
    AreaService s(flash, AREAS, 3U);
    uint8_t b[16] = {};
    CHECK(s.read(1U, 16U, b, 16U) == Status::OK);
    CHECK(b[0] == static_cast<uint8_t>((4U * SECTOR + 16U) >> 4U));
    CHECK(s.read(2U, 2U * SECTOR - 16U, b, 16U) == Status::OK);
    CHECK(s.read(2U, 2U * SECTOR - 15U, b, 16U) == Status::RANGE); // not beyond the area
    CHECK(s.read(2U, 2U * SECTOR, b, 1U) == Status::RANGE);
    CHECK(s.read(3U, 0U, b, 1U) == Status::RANGE);
    CHECK(s.read(0U, 0U, b, 0U) == Status::RANGE);
    CHECK(s.read(0U, 0U, nullptr, 1U) == Status::RANGE);
    flash.blockedFor = 1U;
    CHECK(s.read(0U, 0U, b, 4U) == Status::BUSY);
    CHECK(s.read(0U, 0U, b, 4U) == Status::OK);
}

void testErase()
{
    FakeFlash flash;
    begun = ended = 0U;
    AreaService s(flash, AREAS, 3U, WriteHooks{&onBegin, &onEnd});
    flash.hooked = true;
    CHECK(s.cyclic() == Status::OK); // idle
    CHECK(s.erase(1U) == Status::OK);
    CHECK(s.busy());
    CHECK(begun == 0U); // SYS-44: open only while a command runs
    CHECK(s.erase(0U) == Status::BUSY); // one job at a time
    uint8_t b[8];
    CHECK(s.write(0U, 0U, b, 8U) == Status::BUSY);
    CHECK(s.cyclic() == Status::BUSY); // first sector launched
    CHECK((begun == 1U) && (ended == 0U));
    CHECK(s.cyclic() == Status::BUSY); // still running
    CHECK((begun == 1U) && (ended == 0U));
    CHECK(run(s) == Status::OK);
    CHECK(!s.busy());
    CHECK(s.lastResult() == Status::OK);
    CHECK(s.cyclic() == Status::OK);
    CHECK((begun == 4U) && (ended == 4U)); // one bracket per sector
    CHECK(flash.erases == 4U);
    CHECK(s.commands() == 4U);
    for (uint32_t i = 0U; i < 16U; ++i)
    {
        CHECK(flash.erasedSectors[i] == (((i >= 4U) && (i < 8U)) ? 1U : 0U));
    }
    CHECK(flash.mem[4U * SECTOR - 1U] == 0x3CU);
    CHECK(flash.mem[4U * SECTOR] == 0xFFU);
    CHECK(flash.mem[8U * SECTOR - 1U] == 0xFFU);
    CHECK(flash.mem[8U * SECTOR] == 0x3CU);
    // another command of the block holds the job off: only later, never skipped
    flash.blockedFor = 5U;
    CHECK(s.erase(2U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK((flash.erasedSectors[12] == 1U) && (flash.erasedSectors[13] == 1U));
    // a command error ends the job once, with the hooks balanced
    flash.nextResult = Status::FAILED;
    CHECK(s.erase(0U) == Status::OK);
    CHECK(run(s) == Status::FAILED);
    CHECK(s.lastResult() == Status::FAILED);
    CHECK(s.cyclic() == Status::OK);
    CHECK(flash.erasedSectors[1] == 0U); // stopped after the failed sector
    flash.nextResult = Status::TIMEOUT;
    CHECK(s.erase(0U) == Status::OK);
    CHECK(run(s) == Status::TIMEOUT);
    CHECK(begun == ended);
    // a job held off for long (EEPROM stores, or a block that never becomes free) keeps no write open
    flash.blockedFor = 1000U;
    CHECK(s.erase(2U) == Status::OK);
    for (uint32_t i = 0U; i < 50U; ++i)
    {
        CHECK(s.cyclic() == Status::BUSY);
        CHECK(begun == ended);
    }
    flash.blockedFor = 0U;
    CHECK(run(s) == Status::OK);
    CHECK(begun == ended);
    CHECK(!flash.launchedClosed);
}

void testWrite()
{
    FakeFlash flash;
    begun = ended = 0U;
    AreaService s(flash, AREAS, 3U, WriteHooks{&onBegin, &onEnd});
    flash.hooked = true;
    CHECK(s.erase(0U) == Status::OK);
    CHECK(run(s) == Status::OK);
    uint8_t data[64];
    for (uint32_t i = 0U; i < sizeof(data); ++i)
    {
        data[i] = static_cast<uint8_t>(0xA0U + i);
    }
    std::memset(&data[16], 0xFF, 8U); // an erased unit is not programmed at all
    CHECK(s.write(0U, 8U, data, 3U) == Status::ALIGNMENT);
    CHECK(s.write(0U, 3U, data, 8U) == Status::ALIGNMENT);
    CHECK(s.write(0U, 4U * SECTOR - 8U, data, 16U) == Status::RANGE); // beyond the area
    CHECK(s.write(0U, 0U, nullptr, 8U) == Status::RANGE);
    CHECK(s.write(0U, 0U, data, 0U) == Status::RANGE);
    uint32_t const programs = flash.programs;
    CHECK(s.write(0U, 64U, data, 64U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK((flash.programs - programs) == 7U);
    CHECK(std::memcmp(&flash.mem[64], data, 64U) == 0);
    CHECK(flash.mem[63] == 0xFFU);
    CHECK(flash.mem[128] == 0xFFU);
    CHECK(!flash.overProgrammed);
    // the same write again (interrupted before and repeated): nothing programmed, OK
    CHECK(s.write(0U, 64U, data, 64U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK((flash.programs - programs) == 7U);
    CHECK(!flash.overProgrammed);
    // other data over it: refused before anything is programmed over data
    uint8_t other[8];
    std::memset(other, 0x11U, sizeof(other));
    CHECK(s.write(0U, 64U, other, 8U) == Status::OK);
    CHECK(run(s) == Status::NOT_ERASED);
    CHECK(std::memcmp(&flash.mem[64], data, 8U) == 0);
    CHECK(!flash.overProgrammed);
    // read-back differs: FAILED, not OK
    flash.corruptProgram = true;
    CHECK(s.write(1U, 0U, other, 8U) == Status::OK);
    CHECK(run(s) == Status::NOT_ERASED); // area 1 not erased yet (0x3C)
    CHECK(s.erase(1U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK(s.write(1U, 0U, other, 8U) == Status::OK);
    CHECK(run(s) == Status::FAILED);
    // the command reports an error
    flash.nextResult = Status::FAILED;
    CHECK(s.write(1U, 8U, other, 8U) == Status::OK);
    CHECK(run(s) == Status::FAILED);
    // busy block during the read of a unit and during the program: the job waits
    flash.blockedFor = 3U;
    CHECK(s.write(1U, 16U, other, 8U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK(std::memcmp(&flash.mem[4U * SECTOR + 16U], other, 8U) == 0);
    CHECK(begun == ended);
    CHECK(!flash.launchedClosed);
}

void testPowerLoss()
{
    // an erase interrupted by a reset leaves the sector partly old: the next erase of the area repairs it; a write
    // over the half-erased sector is refused (NOT_ERASED), never programmed over the old bits
    FakeFlash flash;
    AreaService s(flash, AREAS, 3U);
    flash.powerLossAt = 100U;
    CHECK(s.erase(2U) == Status::OK);
    CHECK(run(s) == Status::OK); // the fake finishes the job; the first sector is only partly erased
    CHECK((flash.mem[12U * SECTOR + 99U] == 0xFFU) && (flash.mem[12U * SECTOR + 100U] == 0x3CU));
    uint8_t data[8];
    std::memset(data, 0x55U, sizeof(data));
    CHECK(s.write(2U, 96U, data, 8U) == Status::OK);
    CHECK(run(s) == Status::NOT_ERASED);
    CHECK(!flash.overProgrammed);
    CHECK(s.erase(2U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK(s.write(2U, 96U, data, 8U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK(std::memcmp(&flash.mem[12U * SECTOR + 96U], data, 8U) == 0);
}

// NVM-26: admission of new jobs (s32k1xx port: not from STANDBY on)
bool admit = true;

bool admission() { return admit; }

void testAdmission()
{
    FakeFlash flash;
    begun = ended = 0U;
    AreaService s(flash, AREAS, 3U, WriteHooks{&onBegin, &onEnd}, &admission);
    uint8_t data[8];
    std::memset(data, 0x66U, sizeof(data));
    admit = false;
    CHECK(s.erase(0U) == Status::NOT_AVAILABLE);
    CHECK(s.write(0U, 0U, data, 8U) == Status::NOT_AVAILABLE);
    CHECK(s.refusedJobs() == 2U);
    CHECK(!s.busy());
    CHECK(s.cyclic() == Status::OK);
    CHECK(flash.erases == 0U);
    CHECK(begun == 0U);
    // refused before anything else: also a request that would be wrong anyway counts as refused
    CHECK(s.erase(7U) == Status::NOT_AVAILABLE);
    CHECK(s.refusedJobs() == 3U);
    // reads stay possible
    uint8_t b[4];
    CHECK(s.read(0U, 0U, b, 4U) == Status::OK);
    // admitted again (STANDBY back to RUN): jobs work
    admit = true;
    CHECK(s.erase(0U) == Status::OK);
    // a job accepted before keeps running when the admission closes (it is cut by the run-down, not here)
    admit = false;
    CHECK(run(s) == Status::OK);
    CHECK(flash.erases == 4U);
    CHECK(s.write(0U, 0U, data, 8U) == Status::NOT_AVAILABLE);
    CHECK(s.refusedJobs() == 4U);
    admit = true;
    CHECK(s.write(0U, 0U, data, 8U) == Status::OK);
    CHECK(run(s) == Status::OK);
    CHECK(std::memcmp(&flash.mem[0], data, 8U) == 0);
    CHECK(begun == ended);
    // without an admission: always accepted, nothing counted
    AreaService always(flash, AREAS, 3U);
    admit = false;
    CHECK(always.erase(2U) == Status::OK);
    CHECK(always.refusedJobs() == 0U);
    CHECK(run(always) == Status::OK);
    admit = true;
}

// NVM-25: read in place under a hold (DirectRead, areaAddress)
void testDirectRead()
{
    FakeFlash flash;
    AreaService s(flash, AREAS, 3U);
    {
        ::dataflash::DirectRead scope(flash);
        CHECK(scope.held());
        CHECK(flash.holds == 1U);
        CHECK(scope.address(0U, 1U) == 0x10000000U);
        CHECK(scope.address(SIZE - 8U, 8U) == (0x10000000U + SIZE - 8U));
        CHECK(scope.address(SIZE - 7U, 8U) == 0U); // beyond the usable data flash
        CHECK(scope.address(0U, 0U) == 0U);
        CHECK(scope.address(SIZE, 1U) == 0U);
        CHECK(flash.releases == 0U);
    }
    CHECK(flash.releases == 1U); // released once at the end of the scope
    {
        ::dataflash::DirectRead scope = s.holdForDirectRead();
        CHECK(scope.held());
        CHECK(s.areaAddress(scope, 1U, 16U, 32U) == (0x10000000U + 4U * SECTOR + 16U));
        CHECK(s.areaAddress(scope, 2U, 2U * SECTOR - 8U, 8U) == (0x10000000U + 12U * SECTOR + 2U * SECTOR - 8U));
        CHECK(s.areaAddress(scope, 2U, 2U * SECTOR - 7U, 8U) == 0U); // beyond the area
        CHECK(s.areaAddress(scope, 3U, 0U, 1U) == 0U);               // no such area
        CHECK(s.areaAddress(scope, 0U, 0U, 0U) == 0U);
        FakeFlash other;
        ::dataflash::DirectRead foreign(other);
        CHECK(foreign.held());
        CHECK(s.areaAddress(foreign, 0U, 0U, 8U) == 0U); // a scope of another flash gives nothing
    }
    CHECK(flash.releases == 2U);
    // a busy block: not held, no address, no release
    CHECK(s.erase(0U) == Status::OK);
    CHECK(s.cyclic() == Status::BUSY); // a command runs
    {
        ::dataflash::DirectRead scope = s.holdForDirectRead();
        CHECK(!scope.held());
        CHECK(scope.address(0U, 8U) == 0U);
        CHECK(s.areaAddress(scope, 0U, 0U, 8U) == 0U);
    }
    CHECK(flash.holds == 2U);
    CHECK(flash.releases == 2U);
    CHECK(run(s) == Status::OK);
}
} // namespace

int main()
{
    testLayout();
    testRead();
    testErase();
    testWrite();
    testPowerLoss();
    testAdmission();
    testDirectRead();
    std::printf("DataFlashHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
