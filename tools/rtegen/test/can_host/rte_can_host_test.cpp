// Host test of the CAN part of the generated RteConfig.cpp for the demo project
// (projects/evalboard_s32k144_demo/rte.yaml + EvalBoard.dbc): start values, cyclic transmission with E2E
// protection (profile 11), saturation, reception with ID/length check, the signal qualifier of PC_Command
// (E2E profile 1: timeout, wrong checksum, repeated counter, healing, 200 ms debouncing, on_error hold)
// and the bus error while the transceiver is muted. OpenBSW interfaces are replaced by stubs/HostStubs.h.
// Expected frame bytes are calculated by hand from the DBC (see comments), the E2E checksums come from the
// independent Python reference model tools/e2e/e2e_ref.py - both independent of the code under test.
// Build and run: tools/rtegen/test_rtegen.py (RteCanHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

bool bios::DigitalInput::value      = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]      = {0U, 0U};
int async::LockType::depth          = 0;

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                  \
    do                                                            \
    {                                                             \
        ++checks;                                                 \
        if (!(c))                                                 \
        {                                                         \
            ++failures;                                           \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                         \
    } while (0)

// PC_Command payload "Setpoint 1500 mV, Offset -300 mV, Mode 2" protected with E2E profile 1
// (data id 0x0210, mode BOTH, CRC byte 4, counter in the low nibble of byte 5): CRC per counter value.
uint8_t const PC_CRC[6] = {0xEFU, 0x60U, 0xECU, 0x63U, 0xE9U, 0x66U};

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
        return busState;
    }

    ErrorCode write(::can::CANFrame const& frame) override
    {
        Sent s{frame.getId(), frame.getPayloadLength(), {0U}};
        std::memcpy(s.data, frame.getPayload(), frame.getPayloadLength());
        sent.push_back(s);
        return result;
    }

    void addCANFrameListener(::can::ICANFrameListener& l) override
    {
        listener = &l;
        ++listenerCount;
    }

    State state                          = State::OPEN;
    ::can::ICANTransceiverStateListener::CANTransceiverState busState
        = ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE;
    ErrorCode result                     = ErrorCode::CAN_ERR_OK;
    ::can::ICANFrameListener* listener   = nullptr;
    int listenerCount                    = 0;
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

void receive(uint32_t id, std::vector<uint8_t> const& bytes)
{
    ::can::CANFrame const frame(id, bytes.data(), static_cast<uint8_t>(bytes.size()));
    FakeTransceiver& t = canSystem.transceiver;
    if ((t.listener != nullptr) && t.listener->getFilter().match(id))
    {
        t.listener->frameReceived(frame);
    }
}

/** PC_Command with the payload above, counter 0..5 and crcError added to the CRC byte. */
void sendCommand(uint8_t const counter, uint8_t const crcError = 0U, uint32_t const idBits = 0U)
{
    receive(
        0x210U | idBits,
        {0x03U, 0xE8U, 0x38U, 0x2FU, static_cast<uint8_t>(PC_CRC[counter] + crcError), counter, 0x00U, 0x00U});
}

bool sentEquals(Sent const& s, uint32_t id, std::vector<uint8_t> const& bytes)
{
    return (s.id == id) && (s.length == bytes.size()) && (std::memcmp(s.data, bytes.data(), bytes.size()) == 0);
}

::rte::CanRxStatus const& rx() { return ::rte::CAN_RX_STATUS[0]; }
} // namespace

namespace systems
{
::can::ICanSystem& getCanSystem() { return canSystem; }
} // namespace systems

int main()
{
    FakeTransceiver& t = canSystem.transceiver;
    CHECK(::rte::CAN_RX_COUNT == 1U);
    CHECK(::rte::CAN_TX_COUNT == 2U);
    CHECK(!::rte::isCanAttached());
    CHECK(rx().timeoutMs == 500U);   // GenMsgTimeoutTime of the DBC
    CHECK(rx().debounceMs == 200U);  // can.qualifiers.PC_Command.debounce_ms
    CHECK(!rx().startOnError);       // on_error: hold

    // Tick 0: start values of PC_Command, listener registered, both messages sent.
    // EVB_Status (0x110, Intel): Led1 bit 0, Led2 bit 1, MotorNtcFiltered_mV 16 bit from bit 8, SwVersion
    //   start 1 in byte 4; E2E P11 (data id 0x0110, mode BOTH) writes the CRC into byte 3 and the counter
    //   into the low nibble of byte 5: counter 0 -> CRC 0x5C.
    // EVB_Analog (0x111, Motorola): Coarse 7|12 factor 2: 1234 mV -> 617 = 0x269 -> 26 9x;
    //   Fine 23|16 signed (0.5,-2500): (1234 + 2500) / 0.5 = 7468 = 0x1D2C.
    Rte_Buf_DemoSwc_Led1_Led1                               = 1U;
    Rte_Buf_DemoSwc_Led2_Led2                               = 0U;
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = 1234U;
    Rte_Buf_CanRx_PC_Command_Setpoint_mV                    = -1.0F;
    Rte_Buf_CanRx_PC_Command_Offset_mV                      = 55;
    cycle();
    CHECK(::rte::isCanAttached());
    CHECK(t.listenerCount == 1);
    CHECK(Rte_Buf_CanRx_PC_Command_Setpoint_mV == 2500.0F); // GenSigStartValue 3000 -> 3000 * 0.5 + 1000
    CHECK(Rte_Buf_CanRx_PC_Command_Offset_mV == -100);      // raw 0 -> offset -100
    CHECK(Rte_Buf_CanRx_PC_Command_Mode == 0U);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_DEBOUNCE);       // nothing received yet
    CHECK(t.sent.size() == 2U);
    if (t.sent.size() == 2U)
    {
        CHECK(sentEquals(t.sent[0], 0x110U, {0x01, 0xD2, 0x04, 0x5C, 0x01, 0x00, 0x00, 0x00}));
        CHECK(sentEquals(t.sent[1], 0x111U, {0x26, 0x90, 0x1D, 0x2C, 0x00, 0x00, 0x00, 0x00}));
    }
    CHECK(::rte::CAN_TX_STATUS[0].sent == 1U);

    // Cycle 100 ms = 10 base periods: nothing in ticks 1..9, both again in tick 10 (E2E counter 1).
    // Saturation: 70000 mV -> Status 65535, Coarse 8190 (raw 4095 = FFF), Fine 10000 (raw 25000 = 0x61A8).
    Rte_Buf_DemoSwc_Led1_Led1                               = 0U;
    Rte_Buf_DemoSwc_Led2_Led2                               = 1U;
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = 70000U;
    cycles(9);
    CHECK(t.sent.size() == 2U);
    cycle();
    CHECK(t.sent.size() == 4U);
    if (t.sent.size() == 4U)
    {
        CHECK(sentEquals(t.sent[2], 0x110U, {0x02, 0xFF, 0xFF, 0x0F, 0x01, 0x01, 0x00, 0x00}));
        CHECK(sentEquals(t.sent[3], 0x111U, {0xFF, 0xF0, 0x61, 0xA8, 0x00, 0x00, 0x00, 0x00}));
    }
    CHECK(::rte::CAN_TX_STATUS[0].protectErrors == 0U);

    // Write refused: counted as failed.
    t.result = ::can::ICanTransceiver::ErrorCode::CAN_ERR_TX_FAIL;
    cycles(10);
    CHECK(::rte::CAN_TX_STATUS[0].failed == 1U);
    CHECK(::rte::CAN_TX_STATUS[1].failed == 1U);
    t.result = ::can::ICanTransceiver::ErrorCode::CAN_ERR_OK;

    // Muted: no write at all, and a bus error for every received message of the bus.
    t.state             = ::can::ICanTransceiver::State::MUTED;
    size_t const before = t.sent.size();
    cycles(30);
    CHECK(t.sent.size() == before);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_ERROR);
    t.state = ::can::ICanTransceiver::State::OPEN;
    cycle();
    // no frame since the start for more than 500 ms: the timeout replaces the bus error
    CHECK(rx().timedOut);
    CHECK(rx().timeouts == 1U);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_ERROR_TIMEOUT);

    // PC_Command 0x210: Setpoint 7|16 Motorola signed (0.5,1000): 1500 mV -> raw 1000 = 03 E8;
    // Offset 16|12 Intel signed (1,-100): -300 mV -> raw -200 = 0xF38 -> byte 2 = 38, byte 3 low nibble F;
    // Mode 28|2 = 2 -> byte 3 bits 4..5 = 0x20. Bytes 4/5 carry the E2E CRC and counter.
    // The first frame is INITIAL for profile 1 (no counter reference yet): the qualifier debounces it and
    // the signals keep their values.
    sendCommand(0U);
    CHECK(rx().received == 1U);
    CHECK(async::LockType::depth == 0);
    cycle();
    CHECK(!rx().timedOut);
    CHECK(rx().ageMs == 0U);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_DEBOUNCE);
    CHECK(rx().aliveErrors == 1U);
    CHECK(Rte_Buf_CanRx_PC_Command_Setpoint_mV == 2500.0F); // not applied: only valid messages are taken over
    sendCommand(1U);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_OK);
    CHECK(Rte_Buf_CanRx_PC_Command_Setpoint_mV == 1500.0F);
    CHECK(Rte_Buf_CanRx_PC_Command_Offset_mV == -300);
    CHECK(Rte_Buf_CanRx_PC_Command_Mode == 2U);
    CHECK(Rte_Buf_CanRx_PC_Command_PcCounter == 1U);

    // Short frame and foreign IDs are ignored; the FD qualifier bit does not matter.
    receive(0x210U, {0x00, 0x00, 0x00, 0x00});
    receive(0x211U, {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00});
    receive(0x80000210U, {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}); // extended 0x210
    cycle();
    CHECK(rx().lengthErrors == 1U);
    CHECK(rx().received == 2U);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_OK);
    sendCommand(2U, 0U, 0x20000000U); // FORCE_NON_FD qualifier bit
    cycle();
    CHECK(rx().received == 3U);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_OK);

    // Wrong checksum: debounced for 200 ms, the values keep their last valid state (on_error hold).
    sendCommand(3U, 1U);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_DEBOUNCE);
    CHECK(rx().crcErrors == 1U);
    cycles(19);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_DEBOUNCE); // 190 ms of 200 ms
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_ERROR_CRC);
    CHECK(Rte_Buf_CanRx_PC_Command_Setpoint_mV == 1500.0F);
    sendCommand(4U);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_OK); // healing after one valid message

    // Counter left standing: repeated counter with a correct checksum.
    sendCommand(4U);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_DEBOUNCE);
    cycles(19);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_DEBOUNCE);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_ERROR_ALIVE);
    CHECK(rx().aliveErrors == 2U);
    sendCommand(5U);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_OK);

    // Timeout 500 ms: 49 periods without a frame keep the qualifier, the 50th reports the timeout.
    cycles(49);
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_OK);
    CHECK(rx().ageMs == 490U);
    cycle();
    CHECK(Rte_Buf_CanQlf_PC_Command == QLF_ERROR_TIMEOUT);
    CHECK(rx().timeouts == 2U);
    CHECK(Rte_Buf_CanRx_PC_Command_Setpoint_mV == 1500.0F); // on_error hold: values stay
    cycle();
    CHECK(rx().timeouts == 2U); // counted once per timeout

    // Frames sent over the whole test: every 10th tick while open (muted ticks skipped), listener once.
    CHECK(t.listenerCount == 1);
    CHECK((::rte::CAN_TX_STATUS[0].sent + ::rte::CAN_TX_STATUS[0].failed) * 2U == t.sent.size());
    CHECK(::rte::CAN_TX_STATUS[0].protectErrors == 0U);

    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
