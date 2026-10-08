#pragma once

#include "ecuState/EcuState.h"
#include "ecuState/IPowerSupply.h"
#include "sbc/Uja113x.h"

namespace sbc
{

/** Interrupts that a start of the SBC itself leaves pending at startup: power-on, CAN wake-up, HVIO wake-up. */
inline bool uja113xStartInterruptPending(Uja113x::StartupInfo const& startup)
{
    return ((startup.interrupts.system & UJA113X_POSI_MASK) != 0U)
           || ((startup.interrupts.transceiver & (UJA113X_CWI_MASK | UJA113X_PNFDEI_MASK)) != 0U)
           || (startup.interrupts.wake != 0U) || (startup.interrupts.wakeBank1 != 0U);
}

/**
 * NVM-34: whether the SBC itself went through a start (power-on, wake-up or its own reset) for the current start of
 * the MCU, or the MCU started alone (MCU reset with RSTN not connected to the reset of the MCU - eval board JP1 open -
 * or not seen by the SBC).
 *
 * The reset source register RSS only changes with an SBC reset, so after an MCU-only start it still shows an old
 * event (board 2026-10-02: "lc reboot" with JP1 open, RSS 0 power-on of long ago). Every SBC reset and wake-up passes
 * the Reset mode, which increments the reset counter RCC; the startup of the application writes RCC back to its start
 * value (\p configuredResetCounter, Uja113xConfig::resetCounter). An RCC found at that start value without a start
 * interrupt therefore means: no SBC reset since the last startup of the application.
 * Limits: the RCC compensation of the MCU watchdog test resets (SYS-07) assumes RSTN connected; with RSTN not
 * connected it may take back the count of a real SBC reset, then a V1 undervoltage reset without interrupt is
 * reported as an MCU-only start (the MCU share still sees its own supply reset). Four SBC resets before the startup
 * would wrap RCC - the SBC goes to Forced Sleep at the overflow, the next start is a wake-up with its interrupt.
 * A startup that failed cannot tell: true (the start reasons are UNKNOWN then).
 */
inline bool uja113xRestartedForThisStart(
    Uja113x::StartupInfo const& startup, Uja113x::State const state, uint8_t const configuredResetCounter)
{
    if ((state != Uja113x::State::NORMAL) || uja113xStartInterruptPending(startup))
    {
        return true;
    }
    return (startup.failSafeBefore & UJA113X_RCC_MASK) != (configuredResetCounter & UJA113X_RCC_MASK);
}

/**
 * Reasons of the current start from what the driver found at startup (ecustate::wakeup bits).
 *
 * The reset source register RSS only changes with an SBC reset: after a pure MCU reset (JP1 open, software or
 * debugger reset) it still shows the previous event. A wake-up or power-on therefore only counts together with its
 * interrupt that was pending at startup (CWI, bank 0 wake-up, POSI); these interrupts are cleared by the startup of
 * the application, so a later MCU-only start does not see them again and reports wakeup::RESET. NVM-34: the same for
 * every other reset source - with \p restarted false (uja113xRestartedForThisStart()) the RSS is an old one and the
 * start is an MCU reset (wakeup::RESET), not again the watchdog or undervoltage of an earlier start.
 *
 * \param canBusIndex index of the CAN bus at the integrated transceiver (NX_CAN_<bus>_INDEX), 0xFF if none
 */
inline uint32_t uja113xWakeupSources(
    Uja113x::StartupInfo const& startup,
    Uja113x::State const state,
    uint8_t const canBusIndex,
    bool const restarted = true)
{
    using namespace ::ecustate;
    if (state != Uja113x::State::NORMAL)
    {
        return wakeup::UNKNOWN; // startup failed: registers not trustworthy
    }
    uint32_t sources = wakeup::NONE;
    // CWI, or PNFDEI: with partial networking (COM-07) a frame detect error overflow wakes the SBC as well.
    if ((startup.interrupts.transceiver & (UJA113X_CWI_MASK | UJA113X_PNFDEI_MASK)) != 0U)
    {
        sources |= wakeup::canBus(canBusIndex);
    }
    // 0x64/0x66 hold two bits per HVIO (rising and falling edge); either of them is input i (IO-10).
    uint16_t const edges = static_cast<uint16_t>(
        startup.interrupts.wake | (static_cast<uint16_t>(startup.interrupts.wakeBank1) << 8U));
    for (uint8_t i = 0U; (i < wakeup::LOCAL_COUNT) && (i < UJA113X_HVIO_COUNT); ++i)
    {
        if ((edges & (0x03U << (2U * i))) != 0U)
        {
            sources |= wakeup::local(i);
        }
    }
    if ((startup.interrupts.system & UJA113X_POSI_MASK) != 0U)
    {
        sources |= wakeup::POWER;
    }
    if (sources != wakeup::NONE)
    {
        return sources;
    }
    if (!restarted)
    {
        return wakeup::RESET; // NVM-34: RSS of an earlier SBC reset, this start is an MCU reset
    }
    switch (startup.mainStatus & UJA113X_RSS_MASK)
    {
        case UJA113X_RSS_WD_OVERFLOW_SLEEP: return wakeup::TIMER;
        case UJA113X_RSS_WD_TRIGGER_EARLY:
        case UJA113X_RSS_WD_OVERFLOW:
        case UJA113X_RSS_ILLEGAL_WMC_CHANGE: return wakeup::WATCHDOG;
        case UJA113X_RSS_OVERLOAD_EXIT:
        case UJA113X_RSS_V1_UNDERVOLTAGE: return wakeup::SUPPLY;
        case UJA113X_RSS_EXIT_OFF:          // power-on long ago, POSI already cleared: MCU-only reset
        case UJA113X_RSS_CAN_WAKEUP:        // wake-up long ago, CWI already cleared: MCU-only reset
        case UJA113X_RSS_RSTN_PULLED_DOWN:  // MCU or debugger reset seen by the SBC (JP1 closed)
        case UJA113X_RSS_ILLEGAL_SLEEP: return wakeup::RESET;
        default: return wakeup::UNKNOWN; // LIN/HVIO wake-up without interrupt, diagnostic wake-up, FSP
    }
}

/**
 * Role power_supply of the UJA113x (ecustate::IPowerSupply): power down = SBC Sleep (V1 off) with the wake-up
 * sources the project selected (SYS-02): CAN of the integrated transceiver, WAKE/HVIO inputs of bank 0 and a
 * cyclic wake-up through the watchdog.
 * Call from the task of the driver (not thread safe, like Uja113x).
 */
class Uja113xPowerSupply final : public ::ecustate::IPowerSupply
{
public:
    /**
     * \param canBusIndex index of the CAN bus at the integrated transceiver, 0xFF if the bus is not used
     * \param wakeInputMask further HVIO inputs wired on this board as wake-up inputs (bit i = HVIO(i + 1) over
     *        both banks). IO-33: the inputs the driver configuration sets up as wake-up inputs are always offered
     *        (Uja113x::configuredWakeInputs(); the board describes them in board.yaml devices[].pins and the port
     *        builds the HVIO configuration from that). An input neither names is not offered, so a project that
     *        selects it is told that this board does not have it instead of arming an open input. Which edges
     *        wake is part of the HVIO configuration of the driver (IO-10).
     */
    Uja113xPowerSupply(Uja113x& sbc, uint8_t const canBusIndex, uint8_t const wakeInputMask = 0U)
    : _sbc(sbc)
    , _canBusIndex(canBusIndex)
    , _wakeInputMask(static_cast<uint8_t>(wakeInputMask | Uja113x::configuredWakeInputs(sbc.getConfig())))
    {}

    char const* getName() const override { return "UJA113x"; }

    /** Driver in Normal and the MTPNV allows Sleep (SLPC = 0). */
    bool isPowerDownSupported() const override
    {
        return (_sbc.getState() == Uja113x::State::NORMAL)
               && ((_sbc.getStartupInfo().sbcConfig & UJA113X_SLPC_MASK) == 0U);
    }

    uint32_t getStartupWakeupSources() const override
    {
        return uja113xWakeupSources(_sbc.getStartupInfo(), _sbc.getState(), _canBusIndex, restartedForThisStart());
    }

    /** NVM-34: false after an MCU-only start (RCC unchanged, no start interrupt), see uja113xRestartedForThisStart(). */
    bool restartedForThisStart() const override
    {
        return uja113xRestartedForThisStart(
            _sbc.getStartupInfo(), _sbc.getState(), _sbc.getConfig().resetCounter);
    }

    /** In Normal mode all wake-up sources are off; pending interrupts make enterSleep() refuse instead. */
    uint32_t takeWakeupEvents() override { return 0U; }

    /**
     * The CAN bus of the integrated transceiver, the WAKE/HVIO inputs the board wires, and TIMER as long as
     * the watchdog is usable (not Forced Normal mode, MTPNV FNMC = 0). Nothing before a successful startup.
     */
    uint32_t getSupportedWakeupSources() const override
    {
        using namespace ::ecustate;
        if (_sbc.getState() != Uja113x::State::NORMAL)
        {
            return wakeup::NONE;
        }
        uint32_t sources = (_canBusIndex < wakeup::CAN_BUS_COUNT) ? wakeup::canBus(_canBusIndex) : wakeup::NONE;
        for (uint8_t i = 0U; i < wakeup::LOCAL_COUNT; ++i)
        {
            if ((_wakeInputMask & (1U << i)) != 0U)
            {
                sources |= wakeup::local(i);
            }
        }
        if ((_sbc.getStartupInfo().watchdogStatus & UJA113X_FNMS_MASK) == 0U)
        {
            sources |= wakeup::TIMER; // watchdog overflow in Sleep (RSS 12)
        }
        return sources;
    }

    /** Grid of the SBC: the watchdog periods 8 ... 4096 ms, rounded up. */
    uint32_t getCyclicWakeupMs(uint32_t const requestedMs) const override
    {
        if ((requestedMs == 0U) || ((getSupportedWakeupSources() & ::ecustate::wakeup::TIMER) == 0U))
        {
            return 0U;
        }
        return Uja113x::watchdogPeriodMs(Uja113x::watchdogPeriodFor(requestedMs));
    }

    PowerDownResult powerDown(uint32_t const nowMs, ::ecustate::WakeupSelection const& selection) override
    {
        using namespace ::ecustate;
        if (!isPowerDownSupported())
        {
            return PowerDownResult::NOT_SUPPORTED;
        }
        SleepWakeup wakeupConfig{};
        wakeupConfig.can = (_canBusIndex < wakeup::CAN_BUS_COUNT)
                           && ((selection.sources & wakeup::canBus(_canBusIndex)) != 0U);
        for (uint8_t i = 0U; i < wakeup::LOCAL_COUNT; ++i)
        {
            if (((selection.sources & wakeup::local(i)) != 0U) && ((_wakeInputMask & (1U << i)) != 0U))
            {
                wakeupConfig.wakeInputs = static_cast<uint8_t>(wakeupConfig.wakeInputs | (1U << i));
            }
        }
        wakeupConfig.cyclic = ((selection.sources & wakeup::TIMER) != 0U) && (selection.cyclicMs != 0U);
        wakeupConfig.cyclicPeriod
            = Uja113x::watchdogPeriodFor(wakeupConfig.cyclic ? selection.cyclicMs : 0U);
        switch (_sbc.enterSleep(nowMs, wakeupConfig))
        {
            case Uja113x::Status::OK: return PowerDownResult::STARTED;
            case Uja113x::Status::INTERRUPT_PENDING: return PowerDownResult::WAKEUP_PENDING;
            case Uja113x::Status::NOT_READY:
            case Uja113x::Status::NOT_ALLOWED: return PowerDownResult::NOT_SUPPORTED;
            default: return PowerDownResult::FAILED;
        }
    }

private:
    Uja113x& _sbc;
    uint8_t _canBusIndex;
    uint8_t _wakeInputMask;
};

} // namespace sbc
