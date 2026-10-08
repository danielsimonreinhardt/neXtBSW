// SPDX-License-Identifier: Apache-2.0
/* Host test of the FlexIO I2C master (IO-09) against a model of the FlexIO and an I2C device on the wire.
 *
 * The FlexIO model follows the S32K1xx Reference Manual Rev. 13, chapter 54, independent of the driver: shift buffer
 * aliases (54.3.1.16/18), transmit load with status and underrun flags (54.4.2.1), receive store with stop bit check
 * and overrun (54.4.2.2), the transmitter's stop bit on entering transmit mode, the SCL timer triggered by the
 * inverted transmit status flag, the edge count of TIMCMP[15:8] (54.5.5) and clock stretching only with
 * TIMCFG[TIMRST] "reset on pin equal to output". It advances one SCL half period per status or pin read.
 * The device is a register-pointer slave (first write byte = register, then data; reads from the register) that
 * logs START, repeated START, STOP and every byte with ACK/NACK as seen on the open-drain lines. Faults: absent
 * address, NACK of a data byte, clock stretching (bounded and endless), stuck SDA/SCL, SDA pulled low during a byte,
 * a driver too slow for the bus (transmit underrun). Profiles: FlexIO clock 40 MHz (eval board SOSCDIV2), 48 MHz,
 * 8 MHz; resources FXIO_D6/D7 with shifters 0/1 and timers 0/1, FXIO_D1/D0 with shifters 3/2 and timers 2/3,
 * FXIO_D0/D5 with shifters 1/0 and timers 1/2, and those of RM Table 54-11.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Werror \
 *       -Iplatforms/s32k1xx/bsp/bspFlexioI2c/test/host/stubs -Iplatforms/s32k1xx/bsp/bspFlexioI2c/include \
 *       -isystemexternal/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp \
 *       platforms/s32k1xx/bsp/bspFlexioI2c/src/i2c/FlexioI2cMaster.cpp \
 *       platforms/s32k1xx/bsp/bspFlexioI2c/test/host/FlexioI2cHostTest.cpp -o build/host/flexio_i2c_test.exe
 *   build/host/flexio_i2c_test.exe
 */

#include "i2c/FlexioI2cMaster.h"

#include <array>
#include <cstdio>
#include <string>
#include <vector>

uint32_t nxTestNowUs = 0U;

using bsp::i2c::FlexioI2cConfig;
using bsp::i2c::FlexioI2cMaster;
using bsp::i2c::I2cResult;
namespace flexio = bsp::i2c::flexio;

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

using Log = std::vector<std::string>;

void checkLog(int const line, Log const& actual, Log const& expected)
{
    ++checks;
    if (actual != expected)
    {
        ++failures;
        std::printf("  FAIL %s:%d: wire log\n    expected:", __FILE__, line);
        for (auto const& e : expected)
        {
            std::printf(" [%s]", e.c_str());
        }
        std::printf("\n    actual:  ");
        for (auto const& e : actual)
        {
            std::printf(" [%s]", e.c_str());
        }
        std::printf("\n");
    }
}

#define CHECK_LOG(actual, ...) checkLog(__LINE__, actual, Log{__VA_ARGS__})

uint32_t field(uint32_t const value, uint32_t const mask, uint32_t const shift) { return (value & mask) >> shift; }

uint32_t bitOf(uint32_t const index) { return 1U << index; }

// 54.3.1.16: SHIFTBUFBIS reads return SHIFTBUF[0:31]
uint32_t reverse32(uint32_t const value)
{
    uint32_t result = 0U;
    for (uint32_t i = 0U; i < 32U; ++i)
    {
        if ((value & bitOf(i)) != 0U)
        {
            result |= bitOf(31U - i);
        }
    }
    return result;
}

// 54.3.1.18: SHIFTBUFBBS is bit swapped within each byte
uint32_t swapBitsInBytes(uint32_t const value)
{
    uint32_t result = 0U;
    for (uint32_t byte = 0U; byte < 4U; ++byte)
    {
        for (uint32_t i = 0U; i < 8U; ++i)
        {
            if ((value & bitOf((byte * 8U) + i)) != 0U)
            {
                result |= bitOf((byte * 8U) + (7U - i));
            }
        }
    }
    return result;
}

std::string byteEntry(uint32_t const value, bool const ack)
{
    char text[16];
    std::snprintf(text, sizeof(text), "0x%02X %s", static_cast<unsigned>(value & 0xFFU), ack ? "A" : "N");
    return text;
}

/* I2C device with a register pointer, 7-bit address. */
struct Device
{
    enum class State
    {
        IDLE,
        ADDRESS,
        WRITE,
        READ,
        IGNORE
    };

    uint8_t address           = 0x50U;
    bool present              = true;
    int nackWriteByte         = -1; // data byte of a write (0 = register pointer) that is not acknowledged
    int pullLowWriteByte      = -1; // SDA held low during bits 1..7 of this data byte (other master, short)
    uint32_t stretchHalfBits  = 0U; // SCL held low after every ACK slot, in model steps
    bool sdaStuck             = false;
    bool sclStuck             = false;
    std::array<uint8_t, 256> memory{};
    uint8_t pointer = 0U;
    Log log;

    State state         = State::IDLE;
    uint32_t value      = 0U;
    uint32_t bits       = 0U;
    bool ackSlot        = false;
    bool ack            = false;
    bool reading        = false;
    bool masterAcked    = false;
    bool drivingLow     = false;
    int writeIndex      = 0;
    uint32_t hold       = 0U;
    uint32_t clocksIgnored = 0U; // SCL rises after a NACK before STOP/START
    uint32_t stretched     = 0U; // steps SCL was held

    bool sdaLow() const { return sdaStuck || drivingLow; }
    bool sclLow() const { return sclStuck || (hold > 0U); }

    void reset()
    {
        state      = State::IDLE;
        drivingLow = false;
        ackSlot    = false;
        hold       = 0U;
    }

    void onStart()
    {
        log.push_back((state == State::IDLE) ? "S" : "Sr");
        state      = State::ADDRESS;
        bits       = 0U;
        value      = 0U;
        ackSlot    = false;
        drivingLow = false;
    }

    void onStop()
    {
        log.push_back("P");
        state      = State::IDLE;
        ackSlot    = false;
        drivingLow = false;
    }

    void onRise(bool const sda)
    {
        switch (state)
        {
            case State::ADDRESS:
            case State::WRITE:
                if (!ackSlot)
                {
                    value = ((value << 1U) | (sda ? 1U : 0U)) & 0xFFU;
                    ++bits;
                }
                break;
            case State::READ:
                if (ackSlot)
                {
                    masterAcked = !sda;
                    log.push_back(byteEntry(value, masterAcked));
                }
                else
                {
                    ++bits;
                }
                break;
            case State::IGNORE: ++clocksIgnored; break;
            case State::IDLE: break;
        }
    }

    void driveReadBit() { drivingLow = ((value >> (7U - bits)) & 1U) == 0U; }

    void onFall()
    {
        switch (state)
        {
            case State::ADDRESS:
            case State::WRITE:
                if ((!ackSlot) && (bits == 8U))
                {
                    ackSlot = true;
                    if (state == State::ADDRESS)
                    {
                        ack     = present && ((value >> 1U) == address);
                        reading = (value & 1U) != 0U;
                    }
                    else
                    {
                        ack = writeIndex != nackWriteByte;
                        if (ack)
                        {
                            if (writeIndex == 0)
                            {
                                pointer = static_cast<uint8_t>(value);
                            }
                            else
                            {
                                memory[pointer] = static_cast<uint8_t>(value);
                                ++pointer;
                            }
                        }
                        ++writeIndex;
                    }
                    log.push_back(byteEntry(value, ack));
                    drivingLow = ack;
                }
                else if (ackSlot)
                {
                    ackSlot    = false;
                    bits       = 0U;
                    value      = 0U;
                    drivingLow = false;
                    if (!ack)
                    {
                        state = State::IGNORE;
                        break;
                    }
                    hold = stretchHalfBits;
                    if (state == State::ADDRESS)
                    {
                        writeIndex = 0;
                        if (reading)
                        {
                            state = State::READ;
                            value = memory[pointer];
                            driveReadBit();
                        }
                        else
                        {
                            state = State::WRITE;
                        }
                    }
                }
                else if ((state == State::WRITE) && (writeIndex == pullLowWriteByte))
                {
                    drivingLow = true;
                }
                else
                {
                    drivingLow = false;
                }
                break;
            case State::READ:
                if (ackSlot)
                {
                    ackSlot = false;
                    if (!masterAcked)
                    {
                        state      = State::IGNORE;
                        drivingLow = false;
                        break;
                    }
                    value = memory[pointer];
                    bits  = 0U;
                    hold  = stretchHalfBits;
                    driveReadBit();
                }
                else if (bits == 8U)
                {
                    ackSlot    = true;
                    drivingLow = false;
                    ++pointer;
                }
                else
                {
                    driveReadBit();
                }
                break;
            case State::IGNORE:
            case State::IDLE: drivingLow = false; break;
        }
    }
};

struct Resources
{
    uint8_t sda;
    uint8_t scl;
    uint8_t tx;
    uint8_t rx;
    uint8_t sclTimer;
};

/* FlexIO with the I2C wire: registers, shifters, SCL timer and line levels. */
struct Model
{
    Resources res{6U, 7U, 0U, 1U, 0U};
    uint32_t param = FLEXIO_PARAM_TRIGGER(8U) | FLEXIO_PARAM_PIN(8U) | FLEXIO_PARAM_TIMER(4U)
                     | FLEXIO_PARAM_SHIFTER(4U); // Table 54-1
    uint32_t ctrl  = 0U;
    std::array<uint32_t, 4> shiftctl{};
    std::array<uint32_t, 4> shiftcfg{};
    std::array<uint32_t, 4> shiftbuf{};
    std::array<uint32_t, 4> timctl{};
    std::array<uint32_t, 4> timcfg{};
    std::array<uint32_t, 4> timcmp{};
    uint32_t ssf = 0U;
    uint32_t sef = 0U;
    uint32_t tsf = 0U;

    bool txBufferFull = false;
    uint32_t txShift  = 0xFFFFFFFFU;
    bool txOut        = true;
    uint32_t rxShift  = 0U;

    bool running      = false;
    bool packetDone   = false;
    bool sclLowPhase  = false;
    bool finalPhase   = false;
    uint32_t words    = 0U;
    uint32_t loaded   = 0U;
    uint32_t bitIndex = 8U;
    uint32_t packets  = 0U;

    bool lastSda = true;
    bool lastScl = true;

    uint32_t stepsPerRead = 1U;
    uint32_t readsPerStep = 1U;
    uint32_t readCount    = 0U;

    Device device;
    std::vector<std::string> errors;
    std::array<uint32_t, 16> snapshot{}; // registers at the start of the last packet, see snapshotIndex()

    uint32_t txBit() const { return bitOf(res.tx); }
    uint32_t rxBit() const { return bitOf(res.rx); }
    uint32_t shiftTimer() const { return res.sclTimer + 1U; }

    void error(std::string const& text)
    {
        if (errors.size() < 20U)
        {
            errors.push_back(text);
        }
    }

    bool txTransmitting() const
    {
        return field(shiftctl[res.tx], FLEXIO_SHIFTCTL_SMOD_MASK, FLEXIO_SHIFTCTL_SMOD_SHIFT) == 2U;
    }

    bool sdaLine() const
    {
        uint32_t const ctl = shiftctl[res.tx];
        bool txLow         = false;
        if (txTransmitting()
            && (field(ctl, FLEXIO_SHIFTCTL_PINSEL_MASK, FLEXIO_SHIFTCTL_PINSEL_SHIFT) == res.sda)
            && (field(ctl, FLEXIO_SHIFTCTL_PINCFG_MASK, FLEXIO_SHIFTCTL_PINCFG_SHIFT) != 0U))
        {
            bool const inverted = (ctl & FLEXIO_SHIFTCTL_PINPOL_MASK) != 0U;
            // open drain (PINCFG 01): output enable asserted drives low; inverted polarity asserts it for a 0
            txLow = inverted ? (!txOut) : txOut;
        }
        return !(txLow || device.sdaLow());
    }

    bool sclLine() const
    {
        uint32_t const ctl = timctl[res.sclTimer];
        bool timerLow      = false;
        if ((field(ctl, FLEXIO_TIMCTL_TIMOD_MASK, FLEXIO_TIMCTL_TIMOD_SHIFT) != 0U)
            && (field(ctl, FLEXIO_TIMCTL_PINSEL_MASK, FLEXIO_TIMCTL_PINSEL_SHIFT) == res.scl)
            && (field(ctl, FLEXIO_TIMCTL_PINCFG_MASK, FLEXIO_TIMCTL_PINCFG_SHIFT) != 0U))
        {
            bool const output   = running && sclLowPhase; // timer output 1 while SCL is to be low
            bool const inverted = (ctl & FLEXIO_TIMCTL_PINPOL_MASK) != 0U;
            timerLow            = inverted ? (!output) : output;
        }
        return !(timerLow || device.sclLow());
    }

    void updateLines()
    {
        bool const scl = sclLine();
        if (lastScl && (!scl))
        {
            device.onFall();
        }
        bool sda = sdaLine();
        if ((!lastScl) && scl)
        {
            lastScl = scl;
            device.onRise(sda);
        }
        else if (lastScl && scl && (sda != lastSda))
        {
            if (sda)
            {
                device.onStop();
            }
            else
            {
                device.onStart();
            }
            sda = sdaLine();
        }
        lastScl = scl;
        lastSda = sda;
    }

    uint32_t txStopBit()
    {
        uint32_t const sstop = field(shiftcfg[res.tx], FLEXIO_SHIFTCFG_SSTOP_MASK, FLEXIO_SHIFTCFG_SSTOP_SHIFT);
        if (sstop < 2U)
        {
            error("transmitter without stop bit (ACK slot)");
            return txOut ? 1U : 0U;
        }
        return sstop & 1U;
    }

    void loadTx()
    {
        if (txBufferFull)
        {
            txShift      = shiftbuf[res.tx];
            txBufferFull = false;
        }
        else
        {
            sef |= txBit(); // underrun, the shifter keeps its old content
            txShift = 0xFFFFFFFFU;
        }
        ssf |= txBit();
        txOut = (txShift & 1U) != 0U;
    }

    void checkField(uint32_t const reg, uint32_t const mask, uint32_t const shift, uint32_t const expected,
                    char const* name)
    {
        if (field(reg, mask, shift) != expected)
        {
            char text[96];
            std::snprintf(text, sizeof(text), "%s is %u, expected %u", name,
                          static_cast<unsigned>(field(reg, mask, shift)), static_cast<unsigned>(expected));
            error(text);
        }
    }

    void validateConfiguration()
    {
        uint32_t const shiftT = shiftTimer();
        uint32_t const trgsel = (4U * res.tx) + 1U;
        checkField(ctrl, FLEXIO_CTRL_FLEXEN_MASK, FLEXIO_CTRL_FLEXEN_SHIFT, 1U, "CTRL[FLEXEN]");
        // Transmitter: timer shiftT on rising edge, open drain inverted on SDA, start 0, stop 1 or 0 (ACK)
        uint32_t v = shiftctl[res.tx];
        checkField(v, FLEXIO_SHIFTCTL_TIMSEL_MASK, FLEXIO_SHIFTCTL_TIMSEL_SHIFT, shiftT, "tx TIMSEL");
        checkField(v, FLEXIO_SHIFTCTL_TIMPOL_MASK, FLEXIO_SHIFTCTL_TIMPOL_SHIFT, 0U, "tx TIMPOL");
        checkField(v, FLEXIO_SHIFTCTL_PINCFG_MASK, FLEXIO_SHIFTCTL_PINCFG_SHIFT, 1U, "tx PINCFG");
        checkField(v, FLEXIO_SHIFTCTL_PINSEL_MASK, FLEXIO_SHIFTCTL_PINSEL_SHIFT, res.sda, "tx PINSEL");
        checkField(v, FLEXIO_SHIFTCTL_PINPOL_MASK, FLEXIO_SHIFTCTL_PINPOL_SHIFT, 1U, "tx PINPOL");
        checkField(shiftcfg[res.tx], FLEXIO_SHIFTCFG_SSTART_MASK, FLEXIO_SHIFTCFG_SSTART_SHIFT, 2U, "tx SSTART");
        checkField(shiftcfg[res.tx], FLEXIO_SHIFTCFG_INSRC_MASK, FLEXIO_SHIFTCFG_INSRC_SHIFT, 0U, "tx INSRC");
        // Receiver: timer shiftT on falling edge, input SDA, no start bit, stop bit 0 (ACK)
        v = shiftctl[res.rx];
        checkField(v, FLEXIO_SHIFTCTL_SMOD_MASK, FLEXIO_SHIFTCTL_SMOD_SHIFT, 1U, "rx SMOD");
        checkField(v, FLEXIO_SHIFTCTL_TIMSEL_MASK, FLEXIO_SHIFTCTL_TIMSEL_SHIFT, shiftT, "rx TIMSEL");
        checkField(v, FLEXIO_SHIFTCTL_TIMPOL_MASK, FLEXIO_SHIFTCTL_TIMPOL_SHIFT, 1U, "rx TIMPOL");
        checkField(v, FLEXIO_SHIFTCTL_PINCFG_MASK, FLEXIO_SHIFTCTL_PINCFG_SHIFT, 0U, "rx PINCFG");
        checkField(v, FLEXIO_SHIFTCTL_PINSEL_MASK, FLEXIO_SHIFTCTL_PINSEL_SHIFT, res.sda, "rx PINSEL");
        checkField(shiftcfg[res.rx], FLEXIO_SHIFTCFG_SSTART_MASK, FLEXIO_SHIFTCFG_SSTART_SHIFT, 0U, "rx SSTART");
        checkField(shiftcfg[res.rx], FLEXIO_SHIFTCFG_SSTOP_MASK, FLEXIO_SHIFTCFG_SSTOP_SHIFT, 2U, "rx SSTOP");
        checkField(shiftcfg[res.rx], FLEXIO_SHIFTCFG_INSRC_MASK, FLEXIO_SHIFTCFG_INSRC_SHIFT, 0U, "rx INSRC");
        // SCL timer
        v = timctl[res.sclTimer];
        checkField(v, FLEXIO_TIMCTL_TIMOD_MASK, FLEXIO_TIMCTL_TIMOD_SHIFT, 1U, "scl TIMOD");
        checkField(v, FLEXIO_TIMCTL_PINCFG_MASK, FLEXIO_TIMCTL_PINCFG_SHIFT, 1U, "scl PINCFG");
        checkField(v, FLEXIO_TIMCTL_PINSEL_MASK, FLEXIO_TIMCTL_PINSEL_SHIFT, res.scl, "scl PINSEL");
        checkField(v, FLEXIO_TIMCTL_PINPOL_MASK, FLEXIO_TIMCTL_PINPOL_SHIFT, 0U, "scl PINPOL");
        checkField(v, FLEXIO_TIMCTL_TRGSRC_MASK, FLEXIO_TIMCTL_TRGSRC_SHIFT, 1U, "scl TRGSRC");
        checkField(v, FLEXIO_TIMCTL_TRGPOL_MASK, FLEXIO_TIMCTL_TRGPOL_SHIFT, 1U, "scl TRGPOL");
        checkField(v, FLEXIO_TIMCTL_TRGSEL_MASK, FLEXIO_TIMCTL_TRGSEL_SHIFT, trgsel, "scl TRGSEL");
        v = timcfg[res.sclTimer];
        checkField(v, FLEXIO_TIMCFG_TIMOUT_MASK, FLEXIO_TIMCFG_TIMOUT_SHIFT, 1U, "scl TIMOUT");
        checkField(v, FLEXIO_TIMCFG_TIMDEC_MASK, FLEXIO_TIMCFG_TIMDEC_SHIFT, 0U, "scl TIMDEC");
        checkField(v, FLEXIO_TIMCFG_TIMDIS_MASK, FLEXIO_TIMCFG_TIMDIS_SHIFT, 2U, "scl TIMDIS");
        checkField(v, FLEXIO_TIMCFG_TIMENA_MASK, FLEXIO_TIMCFG_TIMENA_SHIFT, 2U, "scl TIMENA");
        checkField(v, FLEXIO_TIMCFG_TSTOP_MASK, FLEXIO_TIMCFG_TSTOP_SHIFT, 2U, "scl TSTOP");
        checkField(v, FLEXIO_TIMCFG_TSTART_MASK, FLEXIO_TIMCFG_TSTART_SHIFT, 1U, "scl TSTART");
        // Shift timer
        v = timctl[shiftT];
        checkField(v, FLEXIO_TIMCTL_TIMOD_MASK, FLEXIO_TIMCTL_TIMOD_SHIFT, 3U, "shift TIMOD");
        checkField(v, FLEXIO_TIMCTL_PINCFG_MASK, FLEXIO_TIMCTL_PINCFG_SHIFT, 0U, "shift PINCFG");
        checkField(v, FLEXIO_TIMCTL_PINSEL_MASK, FLEXIO_TIMCTL_PINSEL_SHIFT, res.scl, "shift PINSEL");
        checkField(v, FLEXIO_TIMCTL_PINPOL_MASK, FLEXIO_TIMCTL_PINPOL_SHIFT, 1U, "shift PINPOL");
        v = timcfg[shiftT];
        checkField(v, FLEXIO_TIMCFG_TIMDEC_MASK, FLEXIO_TIMCFG_TIMDEC_SHIFT, 2U, "shift TIMDEC");
        checkField(v, FLEXIO_TIMCFG_TIMRST_MASK, FLEXIO_TIMCFG_TIMRST_SHIFT, 0U, "shift TIMRST");
        checkField(v, FLEXIO_TIMCFG_TIMDIS_MASK, FLEXIO_TIMCFG_TIMDIS_SHIFT, 1U, "shift TIMDIS");
        checkField(v, FLEXIO_TIMCFG_TIMENA_MASK, FLEXIO_TIMCFG_TIMENA_SHIFT, 1U, "shift TIMENA");
        checkField(v, FLEXIO_TIMCFG_TSTOP_MASK, FLEXIO_TIMCFG_TSTOP_SHIFT, 1U, "shift TSTOP");
        checkField(v, FLEXIO_TIMCFG_TSTART_MASK, FLEXIO_TIMCFG_TSTART_SHIFT, 1U, "shift TSTART");
        checkField(timcmp[shiftT], 0xFFFFU, 0U, 15U, "shift TIMCMP (8 bits x 2 - 1)");
        uint32_t const baud = timcmp[res.sclTimer] & 0xFFU;
        if (baud < 2U)
        {
            error("SCL faster than FlexIO clock / 6");
        }
    }

    void snapshotRegisters()
    {
        snapshot = {shiftcfg[res.tx], shiftctl[res.tx], shiftcfg[res.rx], shiftctl[res.rx], timcmp[res.sclTimer],
                    timcfg[res.sclTimer], timctl[res.sclTimer], timcmp[shiftTimer()], timcfg[shiftTimer()],
                    timctl[shiftTimer()], 0U, 0U, 0U, 0U, 0U, 0U};
    }

    void maybeStart()
    {
        uint32_t const timod
            = field(timctl[res.sclTimer], FLEXIO_TIMCTL_TIMOD_MASK, FLEXIO_TIMCTL_TIMOD_SHIFT);
        // Timer enabled on trigger high; trigger = transmit status flag, active low
        if ((timod == 0U) || running || packetDone || ((ssf & txBit()) != 0U))
        {
            return;
        }
        if (!txTransmitting())
        {
            error("SCL timer started while the transmitter is not in transmit mode");
        }
        validateConfiguration();
        snapshotRegisters();
        uint32_t const edges = (timcmp[res.sclTimer] >> 8U) & 0xFFU;
        if ((edges < 19U) || (((edges - 1U) % 18U) != 0U))
        {
            error("TIMCMP[15:8] of the SCL timer is no packet of whole words");
        }
        words       = (edges > 0U) ? ((edges - 1U) / 18U) : 0U;
        running     = true;
        sclLowPhase = false;
        finalPhase  = false;
        loaded      = 0U;
        bitIndex    = 8U;
        ++packets;
        // start bit (54.4.3.4): SDA low while SCL is still high
        if (field(shiftcfg[res.tx], FLEXIO_SHIFTCFG_SSTART_MASK, FLEXIO_SHIFTCFG_SSTART_SHIFT) == 2U)
        {
            txOut = false;
        }
        updateLines();
    }

    void stopTimers()
    {
        running     = false;
        packetDone  = false;
        sclLowPhase = false;
        finalPhase  = false;
    }

    bool stretchSupported() const
    {
        return field(timcfg[res.sclTimer], FLEXIO_TIMCFG_TIMRST_MASK, FLEXIO_TIMCFG_TIMRST_SHIFT) == 2U;
    }

    void step()
    {
        if (!running)
        {
            return;
        }
        if (sclLowPhase)
        {
            if (device.sclLow() && !device.sclStuck)
            {
                --device.hold;
                ++device.stretched;
                if (stretchSupported())
                {
                    return; // counter reset while pin equals output: SCL waits
                }
            }
            else if (device.sclStuck && stretchSupported())
            {
                return;
            }
            sclLowPhase = false;
            updateLines();
            if (finalPhase)
            {
                running    = false;
                packetDone = true;
                tsf |= bitOf(res.sclTimer);
                return;
            }
            bool const sda = sdaLine();
            if (bitIndex < 8U)
            {
                rxShift = (rxShift >> 1U) | (sda ? 0x80000000U : 0U);
            }
            else
            {
                // ACK slot: store and stop bit check on the same edge (54.4.3.6)
                if ((ssf & rxBit()) != 0U)
                {
                    sef |= rxBit(); // overrun
                }
                shiftbuf[res.rx] = rxShift;
                ssf |= rxBit();
                if (sda)
                {
                    sef |= rxBit(); // stop bit expected 0
                }
            }
        }
        else
        {
            sclLowPhase = true;
            if (bitIndex < 7U)
            {
                ++bitIndex;
                txShift >>= 1U;
                txOut = (txShift & 1U) != 0U;
            }
            else if (bitIndex == 7U)
            {
                bitIndex = 8U;
                txOut    = txStopBit() != 0U;
            }
            else
            {
                loadTx();
                if (loaded == words)
                {
                    finalPhase = true;
                }
                else
                {
                    ++loaded;
                    bitIndex = 0U;
                }
            }
            updateLines();
        }
    }

    void onStatusRead()
    {
        ++readCount;
        if (readCount >= readsPerStep)
        {
            readCount = 0U;
            for (uint32_t i = 0U; i < stepsPerRead; ++i)
            {
                step();
            }
        }
    }

    void writeShiftctl(uint32_t const index, uint32_t const value)
    {
        uint32_t const oldMode = field(shiftctl[index], FLEXIO_SHIFTCTL_SMOD_MASK, FLEXIO_SHIFTCTL_SMOD_SHIFT);
        uint32_t const newMode = field(value, FLEXIO_SHIFTCTL_SMOD_MASK, FLEXIO_SHIFTCTL_SMOD_SHIFT);
        shiftctl[index]        = value;
        if (index == res.tx)
        {
            if ((newMode == 2U) && (oldMode != 2U))
            {
                // 54.4.2.1: stop bit loaded at once, status flag set
                uint32_t const sstop
                    = field(shiftcfg[res.tx], FLEXIO_SHIFTCFG_SSTOP_MASK, FLEXIO_SHIFTCFG_SSTOP_SHIFT);
                if (sstop >= 2U)
                {
                    txOut = (sstop & 1U) != 0U;
                }
                ssf |= txBit();
                txBufferFull = false;
            }
            else if (newMode != 2U)
            {
                txOut = true;
                ssf &= ~txBit();
                txBufferFull = false;
            }
        }
        else if ((index == res.rx) && (newMode == 0U))
        {
            ssf &= ~rxBit();
        }
    }

    void writeTimctl(uint32_t const index, uint32_t const value)
    {
        timctl[index] = value;
        if ((index == res.sclTimer) && (field(value, FLEXIO_TIMCTL_TIMOD_MASK, FLEXIO_TIMCTL_TIMOD_SHIFT) == 0U))
        {
            stopTimers();
        }
    }

    uint32_t read(uint32_t const offset)
    {
        switch (offset)
        {
            case 0x04U: return param;
            case 0x08U: return ctrl;
            case 0x0CU:
                onStatusRead();
                return (sdaLine() ? bitOf(res.sda) : 0U) | (sclLine() ? bitOf(res.scl) : 0U);
            case 0x10U: onStatusRead(); return ssf;
            case 0x14U: onStatusRead(); return sef;
            case 0x18U: onStatusRead(); return tsf;
            default: break;
        }
        if ((offset >= 0x280U) && (offset < 0x290U))
        {
            uint32_t const index = (offset - 0x280U) / 4U;
            if (index == res.rx)
            {
                ssf &= ~rxBit(); // 54.3.1.6: cleared when SHIFTBUF is read
            }
            return reverse32(shiftbuf[index]);
        }
        char text[64];
        std::snprintf(text, sizeof(text), "read of unexpected register 0x%03X", static_cast<unsigned>(offset));
        error(text);
        return 0U;
    }

    void write(uint32_t const offset, uint32_t const value)
    {
        uint32_t const index = (offset & 0x7FU) / 4U; // register arrays start at 0x...00 or 0x...80
        if (offset == 0x08U)
        {
            ctrl = value;
        }
        else if (offset == 0x10U)
        {
            ssf &= ~value;
        }
        else if (offset == 0x14U)
        {
            sef &= ~value;
        }
        else if (offset == 0x18U)
        {
            tsf &= ~value;
        }
        else if ((offset >= 0x80U) && (offset < 0x90U))
        {
            writeShiftctl((offset - 0x80U) / 4U, value);
        }
        else if ((offset >= 0x100U) && (offset < 0x110U))
        {
            shiftcfg[index] = value;
        }
        else if ((offset >= 0x380U) && (offset < 0x390U))
        {
            shiftbuf[index] = swapBitsInBytes(value);
            if (index == res.tx)
            {
                txBufferFull = true;
                ssf &= ~txBit();
            }
        }
        else if ((offset >= 0x400U) && (offset < 0x410U))
        {
            writeTimctl(index, value);
        }
        else if ((offset >= 0x480U) && (offset < 0x490U))
        {
            timcfg[index] = value;
        }
        else if ((offset >= 0x500U) && (offset < 0x510U))
        {
            timcmp[index] = value;
        }
        else
        {
            char text[64];
            std::snprintf(text, sizeof(text), "write to unexpected register 0x%03X", static_cast<unsigned>(offset));
            error(text);
        }
        maybeStart();
        updateLines();
    }

    bool linesReleased() const { return sdaLine() && sclLine(); }
};

NxTestFlexio flexioRegisters;
Model* model = nullptr;

uint32_t offsetOf(NxTestFlexioRegister const* reg)
{
    return static_cast<uint32_t>(reinterpret_cast<uint8_t const*>(reg)
                                 - reinterpret_cast<uint8_t const*>(&flexioRegisters));
}
} // namespace

uint32_t nxTestFlexioRead(NxTestFlexioRegister const* const reg) { return model->read(offsetOf(reg)); }

void nxTestFlexioWrite(NxTestFlexioRegister const* const reg, uint32_t const value)
{
    model->write(offsetOf(reg), value);
}

namespace
{
constexpr uint32_t EVAL_FLEXIO_HZ = 40000000U; // NX_CLOCK_FLEXIO_HZ of the eval board profile (SOSCDIV2)

FlexioI2cConfig makeConfig(Resources const& res, uint32_t const clockHz, uint32_t const baudHz)
{
    return FlexioI2cConfig{&flexioRegisters, clockHz, baudHz, 200000U, res.sda, res.scl, res.tx, res.rx,
                           res.sclTimer};
}

void testTiming()
{
    std::printf("timing\n");
    // RM Table 54-11 example: 2 words, divide by 4 -> TIMCMP 0x2501
    CHECK(flexio::sclTimerCompare(2U, 1U) == 0x2501U);
    CHECK(flexio::MAX_BYTES_PER_TRANSFER == 13U);
    CHECK(((flexio::MAX_WORDS_PER_PACKET * 18U) + 1U) <= 0xFFU);
    CHECK((((flexio::MAX_WORDS_PER_PACKET + 1U) * 18U) + 1U) > 0xFFU);

    struct Case
    {
        uint32_t clockHz;
        uint32_t requestHz;
        bool valid;
        uint32_t compare;
        uint32_t sclHz;
    };
    Case const cases[] = {
        {40000000U, 100000U, true, 199U, 100000U}, // eval board, standard mode
        {40000000U, 400000U, true, 49U, 400000U},  // fast mode
        {40000000U, 90000U, true, 222U, 89686U},   // not exact: next slower
        {40000000U, 50000U, false, 0U, 0U},        // below f / 512: never faster than requested
        {48000000U, 400000U, true, 59U, 400000U},
        {48000000U, 93750U, true, 255U, 93750U},   // slowest setting
        {48000000U, 93749U, false, 0U, 0U},
        {8000000U, 400000U, true, 9U, 400000U},
        {8000000U, 1000000U, true, 3U, 1000000U},
        {1000000U, 400000U, true, 2U, 166666U},    // limited to f / 6 (54.5.5)
        {0U, 100000U, false, 0U, 0U},
        {40000000U, 0U, false, 0U, 0U},
    };
    for (auto const& c : cases)
    {
        flexio::BaudSetting const s = flexio::calculateBaud(c.clockHz, c.requestHz);
        CHECK(s.valid == c.valid);
        if (c.valid && s.valid)
        {
            CHECK(s.compare == c.compare);
            CHECK(s.sclHz == c.sclHz);
            CHECK(s.sclHz <= c.requestHz);
            CHECK(s.compare >= flexio::MIN_BAUD_COMPARE);
            // the next faster setting would exceed the request (unless already at f / 6)
            if (s.compare > flexio::MIN_BAUD_COMPARE)
            {
                CHECK((c.clockHz / (2U * s.compare)) > c.requestHz);
            }
        }
    }
    CHECK(flexio::halfPeriodUs(100000U) == 5U);
    CHECK(flexio::halfPeriodUs(400000U) == 2U);
    CHECK(flexio::halfPeriodUs(0U) == 1U);
}

void testInit()
{
    std::printf("init\n");
    Model m;
    model                 = &m;
    Resources const res   = m.res;
    m.shiftctl[2]         = 0x00000102U; // another FlexIO user on shifter 2 / timer 2 stays untouched
    m.timctl[2]           = 0x00000003U;
    m.ctrl                = FLEXIO_CTRL_DOZEN_MASK; // set by another FlexIO user
    FlexioI2cConfig const good = makeConfig(res, EVAL_FLEXIO_HZ, 100000U);
    FlexioI2cMaster notInitialized(good);
    uint8_t byte = 0U;
    CHECK(notInitialized.write(0x50U, &byte, 1U) == I2cResult::NOT_INITIALIZED);
    CHECK(m.packets == 0U);

    FlexioI2cMaster master(good);
    CHECK(master.init());
    CHECK((m.ctrl & FLEXIO_CTRL_FLEXEN_MASK) != 0U);
    CHECK(master.getBaudrateHz() == 100000U);
    CHECK(m.shiftctl[2] == 0x00000102U);
    CHECK(m.timctl[2] == 0x00000003U);
    CHECK((m.ctrl & FLEXIO_CTRL_DOZEN_MASK) != 0U);
    CHECK(m.errors.empty());

    struct Bad
    {
        FlexioI2cConfig config;
        char const* name;
    };
    FlexioI2cConfig c = good;
    std::vector<Bad> bad;
    c.base = nullptr;
    bad.push_back({c, "no base"});
    c         = good;
    c.sclPin  = c.sdaPin;
    bad.push_back({c, "SDA == SCL"});
    c         = good;
    c.sdaPin  = 8U;
    bad.push_back({c, "pin 8"});
    c           = good;
    c.rxShifter = c.txShifter;
    bad.push_back({c, "tx == rx"});
    c           = good;
    c.txShifter = 4U;
    bad.push_back({c, "shifter 4"});
    c          = good;
    c.sclTimer = 3U;
    bad.push_back({c, "no timer after SCL timer"});
    c            = good;
    c.baudrateHz = 50000U;
    bad.push_back({c, "rate below f / 512"});
    c           = good;
    c.timeoutUs = 0U;
    bad.push_back({c, "timeout 0"});
    for (auto const& b : bad)
    {
        FlexioI2cMaster rejected(b.config);
        bool const ok = rejected.init();
        CHECK(!ok);
        if (ok)
        {
            std::printf("    accepted: %s\n", b.name);
        }
        CHECK(rejected.write(0x50U, &byte, 1U) == I2cResult::NOT_INITIALIZED);
    }
    // fewer resources on another derivative (PARAM)
    m.param = FLEXIO_PARAM_PIN(4U) | FLEXIO_PARAM_TIMER(2U) | FLEXIO_PARAM_SHIFTER(2U);
    FlexioI2cMaster small(good); // pins 6/7 do not exist
    CHECK(!small.init());
    m.param = FLEXIO_PARAM_PIN(4U) | FLEXIO_PARAM_TIMER(1U) | FLEXIO_PARAM_SHIFTER(2U);
    FlexioI2cConfig lowPins = makeConfig(Resources{0U, 1U, 0U, 1U, 0U}, EVAL_FLEXIO_HZ, 100000U);
    FlexioI2cMaster oneTimer(lowPins);
    CHECK(!oneTimer.init());
    model = nullptr;
}

struct Fixture
{
    Model m;
    FlexioI2cConfig config;
    FlexioI2cMaster master;

    explicit Fixture(Resources const& res = Resources{6U, 7U, 0U, 1U, 0U}, uint32_t clockHz = EVAL_FLEXIO_HZ,
                     uint32_t baudHz = 100000U)
    : m(), config(makeConfig(res, clockHz, baudHz)), master(config)
    {
        m.res = res;
        model = &m;
        CHECK(master.init());
        m.device.memory.fill(0U);
    }

    ~Fixture() { model = nullptr; }
};

void testWriteAndRead(Resources const& res, uint32_t const clockHz, uint32_t const baudHz, char const* profile)
{
    std::printf("write and read (%s)\n", profile);
    Fixture f(res, clockHz, baudHz);
    uint8_t const data[] = {0x10U, 0xABU, 0x5CU};
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x10 A", "0xAB A", "0x5C A", "P");
    CHECK(f.m.device.memory[0x10U] == 0xABU);
    CHECK(f.m.device.memory[0x11U] == 0x5CU);
    CHECK(f.m.linesReleased());
    CHECK(f.m.errors.empty());
    for (auto const& e : f.m.errors)
    {
        std::printf("    model: %s\n", e.c_str());
    }
    uint32_t const baud = f.m.timcmp[res.sclTimer] & 0xFFU;
    CHECK((clockHz / (2U * (baud + 1U))) <= baudHz);
    CHECK(((f.m.timcmp[res.sclTimer] >> 8U) & 0xFFU) == ((4U * 18U) + 1U));

    f.m.device.log.clear();
    f.m.device.memory[0x20U] = 0x11U;
    f.m.device.memory[0x21U] = 0x82U;
    f.m.device.memory[0x22U] = 0xFFU;
    f.m.device.memory[0x23U] = 0x00U;
    uint8_t const reg        = 0x20U;
    uint8_t rx[4]            = {};
    CHECK(f.master.writeRead(0x50U, &reg, 1U, rx, sizeof(rx)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x20 A", "Sr", "0xA1 A", "0x11 A", "0x82 A", "0xFF A", "0x00 N", "P");
    CHECK((rx[0] == 0x11U) && (rx[1] == 0x82U) && (rx[2] == 0xFFU) && (rx[3] == 0x00U));
    CHECK(f.m.linesReleased());
    CHECK(f.m.errors.empty());
    CHECK(f.master.getErrorCount() == 0U);

    // single byte read: NACK on the only byte
    f.m.device.log.clear();
    uint8_t one = 0U;
    CHECK(f.master.read(0x50U, &one, 1U) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA1 A", "0x00 N", "P");
    CHECK(one == 0x00U); // pointer continued at 0x24
}

void testRegistersMatchReferenceManual()
{
    std::printf("register values of RM Table 54-11\n");
    // Table 54-11 uses shifters n/n+1, timers 0/1, SDA on pin 0 and SCL on pin 1
    Fixture f(Resources{0U, 1U, 0U, 1U, 0U}, 8000000U, 1000000U);
    uint8_t const data = 0x42U;
    CHECK(f.master.write(0x50U, &data, 1U) == I2cResult::OK);
    CHECK(f.m.packets == 1U);
    CHECK(f.m.errors.empty());
    auto const& s = f.m.snapshot;
    CHECK(s[0] == 0x00000032U); // SHIFTCFGn
    CHECK(s[1] == 0x01010082U); // SHIFTCTLn
    CHECK(s[2] == 0x00000020U); // SHIFTCFG(n+1)
    CHECK(s[3] == 0x01800001U); // SHIFTCTL(n+1)
    CHECK(s[4] == 0x00002503U); // TIMCMPn: 2 words, divide by 8 here (Table: 0x2501 divide by 4, below f/6)
    CHECK(s[5] == 0x01022222U); // TIMCFGn
    CHECK(s[6] == 0x01C10101U); // TIMCTLn
    CHECK(s[7] == 0x0000000FU); // TIMCMP(n+1)
    CHECK(s[8] == 0x00201112U); // TIMCFG(n+1)
    CHECK(s[9] == 0x01C00183U); // TIMCTL(n+1)
}

void testProbeAndAddressNack()
{
    std::printf("probe and address NACK\n");
    Fixture f;
    CHECK(f.master.write(0x50U, nullptr, 0U) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "P");
    f.m.device.log.clear();
    uint8_t const data[] = {0x01U, 0x02U};
    CHECK(f.master.write(0x51U, data, sizeof(data)) == I2cResult::ADDRESS_NACK);
    CHECK((f.m.device.log.size() >= 3U) && (f.m.device.log[0] == "S") && (f.m.device.log[1] == "0xA2 N")
          && (f.m.device.log.back() == "P"));
    CHECK(f.m.linesReleased());
    CHECK(f.master.getErrorCount() == 1U);
    f.m.device.log.clear();
    uint8_t rx[2] = {};
    f.m.device.present = false;
    CHECK(f.master.read(0x50U, rx, sizeof(rx)) == I2cResult::ADDRESS_NACK);
    CHECK((f.m.device.log.size() >= 3U) && (f.m.device.log[1] == "0xA1 N") && (f.m.device.log.back() == "P"));
    CHECK(f.m.linesReleased());
    // bus usable again
    f.m.device.present = true;
    f.m.device.log.clear();
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x01 A", "0x02 A", "P");
    CHECK(f.m.errors.empty());
}

void testDataNack(uint32_t const readsPerStep, uint32_t const clocksAfterNack, char const* name)
{
    std::printf("data NACK (%s)\n", name);
    Fixture f;
    f.m.readsPerStep            = readsPerStep;
    f.m.device.nackWriteByte    = 2;
    uint8_t const data[]        = {0x30U, 0x01U, 0x02U, 0x03U, 0x04U};
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::DATA_NACK);
    // STOP straight after the NACK (SDA low from the STOP word), no START/STOP pair
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x30 A", "0x01 A", "0x02 N", "P");
    CHECK(f.m.device.memory[0x31U] == 0x00U);
    // late: the word already in the shifter plus the clock of the STOP word; early: only the STOP word's clock
    CHECK(f.m.device.clocksIgnored == clocksAfterNack);
    CHECK(f.m.linesReleased());
    CHECK(f.master.getErrorCount() == 1U);
    f.m.device.log.clear();
    f.m.device.nackWriteByte = -1;
    CHECK(f.master.write(0x50U, data, 2U) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x30 A", "0x01 A", "P");

    // NACK of the last byte of a write without STOP: the bus must still end with a STOP, never stay owned
    f.m.device.log.clear();
    f.m.device.nackWriteByte = 1;
    CHECK(f.master.write(0x50U, data, 2U, false) == I2cResult::DATA_NACK);
    if (clocksAfterNack > 1U)
    {
        // late: repeated START word already out (SDA high), so the driver ends with START + STOP
        CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x30 A", "0x01 N", "Sr", "P");
    }
    else
    {
        // early: the STOP word replaced the repeated START word
        CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x30 A", "0x01 N", "P");
    }
    CHECK(f.m.linesReleased());
    f.m.device.log.clear();
    f.m.device.nackWriteByte = -1;
    CHECK(f.master.write(0x50U, data, 1U) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x30 A", "P");
}

void testLongestTransfer()
{
    std::printf("longest transfer\n");
    Fixture f;
    uint8_t data[flexio::MAX_BYTES_PER_TRANSFER + 1U];
    for (uint32_t i = 0U; i < sizeof(data); ++i)
    {
        data[i] = static_cast<uint8_t>(0x40U + i);
    }
    data[0] = 0x80U;
    CHECK(f.master.write(0x50U, data, flexio::MAX_BYTES_PER_TRANSFER) == I2cResult::OK);
    CHECK(f.m.device.log.size() == (flexio::MAX_BYTES_PER_TRANSFER + 3U));
    CHECK(f.m.device.memory[0x80U + 11U] == data[12]);
    CHECK(((f.m.timcmp[0] >> 8U) & 0xFFU) == 253U);
    uint8_t rx[flexio::MAX_BYTES_PER_TRANSFER + 1U] = {};
    uint8_t const reg                               = 0x80U;
    CHECK(f.master.write(0x50U, &reg, 1U, false) == I2cResult::OK);
    CHECK(f.master.read(0x50U, rx, flexio::MAX_BYTES_PER_TRANSFER) == I2cResult::OK);
    CHECK((rx[0] == data[1]) && (rx[11] == data[12]));
    CHECK(f.master.write(0x50U, data, flexio::MAX_BYTES_PER_TRANSFER + 1U) == I2cResult::INVALID_ARGUMENT);
    CHECK(f.master.read(0x50U, rx, flexio::MAX_BYTES_PER_TRANSFER + 1U) == I2cResult::INVALID_ARGUMENT);
    CHECK(f.master.read(0x50U, rx, 0U) == I2cResult::INVALID_ARGUMENT);
    CHECK(f.master.read(0x50U, nullptr, 1U) == I2cResult::INVALID_ARGUMENT);
    CHECK(f.master.write(0x50U, nullptr, 1U) == I2cResult::INVALID_ARGUMENT);
    CHECK(f.master.write(0x80U, data, 1U) == I2cResult::INVALID_ARGUMENT);
    CHECK(f.m.errors.empty());
}

void testClockStretching()
{
    std::printf("clock stretching\n");
    Fixture f;
    f.m.device.stretchHalfBits = 25U;
    uint8_t const data[]       = {0x05U, 0x99U};
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x05 A", "0x99 A", "P");
    CHECK(f.m.device.stretched == 75U); // three ACK slots of 25 steps each
    f.m.device.log.clear();
    uint8_t rx[3]            = {};
    f.m.device.memory[0x05U] = 0x99U;
    f.m.device.memory[0x06U] = 0x3CU;
    f.m.device.memory[0x07U] = 0xC3U;
    uint8_t const reg        = 0x05U;
    CHECK(f.master.writeRead(0x50U, &reg, 1U, rx, sizeof(rx)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x05 A", "Sr", "0xA1 A", "0x99 A", "0x3C A", "0xC3 N", "P");
    CHECK((rx[0] == 0x99U) && (rx[1] == 0x3CU) && (rx[2] == 0xC3U));
    CHECK(f.m.errors.empty());

    std::printf("endless clock stretching\n");
    f.m.device.log.clear();
    f.m.device.stretchHalfBits = 0xFFFFFFFFU;
    uint32_t const before      = nxTestNowUs;
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::TIMEOUT);
    CHECK((nxTestNowUs - before) >= f.config.timeoutUs);
    CHECK(f.master.getErrorCount() == 1U);
    CHECK(f.m.timctl[0] == 0U);    // timers off
    CHECK(f.m.shiftctl[0] == 0U);  // SDA released by the master
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::BUS_BUSY);
    // device gives up, bus recovers
    f.m.device.stretchHalfBits = 0U;
    f.m.device.reset();
    f.m.updateLines();
    f.m.device.log.clear();
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x05 A", "0x99 A", "P");
}

void testBusFaults()
{
    std::printf("bus faults\n");
    Fixture f;
    uint8_t const data[] = {0x10U, 0x7FU, 0x01U};
    f.m.device.sdaStuck  = true;
    f.m.updateLines();
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::BUS_BUSY);
    CHECK(f.m.packets == 0U);
    f.m.device.sdaStuck = false;
    f.m.device.sclStuck = true;
    f.m.updateLines();
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::BUS_BUSY);
    CHECK(f.m.packets == 0U);
    CHECK(f.master.getErrorCount() == 2U);
    f.m.device.sclStuck = false;
    f.m.updateLines();
    f.m.device.log.clear();

    std::printf("SDA pulled low during a data byte\n");
    f.m.device.pullLowWriteByte = 1;
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::BUS_ERROR);
    CHECK(f.m.timctl[0] == 0U);
    f.m.device.pullLowWriteByte = -1;
    f.m.device.reset();
    f.m.updateLines();
    f.m.device.log.clear();
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x10 A", "0x7F A", "0x01 A", "P");
}

void testDriverTooSlow()
{
    std::printf("driver too slow for the bus\n");
    Fixture f;
    f.m.stepsPerRead     = 40U;
    uint8_t const data[] = {0x10U, 0x7FU, 0x01U};
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::BUS_ERROR);
    CHECK(f.m.timctl[0] == 0U);
    CHECK(f.m.shiftctl[0] == 0U);
    CHECK(f.master.getErrorCount() == 1U);
}

void testStaleFlags()
{
    std::printf("stale flags before a transfer\n");
    Fixture f;
    // flags left by code outside the driver (debugger, another FlexIO user before a hand-over)
    f.m.shiftbuf[1] = 0x12345678U;
    f.m.ssf |= bitOf(1U);
    f.m.sef |= bitOf(0U) | bitOf(1U);
    f.m.tsf |= bitOf(0U);
    uint8_t const data[] = {0x10U, 0x55U};
    CHECK(f.master.write(0x50U, data, sizeof(data)) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x10 A", "0x55 A", "P");
}

void testRepeatedStartChain()
{
    std::printf("repeated START chain\n");
    Fixture f;
    uint8_t const first[]  = {0x40U, 0x01U};
    uint8_t const second[] = {0x02U};
    CHECK(f.master.write(0x50U, first, sizeof(first), false) == I2cResult::OK);
    CHECK(f.m.linesReleased());
    CHECK(f.master.write(0x50U, second, sizeof(second), false) == I2cResult::OK);
    uint8_t rx = 0U;
    CHECK(f.master.read(0x50U, &rx, 1U, true) == I2cResult::OK);
    CHECK_LOG(f.m.device.log, "S", "0xA0 A", "0x40 A", "0x01 A", "Sr", "0xA0 A", "0x02 A", "Sr", "0xA1 A",
              "0x00 N", "P");
    CHECK(f.m.device.memory[0x40U] == 0x01U);
    CHECK(f.m.device.memory[0x02U] == 0x00U); // second write only set the pointer
    CHECK(f.m.errors.empty());
}

} // namespace

int main()
{
    testTiming();
    testInit();
    testRegistersMatchReferenceManual();
    testWriteAndRead(Resources{6U, 7U, 0U, 1U, 0U}, EVAL_FLEXIO_HZ, 100000U, "FXIO_D6/D7, 40 MHz, 100 kHz");
    testWriteAndRead(Resources{6U, 7U, 0U, 1U, 0U}, EVAL_FLEXIO_HZ, 400000U, "FXIO_D6/D7, 40 MHz, 400 kHz");
    testWriteAndRead(Resources{1U, 0U, 3U, 2U, 2U}, 48000000U, 100000U, "FXIO_D1/D0, shifters 3/2, timers 2/3, 48 MHz");
    testWriteAndRead(Resources{0U, 5U, 1U, 0U, 1U}, 8000000U, 400000U, "FXIO_D0/D5, shifters 1/0, timers 1/2, 8 MHz");
    testProbeAndAddressNack();
    testDataNack(1U, 10U, "NACK seen after the next word was loaded");
    testDataNack(5U, 1U, "NACK seen before the next word was loaded");
    testLongestTransfer();
    testClockStretching();
    testBusFaults();
    testDriverTooSlow();
    testStaleFlags();
    testRepeatedStartChain();
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
