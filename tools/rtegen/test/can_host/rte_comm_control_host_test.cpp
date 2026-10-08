// Host test of CommunicationControl (UDS 0x28) in the generated COM (COM-29). Same generate and same build
// profile as rte_can_host_test.cpp (demo project rte.yaml + EvalBoard.dbc, no diagnostic stack at all): the
// RTE owns the state and offers rte::setApplicationTxEnabled(), which a diagnostic stack calls. Nothing in
// the generate asks a diagnostic module, so this test is also the proof that the gate needs none.
//
// Checked here:
//   - the default after a reset is "send": without any call of the setter the cyclic messages go out every
//     100 ms, exactly as in a project that has no diagnostic stack
//   - after the tester disables them (0x28 with controlType 0x01/0x03) nothing is written any more, and the
//     counters of CAN_TX_STATUS stay put - the ECU really falls silent, it does not only answer positively
//   - reception keeps working while the transmission is off: signals and the qualifier are still updated
//     (0x28 controlType 0x03 also switches reception off, that state is not evaluated yet - COM-29 follow-up)
//   - after controlType 0x00, or when the session ends, the messages come back in their own phase (multiples
//     of 100 ms of the same tick count) and with the E2E counter one step on, so a receiver sees no gap
//
// Build and run: tools/rtegen/test_rtegen.py (RteCommControlHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"

#include <cstdio>
#include <cstring>
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

struct Sent
{
    uint32_t id;
    uint8_t length;
    uint8_t data[8];
};

class FakeTransceiver : public ::can::ICanTransceiver
{
public:
    State getState() const override { return state; }

    ::can::ICANTransceiverStateListener::CANTransceiverState getCANTransceiverState() const override
    {
        return ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE;
    }

    ErrorCode write(::can::CANFrame const& frame) override
    {
        Sent s{frame.getId(), frame.getPayloadLength(), {0U}};
        std::memcpy(s.data, frame.getPayload(), frame.getPayloadLength());
        sent.push_back(s);
        return ErrorCode::CAN_ERR_OK;
    }

    void addCANFrameListener(::can::ICANFrameListener& l) override { listener = &l; }

    State state                        = State::OPEN;
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

void cycle()
{
    ::rte::readInputs();
    ::rte::writeOutputs();
}

void cycles(int const count)
{
    for (int i = 0; i < count; ++i)
    {
        cycle();
    }
}

// PC_Command (0x210) with "Setpoint 1500 mV, Offset -300 mV, Mode 2", E2E profile 1: CRC per counter value
// (same reference bytes as rte_can_host_test.cpp, from tools/e2e/e2e_ref.py).
uint8_t const PC_CRC[6] = {0xEFU, 0x60U, 0xECU, 0x63U, 0xE9U, 0x66U};

void sendCommand(uint8_t const counter)
{
    uint8_t const bytes[8]
        = {0x03U, 0xE8U, 0x38U, 0x2FU, PC_CRC[counter], counter, 0x00U, 0x00U};
    ::can::CANFrame const frame(0x210U, bytes, 8U);
    FakeTransceiver& t = canSystem.transceiver;
    if ((t.listener != nullptr) && t.listener->getFilter().match(0x210U))
    {
        t.listener->frameReceived(frame);
    }
}

/** E2E profile 11 counter of EVB_Status (0x110): low nibble of byte 5. */
uint8_t statusCounter(Sent const& s) { return static_cast<uint8_t>(s.data[5] & 0x0FU); }
} // namespace

namespace systems
{
::can::ICanSystem& getCanSystem() { return canSystem; }
} // namespace systems

int main()
{
    FakeTransceiver& t = canSystem.transceiver;
    CHECK(::rte::CAN_TX_COUNT == 2U);

    // Default after a reset, without any diagnostic stack and without a single call of the setter: both
    // messages at tick 0, again 100 ms (10 base periods) later.
    Rte_Buf_DemoSwc_Led1_Led1                               = 1U;
    Rte_Buf_DemoSwc_Led2_Led2                               = 0U;
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = 1234U;
    cycle();
    CHECK(t.sent.size() == 2U);
    cycles(10);
    CHECK(t.sent.size() == 4U);
    CHECK(::rte::CAN_TX_STATUS[0].sent == 2U);
    CHECK(::rte::CAN_TX_STATUS[1].sent == 2U);
    uint8_t const counterBefore = t.sent.empty() ? 0xFFU : statusCounter(t.sent[2]);
    CHECK(counterBefore == 1U); // second EVB_Status frame: E2E counter 1

    // The tester disables the non-diagnostic messages (0x28, step (7) of a programming event).
    ::rte::setApplicationTxEnabled(false);
    size_t const sentAtMute       = t.sent.size();
    uint32_t const statusAtMute   = ::rte::CAN_TX_STATUS[0].sent;
    uint32_t const failedAtMute   = ::rte::CAN_TX_STATUS[0].failed;
    uint32_t const protectAtMute  = ::rte::CAN_TX_STATUS[0].protectErrors;
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = 4321U; // the application keeps running
    cycles(100);                                                     // 1 s = ten cycles of both messages
    CHECK(t.sent.size() == sentAtMute);                              // nothing left the ECU
    CHECK(::rte::CAN_TX_STATUS[0].sent == statusAtMute);             // not counted as sent either
    CHECK(::rte::CAN_TX_STATUS[1].sent == statusAtMute);
    CHECK(::rte::CAN_TX_STATUS[0].failed == failedAtMute);           // and not as a failed write
    CHECK(::rte::CAN_TX_STATUS[0].protectErrors == protectAtMute);   // E2E is not run at all

    // Reception is untouched: the first frame is INITIAL for profile 1, the second is valid.
    sendCommand(0U);
    cycle();
    sendCommand(1U);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_OK);
    CHECK(Rte_Buf_CanRx_PC_Command_Setpoint_mV == 1500.0F);
    CHECK(Rte_Buf_CanRx_PC_Command_Mode == 2U);
    CHECK(t.sent.size() == sentAtMute); // still silent

    // controlType 0x00 (enableRxAndTx), or the end of the diagnostic session: the messages come back.
    ::rte::setApplicationTxEnabled(true);
    int cyclesUntilFrame = 0;
    while ((t.sent.size() == sentAtMute) && (cyclesUntilFrame < 20))
    {
        cycle();
        ++cyclesUntilFrame;
    }
    CHECK(t.sent.size() == sentAtMute + 2U);  // both messages of this bus in the same cycle
    CHECK(cyclesUntilFrame <= 10);            // at the latest one cycle time later, in the old phase
    if (t.sent.size() > sentAtMute)
    {
        Sent const& s = t.sent[sentAtMute];
        CHECK(s.id == 0x110U);
        // E2E counter continues where it stopped: no gap for the receiver, and the fresh signal value is
        // in the frame (MotorNtcFiltered_mV, 16 bit Intel from bit 8: 4321 = 0x10E1 -> byte 1 E1, byte 2 10).
        CHECK(statusCounter(s) == static_cast<uint8_t>((counterBefore + 1U) & 0x0FU));
        CHECK((s.data[1] == 0xE1U) && (s.data[2] == 0x10U));
    }

    // And it stays cyclic afterwards.
    size_t const sentAfterResume = t.sent.size();
    cycles(10);
    CHECK(t.sent.size() == sentAfterResume + 2U);

    std::printf("rte_comm_control_host_test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
