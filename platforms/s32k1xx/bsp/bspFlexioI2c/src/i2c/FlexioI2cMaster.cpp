// SPDX-License-Identifier: Apache-2.0
#include "i2c/FlexioI2cMaster.h"

#include "bsp/timer/SystemTimer.h"

namespace bsp
{
namespace i2c
{

namespace
{
// Field values, S32K1xx Reference Manual Rev. 13, 54.3.1.13 - 54.3.1.20
constexpr uint32_t SMOD_RECEIVE         = 1U;
constexpr uint32_t SMOD_TRANSMIT        = 2U;
constexpr uint32_t PINCFG_OPEN_DRAIN    = 1U; // pin output enable, drives low while asserted
constexpr uint32_t TIMOD_DUAL_8BIT_BAUD = 1U;
constexpr uint32_t TIMOD_16BIT          = 3U;
constexpr uint32_t START_BIT_LOW        = 2U; // SHIFTCFG[SSTART] 10b
constexpr uint32_t STOP_BIT_LOW         = 2U; // SHIFTCFG[SSTOP] 10b: transmit 0 / expect 0 (ACK)
constexpr uint32_t STOP_BIT_HIGH        = 3U; // SHIFTCFG[SSTOP] 11b: transmit 1 (release SDA, NACK)
constexpr uint32_t BITS_PER_WORD        = 8U;
constexpr uint32_t TIMER_ENABLE_WHEN_PREVIOUS = 1U; // TIMCFG[TIMENA]/[TIMDIS] 001b: with timer N-1
constexpr uint32_t TIMERS_PER_ENABLE_CHAIN    = 4U; // 54.3.1.20.2: "Timer N-1" reserved for N divisible by 4
constexpr uint8_t STOP_WORD           = 0x00U; // 54.5.5: extra word, SDA low before STOP
constexpr uint8_t REPEATED_START_WORD = 0xFFU; // 54.5.5: extra word, SDA high before repeated START
constexpr uint8_t RELEASED_WORD       = 0xFFU; // transmitter tristates SDA while the device sends

uint32_t bit(uint8_t const index) { return 1U << index; }
} // namespace

FlexioI2cMaster::FlexioI2cMaster(FlexioI2cConfig const& config)
: _config(config), _baudrateHz(0U), _errorCount(0U), _baudCompare(0U), _initialized(false)
{}

bool FlexioI2cMaster::init()
{
    _initialized = false;
    FLEXIO_Type* const io = _config.base;
    if (io == nullptr)
    {
        return false;
    }
    // Resources of this derivative (54.3.1.3 PARAM), not the numbers of one device.
    uint32_t const param    = io->PARAM;
    uint32_t const pins     = (param & FLEXIO_PARAM_PIN_MASK) >> FLEXIO_PARAM_PIN_SHIFT;
    uint32_t const timers   = (param & FLEXIO_PARAM_TIMER_MASK) >> FLEXIO_PARAM_TIMER_SHIFT;
    uint32_t const shifters = (param & FLEXIO_PARAM_SHIFTER_MASK) >> FLEXIO_PARAM_SHIFTER_SHIFT;
    uint32_t const shiftTimer = static_cast<uint32_t>(_config.sclTimer) + 1U;
    bool const resourcesValid
        = (_config.sdaPin < pins) && (_config.sclPin < pins) && (_config.sdaPin != _config.sclPin)
          && (_config.txShifter < shifters) && (_config.rxShifter < shifters)
          && (_config.txShifter != _config.rxShifter) && (_config.txShifter < FLEXIO_SHIFTCTL_COUNT)
          && (_config.rxShifter < FLEXIO_SHIFTCTL_COUNT) && (shiftTimer < timers)
          && (shiftTimer < FLEXIO_TIMCTL_COUNT) && ((shiftTimer % TIMERS_PER_ENABLE_CHAIN) != 0U);
    flexio::BaudSetting const baud = flexio::calculateBaud(_config.functionalClockHz, _config.baudrateHz);
    if ((!resourcesValid) || (!baud.valid) || (_config.timeoutUs == 0U))
    {
        return false;
    }
    _baudCompare = baud.compare;
    _baudrateHz  = baud.sclHz;

    // Other FlexIO users keep their shifters and timers: no software reset, only the enable bits are added.
    io->CTRL = io->CTRL | FLEXIO_CTRL_FLEXEN_MASK | FLEXIO_CTRL_DBGE_MASK;
    releaseResources();
    clearFlags();
    _initialized = true;
    return true;
}

I2cResult FlexioI2cMaster::write(uint8_t const address, uint8_t const* const data, size_t const length,
                                 bool const sendStop)
{
    if ((length > 0U) && (data == nullptr))
    {
        return I2cResult::INVALID_ARGUMENT;
    }
    return transfer(address, false, data, nullptr, length, sendStop);
}

I2cResult FlexioI2cMaster::read(uint8_t const address, uint8_t* const data, size_t const length,
                                bool const sendStop)
{
    if ((length == 0U) || (data == nullptr))
    {
        return I2cResult::INVALID_ARGUMENT;
    }
    return transfer(address, true, nullptr, data, length, sendStop);
}

I2cResult FlexioI2cMaster::writeRead(uint8_t const address, uint8_t const* const txData, size_t const txLength,
                                     uint8_t* const rxData, size_t const rxLength)
{
    if ((rxLength == 0U) || (rxData == nullptr))
    {
        return I2cResult::INVALID_ARGUMENT;
    }
    I2cResult const result = write(address, txData, txLength, false);
    if (result != I2cResult::OK)
    {
        return result;
    }
    return read(address, rxData, rxLength, true);
}

void FlexioI2cMaster::releaseResources() const
{
    FLEXIO_Type* const io = _config.base;
    // Timers off first: SCL released and no new shift clock, then SDA released.
    io->TIMCTL[_config.sclTimer]      = 0U;
    io->TIMCTL[_config.sclTimer + 1U] = 0U;
    io->SHIFTCTL[_config.txShifter]   = 0U;
    io->SHIFTCTL[_config.rxShifter]   = 0U;
}

void FlexioI2cMaster::clearFlags() const
{
    FLEXIO_Type* const io = _config.base;
    uint32_t const shifters = bit(_config.txShifter) | bit(_config.rxShifter);
    io->SHIFTSTAT           = shifters;
    io->SHIFTERR            = shifters;
    io->TIMSTAT             = bit(_config.sclTimer) | bit(static_cast<uint8_t>(_config.sclTimer + 1U));
}

bool FlexioI2cMaster::isPinHigh(uint8_t const pin) const
{
    return ((_config.base->PIN & FLEXIO_PIN_PDI_MASK) & bit(pin)) != 0U;
}

void FlexioI2cMaster::waitUs(uint32_t const durationUs) const
{
    uint32_t const start = getSystemTimeUs32Bit();
    while ((getSystemTimeUs32Bit() - start) < durationUs) {}
}

void FlexioI2cMaster::setTxStopBit(bool const high) const
{
    _config.base->SHIFTCFG[_config.txShifter]
        = FLEXIO_SHIFTCFG_SSTART(START_BIT_LOW) | FLEXIO_SHIFTCFG_SSTOP(high ? STOP_BIT_HIGH : STOP_BIT_LOW);
}

void FlexioI2cMaster::configurePacket(uint32_t const words) const
{
    FLEXIO_Type* const io     = _config.base;
    uint32_t const shiftTimer = static_cast<uint32_t>(_config.sclTimer) + 1U;
    // Internal trigger "shifter N status flag" (54.3.1.19.3: 4 * N + 1), active low: the packet starts when the
    // first word is written into the empty transmit buffer.
    uint32_t const trigger = FLEXIO_TIMCTL_TRGSEL((4U * _config.txShifter) + 1U) | FLEXIO_TIMCTL_TRGPOL(1U)
                             | FLEXIO_TIMCTL_TRGSRC(1U);

    releaseResources();
    clearFlags();

    // Word counts and rates (54.5.5 Table 54-11)
    io->TIMCMP[_config.sclTimer] = flexio::sclTimerCompare(words, _baudCompare);
    io->TIMCMP[shiftTimer]       = (2U * BITS_PER_WORD) - 1U;

    // Transmitter: START as start bit, ACK slot released (the device acknowledges) until a read sets it low.
    setTxStopBit(true);
    // Receiver: no start bit, the stop bit is the ACK slot and must be low (SHIFTERR on NACK).
    io->SHIFTCFG[_config.rxShifter] = FLEXIO_SHIFTCFG_SSTOP(STOP_BIT_LOW);

    // SCL timer: output low (SCL released) when enabled, on trigger high, off after the last edge of the packet,
    // counter reset while the SCL pin equals the output (clock stretching), start and stop bit.
    io->TIMCFG[_config.sclTimer] = FLEXIO_TIMCFG_TIMOUT(1U) | FLEXIO_TIMCFG_TIMDEC(0U) | FLEXIO_TIMCFG_TIMRST(2U)
                                   | FLEXIO_TIMCFG_TIMDIS(2U) | FLEXIO_TIMCFG_TIMENA(2U) | FLEXIO_TIMCFG_TSTOP(2U)
                                   | FLEXIO_TIMCFG_TSTART(1U);
    // Shift timer: counts edges of the SCL pin (data hold time through the synchronizer), with the SCL timer on and
    // off, start bit and a stop bit (ACK slot) after every eight bits.
    io->TIMCFG[shiftTimer] = FLEXIO_TIMCFG_TIMOUT(0U) | FLEXIO_TIMCFG_TIMDEC(2U) | FLEXIO_TIMCFG_TIMRST(0U)
                             | FLEXIO_TIMCFG_TIMDIS(TIMER_ENABLE_WHEN_PREVIOUS)
                             | FLEXIO_TIMCFG_TIMENA(TIMER_ENABLE_WHEN_PREVIOUS) | FLEXIO_TIMCFG_TSTOP(1U)
                             | FLEXIO_TIMCFG_TSTART(1U);

    // Shifters before the timers: the transmitter in transmit mode sets its status flag at once (54.4.2.1), so the
    // trigger is inactive when the SCL timer mode is written.
    io->SHIFTCTL[_config.rxShifter] = FLEXIO_SHIFTCTL_TIMSEL(shiftTimer) | FLEXIO_SHIFTCTL_TIMPOL(1U)
                                      | FLEXIO_SHIFTCTL_PINCFG(0U) | FLEXIO_SHIFTCTL_PINSEL(_config.sdaPin)
                                      | FLEXIO_SHIFTCTL_PINPOL(0U) | FLEXIO_SHIFTCTL_SMOD(SMOD_RECEIVE);
    io->SHIFTCTL[_config.txShifter] = FLEXIO_SHIFTCTL_TIMSEL(shiftTimer) | FLEXIO_SHIFTCTL_TIMPOL(0U)
                                      | FLEXIO_SHIFTCTL_PINCFG(PINCFG_OPEN_DRAIN)
                                      | FLEXIO_SHIFTCTL_PINSEL(_config.sdaPin) | FLEXIO_SHIFTCTL_PINPOL(1U)
                                      | FLEXIO_SHIFTCTL_SMOD(SMOD_TRANSMIT);

    // Timer mode last (54.4.3: TIMCFG before TIMOD).
    io->TIMCTL[shiftTimer] = trigger | FLEXIO_TIMCTL_PINCFG(0U) | FLEXIO_TIMCTL_PINSEL(_config.sclPin)
                             | FLEXIO_TIMCTL_PINPOL(1U) | FLEXIO_TIMCTL_TIMOD(TIMOD_16BIT);
    io->TIMCTL[_config.sclTimer] = trigger | FLEXIO_TIMCTL_PINCFG(PINCFG_OPEN_DRAIN)
                                   | FLEXIO_TIMCTL_PINSEL(_config.sclPin) | FLEXIO_TIMCTL_PINPOL(0U)
                                   | FLEXIO_TIMCTL_TIMOD(TIMOD_DUAL_8BIT_BAUD);
}

I2cResult FlexioI2cMaster::transfer(uint8_t const address, bool const isRead, uint8_t const* const txData,
                                    uint8_t* const rxData, size_t const length, bool const sendStop)
{
    if (!_initialized)
    {
        return I2cResult::NOT_INITIALIZED;
    }
    if ((address > 0x7FU) || (length > flexio::MAX_BYTES_PER_TRANSFER))
    {
        return I2cResult::INVALID_ARGUMENT;
    }
    FLEXIO_Type* const io = _config.base;
    uint32_t const start  = getSystemTimeUs32Bit();
    if ((!isPinHigh(_config.sdaPin)) || (!isPinHigh(_config.sclPin)))
    {
        ++_errorCount;
        return I2cResult::BUS_BUSY;
    }

    uint32_t const words      = static_cast<uint32_t>(length) + 1U;
    uint8_t const addressByte = static_cast<uint8_t>((static_cast<uint32_t>(address) << 1U) | (isRead ? 1U : 0U));
    uint32_t const txBit      = bit(_config.txShifter);
    uint32_t const rxBit      = bit(_config.rxShifter);
    configurePacket(words);

    // Both shifters are serviced for every word (54.5.5). The transmit buffer is filled as soon as the previous
    // word moved into the shifter (AN12174 5.4: double buffered, no underrun at high rates); at that moment the
    // ACK slot of the word just loaded is still eight bits away, so its stop bit is set then.
    uint32_t nextWord = 0U; // word for the transmit buffer; index `words` is the STOP/repeated START word
    uint32_t received = 0U;
    while ((received < words) || (nextWord <= words))
    {
        if ((getSystemTimeUs32Bit() - start) > _config.timeoutUs)
        {
            return abortPacket(I2cResult::TIMEOUT);
        }
        if ((io->SHIFTERR & txBit) != 0U)
        {
            return abortPacket(I2cResult::BUS_ERROR); // transmit underrun: the next word came too late
        }
        uint32_t const status = io->SHIFTSTAT;
        if (((status & txBit) != 0U) && (nextWord <= words))
        {
            if (nextWord > 0U)
            {
                uint32_t const loaded = nextWord - 1U;
                // A read acknowledges every data byte except the last one (NACK ends the read).
                bool const masterAck = isRead && (loaded > 0U) && (loaded < (words - 1U));
                setTxStopBit(!masterAck);
            }
            uint8_t value = sendStop ? STOP_WORD : REPEATED_START_WORD; // after the last word
            if (nextWord == 0U)
            {
                value = addressByte;
            }
            else if (nextWord < words)
            {
                value = isRead ? RELEASED_WORD : txData[nextWord - 1U];
            }
            // MSB first: bit swapped within the byte (54.5.5 Table 54-11)
            io->SHIFTBUFBBS[_config.txShifter] = value;
            ++nextWord;
        }
        if ((status & rxBit) != 0U)
        {
            // Store and ACK check happen on the same shift edge (54.4.3.6).
            uint8_t const value = static_cast<uint8_t>(io->SHIFTBUFBIS[_config.rxShifter] & 0xFFU);
            bool const nack     = (io->SHIFTERR & rxBit) != 0U;
            if (nack)
            {
                io->SHIFTERR = rxBit;
            }
            uint32_t const index = received;
            ++received;
            if ((index == 0U) || (!isRead))
            {
                if (nack)
                {
                    return abortPacket((index == 0U) ? I2cResult::ADDRESS_NACK : I2cResult::DATA_NACK);
                }
                uint8_t const sent = (index == 0U) ? addressByte : txData[index - 1U];
                if (value != sent)
                {
                    return abortPacket(I2cResult::BUS_ERROR);
                }
            }
            else
            {
                rxData[index - 1U] = value;
                // Own ACK must read back low, the final NACK high.
                if (nack != (index == (words - 1U)))
                {
                    return abortPacket(I2cResult::BUS_ERROR);
                }
            }
        }
    }
    return finishPacket(start, sendStop);
}

I2cResult FlexioI2cMaster::finishPacket(uint32_t const start, bool const sendStop)
{
    FLEXIO_Type* const io  = _config.base;
    uint32_t const sclBit  = bit(_config.sclTimer);
    uint32_t const halfUs  = flexio::halfPeriodUs(_baudrateHz);
    // SCL timer compare: the last edge of the packet, SCL released high with SDA low (STOP) or high (repeated START)
    while (((io->TIMSTAT & sclBit) == 0U) || (!isPinHigh(_config.sclPin)))
    {
        if ((getSystemTimeUs32Bit() - start) > _config.timeoutUs)
        {
            return abortPacket(I2cResult::TIMEOUT);
        }
    }
    waitUs(halfUs); // setup time of STOP / repeated START
    releaseResources();   // SDA released: rising edge while SCL is high is the STOP
    clearFlags();
    if (sendStop)
    {
        waitUs(halfUs); // bus free time before the next START
    }
    if ((!isPinHigh(_config.sdaPin)) || (!isPinHigh(_config.sclPin)))
    {
        ++_errorCount;
        return I2cResult::BUS_ERROR;
    }
    return I2cResult::OK;
}

I2cResult FlexioI2cMaster::abortPacket(I2cResult const reason)
{
    FLEXIO_Type* const io = _config.base;
    uint32_t const txBit  = bit(_config.txShifter);
    uint32_t const sclBit = bit(_config.sclTimer);
    uint32_t const halfUs = flexio::halfPeriodUs(_baudrateHz);
    ++_errorCount;

    // 54.5.5 on NACK: STOP word into the transmit buffer (replaces a word not yet loaded), wait until it is on SDA or
    // the packet has ended, then for SCL high, then timers off.
    io->SHIFTBUFBBS[_config.txShifter] = STOP_WORD;
    uint32_t const start               = getSystemTimeUs32Bit();
    bool inTime                        = true;
    while (inTime && ((io->SHIFTSTAT & txBit) == 0U) && ((io->TIMSTAT & sclBit) == 0U))
    {
        inTime = (getSystemTimeUs32Bit() - start) <= _config.timeoutUs;
    }
    while (inTime && (!isPinHigh(_config.sclPin)))
    {
        inTime = (getSystemTimeUs32Bit() - start) <= _config.timeoutUs;
    }
    io->TIMCTL[_config.sclTimer]      = 0U;
    io->TIMCTL[_config.sclTimer + 1U] = 0U;
    if (isPinHigh(_config.sclPin))
    {
        if (isPinHigh(_config.sdaPin))
        {
            // SDA is high (repeated START word already out, or no word loaded): pull it low while SCL is high.
            // A transmitter entering transmit mode outputs its stop bit at once (54.4.2.1), so leave it first.
            io->SHIFTCTL[_config.txShifter] = 0U;
            setTxStopBit(false);
            io->SHIFTCTL[_config.txShifter] = FLEXIO_SHIFTCTL_PINCFG(PINCFG_OPEN_DRAIN)
                                              | FLEXIO_SHIFTCTL_PINSEL(_config.sdaPin)
                                              | FLEXIO_SHIFTCTL_PINPOL(1U) | FLEXIO_SHIFTCTL_SMOD(SMOD_TRANSMIT);
        }
        waitUs(halfUs);
    }
    // Releasing SDA while SCL is high is the STOP. With SCL held low by a device there is no STOP; the next call
    // then reports BUS_BUSY.
    releaseResources();
    clearFlags();
    waitUs(halfUs);
    return reason;
}

} // namespace i2c
} // namespace bsp
