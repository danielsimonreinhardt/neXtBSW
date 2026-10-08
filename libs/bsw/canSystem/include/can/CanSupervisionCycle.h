#pragma once

#include <cstdint>

namespace can
{

/**
 * Timing of the CAN system supervision: how often the cycle of the CAN system evaluates the transmit gate
 * (CanPhyGate) and the bus state (CanBusMonitor). Hardware independent, so the host test models exactly the
 * cycle the board port runs.
 *
 * Two rates, one runnable:
 *
 *   gate cycle       compare the phy readiness with the transceiver state, mute/unmute (cheap: one
 *                    virtual call per bus that returns a value the phy provider keeps)
 *   bus state cycle  read the controller's fault confinement state and feed the monitor (every
 *                    busStateEvery-th run)
 *
 * History of the gate cycle. The readiness passes through three samplers - the phy provider (e.g. the SBC
 * driver in task bsp reads its status register every 10 ms), this gate (task can) and the consumer (RTE in its
 * CAN task: signal qualifier and transmit). With a 10 ms gate the consumer saw "not ready" up to 20 ms after
 * the provider knew it and could still write a frame in between (host model CanPhyGateHostTest). COM-22 ran the
 * gate every millisecond for that, on every board, also on one with passive transceivers only (+1.15 % CPU
 * measured at the eval board).
 *
 * Since COM-23 a provider reports every change (CanPhyReadiness): the CAN system evaluates the gate right after
 * the report in its own task, above the consumer's, so the mute is in place before the consumer's next cycle
 * and no frame is written after the report - the latency of COM-22 without a fast cycle. The cycle is back at
 * 10 ms and still checks the gate, as the fallback for a provider that does not report (a lost report is
 * repaired within one cycle, as before COM-22); it runs anyway for the bus state. Reading the controller's bus
 * state faster would not help anybody (bus off needs 256 transmit errors, the state is latched in BOFFINT).
 */
class CanSupervisionCycle
{
public:
    /**
     * Period of the supervision runnable, i.e. of the fallback check of the transmit gate (COM-23; the gate
     * follows the reports of CanPhyReadiness right away). 1 ms in COM-22, 10 ms before.
     */
    static constexpr uint32_t GATE_CYCLE_MS = 10U;

    /** Period of the bus state supervision (unchanged since COM-12). */
    static constexpr uint32_t BUS_STATE_CYCLE_MS = 10U;

    static_assert(
        (GATE_CYCLE_MS > 0U) && ((BUS_STATE_CYCLE_MS % GATE_CYCLE_MS) == 0U),
        "the bus state cycle must be a multiple of the gate cycle");

    /**
     * \param gateCycleMs      period of the runnable
     * \param busStateCycleMs  period of the bus state supervision, a multiple of \p gateCycleMs
     */
    constexpr explicit CanSupervisionCycle(
        uint32_t const gateCycleMs = GATE_CYCLE_MS, uint32_t const busStateCycleMs = BUS_STATE_CYCLE_MS)
    : _gateCycleMs((gateCycleMs == 0U) ? 1U : gateCycleMs)
    , _busStateEvery(
          ((gateCycleMs == 0U) || (busStateCycleMs < gateCycleMs)) ? 1U : (busStateCycleMs / gateCycleMs))
    , _runs(0U)
    {}

    uint32_t gateCycleMs() const { return _gateCycleMs; }

    /** Time between two bus state supervisions (the elapsed time to pass to CanBusMonitor::update). */
    uint32_t busStateCycleMs() const { return _gateCycleMs * _busStateEvery; }

    /**
     * One run of the supervision runnable. The gate is evaluated in every run; returns true if the bus
     * state is due in this run as well (every busStateCycleMs(), first time after one full bus state
     * cycle - the bus state right after opening is taken by the caller).
     */
    bool run()
    {
        ++_runs;
        if (_runs >= _busStateEvery)
        {
            _runs = 0U;
            return true;
        }
        return false;
    }

private:
    uint32_t _gateCycleMs;
    uint32_t _busStateEvery;
    uint32_t _runs;
};

} // namespace can
