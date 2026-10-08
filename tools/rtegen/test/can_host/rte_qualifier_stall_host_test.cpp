// Host test of the signal qualifier with a stalled E2E counter (COM-14) for the test fixture
// qualifier_rte.yaml + qualifier.dbc (received messages with P01, P02 and P11).
//
// Board finding 2026-09-16 (tools/boardtest/captures/evalboard_s32k144_demo/2026-09-16_qualifier_led2-einzelframe.*):
// PC_Command (P01) valid up to counter 4, then counter 0 for good (jump, then repetitions). 0.97 s after the
// jump - with the tenth frame of counter 0 - one EVB_Status frame carried Led2 = 1, i.e. can.qlf.PC_Command was
// QLF_OK for one message cycle. Cause: with SyncCounterInit 0 a counter out of sequence does not move the
// reference (LastValidCounter), but every Check call widens the tolerance MaxDeltaCounter by 1. After
// delta - 1 frames the stalled counter fits the widened window and is accepted as OKSOMELOST (E2E_P_OK),
// which healed the qualifier.
//
// Part 1 replays the counter sequence of the capture, part 2 checks every counter value before the stall and
// every stalled value for all three profiles: once the qualifier left QLF_OK in a stall, it must not return
// to QLF_OK, it must end in QLF_ERROR_ALIVE and no signal value of a stalled frame may be taken over after the
// error was noticed. A valid stream after the stall must heal the qualifier again.
//
// The CRC bytes come from the independent Python reference model tools/e2e/e2e_ref.py.
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

// cycles per frame: 20 ms at the 10 ms task (GenMsgCycleTime of qualifier.dbc); debounce_ms 30/0/20 is then
// shorter than two frames, like 200 ms at 100 ms frames in the demo project
constexpr int FRAME_CYCLES = 2;

// CRC bytes per counter value (e2e_ref.py), same layout as rte_qualifier_host_test.cpp
uint8_t const P01_CRC_11[15] = {0x28U, 0x6EU, 0xA4U, 0xE2U, 0x2DU, 0x6BU, 0xA1U, 0xE7U,
                                0x22U, 0x64U, 0xAEU, 0xE8U, 0x27U, 0x61U, 0xABU};
uint8_t const P01_CRC_22[15] = {0x19U, 0x5FU, 0x95U, 0xD3U, 0x1CU, 0x5AU, 0x90U, 0xD6U,
                                0x13U, 0x55U, 0x9FU, 0xD9U, 0x16U, 0x50U, 0x9AU};
uint8_t const P02_CRC_1234[16] = {0x95U, 0x80U, 0x03U, 0xAAU, 0x96U, 0x83U, 0x00U, 0xFEU,
                                  0x93U, 0x86U, 0x05U, 0xACU, 0x90U, 0x85U, 0x06U, 0x56U};
uint8_t const P02_CRC_0007[16] = {0x9CU, 0x89U, 0x0AU, 0xA3U, 0x9FU, 0x8AU, 0x09U, 0xF7U,
                                  0x9AU, 0x8FU, 0x0CU, 0xA5U, 0x99U, 0x8CU, 0x0FU, 0x5FU};
uint8_t const P11_CRC_55[15] = {0x1DU, 0x40U, 0xA7U, 0xFAU, 0x74U, 0x29U, 0xCEU, 0x93U,
                                0xCFU, 0x92U, 0x75U, 0x28U, 0xA6U, 0xFBU, 0x1CU};
uint8_t const P11_CRC_66[15] = {0xEBU, 0xB6U, 0x51U, 0x0CU, 0x82U, 0xDFU, 0x38U, 0x65U,
                                0x39U, 0x64U, 0x83U, 0xDEU, 0x50U, 0x0DU, 0xEAU};

class FakeTransceiver : public ::can::ICanTransceiver
{
public:
    State getState() const override { return State::OPEN; }

    ::can::ICANTransceiverStateListener::CANTransceiverState getCANTransceiverState() const override
    {
        return ::can::ICANTransceiverStateListener::CANTransceiverState::ACTIVE;
    }

    ErrorCode write(::can::CANFrame const&) override { return ErrorCode::CAN_ERR_OK; }

    void addCANFrameListener(::can::ICANFrameListener& l) override { listener = &l; }

    ::can::ICANFrameListener* listener = nullptr;
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

void cycles(int const count)
{
    for (int i = 0; i < count; ++i)
    {
        ::rte::readInputs();
        ::rte::writeOutputs();
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

// one frame per profile; stall = true sends the second value, so a stalled frame taken over is visible
void sendP01(uint8_t const counter, bool const stall)
{
    uint8_t const value = stall ? 0x22U : 0x11U;
    uint8_t const crc   = stall ? P01_CRC_22[counter] : P01_CRC_11[counter];
    receive(0x100U, {value, crc, counter, 0U, 0U, 0U, 0U, 0U});
}

void sendP02(uint8_t const counter, bool const stall)
{
    uint16_t const value = stall ? 0x0007U : 0x1234U;
    uint8_t const crc    = stall ? P02_CRC_0007[counter] : P02_CRC_1234[counter];
    receive(
        0x101U,
        {crc, counter, static_cast<uint8_t>(value & 0xFFU), static_cast<uint8_t>(value >> 8U), 0U, 0U, 0U, 0U});
}

void sendP11(uint8_t const counter, bool const stall)
{
    uint8_t const value = stall ? 0x66U : 0x55U;
    uint8_t const crc   = stall ? P11_CRC_66[counter] : P11_CRC_55[counter];
    receive(0x102U, {crc, static_cast<uint8_t>(0xA0U | counter), value, 0U, 0U, 0U, 0U, 0U});
}

uint32_t valueP01() { return Rte_Buf_CanRx_RxP01_Value; }
uint32_t valueP02() { return Rte_Buf_CanRx_RxP02_Value; }
uint32_t valueP11() { return Rte_Buf_CanRx_RxP11_Value; }
uint8_t qlfP01() { return Rte_Buf_CanQlf_RxP01; }
uint8_t qlfP02() { return Rte_Buf_CanQlf_RxP02; }
uint8_t qlfP11() { return Rte_Buf_CanQlf_RxP11; }

struct Profile
{
    char const* name;
    uint8_t modulo;                     // counter values 0..modulo-1
    void (*send)(uint8_t, bool);
    uint32_t (*value)();
    uint8_t (*qlf)();
    uint32_t stallValue;                // signal value carried by the stalled frames
};

Profile const PROFILES[] = {
    {"RxP01", 15U, &sendP01, &valueP01, &qlfP01, 0x22U},
    {"RxP02", 16U, &sendP02, &valueP02, &qlfP02, 0x0007U},
    {"RxP11", 15U, &sendP11, &valueP11, &qlfP11, 0x66U},
};

/** Sends one frame and runs the cycles until the next one; returns true if the qualifier was QLF_OK in any of
 * them (a qualifier that is QLF_OK for a single cycle is exactly what the board showed). */
bool frame(Profile const& p, uint8_t const counter, bool const stall, bool& errorSeen)
{
    bool okSeen = false;
    p.send(counter, stall);
    for (int i = 0; i < FRAME_CYCLES; ++i)
    {
        cycles(1);
        if (p.qlf() == QLF_OK)
        {
            okSeen = true;
        }
        else
        {
            errorSeen = true;
        }
    }
    return okSeen;
}

/** Valid stream counting up to 'last': the qualifier must be QLF_OK afterwards (healing after a stall). */
bool validUpTo(Profile const& p, uint8_t const last)
{
    bool ignored = false;
    for (uint8_t i = 6U; i > 0U; --i)
    {
        uint8_t const counter = static_cast<uint8_t>((last + p.modulo + 1U - i) % p.modulo);
        (void)frame(p, counter, false, ignored);
    }
    return p.qlf() == QLF_OK;
}

struct StallResult
{
    int okAfterError;    // stalled frame number (1 = first frame of the stalled counter) with QLF_OK after an error
    bool endsAlive;      // QLF_ERROR_ALIVE at the end of the stall
    bool valueLeaked;    // a stalled frame was taken over after the error was noticed
};

StallResult stall(Profile const& p, uint8_t const stalled, int const frames)
{
    StallResult r{0, false, false};
    bool errorSeen          = false;
    bool valueAtError       = false;
    for (int n = 1; n <= frames; ++n)
    {
        bool const wasError = errorSeen;
        bool const ok       = frame(p, stalled, true, errorSeen);
        if (wasError && ok && (r.okAfterError == 0))
        {
            r.okAfterError = n;
        }
        if (!wasError && errorSeen)
        {
            valueAtError = p.value() == p.stallValue; // the first stalled frame may be valid (counter in sequence)
        }
        if (errorSeen && !valueAtError && (p.value() == p.stallValue))
        {
            r.valueLeaked = true;
        }
    }
    r.endsAlive = p.qlf() == QLF_ERROR_ALIVE;
    return r;
}
} // namespace

namespace systems
{
::can::ICanSystem& getCanSystem() { return canSystem; }
} // namespace systems

int main()
{
    cycles(1); // attach the listener

    // ---- part 1: counter sequence of the board capture (P01, PC_Command): ... 13 14 0 1 2 3 4, then 0 for good
    {
        Profile const& p = PROFILES[0];
        bool ignored     = false;
        uint8_t const lead[] = {11U, 12U, 13U, 14U, 0U, 1U, 2U, 3U, 4U, 5U, 6U, 7U, 8U, 9U, 10U, 11U,
                                12U, 13U, 14U, 0U, 1U, 2U, 3U, 4U};
        for (uint8_t const c : lead)
        {
            (void)frame(p, c, false, ignored);
        }
        CHECK(p.qlf() == QLF_OK);
        CHECK(p.value() == 0x11U);
        // 45 frames of counter 0 as in the capture (18.615 s .. 23.309 s)
        StallResult const r = stall(p, 0U, 45);
        if (r.okAfterError != 0)
        {
            std::printf("capture replay: RxP01 QLF_OK again with stalled frame %d (board: frame 10, 0.97 s)\n",
                        r.okAfterError);
        }
        CHECK(r.okAfterError == 0);
        CHECK(r.endsAlive);
        CHECK(!r.valueLeaked);
        // the capture continues with counter 8: a valid stream heals at once
        bool healed = false;
        for (uint8_t c = 8U; c < 15U; ++c)
        {
            (void)frame(p, c, false, ignored);
            healed = healed || (p.qlf() == QLF_OK);
        }
        CHECK(healed && (p.qlf() == QLF_OK));
        CHECK(p.value() == 0x11U);
    }

    // ---- part 2: every counter value before the stall and every stalled value, all profiles
    for (Profile const& p : PROFILES)
    {
        unsigned cases = 0U, okAfterError = 0U, notAlive = 0U, leaked = 0U, notHealed = 0U;
        for (uint8_t last = 0U; last < p.modulo; ++last)
        {
            for (uint8_t stalled = 0U; stalled < p.modulo; ++stalled)
            {
                ++cases;
                if (!validUpTo(p, last))
                {
                    ++notHealed;
                    continue;
                }
                StallResult const r = stall(p, stalled, 2 * p.modulo);
                if (r.okAfterError != 0)
                {
                    if (okAfterError < 3U)
                    {
                        std::printf("%s: last valid %u, stalled at %u: QLF_OK again with stalled frame %d\n",
                                    p.name, last, stalled, r.okAfterError);
                    }
                    ++okAfterError;
                }
                notAlive += r.endsAlive ? 0U : 1U;
                leaked += r.valueLeaked ? 1U : 0U;
            }
        }
        std::printf("%s: %u stall cases, QLF_OK after the error %u, not ERROR_ALIVE at the end %u, stalled value "
                    "taken over %u, not healed by the valid stream %u\n",
                    p.name, cases, okAfterError, notAlive, leaked, notHealed);
        CHECK(okAfterError == 0U);
        CHECK(notAlive == 0U);
        CHECK(leaked == 0U);
        CHECK(notHealed == 0U);
    }

    // ---- part 3: counter jump with a running counter (sender restarted, frames lost): healed with the second
    // frame after the jump for every jump width - the out-of-sequence counter is the new reference
    for (Profile const& p : PROFILES)
    {
        unsigned slow = 0U;
        for (uint8_t jump = 2U; jump < p.modulo; ++jump)
        {
            if (!validUpTo(p, 3U))
            {
                ++slow;
                continue;
            }
            bool ignored = false;
            uint8_t const first = static_cast<uint8_t>((3U + jump) % p.modulo);
            (void)frame(p, first, false, ignored);
            (void)frame(p, static_cast<uint8_t>((first + 1U) % p.modulo), false, ignored);
            if (p.qlf() != QLF_OK)
            {
                std::printf("%s: jump by %u not healed with the second frame (qualifier %u)\n", p.name, jump,
                            p.qlf());
                ++slow;
            }
        }
        CHECK(slow == 0U);
    }

    CHECK(async::LockType::depth == 0);
    std::printf("%u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
