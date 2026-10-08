// Host test of the CSEc way back to the factory state (NVM-13, include/csec/CsecRecovery.h, src/CsecRecovery.cpp)
// against a model of the CSEc: the parameter RAM (32 words, word 0 = command header starts the command), FSTAT CCIF
// cleared while a command runs and set after a number of polls, error bits in word 1 bits 31:16, FCSESTAT RIN after
// INIT_RNG, the DBG_CHAL -> DBG_AUTH pairing ([RM13] 36.5.13). Answers (M4/M5, UID, CHALLENGE) and errors are scripted;
// the model does no cryptography - the vectors below are computed by tools/csec and checked there
// (tools/csec/test_she.py DeviceConstantTest), so they cannot drift.
//
// Checked: nothing reaches the parameter RAM without the typed confirmation, for a key other than the MASTER_ECU_KEY
// authorised by itself, for an M2 with WRITE_PROTECTION or any other changed bit in its first block, on a part without
// CSEc, with the FlexRAM not the emulated EEPROM, or while a command runs; the word order and byte order of M1..M3,
// header last and exactly once, interrupts suspended and cache off while it runs, results read only after CCIF; M4/M5
// on success and nothing on an error; INIT_RNG only when FCSESTAT RIN is clear; GET_ID before DBG_CHAL; DBG_AUTH only
// with the challenge of this session and its word, one attempt per challenge, voided by any command in between; the
// model never sees a key flag.
// NVM-17: a model of the MCU watchdog on simulated LPO time (8 ticks per FSTAT poll, 128 kHz): its counter, the
// time-out and the window, a refresh before the window or a counter past the time-out is a watchdog reset (thrown
// out of the RAM function, recorded with its time). Checked: INIT_RNG only through initRng() (challenge() refuses with
// RIN clear), commands of 600 ms, 5 s and 19 s end without a reset, a hanging command ends in the watchdog reset after
// its budget (INIT_RNG 2 s, DBG_AUTH 20 s) and not before, the duration from the watchdog counter, watchdog off /
// other clock / no 32-bit refresh / window / prescaler, RUN mode only, the journal (running before the launch, done
// before the interrupts come back), CallSeq 0 in every header (LOAD_KEY included), the hex fields of the console.
// NVM-21: the model answers only at the CSE_PRAM address of the part it plays (S32K144 0x1400_1000, S32K116
// 0x1400_0800); a write elsewhere is a write into the FlexRAM (board 2026-09-30) and starts nothing. Checked: the
// address follows SIM SDID SUBSERIES, every access of every command uses it, an unknown sub-series is refused.
// Build and run: test/test_csec.py.

#include "csec/CsecRecovery.h"

#include "cache/cache.h"
#include "csec/CsecHex.h"
#include "csec/CsecPram.h"
#include "mcu/mcu.h"

#include <cstdio>
#include <cstring>
#include <initializer_list>

FakeFtfc fakeFtfc;
FakeSim fakeSim;
FakeCore fakeCore;
FakeWdog fakeWdog;
FakeSmc fakeSmc;

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

using platform::CsecRecovery;
using Result = CsecRecovery::Result;

// computed by tools/csec (MASTER_ECU_KEY of the test only), checked in tools/csec/test_she.py
uint8_t const TEST_MASTER_KEY[16] = {0x01U, 0x23U, 0x45U, 0x67U, 0x89U, 0xABU, 0xCDU, 0xEFU, 0xFEU, 0xDCU, 0xBAU, 0x98U, 0x76U, 0x54U, 0x32U, 0x10U};
uint8_t const TEST_M1[16] = {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x11U};
uint8_t const TEST_M2[32] = {0x88U, 0x9BU, 0x71U, 0x64U, 0x28U, 0xBFU, 0x0FU, 0xD9U, 0x9AU, 0xBAU, 0x27U, 0xFCU, 0x1FU, 0xB1U, 0xDEU, 0x0DU, 0x67U, 0x30U, 0x67U, 0x3EU, 0xC9U, 0xACU, 0x97U, 0x5CU, 0x8AU, 0x15U, 0x7FU, 0x00U, 0x4BU, 0x88U, 0xFAU, 0x1FU};
uint8_t const TEST_M3[16] = {0x87U, 0x8EU, 0x64U, 0x52U, 0xA5U, 0x5DU, 0x01U, 0x99U, 0x2AU, 0xEEU, 0x0CU, 0xD0U, 0x1CU, 0xBDU, 0x87U, 0xB5U};
uint8_t const TEST_M4[32] = {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x11U, 0x45U, 0x98U, 0x2BU, 0xB7U, 0x65U, 0xB5U, 0x1BU, 0x12U, 0x1FU, 0x2EU, 0x80U, 0xAAU, 0x35U, 0xCAU, 0xE4U, 0xF7U};
uint8_t const TEST_M5[16] = {0x88U, 0xAFU, 0xADU, 0xB4U, 0xDFU, 0x85U, 0x8BU, 0xE6U, 0x40U, 0x7DU, 0x30U, 0x67U, 0x3AU, 0x8FU, 0x14U, 0x93U};
// the same load with WRITE_PROTECTION set (FID bit 4) - must never reach the CSEc
uint8_t const TEST_M2_WRITE_PROTECTED[32] = {0xC7U, 0x85U, 0xCDU, 0xDDU, 0xE0U, 0x1FU, 0x99U, 0x00U, 0x61U, 0x9EU, 0x2EU, 0x0CU, 0x61U, 0x01U, 0x3EU, 0x70U, 0x10U, 0xB4U, 0xB9U, 0xECU, 0xE2U, 0xE7U, 0x7AU, 0x18U, 0x73U, 0x97U, 0x57U, 0x24U, 0xD8U, 0xE3U, 0x49U, 0xE1U};
uint8_t const TEST_UID[15] = {0x0AU, 0x1BU, 0x2CU, 0x3DU, 0x4EU, 0x5FU, 0x60U, 0x71U, 0x82U, 0x93U, 0xA4U, 0xB5U, 0xC6U, 0xD7U, 0xE8U};
uint8_t const TEST_CHALLENGE[16] = {0xC0U, 0xFFU, 0xEEU, 0x00U, 0x11U, 0x22U, 0x33U, 0x44U, 0x55U, 0x66U, 0x77U, 0x88U, 0x99U, 0xAAU, 0xBBU, 0xCCU};
uint8_t const TEST_AUTH[16] = {0x8CU, 0x60U, 0x0AU, 0x2AU, 0xDDU, 0x9AU, 0x26U, 0x2EU, 0xA1U, 0x87U, 0x9AU, 0x62U, 0xD5U, 0x06U, 0x0FU, 0x49U};

// ------------------------------------------------------------------------------------------------- CSEc model
struct Launch
{
    uint32_t header;
    bool interruptsOn;
    bool cacheOn;
    uint32_t words[32]; // parameter RAM when the header was written
};

struct Model
{
    uint32_t pram[32]          = {};
    uint32_t writes            = 0U;   // parameter RAM writes (any word)
    int32_t lastDataWrite      = -1;   // sequence number of the last data write
    uint32_t sequence          = 0U;
    Launch launches[8]         = {};
    uint32_t count             = 0U;
    uint32_t busyPolls         = 3U;   // FSTAT reads with CCIF 0 per command (one LPO tick each)
    uint32_t pollsFor[0x20]    = {};   // NVM-17: per FuncID, overrides busyPolls if not 0
    bool hang                  = false; // NVM-17: the commands launched after hangFrom never complete
    uint32_t hangFrom          = 0U;
    uint32_t pollsLeft         = 0U;
    bool readWhileBusy        = false;
    uint16_t scriptedError[0x20] = {}; // per FuncID, 0 = ERC_NO_ERROR
    bool challengeIssued       = false; // last command was DBG_CHAL
    bool masterLoaded          = false;
    bool keysErased            = false;
    bool keyFlagSeen           = false; // an M2 other than "counter 1, no flags" arrived (never allowed)
    uintptr_t base             = 0U;    // NVM-21: CSE_PRAM address of the part the model plays
    uint32_t foreignWrites     = 0U;    // NVM-21: writes to another address (the FlexRAM on the board)
    uint32_t foreignReads      = 0U;
} model;

void put(uint32_t const first, uint8_t const* const bytes, uint32_t const n)
{
    for (uint32_t i = 0U; i < n; i += 4U)
    {
        model.pram[first + (i / 4U)] = (static_cast<uint32_t>(bytes[i]) << 24U) | (static_cast<uint32_t>(bytes[i + 1U]) << 16U)
                                       | (static_cast<uint32_t>(bytes[i + 2U]) << 8U) | bytes[i + 3U];
    }
}

void get(uint32_t const first, uint8_t* const bytes, uint32_t const n)
{
    for (uint32_t i = 0U; i < n; ++i)
    {
        bytes[i] = static_cast<uint8_t>(model.pram[first + (i / 4U)] >> (24U - (8U * (i % 4U))));
    }
}

void execute(uint32_t const header)
{
    uint8_t const cmd    = static_cast<uint8_t>(header >> 24U);
    uint8_t const format = static_cast<uint8_t>(header >> 16U);
    uint8_t const seq    = static_cast<uint8_t>(header >> 8U);
    uint8_t const keyId  = static_cast<uint8_t>(header);
    uint16_t error       = (cmd < 0x20U) ? model.scriptedError[cmd] : 0U;
    bool const afterChallenge = model.challengeIssued;
    model.challengeIssued     = false;
    if ((format != 0U) || (seq != 0U))
    {
        error = CsecRecovery::ERC_SEQUENCE_ERROR;
    }
    if (error == 0U)
    {
        switch (cmd)
        {
            case CsecRecovery::CMD_LOAD_KEY:
            {
                uint8_t m2[32];
                get(8U, m2, 32U);
                if (std::memcmp(m2, TEST_M2, 16U) != 0)
                {
                    model.keyFlagSeen = true; // what a key flag would look like to the CSEc
                }
                uint8_t m1[16];
                get(4U, m1, 16U);
                if ((keyId != CsecRecovery::MASTER_ECU_KEY) || ((m1[15] >> 4U) != keyId))
                {
                    error = CsecRecovery::ERC_KEY_INVALID;
                }
                else if (model.masterLoaded)
                {
                    error = CsecRecovery::ERC_KEY_UPDATE_ERROR; // authorising value is no longer all ones
                }
                else
                {
                    model.masterLoaded = true;
                    put(20U, TEST_M4, 32U);
                    put(28U, TEST_M5, 16U);
                }
                break;
            }
            case CsecRecovery::CMD_INIT_RNG: fakeFtfc.FCSESTAT |= FTFC_FCSESTAT_RIN_MASK; break;
            case CsecRecovery::CMD_GET_ID:
            {
                uint8_t page[16] = {};
                std::memcpy(page, TEST_UID, 15U);
                page[15] = 0x20U; // SREG: RIN
                put(8U, page, 16U);
                break;
            }
            case CsecRecovery::CMD_DBG_CHAL:
                if ((fakeFtfc.FCSESTAT & FTFC_FCSESTAT_RIN_MASK) == 0U)
                {
                    error = CsecRecovery::ERC_RNG_SEED;
                }
                else
                {
                    put(4U, TEST_CHALLENGE, 16U);
                    model.challengeIssued = true;
                }
                break;
            case CsecRecovery::CMD_DBG_AUTH:
            {
                uint8_t auth[16];
                get(4U, auth, 16U);
                if (!afterChallenge)
                {
                    error = CsecRecovery::ERC_SEQUENCE_ERROR;
                }
                else if (std::memcmp(auth, TEST_AUTH, 16U) != 0)
                {
                    error = CsecRecovery::ERC_NO_DEBUGGING;
                }
                else
                {
                    model.keysErased   = true;
                    model.masterLoaded = false;
                }
                break;
            }
            default: error = CsecRecovery::ERC_GENERAL_ERROR; break;
        }
    }
    model.pram[1] = static_cast<uint32_t>((error == 0U) ? CsecRecovery::ERC_NO_ERROR : error) << 16U;
}

// ------------------------------------------------------------------------------ MCU watchdog model (NVM-17)
constexpr uint32_t LPO_TICKS_PER_MS   = 128U;       // LPO 128 kHz
constexpr uint32_t LPO_TICKS_PER_POLL = 8U;         // one FSTAT poll = 8 LPO ticks (62.5 us)
constexpr uint32_t TIMEOUT_TICKS    = 250U * 128U;  // the demo's 250 ms (safeLifecycle watchdog_timeout_ms)
constexpr uint64_t HANG_LIMIT_TICKS = 60000U * 128U; // a model run longer than 60 s: the budget does not end

struct WatchdogReset
{
};

struct Hang
{
};

struct Watchdog
{
    uint64_t now          = 0U; // LPO ticks
    uint64_t lastRefresh  = 0U;
    uint32_t refreshes    = 0U;
    bool fired            = false;
    uint64_t firedAt      = 0U;
    bool earlyRefresh     = false;
    uint8_t commandAtFire = 0U;
} wd;

uint8_t runningCommand = 0U;

uint32_t counter()
{
    uint64_t const lpo = wd.now - wd.lastRefresh;
    return static_cast<uint32_t>(((fakeWdog.CS & WDOG_CS_PRES_MASK) != 0U) ? (lpo >> 8U) : lpo);
}

bool enabled() { return (fakeWdog.CS & WDOG_CS_EN_MASK) != 0U; }

void fire()
{
    wd.fired         = true;
    wd.firedAt       = wd.now;
    wd.commandAtFire = runningCommand;
    throw WatchdogReset{};
}

void advance()
{
    wd.now += LPO_TICKS_PER_POLL;
    if (enabled() && (counter() > (fakeWdog.TOVAL & 0xFFFFU)))
    {
        fire();
    }
    if (wd.now > HANG_LIMIT_TICKS)
    {
        throw Hang{};
    }
}

uint32_t wdogRead() { return enabled() ? (counter() & 0xFFFFU) : 0U; }

void wdogWrite(uint32_t const value)
{
    if (value != FEATURE_WDOG_TRIGGER_VALUE)
    {
        return;
    }
    if (enabled() && ((fakeWdog.CS & WDOG_CS_WIN_MASK) != 0U) && (counter() <= (fakeWdog.WIN & 0xFFFFU)))
    {
        wd.earlyRefresh = true; // refresh inside the closed window: reset
        fire();
    }
    wd.lastRefresh = wd.now;
    ++wd.refreshes;
}

uint8_t fstat(FakeReg8 const&)
{
    advance();
    if (model.hang && (model.count > model.hangFrom))
    {
        return 0U; // the command launched after hangNext() never completes
    }
    if (model.pollsLeft > 0U)
    {
        --model.pollsLeft;
        return 0U; // CCIF 0: command running
    }
    runningCommand = 0U;
    return FTFC_FSTAT_CCIF_MASK;
}

constexpr uint32_t SDID_S32K144 = 0x144F14EAU; // board 2026-09-22: sub-series 4, derivate 4, FEATURES bit 7 = CSEc
constexpr uint32_t SDID_S32K116 = 0x116F12C1U | 0x80U; // made up: sub-series 1, derivate 6, 48 LQFP, with CSEc

uint8_t subseriesOf(uint32_t const sdid) { return static_cast<uint8_t>((sdid & SIM_SDID_SUBSERIES_MASK) >> 24U); }

void reset(bool const csec = true, bool const eeeReady = true, uint32_t const part = SDID_S32K144)
{
    model                = Model();
    model.base           = platform::csec::pramBaseOf(subseriesOf(part)); // NVM-21
    fakeFtfc             = FakeFtfc();
    fakeFtfc.FSTAT.onRead = &fstat;
    fakeFtfc.FCNFG       = eeeReady ? FTFC_FCNFG_EEERDY_MASK : FTFC_FCNFG_RAMRDY_MASK;
    fakeSim              = FakeSim();
    fakeSim.SDID         = csec ? part : (part & ~0x80U); // FEATURES bit 7 = CSEc, and without
    fakeCore             = FakeCore();
    // NVM-17: the watchdog as the family port runs it (OpenBSW enableWatchdog: EN, LPO, CMD32EN, no window)
    fakeWdog             = FakeWdog();
    fakeWdog.CS          = WDOG_CS_EN_MASK | (1U << WDOG_CS_CLK_SHIFT) | WDOG_CS_CMD32EN_MASK;
    fakeWdog.TOVAL       = TIMEOUT_TICKS;
    fakeWdog.CNT.onRead  = &wdogRead;
    fakeWdog.CNT.onWrite = &wdogWrite;
    fakeSmc              = FakeSmc();
    wd                   = Watchdog();
    runningCommand       = 0U;
}

bool same(uint8_t const* a, uint8_t const* b, size_t const n) { return std::memcmp(a, b, n) == 0; }

void hangNext()
{
    model.hang     = true;
    model.hangFrom = model.count;
}

uint32_t ms(uint64_t const ticks) { return static_cast<uint32_t>(ticks / LPO_TICKS_PER_MS); }

uint32_t pollsMs(uint32_t const msLong) { return (msLong * LPO_TICKS_PER_MS) / LPO_TICKS_PER_POLL; }

} // namespace

namespace platform
{
namespace csec
{
void pramWrite(uintptr_t const base, uint32_t const word, uint32_t const value)
{
    if ((base != model.base) || (model.base == 0U))
    {
        ++model.foreignWrites; // NVM-21: FlexRAM or nothing - no command starts
        return;
    }
    ++model.writes;
    ++model.sequence;
    if (word >= 32U)
    {
        return;
    }
    model.pram[word] = value;
    if (word != 0U)
    {
        model.lastDataWrite = static_cast<int32_t>(model.sequence);
        return;
    }
    Launch& l      = model.launches[(model.count < 8U) ? model.count : 7U];
    l.header       = value;
    l.interruptsOn = fakeCore.interruptsOn;
    l.cacheOn      = fakeCore.cacheOn;
    std::memcpy(l.words, model.pram, sizeof(l.words));
    ++model.count;
    execute(value);
    uint8_t const cmd = static_cast<uint8_t>(value >> 24U);
    runningCommand    = cmd;
    model.pollsLeft   = ((cmd < 0x20U) && (model.pollsFor[cmd] != 0U)) ? model.pollsFor[cmd] : model.busyPolls;
}

uint32_t pramRead(uintptr_t const base, uint32_t const word)
{
    if ((base != model.base) || (model.base == 0U))
    {
        ++model.foreignReads;
        return 0xFFFFFFFFU; // erased EEPROM
    }
    if (model.pollsLeft > 0U)
    {
        model.readWhileBusy = true;
    }
    return (word < 32U) ? model.pram[word] : 0U;
}
} // namespace csec
} // namespace platform

namespace
{
uint32_t word(uint8_t const* b) // big-endian word of four message bytes
{
    return (static_cast<uint32_t>(b[0]) << 24U) | (static_cast<uint32_t>(b[1]) << 16U) | (static_cast<uint32_t>(b[2]) << 8U) | b[3];
}

using M1 = uint8_t[16];
using M2 = uint8_t[32];

void testNothingWithoutConfirmation()
{
    reset();
    CsecRecovery r;
    CsecRecovery::LoadKeyAnswer answer{};
    char const* const wrong[] = {nullptr, "", "LOADKEY-MASTER", "LOADKEY-MASTER-ECU-KEY ", "loadkey-master-ecu-key",
                                 "LOADKEY-MASTER-ECU-KEYS", "DBGAUTH-C0FFEE00"};
    for (char const* w : wrong)
    {
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, w, answer) == Result::WRONG_CONFIRMATION);
    }
    CHECK(model.writes == 0U);
    CHECK(fakeFtfc.FSTAT.reads == 0U);
    CHECK(r.lastCommand() == 0U);
}

void testOnlyTheFirstMasterKeyLoad()
{
    reset();
    CsecRecovery r;
    CsecRecovery::LoadKeyAnswer answer{};
    M1 m1;
    std::memcpy(m1, TEST_M1, 16U);
    uint8_t const others[] = {0x41U, 0x14U, 0x12U, 0x21U, 0x10U, 0x01U, 0x00U, 0xFFU};
    for (uint8_t const ids : others) // KEY_1 by MASTER, MASTER by KEY_1, BOOT_MAC_KEY, ...
    {
        m1[15] = ids;
        CHECK(r.loadMasterKey(m1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::NOT_FIRST_LOAD);
    }
    CHECK(model.writes == 0U);
}

void testNoKeyFlagEverPasses()
{
    reset();
    CsecRecovery r;
    CsecRecovery::LoadKeyAnswer answer{};
    // WRITE_PROTECTION: refused before anything is written
    CHECK(r.loadMasterKey(TEST_M1, TEST_M2_WRITE_PROTECTED, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
          == Result::FLAGS_REFUSED);
    // any other bit of the first block (counter, other flags, zero fill): refused as well
    M2 m2;
    unsigned refused = 0U;
    for (unsigned bit = 0U; bit < 128U; ++bit)
    {
        std::memcpy(m2, TEST_M2, 32U);
        m2[bit / 8U] = static_cast<uint8_t>(m2[bit / 8U] ^ (1U << (bit % 8U)));
        if (r.loadMasterKey(TEST_M1, m2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
            == Result::FLAGS_REFUSED)
        {
            ++refused;
        }
    }
    CHECK(refused == 128U);
    CHECK(model.writes == 0U);
    CHECK(!model.keyFlagSeen);
    CHECK(same(CsecRecovery::firstLoadM2Block0(), TEST_M2, 16U));
    // the second block (the key itself) is the host's business: a changed key passes the guard
    std::memcpy(m2, TEST_M2, 32U);
    m2[31] = static_cast<uint8_t>(m2[31] ^ 1U);
    CHECK(r.loadMasterKey(TEST_M1, m2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer) == Result::DONE);
    CHECK(!model.keyFlagSeen);
}

void testChipPreconditions()
{
    CsecRecovery::LoadKeyAnswer answer{};
    CsecRecovery::Challenge challenge{};
    reset(false, true);
    {
        CsecRecovery r;
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::NO_CSEC);
        CHECK(r.challenge(challenge) == Result::NO_CSEC);
    }
    reset(true, false);
    {
        CsecRecovery r;
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::NOT_PARTITIONED);
        CHECK(r.challenge(challenge) == Result::NOT_PARTITIONED);
    }
    for (uint32_t const mode : {0x80U, 0x04U}) // NVM-17: HSRUN, VLPR - no CSEc command there (data sheet)
    {
        reset();
        fakeSmc.PMSTAT = mode;
        CsecRecovery r;
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::NOT_RUN_MODE);
        CHECK(r.initRng() == Result::NOT_RUN_MODE);
        CHECK(r.challenge(challenge) == Result::NOT_RUN_MODE);
        CHECK(model.writes == 0U);
    }
    reset();
    model.pollsLeft = 1000000U; // a command runs
    {
        CsecRecovery r;
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::BUSY);
        CHECK(r.challenge(challenge) == Result::BUSY);
    }
    CHECK(model.writes == 0U);
}

void testLoadMasterKey()
{
    reset();
    CsecRecovery r;
    CsecRecovery::LoadKeyAnswer answer{};
    CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer) == Result::DONE);
    CHECK(model.count == 1U);
    Launch const& l = model.launches[0];
    CHECK(l.header == 0x07000001U); // LOAD_KEY, copy, first call, MASTER_ECU_KEY
    for (uint32_t i = 0U; i < 4U; ++i)
    {
        CHECK(l.words[4U + i] == word(&TEST_M1[4U * i]));
        CHECK(l.words[16U + i] == word(&TEST_M3[4U * i]));
    }
    for (uint32_t i = 0U; i < 8U; ++i)
    {
        CHECK(l.words[8U + i] == word(&TEST_M2[4U * i]));
    }
    CHECK(model.writes == 17U); // 16 data words, then the header once
    CHECK((model.foreignWrites == 0U) && (model.foreignReads == 0U));
    CHECK(model.lastDataWrite == 16);
    CHECK(!l.interruptsOn);
    CHECK(!l.cacheOn);
    CHECK(fakeCore.interruptsOn);
    CHECK(fakeCore.cacheOn);
    CHECK(fakeCore.suspends == 1U);
    CHECK(fakeCore.resumes == 1U);
    CHECK(fakeFtfc.FSTAT.reads >= 4U); // idle check, 3 polls busy, 1 done
    CHECK(!model.readWhileBusy);
    CHECK(same(answer.m4, TEST_M4, 32U));
    CHECK(same(answer.m5, TEST_M5, 16U));
    CHECK(r.errorBits() == CsecRecovery::ERC_NO_ERROR);
    CHECK(r.lastCommand() == CsecRecovery::CMD_LOAD_KEY);
    CHECK(!model.keyFlagSeen);
    // a second load: the slot is no longer empty, the CSEc refuses; the answer stays untouched
    CsecRecovery::LoadKeyAnswer second{};
    std::memset(&second, 0xA5, sizeof(second));
    CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, second)
          == Result::CSEC_ERROR);
    CHECK(r.errorBits() == CsecRecovery::ERC_KEY_UPDATE_ERROR);
    CHECK(std::strstr(CsecRecovery::errorText(r.errorBits()), "KEY_UPDATE") != nullptr);
    CHECK((second.m4[0] == 0xA5U) && (second.m5[15] == 0xA5U));
}

void testChallengeAndAuthorize()
{
    reset();
    CsecRecovery r;
    CsecRecovery::Challenge c{};
    uint8_t const wrongAuth[16] = {};
    CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::NO_CHALLENGE); // no challenge yet
    CHECK(model.writes == 0U);
    CHECK(std::strcmp(r.authConfirmation(), "") == 0);
    // NVM-17: no INIT_RNG inside the challenge - refused while RIN is clear, nothing written
    CHECK(r.challenge(c) == Result::RNG_NOT_READY);
    CHECK(model.writes == 0U);
    CHECK(r.initRng() == Result::DONE);
    CHECK(model.count == 1U);
    CHECK(model.launches[0].header == 0x0A000000U); // INIT_RNG
    CHECK(r.initRng() == Result::DONE);             // RIN set now: no second INIT_RNG
    CHECK(model.count == 1U);
    CHECK(r.challenge(c) == Result::DONE);
    CHECK(model.count == 3U);
    CHECK(model.launches[1].header == 0x10000000U); // GET_ID
    CHECK(model.launches[2].header == 0x12000000U); // DBG_CHAL last: DBG_AUTH must follow directly
    CHECK(same(c.uid, TEST_UID, 15U));
    CHECK(c.sreg == 0x20U);
    CHECK(same(c.challenge, TEST_CHALLENGE, 16U));
    CHECK(std::strcmp(r.authConfirmation(), "DBGAUTH-C0FFEE00") == 0);
    CHECK(!model.readWhileBusy);
    // wrong words: nothing written
    uint32_t const before = model.writes;
    char const* const wrong[] = {nullptr, "", "DBGAUTH-", "DBGAUTH-C0FFEE0", "DBGAUTH-c0ffee00", "DBGAUTH-C0FFEE01",
                                 "LOADKEY-MASTER-ECU-KEY"};
    for (char const* w : wrong)
    {
        CHECK(r.authorize(TEST_AUTH, w) == Result::WRONG_CONFIRMATION);
    }
    CHECK(model.writes == before);
    // the right word: DBG_AUTH with the authorisation in page 1
    CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::DONE);
    CHECK(model.count == 4U);
    Launch const& l = model.launches[3];
    CHECK(l.header == 0x13000000U);
    for (uint32_t i = 0U; i < 4U; ++i)
    {
        CHECK(l.words[4U + i] == word(&TEST_AUTH[4U * i]));
    }
    CHECK(!l.interruptsOn && !l.cacheOn);
    CHECK(model.keysErased);
    // one attempt per challenge
    CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::NO_CHALLENGE);
    CHECK(model.count == 4U);
    // a new challenge: RIN is set now, no second INIT_RNG; a wrong authorisation is the CSEc's refusal
    CHECK(r.challenge(c) == Result::DONE);
    CHECK(model.count == 6U);
    CHECK(model.launches[4].header == 0x10000000U);
    CHECK(r.authorize(wrongAuth, "DBGAUTH-C0FFEE00") == Result::CSEC_ERROR);
    CHECK(r.errorBits() == CsecRecovery::ERC_NO_DEBUGGING);
}

void testCommandBetweenVoidsChallenge()
{
    reset();
    CsecRecovery r;
    CsecRecovery::Challenge c{};
    CsecRecovery::LoadKeyAnswer answer{};
    CHECK(r.initRng() == Result::DONE);
    CHECK(r.challenge(c) == Result::DONE);
    CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer) == Result::DONE);
    uint32_t const count = model.count;
    CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::NO_CHALLENGE);
    CHECK(model.count == count);
    CHECK(!model.keysErased);
}

void testErrorsOfTheSequence()
{
    CsecRecovery::Challenge c{};
    reset();
    model.scriptedError[CsecRecovery::CMD_INIT_RNG] = CsecRecovery::ERC_MEMORY_FAILURE;
    {
        CsecRecovery r;
        CHECK(r.initRng() == Result::CSEC_ERROR);
        CHECK(model.count == 1U);
        CHECK(r.errorBits() == CsecRecovery::ERC_MEMORY_FAILURE);
        CHECK(r.challenge(c) == Result::RNG_NOT_READY); // RIN still clear
        CHECK(model.count == 1U);
        CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::NO_CHALLENGE);
    }
    reset();
    model.scriptedError[CsecRecovery::CMD_GET_ID] = CsecRecovery::ERC_KEY_NOT_AVAILABLE;
    {
        CsecRecovery r;
        CHECK(r.initRng() == Result::DONE);
        CHECK(r.challenge(c) == Result::CSEC_ERROR);
        CHECK(model.count == 2U); // INIT_RNG, GET_ID - no DBG_CHAL after a failed GET_ID
        CHECK(r.lastCommand() == CsecRecovery::CMD_GET_ID);
        CHECK(r.errorBits() == CsecRecovery::ERC_KEY_NOT_AVAILABLE);
    }
    reset();
    model.scriptedError[CsecRecovery::CMD_DBG_CHAL] = CsecRecovery::ERC_KEY_WRITE_PROTECTED;
    {
        CsecRecovery r;
        CHECK(r.initRng() == Result::DONE);
        CHECK(r.challenge(c) == Result::CSEC_ERROR);
        CHECK(model.count == 3U);
        CHECK(std::strstr(CsecRecovery::errorText(r.errorBits()), "no way back") != nullptr);
        CHECK(std::strcmp(r.authConfirmation(), "") == 0);
        CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::NO_CHALLENGE);
    }
    reset();
    model.busyPolls = 6000U;  // long commands: waited for, results read after them
    {
        CsecRecovery r;
        CHECK(r.initRng() == Result::DONE);
        CHECK(r.challenge(c) == Result::DONE);
        CHECK(!model.readWhileBusy);
        CHECK(fakeFtfc.FSTAT.reads >= 18000U);
        CHECK(!wd.fired);
    }
    std::memset(model.scriptedError, 0, sizeof(model.scriptedError));
}

// NVM-17: one DBG_AUTH of \p msLong after a challenge; returns whether the watchdog fired
bool authorizeLasting(CsecRecovery& r, uint32_t const msLong)
{
    CsecRecovery::Challenge c{};
    CHECK(r.initRng() == Result::DONE);
    CHECK(r.challenge(c) == Result::DONE);
    model.pollsFor[CsecRecovery::CMD_DBG_AUTH] = pollsMs(msLong);
    try
    {
        CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::DONE);
    }
    catch (WatchdogReset const&)
    {
        return true;
    }
    return false;
}

void testLongCommandsKeepTheWatchdogQuiet()
{
    // INIT_RNG of 600 ms ("several hundred ms") with the demo's 250 ms watchdog: no reset, refreshed, measured - the
    // counter already at 100 ms when the command starts (the last service of the safety task is that old)
    reset();
    wd.now = 100U * LPO_TICKS_PER_MS;
    {
        CsecRecovery r;
        model.pollsFor[CsecRecovery::CMD_INIT_RNG] = pollsMs(600U);
        bool fired = false;
        try
        {
            CHECK(r.initRng() == Result::DONE);
        }
        catch (WatchdogReset const&)
        {
            fired = true;
        }
        CHECK(!fired);
        CHECK(r.lastWatchdogRefreshes() >= 4U); // 600 ms / 125 ms
        CHECK(r.lastWatchdogRefreshes() == wd.refreshes);
        CHECK((r.lastDurationMs() >= 599U) && (r.lastDurationMs() <= 601U));
        CHECK(fakeCore.interruptsOn);
    }
    // DBG_AUTH: 550 ms (FlexNVM block erase of a 64 KB part, data sheet maximum), 5 s, 19 s - no reset during it
    for (uint32_t const msLong : {550U, 5000U, 19000U})
    {
        reset();
        CsecRecovery r;
        CHECK(!authorizeLasting(r, msLong));
        CHECK(model.keysErased);
        CHECK((r.lastDurationMs() + 1U >= msLong) && (r.lastDurationMs() <= msLong + 1U));
    }
    // short commands: no refresh needed, none done (the watchdog is not served more than necessary)
    reset();
    {
        CsecRecovery r;
        CsecRecovery::LoadKeyAnswer answer{};
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer) == Result::DONE);
        CHECK(r.lastWatchdogRefreshes() == 0U);
        CHECK(wd.refreshes == 0U);
        CHECK(r.lastDurationMs() == 0U); // 3 ticks
    }
}

void testHangingCommandEndsInTheWatchdogReset()
{
    struct Case
    {
        uint8_t command;
        uint32_t budgetMs;
    };
    for (Case const k : {Case{CsecRecovery::CMD_INIT_RNG, CsecRecovery::BUDGET_INIT_RNG_MS},
                         Case{CsecRecovery::CMD_DBG_AUTH, CsecRecovery::BUDGET_DBG_AUTH_MS},
                         Case{CsecRecovery::CMD_LOAD_KEY, CsecRecovery::BUDGET_OTHER_MS}})
    {
        reset();
        CsecRecovery r;
        CsecRecovery::Challenge c{};
        CsecRecovery::LoadKeyAnswer answer{};
        bool fired     = false;
        uint64_t start = 0U;
        try
        {
            if (k.command == CsecRecovery::CMD_DBG_AUTH)
            {
                CHECK(r.initRng() == Result::DONE);
                CHECK(r.challenge(c) == Result::DONE);
            }
            hangNext();
            start = wd.now;
            if (k.command == CsecRecovery::CMD_INIT_RNG)
            {
                (void)r.initRng();
            }
            else if (k.command == CsecRecovery::CMD_DBG_AUTH)
            {
                (void)r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00");
            }
            else
            {
                (void)r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer);
            }
        }
        catch (WatchdogReset const&)
        {
            fired = true;
        }
        catch (Hang const&)
        {
        }
        CHECK(fired);
        CHECK(wd.commandAtFire == k.command);
        CHECK(!wd.earlyRefresh);
        // not before the budget, and at most one refresh interval plus one time-out after it
        uint32_t const after = ms(wd.firedAt - start);
        CHECK(after >= k.budgetMs);
        CHECK(after <= (k.budgetMs + 125U + 250U + 1U));
    }
}

void testWatchdogConfigurations()
{
    CsecRecovery::LoadKeyAnswer answer{};
    // off: nothing to keep quiet, no refresh, no time base
    reset();
    fakeWdog.CS = 0U;
    {
        CsecRecovery r;
        model.pollsFor[CsecRecovery::CMD_INIT_RNG] = pollsMs(600U);
        CHECK(r.initRng() == Result::DONE);
        CHECK(wd.refreshes == 0U);
        CHECK(r.lastDurationMs() == CsecRecovery::DURATION_UNKNOWN);
    }
    // another clock (unknown time base) or no 32-bit refresh: refused before anything is written
    for (uint32_t const cs : {WDOG_CS_EN_MASK | WDOG_CS_CMD32EN_MASK,
                              WDOG_CS_EN_MASK | (2U << WDOG_CS_CLK_SHIFT) | WDOG_CS_CMD32EN_MASK,
                              WDOG_CS_EN_MASK | (1U << WDOG_CS_CLK_SHIFT)})
    {
        reset();
        fakeWdog.CS = cs;
        CsecRecovery r;
        CHECK(r.initRng() == Result::WATCHDOG);
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::WATCHDOG);
        CHECK(model.writes == 0U);
    }
    // window mode: refreshed only after the window (the model resets on an early refresh)
    reset();
    fakeWdog.CS |= WDOG_CS_WIN_MASK;
    fakeWdog.WIN = 200U * LPO_TICKS_PER_MS; // window 200 ms of 250 ms, beyond half the time-out
    {
        CsecRecovery r;
        model.pollsFor[CsecRecovery::CMD_INIT_RNG] = pollsMs(900U);
        bool fired = false;
        try
        {
            CHECK(r.initRng() == Result::DONE);
        }
        catch (WatchdogReset const&)
        {
            fired = true;
        }
        CHECK(!fired);
        CHECK(!wd.earlyRefresh);
        CHECK(wd.refreshes >= 4U);
    }
    // a window that leaves no room: refused
    reset();
    fakeWdog.CS |= WDOG_CS_WIN_MASK;
    fakeWdog.WIN = TIMEOUT_TICKS;
    {
        CsecRecovery r;
        CHECK(r.initRng() == Result::WATCHDOG);
        CHECK(model.writes == 0U);
    }
    // prescaler 256: 500 ticks per second, time-out 125 ticks = 250 ms
    reset();
    fakeWdog.CS |= WDOG_CS_PRES_MASK;
    fakeWdog.TOVAL = 125U;
    {
        CsecRecovery r;
        CsecRecovery::WatchdogPlan plan{};
        CHECK(CsecRecovery::planWatchdog(CsecRecovery::CMD_INIT_RNG, plan));
        CHECK(plan.ticksPerSecond == 500U);
        CHECK(plan.refreshAt == 62U);
        model.pollsFor[CsecRecovery::CMD_INIT_RNG] = pollsMs(1000U);
        bool fired = false;
        try
        {
            CHECK(r.initRng() == Result::DONE);
        }
        catch (WatchdogReset const&)
        {
            fired = true;
        }
        CHECK(!fired);
        CHECK((r.lastDurationMs() >= 990U) && (r.lastDurationMs() <= 1010U)); // 2 ms per tick
    }
    // budgets as documented
    CHECK(CsecRecovery::budgetMs(CsecRecovery::CMD_INIT_RNG) == 2000U);
    CHECK(CsecRecovery::budgetMs(CsecRecovery::CMD_DBG_AUTH) == 20000U);
    CHECK(CsecRecovery::budgetMs(CsecRecovery::CMD_LOAD_KEY) == 1000U);
    CHECK(CsecRecovery::budgetMs(CsecRecovery::CMD_GET_ID) == 1000U);
    CHECK(CsecRecovery::budgetMs(CsecRecovery::CMD_DBG_CHAL) == 1000U);
}

CsecRecovery::Journal volatile journalRecord;
uint8_t journalStateAtResume = 0U;

void recordJournalAtResume() { journalStateAtResume = journalRecord.state(); }

void testJournal()
{
    reset();
    std::memset(const_cast<CsecRecovery::Journal*>(&journalRecord), 0x5A, sizeof(journalRecord));
    CHECK(!journalRecord.valid()); // random content after power-on
    fakeCore.onResume = &recordJournalAtResume;
    {
        CsecRecovery r;
        r.setJournal(&journalRecord);
        CsecRecovery::LoadKeyAnswer answer{};
        model.pollsFor[CsecRecovery::CMD_LOAD_KEY] = pollsMs(7U);
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer) == Result::DONE);
        CHECK(journalRecord.valid());
        CHECK(journalRecord.command() == CsecRecovery::CMD_LOAD_KEY);
        CHECK(journalRecord.state() == CsecRecovery::Journal::STATE_DONE);
        CHECK(journalRecord.errorBits() == CsecRecovery::ERC_NO_ERROR);
        CHECK(journalRecord.durationMs == 7U);
        // written before the interrupts come back (the safety task may reset right after a long command)
        CHECK(journalStateAtResume == CsecRecovery::Journal::STATE_DONE);
        // an error is recorded as well
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::CSEC_ERROR);
        CHECK(journalRecord.errorBits() == CsecRecovery::ERC_KEY_UPDATE_ERROR);
        // nothing launched, nothing journaled
        uint32_t const word = journalRecord.word;
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, "no", answer) == Result::WRONG_CONFIRMATION);
        CHECK(journalRecord.word == word);
    }
    // a command interrupted by the watchdog reset: "running" survives in the journal
    reset();
    {
        CsecRecovery r;
        r.setJournal(&journalRecord);
        hangNext();
        try
        {
            (void)r.initRng();
        }
        catch (WatchdogReset const&)
        {
        }
        CHECK(wd.fired);
        CHECK(journalRecord.valid());
        CHECK(journalRecord.command() == CsecRecovery::CMD_INIT_RNG);
        CHECK(journalRecord.state() == CsecRecovery::Journal::STATE_RUNNING);
        CHECK(journalRecord.durationMs == CsecRecovery::DURATION_UNKNOWN);
    }
    // any changed word invalidates it
    const_cast<CsecRecovery::Journal&>(journalRecord).durationMs ^= 1U;
    CHECK(!journalRecord.valid());
}

void testCallSeqIsFirstCallEverywhere()
{
    // [RM13] Figure 36-42 shows CallSeq 0x01 for LOAD_KEY; the device writes 0x00 (README.md): every header here
    reset();
    CsecRecovery r;
    CsecRecovery::LoadKeyAnswer answer{};
    CsecRecovery::Challenge c{};
    CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer) == Result::DONE);
    CHECK(r.initRng() == Result::DONE);
    CHECK(r.challenge(c) == Result::DONE);
    CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::DONE);
    CHECK(model.count == 5U);
    for (uint32_t i = 0U; i < model.count; ++i)
    {
        CHECK(((model.launches[i].header >> 8U) & 0xFFU) == 0U);  // CallSeq: first call
        CHECK(((model.launches[i].header >> 16U) & 0xFFU) == 0U); // FuncFormat: copy
    }
}

void testHexFields()
{
    using platform::csechex::format;
    using platform::csechex::parse;
    uint8_t out[16] = {};
    char const* const text = "000102030405060708090a0B0c0D0e0F";
    CHECK(parse(text, 32U, out, 16U));
    for (uint32_t i = 0U; i < 16U; ++i)
    {
        CHECK(out[i] == i);
    }
    char back[33] = {};
    format(out, 16U, back);
    CHECK(std::strcmp(back, "000102030405060708090A0B0C0D0E0F") == 0);
    uint8_t untouched[16];
    std::memset(untouched, 0xA5, sizeof(untouched));
    CHECK(!parse(text, 31U, untouched, 16U));                              // one digit short
    CHECK(!parse("000102030405060708090a0B0c0D0e0F0", 33U, untouched, 16U)); // one digit too many
    CHECK(!parse("000102030405060708090a0B0c0D0e0G", 32U, untouched, 16U)); // not hex
    CHECK(!parse("0x0102030405060708090a0B0c0D0e0F", 32U, untouched, 16U)); // prefix
    CHECK(!parse(nullptr, 32U, untouched, 16U));
    CHECK((untouched[0] == 0xA5U) && (untouched[15] == 0xA5U));
    uint8_t m2[32]  = {};
    char m2Text[65] = {};
    format(TEST_M2, 32U, m2Text);
    CHECK(parse(m2Text, 64U, m2, 32U));
    CHECK(same(m2, TEST_M2, 32U));
}

// NVM-21: the CSE_PRAM address follows the sub-series of SIM SDID; every access of every command goes there
void runAllCommands(CsecRecovery& r)
{
    CsecRecovery::LoadKeyAnswer answer{};
    CsecRecovery::Challenge c{};
    CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer) == Result::DONE);
    CHECK(same(answer.m4, TEST_M4, 32U));
    CHECK(r.initRng() == Result::DONE);
    CHECK(r.challenge(c) == Result::DONE);
    CHECK(same(c.challenge, TEST_CHALLENGE, 16U));
    CHECK(r.authorize(TEST_AUTH, "DBGAUTH-C0FFEE00") == Result::DONE);
}

void testPramAddressOfTheSubSeries()
{
    CHECK(platform::csec::pramBaseOf(4U) == 0x14001000U); // S32K14x(W): behind 4 KB FlexRAM ([MAP])
    CHECK(platform::csec::pramBaseOf(1U) == 0x14000800U); // S32K11x: behind 2 KB FlexRAM
    for (uint32_t const part : {SDID_S32K144, SDID_S32K116})
    {
        reset(true, true, part);
        CHECK(CsecRecovery::pramAddress() == model.base);
        CsecRecovery r;
        runAllCommands(r);
        CHECK(model.count == 5U);
        CHECK(model.keysErased);
        CHECK((model.foreignWrites == 0U) && (model.foreignReads == 0U));
    }
    // board 2026-09-30: on the S32K144, 0x1400_0800 is the emulated EEPROM, not the parameter RAM
    reset();
    CHECK(model.base == 0x14001000U);
    CHECK(CsecRecovery::pramAddress() != 0x14000800U);
    // a sub-series the table does not know: refused before anything is read or written, whatever the command
    for (uint32_t const subseries : {0U, 2U, 3U, 5U, 0xFU})
    {
        uint32_t const part = (SDID_S32K144 & ~SIM_SDID_SUBSERIES_MASK) | (subseries << 24U);
        reset(true, true, part);
        CHECK(CsecRecovery::pramAddress() == 0U);
        CsecRecovery r;
        CsecRecovery::LoadKeyAnswer answer{};
        CsecRecovery::Challenge c{};
        CHECK(r.loadMasterKey(TEST_M1, TEST_M2, TEST_M3, CsecRecovery::LOAD_KEY_CONFIRMATION, answer)
              == Result::UNKNOWN_PART);
        CHECK(r.initRng() == Result::UNKNOWN_PART);
        CHECK(r.challenge(c) == Result::UNKNOWN_PART);
        CHECK((model.writes == 0U) && (model.foreignWrites == 0U) && (model.foreignReads == 0U));
        CHECK(fakeFtfc.FSTAT.reads == 0U);
        CHECK(r.lastCommand() == 0U);
    }
    // no CSEc is still reported as such (checked first)
    reset(false, true, SDID_S32K116);
    {
        CsecRecovery r;
        CHECK(r.initRng() == Result::NO_CSEC);
    }
    CHECK(std::strstr(CsecRecovery::resultText(Result::UNKNOWN_PART), "nothing written") != nullptr);
}

void testTexts()
{
    CHECK(std::strstr(CsecRecovery::resultText(Result::FLAGS_REFUSED), "WRITE_PROTECTION") != nullptr);
    CHECK(std::strstr(CsecRecovery::resultText(Result::WRONG_CONFIRMATION), "nothing written") != nullptr);
    CHECK(std::strcmp(CsecRecovery::errorText(CsecRecovery::ERC_NO_ERROR), "ERC_NO_ERROR") == 0);
    CHECK(std::strcmp(CsecRecovery::errorText(CsecRecovery::ERC_SEQUENCE_ERROR | CsecRecovery::ERC_NO_DEBUGGING),
                      "ERC_SEQUENCE_ERROR")
          == 0);
    CHECK(std::strcmp(CsecRecovery::LOAD_KEY_CONFIRMATION, "LOADKEY-MASTER-ECU-KEY") == 0);
    CHECK(std::strstr(CsecRecovery::resultText(Result::RNG_NOT_READY), "csec rng") != nullptr);
    CHECK(std::strstr(CsecRecovery::resultText(Result::NOT_RUN_MODE), "nothing written") != nullptr);
    CHECK(std::strstr(CsecRecovery::resultText(Result::WATCHDOG), "nothing written") != nullptr);
}

} // namespace

int main()
{
    testNothingWithoutConfirmation();
    testOnlyTheFirstMasterKeyLoad();
    testNoKeyFlagEverPasses();
    testChipPreconditions();
    testLoadMasterKey();
    testChallengeAndAuthorize();
    testCommandBetweenVoidsChallenge();
    testErrorsOfTheSequence();
    testLongCommandsKeepTheWatchdogQuiet();
    testHangingCommandEndsInTheWatchdogReset();
    testWatchdogConfigurations();
    testJournal();
    testCallSeqIsFirstCallEverywhere();
    testPramAddressOfTheSubSeries();
    testHexFields();
    testTexts();
    (void)TEST_MASTER_KEY;
    std::printf("CsecRecoveryHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
