#pragma once

#include "console/AsyncCommandWrapper.h"
#include "lifecycle/SingleContextLifecycleComponent.h"
#include "sbc/Uja113x.h"
#include "sbc/Uja113xCanPhy.h"
#include "sbc/Uja113xResetCounter.h"
#include "spi/Lpspi.h"

#include <util/command/GroupCommand.h>

namespace systems
{

/**
 * SYS-07: SBC access from main() before the BSP and the lifecycle exist (src/sbc/SbcEarlyStart.cpp).
 */
class SbcEarlyStart
{
public:
    SbcEarlyStart() = delete;

    /**
     * Call first in main(), before the MCU watchdog fast test: after one of its resets (RCM_SRS.WDOG
     * with WDOG_CS.TST 2/3) RCC in the UJA113x is decremented. Other starts do not touch the SBC.
     */
    static void compensateResetCounter();

    /**
     * Board test (console "sbc failstart"): call before the BSP initialization. If a failed start is
     * armed, one is consumed and the MCU resets here, as a crash before the SBC start would.
     */
    static void failStartIfArmed();

    /** Arms \p count (1..3) failed starts in no-init RAM, 0 disarms. False if RAM is not usable. */
    static bool armFailStarts(uint8_t count);
    /** Remaining armed failed starts, 0 if none or RAM content not valid since power-on. */
    static uint8_t getArmedFailStarts();

    static ::sbc::ResetCounterCompensation const& getCompensation();
    static uint32_t getResetStatus();   // RCM_SRS at the compensation
    static uint8_t getWatchdogTestField(); // WDOG_CS.TST at the compensation
};

/**
 * Runs the UJA113x complex device driver on the eval board: startup at lifecycle init, 10 ms
 * service cycle (watchdog, SBC interrupts, CAN transceiver, mode supervision), console "sbc".
 * Provider of the BSW service bsw.power (RTE-16, SbcPowerReport.h): battery monitor after the startup and every
 * POWER_REPORT_CYCLES service cycles.
 * Role can_phy (getCanPhy): every change of the transceiver readiness is reported to the CAN system through
 * ::can::CanPhyReadiness (COM-23), right after the cycle or the console write that read it.
 * The command runs in the console context (SYS-13); only the SBC accesses of a command run in this system's
 * task, as one snapshot (SbcConsole.h), so the watchdog trigger never waits for the UART.
 * IO-68: where the board wires INTN1 (module connection intn1) the service samples it every cycle and the driver
 * reads the interrupt registers only when it is low (fallback every 100 ms, line supervision); a project pin event on
 * that input adds the pin interrupt, the wake-up line of the MCU (isMcuWakeLineArmed()). Board test "sbc standby".
 */
class SbcSystem
: public ::lifecycle::SingleContextLifecycleComponent
, private ::async::RunnableType
{
public:
    explicit SbcSystem(::async::ContextType context);

    void init() override;
    void run() override;
    void shutdown() override;

    ::sbc::Uja113x& getSbc() { return _sbc; }

    /** Role can_phy of the integrated CAN transceiver. */
    ::bios::CanPhy& getCanPhy() { return _canPhy; }

    /**
     * IO-68: true if INTN1 can wake the MCU from a stop mode - the board wires it (module connection intn1), the
     * project has a pin event on that input (its pin interrupt) and the event is armed now (not disarmed by its rate
     * limit). Without it a stop mode of the MCU (PLAT-108, SYS-156) must not rely on the SBC's wake-up sources.
     */
    bool isMcuWakeLineArmed() const;

#ifdef NX_FLEXNVM_PARTITION_COMMAND
    /**
     * NVM-17 (test build): how long the core may block with interrupts suspended before the SBC watchdog resets
     * it, ms - 0xFFFFFFFF if the watchdog does not run (Forced Normal mode, or autonomous in software development
     * mode: the same decision as the driver's at startup), the period less one service cycle if it runs (timeout or
     * window: the last trigger may be one cycle old), 0 before a successful startup (unknown). Reads what the
     * driver found at startup, no SPI access.
     */
    uint32_t watchdogAllowanceMs() const;
#endif

private:
    class Command : public ::util::command::GroupCommand
    {
    public:
        explicit Command(SbcSystem& system) : _system(system) {}

    protected:
        DECLARE_COMMAND_GROUP_GET_INFO
        void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

    private:
        SbcSystem& _system;
    };

    void execute() override;
    void logResetCounterCompensation();
    void logStartup(::sbc::Uja113x::Status status);
    void reportChanges(uint32_t nowMs);
    /** IO-68: INTN1 now (level, or an edge of its pin event since the previous call); UNKNOWN if not wired. */
    ::sbc::Uja113x::InterruptLine sampleInterruptLine();
    /** IO-68, board test "sbc standby": leaves Standby on a wake-up interrupt or after the requested time. */
    void serviceStandbyTest(uint32_t nowMs, ::sbc::Uja113x::InterruptFlags const& events);
    /** Logs a change of the CAN transceiver readiness and reports it to the CAN system (COM-23). */
    void reportCanReadiness();
    void reportPower();

    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
    ::bsp::spi::Lpspi _spi;
    ::sbc::Uja113x _sbc;
    ::sbc::Uja113xCanPhy _canPhy;
#if !defined(NX_CONSOLE) || NX_CONSOLE
    // PLAT-106: console "sbc" only in an image with console (project.yaml console.enabled, NX_CONSOLE)
    Command _command;
    ::console::AsyncCommandWrapper _asyncCommand;
#endif
    uint32_t _sleepAtMs;
    bool _sleepRequested;
    bool _resetRequested; // console "failstart", reset after SLEEP_DELAY_MS
    bool _canReady;
    ::sbc::Uja113x::State _state;
    uint16_t _startupAttempts;
    uint8_t _powerCycle; // service cycles since the last bsw.power report
    // IO-68: INTN1 and the board test "sbc standby"
    uint32_t _intnCount;         // count of the INTN1 pin event seen last
    uint32_t _intnEdgeUs;        // time of its last accepted edge
    uint32_t _standbyForMs;      // requested time in Standby
    uint32_t _standbySinceMs;
    uint32_t _standbyIntnCount;  // pin event count when Standby was entered
    bool _standbyRequested;      // console request, entered in the next cycle
    bool _standbyTest;           // in Standby because of the console
    bool _lineFaultLogged;
};

} // namespace systems
