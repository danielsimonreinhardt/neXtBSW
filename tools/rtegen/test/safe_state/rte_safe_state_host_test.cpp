// SIL test of SYS-141: the RTE ports of the safe state selection through the generated RTE of the test/multitask
// components, extended by tools/rtegen/test_rtegen_safe_state.py with
//   AppSwc.SlowCount  (uint32, provided, task app)  -> safety.safe_state.BRIDGE          the requested state
//   AppSwc.Speed      (uint32, required, task app)  <- safety.safe_state_active.BRIDGE   the state in force
//   MotorSwc.Setpoint (uint16, required, task motor)<- safety.qlf.safe_state.BRIDGE      its qualifier
// The hand-over safestate::rte is the one of RteSystem.cpp (Selection of ecu/application/src/systems/safeState/
// SafeStateRte.h); the service behind it is a model with the rules of hardFaultHandler/safeStateSelection.h (the real
// service is exercised by SafeStateRteHostTest.cpp). Fault injection: a state the project does not allow, a short
// removed by a bridge fault, an unknown state number, a corrupted selection word.
// Build and run: tools/rtegen/test_rtegen_safe_state.py.

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"
#include "safeState/SafeStateRte.h"

#include <cstdio>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

extern "C" void Test_Trace(uint8 const /* id */) {}

extern "C" void Test_Hook(uint8 const /* where */) {}

namespace
{
// model of the service: BRIDGE (0) allows all three states, AUX (1) freewheel and short low, FAN (2) freewheel only
constexpr uint32_t ALLOWED[3] = {0x7U, 0x3U, 0x1U};
uint32_t word[3]              = {0U, 0U, 0U}; // the state the walks would apply
bool blocked[3]               = {false, false, false};
unsigned selects              = 0U;
unsigned refusals             = 0U;

struct ModelService
{
    static int32_t select(uint32_t const group, uint32_t const state)
    {
        ++selects;
        if (group >= 3U)
        {
            return -1;
        }
        if ((state >= 3U) || ((ALLOWED[group] & (1UL << state)) == 0U))
        {
            return -2;
        }
        if ((state != 0U) && blocked[group])
        {
            return -3;
        }
        word[group] = state;
        return 0;
    }

    static uint32_t selected(uint32_t const group) { return (group < 3U) ? word[group] : 0U; }

    static uint32_t allowed(uint32_t const group)
    {
        return (group >= 3U) ? 0U : (blocked[group] ? 0x1U : ALLOWED[group]);
    }
};

struct ModelLog
{
    static void refused(uint32_t, uint32_t, int32_t) { ++refusals; }
};

::safestate::Selection<ModelService, ModelLog, ::rte::SAFE_STATE_GROUP_COUNT> selection;
} // namespace

namespace safestate
{
namespace rte
{
// as RteSystem.cpp
void request(uint32_t const group, uint32_t const state) { selection.request(group, state); }

void read(uint32_t const group, uint32_t const allowed, uint32_t& active, bool& inForce)
{
    selection.read(group, allowed, active, inForce);
}
} // namespace rte
} // namespace safestate

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

constexpr size_t TASK_MOTOR = 0U;
constexpr size_t TASK_APP   = 1U;

// one cycle of both tasks: the application writes its request, the RTE hands it over at the end of the app task
void cycle(uint32_t const request)
{
    Rte_Buf_AppSwc_SlowCount_SlowCount = request;
    ::rte::writeOutputs(TASK_APP);
    ::rte::readInputs(TASK_APP);
    ::rte::readInputs(TASK_MOTOR);
}

uint32_t active() { return Rte_Buf_AppSwc_Speed_Speed; }

uint16_t qualifier() { return Rte_Cpy_motor_MotorSwc_Setpoint_Setpoint; }

void test()
{
    static_assert(::rte::SAFE_STATE_GROUP_COUNT == 3U, "BRIDGE, AUX, FAN");
    // after a reset: freewheel requested and in force
    cycle(0U);
    CHECK(active() == 0U);
    CHECK(qualifier() == QLF_OK);
    CHECK(refusals == 0U);

    // short low, then short high: handed over every cycle, in force
    cycle(1U);
    CHECK(word[0] == 1U);
    CHECK(active() == 1U);
    CHECK(qualifier() == QLF_OK);
    unsigned const before = selects;
    cycle(2U);
    cycle(2U);
    CHECK(selects == before + 2U); // every cycle, not on change only (a lost word is rewritten)
    CHECK(active() == 2U);
    CHECK(qualifier() == QLF_OK);

    // an unknown state number: refused once, the selection stays, qualifier ERROR while it is requested
    cycle(7U);
    CHECK(refusals == 1U);
    CHECK(active() == 2U);
    CHECK(qualifier() == QLF_ERROR);
    cycle(7U);
    CHECK(refusals == 1U); // reported once per change, not every cycle

    // a bridge fault removes the shorts of BRIDGE until power-on: the walks apply freewheel
    blocked[0] = true;
    word[0]    = 0U;
    cycle(2U);
    CHECK(refusals == 2U);
    CHECK(active() == 0U);
    CHECK(qualifier() == QLF_ERROR);
    // freewheel is accepted, but a state of the project is gone: the qualifier stays ERROR
    cycle(0U);
    CHECK(active() == 0U);
    CHECK(qualifier() == QLF_ERROR);
    CHECK(refusals == 2U);

    // power-on: the block is gone; a corrupted word (the walks apply something else than requested) is seen
    blocked[0] = false;
    cycle(1U);
    CHECK(qualifier() == QLF_OK);
    word[0] = 0U; // the next hand-over has not come yet: read before the request of this cycle
    ::rte::readInputs(TASK_MOTOR);
    CHECK(qualifier() == QLF_ERROR);
    cycle(1U); // rewritten every cycle
    CHECK(active() == 1U);
    CHECK(qualifier() == QLF_OK);
}
} // namespace

int main()
{
    test();
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
