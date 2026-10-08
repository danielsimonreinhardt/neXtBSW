#pragma once

#include "sbc/Uja113x.h"

#include <cstddef>
#include <cstdint>

/**
 * Console command "sbc" in the console context (SYS-13): the snapshot of the UJA113x that SbcSystem takes in its
 * own task (SPI accesses, driver state, counters) and the text printed from it afterwards in the console context.
 *
 * Until SYS-13 the whole command ran in the task of the SBC system (TASK_BSP), next to the 10 ms service cycle
 * with the watchdog trigger and - in the same task - the overcurrent supervision of the high-side switches and
 * the supervision of the gate driver: "sbc dump" held it at the UART for about 24 lines (~25 ms), "sbc status"
 * for about 6 lines (~40 ms). Now the task spends only the SPI accesses of one snapshot on a command.
 *
 * Independent of OpenBSW and the board: the text goes through any object with a printf member
 * (util::format::SharedStringWriter on the target), the host test is
 * libs/bsw/consoleContext/test/host/SbcConsoleHostTest.cpp. The text is the one before SYS-13.
 */
namespace systems
{
namespace sbcconsole
{

inline char const* modeName(uint8_t const mode)
{
    switch (mode & UJA113X_MC_MASK)
    {
        case UJA113X_MC_NORMAL: return "Normal";
        case UJA113X_MC_STANDBY: return "Standby";
        case UJA113X_MC_SLEEP: return "Sleep";
        default: return "invalid";
    }
}

/** Everything "sbc status" prints, taken in the task of the SBC system. */
struct StatusSnapshot
{
    char const* driverState;
    uint8_t resetSource;
    char const* resetSourceName;
    bool registersOk; // all five status registers read without SPI error
    uint8_t mode;
    uint8_t supply;
    uint8_t transceiver;
    uint8_t watchdogStatus;
    uint8_t failSafe;
    bool canReady;
    char const* compensationName;
    uint8_t failSafeBefore;
    uint8_t failSafeAfter;
    uint32_t resetStatus; // RCM_SRS at the compensation
    uint8_t watchdogTestField;
    uint8_t armedFailStarts;
    ::sbc::Uja113x::Counters counters;
    bool hasInterruptPins; // NX_USE_DIO_SBC_INTERRUPT1/2
    bool interrupt1Active;
    bool interrupt2Active;
};

template<class Out>
void writeStatus(Out& out, StatusSnapshot const& s)
{
    (void)out.printf(
        "driver: %s, reset source %u (%s)\r\n",
        s.driverState,
        static_cast<unsigned>(s.resetSource),
        s.resetSourceName);
    if (s.registersOk)
    {
        (void)out.printf(
            "mode %s, watchdog status 0x%02x, fail-safe 0x%02x (RCC %u)\r\n",
            modeName(s.mode),
            s.watchdogStatus,
            s.failSafe,
            static_cast<unsigned>(s.failSafe & UJA113X_RCC_MASK));
        (void)out.printf(
            "supply 0x%02x: V1 %s, VEXT/V2 %u (0 ok, 1 UV, 2 OV, 3 off), SMPS %s\r\n",
            s.supply,
            ((s.supply & UJA113X_V1S_MASK) != 0U) ? "undervoltage" : "ok",
            static_cast<unsigned>((s.supply & UJA113X_VEXTS_MASK) >> UJA113X_VEXTS_SHIFT),
            ((s.supply & UJA113X_SMPSS_MASK) != 0U) ? "out of limit" : "ok");
        (void)out.printf(
            "CAN 0x%02x: transmitter %s, VCAN %s, failure %s -> %s\r\n",
            s.transceiver,
            ((s.transceiver & UJA113X_CTS_MASK) != 0U) ? "on" : "off",
            ((s.transceiver & UJA113X_VCS_MASK) != 0U) ? "undervoltage" : "ok",
            ((s.transceiver & UJA113X_CFS_MASK) != 0U) ? "yes" : "no",
            s.canReady ? "ready" : "not ready");
    }
    else
    {
        (void)out.printf("SPI error\r\n");
    }
    (void)out.printf(
        "RCC compensation at start: %s (0x02 0x%02x -> 0x%02x), SRS 0x%x, TST %u, failed starts armed %u\r\n",
        s.compensationName,
        s.failSafeBefore,
        s.failSafeAfter,
        static_cast<unsigned>(s.resetStatus),
        static_cast<unsigned>(s.watchdogTestField),
        static_cast<unsigned>(s.armedFailStarts));
    ::sbc::Uja113x::Counters const& c = s.counters;
    (void)out.printf(
        "counters: startups %u, SPI errors %u, read-back %u, echo %u, WD triggers %u, irq %u, CAN lost %u, mode lost "
        "%u\r\n",
        static_cast<unsigned>(c.startupAttempts),
        static_cast<unsigned>(c.spiErrors),
        static_cast<unsigned>(c.verifyErrors),
        static_cast<unsigned>(c.addressEchoMismatches),
        static_cast<unsigned>(c.watchdogTriggers),
        static_cast<unsigned>(c.interruptEvents),
        static_cast<unsigned>(c.canNotReady),
        static_cast<unsigned>(c.modeLost));
    if (s.hasInterruptPins)
    {
        // INTN pins only for display: the driver polls the interrupt registers.
        (void)out.printf(
            "pins: SBC_INTERRUPT1 %s, SBC_INTERRUPT2 %s\r\n",
            s.interrupt1Active ? "active (low)" : "inactive",
            s.interrupt2Active ? "active (low)" : "inactive");
    }
}

/** Registers of "sbc dump": every documented register of the UJA113x, in address order of the data sheet. */
constexpr uint8_t DUMP_ADDRESSES[] = {
    UJA113X_REG_WATCHDOG_CONTROL,       UJA113X_REG_MODE_CONTROL,
    UJA113X_REG_FAIL_SAFE_CONTROL,      UJA113X_REG_MAIN_STATUS,
    UJA113X_REG_SYSTEM_IRQ_ENABLE,      UJA113X_REG_WATCHDOG_STATUS,
    UJA113X_REG_LOCK_CONTROL,           UJA113X_REG_REGULATOR_CONTROL,
    UJA113X_REG_SUPPLY_STATUS,          UJA113X_REG_SUPPLY_IRQ_ENABLE,
    UJA113X_REG_CAN_CONTROL,            UJA113X_REG_TRANSCEIVER_STATUS,
    UJA113X_REG_TRANSCEIVER_IRQ_ENABLE, UJA113X_REG_BANK0_WAKE_IRQ_ENABLE,
    UJA113X_REG_GLOBAL_IRQ_STATUS,      UJA113X_REG_SYSTEM_IRQ_STATUS,
    UJA113X_REG_SUPPLY_IRQ_STATUS,      UJA113X_REG_TRANSCEIVER_IRQ_STATUS,
    UJA113X_REG_BANK0_WAKE_IRQ_STATUS,  UJA113X_REG_MTPNV_STATUS,
    UJA113X_REG_STARTUP_CONTROL,        UJA113X_REG_SBC_CONFIG_CONTROL,
    UJA113X_REG_IDENTIFICATION_0,       UJA113X_REG_IDENTIFICATION_1,
};
constexpr size_t DUMP_COUNT = sizeof(DUMP_ADDRESSES) / sizeof(DUMP_ADDRESSES[0]);

/** "sbc dump": all registers read in one snapshot, so the dump shows one moment. */
struct DumpSnapshot
{
    uint8_t value[DUMP_COUNT];
    bool ok[DUMP_COUNT];
};

/** Reads the dump registers (call in the task of the SBC system); \p read(address, value) returns true if ok. */
template<class Read>
void takeDump(DumpSnapshot& dump, Read&& read)
{
    for (size_t i = 0U; i < DUMP_COUNT; ++i)
    {
        dump.value[i] = 0U;
        dump.ok[i]    = read(DUMP_ADDRESSES[i], dump.value[i]);
    }
}

template<class Out>
void writeDump(Out& out, DumpSnapshot const& dump)
{
    for (size_t i = 0U; i < DUMP_COUNT; ++i)
    {
        if (dump.ok[i])
        {
            (void)out.printf("0x%02x: 0x%02x\r\n", DUMP_ADDRESSES[i], dump.value[i]);
        }
        else
        {
            (void)out.printf("0x%02x: SPI error\r\n", DUMP_ADDRESSES[i]);
        }
    }
}

} // namespace sbcconsole
} // namespace systems
