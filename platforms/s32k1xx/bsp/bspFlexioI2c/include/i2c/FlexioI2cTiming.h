// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>

namespace bsp
{
namespace i2c
{

/**
 * Numbers of the FlexIO I2C master that follow from the S32K1xx Reference Manual Rev. 13, chapter 54, alone; no
 * register access, so they are checked on the host with any functional clock (test profiles).
 */
namespace flexio
{

/** 54.3.1.21.3: SCL period in dual 8-bit baud mode is (TIMCMP[7:0] + 1) * 2 FlexIO clock cycles. */
constexpr uint32_t MAX_BAUD_COMPARE = 0xFFU;
/**
 * 54.5.5: "Due to synchronization delays, the data valid time for the transmit output is 2 FlexIO clock cycles, so
 * the maximum baud rate is divide by 6 of the FlexIO clock frequency" -> TIMCMP[7:0] of at least 2.
 */
constexpr uint32_t MIN_BAUD_COMPARE = 2U;
/** 54.5.5 Table 54-11: TIMCMP[15:8] of the SCL timer = (number of words x 18) + 1, eight bits wide. */
constexpr uint32_t EDGES_PER_WORD = 18U;
/** Words (address byte included) that fit into one packet from START to repeated START or STOP: (255 - 1) / 18. */
constexpr uint32_t MAX_WORDS_PER_PACKET = (0xFFU - 1U) / EDGES_PER_WORD;
/** Data bytes per call of write() or read(): one word of the packet is the address byte. */
constexpr uint32_t MAX_BYTES_PER_TRANSFER = MAX_WORDS_PER_PACKET - 1U;

struct BaudSetting
{
    uint8_t compare;    // TIMCMP[7:0] of the SCL timer
    uint32_t sclHz;     // nominal SCL frequency; clock stretching support lengthens each half period (54.5.5)
    bool valid;         // false if no compare value keeps SCL at or below the requested rate and within f/6
};

/**
 * Fastest SCL frequency that does not exceed \p requestedHz. An I2C bus must never run faster than its slowest
 * device, so unlike a SPI divider a request below the slowest setting is not rounded up but reported as invalid.
 */
constexpr BaudSetting calculateBaud(uint32_t const functionalClockHz, uint32_t const requestedHz)
{
    if ((functionalClockHz == 0U) || (requestedHz == 0U))
    {
        return BaudSetting{0U, 0U, false};
    }
    // (compare + 1) * 2 >= functionalClockHz / requestedHz, rounded up so that the result stays at or below.
    uint32_t const twoRequested = 2U * requestedHz;
    uint32_t halfDivider        = (functionalClockHz / twoRequested)
                           + (((functionalClockHz % twoRequested) != 0U) ? 1U : 0U);
    if (halfDivider < (MIN_BAUD_COMPARE + 1U))
    {
        halfDivider = MIN_BAUD_COMPARE + 1U;
    }
    if (halfDivider > (MAX_BAUD_COMPARE + 1U))
    {
        return BaudSetting{0U, 0U, false};
    }
    return BaudSetting{
        static_cast<uint8_t>(halfDivider - 1U), functionalClockHz / (2U * halfDivider), true};
}

/** TIMCMP value of the SCL timer for a packet of \p words (address byte included, 1..MAX_WORDS_PER_PACKET). */
constexpr uint32_t sclTimerCompare(uint32_t const words, uint8_t const baudCompare)
{
    return (((words * EDGES_PER_WORD) + 1U) << 8U) | static_cast<uint32_t>(baudCompare);
}

/** Microseconds of half an SCL period, rounded up, at least 1 (setup and bus free times around STOP). */
constexpr uint32_t halfPeriodUs(uint32_t const sclHz)
{
    return (sclHz == 0U) ? 1U : ((500000U + sclHz - 1U) / sclHz);
}

} // namespace flexio
} // namespace i2c
} // namespace bsp
