// NVM-13: the CSEc way back to the factory state of the S32K1 family, device side ([AN5401] 4.5, [RM13] 36.5.13).
//
// After PGMPART with a key space (FCCOB1 != 0) the mass erase is refused, with or without a loaded key (CLAUDE.md,
// decision csec-pgmpart-rueckweg 2026-09-29). The only way back:
//   1. loadMasterKey(): CMD_LOAD_KEY of the MASTER_ECU_KEY into its EMPTY slot (authorised by itself, authorising
//      value all ones, counter 1) - only needed while no MASTER_ECU_KEY is loaded;
//   2. initRng(): CMD_INIT_RNG once after the reset (NVM-17: at start-up, before the safety task runs - see below);
//   3. challenge(): CMD_GET_ID (UID), CMD_DBG_CHAL (CHALLENGE); refused while FCSESTAT RIN is clear;
//   4. authorize(): CMD_DBG_AUTH with AUTHORIZATION = CMAC_K(CHALLENGE | UID), K = KDF(MASTER_ECU_KEY, DEBUG_KEY_C);
//   5. reset: all keys erased, FlexRAM plain RAM again (RAMRDY 1), FlexNVM all data flash (EEERDY 0), mass erase
//      possible again.
// M1..M3 and AUTHORIZATION are computed on the workstation (tools/csec); the device never sees the MASTER_ECU_KEY.
//
// Guards (all checked BEFORE anything is written to the CSEc parameter RAM):
//  - IRREVERSIBLE steps need a typed confirmation: LOAD_KEY the word LOAD_KEY_CONFIRMATION, DBG_AUTH the word
//    "DBGAUTH-" + the first four bytes of THIS session's challenge in upper-case hex (so a line of another session
//    or another board is refused). initRng() and challenge() change nothing that outlives a reset and need no word.
//  - NO KEY EVER GETS WRITE_PROTECTION (CLAUDE.md): loadMasterKey() takes only the one load it is for. M1 must name
//    slot 1 authorised by slot 1, and the first block of M2 must be FIRST_LOAD_M2_BLOCK0 - the encryption of
//    "counter 1, all flags 0" under the key derived from the empty slot (CBC, IV 0: the block depends on nothing
//    else, tools/csec first_load_m2_block0()). The flags live only in that block, so any M2 with a flag set - in
//    particular WRITE_PROTECTION - is refused without reaching the CSEc. Another key or a key update is not possible.
//  - a part without CSEc (SIM SDID), a sub-series without a known CSE_PRAM address (NVM-21), FlexRAM not holding
//    the emulated EEPROM (FCNFG EEERDY 0: no key space, nothing to undo - the mass erase works), a running flash
//    command (FSTAT CCIF 0), the core not in RUN mode (SMC PMSTAT: in HSRUN and VLPR the data sheet allows no CSEc or
//    flash command), or an MCU watchdog this module cannot keep quiet (below): refused, nothing written.
//
// Every command is launched from RAM (section .launchFromRAM, like OpenBSW's EepromDriver) with interrupts suspended
// and the code cache off, and the RAM function waits until the command is complete: CMD_DBG_AUTH erases the FlexNVM,
// and nothing may fetch from flash meanwhile.
//
// NVM-17, watchdogs and time. CMD_INIT_RNG "may need several hundred ms" ([RM13] 36.5.13.16) and CMD_DBG_AUTH erases
// the data flash and the key store (the data sheet gives no time for it; the Erase Flash Block of the FlexNVM alone
// takes up to 550 ms on a 64 KB part and 4250 ms on 512 KB). Interrupts stay suspended the whole time, so no task can
// serve a watchdog:
//  - MCU watchdog (WDOG, family peripheral): the RAM function refreshes it while it waits - whenever its counter has
//    passed half the time-out (or the window, if one is set) - but for at most budgetMs(command) of LPO time
//    (WatchdogPlan). A command that hangs longer still ends in the watchdog reset. Refused (WATCHDOG, nothing
//    written) when the watchdog runs in a configuration the plan does not cover: not from the LPO, no 32-bit refresh
//    (CS CMD32EN), or a window that leaves no room. The same counter measures the duration (lastDurationMs()).
//  - A watchdog outside the MCU (SBC) is not this module's: the caller must check that it allows budgetMs(command)
//    (NVM-17: integration::externalWatchdogAllowanceMs() in the console command of the port).
//  - The safety task's period check (SM-04) sees the gap afterwards and resets the ECU. That is harmless after
//    DBG_AUTH (a reset has to follow anyway) and after the short commands (well below one period window), but it
//    would clear FCSESTAT RIN right after an INIT_RNG. Therefore challenge() no longer runs INIT_RNG: initRng() is
//    called at start-up, before the safety task runs, on request of the console command (NVM-17).
//  - An optional journal (setJournal(), no-init RAM of the caller) is written BEFORE the command is launched
//    ("running") and before the interrupts are resumed ("done", error bits, duration), so a reset during or right
//    after a command is visible at the next start.
//
// Reachable only from a test build with the user at the board (NVM-07, option NX_FLEXNVM_PARTITION_COMMAND); nothing
// calls this at start-up unless the console command of that build asked for it (test/test_csec.py).

#pragma once

#include <cstdint>

namespace platform
{

class CsecRecovery
{
public:
    static constexpr char const* LOAD_KEY_CONFIRMATION = "LOADKEY-MASTER-ECU-KEY";
    static constexpr char const* AUTH_CONFIRMATION_PREFIX = "DBGAUTH-";
    static constexpr uint32_t AUTH_CONFIRMATION_LENGTH    = 16U; // "DBGAUTH-" + 8 hex digits

    // CSEc error bits ([RM13] 36.5.13.3.1, Figure 36-16): page 0 word 1 bits 31:16
    static constexpr uint16_t ERC_NO_ERROR             = 0x0001U;
    static constexpr uint16_t ERC_SEQUENCE_ERROR       = 0x0002U;
    static constexpr uint16_t ERC_KEY_NOT_AVAILABLE    = 0x0004U;
    static constexpr uint16_t ERC_KEY_INVALID          = 0x0008U;
    static constexpr uint16_t ERC_KEY_EMPTY            = 0x0010U;
    static constexpr uint16_t ERC_NO_SECURE_BOOT       = 0x0020U;
    static constexpr uint16_t ERC_KEY_WRITE_PROTECTED  = 0x0040U;
    static constexpr uint16_t ERC_KEY_UPDATE_ERROR     = 0x0080U;
    static constexpr uint16_t ERC_RNG_SEED             = 0x0100U;
    static constexpr uint16_t ERC_NO_DEBUGGING         = 0x0200U;
    static constexpr uint16_t ERC_MEMORY_FAILURE       = 0x0400U;
    static constexpr uint16_t ERC_GENERAL_ERROR        = 0x0800U;

    // FuncID of the commands used here ([RM13] 36.5.13.3.1)
    static constexpr uint8_t CMD_LOAD_KEY = 0x07U;
    static constexpr uint8_t CMD_INIT_RNG = 0x0AU;
    static constexpr uint8_t CMD_GET_ID   = 0x10U;
    static constexpr uint8_t CMD_DBG_CHAL = 0x12U;
    static constexpr uint8_t CMD_DBG_AUTH = 0x13U;

    static constexpr uint8_t MASTER_ECU_KEY = 0x01U; // KeyID {KBS 0, KeyIDx 1}

    // NVM-17: how long the MCU watchdog is kept quiet per command, ms of LPO time (see above). INIT_RNG: "several
    // hundred ms" in the reference manual, four times that. DBG_AUTH: above the longest flash erase of the whole
    // family in the data sheet (Erase All Blocks of the S32K148, 17 s), so a reset during the erase cannot come from
    // the MCU watchdog. The others copy a page or program one key record: 1 s.
    static constexpr uint32_t BUDGET_INIT_RNG_MS = 2000U;
    static constexpr uint32_t BUDGET_DBG_AUTH_MS = 20000U;
    static constexpr uint32_t BUDGET_OTHER_MS    = 1000U;
    // WDOG counter clock of the family port: the LPO, 128 kHz nominal (OpenBSW Watchdog::enableWatchdog, CS CLK 01)
    static constexpr uint32_t WATCHDOG_LPO_HZ = 128000U;

    static constexpr uint32_t DURATION_UNKNOWN = 0xFFFFFFFFU;

    enum class Result : uint8_t
    {
        DONE,               // command(s) executed, CSEc answered ERC_NO_ERROR
        WRONG_CONFIRMATION, // nothing written: typed word missing or not the expected one
        NOT_FIRST_LOAD,     // nothing written: M1 does not name MASTER_ECU_KEY authorised by itself (ID/AuthID 1/1)
        FLAGS_REFUSED,      // nothing written: M2 is not "counter 1, no flags" of the empty slot (WRITE_PROTECTION!)
        NO_CHALLENGE,       // nothing written: DBG_AUTH without a challenge of this session (challenge() first)
        NO_CSEC,            // nothing written: SIM SDID says the part has no CSEc
        NOT_PARTITIONED,    // nothing written: FlexRAM is not the emulated EEPROM (EEERDY 0) - no CSEc keys to erase
        BUSY,               // nothing written: a flash or CSEc command is running (FSTAT CCIF 0)
        CSEC_ERROR,         // the CSEc answered an error, see errorBits()
        RNG_NOT_READY,      // nothing written: FCSESTAT RIN clear - INIT_RNG first (at start-up, NVM-17)
        NOT_RUN_MODE,       // nothing written: the core is not in RUN mode (HSRUN, VLPR: no CSEc/flash commands there)
        WATCHDOG,           // nothing written: the MCU watchdog runs in a configuration the plan does not cover
        UNKNOWN_PART,       // nothing written: SIM SDID sub-series without a known CSE_PRAM address (NVM-21)
    };

    struct LoadKeyAnswer
    {
        uint8_t m4[32];
        uint8_t m5[16];
    };

    struct Challenge
    {
        uint8_t challenge[16];
        uint8_t uid[15];
        uint8_t sreg; // status register of CMD_GET_ID
    };

    /**
     * NVM-17: how the RAM function keeps the MCU watchdog quiet during one command. refreshAt 0: the watchdog is off,
     * nothing to do. Otherwise it refreshes whenever the counter reached refreshAt, at most refreshLimit times.
     */
    struct WatchdogPlan
    {
        uint32_t refreshAt;
        uint32_t refreshLimit;
        uint32_t ticksPerSecond; // counter ticks per second (LPO, prescaler), 0 with the watchdog off
    };

    /**
     * NVM-17: record of the last command in the caller's no-init RAM (setJournal()). Written before the launch
     * (state RUNNING) and before the interrupts are resumed (state DONE, error bits, duration); valid() tells a
     * record from the random content after power-on.
     */
    struct Journal
    {
        static constexpr uint32_t TAG           = 0x43534543U; // "CSEC"
        static constexpr uint8_t STATE_RUNNING  = 1U;
        static constexpr uint8_t STATE_DONE     = 2U;

        uint32_t tag;
        uint32_t word;       // command 31:24, state 23:16, error bits 15:0
        uint32_t durationMs; // DURATION_UNKNOWN without a running watchdog
        uint32_t check;      // ~(tag ^ word ^ durationMs)

        bool valid() const volatile { return (tag == TAG) && (check == ~(tag ^ word ^ durationMs)); }
        uint8_t command() const volatile { return static_cast<uint8_t>(word >> 24U); }
        uint8_t state() const volatile { return static_cast<uint8_t>(word >> 16U); }
        uint16_t errorBits() const volatile { return static_cast<uint16_t>(word); }
    };

    /** CMD_LOAD_KEY of the MASTER_ECU_KEY into its empty slot; \p answer holds M4/M5 on DONE. IRREVERSIBLE. */
    Result loadMasterKey(
        uint8_t const (&m1)[16],
        uint8_t const (&m2)[32],
        uint8_t const (&m3)[16],
        char const* confirmation,
        LoadKeyAnswer& answer);

    /**
     * CMD_INIT_RNG, unless FCSESTAT RIN is already set (DONE without a command). Blocks the core for up to several
     * hundred ms: only at start-up, before the safety task runs (NVM-17).
     */
    Result initRng();

    /** CMD_GET_ID, CMD_DBG_CHAL; RNG_NOT_READY while FCSESTAT RIN is clear. Nothing that outlives a reset. */
    Result challenge(Challenge& out);

    /**
     * CMD_DBG_AUTH with \p authorization; \p confirmation is "DBGAUTH-" + the first 4 bytes of the last challenge.
     * IRREVERSIBLE: erases all CSEc keys and the FlexNVM partition (after the next reset).
     */
    Result authorize(uint8_t const (&authorization)[16], char const* confirmation);

    /** NVM-17: the journal the next commands write (nullptr: none). */
    void setJournal(Journal volatile* journal) { _journal = journal; }

    /** Error bits of the last command the CSEc executed (0: none executed). */
    uint16_t errorBits() const { return _errorBits; }

    /** FuncID of the last command launched (0: none). */
    uint8_t lastCommand() const { return _lastCommand; }

    /** NVM-17: duration of the last command in ms, measured with the watchdog counter (DURATION_UNKNOWN: off). */
    uint32_t lastDurationMs() const { return _durationMs; }

    /** NVM-17: watchdog refreshes of the RAM function during the last command. */
    uint32_t lastWatchdogRefreshes() const { return _refreshes; }

    /** The confirmation word authorize() expects now ("" without a challenge). */
    char const* authConfirmation() const { return _authWord; }

    /** NVM-17: how long the MCU watchdog is kept quiet for \p command, ms. */
    static uint32_t budgetMs(uint8_t command);

    /**
     * NVM-17: the plan for \p command against the watchdog as it runs now; false (WATCHDOG) for a configuration the
     * plan does not cover. Register reads only.
     */
    static bool planWatchdog(uint8_t command, WatchdogPlan& plan);

    static char const* resultText(Result result);

    /**
     * NVM-21: address of the CSEc parameter RAM of this part (SIM SDID sub-series, csec/CsecPramAddress.h): 0x1400_1000
     * on the S32K14x, 0x1400_0800 on the S32K11x, 0 for an unknown sub-series. Register read only.
     */
    static uintptr_t pramAddress();

    /** Name of the highest-priority error bit set in \p bits ([RM13] 36.5.13.3 order). */
    static char const* errorText(uint16_t bits);

    /** First block of M2 of the first MASTER_ECU_KEY load: counter 1, no flags (see above). */
    static uint8_t const* firstLoadM2Block0();

private:
    // sets _pram (0 unless DONE)
    Result ready(uint8_t command, WatchdogPlan& plan);
    Result launch(uint8_t command, uint8_t keyId, WatchdogPlan const& plan);
    void journal(uint8_t command, uint8_t state, uint16_t errorBits, uint32_t durationMs);

    Journal volatile* _journal = nullptr;
    uintptr_t _pram       = 0U; // NVM-21: CSE_PRAM of this part, set by ready() before anything is written
    uint32_t _durationMs  = DURATION_UNKNOWN;
    uint32_t _refreshes   = 0U;
    uint16_t _errorBits   = 0U;
    uint8_t _lastCommand  = 0U;
    bool _challengeValid  = false;
    char _authWord[AUTH_CONFIRMATION_LENGTH + 1U] = {};
};

} // namespace platform
