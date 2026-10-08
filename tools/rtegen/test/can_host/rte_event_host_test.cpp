// Host test of event triggered sending in the generated RteConfig.cpp (COM-38) for the test fixture
// event_rte.yaml + event.dbc (CAN task 10 ms):
//   TxEvtP01   0x300  GenMsgSendType Spontaneous, GenMsgDelayTime 30 ms, E2E profile 1
//   TxMixP02   0x301  CyclicAndSpontaneous, cycle 50 ms, GenMsgDelayTime 20 ms, E2E profile 2
//   TxEvtPlain 0x302  Spontaneous without a minimum gap, no E2E
//   TxCyc      0x303  cyclic 20 ms, no GenMsgSendType value - as before COM-38
// Checked here, cycle by cycle: no frame while the values equal the DBC start values, a change goes out in
// the same cycle, a change inside the minimum gap waits and goes out with the value it has then, the mixed
// message keeps its cycle and a cyclic send inside the gap is held back, a refused write keeps the change
// pending, CommunicationControl (COM-29) mutes the event messages and a change made meanwhile goes out after
// it, the cyclic message ignores changes.
//
// Every frame is printed as "FRAME <cycle> <id> <bytes>"; tools/rtegen/test_rtegen.py (RteEventHostTest)
// checks counter and CRC of each E2E frame against the independent reference model tools/e2e/e2e_ref.py -
// the counter must advance by one per frame, event or cyclic, without gap.

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"

#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <vector>

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

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

constexpr uint32_t ID_EVT_P01   = 0x300U;
constexpr uint32_t ID_MIX_P02   = 0x301U;
constexpr uint32_t ID_EVT_PLAIN = 0x302U;
constexpr uint32_t ID_CYC       = 0x303U;
// index in CAN_TX_STATUS: order of the connections in event_rte.yaml
constexpr size_t IX_EVT_P01   = 0U;
constexpr size_t IX_MIX_P02   = 1U;
constexpr size_t IX_CYC       = 2U;
constexpr size_t IX_EVT_PLAIN = 3U;

struct Sent
{
    uint32_t id;
    uint8_t length;
    uint8_t data[8];
};

class FakeTransceiver : public ::can::ICanTransceiver
{
public:
    State getState() const override { return State::OPEN; }

    ::can::ICANTransceiverStateListener::CANTransceiverState getCANTransceiverState() const override
    {
        return ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE;
    }

    ErrorCode write(::can::CANFrame const& frame) override
    {
        if (refuse)
        {
            return ErrorCode::CAN_ERR_TX_FAIL;
        }
        Sent s{frame.getId(), frame.getPayloadLength(), {0U}};
        std::memcpy(s.data, frame.getPayload(), frame.getPayloadLength());
        sent.push_back(s);
        return ErrorCode::CAN_ERR_OK;
    }

    void addCANFrameListener(::can::ICANFrameListener& l) override { listener = &l; }

    bool refuse                        = false; // hardware queue full: every write is refused
    ::can::ICANFrameListener* listener = nullptr;
    std::vector<Sent> sent;
};

class FakeCanSystem : public ::can::ICanSystem
{
public:
    ::can::ICanTransceiver* getCanTransceiver(uint8_t busId) override
    {
        return (busId == ::busid::CAN0) ? &transceiver : nullptr;
    }

    FakeTransceiver transceiver;
};

FakeCanSystem canSystem;
int tick = 0;
std::vector<Sent> last; // frames of the last cycle

/** One cycle of the CAN task (10 ms); the frames it wrote are in `last` and printed for the E2E check. */
void cycle()
{
    FakeTransceiver& t  = canSystem.transceiver;
    size_t const before = t.sent.size();
    ::rte::readInputs();
    ::rte::writeOutputs();
    last.assign(t.sent.begin() + static_cast<std::ptrdiff_t>(before), t.sent.end());
    for (Sent const& s : last)
    {
        std::printf("FRAME %d 0x%03X", tick, static_cast<unsigned>(s.id));
        for (uint8_t i = 0U; i < s.length; ++i)
        {
            std::printf(" %02X", static_cast<unsigned>(s.data[i]));
        }
        std::printf("\n");
    }
    ++tick;
}

/** The ids the last cycle sent, in this order. */
bool sent(std::initializer_list<uint32_t> const ids)
{
    if (last.size() != ids.size())
    {
        return false;
    }
    size_t i = 0U;
    for (uint32_t const id : ids)
    {
        if (last[i].id != id)
        {
            return false;
        }
        ++i;
    }
    return true;
}

Sent const* frame(uint32_t const id)
{
    for (Sent const& s : last)
    {
        if (s.id == id)
        {
            return &s;
        }
    }
    return nullptr;
}

/** Value byte 0 of TxEvtP01, Flag bit 0 of byte 2 of TxMixP02, Flag bit 0 of byte 0 of TxEvtPlain/TxCyc. */
int valueOf(uint32_t const id)
{
    Sent const* s = frame(id);
    if (s == nullptr)
    {
        return -1;
    }
    return (id == ID_MIX_P02) ? (s->data[2] & 1) : ((id == ID_EVT_P01) ? s->data[0] : (s->data[0] & 1));
}

::rte::CanTxStatus const& tx(size_t const index) { return ::rte::CAN_TX_STATUS[index]; }
} // namespace

namespace systems
{
::can::ICanSystem& getCanSystem() { return canSystem; }
} // namespace systems

int main()
{
    FakeTransceiver& t = canSystem.transceiver;
    CHECK(::rte::CAN_TX_COUNT == 4U);
    CHECK(tx(IX_EVT_P01).cycleMs == 0U);  // event message: no cycle
    CHECK(tx(IX_MIX_P02).cycleMs == 50U);
    CHECK(tx(IX_CYC).cycleMs == 20U);
    CHECK(tx(IX_EVT_PLAIN).cycleMs == 0U);

    auto& value = Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV; // TxEvtP01.Value
    auto& led1  = Rte_Buf_DemoSwc_Led1_Led1;                               // TxMixP02.Flag, TxCyc.Flag
    auto& led2  = Rte_Buf_DemoSwc_Led2_Led2;                               // TxEvtPlain.Flag

    // cycle 0: the values equal the DBC start values (Value 5, flags 0) - the event messages stay quiet,
    // the mixed and the cyclic message start their cycle
    value = 5U;
    led1  = 0U;
    led2  = 0U;
    cycle();
    CHECK(sent({ID_MIX_P02, ID_CYC}));

    // cycle 1: a change goes out in the same cycle
    value = 6U;
    cycle();
    CHECK(sent({ID_EVT_P01}));
    CHECK(valueOf(ID_EVT_P01) == 6);
    // cycles 2 and 3: changes inside the minimum gap of 30 ms wait
    value = 7U;
    cycle();
    CHECK(sent({ID_CYC}));
    value = 8U;
    cycle();
    CHECK(sent({}));
    // cycle 4: 30 ms after the last send - the value it has now, the one in between is never sent
    cycle();
    CHECK(sent({ID_EVT_P01, ID_CYC}));
    CHECK(valueOf(ID_EVT_P01) == 8);

    // cycle 5: the mixed message in its cycle; the event message without a gap sends at once
    led2 = 1U;
    cycle();
    CHECK(sent({ID_MIX_P02, ID_EVT_PLAIN}));
    CHECK(valueOf(ID_EVT_PLAIN) == 1);
    // cycle 6: the mixed message changes 10 ms after its last send (gap 20 ms): waits; TxEvtPlain back to 0
    led1 = 1U;
    led2 = 0U;
    cycle();
    CHECK(sent({ID_CYC, ID_EVT_PLAIN}));
    CHECK(valueOf(ID_CYC) == 1);        // the cyclic message carries the new value in its cycle
    CHECK(valueOf(ID_EVT_PLAIN) == 0);
    // cycle 7: 20 ms after the last send the change goes out
    cycle();
    CHECK(sent({ID_MIX_P02}));
    CHECK(valueOf(ID_MIX_P02) == 1);
    cycle(); // 8
    CHECK(sent({ID_CYC}));
    // cycle 9: the mixed message changes back
    led1 = 0U;
    cycle();
    CHECK(sent({ID_MIX_P02}));
    CHECK(valueOf(ID_MIX_P02) == 0);
    // cycle 10: its cycle is due 10 ms after the last send - held back by the minimum gap ...
    cycle();
    CHECK(sent({ID_CYC}));
    // cycle 11: ... and sent 20 ms after it
    cycle();
    CHECK(sent({ID_MIX_P02}));
    CHECK(tx(IX_MIX_P02).sent == 5U);    // cycles 0, 5, 7, 9 and 11

    // cycle 12: the transceiver refuses every write - the change of TxEvtPlain stays pending
    t.refuse = true;
    led2     = 1U;
    uint32_t const plainFailed = tx(IX_EVT_PLAIN).failed;
    cycle();
    CHECK(sent({}));
    CHECK(tx(IX_EVT_PLAIN).failed == plainFailed + 1U);
    CHECK(tx(IX_CYC).failed == 1U);
    // cycle 13: written again, without waiting for another change
    t.refuse = false;
    cycle();
    CHECK(sent({ID_EVT_PLAIN}));
    CHECK(valueOf(ID_EVT_PLAIN) == 1);
    cycle(); // 14
    CHECK(sent({ID_CYC}));
    cycle(); // 15: mixed cycle, 40 ms after its last send
    CHECK(sent({ID_MIX_P02}));

    // cycles 16..19: CommunicationControl (COM-29) mutes the application messages, a change waits
    ::rte::setApplicationTxEnabled(false);
    value = 9U;
    for (int i = 0; i < 4; ++i)
    {
        cycle();
        CHECK(sent({}));
    }
    // cycle 20: back on - the change goes out at once, the mixed message in its old phase
    ::rte::setApplicationTxEnabled(true);
    cycle();
    CHECK(sent({ID_EVT_P01, ID_MIX_P02, ID_CYC}));
    CHECK(valueOf(ID_EVT_P01) == 9);
    // cycles 21..29: no change, so only the cycles of the mixed (25) and the cyclic message
    for (int i = 21; i < 30; ++i)
    {
        cycle();
        if (i == 25)
        {
            CHECK(sent({ID_MIX_P02}));
        }
        else
        {
            CHECK(sent((i % 2) == 0 ? std::initializer_list<uint32_t>{ID_CYC} : std::initializer_list<uint32_t>{}));
        }
    }

    CHECK(tx(IX_EVT_P01).sent == 3U);
    CHECK(tx(IX_MIX_P02).sent == 8U);    // and 15, 20, 25
    CHECK(tx(IX_EVT_PLAIN).sent == 3U);
    CHECK(tx(IX_EVT_P01).protectErrors == 0U);
    CHECK(tx(IX_MIX_P02).protectErrors == 0U);
    CHECK(async::LockType::depth == 0);

    std::printf("rte_event_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
