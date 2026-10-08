/* Host test of the CAN transmit gate and the can_phy role providers (CanPhyGate, PassiveCanPhy,
 * Uja113xCanPhy), the reports of the readiness (CanPhyReadiness, COM-23) and the latency model of the
 * transceiver status (COM-22, COM-23), no target hardware needed.
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/canSystem/include -Ilibs/bsw/canPhyPassive/include -Ilibs/bsw/sbcUja113x/include \
 *       -Ilibs/bsp/bspSpi/include -isystem external/openbsw/libs/bsw/bsp/include \
 *       libs/bsw/sbcUja113x/src/sbc/Uja113x.cpp libs/bsw/canSystem/test/host/CanPhyGateHostTest.cpp \
 *       -o build/host/can_phy_gate_test.exe
 *   build/host/can_phy_gate_test.exe
 */

#include "can/CanPhyGate.h"
#include "can/CanPhyReadiness.h"
#include "can/CanSupervisionCycle.h"
#include "can/PassiveCanPhy.h"
#include "sbc/Uja113x.h"
#include "sbc/Uja113xCanPhy.h"

#include <cstdio>

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

using ::bios::CanPhy;
using ::can::CanPhyGate;
using Action = CanPhyGate::Action;

/* Transceiver with a settable status, like a part with an error pin. */
class FakePhy : public CanPhy
{
public:
    void init(uint32_t) override {}

    bool setMode(Mode const mode, uint32_t const id) override
    {
        fMode  = mode;
        lastId = id;
        return true;
    }

    ErrorCode getPhyErrorStatus(uint32_t const id) override
    {
        lastId = id;
        return status;
    }

    ErrorCode status = CAN_PHY_ERROR_NONE;
    uint32_t lastId  = 0xFFFFFFFFU;
};

/* SPI without a SBC behind it: every transfer fails, so the driver never reports the transceiver ready. */
class DeadSpi : public ::bsp::spi::ISpiDevice
{
public:
    bool transfer(uint32_t, uint32_t& rx) override
    {
        rx = 0U;
        return false;
    }
};

void testReadiness()
{
    std::printf("phy status -> readiness\n");
    CHECK(CanPhyGate::isReady(CanPhy::CAN_PHY_ERROR_NONE));
    CHECK(!CanPhyGate::isReady(CanPhy::CAN_PHY_ERROR));
    CHECK(CanPhyGate::isReady(CanPhy::CAN_PHY_ERROR_UNSUPPORTED)); // no status available: do not block

    FakePhy phy;
    CHECK(CanPhyGate::isReady(phy, 1U));
    CHECK(phy.lastId == 1U); // the bus id reaches the phy (several buses, one provider)
    phy.status = CanPhy::CAN_PHY_ERROR;
    CHECK(!CanPhyGate::isReady(phy, 0U));
    CHECK(phy.lastId == 0U);

    CanPhy& dummy = ::bios::CanPhyDummy::getInstance(); // OpenBSW dummy reports UNSUPPORTED
    CHECK(CanPhyGate::isReady(dummy, 0U));
}

void testDecision()
{
    std::printf("mute/unmute decision\n");
    // open, muted, ready
    CHECK(CanPhyGate::decide(true, false, true) == Action::NONE);
    CHECK(CanPhyGate::decide(true, false, false) == Action::MUTE);
    CHECK(CanPhyGate::decide(false, true, false) == Action::NONE); // stays muted
    CHECK(CanPhyGate::decide(false, true, true) == Action::UNMUTE);
    CHECK(CanPhyGate::decide(false, false, false) == Action::NONE); // closed/initialized: not touched
    CHECK(CanPhyGate::decide(false, false, true) == Action::NONE);

    CHECK(CanPhyGate::isTransmitAllowed(true, true));
    CHECK(!CanPhyGate::isTransmitAllowed(true, false));
    CHECK(!CanPhyGate::isTransmitAllowed(false, true)); // muted or closed
}

void testSupervisionSequence()
{
    std::printf("supervision sequence\n");
    // Model of CanSystem::updateTransmitPermission with a transceiver state and a phy that drops out.
    enum class State
    {
        OPEN,
        MUTED
    };

    FakePhy phy;
    State state = State::OPEN;
    int mutes   = 0;
    int unmutes = 0;
    auto cycle  = [&]()
    {
        switch (CanPhyGate::decide(
            state == State::OPEN, state == State::MUTED, CanPhyGate::isReady(phy, 0U)))
        {
            case Action::MUTE:
                state = State::MUTED;
                ++mutes;
                break;
            case Action::UNMUTE:
                state = State::OPEN;
                ++unmutes;
                break;
            default: break;
        }
    };
    for (int i = 0; i < 10; ++i)
    {
        cycle();
    }
    CHECK((state == State::OPEN) && (mutes == 0));
    phy.status = CanPhy::CAN_PHY_ERROR;
    for (int i = 0; i < 10; ++i)
    {
        cycle();
    }
    CHECK((state == State::MUTED) && (mutes == 1)); // muted once, not every cycle
    phy.status = CanPhy::CAN_PHY_ERROR_NONE;
    cycle();
    CHECK((state == State::OPEN) && (unmutes == 1));
}

void testRoleProviders()
{
    std::printf("role providers: passive, UJA113x\n");
    ::can::PassiveCanPhy passive;
    CanPhy& p = passive;
    CHECK(p.getMode(0U) == CanPhy::CAN_PHY_MODE_UNDEFINED);
    CHECK(p.setMode(CanPhy::CAN_PHY_MODE_STANDBY, 0U));
    CHECK(p.getMode(0U) == CanPhy::CAN_PHY_MODE_STANDBY);
    CHECK(p.getPhyErrorStatus(0U) == CanPhy::CAN_PHY_ERROR_NONE);
    CHECK(p.getPhyErrorStatus(3U) == CanPhy::CAN_PHY_ERROR_NONE);
    CHECK(CanPhyGate::isReady(p, 0U));

    DeadSpi spi;
    ::sbc::Uja113xConfig const config = {
        0x51U,
        0x00U,
        0x06U,
        ::sbc::WatchdogMode::AUTONOMOUS,
        ::sbc::WatchdogPeriod::MS_128,
        ::sbc::V2Mode::OFF,
        ::sbc::CanMode::ACTIVE,
        0U,
        0U,
        0U,
        0U,
    };
    ::sbc::Uja113x sbc(spi, config);
    ::sbc::Uja113xCanPhy sbcPhy(sbc);
    CanPhy& s = sbcPhy;
    CHECK(!sbc.isCanTransceiverReady()); // not started
    CHECK(s.getPhyErrorStatus(0U) == CanPhy::CAN_PHY_ERROR);
    CHECK(!CanPhyGate::isReady(s, 0U));
    (void)sbc.startup(0U); // SPI fails: stays not ready
    CHECK(s.getPhyErrorStatus(0U) == CanPhy::CAN_PHY_ERROR);
    CHECK(s.setMode(CanPhy::CAN_PHY_MODE_ACTIVE, 0U)); // mode is owned by the SBC driver, only recorded
    CHECK(s.getMode(0U) == CanPhy::CAN_PHY_MODE_ACTIVE);
}

/* ============================================ reports of the readiness (COM-23) ======================== */

/* The CAN system's side of ::can::CanPhyReadiness in the model: a report only marks the gate runnable as ready in
 * task can (CanSystem::phyReadinessChanged -> ::async::execute), several reports collapse into one evaluation. */
class GateEvents : public ::can::ICanPhyReadinessListener
{
public:
    void phyReadinessChanged(CanPhy& phy) override
    {
        pending = true;
        lastPhy = &phy;
        ++reports;
    }

    bool pending     = false;
    CanPhy* lastPhy  = nullptr;
    uint32_t reports = 0U;
};

/* Registers the model's listener for one run and removes it again (the slot is static, like on the target). */
class ListenerScope
{
public:
    ListenerScope(GateEvents& events, bool const active) : _events(events), _active(active)
    {
        if (_active)
        {
            CHECK(::can::CanPhyReadiness::setListener(_events));
        }
    }

    ~ListenerScope()
    {
        if (_active)
        {
            ::can::CanPhyReadiness::removeListener(_events);
        }
    }

    ListenerScope(ListenerScope const&)            = delete;
    ListenerScope& operator=(ListenerScope const&) = delete;

private:
    GateEvents& _events;
    bool _active;
};

void testReadinessReports()
{
    std::printf("readiness reports: one listener, notify without listener\n");
    FakePhy phy;
    ::can::CanPhyReadiness::notify(phy); // no CAN system (board without CAN, before its start): nothing happens
    CHECK(!::can::CanPhyReadiness::hasListener());

    GateEvents first;
    GateEvents second;
    CHECK(::can::CanPhyReadiness::setListener(first));
    CHECK(::can::CanPhyReadiness::setListener(first)); // again: fine
    CHECK(!::can::CanPhyReadiness::setListener(second)); // one CAN system per ECU, the first one stays
    ::can::CanPhyReadiness::notify(phy);
    CHECK(first.pending && (first.reports == 1U) && (first.lastPhy == &phy));
    CHECK(!second.pending);
    ::can::CanPhyReadiness::removeListener(second); // not the registered one: no effect
    CHECK(::can::CanPhyReadiness::hasListener());
    ::can::CanPhyReadiness::removeListener(first);
    CHECK(!::can::CanPhyReadiness::hasListener());
    ::can::CanPhyReadiness::notify(phy);
    CHECK(first.reports == 1U);
    CHECK(::can::CanPhyReadiness::setListener(second));
    ::can::CanPhyReadiness::removeListener(second);
}

/* ============================================ latency of the transceiver status (COM-22, COM-23) ====== */

/* At the eval board VCAN cannot be cut and pulling the connector does not make the SBC report "phy not
 * ready", so the latency from a failing transceiver to the application is shown here, with the real
 * UJA113x driver behind a register model and the three tasks the status passes through:
 *
 *   bsp  (priority 20, 10 ms)  SbcSystem: Uja113x::cyclic() reads the transceiver status register 0x22;
 *                              since COM-23 it reports a change through ::can::CanPhyReadiness (the real
 *                              header), and a console write of 0x20 reads and reports at once
 *   can  (priority 40)         CanSystem: CanPhyGate::decide() -> mute()/unmute() of the transceiver, in the
 *                              supervision cycle (CanSupervisionCycle: 1 ms in COM-22, 10 ms before and since
 *                              COM-23) and, since COM-23, right after a report (gate runnable)
 *   swc  (priority 30, 10 ms)  generated RTE, task of the CAN signals: canBusError() = state != OPEN ->
 *                              QLF_ERROR of every received message; writeCan() writes only while OPEN
 *
 * Priorities and periods as in ecu/asyncCoreConfiguration/tasks.yaml and projects/evalboard_s32k144_demo
 * (rte.yaml: swc 30, 10 ms). Time runs in 1 ms ticks (async tick); the ready tasks of a tick run in the order
 * of their priority, and a task that becomes ready within the tick (the gate after a report) runs as soon as it
 * is the highest ready one - this is how can preempts bsp. A change of the register happens right after all
 * tasks of a tick have run, i.e. just after a read - latencies are therefore "up to" values. Every relative
 * phase of the three tasks and of the change is tried. */

/* SBC register file: reads echo the address (AH1501 2.6), interrupt status registers are write 1 to clear,
 * the global interrupt status is derived from them. Enough for startup() and cyclic() of the driver. With
 * ctsFollowsCmc the transmitter status CTS follows the CAN mode (listen-only/offline: off). */
class SbcRegisterModel : public ::bsp::spi::ISpiDevice
{
public:
    SbcRegisterModel()
    {
        for (uint8_t& r : reg)
        {
            r = 0U;
        }
        reg[UJA113X_REG_IDENTIFICATION_0]   = 0x51U;
        reg[UJA113X_REG_WATCHDOG_STATUS]    = UJA113X_SDMS_MASK; // development mode: autonomous watchdog off
        reg[UJA113X_REG_MODE_CONTROL]       = UJA113X_MC_STANDBY;
        reg[UJA113X_REG_SBC_CONFIG_CONTROL] = 0x06U;
        setTransceiverReady(true);
    }

    bool transfer(uint32_t const tx, uint32_t& rx) override
    {
        uint8_t const address = static_cast<uint8_t>((tx >> UJA113X_SPI_ADDRESS_SHIFT) & 0x7FU);
        bool const readOnly   = (tx & UJA113X_SPI_RO_BIT) != 0U;
        uint8_t const data    = static_cast<uint8_t>(tx & 0xFFU);
        uint8_t const value   = read(address);
        rx = (static_cast<uint32_t>(address) << UJA113X_SPI_ADDRESS_SHIFT) | (tx & UJA113X_SPI_RO_BIT) | value;
        if (readOnly)
        {
            if (address == UJA113X_REG_TRANSCEIVER_STATUS)
            {
                ++statusReads;
            }
        }
        else if ((address >= UJA113X_REG_SYSTEM_IRQ_STATUS) && (address <= UJA113X_REG_BANK1_FAIL_IRQ_STATUS))
        {
            reg[address] = static_cast<uint8_t>(reg[address] & static_cast<uint8_t>(~data));
        }
        else
        {
            reg[address] = data;
        }
        return true;
    }

    /* Not ready = VCAN undervoltage, transmitter off (what a lost VCAN looks like, data sheet VCS/CTS). */
    void setTransceiverReady(bool const ready)
    {
        reg[UJA113X_REG_TRANSCEIVER_STATUS] = ready ? UJA113X_CTS_MASK : UJA113X_VCS_MASK;
    }

    uint32_t statusReads = 0U;
    bool ctsFollowsCmc   = false;

private:
    uint8_t read(uint8_t const address) const
    {
        if (address == UJA113X_REG_GLOBAL_IRQ_STATUS)
        {
            return global();
        }
        if ((address == UJA113X_REG_TRANSCEIVER_STATUS) && ctsFollowsCmc)
        {
            uint8_t const cmc = reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CMC_MASK;
            bool const active = ((reg[UJA113X_REG_MODE_CONTROL] & UJA113X_MC_MASK) == UJA113X_MC_NORMAL)
                                && ((cmc == UJA113X_CMC_ACTIVE) || (cmc == UJA113X_CMC_ACTIVE_NO_UV));
            return static_cast<uint8_t>(
                (reg[address] & static_cast<uint8_t>(~UJA113X_CTS_MASK)) | (active ? UJA113X_CTS_MASK : 0U));
        }
        return reg[address];
    }

    uint8_t global() const
    {
        uint8_t g = 0U;
        for (uint8_t i = 0U; i < 7U; ++i)
        {
            if (reg[UJA113X_REG_SYSTEM_IRQ_STATUS + i] != 0U)
            {
                g = static_cast<uint8_t>(g | (1U << i));
            }
        }
        return g;
    }

    uint8_t reg[128];
};

::sbc::Uja113xConfig const ACTIVE_CAN_CONFIG = {
    0x51U,
    0x00U,
    0x06U,
    ::sbc::WatchdogMode::AUTONOMOUS,
    ::sbc::WatchdogPeriod::MS_128,
    ::sbc::V2Mode::OFF,
    ::sbc::CanMode::ACTIVE, // transmitter on: status read in every cycle (COM-08)
    0U,
    0U,
    0U,
    0U,
};

constexpr uint32_t NEVER = 0xFFFFFFFFU;

/* SbcSystem::reportCanReadiness() of the board port: log (not modelled) and report a change. */
class SbcSystemModel
{
public:
    SbcSystemModel(::sbc::Uja113x& sbc, CanPhy& phy) : _sbc(sbc), _phy(phy), _reported(sbc.isCanTransceiverReady())
    {}

    void reportCanReadiness()
    {
        bool const ready = _sbc.isCanTransceiverReady();
        if (ready != _reported)
        {
            _reported = ready;
            ::can::CanPhyReadiness::notify(_phy);
        }
    }

    bool reported() const { return _reported; }

private:
    ::sbc::Uja113x& _sbc;
    CanPhy& _phy;
    bool _reported;
};

/* ---- CAN controller writing into a transmitter that is off (finding of the board test 2026-09-18) ----
 *
 * Listen-only or a lost VCAN switch the transmitter off, the receiver keeps working. A frame the controller
 * starts then fails at its first dominant bit (it sends dominant, reads recessive): bit error, TEC + 8 ([RM]
 * Rev. 13, 55.4.2.5, ISO 11898-1), error frame, retry. Error passive after 16 attempts, bus off after 32 (TEC >
 * 255, [RM] p. 1822). One attempt: SOF 1 + error flag 6 + delimiter 8 + intermission 3 = 18 bit times, error
 * passive + suspend transmission 8 = 26; 500 kbit/s. This is the slow end: a bit error inside the active error
 * flag counts + 8 as well. Automatic recovery (CTRL1[BOFFREC] = 0, COM-12): 128 x 11 recessive bits on the idle
 * bus ([RM] p. 1815, p. 1822); during bus off TXERRCNT counts these sequences, so the "TEC 99" of the board log
 * means the bus off began about 99 x 11 bit times = 2.2 ms before the 10 ms poll read it. The buffer stays
 * active, so after the recovery the same frame fails again. The mute deactivates the transmit buffers
 * (FlexCANDevice::mute, CODE INACTIVE); an inactive buffer takes no part in the arbitration ([RM] 55.5.7.2,
 * p. 1909), so the retries end with the mute - the mute cannot undo a bus off that happened before it. */
constexpr uint32_t BIT_NS               = 2000U; // 500 kbit/s
constexpr uint32_t ATTEMPT_ACTIVE_BITS  = 18U;
constexpr uint32_t ATTEMPT_PASSIVE_BITS = 26U;
constexpr uint32_t BUS_OFF_US
    = ((16U * ATTEMPT_ACTIVE_BITS) + (16U * ATTEMPT_PASSIVE_BITS)) * BIT_NS / 1000U; // 1408 us
constexpr uint32_t RECOVERY_US = (128U * 11U) * BIT_NS / 1000U;                    // 2816 us

/* Bus off episodes of one frame written into a dead transmitter \p deadUs before its mute. */
uint32_t busOffsWithin(uint32_t const deadUs)
{
    uint32_t count = 0U;
    uint32_t at    = BUS_OFF_US;
    while (at <= deadUs)
    {
        ++count;
        at += RECOVERY_US + BUS_OFF_US;
    }
    return count;
}

/* How the transceiver status changes. */
enum class Change : uint8_t
{
    REGISTER,          // by itself (VCAN lost, CAN failure): seen with the next status read of the SBC cycle
    CAN_CONTROL_WRITE, // console "sbc write 0x20 ..." in the SBC task (the board test of COM-22)
};

/* Relative timing and the variant of one run of the model. */
struct Timing
{
    uint32_t gateCycleMs;     // supervision cycle of the CAN system (CanSupervisionCycle)
    bool reports;             // COM-23: the provider reports every change, the gate runs right after it
    uint32_t sbcPhase;        // first tick of task bsp (0..9)
    uint32_t gatePhase;       // first tick of the supervision in task can (0..gateCycleMs-1)
    uint32_t consumerPhase;   // first tick of the RTE task (0..9)
    uint8_t consumerPriority; // swc 30 below can 40; a project may put its task above can
    Change change;
};

/* What the application saw after the status changed (ms after the change, NEVER if not within the run). */
struct Seen
{
    uint32_t reported;          // SbcSystem reports it (driver readiness changed)
    uint32_t muted;             // transceiver muted (towards not ready) or open again (towards ready)
    uint32_t qualifier;         // RTE qualifier QLF_ERROR (towards not ready) or QLF_OK (towards ready)
    uint32_t writesAfterReport; // frames the RTE wrote after the SBC had reported "not ready"
    uint32_t deadWrites;        // frames the RTE wrote after the transmitter went off (towards not ready)
    uint32_t busOffs;           // bus off episodes of the first of them before the mute
};

constexpr uint32_t SBC_CYCLE_MS      = 10U; // SbcSystem, task bsp
constexpr uint32_t CONSUMER_CYCLE_MS = 10U; // rte::CAN_PERIOD_MS of the demo project, task swc
constexpr uint8_t PRIO_BSP           = 20U;
constexpr uint8_t PRIO_CAN           = 40U;
constexpr uint32_t RUN_AFTER_MS      = 60U;

/**
 * One run: bus up, the transceiver status changes in tick \p changeTick (towards \p towardsReady) - right after
 * the tasks of the tick for Change::REGISTER, inside the SBC task of the tick for Change::CAN_CONTROL_WRITE - and
 * the run lasts until RUN_AFTER_MS after the change.
 */
Seen simulate(Timing const& t, uint32_t const changeTick, bool const towardsReady)
{
    SbcRegisterModel spi;
    spi.ctsFollowsCmc = (t.change == Change::CAN_CONTROL_WRITE);
    ::sbc::Uja113x sbc(spi, ACTIVE_CAN_CONFIG);
    ::sbc::Uja113xCanPhy phy(sbc);
    ::can::CanSupervisionCycle cycle(t.gateCycleMs, ::can::CanSupervisionCycle::BUS_STATE_CYCLE_MS);
    GateEvents events;
    ListenerScope const listener(events, t.reports);
    enum class TxState
    {
        OPEN,
        MUTED
    };

    if (t.change == Change::REGISTER)
    {
        spi.setTransceiverReady(!towardsReady);
    }
    (void)sbc.startup(0U);
    uint8_t canControl = 0U;
    (void)sbc.readRegister(UJA113X_REG_CAN_CONTROL, canControl);
    uint8_t const activeControl = canControl;
    uint8_t const listenOnlyControl
        = static_cast<uint8_t>((canControl & static_cast<uint8_t>(~UJA113X_CMC_MASK)) | UJA113X_CMC_LISTEN_ONLY);
    if ((t.change == Change::CAN_CONTROL_WRITE) && towardsReady)
    {
        (void)sbc.writeRegister(UJA113X_REG_CAN_CONTROL, listenOnlyControl); // start in listen-only
    }
    SbcSystemModel sbcSystem(sbc, phy);
    // start as CanSystem::Channel::start() does: mute before the first frame if not ready
    TxState state = CanPhyGate::isReady(phy, 0U) ? TxState::OPEN : TxState::MUTED;
    bool const settledStart = towardsReady ? (state == TxState::MUTED) : (state == TxState::OPEN);
    CHECK(settledStart);

    Seen seen{NEVER, NEVER, NEVER, 0U, 0U, 0U};
    bool changed           = false;
    uint32_t firstDeadTick = NEVER;
    auto due               = [](uint32_t const tick, uint32_t const phase, uint32_t const period)
    { return (tick >= phase) && (((tick - phase) % period) == 0U); };

    for (uint32_t tick = 1U; tick <= (changeTick + RUN_AFTER_MS); ++tick)
    {
        bool bspDue      = due(tick, t.sbcPhase, SBC_CYCLE_MS);
        bool writeDue    = (t.change == Change::CAN_CONTROL_WRITE) && (tick == changeTick);
        bool gateDue     = due(tick, t.gatePhase, t.gateCycleMs);
        bool consumerDue = due(tick, t.consumerPhase, CONSUMER_CYCLE_MS);
        while (true)
        {
            bool const bspReady = bspDue || writeDue;
            bool const canReady = gateDue || events.pending;
            // highest priority of the ready tasks runs next
            int next          = -1;
            uint8_t nextPrio  = 0U;
            if (bspReady && (PRIO_BSP > nextPrio))
            {
                next     = 0;
                nextPrio = PRIO_BSP;
            }
            if (canReady && (PRIO_CAN > nextPrio))
            {
                next     = 1;
                nextPrio = PRIO_CAN;
            }
            if (consumerDue && (t.consumerPriority > nextPrio))
            {
                next     = 2;
                nextPrio = t.consumerPriority;
            }
            if (next < 0)
            {
                break;
            }
            uint32_t const since = changed ? (tick - changeTick) : NEVER;
            if (next == 0)
            {
                if (writeDue)
                {
                    // console "sbc write 0x20 ..." (SbcSystem::Command, run in the SBC task by the console call)
                    writeDue = false;
                    (void)sbc.writeRegister(
                        UJA113X_REG_CAN_CONTROL, towardsReady ? activeControl : listenOnlyControl);
                    changed = true;
                    sbcSystem.reportCanReadiness();
                }
                else
                {
                    bspDue = false;
                    sbc.cyclic(tick);
                    sbcSystem.reportCanReadiness();
                }
                if (changed && (seen.reported == NEVER) && (sbcSystem.reported() == towardsReady))
                {
                    seen.reported = tick - changeTick;
                }
            }
            else if (next == 1)
            {
                if (gateDue)
                {
                    gateDue = false;
                    (void)cycle.run(); // bus state part not modelled here (CanBusStateHostTest)
                }
                events.pending = false; // a report and the cycle in the same tick: one evaluation is enough
                switch (CanPhyGate::decide(
                    state == TxState::OPEN, state == TxState::MUTED, CanPhyGate::isReady(phy, 0U)))
                {
                    case Action::MUTE:
                        state = TxState::MUTED;
                        if (firstDeadTick != NEVER)
                        {
                            seen.busOffs = busOffsWithin((tick - firstDeadTick) * 1000U);
                        }
                        break;
                    case Action::UNMUTE: state = TxState::OPEN; break;
                    default: break;
                }
                if (changed && (seen.muted == NEVER) && ((state == TxState::OPEN) == towardsReady))
                {
                    seen.muted = since;
                }
            }
            else
            {
                consumerDue = false;
                // readCan(): the message itself keeps arriving (reception continues while muted)
                bool const busError   = (state != TxState::OPEN);
                uint8_t const quality = busError ? 16U /* QLF_ERROR */ : 1U /* QLF_OK */;
                if (changed && (seen.qualifier == NEVER) && ((quality == 1U) == towardsReady))
                {
                    seen.qualifier = since;
                }
                // writeCan(): only while OPEN; the transceiver refuses write() when muted anyway
                if ((state == TxState::OPEN) && !towardsReady && changed)
                {
                    ++seen.deadWrites;
                    if (firstDeadTick == NEVER)
                    {
                        firstDeadTick = tick;
                    }
                    if (seen.reported != NEVER)
                    {
                        ++seen.writesAfterReport;
                    }
                }
            }
        }
        if ((t.change == Change::REGISTER) && (tick == changeTick))
        {
            spi.setTransceiverReady(towardsReady); // right after the tasks of this tick
            changed = true;
        }
    }
    if ((state == TxState::OPEN) && (firstDeadTick != NEVER))
    {
        seen.busOffs = busOffsWithin((changeTick + RUN_AFTER_MS - firstDeadTick) * 1000U); // never muted
    }
    return seen;
}

struct Worst
{
    uint32_t reportedMin = NEVER, reportedMax = 0U;
    uint32_t mutedMax = 0U, qualifierMax = 0U;     // from the change of the register
    uint32_t mutedAfterReportMax = 0U;             // from the report of the SBC driver
    uint32_t qualifierAfterReportMax   = 0U;
    uint32_t qualifierAfterReportMin   = NEVER;
    uint32_t writesAfterReportMax      = 0U;
    uint32_t deadWritesMax             = 0U;
    uint32_t busOffsMax                = 0U;
    uint32_t busOffRuns                = 0U;       // runs with at least one bus off
    uint32_t runs                      = 0U;
    uint32_t missing                   = 0U;       // runs in which a stage never saw the change
};

uint32_t maxOf(uint32_t const a, uint32_t const b) { return (a > b) ? a : b; }

uint32_t minOf(uint32_t const a, uint32_t const b) { return (a < b) ? a : b; }

/* Every phase of bsp, supervision and consumer and every position of the change within a 10 ms cycle. */
Worst sweep(
    uint32_t const gateCycleMs,
    bool const reports,
    uint8_t const consumerPriority,
    bool const towardsReady,
    Change const change = Change::REGISTER)
{
    Worst w;
    for (uint32_t sbcPhase = 0U; sbcPhase < SBC_CYCLE_MS; ++sbcPhase)
    {
        for (uint32_t gatePhase = 0U; gatePhase < gateCycleMs; ++gatePhase)
        {
            for (uint32_t consumerPhase = 0U; consumerPhase < CONSUMER_CYCLE_MS; ++consumerPhase)
            {
                for (uint32_t tick = 20U; tick < 30U; ++tick)
                {
                    Timing const t{
                        gateCycleMs, reports, sbcPhase, gatePhase, consumerPhase, consumerPriority, change};
                    Seen const s = simulate(t, tick, towardsReady);
                    ++w.runs;
                    if ((s.reported == NEVER) || (s.muted == NEVER) || (s.qualifier == NEVER))
                    {
                        ++w.missing;
                        continue;
                    }
                    w.reportedMin             = minOf(w.reportedMin, s.reported);
                    w.reportedMax             = maxOf(w.reportedMax, s.reported);
                    w.mutedMax                = maxOf(w.mutedMax, s.muted);
                    w.qualifierMax            = maxOf(w.qualifierMax, s.qualifier);
                    w.mutedAfterReportMax     = maxOf(w.mutedAfterReportMax, s.muted - s.reported);
                    w.qualifierAfterReportMax = maxOf(w.qualifierAfterReportMax, s.qualifier - s.reported);
                    w.qualifierAfterReportMin = minOf(w.qualifierAfterReportMin, s.qualifier - s.reported);
                    w.writesAfterReportMax    = maxOf(w.writesAfterReportMax, s.writesAfterReport);
                    w.deadWritesMax           = maxOf(w.deadWritesMax, s.deadWrites);
                    w.busOffsMax              = maxOf(w.busOffsMax, s.busOffs);
                    if (s.busOffs > 0U)
                    {
                        ++w.busOffRuns;
                    }
                }
            }
        }
    }
    return w;
}

void printWorst(char const* const label, Worst const& w)
{
    std::printf(
        "  %-38s SBC reports %u..%u ms, muted <= %u ms (%u after report), qualifier <= %u ms "
        "(%u..%u after report), frames after report <= %u, into the dead transmitter <= %u, "
        "bus off in %u runs (<= %u times), %u runs\n",
        label,
        static_cast<unsigned>(w.reportedMin),
        static_cast<unsigned>(w.reportedMax),
        static_cast<unsigned>(w.mutedMax),
        static_cast<unsigned>(w.mutedAfterReportMax),
        static_cast<unsigned>(w.qualifierMax),
        static_cast<unsigned>(w.qualifierAfterReportMin),
        static_cast<unsigned>(w.qualifierAfterReportMax),
        static_cast<unsigned>(w.writesAfterReportMax),
        static_cast<unsigned>(w.deadWritesMax),
        static_cast<unsigned>(w.busOffRuns),
        static_cast<unsigned>(w.busOffsMax),
        static_cast<unsigned>(w.runs));
}

void testSupervisionCycle()
{
    std::printf("supervision cycle: 10 ms, gate (fallback) and bus state in every run\n");
    CHECK(::can::CanSupervisionCycle::GATE_CYCLE_MS == 10U);
    CHECK(::can::CanSupervisionCycle::BUS_STATE_CYCLE_MS == 10U);
    ::can::CanSupervisionCycle cycle;
    CHECK(cycle.gateCycleMs() == 10U);
    CHECK(cycle.busStateCycleMs() == 10U);
    int runs         = 0;
    int busStateRuns = 0;
    for (uint32_t ms = 1U; ms <= 1000U; ++ms) // one second
    {
        if ((ms % cycle.gateCycleMs()) == 0U)
        {
            ++runs;
            if (cycle.run())
            {
                ++busStateRuns;
            }
        }
    }
    CHECK(runs == 100);          // 100 activations of task can per second instead of 1000 (COM-22)
    CHECK(busStateRuns == 100);  // bus state still every 10 ms, not faster
    // the layout of COM-22 (gate 1 ms, bus state every 10th run) is still expressible
    ::can::CanSupervisionCycle com22(1U, 10U);
    for (int i = 1; i < 10; ++i)
    {
        CHECK(!com22.run());
    }
    CHECK(com22.run());
    CHECK(com22.busStateCycleMs() == 10U);
    // nonsense is clamped instead of dividing by zero or never running the bus state
    ::can::CanSupervisionCycle zero(0U, 10U);
    CHECK(zero.gateCycleMs() == 1U);
    CHECK(zero.run());
    ::can::CanSupervisionCycle slower(20U, 10U);
    CHECK(slower.run());
}

void testStatusLatency()
{
    std::printf("latency of the transceiver status, SBC -> gate -> RTE (ms after the change)\n");

    // Before COM-22: gate every 10 ms, no reports. The SBC stage alone is at most one cycle (COM-08), but gate and
    // RTE each add a sampling cycle of their own.
    Worst const oldDown = sweep(10U, false, 30U, false);
    Worst const oldUp   = sweep(10U, false, 30U, true);
    printWorst("gate 10 ms, not ready:", oldDown);
    printWorst("gate 10 ms, ready again:", oldUp);
    CHECK((oldDown.missing == 0U) && (oldUp.missing == 0U));
    CHECK(oldDown.reportedMax == 10U);           // COM-08: the SBC sees it within its 10 ms cycle
    CHECK(oldDown.mutedAfterReportMax == 10U);   // + one gate cycle
    // the application: two more samplers after the report. 19 whole ticks, i.e. just under 20 ms: the SBC
    // reads at the end of its tick (lowest priority), after the gate of the same tick has already run.
    CHECK(oldDown.qualifierAfterReportMax == 19U);
    CHECK(oldDown.qualifierMax == 29U);          // three samplers in a row: just under 30 ms
    CHECK(oldDown.mutedMax == 20U);              // the "rund 20 ms" model value of COM-08
    CHECK(oldDown.writesAfterReportMax >= 1U);   // a frame can still be written after the SBC knew
    CHECK(oldUp.qualifierAfterReportMax == 19U);

    // COM-22: gate every millisecond in task can above the RTE task, no reports.
    Worst const com22Down = sweep(1U, false, 30U, false);
    Worst const com22Up   = sweep(1U, false, 30U, true);
    printWorst("COM-22 gate 1 ms, not ready:", com22Down);
    printWorst("COM-22 gate 1 ms, ready again:", com22Up);
    CHECK((com22Down.missing == 0U) && (com22Up.missing == 0U));
    CHECK(com22Down.mutedAfterReportMax == 1U);
    CHECK(com22Down.qualifierAfterReportMax <= CONSUMER_CYCLE_MS);
    CHECK(com22Down.writesAfterReportMax == 0U);
    CHECK(com22Up.mutedAfterReportMax == 1U);

    // COM-23: supervision back at 10 ms, the SBC reports every change and the gate runs right after it (task can
    // preempts task bsp). Same guarantee as COM-22 - muted <= 1 ms after the report (here in the same
    // millisecond), QLF_ERROR within one RTE cycle, no frame after the report - without a 1 ms cycle.
    uint32_t const cycleMs = ::can::CanSupervisionCycle::GATE_CYCLE_MS;
    Worst const down       = sweep(cycleMs, true, 30U, false);
    Worst const up         = sweep(cycleMs, true, 30U, true);
    printWorst("COM-23 reports, not ready:", down);
    printWorst("COM-23 reports, ready again:", up);
    CHECK((down.missing == 0U) && (up.missing == 0U));
    CHECK(down.reportedMax == 10U);              // unchanged, SBC stage (libs/bsw/sbcUja113x, COM-08)
    CHECK(down.mutedAfterReportMax <= 1U);       // requirement: muted <= 1 ms after the report ...
    CHECK(down.mutedAfterReportMax == 0U);       // ... in the model even in the same millisecond
    CHECK(down.qualifierAfterReportMax <= CONSUMER_CYCLE_MS); // within one 10 ms cycle of the RTE
    CHECK(down.qualifierAfterReportMin >= 1U);
    CHECK(down.mutedMax <= 10U);
    CHECK(down.qualifierMax <= 20U);             // two samplers left: SBC and RTE
    CHECK(down.writesAfterReportMax == 0U);      // no frame after the SBC reported "not ready"
    // back: unmuted right after the report, QLF_OK again within one RTE cycle
    CHECK(up.reportedMax == 10U);
    CHECK(up.mutedAfterReportMax == 0U);
    CHECK(up.qualifierAfterReportMax <= CONSUMER_CYCLE_MS);
    CHECK(up.qualifierMax <= 20U);

    // Several tasks. With reports the gate runs right after the provider's task, before any task released later:
    // an RTE task above task can changes nothing on this path. It does on the fallback path of a provider
    // without reports (and on the bus state, COM-12, which only the 10 ms cycle carries): the RTE task runs
    // before the gate of the same tick and sees the change one cycle later, and may still write a frame. Hence
    // the nxconfig check (tools/nxconfig/checks.py, can task order): the RTE's CAN task below task can.
    Worst const above = sweep(cycleMs, true, 45U, false);
    printWorst("COM-23 reports, RTE task above can:", above);
    CHECK(above.missing == 0U);
    CHECK(above.mutedAfterReportMax == 0U);
    CHECK(above.qualifierAfterReportMax <= CONSUMER_CYCLE_MS);
    CHECK(above.writesAfterReportMax == 0U);
    Worst const fallbackBelow = sweep(cycleMs, false, 30U, false);
    Worst const fallbackAbove = sweep(cycleMs, false, 45U, false);
    printWorst("no reports (fallback), RTE below can:", fallbackBelow);
    printWorst("no reports (fallback), RTE above can:", fallbackAbove);
    CHECK(fallbackBelow.qualifierAfterReportMax == 19U); // = before COM-22
    CHECK(fallbackAbove.qualifierAfterReportMax > fallbackBelow.qualifierAfterReportMax);
    CHECK(fallbackAbove.writesAfterReportMax >= 1U);
}

void testDeadTransmitter()
{
    std::printf("frames into a transmitter that is off, bus off (finding 2026-09-18)\n");
    CHECK(BUS_OFF_US == 1408U);
    CHECK(RECOVERY_US == 2816U);
    CHECK(busOffsWithin(1407U) == 0U);
    CHECK(busOffsWithin(1408U) == 1U);
    CHECK(busOffsWithin(5631U) == 1U);
    CHECK(busOffsWithin(5632U) == 2U);

    // A change the SBC only sees with its next status read (VCAN lost, a CAN failure; and the console write as
    // it was before COM-23): the transmitter is off for up to one SBC cycle before anybody knows. A frame the RTE
    // writes in that window fails at every attempt and takes the controller bus off within 1.4 ms - before the
    // report, so no gate can prevent it, whatever its cycle. The reports only end the window at the report: no
    // frame after it, and the mute stops the retries (at most one more bus off episode per window).
    Worst const com22 = sweep(1U, false, 30U, false);
    Worst const com23 = sweep(::can::CanSupervisionCycle::GATE_CYCLE_MS, true, 30U, false);
    printWorst("COM-22, change of the register:", com22);
    printWorst("COM-23, change of the register:", com23);
    CHECK(com23.deadWritesMax == 1U);            // at most one RTE cycle in the window
    CHECK(com23.busOffRuns > 0U);                // the board finding: bus off, recovered, 10 ms later reported
    CHECK(com23.busOffRuns < com23.runs);
    CHECK(com23.busOffsMax <= com22.busOffsMax); // reports end the window no later than the 1 ms gate

    // The board test of COM-22 switched the transmitter off with "sbc write 0x20 0x03". Since COM-23 the driver
    // reads the status right after that write and SbcSystem reports it in the same SBC task call: the mute is
    // in place before the RTE can write again - no frame into the dead transmitter, no bus off. What remains is
    // a frame that is on the bus at the very moment of the write: it fails, the mute follows within the same
    // task switch (SPI 2 x 16 bit at 500 kHz = 64 us, i.e. at most 2-3 attempts, TEC <= 24 of 256).
    Worst const writeDown = sweep(::can::CanSupervisionCycle::GATE_CYCLE_MS, true, 30U, false,
                                  Change::CAN_CONTROL_WRITE);
    Worst const writeUp   = sweep(::can::CanSupervisionCycle::GATE_CYCLE_MS, true, 30U, true,
                                  Change::CAN_CONTROL_WRITE);
    printWorst("COM-23, sbc write 0x20 listen-only:", writeDown);
    printWorst("COM-23, sbc write 0x20 active again:", writeUp);
    CHECK((writeDown.missing == 0U) && (writeUp.missing == 0U));
    CHECK((writeDown.reportedMin == 0U) && (writeDown.reportedMax == 0U)); // reported with the write
    CHECK(writeDown.mutedMax == 0U);
    CHECK(writeDown.deadWritesMax == 0U);
    CHECK(writeDown.busOffRuns == 0U);
    CHECK(writeDown.qualifierMax <= CONSUMER_CYCLE_MS);
    CHECK((writeUp.reportedMax == 0U) && (writeUp.mutedMax == 0U));
    // the same write without the report (a provider that only reports from its cycle) keeps the window open
    Worst const writeNoReport = sweep(::can::CanSupervisionCycle::GATE_CYCLE_MS, false, 30U, false,
                                      Change::CAN_CONTROL_WRITE);
    printWorst("sbc write 0x20, no report:", writeNoReport);
    CHECK(writeNoReport.deadWritesMax >= 1U);
    CHECK(writeNoReport.busOffRuns > 0U);
}

void testLatencyTwoBusesAndPassive()
{
    std::printf("two buses, provider without reports, board without SBC\n");
    // Two buses, each with its own phy: bus 1 (a transceiver with error pin) fails, bus 0 (SBC) does not.
    SbcRegisterModel spi;
    ::sbc::Uja113x sbc(spi, ACTIVE_CAN_CONFIG);
    ::sbc::Uja113xCanPhy sbcPhy(sbc);
    (void)sbc.startup(0U);
    bool const variants[] = {true, false};
    for (bool const pinReports : variants)
    {
        FakePhy pinPhy;
        GateEvents events;
        ListenerScope const listener(events, true);
        CanPhy* const phys[2] = {&sbcPhy, &pinPhy};
        bool open[2]          = {true, true};
        bool muted[2]         = {false, false};
        ::can::CanSupervisionCycle cycle;
        uint32_t mutedAt[2] = {NEVER, NEVER};
        uint32_t gateRuns   = 0U;
        auto gate           = [&](uint32_t const tick)
        {
            ++gateRuns;
            for (uint32_t bus = 0U; bus < 2U; ++bus)
            {
                switch (CanPhyGate::decide(open[bus], muted[bus], CanPhyGate::isReady(*phys[bus], bus)))
                {
                    case Action::MUTE:
                        open[bus]    = false;
                        muted[bus]   = true;
                        mutedAt[bus] = tick;
                        break;
                    case Action::UNMUTE:
                        open[bus]  = true;
                        muted[bus] = false;
                        break;
                    default: break;
                }
            }
        };
        for (uint32_t tick = 1U; tick <= 100U; ++tick)
        {
            if ((tick % SBC_CYCLE_MS) == 0U)
            {
                sbc.cyclic(tick);
            }
            if ((tick % cycle.gateCycleMs()) == 0U)
            {
                (void)cycle.run();
                gate(tick);
            }
            if (tick == 53U)
            {
                pinPhy.status = CanPhy::CAN_PHY_ERROR; // in tick 53, between two supervision runs
                if (pinReports)
                {
                    ::can::CanPhyReadiness::notify(pinPhy); // its driver reports (pin interrupt or poll)
                }
            }
            if (events.pending)
            {
                events.pending = false;
                gate(tick); // gate runnable in task can
            }
        }
        // reported: muted in the same millisecond; without a report the 10 ms cycle catches it (as before COM-22)
        CHECK(mutedAt[1] == (pinReports ? 53U : 60U));
        CHECK(mutedAt[0] == NEVER);
        CHECK(open[0] && !muted[0]);
        CHECK(pinPhy.lastId == 1U); // each bus asks its phy with its own bus id
        CHECK(gateRuns == (pinReports ? 11U : 10U));
        CHECK(events.reports == (pinReports ? 1U : 0U));
    }

    // Without SBC (passive transceiver): nothing reports, the gate never mutes; bus errors come from the
    // controller only (CanBusStateHostTest). No activation of task can for the gate: in one second the CAN
    // system runs its 10 ms cycle (100 runs, needed for the bus state anyway) instead of 1000 runs (COM-22).
    ::can::PassiveCanPhy passive;
    GateEvents events;
    ListenerScope const listener(events, true);
    ::can::CanSupervisionCycle cycle;
    bool passiveOpen = true;
    int passiveMutes = 0;
    uint32_t runs    = 0U;
    for (uint32_t tick = 1U; tick <= 1000U; ++tick)
    {
        if (((tick % cycle.gateCycleMs()) == 0U) || events.pending)
        {
            events.pending = false;
            ++runs;
            (void)cycle.run();
            if (CanPhyGate::decide(passiveOpen, !passiveOpen, CanPhyGate::isReady(passive, 0U)) == Action::MUTE)
            {
                passiveOpen = false;
                ++passiveMutes;
            }
        }
    }
    CHECK(passiveOpen && (passiveMutes == 0));
    CHECK(events.reports == 0U);
    CHECK(runs == 100U);
    std::printf("  passive transceiver: %u runs of the CAN supervision per second (COM-22: 1000), 0 reports\n",
                static_cast<unsigned>(runs));
}
} // namespace

int main()
{
    testReadiness();
    testDecision();
    testSupervisionSequence();
    testRoleProviders();
    testReadinessReports();
    testSupervisionCycle();
    testStatusLatency();
    testDeadTransmitter();
    testLatencyTwoBusesAndPassive();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
