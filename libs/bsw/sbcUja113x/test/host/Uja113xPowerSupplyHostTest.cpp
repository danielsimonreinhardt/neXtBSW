/* Host test of the role power_supply of the UJA113x (sbc/Uja113xPowerSupply.h, SYS-01): start reasons from the
 * startup information, power down = SBC Sleep, and the ECU state controller with the SBC as its power supply.
 * Lean register model (development mode, watchdog off); the full model is in Uja113xHostTest.cpp.
 * Build and run: libs/bsw/ecuState/test/test_ecustate_host.py (zig c++). */

#include "ecuState/EcuStateController.h"
#include "ecuState/EcuStateRte.h"
#include "ecuState/McuStartReasons.h"
#include "ecuState/PermanentPowerSupply.h"
#include "sbc/Uja113x.h"
#include "sbc/Uja113xPowerSupply.h"

#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using namespace ::ecustate;
using PowerDownResult = IPowerSupply::PowerDownResult;
using SbcState        = ::sbc::Uja113x::State;

/** Selection the ECU state management would pass for "CAN wake-up on bus 0" (SYS-02). */
constexpr WakeupSelection CAN0_ONLY{wakeup::canBus(0U), 0U};

uint8_t const ID = 0x51U;

class LeanUja113x : public ::bsp::spi::ISpiDevice
{
public:
    uint8_t reg[128];
    bool sleeping     = false;
    bool illegalSleep = false;

    LeanUja113x()
    {
        std::memset(reg, 0, sizeof(reg));
        reg[UJA113X_REG_IDENTIFICATION_0]   = ID;
        reg[UJA113X_REG_WATCHDOG_CONTROL]   = (UJA113X_WMC_AUTONOMOUS << 5U) | UJA113X_NWP_128MS;
        reg[UJA113X_REG_MODE_CONTROL]       = UJA113X_MC_STANDBY;
        reg[UJA113X_REG_WATCHDOG_STATUS]    = UJA113X_SDMS_MASK;
        reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK;
        reg[UJA113X_REG_SBC_CONFIG_CONTROL] = 0x06U;
    }

    void resetSource(uint8_t const rss)
    {
        reg[UJA113X_REG_MAIN_STATUS] = static_cast<uint8_t>(UJA113X_NMS_MASK | rss);
    }

    uint8_t global() const
    {
        uint8_t g = 0U;
        for (uint8_t i = 0U; i < 7U; ++i)
        {
            if (reg[0x61U + i] != 0U)
            {
                g = static_cast<uint8_t>(g | (1U << i));
            }
        }
        return g;
    }

    bool transfer(uint32_t const tx, uint32_t& rx) override
    {
        uint8_t const address = static_cast<uint8_t>((tx >> 9U) & 0x7FU);
        bool const readOnly   = (tx & 0x100U) != 0U;
        uint8_t const data    = static_cast<uint8_t>(tx & 0xFFU);
        uint8_t const value   = (address == UJA113X_REG_GLOBAL_IRQ_STATUS) ? global() : reg[address];
        rx = (static_cast<uint32_t>(address) << 9U) | (tx & 0x100U) | value;
        if (readOnly)
        {
            return true;
        }
        if ((address >= UJA113X_REG_SYSTEM_IRQ_STATUS) && (address <= 0x67U))
        {
            reg[address] = static_cast<uint8_t>(reg[address] & ~data);
        }
        else if (address == UJA113X_REG_MODE_CONTROL)
        {
            if (data == UJA113X_MC_SLEEP)
            {
                // The SBC resets instead of sleeping without a wake-up source: CAN wake-up, a bank 0 wake-up
                // input or a watchdog that keeps running in Sleep (cyclic wake-up, SYS-02).
                uint8_t const wmc = static_cast<uint8_t>(
                    (reg[UJA113X_REG_WATCHDOG_CONTROL] & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT);
                bool const wake = ((reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) != 0U)
                                  || (reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] != 0U)
                                  || (wmc != UJA113X_WMC_AUTONOMOUS);
                if ((global() != 0U) || !wake)
                {
                    illegalSleep = true;
                    return true;
                }
                sleeping = true;
            }
            reg[address] = data;
        }
        else if ((address == UJA113X_REG_MAIN_STATUS) || (address == UJA113X_REG_GLOBAL_IRQ_STATUS)
                 || (address >= UJA113X_REG_MTPNV_STATUS))
        {
            // read only
        }
        else
        {
            reg[address] = data;
        }
        return true;
    }
};

::sbc::Uja113xConfig config()
{
    ::sbc::Uja113xConfig c{};
    c.identification       = ID;
    c.startupControl       = 0x00U;
    c.sbcConfig            = 0x06U;
    c.watchdogMode         = ::sbc::WatchdogMode::AUTONOMOUS;
    c.watchdogPeriod       = ::sbc::WatchdogPeriod::MS_128;
    c.v2                   = ::sbc::V2Mode::ON_NORMAL_STANDBY_RESET;
    c.can                  = ::sbc::CanMode::ACTIVE;
    c.transceiverIrqEnable = UJA113X_CFI_MASK;
    return c;
}

struct Sbc
{
    explicit Sbc(uint8_t const identification = ID) : cfg(config())
    {
        cfg.identification                        = identification;
        fake.reg[UJA113X_REG_IDENTIFICATION_0]    = identification;
    }

    LeanUja113x fake;
    ::sbc::Uja113xConfig cfg;
    ::sbc::Uja113x driver{fake, cfg};
};

// ---------------------------------------------------------------------------------------------------------------

void startReasons()
{
    // rcc: RCC (register 0x02) as the SBC holds it at the startup of the application; the configuration writes 0
    // back after every startup, so 0 without a start interrupt means no SBC reset since then (NVM-34).
    struct Case
    {
        char const* name;
        uint8_t rss;
        uint8_t system;
        uint8_t transceiver;
        uint8_t wake;      // 0x64, two bits per HVIO of bank 0 (rising, falling edge)
        uint8_t wakeBank1; // 0x66, only on a device with two HVIO banks
        uint8_t identification;
        uint8_t canIndex;
        uint8_t rcc;
        uint32_t expected;
        bool restarted;
    } const cases[] = {
        {"power-on", UJA113X_RSS_EXIT_OFF, UJA113X_POSI_MASK, 0U, 0U, 0U, ID, 0U, 0U, wakeup::POWER, true},
        {"power-on with CAN activity", UJA113X_RSS_EXIT_OFF, UJA113X_POSI_MASK, UJA113X_CWI_MASK, 0U, 0U, ID,
         0U, 0U, wakeup::POWER | wakeup::canBus(0U), true},
        {"CAN wake-up on bus 1", UJA113X_RSS_CAN_WAKEUP, 0U, UJA113X_CWI_MASK, 0U, 0U, ID, 1U, 1U,
         wakeup::canBus(1U), true},
        // JP1 open: the RCC compensation of the MCU test resets took the count of the wake-up back - the
        // interrupt still tells
        {"CAN wake-up, RCC compensated", UJA113X_RSS_CAN_WAKEUP, 0U, UJA113X_CWI_MASK, 0U, 0U, ID, 0U, 0U,
         wakeup::canBus(0U), true},
        {"partial networking frame error on bus 0", UJA113X_RSS_CAN_WAKEUP, 0U, UJA113X_PNFDEI_MASK, 0U, 0U,
         ID, 0U, 1U, wakeup::canBus(0U), true},
        {"CAN wake-up, bus not used", UJA113X_RSS_CAN_WAKEUP, 0U, UJA113X_CWI_MASK, 0U, 0U, ID, 0xFFU, 1U,
         wakeup::UNKNOWN, true},
        {"MCU-only reset after CAN wake-up", UJA113X_RSS_CAN_WAKEUP, 0U, 0U, 0U, 0U, ID, 0U, 0U, wakeup::RESET,
         false},
        // NVM-34, board 2026-10-02: "lc reboot" with JP1 open - RSS 0 of long ago, RCC 0
        {"MCU-only reset after power-on (JP1 open)", UJA113X_RSS_EXIT_OFF, 0U, 0U, 0U, 0U, ID, 0U, 0U,
         wakeup::RESET, false},
        // NVM-34, board 2026-10-02: "lc reboot" with JP1 closed - RSS 17, RCC 1 after the compensation
        {"RSTN (JP1 closed)", UJA113X_RSS_RSTN_PULLED_DOWN, 0U, 0U, 0U, 0U, ID, 0U, 1U, wakeup::RESET, true},
        {"MCU-only reset after an RSTN reset (JP1 opened since)", UJA113X_RSS_RSTN_PULLED_DOWN, 0U, 0U, 0U, 0U,
         ID, 0U, 0U, wakeup::RESET, false},
        // Register 0x64 holds two bits per HVIO: bit 4 is the rising edge of HVIO3, so input index 2 (IO-10)
        {"local wake-up input 2, rising edge", 6U, 0U, 0U, 0x10U, 0U, ID, 0U, 1U, wakeup::local(2U), true},
        {"local wake-up input 2, falling edge", 6U, 0U, 0U, 0x20U, 0U, ID, 0U, 1U, wakeup::local(2U), true},
        {"local wake-up input 0", 4U, 0U, 0U, 0x01U, 0U, ID, 0U, 1U, wakeup::local(0U), true},
        // Bank 1 (HVIO5..8) has its own register and must not be mapped onto bank 0: bit 0 of 0x66 is HVIO5
        {"local wake-up input 4 on a device with two banks", 8U, 0U, 0U, 0U, 0x01U, 0x11U, 0U, 1U,
         wakeup::local(4U), true},
        {"watchdog overflow", UJA113X_RSS_WD_OVERFLOW, 0U, 0U, 0U, 0U, ID, 0U, 1U, wakeup::WATCHDOG, true},
        {"watchdog early trigger", UJA113X_RSS_WD_TRIGGER_EARLY, 0U, 0U, 0U, 0U, ID, 0U, 1U, wakeup::WATCHDOG,
         true},
        // NVM-34: an old watchdog reset of the SBC is not reported again by an MCU-only start
        {"MCU-only reset after an SBC watchdog reset", UJA113X_RSS_WD_OVERFLOW, 0U, 0U, 0U, 0U, ID, 0U, 0U,
         wakeup::RESET, false},
        {"cyclic wake-up in sleep", UJA113X_RSS_WD_OVERFLOW_SLEEP, 0U, 0U, 0U, 0U, ID, 0U, 1U, wakeup::TIMER,
         true},
        {"V1 undervoltage", UJA113X_RSS_V1_UNDERVOLTAGE, 0U, 0U, 0U, 0U, ID, 0U, 1U, wakeup::SUPPLY, true},
        {"MCU-only reset after a V1 undervoltage", UJA113X_RSS_V1_UNDERVOLTAGE, 0U, 0U, 0U, 0U, ID, 0U, 0U,
         wakeup::RESET, false},
        {"diagnostic wake-up", UJA113X_RSS_DIAGNOSTIC_WAKEUP, 0U, 0U, 0U, 0U, ID, 0U, 1U, wakeup::UNKNOWN, true},
        {"wake-up from FSP", UJA113X_RSS_WAKEUP_FROM_FSP, 0U, 0U, 0U, 0U, ID, 0U, 1U, wakeup::UNKNOWN, true},
        {"RCC 3 (three SBC resets)", UJA113X_RSS_RSTN_PULLED_DOWN, 0U, 0U, 0U, 0U, ID, 0U, 3U, wakeup::RESET,
         true},
    };
    for (Case const& c : cases)
    {
        Sbc sbc(c.identification);
        sbc.fake.resetSource(c.rss);
        sbc.fake.reg[UJA113X_REG_SYSTEM_IRQ_STATUS]      = c.system;
        sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = c.transceiver;
        sbc.fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_STATUS]  = c.wake;
        sbc.fake.reg[UJA113X_REG_BANK1_WAKE_IRQ_STATUS]  = c.wakeBank1;
        sbc.fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL]      = c.rcc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, c.canIndex);
        uint32_t const sources = supply.getStartupWakeupSources();
        bool const restarted   = supply.restartedForThisStart();
        if ((sources != c.expected) || (restarted != c.restarted))
        {
            std::printf("  %s: 0x%08x restarted %d, expected 0x%08x restarted %d\n", c.name,
                        static_cast<unsigned>(sources), restarted ? 1 : 0, static_cast<unsigned>(c.expected),
                        c.restarted ? 1 : 0);
        }
        CHECK(sources == c.expected);
        CHECK(restarted == c.restarted);
        CHECK(sbc.fake.global() == 0U); // the startup cleared the interrupts: a second start sees RESET
        // the startup writes RCC back to its start value (0): the base of the next start (NVM-34)
        CHECK((sbc.fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_RCC_MASK) == 0U);
        CHECK(supply.takeWakeupEvents() == 0U);
    }
}

/**
 * NVM-34, board finding 2026-10-02 (NVM-05 step 5): one SBC, several starts of the MCU, as on the eval board. JP1
 * connects RSTN of the SBC with the reset of the MCU. Model of the SBC side: an MCU reset with JP1 closed pulls RSTN
 * (Reset mode: RSS 17, RCC + 1; the compensation of the two MCU watchdog test resets leaves that one count, SYS-07);
 * with JP1 open the SBC sees nothing and stays in Normal with its registers of the last start.
 */
struct StartResult
{
    uint32_t sources;
    bool restarted;
};

StartResult startMcu(LeanUja113x& fake, ::sbc::Uja113xConfig const& cfg)
{
    ::sbc::Uja113x driver(fake, cfg);
    CHECK(driver.startup(0U) == ::sbc::Uja113x::Status::OK);
    ::sbc::Uja113xPowerSupply supply(driver, 0U);
    return StartResult{supply.getStartupWakeupSources(), supply.restartedForThisStart()};
}

void jp1OpenAndClosed()
{
    LeanUja113x fake;
    ::sbc::Uja113xConfig const cfg = config();
    // power-on: POSI pending, RSS 0
    fake.resetSource(UJA113X_RSS_EXIT_OFF);
    fake.reg[UJA113X_REG_SYSTEM_IRQ_STATUS] = UJA113X_POSI_MASK;
    StartResult s = startMcu(fake, cfg);
    CHECK((s.sources == wakeup::POWER) && s.restarted);
    CHECK(fake.reg[UJA113X_REG_MODE_CONTROL] == UJA113X_MC_NORMAL);
    // "lc reboot" with JP1 open: the SBC did not see it - RSS still 0 (power-on), RCC 0
    s = startMcu(fake, cfg);
    CHECK((s.sources == wakeup::RESET) && !s.restarted);
    // "lc reboot" with JP1 closed: RSS 17, RCC 1 -> the supply row counts RESET
    fake.resetSource(UJA113X_RSS_RSTN_PULLED_DOWN);
    fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = 1U;
    fake.reg[UJA113X_REG_MODE_CONTROL]      = UJA113X_MC_STANDBY;
    s = startMcu(fake, cfg);
    CHECK((s.sources == wakeup::RESET) && s.restarted);
    // JP1 opened again: RSS 17 of the previous start, RCC 0 -> MCU-only
    s = startMcu(fake, cfg);
    CHECK((s.sources == wakeup::RESET) && !s.restarted);
    // a watchdog reset of the SBC (RSS 15, RCC + 1), then "lc reboot" with JP1 open: not a second watchdog reset
    fake.resetSource(UJA113X_RSS_WD_OVERFLOW);
    fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = 1U;
    fake.reg[UJA113X_REG_MODE_CONTROL]      = UJA113X_MC_STANDBY;
    s = startMcu(fake, cfg);
    CHECK((s.sources == wakeup::WATCHDOG) && s.restarted);
    s = startMcu(fake, cfg);
    CHECK((s.sources == wakeup::RESET) && !s.restarted);
    // the combined start reason of the ECU state management stays an MCU reset
    CHECK(combineStartReasons(s.sources, wakeup::RESET) == wakeup::RESET);
}

/** NVM-34: a supply that cannot tell keeps the default of the interface - every start counts as its start. */
void defaultRestarted()
{
    PermanentPowerSupply permanent;
    IPowerSupply const& role = permanent;
    CHECK(role.restartedForThisStart());
}

void failedStartup()
{
    Sbc sbc;
    sbc.fake.reg[UJA113X_REG_IDENTIFICATION_0] = 0x49U; // other device
    sbc.fake.reg[UJA113X_REG_SYSTEM_IRQ_STATUS] = UJA113X_POSI_MASK;
    CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::WRONG_DEVICE);
    ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
    CHECK(supply.getStartupWakeupSources() == wakeup::UNKNOWN);
    CHECK(!supply.isPowerDownSupported());
    CHECK(supply.powerDown(10U, CAN0_ONLY) == PowerDownResult::NOT_SUPPORTED);
    CHECK(!sbc.fake.sleeping);
    CHECK(std::strcmp(supply.getName(), "UJA113x") == 0);
    // SYS-02: a driver that is not up offers no wake-up source, so the ECU does not even try to sleep
    CHECK(supply.getSupportedWakeupSources() == wakeup::NONE);
    CHECK(supply.getCyclicWakeupMs(100U) == 0U);
}

void sleepNotAllowedByMtpnv()
{
    Sbc sbc;
    sbc.fake.reg[UJA113X_REG_SBC_CONFIG_CONTROL] = 0x07U; // SLPC = 1: Sleep not supported
    CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
    ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
    CHECK(!supply.isPowerDownSupported());
    CHECK(supply.powerDown(10U, CAN0_ONLY) == PowerDownResult::NOT_SUPPORTED);
    CHECK(!sbc.fake.sleeping && !sbc.fake.illegalSleep);
}

void powerDown()
{
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.isPowerDownSupported());
        CHECK(supply.powerDown(10U, CAN0_ONLY) == PowerDownResult::STARTED);
        CHECK(sbc.fake.sleeping);
        CHECK(!sbc.fake.illegalSleep);
        CHECK((sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) != 0U);
        CHECK(sbc.fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U); // no local input selected
        CHECK(!supply.isPowerDownSupported()); // driver no longer in Normal
    }
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = UJA113X_CFI_MASK; // arrives before the Sleep
        CHECK(supply.powerDown(10U, CAN0_ONLY) == PowerDownResult::WAKEUP_PENDING);
        CHECK(!sbc.fake.sleeping);
        CHECK((sbc.fake.reg[UJA113X_REG_MODE_CONTROL] & UJA113X_MC_MASK) == UJA113X_MC_NORMAL);
        CHECK((sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U); // CWI removed
        CHECK(supply.isPowerDownSupported());
    }
}

/* ---- SYS-02: which wake-up sources this board offers and what the SBC arms for them ---- */

void offeredWakeupSources()
{
    // Bus of the integrated transceiver, the two wired WAKE inputs of this board and the cyclic wake-up
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 1U, 0x05U); // inputs 0 and 2 wired
        CHECK(supply.getSupportedWakeupSources()
              == (wakeup::canBus(1U) | wakeup::local(0U) | wakeup::local(2U) | wakeup::TIMER));
    }
    // Transceiver of the SBC not used by any bus: no CAN wake-up on offer
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0xFFU);
        CHECK(supply.getSupportedWakeupSources() == wakeup::TIMER);
    }
    // Forced Normal mode (MTPNV FNMC = 1): the watchdog is off, there is no cyclic wake-up
    {
        Sbc sbc;
        sbc.fake.reg[UJA113X_REG_WATCHDOG_STATUS] = UJA113X_FNMS_MASK | UJA113X_SDMS_MASK;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.getSupportedWakeupSources() == wakeup::canBus(0U));
        CHECK(supply.getCyclicWakeupMs(100U) == 0U);
    }
    // Grid of the SBC: the watchdog periods, rounded up, 4096 ms at the top
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.getCyclicWakeupMs(0U) == 0U);
        CHECK(supply.getCyclicWakeupMs(1U) == 8U);
        CHECK(supply.getCyclicWakeupMs(128U) == 128U);
        CHECK(supply.getCyclicWakeupMs(129U) == 256U);
        CHECK(supply.getCyclicWakeupMs(300U) == 1024U);
        CHECK(supply.getCyclicWakeupMs(100000U) == 4096U);
    }
}

void armedWakeupSources()
{
    // Local input: enabled in register 0x37, CAN wake-up stays off if the project did not select it
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U, 0x05U);
        CHECK(supply.powerDown(10U, WakeupSelection{wakeup::local(2U), 0U}) == PowerDownResult::STARTED);
        CHECK(sbc.fake.sleeping);
        CHECK(!sbc.fake.illegalSleep);
        // Input index 2 is HVIO3: bits 4 and 5 of register 0x37 (both edges, none configured) - IO-10
        CHECK(sbc.fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0x30U);
        CHECK((sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
    }
    // An input the board does not wire is not armed, even if the selection still names it
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U, 0x01U); // only input 0 wired
        CHECK(supply.powerDown(10U, WakeupSelection{wakeup::canBus(0U) | wakeup::local(3U), 0U})
              == PowerDownResult::STARTED);
        CHECK(sbc.fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U);
        CHECK((sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) != 0U);
    }
    // Cyclic wake-up: the watchdog keeps running in Sleep as timeout watchdog with the selected period
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.powerDown(10U, WakeupSelection{wakeup::canBus(0U) | wakeup::TIMER, 1024U})
              == PowerDownResult::STARTED);
        CHECK(sbc.fake.sleeping);
        CHECK(!sbc.fake.illegalSleep);
        uint8_t const watchdog = sbc.fake.reg[UJA113X_REG_WATCHDOG_CONTROL];
        CHECK(((watchdog & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT) == UJA113X_WMC_TIMEOUT);
        CHECK((watchdog & UJA113X_NWP_MASK) == UJA113X_NWP_1024MS);
    }
    // Without a cyclic wake-up the watchdog is switched off for the Sleep, as before SYS-02
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.powerDown(10U, CAN0_ONLY) == PowerDownResult::STARTED);
        uint8_t const watchdog = sbc.fake.reg[UJA113X_REG_WATCHDOG_CONTROL];
        CHECK(((watchdog & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT) == UJA113X_WMC_AUTONOMOUS);
    }
    // Nothing selected: the SBC would reset instead of sleeping, so the driver refuses
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.powerDown(10U, WakeupSelection{wakeup::NONE, 0U}) == PowerDownResult::NOT_SUPPORTED);
        CHECK(!sbc.fake.sleeping);
        CHECK(!sbc.fake.illegalSleep);
        CHECK((sbc.fake.reg[UJA113X_REG_MODE_CONTROL] & UJA113X_MC_MASK) == UJA113X_MC_NORMAL);
    }
    // Abort with a pending interrupt: the armed sources are taken back again
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U, 0x0FU);
        sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = UJA113X_CFI_MASK;
        CHECK(supply.powerDown(10U, WakeupSelection{wakeup::canBus(0U) | wakeup::local(1U) | wakeup::TIMER, 64U})
              == PowerDownResult::WAKEUP_PENDING);
        CHECK(!sbc.fake.sleeping);
        CHECK(sbc.fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U);
        CHECK((sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
        uint8_t const watchdog = sbc.fake.reg[UJA113X_REG_WATCHDOG_CONTROL];
        CHECK(((watchdog & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT) == UJA113X_WMC_AUTONOMOUS);
        CHECK((sbc.fake.reg[UJA113X_REG_MODE_CONTROL] & UJA113X_MC_MASK) == UJA113X_MC_NORMAL);
        CHECK(supply.isPowerDownSupported());
    }
}

/* ECU state controller with the SBC as power supply: the same flow as without SBC ends in SBC Sleep. */
class Lifecycle final : public ILifecycleControl
{
public:
    uint8_t level  = 0U;
    uint8_t target = 9U;
    std::vector<uint8_t> requests;
    EcuStateController* listener = nullptr;

    void transitionToLevel(uint8_t const l) override
    {
        requests.push_back(l);
        target = l;
    }

    void step()
    {
        if (level != target)
        {
            bool const down = level > target;
            level           = down ? static_cast<uint8_t>(level - 1U) : static_cast<uint8_t>(level + 1U);
            listener->lifecycleLevelReached(level, down);
        }
    }
};

void controllerWithSbc()
{
    // SYS-02: step limits 100/100 ms (no provider, so they never apply), wake-up over the CAN bus of the SBC
    constexpr EcuStateConfig cfg{
        200U,
        100U,
        1000U,
        400U,
        {100U, 100U},
        WakeupSelection{wakeup::canBus(0U), 0U},
        WakeupDuringShutdown::RESTART,
        AfterWatchdogReset::RUN,
        ShutdownStepError::RESTART};
    for (bool const interruptBeforeSleep : {false, true})
    {
        ::ecustate::rte::reset();
        Sbc sbc;
        sbc.fake.resetSource(UJA113X_RSS_CAN_WAKEUP);
        sbc.fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = UJA113X_CWI_MASK;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        Lifecycle lifecycle;
        ShutdownSequence sequence; // this project has no provider for any run-down step
        EcuStateController controller(cfg, supply, lifecycle, sequence, 9U, 1U);
        lifecycle.listener = &controller;
        ::ecustate::rte::writeSleepRequest(true);
        uint32_t now = 0U;
        bool injected = false;
        for (; now < 2000U; now += 10U)
        {
            lifecycle.step();
            sbc.driver.cyclic(now);
            if (interruptBeforeSleep && (controller.getStateMachine().getState() == State::SLEEP))
            {
                // The driver cycle clears interrupts every 10 ms; only one arriving in the same cycle as the
                // delayed power down is still pending when enterSleep() checks.
                sbc.fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
                injected = true;
            }
            controller.cyclic(now);
            if (sbc.fake.sleeping || (!lifecycle.requests.empty() && (lifecycle.requests.back() == 0U)))
            {
                break;
            }
        }
        uint32_t wake = 0U;
        ::ecustate::rte::readWakeupSources(wake);
        CHECK(wake == wakeup::canBus(0U));
        CHECK(!sbc.fake.illegalSleep);
        if (!interruptBeforeSleep)
        {
            CHECK(sbc.fake.sleeping);
            CHECK(controller.getPowerDownResult() == PowerDownResult::STARTED);
            CHECK(lifecycle.level == 1U);
            CHECK(now >= 200U + 100U + 100U); // min run, post-run, log delay
        }
        else
        {
            CHECK(injected);
            CHECK(!sbc.fake.sleeping);
            CHECK(controller.getPowerDownResult() == PowerDownResult::WAKEUP_PENDING);
            CHECK(!lifecycle.requests.empty() && (lifecycle.requests.back() == 0U)); // restart
        }
    }
}

} // namespace

/* IO-33: the wake-up inputs the driver configuration sets up (the port builds them from board.yaml devices[].pins)
 * are offered and armed without a mask from the integration. */
void wakeInputsOfferedFromConfiguration()
{
    ::sbc::HvioConfig const wake{::sbc::HvioFunction::WAKE_INPUT, false, ::sbc::HvioActivation::ON,
                                 ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U};
    {
        Sbc sbc;
        sbc.cfg.hvio[0].configure = true;
        sbc.cfg.hvio[0].pin[1]    = wake; // HVIO2 = LOCAL1
        sbc.cfg.hvio[0].pin[3]    = wake; // HVIO4 = LOCAL3
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.getSupportedWakeupSources()
              == (wakeup::canBus(0U) | wakeup::local(1U) | wakeup::local(3U) | wakeup::TIMER));
        // Only the selected input is armed, with both edges; an input the board does not wire is dropped
        CHECK(supply.powerDown(0U, WakeupSelection{wakeup::local(1U) | wakeup::local(0U), 0U})
              == PowerDownResult::STARTED);
        CHECK(sbc.fake.sleeping);
        CHECK(sbc.fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0x0CU);
    }
    // Without wiring nothing local is offered (as before IO-33)
    {
        Sbc sbc;
        CHECK(sbc.driver.startup(0U) == ::sbc::Uja113x::Status::OK);
        ::sbc::Uja113xPowerSupply supply(sbc.driver, 0U);
        CHECK(supply.getSupportedWakeupSources() == (wakeup::canBus(0U) | wakeup::TIMER));
    }
}

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"startReasons", startReasons},
        {"jp1OpenAndClosed", jp1OpenAndClosed},
        {"defaultRestarted", defaultRestarted},
        {"failedStartup", failedStartup},
        {"sleepNotAllowedByMtpnv", sleepNotAllowedByMtpnv},
        {"powerDown", powerDown},
        {"offeredWakeupSources", offeredWakeupSources},
        {"armedWakeupSources", armedWakeupSources},
        {"controllerWithSbc", controllerWithSbc},
        {"wakeInputsOfferedFromConfiguration", wakeInputsOfferedFromConfiguration},
    };
    for (auto const& test : tests)
    {
        int const before = failures;
        test.fn();
        std::printf("%s %s\n", (failures == before) ? "ok  " : "FAIL", test.name);
    }
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
