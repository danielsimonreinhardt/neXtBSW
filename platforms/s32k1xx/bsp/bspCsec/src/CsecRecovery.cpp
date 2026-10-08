// NVM-13: the CSEc way back to the factory state, device side (see csec/CsecRecovery.h). IRREVERSIBLE steps behind a
// typed confirmation; no key flag can pass (FIRST_LOAD_M2_BLOCK0). Reachable only from a test build (NVM-07).
// NVM-17: INIT_RNG separate from the challenge (start-up), MCU watchdog kept quiet within a budget, journal.
// NVM-21: the parameter RAM at the address of the sub-series (csec/CsecPramAddress.h), chosen before anything is
// written; an unknown sub-series is refused.

#include "csec/CsecRecovery.h"

#include "cache/cache.h"
#include "csec/CsecPram.h"
#include "flexnvm/FlexNvmChip.h"
#include "interrupts/suspendResumeAllInterrupts.h"
#include "mcu/mcu.h"

namespace platform
{
namespace
{
// AES-128 of "counter 1 | flags 00000 | 95 zero bits" under K1 = KDF(all ones, KEY_UPDATE_ENC_C): the first block of
// M2 of every first MASTER_ECU_KEY load. Computed by tools/csec (first_load_m2_block0), checked by
// tools/csec/test_she.py.
constexpr uint8_t FIRST_LOAD_M2_BLOCK0[16] = {
    0x88U, 0x9BU, 0x71U, 0x64U, 0x28U, 0xBFU, 0x0FU, 0xD9U, 0x9AU, 0xBAU, 0x27U, 0xFCU, 0x1FU, 0xB1U, 0xDEU, 0x0DU};

constexpr uint8_t M1_MASTER_BY_MASTER = 0x11U; // M1 byte 15: ID 1 (MASTER_ECU_KEY) | AuthID 1 (itself)

// CSE_PRAM word indices ([RM13] 36.5.13.13/.23/.26/.27; page n starts at word 4n)
constexpr uint32_t WORD_HEADER    = 0U;
constexpr uint32_t WORD_ERROR     = 1U;
constexpr uint32_t WORD_PAGE1     = 4U;  // M1, CHALLENGE (GET_ID in, DBG_CHAL out), AUTHORIZATION
constexpr uint32_t WORD_M2        = 8U;  // pages 2-3
constexpr uint32_t WORD_UID       = 8U;  // GET_ID: UID bytes 32..46, SREG byte 47
constexpr uint32_t WORD_M3        = 16U; // page 4
constexpr uint32_t WORD_M4        = 20U; // pages 5-6
constexpr uint32_t WORD_M5        = 28U; // page 7

constexpr uint8_t FORMAT_COPY = 0x00U;
// NVM-17: CallSeq of every command here, CMD_LOAD_KEY included, is 0 ("first call"). CallSeq 1 continues a command
// whose data do not fit into the parameter RAM (the multi-page ENC/DEC/MAC figures of [RM13] 36.5.13 show 1 only on
// their continuation pages); a first call with 1 is a sequence error ([RM13] CSEc notes, [AN5401] 3.2.2). LOAD_KEY
// fits into one call (M1..M3 pages 1-4, M4/M5 pages 5-7). That its two figures (36-42/36-43) show 0x01 is taken as
// a documentation error - see README.md "CallSeq of CMD_LOAD_KEY".
constexpr uint8_t CALL_FIRST  = 0x00U;

constexpr uint32_t WDOG_COUNTER_MASK = 0xFFFFU; // CNT, TOVAL, WIN: 16-bit values
constexpr uint32_t WDOG_CLK_LPO      = 1U;      // CS CLK 01
constexpr uint32_t PMSTAT_RUN        = 0x01U;   // SMC PMSTAT: RUN ([RM13] 40.3.6)

bool sameText(char const* a, char const* b)
{
    if ((a == nullptr) || (b == nullptr))
    {
        return false;
    }
    while ((*a != '\0') && (*a == *b))
    {
        ++a;
        ++b;
    }
    return *a == *b;
}

void writeBytes(uintptr_t const pram, uint32_t const firstWord, uint8_t const* const bytes, uint32_t const count)
{
    for (uint32_t i = 0U; i < count; i += 4U)
    {
        uint32_t const word = (static_cast<uint32_t>(bytes[i]) << 24U) | (static_cast<uint32_t>(bytes[i + 1U]) << 16U)
                              | (static_cast<uint32_t>(bytes[i + 2U]) << 8U) | static_cast<uint32_t>(bytes[i + 3U]);
        csec::pramWrite(pram, firstWord + (i / 4U), word);
    }
}

void readBytes(uintptr_t const pram, uint32_t const firstWord, uint8_t* const bytes, uint32_t const count)
{
    for (uint32_t i = 0U; i < count; ++i)
    {
        uint32_t const word = csec::pramRead(pram, firstWord + (i / 4U));
        bytes[i]            = static_cast<uint8_t>(word >> (24U - (8U * (i % 4U))));
    }
}

char hexDigit(uint8_t const value)
{
    return static_cast<char>((value < 10U) ? ('0' + value) : ('A' + (value - 10U)));
}

// NVM-17: what the RAM function does with the MCU watchdog while it waits, and what it saw
struct WatchdogRun
{
    uint32_t refreshAt;     // 0: watchdog off, never touched
    uint32_t refreshesLeft; // budget
    uint32_t refreshes;     // done
    uint32_t ticks;         // counter ticks from the launch to the completion
};

// Writes the command header (which starts the command) and waits for its completion - from RAM: while the command
// runs, the flash is not available for instruction fetches (CMD_DBG_AUTH erases the FlexNVM). There is no time-out:
// leaving the loop while the command runs would fetch from flash. NVM-17: the loop keeps the MCU watchdog quiet within
// the budget of the plan - a refresh (one 32-bit write, CS CMD32EN) whenever the counter reached refreshAt (half the
// time-out, or past the window) - and adds up the counter ticks as the time base of the duration. With the budget
// used up it refreshes no more: a hanging command still ends in the watchdog reset. Nothing is called from here
// (test/test_csec.py checks the target object for branches out of the section).
#ifndef NX_CSEC_HOST_TEST
void csecLaunchFromRam(uint32_t header, uintptr_t pram, WatchdogRun& run) __attribute__((section(".launchFromRAM")));
#endif

__attribute__((aligned(32), noinline)) void
csecLaunchFromRam(uint32_t const header, uintptr_t const pram, WatchdogRun& run)
{
    uint32_t const start = WDOG->CNT & WDOG_COUNTER_MASK;
    csec::pramWrite(pram, WORD_HEADER, header);
    uint32_t ticks = 0U;
    while ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
        if (run.refreshAt != 0U)
        {
            uint32_t const count = WDOG->CNT & WDOG_COUNTER_MASK;
            if ((count >= run.refreshAt) && (run.refreshesLeft != 0U))
            {
                WDOG->CNT = FEATURE_WDOG_TRIGGER_VALUE;
                --run.refreshesLeft;
                ++run.refreshes;
                ticks += count;
            }
        }
    }
    ticks += WDOG->CNT & WDOG_COUNTER_MASK;
    run.ticks = ticks - start;
}

} // namespace

uint8_t const* CsecRecovery::firstLoadM2Block0() { return FIRST_LOAD_M2_BLOCK0; }

uintptr_t CsecRecovery::pramAddress() { return csec::pramBaseOf(FlexNvmChip::deviceId().subseries); }

uint32_t CsecRecovery::budgetMs(uint8_t const command)
{
    if (command == CMD_INIT_RNG)
    {
        return BUDGET_INIT_RNG_MS;
    }
    if (command == CMD_DBG_AUTH)
    {
        return BUDGET_DBG_AUTH_MS;
    }
    return BUDGET_OTHER_MS;
}

bool CsecRecovery::planWatchdog(uint8_t const command, WatchdogPlan& plan)
{
    plan = WatchdogPlan{0U, 0U, 0U};
    uint32_t const cs = WDOG->CS;
    if ((cs & WDOG_CS_EN_MASK) == 0U)
    {
        return true; // off: nothing to keep quiet, no time base
    }
    if ((((cs & WDOG_CS_CLK_MASK) >> WDOG_CS_CLK_SHIFT) != WDOG_CLK_LPO) || ((cs & WDOG_CS_CMD32EN_MASK) == 0U))
    {
        return false; // another clock (unknown time base) or no 32-bit refresh
    }
    uint32_t const timeout = WDOG->TOVAL & WDOG_COUNTER_MASK;
    uint32_t refreshAt     = timeout / 2U;
    if ((cs & WDOG_CS_WIN_MASK) != 0U)
    {
        uint32_t const window = (WDOG->WIN & WDOG_COUNTER_MASK) + 1U; // refresh only after the window
        if (window > refreshAt)
        {
            refreshAt = window;
        }
    }
    if ((refreshAt == 0U) || (refreshAt >= timeout))
    {
        return false;
    }
    uint32_t const ticksPerSecond = ((cs & WDOG_CS_PRES_MASK) != 0U) ? (WATCHDOG_LPO_HZ / 256U) : WATCHDOG_LPO_HZ;
    uint64_t const budgetTicks    = (static_cast<uint64_t>(budgetMs(command)) * ticksPerSecond) / 1000U;
    plan.refreshAt                = refreshAt;
    plan.refreshLimit             = static_cast<uint32_t>(budgetTicks / refreshAt) + 1U;
    plan.ticksPerSecond           = ticksPerSecond;
    return true;
}

CsecRecovery::Result CsecRecovery::ready(uint8_t const command, WatchdogPlan& plan)
{
    _pram = 0U;
    FlexNvmChip::DeviceId const id = FlexNvmChip::deviceId();
    if (!id.security)
    {
        return Result::NO_CSEC;
    }
    uintptr_t const pram = csec::pramBaseOf(id.subseries);
    if (pram == 0U)
    {
        return Result::UNKNOWN_PART; // NVM-21: no parameter RAM address for this sub-series
    }
    if (!FlexNvmChip::eeeReady())
    {
        return Result::NOT_PARTITIONED;
    }
    if ((IP_SMC->PMSTAT & SMC_PMSTAT_PMSTAT_MASK) != PMSTAT_RUN)
    {
        return Result::NOT_RUN_MODE;
    }
    if ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)
    {
        return Result::BUSY;
    }
    if (!planWatchdog(command, plan))
    {
        return Result::WATCHDOG;
    }
    _pram = pram;
    return Result::DONE;
}

void CsecRecovery::journal(uint8_t const command, uint8_t const state, uint16_t const errorBits,
                           uint32_t const durationMs)
{
    if (_journal == nullptr)
    {
        return;
    }
    uint32_t const word = (uint32_t{command} << 24U) | (uint32_t{state} << 16U) | uint32_t{errorBits};
    _journal->tag        = Journal::TAG;
    _journal->word       = word;
    _journal->durationMs = durationMs;
    _journal->check      = ~(Journal::TAG ^ word ^ durationMs);
}

CsecRecovery::Result CsecRecovery::launch(uint8_t const command, uint8_t const keyId, WatchdogPlan const& plan)
{
    uint32_t const header = (static_cast<uint32_t>(command) << 24U) | (static_cast<uint32_t>(FORMAT_COPY) << 16U)
                            | (static_cast<uint32_t>(CALL_FIRST) << 8U) | static_cast<uint32_t>(keyId);
    _lastCommand = command;
    WatchdogRun run{plan.refreshAt, plan.refreshLimit, 0U, 0U};
    journal(command, Journal::STATE_RUNNING, 0U, DURATION_UNKNOWN);
    cacheInvalidate(0U, 0U);
    cacheDisable();
    uint32_t const oldInterruptStatus = getMachineStateRegisterValueAndSuspendAllInterrupts();
    csecLaunchFromRam(header, _pram, run);
    cacheEnable();
    // results before the interrupts come back: the safety task may reset the ECU right after a long command
    _errorBits  = static_cast<uint16_t>(csec::pramRead(_pram, WORD_ERROR) >> 16U);
    _refreshes  = run.refreshes;
    _durationMs = (plan.ticksPerSecond == 0U)
                      ? DURATION_UNKNOWN
                      : static_cast<uint32_t>((static_cast<uint64_t>(run.ticks) * 1000U) / plan.ticksPerSecond);
    journal(command, Journal::STATE_DONE, _errorBits, _durationMs);
    resumeAllInterrupts(oldInterruptStatus);
    return (_errorBits == ERC_NO_ERROR) ? Result::DONE : Result::CSEC_ERROR;
}

CsecRecovery::Result CsecRecovery::loadMasterKey(
    uint8_t const (&m1)[16],
    uint8_t const (&m2)[32],
    uint8_t const (&m3)[16],
    char const* const confirmation,
    LoadKeyAnswer& answer)
{
    if (!sameText(confirmation, LOAD_KEY_CONFIRMATION))
    {
        return Result::WRONG_CONFIRMATION;
    }
    if (m1[15] != M1_MASTER_BY_MASTER)
    {
        return Result::NOT_FIRST_LOAD;
    }
    // no key flag - WRITE_PROTECTION above all - can reach the CSEc: the flags are in this block only
    for (uint32_t i = 0U; i < 16U; ++i)
    {
        if (m2[i] != FIRST_LOAD_M2_BLOCK0[i])
        {
            return Result::FLAGS_REFUSED;
        }
    }
    WatchdogPlan plan{};
    Result const state = ready(CMD_LOAD_KEY, plan);
    if (state != Result::DONE)
    {
        return state;
    }
    _challengeValid = false; // any command between DBG_CHAL and DBG_AUTH voids the challenge ([RM13] 36.5.13.26)
    _authWord[0]    = '\0';
    writeBytes(_pram, WORD_PAGE1, m1, 16U);
    writeBytes(_pram, WORD_M2, m2, 32U);
    writeBytes(_pram, WORD_M3, m3, 16U);
    Result const result = launch(CMD_LOAD_KEY, MASTER_ECU_KEY, plan);
    if (result == Result::DONE)
    {
        readBytes(_pram, WORD_M4, answer.m4, 32U);
        readBytes(_pram, WORD_M5, answer.m5, 16U);
    }
    return result;
}

CsecRecovery::Result CsecRecovery::initRng()
{
    WatchdogPlan plan{};
    Result const state = ready(CMD_INIT_RNG, plan);
    if (state != Result::DONE)
    {
        return state;
    }
    _challengeValid = false;
    _authWord[0]    = '\0';
    if ((FTFC->FCSESTAT & FTFC_FCSESTAT_RIN_MASK) != 0U)
    {
        return Result::DONE; // already initialised since the reset ([AN5401] 4.5 note: once after every reset)
    }
    return launch(CMD_INIT_RNG, 0U, plan);
}

CsecRecovery::Result CsecRecovery::challenge(Challenge& out)
{
    WatchdogPlan plan{};
    Result result = ready(CMD_DBG_CHAL, plan); // GET_ID and DBG_CHAL have the same budget
    if (result != Result::DONE)
    {
        return result;
    }
    if ((FTFC->FCSESTAT & FTFC_FCSESTAT_RIN_MASK) == 0U)
    {
        return Result::RNG_NOT_READY; // INIT_RNG here would block longer than the safety task's period check allows
    }
    _challengeValid = false;
    _authWord[0]    = '\0';
    uint8_t const zero[16] = {};
    // GET_ID: the MAC over this challenge is not used (0 while the key is empty)
    writeBytes(_pram, WORD_PAGE1, zero, 16U);
    result = launch(CMD_GET_ID, 0U, plan);
    if (result != Result::DONE)
    {
        return result;
    }
    uint8_t idPage[16] = {};
    readBytes(_pram, WORD_UID, idPage, 16U);
    for (uint32_t i = 0U; i < 15U; ++i)
    {
        out.uid[i] = idPage[i];
    }
    out.sreg = idPage[15];
    result   = launch(CMD_DBG_CHAL, 0U, plan); // DBG_AUTH must follow directly
    if (result != Result::DONE)
    {
        return result;
    }
    readBytes(_pram, WORD_PAGE1, out.challenge, 16U);
    char const* const prefix = AUTH_CONFIRMATION_PREFIX;
    uint32_t n               = 0U;
    while (prefix[n] != '\0')
    {
        _authWord[n] = prefix[n];
        ++n;
    }
    for (uint32_t i = 0U; i < 4U; ++i)
    {
        _authWord[n++] = hexDigit(static_cast<uint8_t>(out.challenge[i] >> 4U));
        _authWord[n++] = hexDigit(static_cast<uint8_t>(out.challenge[i] & 0xFU));
    }
    _authWord[n]    = '\0';
    _challengeValid = true;
    return Result::DONE;
}

CsecRecovery::Result CsecRecovery::authorize(uint8_t const (&authorization)[16], char const* const confirmation)
{
    if (!_challengeValid)
    {
        return Result::NO_CHALLENGE;
    }
    if (!sameText(confirmation, _authWord))
    {
        return Result::WRONG_CONFIRMATION;
    }
    WatchdogPlan plan{};
    Result const state = ready(CMD_DBG_AUTH, plan);
    if (state != Result::DONE)
    {
        return state;
    }
    _challengeValid = false; // one attempt per challenge
    _authWord[0]    = '\0';
    writeBytes(_pram, WORD_PAGE1, authorization, 16U);
    return launch(CMD_DBG_AUTH, 0U, plan);
}

char const* CsecRecovery::resultText(Result const result)
{
    switch (result)
    {
        case Result::DONE: return "done";
        case Result::WRONG_CONFIRMATION: return "wrong or missing confirmation word, nothing written";
        case Result::NOT_FIRST_LOAD:
            return "M1 does not name MASTER_ECU_KEY authorised by itself (only the first load is possible), nothing "
                   "written";
        case Result::FLAGS_REFUSED:
            return "M2 is not 'counter 1, no key flags' of the empty slot - key flags (WRITE_PROTECTION!) are never "
                   "passed on, nothing written";
        case Result::NO_CHALLENGE: return "no challenge of this session (csec challenge first), nothing written";
        case Result::NO_CSEC: return "this part has no CSEc (SIM SDID), nothing written";
        case Result::NOT_PARTITIONED:
            return "FlexRAM is not the emulated EEPROM (EEERDY 0): no CSEc key space to undo, nothing written";
        case Result::BUSY: return "a flash or CSEc command is running, nothing written";
        case Result::CSEC_ERROR: return "the CSEc refused the command (error bits)";
        case Result::RNG_NOT_READY:
            return "random number generator not initialised since the reset (csec rng: INIT_RNG at the next start), "
                   "nothing written";
        case Result::NOT_RUN_MODE: return "core not in RUN mode (HSRUN/VLPR allow no CSEc command), nothing written";
        case Result::UNKNOWN_PART:
            return "sub-series of SIM SDID unknown: no CSEc parameter RAM address for this part, nothing written";
        case Result::WATCHDOG:
            return "MCU watchdog not from the LPO, without 32-bit refresh or with a window too late to keep it quiet, "
                   "nothing written";
        default: return "?";
    }
}

char const* CsecRecovery::errorText(uint16_t const bits)
{
    // priority order of [RM13] 36.5.13.3 (highest first); a set bit names the reason
    if ((bits & ERC_GENERAL_ERROR) != 0U)
    {
        return "ERC_GENERAL_ERROR";
    }
    if ((bits & ERC_SEQUENCE_ERROR) != 0U)
    {
        return "ERC_SEQUENCE_ERROR";
    }
    if ((bits & ERC_KEY_INVALID) != 0U)
    {
        return "ERC_KEY_INVALID";
    }
    if ((bits & ERC_KEY_NOT_AVAILABLE) != 0U)
    {
        return "ERC_KEY_NOT_AVAILABLE";
    }
    if ((bits & ERC_KEY_EMPTY) != 0U)
    {
        return "ERC_KEY_EMPTY";
    }
    if ((bits & ERC_NO_SECURE_BOOT) != 0U)
    {
        return "ERC_NO_SECURE_BOOT";
    }
    if ((bits & ERC_KEY_WRITE_PROTECTED) != 0U)
    {
        return "ERC_KEY_WRITE_PROTECTED (no way back exists)";
    }
    if ((bits & ERC_KEY_UPDATE_ERROR) != 0U)
    {
        return "ERC_KEY_UPDATE_ERROR (M3 wrong, or MASTER_ECU_KEY already loaded)";
    }
    if ((bits & ERC_RNG_SEED) != 0U)
    {
        return "ERC_RNG_SEED";
    }
    if ((bits & ERC_NO_DEBUGGING) != 0U)
    {
        return "ERC_NO_DEBUGGING (authorisation wrong)";
    }
    if ((bits & ERC_MEMORY_FAILURE) != 0U)
    {
        return "ERC_MEMORY_FAILURE";
    }
    if (bits == ERC_NO_ERROR)
    {
        return "ERC_NO_ERROR";
    }
    return "no error bit set";
}

} // namespace platform
