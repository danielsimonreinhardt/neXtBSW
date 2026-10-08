// Console command "csec" (NVM-17), see systems/CsecCommand.h. TEST BUILDS ONLY: this file is compiled only with the
// CMake option NX_FLEXNVM_PARTITION_COMMAND (port/main/CMakeLists.txt), together with the library bspCsecRecovery.
// Runs in the console context; "csec rng" arms INIT_RNG for the next start (csecInitRngAtStart, before the BSP).

#include "systems/CsecCommand.h"

#include "csec/CsecHex.h"
#include "flexnvm/FlexNvmChip.h"
#include "integration/Integration.h"
#include "mcu/mcu.h"
#include "reset/softwareSystemReset.h"

#include <safeSupervisor/SafeConsole.h>
#include <util/command/CommandContext.h>
#include <util/format/SharedStringWriter.h>

#include <cinttypes>
#include <cstdio>

namespace
{
using ::platform::CsecRecovery;
using CsecResult = CsecRecovery::Result;

enum Id
{
    ID_STATUS,
    ID_M1,
    ID_M2,
    ID_M3,
    ID_LOADKEY,
    ID_RNG,
    ID_CHALLENGE,
    ID_DBGAUTH,
};

constexpr uint8_t STAGED_M1  = 0x1U;
constexpr uint8_t STAGED_M2  = 0x2U;
constexpr uint8_t STAGED_M3  = 0x4U;
constexpr uint8_t STAGED_ALL = STAGED_M1 | STAGED_M2 | STAGED_M3;

constexpr uint32_t WORD_MAX         = 31U;
constexpr uint32_t RNG_REQUEST      = 0x524E4731U; // "RNG1"
constexpr uint32_t UNLIMITED        = 0xFFFFFFFFU;

// No-init RAM of the application linker script (.noinit.*, not cleared by startUp.S): the journal of the last CSEc
// command (written by CsecRecovery before the launch and before the interrupts come back - it survives the reset of
// the safety task's period check after DBG_AUTH and a watchdog reset during a command) and the INIT_RNG request of
// "csec rng" (word and its complement: the random content after power-on is not a request).
__attribute__((section(".noinit.csec"))) CsecRecovery::Journal volatile csecJournal;
__attribute__((section(".noinit.csec"))) uint32_t volatile csecRngRequest[2];

// Result of the INIT_RNG at this start (csecInitRngAtStart), for "csec status"
struct StartRng
{
    bool ran;
    CsecResult result;
    uint16_t errorBits;
    uint32_t durationMs;
    uint32_t refreshes;
};

StartRng startRng{false, CsecResult::DONE, 0U, CsecRecovery::DURATION_UNKNOWN, 0U};

char const* commandName(uint8_t const command)
{
    switch (command)
    {
        case CsecRecovery::CMD_LOAD_KEY: return "LOAD_KEY";
        case CsecRecovery::CMD_INIT_RNG: return "INIT_RNG";
        case CsecRecovery::CMD_GET_ID: return "GET_ID";
        case CsecRecovery::CMD_DBG_CHAL: return "DBG_CHAL";
        case CsecRecovery::CMD_DBG_AUTH: return "DBG_AUTH";
        default: return "?";
    }
}

// the next token into word (at most WORD_MAX characters, longer ones are cut and so never match); its length
uint32_t scanWord(::util::command::CommandContext& context, char (&word)[WORD_MAX + 1U])
{
    word[0] = '\0';
    if (!context.hasToken())
    {
        return 0U;
    }
    ::util::string::ConstString const token = context.scanToken();
    uint32_t const length = (token.length() < WORD_MAX) ? static_cast<uint32_t>(token.length()) : WORD_MAX;
    for (uint32_t i = 0U; i < length; ++i)
    {
        word[i] = token.data()[i];
    }
    word[length] = '\0';
    return static_cast<uint32_t>(token.length());
}

// the next token as exactly `bytes` bytes of hex; false if missing or malformed (out untouched)
bool scanHex(::util::command::CommandContext& context, uint8_t* const out, size_t const bytes)
{
    if (!context.hasToken())
    {
        return false;
    }
    ::util::string::ConstString const token = context.scanToken();
    return ::platform::csechex::parse(token.data(), token.length(), out, bytes);
}

bool firstBlockIs(uint8_t const* const block, uint8_t const* const expected)
{
    for (uint32_t i = 0U; i < 16U; ++i)
    {
        if (block[i] != expected[i])
        {
            return false;
        }
    }
    return true;
}

void printHex(::util::format::SharedStringWriter& out, char const* const name, uint8_t const* const bytes,
              size_t const count)
{
    char text[65] = {};
    ::platform::csechex::format(bytes, count, text);
    (void)out.printf("  %s = %s\r\n", name, text);
}

void printResult(::util::format::SharedStringWriter& out, char const* const what, CsecRecovery const& csec,
                 CsecResult const result)
{
    (void)out.printf("csec: %s: %s\r\n", what, CsecRecovery::resultText(result));
    // details only when this call launched a command (a refusal writes nothing, the last command is an older one)
    if ((csec.lastCommand() != 0U) && ((result == CsecResult::DONE) || (result == CsecResult::CSEC_ERROR)))
    {
        (void)out.printf("  last command %s, error bits 0x%04x (%s)", commandName(csec.lastCommand()),
                         static_cast<unsigned>(csec.errorBits()), CsecRecovery::errorText(csec.errorBits()));
        if (csec.lastDurationMs() != CsecRecovery::DURATION_UNKNOWN)
        {
            (void)out.printf(", %u ms, %u watchdog refreshes", static_cast<unsigned>(csec.lastDurationMs()),
                             static_cast<unsigned>(csec.lastWatchdogRefreshes()));
        }
        (void)out.printf("\r\n");
    }
}

// the watchdog outside the MCU must allow the budget of the command; false (and a line) otherwise
bool externalWatchdogAllows(::util::format::SharedStringWriter& out, uint8_t const command)
{
    uint32_t const allowed = ::integration::externalWatchdogAllowanceMs();
    uint32_t const budget  = CsecRecovery::budgetMs(command);
    if (allowed >= budget)
    {
        return true;
    }
    (void)out.printf("csec: %s needs %u ms with interrupts suspended, the watchdog outside the MCU (SBC) allows %u "
                     "ms - nothing written. Test build with that watchdog off (software development mode) or a "
                     "longer period.\r\n",
                     commandName(command), static_cast<unsigned>(budget), static_cast<unsigned>(allowed));
    return false;
}

void printJournal(::util::format::SharedStringWriter& out)
{
    if (!csecJournal.valid())
    {
        (void)out.printf("journal: none (no CSEc command since power-on)\r\n");
        return;
    }
    uint8_t const state = csecJournal.state();
    (void)out.printf("journal: last command %s, %s", commandName(csecJournal.command()),
                     (state == CsecRecovery::Journal::STATE_DONE)
                         ? "done"
                         : "INTERRUPTED (reset while it ran - check the chip state with flexnvm show)");
    if (state == CsecRecovery::Journal::STATE_DONE)
    {
        (void)out.printf(", error bits 0x%04x (%s)", static_cast<unsigned>(csecJournal.errorBits()),
                         CsecRecovery::errorText(csecJournal.errorBits()));
        if (csecJournal.durationMs != CsecRecovery::DURATION_UNKNOWN)
        {
            (void)out.printf(", %u ms", static_cast<unsigned>(csecJournal.durationMs));
        }
    }
    (void)out.printf("\r\n");
}

void printStatus(::util::format::SharedStringWriter& out, uint8_t const staged)
{
    ::platform::FlexNvmChip::DeviceId const id = ::platform::FlexNvmChip::deviceId();
    (void)out.printf("device: CSEc %s, FlexRAM %s (RAMRDY %u, EEERDY %u)\r\n", id.security ? "present" : "NOT present",
                     ::platform::FlexNvmChip::eeeReady() ? "is the emulated EEPROM (key space possible)"
                                                          : "is RAM (no key space, mass erase possible)",
                     static_cast<unsigned>(::platform::FlexNvmChip::ramReady() ? 1U : 0U),
                     static_cast<unsigned>(::platform::FlexNvmChip::eeeReady() ? 1U : 0U));
    uint32_t const cse = FTFC->FCSESTAT;
    (void)out.printf("FCSESTAT 0x%02x: RIN %u (random number generator initialised since the reset), BOK %u, BFN "
                     "%u, EDB %u, IDB %u, BSY %u\r\n",
                     static_cast<unsigned>(cse), static_cast<unsigned>((cse & FTFC_FCSESTAT_RIN_MASK) != 0U),
                     static_cast<unsigned>((cse & FTFC_FCSESTAT_BOK_MASK) != 0U),
                     static_cast<unsigned>((cse & FTFC_FCSESTAT_BFN_MASK) != 0U),
                     static_cast<unsigned>((cse & FTFC_FCSESTAT_EDB_MASK) != 0U),
                     static_cast<unsigned>((cse & FTFC_FCSESTAT_IDB_MASK) != 0U),
                     static_cast<unsigned>((cse & FTFC_FCSESTAT_BSY_MASK) != 0U));
    CsecRecovery::WatchdogPlan plan{};
    if (!CsecRecovery::planWatchdog(CsecRecovery::CMD_DBG_AUTH, plan))
    {
        (void)out.printf("MCU watchdog: %s\r\n", CsecRecovery::resultText(CsecResult::WATCHDOG));
    }
    else if (plan.refreshAt == 0U)
    {
        (void)out.printf("MCU watchdog: off\r\n");
    }
    else
    {
        (void)out.printf("MCU watchdog: kept quiet during a command (refresh at %u ticks, %u ticks/s), budget "
                         "INIT_RNG %u ms, DBG_AUTH %u ms, others %u ms\r\n",
                         static_cast<unsigned>(plan.refreshAt), static_cast<unsigned>(plan.ticksPerSecond),
                         static_cast<unsigned>(CsecRecovery::BUDGET_INIT_RNG_MS),
                         static_cast<unsigned>(CsecRecovery::BUDGET_DBG_AUTH_MS),
                         static_cast<unsigned>(CsecRecovery::BUDGET_OTHER_MS));
    }
    uint32_t const allowed = ::integration::externalWatchdogAllowanceMs();
    if (allowed == UNLIMITED)
    {
        (void)out.printf("watchdog outside the MCU: none running\r\n");
    }
    else
    {
        (void)out.printf("watchdog outside the MCU (SBC): allows %u ms with interrupts suspended%s\r\n",
                         static_cast<unsigned>(allowed),
                         (allowed < CsecRecovery::BUDGET_DBG_AUTH_MS) ? " - NOT enough for dbgauth" : "");
    }
    if (startRng.ran)
    {
        (void)out.printf("INIT_RNG at this start: %s, error bits 0x%04x (%s)",
                         CsecRecovery::resultText(startRng.result), static_cast<unsigned>(startRng.errorBits),
                         CsecRecovery::errorText(startRng.errorBits));
        if (startRng.durationMs != CsecRecovery::DURATION_UNKNOWN)
        {
            (void)out.printf(", %u ms, %u watchdog refreshes", static_cast<unsigned>(startRng.durationMs),
                             static_cast<unsigned>(startRng.refreshes));
        }
        (void)out.printf("\r\n");
    }
    printJournal(out);
    (void)out.printf("staged: M1 %s, M2 %s, M3 %s\r\n", ((staged & STAGED_M1) != 0U) ? "yes" : "no",
                     ((staged & STAGED_M2) != 0U) ? "yes" : "no", ((staged & STAGED_M3) != 0U) ? "yes" : "no");
}
} // namespace

namespace systems
{

CsecCommand::CsecCommand() : _csec(), _m1(), _m2(), _m3(), _staged(0U) { _csec.setJournal(&csecJournal); }

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(CsecCommand, "csec", "CSEc way back to the factory state (NVM-17, TEST BUILD, board rule)")
COMMAND_GROUP_COMMAND(ID_STATUS, "status", "CSEc, FlexRAM, FCSESTAT, watchdogs, staged M1..M3, last command")
COMMAND_GROUP_COMMAND(ID_M1, "m1", "stage M1 (param: <32 hex>, from tools/csec load-master)")
COMMAND_GROUP_COMMAND(ID_M2, "m2", "stage M2 (param: <64 hex>)")
COMMAND_GROUP_COMMAND(ID_M3, "m3", "stage M3 (param: <32 hex>)")
COMMAND_GROUP_COMMAND(ID_LOADKEY, "loadkey", "IRREVERSIBLE: load the MASTER_ECU_KEY into its empty slot (param: LOADKEY-MASTER-ECU-KEY) (board rule)")
COMMAND_GROUP_COMMAND(ID_RNG, "rng", "reset, then INIT_RNG at the start before the safety task (needed once per reset) (board rule)")
COMMAND_GROUP_COMMAND(ID_CHALLENGE, "challenge", "GET_ID and DBG_CHAL: UID, CHALLENGE, word for dbgauth (board rule)")
COMMAND_GROUP_COMMAND(ID_DBGAUTH, "dbgauth", "IRREVERSIBLE: erase all keys and the partition (param: <32 hex> DBGAUTH-XXXXXXXX) (board rule)")
DEFINE_COMMAND_GROUP_GET_INFO_END

void CsecCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    ::util::format::SharedStringWriter out(context);
    if (idx == ID_STATUS)
    {
        if (context.checkEol())
        {
            printStatus(out, _staged);
        }
        return;
    }
    if ((idx == ID_M1) || (idx == ID_M2) || (idx == ID_M3))
    {
        uint8_t* const target = (idx == ID_M1) ? _m1 : ((idx == ID_M2) ? _m2 : _m3);
        size_t const bytes    = (idx == ID_M2) ? 32U : 16U;
        uint8_t const bit     = (idx == ID_M1) ? STAGED_M1 : ((idx == ID_M2) ? STAGED_M2 : STAGED_M3);
        bool const ok         = scanHex(context, target, bytes);
        if (!context.checkEol())
        {
            return;
        }
        if (!ok)
        {
            (void)out.printf("csec: expected %u hex digits, nothing staged\r\n", static_cast<unsigned>(2U * bytes));
            return;
        }
        _staged = static_cast<uint8_t>(_staged | bit);
        if ((idx == ID_M2) && !firstBlockIs(_m2, CsecRecovery::firstLoadM2Block0()))
        {
            // loadkey refuses it as well; said here so the wrong line is seen at once
            (void)out.printf(
                "csec: M2 is not 'counter 1, no key flags' of the empty slot - loadkey will refuse it\r\n");
        }
        (void)out.printf("csec: staged (M1 %s, M2 %s, M3 %s)\r\n", ((_staged & STAGED_M1) != 0U) ? "yes" : "no",
                         ((_staged & STAGED_M2) != 0U) ? "yes" : "no", ((_staged & STAGED_M3) != 0U) ? "yes" : "no");
        return;
    }
    if (idx == ID_LOADKEY)
    {
        char word[WORD_MAX + 1U] = {};
        uint32_t const length    = scanWord(context, word);
        if (!context.checkEol())
        {
            return;
        }
        if (_staged != STAGED_ALL)
        {
            (void)out.printf("csec: stage M1, M2 and M3 first (csec m1/m2/m3), nothing written\r\n");
            return;
        }
        if (length > WORD_MAX)
        {
            word[0] = '\0'; // a cut word is no confirmation
        }
        if (!externalWatchdogAllows(out, CsecRecovery::CMD_LOAD_KEY))
        {
            return;
        }
        CsecRecovery::LoadKeyAnswer answer{};
        CsecResult const result = _csec.loadMasterKey(_m1, _m2, _m3, word, answer);
        _staged             = 0U; // one attempt per staged set
        printResult(out, "LOAD_KEY", _csec, result);
        if (result == CsecResult::DONE)
        {
            printHex(out, "M4", answer.m4, 32U);
            printHex(out, "M5", answer.m5, 16U);
            (void)out.printf("  compare: python -m tools.csec check-load --project <project> --m4 <M4> --m5 <M5> "
                             "[--uid <UID>]\r\n");
        }
        return;
    }
    if (idx == ID_RNG)
    {
        if (!context.checkEol())
        {
            return;
        }
        CsecRecovery::WatchdogPlan plan{};
        ::platform::FlexNvmChip::DeviceId const id = ::platform::FlexNvmChip::deviceId();
        if (!id.security || !::platform::FlexNvmChip::eeeReady())
        {
            (void)out.printf("csec: %s\r\n", CsecRecovery::resultText(id.security ? CsecResult::NOT_PARTITIONED
                                                                                 : CsecResult::NO_CSEC));
            return;
        }
        if (!CsecRecovery::planWatchdog(CsecRecovery::CMD_INIT_RNG, plan))
        {
            (void)out.printf("csec: %s\r\n", CsecRecovery::resultText(CsecResult::WATCHDOG));
            return;
        }
        if (!externalWatchdogAllows(out, CsecRecovery::CMD_INIT_RNG))
        {
            return;
        }
        if ((FTFC->FCSESTAT & FTFC_FCSESTAT_RIN_MASK) != 0U)
        {
            (void)out.printf("csec: random number generator already initialised since the reset - csec challenge\r\n");
            return;
        }
        csecRngRequest[0] = RNG_REQUEST;
        csecRngRequest[1] = ~RNG_REQUEST;
        (void)out.printf("csec: INIT_RNG armed for the next start (before the safety task) - resetting now; then "
                         "csec status and csec challenge\r\n");
        (void)::safety::safeConsoleFlush();
        softwareSystemReset();
        return;
    }
    if (idx == ID_CHALLENGE)
    {
        if (!context.checkEol())
        {
            return;
        }
        if (!externalWatchdogAllows(out, CsecRecovery::CMD_DBG_CHAL))
        {
            return;
        }
        CsecRecovery::Challenge challenge{};
        CsecResult const result = _csec.challenge(challenge);
        printResult(out, "challenge", _csec, result);
        if (result == CsecResult::DONE)
        {
            printHex(out, "UID", challenge.uid, 15U);
            (void)out.printf("  SREG = 0x%02x\r\n", static_cast<unsigned>(challenge.sreg));
            printHex(out, "CHALLENGE", challenge.challenge, 16U);
            (void)out.printf("  next: python -m tools.csec debug-auth --project <project> --challenge <CHALLENGE> "
                             "--uid <UID>\r\n");
            (void)out.printf("  then: csec dbgauth <AUTHORIZATION> %s (no other csec command in between)\r\n",
                             _csec.authConfirmation());
        }
        return;
    }
    if (idx == ID_DBGAUTH)
    {
        uint8_t authorization[16] = {};
        bool const ok             = scanHex(context, authorization, 16U);
        char word[WORD_MAX + 1U]  = {};
        uint32_t const length     = scanWord(context, word);
        if (!context.checkEol())
        {
            return;
        }
        if (!ok)
        {
            (void)out.printf("csec: expected <32 hex> DBGAUTH-XXXXXXXX, nothing written\r\n");
            return;
        }
        if (length > WORD_MAX)
        {
            word[0] = '\0';
        }
        if (!externalWatchdogAllows(out, CsecRecovery::CMD_DBG_AUTH))
        {
            return;
        }
        (void)out.printf("csec: DBG_AUTH - up to %u ms with interrupts suspended; after a successful erase the "
                         "safety task's period check resets the ECU: then csec status (journal) and flexnvm show\r\n",
                         static_cast<unsigned>(CsecRecovery::BUDGET_DBG_AUTH_MS));
        (void)::safety::safeConsoleFlush();
        CsecResult const result = _csec.authorize(authorization, word);
        printResult(out, "DBG_AUTH", _csec, result);
        if (result == CsecResult::DONE)
        {
            (void)out.printf("  all CSEc keys erased; reset the ECU - FlexRAM is RAM again after it (flexnvm show: "
                             "RAMRDY 1, EEERDY 0)\r\n");
        }
        return;
    }
}

void csecInitRngAtStart()
{
    if ((csecRngRequest[0] != RNG_REQUEST) || (csecRngRequest[1] != ~RNG_REQUEST))
    {
        return;
    }
    // cleared first: a reset during INIT_RNG does not repeat it
    csecRngRequest[0] = 0U;
    csecRngRequest[1] = 0U;
    CsecRecovery csec;
    csec.setJournal(&csecJournal);
    CsecResult const result = csec.initRng();
    startRng            = StartRng{true, result, csec.errorBits(), csec.lastDurationMs(), csec.lastWatchdogRefreshes()};
    printf("csec: INIT_RNG at start: %s, error bits 0x%04x, %" PRIu32 " ms\r\n", CsecRecovery::resultText(result),
           static_cast<unsigned>(csec.errorBits()), csec.lastDurationMs());
}

} // namespace systems
