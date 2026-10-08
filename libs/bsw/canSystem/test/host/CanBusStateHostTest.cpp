/* Host test of the CAN bus state (COM-12): fault confinement mapping, the transitions
 * error active -> bus off -> back with their counters and times, two buses side by side, a board
 * without SBC (passive transceiver: the bus error can only come from the controller), the effect on the
 * signal qualifier (precedence Bus > Timeout > CRC > Alive, COM-03) and the reading mistake in
 * OpenBSW's FlexCANDevice::getBusOffState() that made the qualifier unusable at the board.
 * No target hardware needed.
 *
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
 *       -Ilibs/bsw/canSystem/include -Ilibs/bsw/canPhyPassive/include \
 *       -isystem external/openbsw/libs/bsw/bsp/include \
 *       libs/bsw/canSystem/test/host/CanBusStateHostTest.cpp -o build/host/can_bus_state_test.exe
 *   build/host/can_bus_state_test.exe
 */

#include "can/CanBusState.h"
#include "can/CanPhyGate.h"
#include "can/PassiveCanPhy.h"

#include <cstdio>
#include <string_view>

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

using ::can::CanBusMonitor;
using ::can::CanBusState;
using Event = CanBusMonitor::Event;

/* ---------------------------------------------------------------- register model of one FlexCAN bus */

/* The two ESR1 fields this test needs, at their real positions (S32K1xx reference manual):
 *   bit 0    WAKINT   wake interrupt flag (latched)
 *   bit 1    ERRINT   error interrupt flag (latched, set by the first error frame)
 *   bit 2    BOFFINT  bus off interrupt flag (latched)
 *   bit 4..5 FLTCONF  fault confinement: 00 error active, 01 error passive, 1x bus off
 *   bit 7    IDLE
 */
constexpr uint32_t ESR1_ERRINT  = 0x2U;
constexpr uint32_t ESR1_BOFFINT = 0x4U;
constexpr uint32_t FLTCONF_MASK = 0x30U;
constexpr uint32_t FLTCONF_SHIFT = 4U;

uint32_t esr1Of(uint32_t const faultConfinement, bool const errorInterrupt, bool const everBusOff)
{
    uint32_t value = (faultConfinement << FLTCONF_SHIFT) & FLTCONF_MASK;
    value |= 0x80U; // IDLE, as seen at the board
    if (errorInterrupt)
    {
        value |= ESR1_ERRINT;
    }
    if (everBusOff)
    {
        value |= ESR1_BOFFINT;
    }
    return value;
}

/* How the board port reads the field: mask first, then shift back (COM-09, CanSystem::fieldValue). */
uint8_t faultConfinementOf(uint32_t const esr1)
{
    return static_cast<uint8_t>((esr1 & FLTCONF_MASK) >> FLTCONF_SHIFT);
}

/* How OpenBSW reads it: FLEXCAN_ESR1_FLTCONF(x) is the NXP **setter** macro, so the whole register is
 * shifted left and masked - the result are bits 0..1 of ESR1 (WAKINT, ERRINT), not the field.
 * external/openbsw/.../FlexCANDevice.h: (FLEXCAN_ESR1_FLTCONF(ESR1) > 1) || fBusOff, with fBusOff sticky.
 */
uint32_t openbswFltconfExpression(uint32_t const esr1)
{
    return ((esr1 << FLTCONF_SHIFT) & FLTCONF_MASK);
}

class OpenbswBusOffModel
{
public:
    /* FlexCANDevice::getBusOffState() == BUS_OFF */
    bool isBusOff(uint32_t const esr1)
    {
        if ((openbswFltconfExpression(esr1) > 1U) || _sticky)
        {
            _sticky = true;
            return true;
        }
        return false;
    }

    void frameReceived() { _sticky = false; } // receiveISR() clears it, getBusOffState() sets it again

private:
    bool _sticky = false;
};

/* ------------------------------------------------------------------------------------ the mapping */

void testFaultConfinementMapping()
{
    std::printf("fault confinement -> bus state\n");
    CHECK(CanBusMonitor::fromFaultConfinement(0U) == CanBusState::ERROR_ACTIVE);
    CHECK(CanBusMonitor::fromFaultConfinement(1U) == CanBusState::ERROR_PASSIVE);
    CHECK(CanBusMonitor::fromFaultConfinement(2U) == CanBusState::BUS_OFF);
    CHECK(CanBusMonitor::fromFaultConfinement(3U) == CanBusState::BUS_OFF); // 1x, both mean bus off

    // controllers with separate flags (bxCAN ESR: EPVF/BOFF), so the same logic serves STM32
    CHECK(CanBusMonitor::fromFlags(false, false) == CanBusState::ERROR_ACTIVE);
    CHECK(CanBusMonitor::fromFlags(true, false) == CanBusState::ERROR_PASSIVE);
    CHECK(CanBusMonitor::fromFlags(true, true) == CanBusState::BUS_OFF);
    CHECK(CanBusMonitor::fromFlags(false, true) == CanBusState::BUS_OFF); // bus off wins

    // only bus off invalidates the received signals
    CHECK(!CanBusMonitor::isBusError(CanBusState::ERROR_ACTIVE));
    CHECK(!CanBusMonitor::isBusError(CanBusState::ERROR_PASSIVE));
    CHECK(!CanBusMonitor::isBusError(CanBusState::UNKNOWN));
    CHECK(CanBusMonitor::isBusError(CanBusState::BUS_OFF));

    // plain text of "can status" and "rte can"
    CHECK(std::string_view(CanBusMonitor::name(CanBusState::ERROR_ACTIVE)) == "error active");
    CHECK(std::string_view(CanBusMonitor::name(CanBusState::ERROR_PASSIVE)) == "error passive");
    CHECK(std::string_view(CanBusMonitor::name(CanBusState::BUS_OFF)) == "bus off");
    CHECK(std::string_view(CanBusMonitor::name(CanBusState::UNKNOWN)) == "unknown");
}

/* ------------------------------------------------------- active -> bus off -> back, with the counters */

void testBusOffAndRecovery()
{
    std::printf("active -> bus off -> back\n");
    CanBusMonitor monitor;
    CHECK(monitor.state() == CanBusState::UNKNOWN); // before the controller is started
    CHECK(!monitor.isBusOff());
    CHECK(monitor.busOffCount() == 0U);

    CHECK(monitor.update(CanBusState::ERROR_ACTIVE, 0U) == Event::STARTED);
    for (int i = 0; i < 5; ++i)
    {
        CHECK(monitor.update(CanBusState::ERROR_ACTIVE, 10U) == Event::NONE); // no event while nothing changes
    }
    CHECK(monitor.state() == CanBusState::ERROR_ACTIVE);
    CHECK(monitor.busOffCount() == 0U);
    CHECK(monitor.busOffMs() == 0U);

    // error passive first (the transmit error counter passed 127), then off the bus
    CHECK(monitor.update(CanBusState::ERROR_PASSIVE, 10U) == Event::WENT_PASSIVE);
    CHECK(monitor.passiveCount() == 1U);
    CHECK(monitor.busOffCount() == 0U); // error passive is not bus off
    CHECK(monitor.update(CanBusState::BUS_OFF, 10U) == Event::WENT_BUS_OFF);
    CHECK(monitor.isBusOff());
    CHECK(monitor.busOffCount() == 1U);
    CHECK(monitor.recoveries() == 0U);
    CHECK(monitor.busOffMs() == 0U); // the phase has just begun

    for (int i = 0; i < 4; ++i)
    {
        CHECK(monitor.update(CanBusState::BUS_OFF, 10U) == Event::NONE);
    }
    CHECK(monitor.busOffMs() == 40U);
    CHECK(monitor.totalBusOffMs() == 40U);

    // the bus is back: the controller recovered on its own (FlexCAN CTRL1[BOFFREC] == 0)
    CHECK(monitor.update(CanBusState::ERROR_ACTIVE, 10U) == Event::RECOVERED);
    CHECK(!monitor.isBusOff());
    CHECK(monitor.state() == CanBusState::ERROR_ACTIVE);
    CHECK(monitor.recoveries() == 1U);
    CHECK(monitor.busOffCount() == 1U);
    CHECK(monitor.busOffMs() == 50U);        // duration of the phase that just ended
    CHECK(monitor.longestBusOffMs() == 50U);
    CHECK(monitor.totalBusOffMs() == 50U);

    // second, shorter bus off: the longest one is kept, the total adds up
    CHECK(monitor.update(CanBusState::BUS_OFF, 10U) == Event::WENT_BUS_OFF);
    CHECK(monitor.update(CanBusState::BUS_OFF, 10U) == Event::NONE);
    CHECK(monitor.update(CanBusState::ERROR_ACTIVE, 10U) == Event::RECOVERED);
    CHECK(monitor.busOffCount() == 2U);
    CHECK(monitor.recoveries() == 2U);
    CHECK(monitor.busOffMs() == 20U);
    CHECK(monitor.longestBusOffMs() == 50U); // not overwritten by the shorter phase
    CHECK(monitor.totalBusOffMs() == 70U);

    // back from error passive without a bus off in between
    CHECK(monitor.update(CanBusState::ERROR_PASSIVE, 10U) == Event::WENT_PASSIVE);
    CHECK(monitor.update(CanBusState::ERROR_ACTIVE, 10U) == Event::WENT_ACTIVE);
    CHECK(monitor.passiveCount() == 2U);
    CHECK(monitor.busOffCount() == 2U);

    // controller closed (shutdown): state unknown again, counters stay
    CHECK(monitor.stop() == Event::STOPPED);
    CHECK(monitor.state() == CanBusState::UNKNOWN);
    CHECK(monitor.stop() == Event::NONE); // only one event per transition
    CHECK(monitor.busOffCount() == 2U);
    CHECK(monitor.longestBusOffMs() == 50U);
    // opened again: the first state is STARTED, not WENT_ACTIVE
    CHECK(monitor.update(CanBusState::ERROR_ACTIVE, 10U) == Event::STARTED);

    // a bus that is off right after opening is counted, too
    CanBusMonitor cold;
    CHECK(cold.update(CanBusState::BUS_OFF, 0U) == Event::WENT_BUS_OFF);
    CHECK(cold.busOffCount() == 1U);
    CHECK(cold.recoveries() == 0U);

    // saturating time, no wrap
    CanBusMonitor longOff;
    (void)longOff.update(CanBusState::BUS_OFF, 0U);
    (void)longOff.update(CanBusState::BUS_OFF, UINT32_MAX);
    (void)longOff.update(CanBusState::BUS_OFF, 1000U);
    CHECK(longOff.busOffMs() == UINT32_MAX);
    CHECK(longOff.totalBusOffMs() == UINT32_MAX);
}

/* --------------------------------------------------------------------------------- two buses */

void testTwoBuses()
{
    std::printf("two buses, independent counters\n");
    CanBusMonitor can0;
    CanBusMonitor can1;
    (void)can0.update(CanBusState::ERROR_ACTIVE, 0U);
    (void)can1.update(CanBusState::ERROR_ACTIVE, 0U);

    // only CAN1 goes off the bus (e.g. its own wiring): CAN0 must not be affected
    CHECK(can1.update(CanBusState::BUS_OFF, 10U) == Event::WENT_BUS_OFF);
    CHECK(can0.update(CanBusState::ERROR_ACTIVE, 10U) == Event::NONE);
    CHECK(can1.isBusOff());
    CHECK(!can0.isBusOff());
    CHECK(can0.busOffCount() == 0U);
    CHECK(can1.busOffCount() == 1U);
    (void)can1.update(CanBusState::BUS_OFF, 30U);
    (void)can0.update(CanBusState::ERROR_ACTIVE, 30U);
    CHECK(can1.busOffMs() == 30U);
    CHECK(can0.busOffMs() == 0U);
    CHECK(can1.update(CanBusState::ERROR_ACTIVE, 10U) == Event::RECOVERED);
    CHECK(can0.update(CanBusState::ERROR_ACTIVE, 10U) == Event::NONE);
    CHECK(can0.recoveries() == 0U);
    CHECK(can1.recoveries() == 1U);
}

/* -------------------------------------------------- board without SBC: only the controller can report */

void testBoardWithoutSbc()
{
    std::printf("board without SBC (passive transceiver)\n");
    // A board with a passive transceiver (no error pin, no SPI status) always reports "ready", so the
    // transmit gate never mutes and a bus error can only come from the controller. Before COM-12 such a
    // board could not report a bus error at all.
    ::can::PassiveCanPhy passive;
    ::bios::CanPhy& phy = passive;
    CHECK(::can::CanPhyGate::isReady(phy, 0U));
    CHECK(::can::CanPhyGate::decide(true, false, ::can::CanPhyGate::isReady(phy, 0U))
          == ::can::CanPhyGate::Action::NONE);
    CHECK(::can::CanPhyGate::isTransmitAllowed(true, ::can::CanPhyGate::isReady(phy, 0U)));

    CanBusMonitor monitor;
    (void)monitor.update(CanBusMonitor::fromFaultConfinement(faultConfinementOf(esr1Of(0U, true, false))), 0U);
    CHECK(monitor.state() == CanBusState::ERROR_ACTIVE); // ERRINT latched, but the bus is fine
    CHECK(!CanBusMonitor::isBusError(monitor.state()));
    // the controller goes off the bus; the phy still says "ready"
    CHECK(monitor.update(CanBusMonitor::fromFaultConfinement(faultConfinementOf(esr1Of(2U, true, true))), 10U)
          == Event::WENT_BUS_OFF);
    CHECK(CanBusMonitor::isBusError(monitor.state()));
    CHECK(::can::CanPhyGate::isReady(phy, 0U)); // unchanged: the two sources are independent

    // a dummy phy (no can_phy module wired at all) behaves the same way
    ::bios::CanPhy& dummy = ::bios::CanPhyDummy::getInstance();
    CHECK(::can::CanPhyGate::isReady(dummy, 0U));
}

/* ------------------------------------------- the reading mistake that hid behind the qualifier */

void testOpenbswReadingMistake()
{
    std::printf("OpenBSW getBusOffState(): setter macro instead of a field read\n");
    // Values measured at the board (COM-09/COM-03 board tests, 2026-09-16): ESR1 0x00240082 with
    // FLTCONF 0 (error active), TEC 30, REC 0, and BOFFINT clear - the bus was never off.
    constexpr uint32_t BOARD_ESR1 = 0x00240082U;
    CHECK(faultConfinementOf(BOARD_ESR1) == 0U);                          // really error active
    CHECK((BOARD_ESR1 & ESR1_BOFFINT) == 0U);                             // never went bus off
    CHECK((BOARD_ESR1 & ESR1_ERRINT) != 0U);                              // but an error frame was seen
    CHECK(CanBusMonitor::fromFaultConfinement(faultConfinementOf(BOARD_ESR1)) == CanBusState::ERROR_ACTIVE);
    CHECK(!CanBusMonitor::isBusError(CanBusMonitor::fromFaultConfinement(faultConfinementOf(BOARD_ESR1))));

    // The driver's expression answers 32 for the same register (the board reported "FLTCONF 32" before
    // COM-09 fixed the console output with exactly this argument).
    CHECK(openbswFltconfExpression(BOARD_ESR1) == 32U);
    OpenbswBusOffModel driver;
    CHECK(driver.isBusOff(BOARD_ESR1)); // spurious bus off, and it is sticky from now on
    driver.frameReceived();
    CHECK(driver.isBusOff(BOARD_ESR1)); // receiving does not help, the expression is evaluated again

    // A perfectly quiet bus is answered correctly, which is why the mistake stays hidden until the first
    // error frame latches ERRINT.
    OpenbswBusOffModel fresh;
    CHECK(!fresh.isBusOff(esr1Of(0U, false, false)));
    CHECK(fresh.isBusOff(esr1Of(0U, true, false))); // one error frame is enough, for ever
    CHECK(fresh.isBusOff(esr1Of(0U, false, false)));

    // Our own reading is right in every combination, and it is the only one the qualifier uses.
    for (uint32_t fltconf = 0U; fltconf < 4U; ++fltconf)
    {
        for (int flags = 0; flags < 4; ++flags)
        {
            uint32_t const esr1 = esr1Of(fltconf, (flags & 1) != 0, (flags & 2) != 0);
            CHECK(faultConfinementOf(esr1) == fltconf);
            CHECK(CanBusMonitor::isBusError(
                      CanBusMonitor::fromFaultConfinement(faultConfinementOf(esr1)))
                  == (fltconf > 1U));
        }
    }
}

/* ------------------------------------------------ effect on the signal qualifier (precedence, COM-03) */

// Values of Rte_Qlf.h (tools/rtegen); repeated here so the test does not need the generated header.
constexpr uint8_t QLF_DEBOUNCE      = 0U;
constexpr uint8_t QLF_OK            = 1U;
constexpr uint8_t QLF_ERROR_ALIVE   = 2U;
constexpr uint8_t QLF_ERROR_CRC     = 4U;
constexpr uint8_t QLF_ERROR_TIMEOUT = 8U;
constexpr uint8_t QLF_ERROR         = 16U;

/* Precedence of the generated RTE (RteCan.cpp.j2, superviseRx): Bus > Timeout > CRC > Alive. */
uint8_t qualifierOf(
    bool const busError, bool const timedOut, bool const crcError, bool const aliveError, bool const valid)
{
    if (busError)
    {
        return QLF_ERROR;
    }
    if (timedOut)
    {
        return QLF_ERROR_TIMEOUT;
    }
    if (crcError)
    {
        return QLF_ERROR_CRC;
    }
    if (aliveError)
    {
        return QLF_ERROR_ALIVE;
    }
    return valid ? QLF_OK : QLF_DEBOUNCE;
}

/* canBusError() of the generated RTE: not open, or bus off. */
bool canBusError(bool const open, CanBusState const state)
{
    return !open || CanBusMonitor::isBusError(state);
}

void testQualifierEffect()
{
    std::printf("effect on the signal qualifier\n");
    // 1. healthy bus: the qualifier of a valid message is QLF_OK - the acceptance criterion of the
    //    board test that failed before COM-12.
    CHECK(!canBusError(true, CanBusState::ERROR_ACTIVE));
    CHECK(qualifierOf(canBusError(true, CanBusState::ERROR_ACTIVE), false, false, false, true) == QLF_OK);

    // 2. error passive (a node alone on its bus): still QLF_OK, and the other states stay visible
    CHECK(!canBusError(true, CanBusState::ERROR_PASSIVE));
    CHECK(qualifierOf(canBusError(true, CanBusState::ERROR_PASSIVE), false, false, false, true) == QLF_OK);
    CHECK(qualifierOf(canBusError(true, CanBusState::ERROR_PASSIVE), true, false, false, true)
          == QLF_ERROR_TIMEOUT);
    CHECK(qualifierOf(canBusError(true, CanBusState::ERROR_PASSIVE), false, true, false, true)
          == QLF_ERROR_CRC);
    CHECK(qualifierOf(canBusError(true, CanBusState::ERROR_PASSIVE), false, false, true, true)
          == QLF_ERROR_ALIVE);

    // 3. bus off: QLF_ERROR for every received message of the bus, it overrules everything
    CHECK(canBusError(true, CanBusState::BUS_OFF));
    CHECK(qualifierOf(canBusError(true, CanBusState::BUS_OFF), false, false, false, true) == QLF_ERROR);
    CHECK(qualifierOf(canBusError(true, CanBusState::BUS_OFF), true, true, true, true) == QLF_ERROR);

    // 4. muted (transceiver not ready, CanPhyGate) is a bus error as well, whatever the bus says
    CHECK(canBusError(false, CanBusState::ERROR_ACTIVE));
    CHECK(qualifierOf(canBusError(false, CanBusState::ERROR_ACTIVE), false, false, false, true) == QLF_ERROR);

    // 5. recovery: after the bus is back the message's own state decides again (healing, Q7)
    CHECK(qualifierOf(canBusError(true, CanBusState::ERROR_ACTIVE), true, false, false, true)
          == QLF_ERROR_TIMEOUT);
    CHECK(qualifierOf(canBusError(true, CanBusState::ERROR_ACTIVE), false, false, false, true) == QLF_OK);

    // 6. this is what the board showed before COM-12: the driver's state made every step look the same
    OpenbswBusOffModel driver;
    constexpr uint32_t BOARD_ESR1 = 0x00240082U;
    bool const driverBusOff       = driver.isBusOff(BOARD_ESR1);
    CHECK(driverBusOff);
    CHECK(qualifierOf(driverBusOff, false, false, false, true) == QLF_ERROR);   // step 2 expected QLF_OK
    CHECK(qualifierOf(driverBusOff, false, true, false, true) == QLF_ERROR);    // step 4 expected 4
    CHECK(qualifierOf(driverBusOff, false, false, true, true) == QLF_ERROR);    // step 5 expected 2
    CHECK(qualifierOf(driverBusOff, true, false, false, true) == QLF_ERROR);    // step 6 expected 8
    // with the corrected reading the same steps give the expected values
    CanBusState const real = CanBusMonitor::fromFaultConfinement(faultConfinementOf(BOARD_ESR1));
    CHECK(qualifierOf(canBusError(true, real), false, false, false, true) == QLF_OK);
    CHECK(qualifierOf(canBusError(true, real), false, true, false, true) == QLF_ERROR_CRC);
    CHECK(qualifierOf(canBusError(true, real), false, false, true, true) == QLF_ERROR_ALIVE);
    CHECK(qualifierOf(canBusError(true, real), true, false, false, true) == QLF_ERROR_TIMEOUT);
}

/* ------------------------------------------- the whole supervision cycle of one bus, as CanSystem runs it */

void testSupervisionSequence()
{
    std::printf("supervision sequence of one bus\n");
    // Model of CanSystem::execute(): every 10 ms read ESR1, feed the monitor, decide the qualifier.
    uint32_t esr1 = esr1Of(0U, false, false);
    bool open     = true;
    CanBusMonitor monitor;
    int busOffLogs   = 0;
    int recoveryLogs = 0;
    auto cycle       = [&](uint32_t const elapsedMs)
    {
        CanBusState const state
            = open ? CanBusMonitor::fromFaultConfinement(faultConfinementOf(esr1)) : CanBusState::UNKNOWN;
        Event const event = (state == CanBusState::UNKNOWN) ? monitor.stop() : monitor.update(state, elapsedMs);
        if (event == Event::WENT_BUS_OFF)
        {
            ++busOffLogs;
        }
        if (event == Event::RECOVERED)
        {
            ++recoveryLogs;
        }
        return qualifierOf(canBusError(open, monitor.state()), false, false, false, true);
    };

    CHECK(cycle(0U) == QLF_OK);
    for (int i = 0; i < 20; ++i)
    {
        CHECK(cycle(10U) == QLF_OK); // 200 ms of a healthy bus: no bus error, no log
    }
    CHECK(busOffLogs == 0);

    // the first error frame latches ERRINT - this is where the driver started to lie
    esr1 = esr1Of(0U, true, false);
    CHECK(cycle(10U) == QLF_OK);
    CHECK(busOffLogs == 0);

    // errors pile up: error passive first (no bus error), then bus off
    esr1 = esr1Of(1U, true, false);
    CHECK(cycle(10U) == QLF_OK);
    esr1 = esr1Of(2U, true, true);
    CHECK(cycle(10U) == QLF_ERROR);
    CHECK(busOffLogs == 1); // logged once, not in every cycle
    for (int i = 0; i < 5; ++i)
    {
        CHECK(cycle(10U) == QLF_ERROR);
    }
    CHECK(busOffLogs == 1);
    CHECK(monitor.busOffMs() == 50U);

    // the controller recovered by itself (BOFFREC == 0); BOFFINT stays latched as the memory of it
    esr1 = esr1Of(0U, true, true);
    CHECK(cycle(10U) == QLF_OK);
    CHECK(recoveryLogs == 1);
    CHECK(monitor.busOffCount() == 1U);
    CHECK(monitor.longestBusOffMs() == 60U);
    CHECK((esr1 & ESR1_BOFFINT) != 0U); // "ever bus off yes" in can status

    // shutdown: the bus state is unknown, and "not open" is a bus error of its own
    open = false;
    CHECK(cycle(10U) == QLF_ERROR);
    CHECK(monitor.state() == CanBusState::UNKNOWN);
    CHECK(monitor.busOffCount() == 1U); // counters survive
}
} // namespace

int main()
{
    testFaultConfinementMapping();
    testBusOffAndRecovery();
    testTwoBuses();
    testBoardWithoutSbc();
    testOpenbswReadingMistake();
    testQualifierEffect();
    testSupervisionSequence();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
