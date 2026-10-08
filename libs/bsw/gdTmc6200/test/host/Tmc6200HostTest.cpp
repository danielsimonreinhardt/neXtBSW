/* Host test of the TMC6200 driver against a register model of the device (datasheet Rev. 1.08).
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/gdTmc6200/include libs/bsw/gdTmc6200/src/gd/Tmc6200.cpp \
 *       libs/bsw/gdTmc6200/test/host/Tmc6200HostTest.cpp -o build/host/tmc6200_test.exe
 *   build/host/tmc6200_test.exe
 */

#include "gd/Tmc6200.h"

#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                                  \
    do                                                                               \
    {                                                                                \
        ++checks;                                                                    \
        if (!(cond))                                                                 \
        {                                                                            \
            ++failures;                                                              \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);            \
        }                                                                            \
    } while (false)

using ::gd::KEEP;
using ::gd::Tmc6200;
using Status = ::gd::Tmc6200::Status;
using State  = ::gd::Tmc6200::State;
namespace fault = ::gd::fault;

uint32_t const SHORT_CONF_RESET = TMC6200_SHORT_CONF_RESET_WITHOUT_LEVELS | 0x0606UL; // OTP levels 6 (otp0.5 = 0)
uint32_t const DRV_CONF_RESET   = TMC6200_DRV_CONF_RESET_WITHOUT_BBM | 4UL;          // OTP BBM 4 (otp0.7..6 = 0)

/* Register model with the behaviour the driver relies on, independent of the driver's helpers:
 * frame layout p.18, reset values p.22-26, write-1-to-clear GSTAT p.22, DRV_EN cycling p.22, IOIN p.23. */
class FakeTmc6200
: public ::gd::ITmc6200Spi
, public ::gd::ITmc6200Io
{
public:
    uint32_t reg[128];
    bool spiFails        = false;
    bool sdoStuckHigh    = false;
    bool unpowered       = false; // VS missing (IO-29 board test): SDO reads 0, writes have no effect
    uint8_t unpoweredStatusByte = 0U; // IO-67: something drives the status byte although the data reads 0
    bool pinWriteFails   = false;
    bool drvEnWired      = true;  // DRV_EN of the MCU reaches the device
    bool faultWired      = true;
    bool ignoreWritesTo00 = false; // GCONF does not take a value (read-back mismatch)
    bool hot             = false;  // temperature above the limit: drv_ot does not clear
    /* IO-44: GSTAT bits whose condition is still present. Clearing a flag does not end the condition (p.22
     * "Switch off the affected MOSFET by its HS/LS input to clear a pending short condition"), so the device sets
     * them again - the board saw exactly this: "gd clear" followed by faults 0x0800 again (captures 45/46). */
    uint32_t stickyGstat = 0U;
    bool drvEnPin        = false;  // level of the MCU output
    uint8_t lastAddressByte = 0U;
    uint32_t enableHighCount = 0U; // setDriverEnable(true) calls
    std::vector<uint8_t> writes;   // addresses of write datagrams
    std::vector<char> sequence;    // 'E' enable pin write, 'S' SPI transfer
    std::vector<uint64_t> frames;

    FakeTmc6200() { powerOn(); }

    void powerOn()
    {
        std::memset(reg, 0, sizeof(reg));
        reg[TMC6200_REG_GCONF]        = TMC6200_GCONF_RESET;
        reg[TMC6200_REG_GSTAT]        = TMC6200_GSTAT_RESET_MASK;
        reg[TMC6200_REG_IOIN]         = static_cast<uint32_t>(TMC6200_VERSION) << 24U;
        reg[TMC6200_REG_OTP_READ]     = 0x0FU; // factory clock trim only
        reg[TMC6200_REG_FACTORY_CONF] = 0x0FU;
        reg[TMC6200_REG_SHORT_CONF]   = SHORT_CONF_RESET;
        reg[TMC6200_REG_DRV_CONF]     = DRV_CONF_RESET;
    }

    bool deviceDrvEn() const { return drvEnWired && drvEnPin; }

    /* FAULT output: ORed status bits (p.22-23 "ORed to STATUS output"). IO-44: the short counters shortdet_u/v/w
     * only reach the pin with GCONF faultdirect = 1 - p.22 says faultdirect 0 = "Fault output active when at
     * least one bridge is shut down continuously", 1 = "shows each protective action of the overcurrent
     * shutdown", and a triggered short counter is exactly a protective action that was taken back by a retry.
     * Board 2026-09-23 (captures 45-47, faultdirect at the reset value 0): GSTAT 0x1000 / 0x0110 / 0x0010 was
     * reported as fault 0x0800 - without the FAULT pin bit 0x1000. */
    static constexpr uint32_t SHORTDET_MASK
        = TMC6200_GSTAT_SHORTDET_U_MASK | TMC6200_GSTAT_SHORTDET_V_MASK | TMC6200_GSTAT_SHORTDET_W_MASK;

    bool faultOutput() const
    {
        uint32_t mask = TMC6200_GSTAT_DRV_OT_MASK | TMC6200_GSTAT_UV_CP_MASK | (0x7770UL & ~SHORTDET_MASK);
        if ((reg[TMC6200_REG_GCONF] & TMC6200_GCONF_FAULTDIRECT_MASK) != 0U)
        {
            mask |= SHORTDET_MASK;
        }
        return (reg[TMC6200_REG_GSTAT] & mask) != 0U;
    }

    bool transfer(uint64_t const tx, uint64_t& rx) override
    {
        sequence.push_back('S');
        frames.push_back(tx);
        if (spiFails)
        {
            return false;
        }
        if (sdoStuckHigh)
        {
            rx = 0xFFFFFFFFFFULL;
            return true;
        }
        if (unpowered)
        {
            if ((tx & 0x8000000000ULL) != 0U)
            {
                writes.push_back(static_cast<uint8_t>((tx >> 32U) & 0x7FU));
            }
            // as seen on the eval board 2026-09-18 and 2026-09-30: IOIN 0x00000000, every register 0
            rx = static_cast<uint64_t>(unpoweredStatusByte) << 32U;
            return true;
        }
        CHECK((tx >> 40U) == 0U); // 40-bit datagram
        uint8_t const addressByte = static_cast<uint8_t>(tx >> 32U);
        uint8_t const address     = addressByte & 0x7FU;
        uint32_t const data       = static_cast<uint32_t>(tx);
        uint32_t value            = reg[address];
        if (address == TMC6200_REG_IOIN)
        {
            value = (value & ~TMC6200_IOIN_DRV_EN_MASK) | (deviceDrvEn() ? TMC6200_IOIN_DRV_EN_MASK : 0U);
        }
        rx              = (static_cast<uint64_t>(lastAddressByte) << 32U) | value;
        lastAddressByte = addressByte;
        if ((addressByte & 0x80U) != 0U)
        {
            writes.push_back(address);
            write(address, data);
        }
        return true;
    }

    bool setDriverEnable(bool const on) override
    {
        sequence.push_back('E');
        if (pinWriteFails)
        {
            return false;
        }
        if (on)
        {
            ++enableHighCount;
        }
        bool const before = deviceDrvEn();
        drvEnPin          = on;
        if (before && !deviceDrvEn())
        {
            // p.22: cycling DRV_EN clears all flags except reset and drv_otpw (drv_ot only when cool)
            uint32_t clear = TMC6200_GSTAT_CLEARED_BY_DRV_EN_MASK;
            if (hot)
            {
                clear &= ~TMC6200_GSTAT_DRV_OT_MASK;
            }
            reg[TMC6200_REG_GSTAT] &= ~clear;
            reg[TMC6200_REG_GSTAT] |= stickyGstat; // IO-44: a condition that is still there comes back
        }
        return true;
    }

    bool readFault(bool& active) override
    {
        active = faultOutput();
        return faultWired;
    }

    void write(uint8_t const address, uint32_t const data)
    {
        switch (address)
        {
            case TMC6200_REG_GCONF:
                if (!ignoreWritesTo00)
                {
                    reg[address] = data & TMC6200_GCONF_WRITABLE_MASK;
                }
                break;
            case TMC6200_REG_GSTAT:
            {
                uint32_t clear = data & TMC6200_GSTAT_ALL_MASK;
                if (!deviceDrvEn())
                {
                    clear &= ~TMC6200_GSTAT_RESET_MASK; // "DRV_EN must be high to allow clearing reset"
                }
                if (hot)
                {
                    clear &= ~TMC6200_GSTAT_DRV_OT_MASK;
                }
                reg[address] &= ~clear;
                reg[address] |= stickyGstat; // IO-44: the condition is still there, the device reports it again
                break;
            }
            case TMC6200_REG_SHORT_CONF: reg[address] = data & TMC6200_SHORT_CONF_WRITABLE_MASK; break;
            case TMC6200_REG_DRV_CONF: reg[address] = data & TMC6200_DRV_CONF_WRITABLE_MASK; break;
            case TMC6200_REG_FACTORY_CONF: reg[address] = data & 0x1FU; break;
            default: break; // read-only registers ignore writes; OTP_PROG is only recorded
        }
    }

    bool wrote(uint8_t const address) const
    {
        for (uint8_t a : writes)
        {
            if (a == address)
            {
                return true;
            }
        }
        return false;
    }
};

::gd::Tmc6200Config const KEEP_ALL = ::gd::keepAllConfig();

/* A configuration that changes one field in each register (values differ from the reset values). */
::gd::Tmc6200Config configured()
{
    ::gd::Tmc6200Config c    = ::gd::keepAllConfig();
    c.interfaceMode          = 0; // individual high/low side signals (reset: single line)
    c.overtemperatureSelect  = 2; // 136 C
    c.shortRetries           = 1;
    return c;
}

/* Tmc6200 keeps a reference to its configuration: never pass the temporary of configured() (dangling, IO-34). */
::gd::Tmc6200Config const CONFIGURED = configured();

void initIsReadOnlyWithKeepConfig()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, KEEP_ALL);
    CHECK(drv.getState() == State::UNINITIALIZED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(fake.writes.empty());
    CHECK(drv.getStartupInfo().writes == 0U);
    CHECK(!fake.drvEnPin);
    CHECK(fake.enableHighCount == 0U);
    CHECK((drv.getStartupInfo().gstat & TMC6200_GSTAT_RESET_MASK) != 0U); // power-on flag reported
    CHECK(drv.getStartupInfo().gconf == TMC6200_GCONF_RESET);
    CHECK(drv.getStartupInfo().shortConf == SHORT_CONF_RESET);
    CHECK(drv.getStartupInfo().drvConf == DRV_CONF_RESET);
    CHECK(drv.getFaults() == 0U); // reset flag is information, not a fault
    // cyclic supervision reads only as well
    for (uint32_t t = 10U; t <= 2000U; t += 10U)
    {
        drv.cyclic(t);
    }
    CHECK(fake.writes.empty());
    CHECK(drv.getState() == State::DISABLED);
}

void driverEnableIsLowAfterInit()
{
    FakeTmc6200 fake;
    fake.drvEnPin = true; // e.g. left on by a previous software before a controller reset
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(!fake.drvEnPin);
    CHECK(!drv.isEnabled());
    // DRV_EN is switched off before the first datagram (p.34: disable, then re-initialize)
    CHECK(!fake.sequence.empty() && (fake.sequence[0] == 'E'));
    CHECK(fake.enableHighCount == 0U);
}

void datagramFormat()
{
    // Datasheet p.18 example: read GSTAT -> 0x0100000000, write GCONF := 0x10 -> 0x8000000010
    CHECK(Tmc6200::readDatagram(TMC6200_REG_GSTAT) == 0x0100000000ULL);
    CHECK(Tmc6200::writeDatagram(TMC6200_REG_GCONF, 0x10U) == 0x8000000010ULL);
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, KEEP_ALL);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(fake.frames.size() >= 1U);
    CHECK(fake.frames[0] == 0x0400000000ULL); // first access: IOIN (version check)
    // the status byte of the reply is not used as data
    uint32_t v = 0U;
    CHECK(drv.readRegister(TMC6200_REG_GSTAT, v) == Status::OK);
    CHECK(v == TMC6200_GSTAT_RESET_MASK);
}

void wrongVersionIsNotWritten()
{
    FakeTmc6200 fake;
    fake.reg[TMC6200_REG_IOIN] = 0x11UL << 24U;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::WRONG_DEVICE);
    CHECK(drv.getState() == State::FAILED);
    CHECK(fake.writes.empty());
    CHECK(!fake.drvEnPin);
    CHECK((drv.getFaults() & fault::COMMUNICATION) != 0U);
    CHECK(drv.enable() == Status::NOT_READY);
    CHECK(fake.enableHighCount == 0U);
}

void spiFailureIsRetried()
{
    FakeTmc6200 fake;
    fake.spiFails = true;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::SPI_ERROR);
    CHECK(drv.getState() == State::FAILED);
    uint32_t const attempts = drv.getCounters().startupAttempts;
    drv.cyclic(500U);
    CHECK(drv.getCounters().startupAttempts == attempts); // not before RETRY_MS
    fake.spiFails = false;
    drv.cyclic(1000U);
    CHECK(drv.getCounters().startupAttempts == attempts + 1U);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(fake.enableHighCount == 0U); // a successful retry does not enable
    // the communication fault stays latched until the application acknowledges it
    CHECK((drv.getLatchedFaults() & fault::COMMUNICATION) != 0U);
    CHECK((drv.getFaults() & fault::COMMUNICATION) == 0U);
    CHECK(drv.enable() == Status::NOT_ALLOWED);
    CHECK(drv.clearFaults() == Status::OK);
    CHECK(drv.enable() == Status::OK);
}

void stuckSdoIsSpiError()
{
    FakeTmc6200 fake;
    fake.sdoStuckHigh = true;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::SPI_ERROR);
    CHECK(fake.writes.empty());
    CHECK(drv.getCounters().spiErrors == 1U);
}

void configuredFieldsAreWrittenAndVerified()
{
    FakeTmc6200 fake;
    ::gd::Tmc6200Config const config = configured();
    Tmc6200 drv(fake, fake, config);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.getStartupInfo().writes == 3U);
    CHECK(fake.writes.size() == 3U);
    CHECK(fake.reg[TMC6200_REG_GCONF] == 0x00U); // singleline 0, rest untouched
    CHECK(fake.reg[TMC6200_REG_SHORT_CONF] == ((SHORT_CONF_RESET & ~TMC6200_SHORT_CONF_RETRY_MASK) | 0x01000000UL));
    CHECK(fake.reg[TMC6200_REG_DRV_CONF] == ((DRV_CONF_RESET & ~TMC6200_DRV_CONF_OTSELECT_MASK) | 0x00020000UL));
    // GCONF is written before the protection and driver settings (p.6)
    CHECK(fake.writes[0] == TMC6200_REG_GCONF);
    // never: OTP programming, clock trim, protection disable bits, test mode
    CHECK(!fake.wrote(TMC6200_REG_OTP_PROG));
    CHECK(!fake.wrote(TMC6200_REG_FACTORY_CONF));
    CHECK((fake.reg[TMC6200_REG_SHORT_CONF] & 0x60000000UL) == 0U);
    CHECK((fake.reg[TMC6200_REG_GCONF] & TMC6200_GCONF_TEST_MODE_MASK) == 0U);

    // A second startup (controller reset) with the device already configured writes nothing.
    fake.writes.clear();
    Tmc6200 again(fake, fake, config);
    CHECK(again.init(100U) == Status::OK);
    CHECK(fake.writes.empty());

    // Every field of every register
    FakeTmc6200 full;
    ::gd::Tmc6200Config all   = ::gd::keepAllConfig();
    all.interfaceMode         = 0;
    all.faultDirect           = 1;
    all.amplification         = 3;
    all.amplifierOff          = 1;
    all.shortToSupplyLevel    = 9;
    all.shortToGroundLevel    = 14;
    all.shortFilter           = 2;
    all.shortDelay            = 1;
    all.shortRetries          = 0;
    all.protectParallel       = 0;
    all.breakBeforeMakeClocks = 7;
    all.overtemperatureSelect = 3;
    all.driveStrength         = 1;
    CHECK(::gd::isValidTmc6200Config(all));
    Tmc6200 fullDrv(full, full, all);
    CHECK(fullDrv.init(0U) == Status::OK);
    CHECK(full.reg[TMC6200_REG_GCONF] == 0x74UL);
    CHECK(full.reg[TMC6200_REG_SHORT_CONF] == 0x00120E09UL);
    CHECK(full.reg[TMC6200_REG_DRV_CONF] == 0x00070007UL);
}

void verifyErrorFailsStartup()
{
    FakeTmc6200 fake;
    fake.ignoreWritesTo00 = true;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::VERIFY_ERROR);
    CHECK(drv.getState() == State::FAILED);
    CHECK(drv.getCounters().verifyErrors == 1U);
    CHECK(!fake.wrote(TMC6200_REG_SHORT_CONF)); // stops at the first mismatch
    CHECK(drv.enable() == Status::NOT_READY);
    CHECK(!fake.drvEnPin);
}

void testModeIsDeviceError()
{
    FakeTmc6200 fake;
    fake.reg[TMC6200_REG_GCONF] |= TMC6200_GCONF_TEST_MODE_MASK;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::DEVICE_ERROR);
    CHECK(fake.writes.empty());
    CHECK((drv.getFaults() & fault::TEST_MODE) != 0U);
    CHECK(drv.enable() == Status::NOT_READY);
}

void neverEnablesByItself()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    uint32_t t = 0U;
    auto run = [&](uint32_t const ms) {
        for (uint32_t end = t + ms; t < end; t += 10U)
        {
            drv.cyclic(t);
        }
    };
    run(3000U);
    // faults come and go, SPI fails and recovers, the device resets, faults are acknowledged
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_UV_CP_MASK;
    run(500U);
    fake.reg[TMC6200_REG_GSTAT] &= ~TMC6200_GSTAT_UV_CP_MASK;
    CHECK(drv.clearFaults() == Status::OK);
    run(500U);
    fake.spiFails = true;
    run(1500U);
    fake.spiFails = false;
    run(1500U);
    // acknowledged while the SPI still fails: the successful retry has nothing latched and still stays off
    fake.spiFails = true;
    run(1500U);
    CHECK(drv.getState() == State::FAILED);
    CHECK(drv.clearFaults() == Status::OK);
    fake.spiFails = false;
    run(1500U);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(drv.getLatchedFaults() == 0U);
    fake.powerOn();
    run(1500U);
    CHECK(drv.clearFaults() == Status::OK);
    run(3000U);
    CHECK(fake.enableHighCount == 0U);
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::DISABLED);
}

void enableAndDisable()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.enable() == Status::NOT_READY); // before init
    CHECK(fake.enableHighCount == 0U);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    CHECK(drv.getState() == State::ENABLED);
    CHECK(fake.drvEnPin);
    // the power-on flag is cleared with DRV_EN high (p.22), so a later device reset is visible
    CHECK((fake.reg[TMC6200_REG_GSTAT] & TMC6200_GSTAT_RESET_MASK) == 0U);
    for (uint32_t t = 10U; t <= 1000U; t += 10U)
    {
        drv.cyclic(t);
    }
    CHECK(drv.getState() == State::ENABLED);
    CHECK(drv.getFaults() == 0U);
    CHECK(drv.disable() == Status::OK);
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(drv.getCounters().enables == 1U);
}

void enableRefusedWithPendingFaults()
{
    FakeTmc6200 fake;
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_UV_CP_MASK; // e.g. bridge supply not present at startup
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK((drv.getFaults() & fault::CHARGE_PUMP_UNDERVOLTAGE) != 0U);
    CHECK(drv.enable() == Status::NOT_ALLOWED);
    CHECK(fake.enableHighCount == 0U);
    // acknowledged: GSTAT cleared by write 1 (p.22), then enabling works
    CHECK(drv.clearFaults() == Status::OK);
    CHECK((fake.reg[TMC6200_REG_GSTAT] & TMC6200_GSTAT_UV_CP_MASK) == 0U);
    CHECK(drv.enable() == Status::OK);
    // warnings do not block
    CHECK(drv.disable() == Status::OK);
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_DRV_OTPW_MASK;
    drv.cyclic(10U);
    CHECK((drv.getFaults() & fault::OVERTEMPERATURE_WARNING) != 0U);
    CHECK(drv.enable() == Status::OK);
}

void shortWhileEnabledSwitchesOff()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    drv.cyclic(10U);
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_S2GV_MASK | TMC6200_GSTAT_SHORTDET_V_MASK;
    drv.cyclic(20U);
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(drv.getCounters().shutdowns == 1U);
    uint16_t const latched = drv.getLatchedFaults();
    CHECK((latched & fault::SHORT_TO_GROUND_V) != 0U);
    CHECK((latched & fault::SHORT_DETECTED) != 0U);
    CHECK((latched & fault::FAULT_PIN) != 0U);
    // DRV_EN low cleared the device flags (p.22), the driver keeps them latched
    drv.cyclic(30U);
    CHECK((drv.getFaults() & fault::SHORT_TO_GROUND_V) == 0U);
    CHECK((drv.getLatchedFaults() & fault::SHORT_TO_GROUND_V) != 0U);
    CHECK(drv.enable() == Status::NOT_ALLOWED);
    CHECK(drv.clearFaults() == Status::OK);
    CHECK(drv.getLatchedFaults() == 0U);
    CHECK(drv.enable() == Status::OK);
}

void overtemperatureShutdownAndWarning()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_DRV_OTPW_MASK;
    drv.cyclic(10U);
    CHECK(drv.isEnabled()); // prewarning: no action (p.22)
    CHECK((drv.getFaults() & fault::OVERTEMPERATURE_WARNING) != 0U);
    fake.hot = true;
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_DRV_OT_MASK;
    drv.cyclic(20U);
    CHECK(!drv.isEnabled());
    CHECK(!fake.drvEnPin);
    // still hot: the flag does not clear, enabling stays refused - and IO-44: the clear says so instead of "ok"
    CHECK(drv.clearFaults() == Status::NOT_CLEARED);
    CHECK((drv.getFaults() & fault::OVERTEMPERATURE_SHUTDOWN) != 0U);
    CHECK(drv.enable() == Status::NOT_ALLOWED);
    fake.hot = false;
    CHECK(drv.clearFaults() == Status::OK);
    CHECK(drv.enable() == Status::OK);
}

void deviceResetWhileEnabled()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    drv.cyclic(10U);
    // VCC_IO dropout: registers back to reset values, reset flag set (p.22, p.34)
    fake.writes.clear();
    fake.powerOn();
    drv.cyclic(20U);
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(drv.getCounters().deviceResets == 1U);
    CHECK((drv.getLatchedFaults() & fault::DEVICE_RESET) != 0U);
    // configuration written again
    CHECK(fake.reg[TMC6200_REG_GCONF] == 0x00U);
    CHECK(fake.writes.size() == 3U);
    CHECK(drv.enable() == Status::NOT_ALLOWED);
    CHECK(drv.clearFaults() == Status::OK);
    CHECK(drv.enable() == Status::OK);
}

void deviceResetWhileDisabled()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    drv.cyclic(100U);
    fake.powerOn(); // reset flag alone is not visible while DRV_EN is low: the register check finds it
    drv.cyclic(150U);
    CHECK(drv.getCounters().deviceResets == 0U); // next check only after CONFIG_CHECK_MS
    drv.cyclic(200U);
    CHECK(drv.getCounters().deviceResets == 1U);
    CHECK(fake.reg[TMC6200_REG_GCONF] == 0x00U);
    CHECK(fake.enableHighCount == 0U);
}

void enablePinMismatch()
{
    FakeTmc6200 fake;
    fake.drvEnWired = false;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::PIN_ERROR);
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::DISABLED);
    CHECK((drv.getLatchedFaults() & fault::ENABLE_MISMATCH) != 0U);

    // DRV_EN high at the device although the MCU drives it low (other driver, short to supply)
    FakeTmc6200 other;
    struct Stuck : ::gd::ITmc6200Spi
    {
        FakeTmc6200& f;
        explicit Stuck(FakeTmc6200& fake) : f(fake) {}
        bool transfer(uint64_t const tx, uint64_t& rx) override
        {
            bool const ok = f.transfer(tx, rx);
            if (((tx >> 32U) & 0x7FU) == TMC6200_REG_IOIN)
            {
                rx |= TMC6200_IOIN_DRV_EN_MASK;
            }
            return ok;
        }
    } stuck(other);
    Tmc6200 drv3(stuck, other, CONFIGURED);
    CHECK(drv3.init(0U) == Status::OK);
    CHECK((drv3.getFaults() & fault::ENABLE_MISMATCH) != 0U);
    CHECK(drv3.enable() == Status::NOT_ALLOWED);

    FakeTmc6200 pin;
    pin.pinWriteFails = true;
    Tmc6200 drv4(pin, pin, CONFIGURED);
    CHECK(drv4.init(0U) == Status::OK);
    CHECK(drv4.enable() == Status::PIN_ERROR);
    CHECK(drv4.getState() == State::DISABLED);
}

void spiErrorWhileEnabled()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    fake.spiFails = true;
    drv.cyclic(10U);
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::FAILED);
    CHECK(drv.getCounters().shutdowns == 1U);
    fake.spiFails = false;
    drv.cyclic(1010U);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(!fake.drvEnPin);
}

/* IO-29: device without supply VS (eval board 2026-09-18, IOIN 0x00000000). A failed startup costs exactly one
 * datagram (IOIN), repeated once per RETRY_MS, and writes nothing. */
void missingSupplyCostsOneDatagramPerRetry()
{
    FakeTmc6200 fake;
    fake.unpowered = true;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::NO_SUPPLY); // IO-67 (was WRONG_DEVICE)
    CHECK(drv.getStartupInfo().ioin == 0U);
    CHECK(fake.frames.size() == 1U);
    CHECK(!fake.frames.empty() && (fake.frames[0] == Tmc6200::readDatagram(TMC6200_REG_IOIN)));
    // 10 s in the 10 ms cycle of the board: one new attempt per RETRY_MS, one datagram each
    for (uint32_t t = 10U; t <= 10000U; t += 10U)
    {
        drv.cyclic(t);
    }
    CHECK(drv.getCounters().startupAttempts == 11U);
    CHECK(fake.frames.size() == 11U);
    for (uint64_t const frame : fake.frames)
    {
        CHECK(frame == Tmc6200::readDatagram(TMC6200_REG_IOIN));
    }
    CHECK(fake.writes.empty());
    CHECK(drv.getState() == State::FAILED);
    // IO-67: reported as missing supply, not as a communication fault
    CHECK(drv.getFaults() == fault::NO_SUPPLY);
    CHECK((drv.getLatchedFaults() & fault::COMMUNICATION) == 0U);
    CHECK(drv.getCounters().spiErrors == 0U);
    CHECK(drv.enable() == Status::NOT_READY);
    CHECK(!fake.drvEnPin);
    CHECK(fake.enableHighCount == 0U);
}

/* IO-29: VS connected while the driver retries: the next attempt (at most RETRY_MS later) finds the device,
 * configures it and stays off. Also pins the SPI load of the supervision that follows (board checklist). */
void supplyConnectedLaterIsDetected()
{
    FakeTmc6200 fake;
    fake.unpowered = true;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::NO_SUPPLY);
    for (uint32_t t = 10U; t <= 3000U; t += 10U)
    {
        drv.cyclic(t);
    }
    CHECK(drv.getState() == State::FAILED);
    CHECK(fake.writes.empty());
    // supply switched on after the attempt at 3000 ms: the device starts with its reset values
    fake.unpowered = false;
    fake.powerOn();
    uint32_t detectedMs = 0U;
    for (uint32_t t = 3010U; (t <= 5000U) && (detectedMs == 0U); t += 10U)
    {
        drv.cyclic(t);
        if (drv.getState() == State::DISABLED)
        {
            detectedMs = t;
        }
    }
    CHECK(detectedMs == 4000U); // the next regular attempt, RETRY_MS after the last one
    CHECK(fake.writes.size() == 3U); // the configured fields, as after a normal startup
    CHECK(fake.reg[TMC6200_REG_GCONF] == 0x00U);
    CHECK(!fake.drvEnPin);
    CHECK(fake.enableHighCount == 0U);
    // IO-67: nothing latched from the unpowered phase (before IO-67: COMMUNICATION until clearFaults(), board
    // 2026-09-30) - and still nothing switched on by itself
    CHECK(drv.getLatchedFaults() == 0U);
    // supervision with supply, 1 s in the 10 ms cycle: GSTAT + IOIN per cycle, 3 configuration reads per 100 ms
    fake.frames.clear();
    for (uint32_t t = detectedMs + 10U; t <= detectedMs + 1000U; t += 10U)
    {
        drv.cyclic(t);
    }
    CHECK(fake.frames.size() == 230U);
    CHECK(drv.getState() == State::DISABLED);
    CHECK(!fake.drvEnPin);
    CHECK(fake.enableHighCount == 0U);
    // the explicit request of the application is what switches on, without a clearFaults() first
    CHECK(drv.enable() == Status::OK);
    CHECK(fake.drvEnPin);
}

void protectedRegisters()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, KEEP_ALL);
    uint32_t v = 0U;
    CHECK(drv.writeRegister(TMC6200_REG_DRV_CONF, 0U) == Status::NOT_READY); // before init
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.readRegister(TMC6200_REG_OTP_PROG, v) == Status::NOT_ALLOWED);
    CHECK(drv.readRegister(0x05U, v) == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_OTP_PROG, 0xBD00U) == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_FACTORY_CONF, 0x10U) == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_IOIN, 0U) == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_OTP_READ, 0U) == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_GCONF, TMC6200_GCONF_TEST_MODE_MASK) == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_GCONF, 0x100U) == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_SHORT_CONF, SHORT_CONF_RESET | TMC6200_SHORT_CONF_DISABLE_S2G_MASK)
          == Status::NOT_ALLOWED);
    CHECK(drv.writeRegister(TMC6200_REG_SHORT_CONF, SHORT_CONF_RESET | TMC6200_SHORT_CONF_DISABLE_S2VS_MASK)
          == Status::NOT_ALLOWED);
    CHECK(fake.writes.empty());
    // allowed write becomes the new reference: no device reset is detected afterwards
    CHECK(drv.writeRegister(TMC6200_REG_DRV_CONF, DRV_CONF_RESET | 0x00030000UL) == Status::OK);
    CHECK(fake.reg[TMC6200_REG_DRV_CONF] == (DRV_CONF_RESET | 0x00030000UL));
    for (uint32_t t = 10U; t <= 500U; t += 10U)
    {
        drv.cyclic(t);
    }
    CHECK(drv.getCounters().deviceResets == 0U);
    // no register writes while enabled, no clearing either
    CHECK(drv.enable() == Status::OK);
    fake.writes.clear();
    CHECK(drv.writeRegister(TMC6200_REG_DRV_CONF, DRV_CONF_RESET) == Status::NOT_ALLOWED);
    CHECK(drv.clearFaults() == Status::NOT_ALLOWED);
    CHECK(fake.writes.empty());
}

void physicalUnitConversions()
{
    // overtemperature (p.25)
    CHECK(::gd::overtemperatureSelectForCelsius(150) == 0);
    CHECK(::gd::overtemperatureSelectForCelsius(143) == 1);
    CHECK(::gd::overtemperatureSelectForCelsius(136) == 2);
    CHECK(::gd::overtemperatureSelectForCelsius(120) == 3);
    CHECK(::gd::overtemperatureSelectForCelsius(140) == KEEP);
    CHECK(::gd::overtemperatureCelsius(2U) == 136U);
    // break-before-make (42 ns per clock, p.14/p.25): the time is covered, never shorter
    CHECK(::gd::breakBeforeMakeClocksForNs(0U) == 0);
    CHECK(::gd::breakBeforeMakeClocksForNs(42U) == 1);
    CHECK(::gd::breakBeforeMakeClocksForNs(43U) == 2);
    CHECK(::gd::breakBeforeMakeClocksForNs(168U) == 4);
    CHECK(::gd::breakBeforeMakeClocksForNs(630U) == 15);
    CHECK(::gd::breakBeforeMakeClocksForNs(631U) == KEEP);
    // gate current (p.36): weakest setting that reaches the current
    CHECK(::gd::driveStrengthForGateCurrentMa(300U) == 0);
    CHECK(::gd::driveStrengthForGateCurrentMa(400U) == 0);
    CHECK(::gd::driveStrengthForGateCurrentMa(401U) == 2);
    CHECK(::gd::driveStrengthForGateCurrentMa(1200U) == 3);
    CHECK(::gd::driveStrengthForGateCurrentMa(1201U) == KEEP);
    // short levels (p.38 typical points)
    CHECK(::gd::shortLevelMv(6) == 625);
    CHECK(::gd::shortLevelMv(15) == 1560);
    CHECK(::gd::shortLevelForMv(625, TMC6200_SHORT_LEVEL_S2VS_MIN) == 6);
    CHECK(::gd::shortLevelForMv(1560, TMC6200_SHORT_LEVEL_S2G_MIN) == 15);
    CHECK(::gd::shortLevelForMv(700, TMC6200_SHORT_LEVEL_S2VS_MIN) == 6); // trips at or below the request
    CHECK(::gd::shortLevelForMv(50, TMC6200_SHORT_LEVEL_S2VS_MIN) == KEEP);
    for (int32_t level = 2; level <= 15; ++level)
    {
        CHECK(::gd::shortLevelMv(level) > ::gd::shortLevelMv(level - 1)); // monotonic
    }
    // configuration ranges
    ::gd::Tmc6200Config c = ::gd::keepAllConfig();
    CHECK(::gd::isValidTmc6200Config(c));
    c.amplification = 2; // "(x10)" duplicate, not accepted
    CHECK(!::gd::isValidTmc6200Config(c));
    c                    = ::gd::keepAllConfig();
    c.shortToGroundLevel = 1; // S2G minimum is 2
    CHECK(!::gd::isValidTmc6200Config(c));
    c                       = ::gd::keepAllConfig();
    c.breakBeforeMakeClocks = 16;
    CHECK(!::gd::isValidTmc6200Config(c));
    c              = ::gd::keepAllConfig();
    c.shortRetries = -2;
    CHECK(!::gd::isValidTmc6200Config(c));
    // an invalid configuration is not written
    FakeTmc6200 fake;
    c                = ::gd::keepAllConfig();
    c.interfaceMode  = 0;
    c.driveStrength  = 4;
    Tmc6200 drv(fake, fake, c);
    CHECK(drv.init(0U) == Status::NOT_ALLOWED);
    CHECK(fake.writes.empty());
}

void shortDetectionInPhysicalUnits()
{
    // IO-35: project values in mV and ns (module.yaml short_*) -> SHORT_CONF fields, as the family integration
    // converts them (bsp/gd/gateDriverConfiguration.h)
    CHECK(::gd::shortFilterForNs(100U) == 0);
    CHECK(::gd::shortFilterForNs(1000U) == 1);
    CHECK(::gd::shortFilterForNs(2000U) == 2);
    CHECK(::gd::shortFilterForNs(3000U) == 3);
    CHECK(::gd::shortFilterForNs(500U) == KEEP);
    CHECK(::gd::shortFilterForNs(0U) == KEEP);
    CHECK(::gd::shortFilterNs(0U) == 100U);
    CHECK(::gd::shortFilterNs(1U) == 1000U);
    CHECK(::gd::shortFilterNs(3U) == 3000U);
    CHECK(::gd::shortDelayForNs(750U) == 0);
    CHECK(::gd::shortDelayForNs(1500U) == 1);
    CHECK(::gd::shortDelayForNs(1000U) == KEEP);
    CHECK(::gd::shortDelayNs(0U) == 750U);
    CHECK(::gd::shortDelayNs(1U) == 1500U);
    // not set -> KEEP (reset value, no write); converted -> field; not realizable -> invalid, never the reset value
    CHECK(::gd::fieldForRequest(0, 5) == KEEP);
    CHECK(::gd::fieldForRequest(-1, 5) == KEEP);
    CHECK(::gd::fieldForRequest(1000, 9) == 9);
    CHECK(::gd::fieldForRequest(100, KEEP) == ::gd::INVALID_FIELD);
    // levels (typical, p.38 plus interpolation): trips at or below the request, lowest levels of each detector
    CHECK(::gd::shortLevelForMv(1000, TMC6200_SHORT_LEVEL_S2VS_MIN) == 9); // 937 mV
    CHECK(::gd::shortLevelMv(9) == 937);
    CHECK(::gd::shortLevelForMv(209, TMC6200_SHORT_LEVEL_S2G_MIN) == 2);
    CHECK(::gd::shortLevelForMv(208, TMC6200_SHORT_LEVEL_S2G_MIN) == KEEP);
    CHECK(::gd::shortLevelForMv(106, TMC6200_SHORT_LEVEL_S2VS_MIN) == 1);
    CHECK(::gd::shortLevelForMv(105, TMC6200_SHORT_LEVEL_S2VS_MIN) == KEEP);

    ::gd::Tmc6200Config c = ::gd::keepAllConfig();
    c.shortToGroundLevel  = ::gd::fieldForRequest(1560, ::gd::shortLevelForMv(1560, TMC6200_SHORT_LEVEL_S2G_MIN));
    c.shortToSupplyLevel  = ::gd::fieldForRequest(1000, ::gd::shortLevelForMv(1000, TMC6200_SHORT_LEVEL_S2VS_MIN));
    c.shortFilter         = ::gd::fieldForRequest(2000, ::gd::shortFilterForNs(2000U));
    c.shortDelay          = ::gd::fieldForRequest(1500, ::gd::shortDelayForNs(1500U));
    CHECK(::gd::isValidTmc6200Config(c));
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, c);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(fake.writes.size() == 1U);
    CHECK(!fake.writes.empty() && (fake.writes[0] == TMC6200_REG_SHORT_CONF));
    // S2G 15, S2VS 9, filter 2 (2 us), delay 1 (1500 ns); retry 3 and protect_parallel 1 stay as the device has them
    CHECK(fake.reg[TMC6200_REG_SHORT_CONF] == 0x13120F09UL);
    CHECK(drv.getStartupInfo().shortConf == 0x13120F09UL);

    // defaults (all 0): nothing written, as before IO-35
    ::gd::Tmc6200Config keep = ::gd::keepAllConfig();
    keep.shortToGroundLevel  = ::gd::fieldForRequest(0, ::gd::shortLevelForMv(0, TMC6200_SHORT_LEVEL_S2G_MIN));
    keep.shortFilter         = ::gd::fieldForRequest(0, KEEP);
    FakeTmc6200 untouched;
    Tmc6200 keepDrv(untouched, untouched, keep);
    CHECK(keepDrv.init(0U) == Status::OK);
    CHECK(untouched.writes.empty());

    // a value the device cannot realize is not written at all
    c.shortToGroundLevel = ::gd::fieldForRequest(100, ::gd::shortLevelForMv(100, TMC6200_SHORT_LEVEL_S2G_MIN));
    CHECK(!::gd::isValidTmc6200Config(c));
    FakeTmc6200 other;
    Tmc6200 bad(other, other, c);
    CHECK(bad.init(0U) == Status::NOT_ALLOWED);
    CHECK(other.writes.empty());
}

void faultPinNotWired()
{
    FakeTmc6200 fake;
    fake.faultWired = false;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_S2VSW_MASK;
    drv.cyclic(10U);
    CHECK(!drv.isEnabled()); // GSTAT alone is enough
    CHECK((drv.getLatchedFaults() & fault::SHORT_TO_SUPPLY_W) != 0U);
    CHECK((drv.getLatchedFaults() & fault::FAULT_PIN) == 0U);
    CHECK(std::strcmp(Tmc6200::faultName(fault::SHORT_TO_SUPPLY_W), "short to VS W") == 0);
    CHECK(std::strcmp(Tmc6200::faultName(0U), "none") == 0);
}

/* IO-34: console command path "gd clear" / "gd off" / "gd enable" of GateDriverSystem::Command (platforms/s32k1xx/
 * port/main/src/systems/GateDriverSystem.cpp, "enable" only with NX_GD_ENABLE_COMMAND). Same calls and the same
 * reply line as there (the system itself needs OpenBSW and does not build on the host): one driver request, then
 * "<command> <subcommand>: <status>, state <state>, faults 0x<latched>". Keep both in sync. */
enum class ConsoleStateCommand : uint8_t
{
    CLEAR,
    OFF,
    ENABLE,
};

struct ConsoleReply
{
    Status status;
    char line[96];
};

ConsoleReply consoleStateCommand(Tmc6200& drv, ConsoleStateCommand const idx)
{
    ConsoleReply reply{};
    reply.status = (idx == ConsoleStateCommand::CLEAR) ? drv.clearFaults()
                   : (idx == ConsoleStateCommand::OFF) ? drv.disable()
                                                       : drv.enable();
    (void)std::snprintf(
        reply.line,
        sizeof(reply.line),
        "%s %s: %s, state %s, faults 0x%04x\r\n",
        "gd",
        (idx == ConsoleStateCommand::CLEAR) ? "clear" : ((idx == ConsoleStateCommand::OFF) ? "off" : "enable"),
        Tmc6200::statusName(reply.status),
        Tmc6200::stateName(drv.getState()),
        static_cast<unsigned>(drv.getLatchedFaults()));
    return reply;
}

/* IO-44, the safety-relevant case. Board 2026-09-23 (e2 with the user, 24 V/1 A, load removed, captures
 * tools/boardtest/captures/evalboard_s32k144_demo/2026-09-23_board/38-41 and 45-47): the device reported
 * "short detected" (GSTAT shortdet only) and the driver stayed enabled - DRV_EN = PTC11, GPIOC PDOR 0x00000800,
 * PDIR 0x0000081C, so really high. Of the 15 occurrences of fault 0x0800 with "DRV_EN on" that day, a shutdown
 * fault followed in only 5 (11 ms to 1551 ms later); the other 10 ran on until the operator typed "gd off".
 * Here the reported short switches off, in the first cycle that sees it. */
void reportedShortSwitchesOff()
{
    struct Case
    {
        char const* name;
        uint32_t gstat;   // as read on the board
        uint16_t faults;  // as logged on the board ("MOT1_GD fault 0x....")
    };
    // Every fault code measured on 2026-09-23 with its GSTAT; all of them must switch the bridge off.
    static constexpr Case const cases[] = {
        {"45/46 50/50/0 and 0/0/50, order B: shortdet W", 0x1000UL, 0x0800U},
        {"46/47 shortdet U+V", 0x0110UL, 0x0800U},
        {"38/41 shortdet U", 0x0010UL, 0x0800U},
        {"37 charge pump undervoltage", 0x0008UL, 0x1004U},
        {"38/39/41 charge pump undervoltage after a short", 0x0018UL, 0x1804U},
        {"37/39 short to GND U", 0x0030UL, 0x1820U},
        {"40 short to VS V", 0x0500UL, 0x1a00U},
        {"41 overtemperature after a short", 0x001eUL, 0x181cU},
    };
    for (Case const& c : cases)
    {
        FakeTmc6200 fake;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::OK);
        CHECK(drv.enable() == Status::OK);
        drv.cyclic(10U);
        CHECK(drv.isEnabled());
        fake.reg[TMC6200_REG_GSTAT] |= c.gstat;
        drv.cyclic(20U);
        if (drv.getLatchedFaults() != c.faults)
        {
            std::printf("  case %s: GSTAT 0x%04x -> 0x%04x, board 0x%04x\n", c.name,
                        static_cast<unsigned>(c.gstat), drv.getLatchedFaults(), c.faults);
        }
        CHECK(drv.getLatchedFaults() == c.faults); // same code the board logged
        CHECK(!drv.isEnabled());                   // ... and, unlike on the board, DRV_EN goes low
        CHECK(!fake.drvEnPin);
        CHECK(drv.getState() == State::DISABLED);
        CHECK(drv.getCounters().shutdowns == 1U);
        // and it stays off: enabling is refused until the application acknowledges
        drv.cyclic(30U);
        CHECK(drv.enable() == Status::NOT_ALLOWED);
        CHECK(!fake.drvEnPin);
    }
}

/* IO-44: "gd clear" must not look as if it had cleared up. Board 2026-09-23 (captures 45/46): with the standing
 * phase still driven, clear answered "ok, state disabled, faults 0x0000" in the runs where DRV_EN had been taken
 * away first - but while the condition was there, faults and latched stayed 0x0800. Datasheet p.22: "Switch off
 * the affected MOSFET by its HS/LS input to clear a pending short condition. Just resetting the flag will not
 * switch it on again." */
void clearIsHonestWhileTheConditionIsThere()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_SHORTDET_W_MASK;
    drv.cyclic(10U);
    CHECK(!drv.isEnabled());
    CHECK(drv.getLatchedFaults() == 0x0800U);
    // the condition is still there: the device sets the flag again right after every write of 1
    fake.stickyGstat = TMC6200_GSTAT_SHORTDET_W_MASK;
    ConsoleReply const stillThere = consoleStateCommand(drv, ConsoleStateCommand::CLEAR);
    CHECK(stillThere.status == Status::NOT_CLEARED);
    CHECK(drv.getFaults() == fault::SHORT_DETECTED);
    CHECK(drv.getLatchedFaults() == fault::SHORT_DETECTED); // nothing pretends to be clean
    CHECK(std::strcmp(stillThere.line, "gd clear: fault still present, state disabled, faults 0x0800\r\n") == 0);
    CHECK(drv.enable() == Status::NOT_ALLOWED);
    CHECK(!fake.drvEnPin);
    // the operator takes the PWM off the phase (board: "pwm set 0xFF 0"): now the clear really clears
    fake.stickyGstat = 0U;
    ConsoleReply const cleared = consoleStateCommand(drv, ConsoleStateCommand::CLEAR);
    CHECK(cleared.status == Status::OK);
    CHECK(drv.getLatchedFaults() == 0U);
    CHECK(std::strcmp(cleared.line, "gd clear: ok, state disabled, faults 0x0000\r\n") == 0);
}

/* IO-44/IO-45: enabling is refused while the three phases are not in one common state, with the reason and without
 * touching DRV_EN. Board 2026-09-23/24 (B-IO44-3): the mixture fails - 50/50/0 and 0/0/50 in 6 of 6 runs, 50/50/100 in
 * 2 of 2 - while all off (11 of 12), all full (13 of 13) and all switching (10 of 10) run. The rule of IO-44 ("every
 * phase switches") refused the common states all off and all full; IO-45 replaces it by one check over all three. */
::gd::PhaseDuty commandedDuty{};
bool dutyKnown = true;

bool phaseDutySource(::gd::PhaseDuty& duty)
{
    duty = commandedDuty;
    return dutyKnown;
}

void enableNeedsOneCommonPhaseState()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);

    // without a source attached nothing changes (a board that does not tell the driver keeps its behaviour)
    commandedDuty = {0U, 0U, 0U};
    CHECK(drv.enable() == Status::OK);
    CHECK(drv.disable() == Status::OK);

    drv.setPhaseDutySource(phaseDutySource);
    dutyKnown = true;
    struct Duty
    {
        uint16_t u;
        uint16_t v;
        uint16_t w;
    };
    // mixtures, per mille: the measured failing points 50/50/0 (capture 45), 0/0/50 (capture 46) and 50/50/100
    // (B-IO44-3), and every other mixture of the three states in every position
    static constexpr Duty const mixed[] = {
        {500U, 500U, 0U},    // capture 45
        {0U, 0U, 500U},      // capture 46
        {500U, 500U, 1000U}, // B-IO44-3, 2026-09-24: 2 of 2 with shutdown
        {700U, 700U, 0U},
        {1000U, 500U, 500U},
        {500U, 1000U, 500U},
        {0U, 500U, 500U},
        {500U, 0U, 500U},
        {0U, 0U, 1000U},     // off and full, no phase switching
        {1000U, 1000U, 0U},
        {0U, 1000U, 0U},
        {0U, 500U, 1000U},   // all three states at once
        {1000U, 0U, 500U},
        {1U, 1U, 0U},        // the smallest step away from "all off"
        {999U, 1000U, 999U}, // and from "all full"
    };
    for (Duty const& s : mixed)
    {
        commandedDuty         = {s.u, s.v, s.w};
        uint32_t const before = fake.enableHighCount;
        ConsoleReply const r  = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
        CHECK(r.status == Status::PHASES_MIXED);
        CHECK(fake.enableHighCount == before); // DRV_EN is never written high
        CHECK(!fake.drvEnPin);
        CHECK(drv.getState() == State::DISABLED);
        CHECK(drv.getLatchedFaults() == 0U); // a refusal is no fault of the device
        CHECK(drv.getLastPhaseDuty().w == s.w);
        CHECK(std::strcmp(r.line, "gd enable: phases mixed, state disabled, faults 0x0000\r\n") == 0);
    }
    // the duty is not known: the driver does not guess, not even for a common state
    static constexpr Duty const common[] = {{500U, 500U, 500U}, {0U, 0U, 0U}, {1000U, 1000U, 1000U}};
    dutyKnown = false;
    for (Duty const& s : common)
    {
        commandedDuty = {s.u, s.v, s.w};
        CHECK(drv.enable() == Status::PHASES_MIXED);
        CHECK(!fake.drvEnPin);
    }

    // one common state: all switching (the fault-free points of series B, 10 to 90 %, and unequal duties - the rule
    // asks for the state, not for the duty), all off (11 of 12) and all full (13 of 13, B-IO44-3)
    dutyKnown = true;
    static constexpr Duty const allowed[] = {
        {100U, 100U, 100U},
        {300U, 300U, 300U},
        {500U, 500U, 500U},
        {700U, 700U, 700U},
        {900U, 900U, 900U},
        {1U, 999U, 500U},
        {0U, 0U, 0U},        // all off: capture 47 was refused by the rule of IO-44
        {1000U, 1000U, 1000U},
        {1000U, 1500U, 65535U}, // above DUTY_FULL is full as well
    };
    for (Duty const& s : allowed)
    {
        commandedDuty = {s.u, s.v, s.w};
        CHECK(drv.enable() == Status::OK);
        CHECK(fake.drvEnPin);
        CHECK(drv.getLastPhaseDuty().u == s.u);
        CHECK(drv.getLastPhaseDuty().v == s.v);
        CHECK(drv.getLastPhaseDuty().w == s.w);
        CHECK(drv.disable() == Status::OK);
    }
    // a latched shutdown fault outranks the enable rule: the stronger reason is the one reported
    commandedDuty = {500U, 500U, 500U};
    CHECK(drv.enable() == Status::OK);
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_S2GU_MASK;
    drv.cyclic(10U);
    CHECK(!drv.isEnabled());
    commandedDuty = {500U, 500U, 0U};
    CHECK(drv.enable() == Status::NOT_ALLOWED);
    CHECK(!fake.drvEnPin);
    // the pure predicates, independent of the driver state
    CHECK(Tmc6200::phaseState(0U) == ::gd::PhaseState::OFF);
    CHECK(Tmc6200::phaseState(1U) == ::gd::PhaseState::SWITCHING);
    CHECK(Tmc6200::phaseState(Tmc6200::DUTY_FULL - 1U) == ::gd::PhaseState::SWITCHING);
    CHECK(Tmc6200::phaseState(Tmc6200::DUTY_FULL) == ::gd::PhaseState::FULL);
    CHECK(Tmc6200::phaseState(Tmc6200::DUTY_FULL + 1U) == ::gd::PhaseState::FULL);
    for (Duty const& s : mixed)
    {
        CHECK(!Tmc6200::isCommonState({s.u, s.v, s.w}));
        // the rule does not depend on the order of the phases
        CHECK(!Tmc6200::isCommonState({s.w, s.u, s.v}));
        CHECK(!Tmc6200::isCommonState({s.v, s.w, s.u}));
    }
    for (Duty const& s : allowed)
    {
        CHECK(Tmc6200::isCommonState({s.u, s.v, s.w}));
        CHECK(Tmc6200::isCommonState({s.w, s.u, s.v}));
    }
    // every combination of the three states: common exactly when all three are equal (27 cases)
    static constexpr uint16_t const representative[] = {0U, 500U, Tmc6200::DUTY_FULL};
    for (uint16_t const u : representative)
    {
        for (uint16_t const v : representative)
        {
            for (uint16_t const w : representative)
            {
                CHECK(Tmc6200::isCommonState({u, v, w}) == ((u == v) && (v == w)));
            }
        }
    }
}

void consoleEnableCommand()
{
    // before init (UNINITIALIZED): refused, DRV_EN never high
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    ConsoleReply r = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::NOT_READY);
    CHECK(fake.enableHighCount == 0U);
    CHECK(std::strcmp(r.line, "gd enable: not ready, state uninitialized, faults 0x0000\r\n") == 0);

    // power-on reset flag is information, not a shutdown fault: enable from DISABLED works (board test 2026-09-18:
    // "disabled -> enabled", IOIN DRV_EN 1, GSTAT reset flag cleared with DRV_EN high)
    CHECK(drv.init(0U) == Status::OK);
    CHECK(fake.enableHighCount == 0U);
    r = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::OK);
    CHECK(fake.drvEnPin);
    CHECK(fake.enableHighCount == 1U);
    CHECK(drv.getCounters().enables == 1U);
    CHECK((drv.getLastIoin() & TMC6200_IOIN_DRV_EN_MASK) != 0U); // checked through IOIN
    CHECK(std::strcmp(r.line, "gd enable: ok, state enabled, faults 0x0000\r\n") == 0);

    // already ENABLED: no second pin write (the driver answers ok, nothing happens)
    r = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::OK);
    CHECK(fake.enableHighCount == 1U);
    CHECK(drv.getCounters().enables == 1U);

    // clear is refused while enabled (DRV_EN stays as it is), off switches DRV_EN low
    r = consoleStateCommand(drv, ConsoleStateCommand::CLEAR);
    CHECK(r.status == Status::NOT_ALLOWED);
    CHECK(fake.drvEnPin);
    r = consoleStateCommand(drv, ConsoleStateCommand::OFF);
    CHECK(r.status == Status::OK);
    CHECK(!fake.drvEnPin);
    CHECK(std::strcmp(r.line, "gd off: ok, state disabled, faults 0x0000\r\n") == 0);

    // shutdown fault while enabled: the driver switches off and latches; enable is refused until "gd clear"
    r = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::OK);
    fake.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_S2GU_MASK | TMC6200_GSTAT_SHORTDET_U_MASK;
    drv.cyclic(10U);
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::DISABLED);
    drv.cyclic(20U); // device flags cleared by DRV_EN low (p.22), latched in the driver
    uint32_t const highBefore = fake.enableHighCount;
    r = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::NOT_ALLOWED);
    CHECK(fake.enableHighCount == highBefore);
    CHECK(!fake.drvEnPin);
    char expected[96];
    (void)std::snprintf(
        expected,
        sizeof(expected),
        "gd enable: not allowed, state disabled, faults 0x%04x\r\n",
        static_cast<unsigned>(drv.getLatchedFaults()));
    CHECK((drv.getLatchedFaults() & fault::SHORT_TO_GROUND_U) != 0U);
    CHECK(std::strcmp(r.line, expected) == 0);
    r = consoleStateCommand(drv, ConsoleStateCommand::CLEAR);
    CHECK(r.status == Status::OK);
    CHECK(!fake.drvEnPin); // clear never enables
    CHECK(std::strcmp(r.line, "gd clear: ok, state disabled, faults 0x0000\r\n") == 0);
    r = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::OK);
    CHECK(fake.drvEnPin);
    CHECK(consoleStateCommand(drv, ConsoleStateCommand::OFF).status == Status::OK);

    // pending shutdown fault from the start (bridge supply missing, UV_CP): refused
    FakeTmc6200 noSupply;
    noSupply.reg[TMC6200_REG_GSTAT] |= TMC6200_GSTAT_UV_CP_MASK;
    Tmc6200 drv2(noSupply, noSupply, CONFIGURED);
    CHECK(drv2.init(0U) == Status::OK);
    r = consoleStateCommand(drv2, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::NOT_ALLOWED);
    CHECK(noSupply.enableHighCount == 0U);

    // FAILED (e.g. SPI error / VS missing): refused, DRV_EN stays low
    FakeTmc6200 broken;
    broken.spiFails = true;
    Tmc6200 drv3(broken, broken, CONFIGURED);
    CHECK(drv3.init(0U) != Status::OK);
    CHECK(drv3.getState() == State::FAILED);
    r = consoleStateCommand(drv3, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::NOT_READY);
    CHECK(broken.enableHighCount == 0U);
    CHECK(!broken.drvEnPin);

    // DRV_EN does not reach the device: IOIN check fails, the driver drives the pin low again
    FakeTmc6200 open;
    open.drvEnWired = false;
    Tmc6200 drv4(open, open, CONFIGURED);
    CHECK(drv4.init(0U) == Status::OK);
    r = consoleStateCommand(drv4, ConsoleStateCommand::ENABLE);
    CHECK(r.status == Status::PIN_ERROR);
    CHECK(!open.drvEnPin);
    CHECK(drv4.getState() == State::DISABLED);
    CHECK((drv4.getLatchedFaults() & fault::ENABLE_MISMATCH) != 0U);
}

bool safeStateLatched = false;

bool safeStateLockQuery() { return safeStateLatched; }

/* SYS-36: the enable lock of the safe state. S1 pulls DRV_EN low behind the driver's back (safe outputs table) and
 * sets the lock: the driver follows without a fault, refuses enable() and never drives DRV_EN high again. */
void safeStateLock()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(!drv.updateEnableLock()); // no lock attached
    drv.setEnableLock(&safeStateLockQuery);
    safeStateLatched = false;
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    drv.cyclic(10U);
    CHECK(drv.getState() == State::ENABLED);

    fake.drvEnPin    = false; // the table walk of S1
    safeStateLatched = true;
    uint32_t const highWrites = fake.enableHighCount;
    for (uint32_t t = 20U; t <= 2000U; t += 10U)
    {
        drv.cyclic(t);
        CHECK(drv.enable() == Status::LOCKED); // the application insists
    }
    CHECK(fake.enableHighCount == highWrites);
    CHECK(!fake.drvEnPin);
    CHECK(drv.isLocked());
    CHECK(drv.getState() == State::DISABLED);
    CHECK(drv.getLatchedFaults() == 0U); // before SYS-36: ENABLE_MISMATCH, latched as a shutdown fault
    CHECK(drv.getCounters().shutdowns == 0U);
    CHECK(drv.clearFaults() == Status::OK); // DRV_EN is low: allowed, and harmless

    // a real mismatch while locked is still one: DRV_EN high at the device although commanded low
    fake.drvEnPin = true;
    drv.cyclic(2010U);
    CHECK((drv.getFaults() & fault::ENABLE_MISMATCH) != 0U);
    fake.drvEnPin = false;
    drv.cyclic(2020U);
    CHECK(drv.clearFaults() == Status::OK);

    // updateEnableLock() acts at once: enabled, then S1 without a cycle in between
    safeStateLatched = false;
    CHECK(drv.enable() == Status::OK);
    CHECK(fake.drvEnPin);
    safeStateLatched = true;
    CHECK(drv.updateEnableLock());
    CHECK(!fake.drvEnPin);
    CHECK(drv.getState() == State::DISABLED);

    // SYS-35 (decision safe-state-rueckweg): `gd enable` in the safe state is refused with a clear message
    ConsoleReply const refused = consoleStateCommand(drv, ConsoleStateCommand::ENABLE);
    CHECK(refused.status == Status::LOCKED);
    CHECK(std::strcmp(refused.line, "gd enable: refused: safe state, state disabled, faults 0x0000\r\n") == 0);
    CHECK(!fake.drvEnPin);

    // lock released (SYS-35: the safe state was left): nothing switches on by itself, the next enable() works
    safeStateLatched = false;
    for (uint32_t t = 3000U; t <= 3500U; t += 10U)
    {
        drv.cyclic(t);
    }
    CHECK(!drv.isLocked());
    CHECK(!fake.drvEnPin);
    CHECK(drv.getLatchedFaults() == 0U);
    CHECK(drv.enable() == Status::OK);
    CHECK(fake.drvEnPin);
    CHECK(std::strcmp(::gd::Tmc6200::statusName(Status::LOCKED), "refused: safe state") == 0);
}

/* SYS-36, counter-example: the same S1 without the lock attached is what the board showed - the driver takes the low
 * DRV_EN for a wiring fault and latches ENABLE_MISMATCH. */
void safeStateWithoutLockIsAMismatch()
{
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    CHECK(drv.enable() == Status::OK);
    fake.drvEnPin = false; // the table walk of S1, nobody tells the driver
    drv.cyclic(10U);
    CHECK((drv.getLatchedFaults() & fault::ENABLE_MISMATCH) != 0U);
    CHECK(drv.getCounters().shutdowns == 1U);
}

/* SYS-147 (board 2026-09-30, a DRV_EN pulse of about 0.2 ms after IO-45): DRV_EN is the driver's output, and a high
 * level nobody commanded is taken back by the driver itself - in every state, within one cycle. Before SYS-147 the
 * driver only latched ENABLE_MISMATCH in DISABLED (and only with the device supplied) and left the pin high; in
 * FAILED it drove it low once per RETRY_MS. The foreign writer here stands for the console `out set` of OpenBSW, a
 * debugger write to PSOR or a stray store to the port: the fake's pin is set behind the driver's back. */
void foreignDriverEnableIsTakenBack()
{
    // DISABLED, device supplied: the high level is seen in IOIN, latched as a mismatch, then driven low in the same
    // cycle - never written high by the driver
    FakeTmc6200 fake;
    Tmc6200 drv(fake, fake, CONFIGURED);
    CHECK(drv.init(0U) == Status::OK);
    drv.cyclic(10U);
    CHECK(drv.getLatchedFaults() == 0U);
    fake.drvEnPin = true; // foreign write
    drv.cyclic(20U);
    CHECK(!fake.drvEnPin);
    CHECK((drv.getLatchedFaults() & fault::ENABLE_MISMATCH) != 0U); // detected before it was taken back
    CHECK(drv.getState() == State::DISABLED);
    CHECK(fake.enableHighCount == 0U);
    CHECK(drv.enable() == Status::NOT_ALLOWED); // the latched mismatch blocks the next enable until acknowledged
    CHECK(!fake.drvEnPin);
    // the following cycle sees the pin low again: the mismatch is gone, the latch stays until clearFaults()
    drv.cyclic(30U);
    CHECK((drv.getFaults() & fault::ENABLE_MISMATCH) == 0U);
    CHECK((drv.getLatchedFaults() & fault::ENABLE_MISMATCH) != 0U);
    CHECK(drv.clearFaults() == Status::OK);
    CHECK(drv.enable() == Status::OK);
    CHECK(fake.enableHighCount == 1U);

    // a writer that insists (every cycle): the pin is low after every cycle of the driver
    FakeTmc6200 again;
    Tmc6200 drv2(again, again, CONFIGURED);
    CHECK(drv2.init(0U) == Status::OK);
    for (uint32_t t = 10U; t <= 1000U; t += 10U)
    {
        again.drvEnPin = true;
        drv2.cyclic(t);
        CHECK(!again.drvEnPin);
    }
    CHECK(again.enableHighCount == 0U);

    // FAILED, device without supply (the board of 2026-09-30: IOIN 0, "DRV_EN stays off"): IOIN cannot show the
    // level, no retry is due - the pin is driven low in the next cycle anyway, not only at the next retry
    FakeTmc6200 unpowered;
    unpowered.unpowered = true;
    Tmc6200 drv3(unpowered, unpowered, CONFIGURED);
    CHECK(drv3.init(0U) == Status::NO_SUPPLY);
    CHECK(drv3.getState() == State::FAILED);
    unpowered.drvEnPin = true;
    drv3.cyclic(10U); // next retry at 1000 ms
    CHECK(drv3.getCounters().startupAttempts == 1U);
    CHECK(!unpowered.drvEnPin);
    CHECK(unpowered.enableHighCount == 0U);

    // UNINITIALIZED: the first cycle starts the device and leaves the pin low
    FakeTmc6200 fresh;
    fresh.drvEnPin = true;
    Tmc6200 drv4(fresh, fresh, CONFIGURED);
    drv4.cyclic(0U);
    CHECK(!fresh.drvEnPin);

    // ENABLED: the commanded level is the driver's own - supervision cycles do not touch it
    FakeTmc6200 on;
    Tmc6200 drv5(on, on, CONFIGURED);
    CHECK(drv5.init(0U) == Status::OK);
    CHECK(drv5.enable() == Status::OK);
    uint32_t const pinWrites = static_cast<uint32_t>(on.sequence.size());
    for (uint32_t t = 10U; t <= 1000U; t += 10U)
    {
        drv5.cyclic(t);
        CHECK(on.drvEnPin);
    }
    uint32_t enableWritesWhileOn = 0U;
    for (size_t i = pinWrites; i < on.sequence.size(); ++i)
    {
        enableWritesWhileOn += (on.sequence[i] == 'E') ? 1U : 0U;
    }
    CHECK(enableWritesWhileOn == 0U);
    CHECK(drv5.getState() == State::ENABLED);
    CHECK(drv5.getFaults() == 0U);
    // after disable() the hold applies again
    CHECK(drv5.disable() == Status::OK);
    on.drvEnPin = true;
    drv5.cyclic(1010U);
    CHECK(!on.drvEnPin);

    // S1 locked: the lock switches off, and a foreign write while locked is taken back by the driver as well (on the
    // target the hold of the integration is faster, 1 ms)
    FakeTmc6200 locked;
    Tmc6200 drv6(locked, locked, CONFIGURED);
    drv6.setEnableLock(&safeStateLockQuery);
    safeStateLatched = false;
    CHECK(drv6.init(0U) == Status::OK);
    CHECK(drv6.enable() == Status::OK);
    safeStateLatched = true;
    drv6.cyclic(10U);
    CHECK(!locked.drvEnPin);
    locked.drvEnPin = true; // the J-Link write of SYS-30 B15 pin case A
    drv6.cyclic(20U);
    CHECK(!locked.drvEnPin);
    CHECK((drv6.getLatchedFaults() & fault::ENABLE_MISMATCH) != 0U);
    safeStateLatched = false;
}

/* Runs the driver in its 10 ms cycle from `from` to `to` (both included). */
void runCycles(Tmc6200& drv, uint32_t const from, uint32_t const to)
{
    for (uint32_t t = from; t <= to; t += 10U)
    {
        drv.cyclic(t);
    }
}

/* IO-67: board 2026-09-30 - started without the 24 V supply of the power stage, the driver latched COMMUNICATION
 * and kept it after the 24 V were there; only `gd clear` removed it. A device that answers like a device without
 * supply (IOIN an all-zero datagram) is NO_SUPPLY: latched only when it goes missing out of a running state
 * (DISABLED, ENABLED), a current fault only while the driver has not brought it up. Nothing ever switches on by
 * itself, and every other startup failure still latches. */
void missingSupplyIsNotACommunicationFault()
{
    // 1. start without supply, supply switched on later: found by the next attempt, nothing latched, DRV_EN off
    //    until the explicit enable()
    {
        FakeTmc6200 fake;
        fake.unpowered = true;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::NO_SUPPLY);
        runCycles(drv, 10U, 3000U);
        CHECK(drv.getState() == State::FAILED);
        CHECK(drv.getFaults() == fault::NO_SUPPLY);
        CHECK(drv.getLatchedFaults() == fault::NO_SUPPLY); // the current fault, visible in gd.<device>.faults
        CHECK(drv.enable() == Status::NOT_READY);
        CHECK(fake.writes.empty());
        fake.unpowered = false;
        fake.powerOn();
        runCycles(drv, 3010U, 4000U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK(drv.getFaults() == 0U);
        CHECK(drv.getLatchedFaults() == 0U);
        CHECK(fake.enableHighCount == 0U);
        CHECK(!fake.drvEnPin);
        runCycles(drv, 4010U, 6000U);
        CHECK(fake.enableHighCount == 0U); // the recovery does not enable, not even later
        CHECK(drv.enable() == Status::OK);
        CHECK(fake.drvEnPin);
    }
    // 2. supply lost while DISABLED: latched; after the supply is back enable() is refused until clearFaults()
    {
        FakeTmc6200 fake;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::OK);
        runCycles(drv, 10U, 500U);
        fake.unpowered = true;
        drv.cyclic(510U);
        CHECK(drv.getState() == State::FAILED);
        CHECK((drv.getLatchedFaults() & fault::NO_SUPPLY) != 0U);
        CHECK((drv.getLatchedFaults() & fault::COMMUNICATION) == 0U);
        CHECK(drv.getLastIoin() == 0U);
        runCycles(drv, 520U, 2500U);
        fake.unpowered = false;
        fake.powerOn();
        runCycles(drv, 2510U, 3600U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK(drv.getFaults() == 0U);
        CHECK(drv.getLatchedFaults() == fault::NO_SUPPLY);
        CHECK(drv.enable() == Status::NOT_ALLOWED);
        CHECK(fake.enableHighCount == 0U);
        CHECK(drv.clearFaults() == Status::OK);
        CHECK(drv.enable() == Status::OK);
    }
    // 3. supply lost while ENABLED: DRV_EN off in the same cycle, counted as a shutdown, latched
    {
        FakeTmc6200 fake;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::OK);
        CHECK(drv.enable() == Status::OK);
        drv.cyclic(10U);
        fake.unpowered = true;
        drv.cyclic(20U);
        CHECK(!fake.drvEnPin);
        CHECK(drv.getState() == State::FAILED);
        CHECK(drv.getCounters().shutdowns == 1U);
        fake.unpowered = false;
        fake.powerOn();
        runCycles(drv, 30U, 1100U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK((drv.getLatchedFaults() & fault::NO_SUPPLY) != 0U);
        CHECK(drv.enable() == Status::NOT_ALLOWED);
        CHECK(!fake.drvEnPin);
    }
    // 4. supply lost, acknowledged while still missing: the retries do not latch it again
    {
        FakeTmc6200 fake;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::OK);
        fake.unpowered = true;
        drv.cyclic(10U);
        CHECK((drv.getLatchedFaults() & fault::NO_SUPPLY) != 0U);
        CHECK(drv.clearFaults() == Status::OK);
        runCycles(drv, 20U, 3000U);
        fake.unpowered = false;
        fake.powerOn();
        runCycles(drv, 3010U, 4100U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK(drv.getLatchedFaults() == 0U);
        CHECK(fake.enableHighCount == 0U);
    }
    // 5. supply gone right at the enable: DRV_EN taken back, latched as missing supply
    {
        FakeTmc6200 fake;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::OK);
        fake.unpowered = true;
        CHECK(drv.enable() == Status::NO_SUPPLY);
        CHECK(!fake.drvEnPin);
        CHECK(drv.getState() == State::FAILED);
        CHECK((drv.getLatchedFaults() & fault::NO_SUPPLY) != 0U);
    }
    // 6. every other startup failure still latches before the device was ever up
    {
        // something drives the status byte while the data reads 0: not the signature of a missing supply
        FakeTmc6200 fake;
        fake.unpowered           = true;
        fake.unpoweredStatusByte = 0x04U;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::WRONG_DEVICE);
        CHECK(drv.getFaults() == fault::COMMUNICATION);
        fake.unpowered = false;
        fake.powerOn();
        runCycles(drv, 10U, 1000U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK((drv.getLatchedFaults() & fault::COMMUNICATION) != 0U);
        CHECK(drv.enable() == Status::NOT_ALLOWED);
    }
    {
        // another version than 0x10, SDO stuck high: latched as before
        FakeTmc6200 fake;
        fake.reg[TMC6200_REG_IOIN] = 0x11UL << 24U;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::WRONG_DEVICE);
        fake.reg[TMC6200_REG_IOIN] = static_cast<uint32_t>(TMC6200_VERSION) << 24U;
        runCycles(drv, 10U, 1000U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK((drv.getLatchedFaults() & fault::COMMUNICATION) != 0U);
        CHECK(drv.enable() == Status::NOT_ALLOWED);

        FakeTmc6200 high;
        high.sdoStuckHigh = true;
        Tmc6200 drv2(high, high, CONFIGURED);
        CHECK(drv2.init(0U) == Status::SPI_ERROR);
        high.sdoStuckHigh = false;
        runCycles(drv2, 10U, 1000U);
        CHECK(drv2.getState() == State::DISABLED);
        CHECK((drv2.getLatchedFaults() & fault::COMMUNICATION) != 0U);
    }
    {
        // a supply that comes up through a garbled answer: the garbled attempt latches (conservative)
        FakeTmc6200 fake;
        fake.unpowered = true;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::NO_SUPPLY);
        fake.unpowered             = false;
        fake.reg[TMC6200_REG_IOIN] = 0x01000000UL;
        runCycles(drv, 10U, 1000U);
        CHECK(drv.getState() == State::FAILED);
        CHECK(drv.getFaults() == fault::COMMUNICATION); // the answer of this attempt replaces NO_SUPPLY
        fake.powerOn();
        runCycles(drv, 1010U, 2000U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK((drv.getLatchedFaults() & fault::COMMUNICATION) != 0U);
        CHECK(drv.enable() == Status::NOT_ALLOWED);
    }
    {
        // SPI error first (latched), then no supply: the current fault names the missing supply, the latch stays
        FakeTmc6200 fake;
        fake.spiFails = true;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::SPI_ERROR);
        fake.spiFails  = false;
        fake.unpowered = true;
        runCycles(drv, 10U, 1000U);
        CHECK(drv.getFaults() == fault::NO_SUPPLY);
        CHECK((drv.getLatchedFaults() & fault::COMMUNICATION) != 0U);
    }
    // 7. only IOIN carries the signature: other registers of a supplied device may read as an all-zero datagram
    //    (GCONF 0 after the configuration, read right after a read of address 0x00)
    {
        FakeTmc6200 fake;
        Tmc6200 drv(fake, fake, CONFIGURED);
        CHECK(drv.init(0U) == Status::OK);
        uint32_t value = 0xFFFFFFFFUL;
        CHECK(drv.readRegister(TMC6200_REG_GCONF, value) == Status::OK);
        value = 0xFFFFFFFFUL;
        CHECK(drv.readRegister(TMC6200_REG_GCONF, value) == Status::OK); // frame 0x0000000000
        CHECK(value == 0U);
        CHECK(drv.getState() == State::DISABLED);
        CHECK(drv.getLatchedFaults() == 0U);
    }
    // 8. names: the reply says both causes the driver cannot tell apart, within the 20 characters of a log entry
    CHECK(std::strcmp(Tmc6200::statusName(Status::NO_SUPPLY), "no supply or SDO low") == 0);
    CHECK(std::strlen(Tmc6200::statusName(Status::NO_SUPPLY)) <= 20U);
    CHECK(std::strcmp(Tmc6200::faultName(fault::NO_SUPPLY), "no supply") == 0);
    CHECK((fault::SHUTDOWN & fault::NO_SUPPLY) != 0U);
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"initIsReadOnlyWithKeepConfig", initIsReadOnlyWithKeepConfig},
        {"driverEnableIsLowAfterInit", driverEnableIsLowAfterInit},
        {"datagramFormat", datagramFormat},
        {"wrongVersionIsNotWritten", wrongVersionIsNotWritten},
        {"spiFailureIsRetried", spiFailureIsRetried},
        {"stuckSdoIsSpiError", stuckSdoIsSpiError},
        {"configuredFieldsAreWrittenAndVerified", configuredFieldsAreWrittenAndVerified},
        {"verifyErrorFailsStartup", verifyErrorFailsStartup},
        {"testModeIsDeviceError", testModeIsDeviceError},
        {"neverEnablesByItself", neverEnablesByItself},
        {"enableAndDisable", enableAndDisable},
        {"enableRefusedWithPendingFaults", enableRefusedWithPendingFaults},
        {"shortWhileEnabledSwitchesOff", shortWhileEnabledSwitchesOff},
        {"reportedShortSwitchesOff", reportedShortSwitchesOff},
        {"clearIsHonestWhileTheConditionIsThere", clearIsHonestWhileTheConditionIsThere},
        {"enableNeedsOneCommonPhaseState", enableNeedsOneCommonPhaseState},
        {"overtemperatureShutdownAndWarning", overtemperatureShutdownAndWarning},
        {"deviceResetWhileEnabled", deviceResetWhileEnabled},
        {"deviceResetWhileDisabled", deviceResetWhileDisabled},
        {"enablePinMismatch", enablePinMismatch},
        {"spiErrorWhileEnabled", spiErrorWhileEnabled},
        {"missingSupplyCostsOneDatagramPerRetry", missingSupplyCostsOneDatagramPerRetry},
        {"supplyConnectedLaterIsDetected", supplyConnectedLaterIsDetected},
        {"protectedRegisters", protectedRegisters},
        {"physicalUnitConversions", physicalUnitConversions},
        {"shortDetectionInPhysicalUnits", shortDetectionInPhysicalUnits},
        {"faultPinNotWired", faultPinNotWired},
        {"consoleEnableCommand", consoleEnableCommand},
        {"safeStateLock", safeStateLock},
        {"safeStateWithoutLockIsAMismatch", safeStateWithoutLockIsAMismatch},
        {"foreignDriverEnableIsTakenBack", foreignDriverEnableIsTakenBack},
        {"missingSupplyIsNotACommunicationFault", missingSupplyIsNotACommunicationFault},
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
