// Host test of the signal qualifier in the generated RteConfig.cpp (COM-03,
// doc/architecture/signal-qualifier.md) for the test fixture qualifier_rte.yaml + qualifier.dbc:
// one received message per E2E profile (P01, P02, P11 in mode NIBBLE), one without E2E, one sent message
// with P02 protection. Covered: timeout, wrong checksum, repeated and out of sequence counter, healing
// after one valid message, time based debouncing (0, 20 and 30 ms), both on_error variants (hold and
// init), bus error through the transceiver state (muted and bus off) and the E2E protection of the sent
// message. OpenBSW interfaces are replaced by stubs/HostStubs.h.
//
// The expected frame bytes (CRC per counter value) come from the independent Python reference model
// tools/e2e/e2e_ref.py, not from the C library under test, so a wrong bit offset in the generated
// configuration shows up here.
// Build and run: tools/rtegen/test_rtegen.py (RteQualifierHostTest).

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

#define CHECK(c)                                                      \
    do                                                                \
    {                                                                 \
        ++checks;                                                     \
        if (!(c))                                                     \
        {                                                             \
            ++failures;                                               \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c);   \
        }                                                             \
    } while (0)

// message ids of qualifier.dbc and their index in CAN_RX_STATUS
constexpr uint32_t ID_RX_P01   = 0x100U;
constexpr uint32_t ID_RX_P02   = 0x101U;
constexpr uint32_t ID_RX_P11   = 0x102U;
constexpr uint32_t ID_RX_PLAIN = 0x103U;
constexpr uint32_t ID_TX_P02   = 0x200U;
constexpr size_t IX_PLAIN      = 0U;
constexpr size_t IX_P01        = 1U;
constexpr size_t IX_P02        = 2U;
constexpr size_t IX_P11        = 3U;

// CRC bytes per counter value (e2e_ref.py): P01 data id 0x123 mode BOTH, CRC byte 1, counter byte 2
uint8_t const P01_CRC_11[5] = {0x28U, 0x6EU, 0xA4U, 0xE2U, 0x2DU}; // Value 0x11
uint8_t const P01_CRC_22[5] = {0x19U, 0x5FU, 0x95U, 0xD3U, 0x1CU}; // Value 0x22
// P02 data id list 0x01..0x10, CRC byte 0, counter low nibble of byte 1, Value bytes 2/3 (Intel)
uint8_t const P02_CRC_1234[4] = {0x95U, 0x80U, 0x03U, 0xAAU};      // Value 0x1234
uint8_t const P02_CRC_0007[4] = {0x9CU, 0x89U, 0x0AU, 0xA3U};      // Value 0x0007
// P11 data id 0x0A12 mode NIBBLE, CRC byte 0, counter low and data id nibble high nibble of byte 1
uint8_t const P11_CRC_55[4] = {0x1DU, 0x40U, 0xA7U, 0xFAU};        // Value 0x55
uint8_t const P11_CRC_66[4] = {0xEBU, 0xB6U, 0x51U, 0x0CU};        // Value 0x66

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
        return ErrorCode::CAN_ERR_OK;
    }

    void addCANFrameListener(::can::ICANFrameListener& l) override
    {
        listener = &l;
        ++listenerCount;
    }

    State state = State::OPEN;
    ::can::ICANTransceiverStateListener::CANTransceiverState busState
        = ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE;
    ::can::ICANFrameListener* listener = nullptr;
    int listenerCount                  = 0;
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

void receive(uint32_t const id, std::vector<uint8_t> const& bytes)
{
    ::can::CANFrame const frame(id, bytes.data(), static_cast<uint8_t>(bytes.size()));
    FakeTransceiver& t = canSystem.transceiver;
    if ((t.listener != nullptr) && t.listener->getFilter().match(id))
    {
        t.listener->frameReceived(frame);
    }
}

/** RxP01: Value byte 0, CRC byte 1 (crcError is added to it), counter in the low nibble of byte 2. */
void sendP01(uint8_t const counter, uint8_t const value, uint8_t const crcError = 0U)
{
    uint8_t const crc = (value == 0x11U) ? P01_CRC_11[counter] : P01_CRC_22[counter];
    receive(ID_RX_P01, {value, static_cast<uint8_t>(crc + crcError), counter, 0U, 0U, 0U, 0U, 0U});
}

/** RxP02: CRC byte 0 (crcError is added to it), counter low nibble of byte 1, Value bytes 2/3. */
void sendP02(uint8_t const counter, uint16_t const value, uint8_t const crcError = 0U)
{
    uint8_t const crc = (value == 0x1234U) ? P02_CRC_1234[counter] : P02_CRC_0007[counter];
    receive(
        ID_RX_P02,
        {static_cast<uint8_t>(crc + crcError), counter, static_cast<uint8_t>(value & 0xFFU),
         static_cast<uint8_t>(value >> 8U), 0U, 0U, 0U, 0U});
}

/** RxP11: CRC byte 0, byte 1 = data id nibble 0xA (nibbleError replaces it) and counter, Value byte 2. */
void sendP11(uint8_t const counter, uint8_t const value, uint8_t const crcError = 0U, uint8_t const nibble = 0xAU)
{
    uint8_t const crc = (value == 0x55U) ? P11_CRC_55[counter] : P11_CRC_66[counter];
    receive(
        ID_RX_P11,
        {static_cast<uint8_t>(crc + crcError), static_cast<uint8_t>(static_cast<uint8_t>(nibble << 4U) | counter),
         value, 0U, 0U, 0U, 0U, 0U});
}

/** RxPlain (no E2E): Flag bit 0 of byte 0, Value byte 1. */
void sendPlain(uint8_t const flag, uint8_t const value)
{
    receive(ID_RX_PLAIN, {flag, value, 0U, 0U, 0U, 0U, 0U, 0U});
}

bool sentEquals(Sent const& s, uint32_t const id, std::vector<uint8_t> const& bytes)
{
    return (s.id == id) && (s.length == bytes.size()) && (std::memcmp(s.data, bytes.data(), bytes.size()) == 0);
}

::rte::CanRxStatus const& rx(size_t const index) { return ::rte::CAN_RX_STATUS[index]; }
} // namespace

namespace systems
{
::can::ICanSystem& getCanSystem() { return canSystem; }
} // namespace systems

int main()
{
    FakeTransceiver& t = canSystem.transceiver;
    CHECK(::rte::CAN_RX_COUNT == 4U);
    CHECK(::rte::CAN_TX_COUNT == 1U);
    CHECK(std::strcmp(rx(IX_PLAIN).name, "RxPlain") == 0);
    CHECK(std::strcmp(rx(IX_P01).name, "RxP01") == 0);
    CHECK(std::strcmp(rx(IX_P02).name, "RxP02") == 0);
    CHECK(std::strcmp(rx(IX_P11).name, "RxP11") == 0);
    // timeout and debounce times of qualifier_rte.yaml (RxPlain overrides GenMsgTimeoutTime)
    CHECK(rx(IX_PLAIN).timeoutMs == 50U);
    CHECK(rx(IX_P01).timeoutMs == 100U);
    CHECK((rx(IX_P01).debounceMs == 30U) && (rx(IX_P02).debounceMs == 0U) && (rx(IX_P11).debounceMs == 20U));
    CHECK(rx(IX_P02).startOnError && rx(IX_PLAIN).startOnError);       // on_error: init
    CHECK(!rx(IX_P01).startOnError && !rx(IX_P11).startOnError);       // on_error: hold (default)

    // ---- start: nothing valid yet, the signals hold the DBC start values
    CHECK(Rte_Buf_CanRx_RxP01_Value == 200U);
    CHECK(Rte_Buf_CanRx_RxP02_Value == 4000U);
    CHECK(Rte_Buf_CanRx_RxP11_Value == 100U);
    CHECK(Rte_Buf_CanRx_RxPlain_Value == 9U);

    // ---- sent message: E2E P02 protection, counter starts at 1 and the CRC covers the data id of the counter
    Rte_Buf_DemoSwc_Led1_Led1                               = 1U; // TxP02.Flag
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = 0U; // TxP02.Value
    cycle();                                                       // tick 0: TxP02 (20 ms) is due
    CHECK(t.listenerCount == 1);
    CHECK(t.sent.size() == 1U);
    if (t.sent.size() == 1U)
    {
        CHECK(sentEquals(t.sent[0], ID_TX_P02, {0x41U, 0x01U, 0x01U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U}));
    }
    Rte_Buf_DemoSwc_Led1_Led1                               = 0U;
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = 200U;
    cycle();                                                       // tick 1: not due
    CHECK(t.sent.size() == 1U);
    cycle();                                                       // tick 2: counter 2
    CHECK(t.sent.size() == 2U);
    if (t.sent.size() == 2U)
    {
        CHECK(sentEquals(t.sent[1], ID_TX_P02, {0x8FU, 0x02U, 0x00U, 0xC8U, 0x00U, 0x00U, 0x00U, 0x00U}));
    }
    Rte_Buf_DemoSwc_Led1_Led1                               = 1U;
    Rte_Buf_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV = 0U;
    cycles(2);                                                     // tick 4: counter 3
    CHECK(t.sent.size() == 3U);
    if (t.sent.size() == 3U)
    {
        CHECK(sentEquals(t.sent[2], ID_TX_P02, {0x6BU, 0x03U, 0x01U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U}));
    }
    CHECK(::rte::CAN_TX_STATUS[0].protectErrors == 0U);
    CHECK(::rte::CAN_TX_STATUS[0].sent == 3U);

    // ---- message without E2E: the first frame is valid at once, timeout with on_error init
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_ERROR_TIMEOUT); // 5 cycles = 50 ms without a frame
    CHECK(rx(IX_PLAIN).timedOut);
    sendPlain(1U, 42U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_OK);
    CHECK(Rte_Buf_DemoSwc_Button1_Button1 == 1U);
    CHECK(Rte_Buf_CanRx_RxPlain_Value == 42U);
    cycles(4);
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_OK);     // 40 ms without a frame: still inside the timeout
    CHECK(Rte_Buf_CanRx_RxPlain_Value == 42U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_ERROR_TIMEOUT);
    CHECK(Rte_Buf_CanRx_RxPlain_Value == 9U);    // on_error: init - DBC start values
    CHECK(Rte_Buf_DemoSwc_Button1_Button1 == 0U);
    sendPlain(1U, 42U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_OK);     // healing after one valid message
    CHECK(Rte_Buf_CanRx_RxPlain_Value == 42U);

    // ---- P01: the first frame is INITIAL (wrong sequence), the second one synchronises
    sendP01(0U, 0x11U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_DEBOUNCE);   // counter error, still inside debounce_ms
    CHECK(rx(IX_P01).aliveErrors == 1U);
    CHECK(Rte_Buf_CanRx_RxP01_Value == 200U);      // on_error: hold, no valid value yet
    sendP01(1U, 0x11U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_OK);
    CHECK(Rte_Buf_CanRx_RxP01_Value == 0x11U);
    CHECK(Rte_Buf_CanRx_RxP01_Counter == 1U);

    // wrong CRC: debounced for 30 ms, the signals keep their last valid value (hold)
    sendP01(2U, 0x22U, 1U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_DEBOUNCE);
    CHECK(rx(IX_P01).crcErrors == 1U);
    CHECK(Rte_Buf_CanRx_RxP01_Value == 0x11U);
    cycles(2);
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_DEBOUNCE);   // 20 ms of 30 ms
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_ERROR_CRC);
    CHECK(Rte_Buf_CanRx_RxP01_Value == 0x11U);
    sendP01(3U, 0x22U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_OK);         // healing
    CHECK(Rte_Buf_CanRx_RxP01_Value == 0x22U);

    // timeout has precedence over an E2E error and does not touch the values with on_error hold
    uint32_t const timeoutsBefore = rx(IX_P01).timeouts;
    cycles(9);
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_OK);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_ERROR_TIMEOUT);
    CHECK(rx(IX_P01).timeouts == (timeoutsBefore + 1U));
    CHECK(Rte_Buf_CanRx_RxP01_Value == 0x22U);
    cycles(5);
    CHECK(rx(IX_P01).timeouts == (timeoutsBefore + 1U)); // counted once per timeout
    sendP01(4U, 0x11U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_OK);
    CHECK(Rte_Buf_CanRx_RxP01_Value == 0x11U);

    // ---- P02: debounce_ms 0 reports at once, on_error init falls back to the start value
    sendP02(0U, 0x1234U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP02 == QLF_ERROR_ALIVE); // first frame: INITIAL -> wrong sequence, no debouncing
    CHECK(Rte_Buf_CanRx_RxP02_Value == 4000U);
    sendP02(1U, 0x1234U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP02 == QLF_OK);
    CHECK(Rte_Buf_CanRx_RxP02_Value == 0x1234U);
    sendP02(2U, 0x1234U, 0x10U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP02 == QLF_ERROR_CRC);   // wrong CRC, reported at once
    CHECK(Rte_Buf_CanRx_RxP02_Value == 4000U);      // on_error: init
    CHECK(rx(IX_P02).crcErrors == 1U);
    sendP02(3U, 0x0007U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP02 == QLF_OK);          // healing
    CHECK(Rte_Buf_CanRx_RxP02_Value == 7U);

    // ---- P11: valid with the first frame, repeated counter and wrong data id nibble
    sendP11(0U, 0x55U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_OK);
    CHECK(Rte_Buf_CanRx_RxP11_Value == 0x55U);
    CHECK(Rte_Buf_CanRx_RxP11_IdNibble == 0xAU);
    sendP11(0U, 0x66U);                             // same counter again: repeated
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_DEBOUNCE);
    CHECK(Rte_Buf_CanRx_RxP11_Value == 0x55U);      // hold
    CHECK(rx(IX_P11).aliveErrors == 1U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_DEBOUNCE);    // 10 ms of 20 ms
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_ERROR_ALIVE);
    sendP11(1U, 0x66U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_OK);          // healing
    CHECK(Rte_Buf_CanRx_RxP11_Value == 0x66U);
    sendP11(2U, 0x55U, 0U, 0xBU);                   // wrong data id nibble (and therefore wrong CRC)
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_DEBOUNCE);
    CHECK(rx(IX_P11).crcErrors == 1U);
    cycles(2);
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_ERROR_CRC);
    CHECK(Rte_Buf_CanRx_RxP11_Value == 0x66U);      // hold
    sendP11(2U, 0x55U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_OK);
    CHECK(Rte_Buf_CanRx_RxP11_Value == 0x55U);

    // the checksum has precedence over the counter while both are pending
    sendP11(2U, 0x66U);                             // repeated counter
    cycle();
    sendP11(3U, 0x55U, 1U);                         // wrong CRC
    cycle();
    cycles(2);
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_ERROR_CRC);
    sendP11(3U, 0x55U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_OK);

    // ---- bus error: every received message of the bus at once, with the highest precedence
    size_t const sentBeforeMute = t.sent.size();
    t.state                     = ::can::ICanTransceiver::State::MUTED;
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP01 == QLF_ERROR);
    CHECK(Rte_Buf_CanQlf_RxP02 == QLF_ERROR);
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_ERROR);
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_ERROR);
    CHECK(t.sent.size() == sentBeforeMute);         // muted: nothing is written
    CHECK(std::strcmp(::rte::canBusStateName(), "muted (phy not ready)") == 0);
    t.state    = ::can::ICanTransceiver::State::OPEN;
    t.busState = ::can::ICANTransceiverStateListener::CANTransceiverState::BUS_OFF;
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 == QLF_ERROR);
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_ERROR);
    CHECK(std::strcmp(::rte::canBusStateName(), "bus off") == 0);
    t.busState = ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE;
    cycle();
    CHECK(Rte_Buf_CanQlf_RxP11 != QLF_ERROR);       // the bus is back: the own state decides again
    CHECK(std::strcmp(::rte::canBusStateName(), "error active") == 0);
    sendPlain(0U, 77U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_OK);
    CHECK(Rte_Buf_CanRx_RxPlain_Value == 77U);

    // ---- error passive is NOT a bus error (COM-12): the bus still works, and a node alone on its bus
    // reaches that state regularly. Reporting it would hide timeout, CRC and counter states, which is
    // what made the qualifier unusable in the COM-03 board test.
    t.busState = ::can::ICANTransceiverStateListener::CANTransceiverState::PASSIVE;
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_OK);
    CHECK(std::strcmp(::rte::canBusStateName(), "error passive") == 0);
    sendPlain(0U, 78U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_OK);        // still healing while error passive
    CHECK(Rte_Buf_CanRx_RxPlain_Value == 78U);      // and values are still taken over
    cycles(7);                                      // timeoutMs of RxPlain is 50 ms, cycle 10 ms
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_ERROR_TIMEOUT); // timeout stays visible, not overruled
    // bus off during the timeout: bus error wins again (precedence Bus > Timeout > CRC > Alive)
    t.busState = ::can::ICANTransceiverStateListener::CANTransceiverState::BUS_OFF;
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_ERROR);
    t.busState = ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE;
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_ERROR_TIMEOUT); // and the timeout is back underneath
    sendPlain(0U, 79U);
    cycle();
    CHECK(Rte_Buf_CanQlf_RxPlain == QLF_OK);
    CHECK(async::LockType::depth == 0);
    CHECK(::rte::CAN_TX_STATUS[0].protectErrors == 0U);
    CHECK(t.listenerCount == 1);

    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
