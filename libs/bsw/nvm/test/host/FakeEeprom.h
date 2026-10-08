// NVM-02/NVM-18 host tests of libs/bsw/nvm: a virtual EEPROM in RAM behind OpenBSW's eeprom::IEepromDriver (the
// memory attrappe of the NvM, same as ecu/application/src/systems/test/host/FakeEeprom.h).
// Erased bytes read 0xFF like an EEPROM emulation after preparation. Faults for the tests: refuse reads, refuse
// writes, stop after a number of written bytes (power loss in the middle of a record), drop writes silently.
// NVM-18: a power loss as soon as a write reaches an address range (lossFrom..lossTo, e.g. the second ASIL copy);
// every later write fails as well until the test clears `dead`.
// NVM-30: reads refused in an address range only (one unreadable place), and one bit flipped in the memory right
// before a given read call (a place that changes between two reads).

#pragma once

#include <bsp/eeprom/IEepromDriver.h>

#include <cstdint>
#include <cstring>

template<uint32_t SIZE>
class FakeEeprom : public ::eeprom::IEepromDriver
{
public:
    FakeEeprom() { erase(); }

    void erase() { std::memset(bytes, 0xFF, sizeof(bytes)); }

    ::bsp::BspReturnCode init() override
    {
        ++initCalls; // the NvM must never call it (on the S32K1 it would partition the FlexNVM)
        return ::bsp::BSP_ERROR;
    }

    ::bsp::BspReturnCode write(uint32_t const address, uint8_t const* const buffer, uint32_t const length) override
    {
        ++writeCalls;
        if (refuseWrites || dead || (buffer == nullptr) || (address > SIZE) || (length > (SIZE - address)))
        {
            return ::bsp::BSP_ERROR;
        }
        if ((address < lossTo) && ((address + length) > lossFrom))
        {
            dead = true;
            return ::bsp::BSP_ERROR;
        }
        for (uint32_t i = 0U; i < length; ++i)
        {
            if (powerLossAfter == 0U)
            {
                return ::bsp::BSP_ERROR; // the rest of the record never arrives
            }
            if (powerLossAfter != NO_LOSS)
            {
                --powerLossAfter;
            }
            if (!dropWrites)
            {
                bytes[address + i] = buffer[i];
            }
            ++bytesWritten;
        }
        return ::bsp::BSP_OK;
    }

    ::bsp::BspReturnCode read(uint32_t const address, uint8_t* const buffer, uint32_t const length) override
    {
        ++readCalls;
        if (refuseReads || (buffer == nullptr) || (address > SIZE) || (length > (SIZE - address)))
        {
            return ::bsp::BSP_ERROR;
        }
        if ((address < refuseReadTo) && ((address + length) > refuseReadFrom))
        {
            return ::bsp::BSP_ERROR;
        }
        if ((flipAtRead != 0U) && (readCalls == flipAtRead) && (flipAddress < SIZE))
        {
            bytes[flipAddress] = static_cast<uint8_t>(bytes[flipAddress] ^ 0x01U);
        }
        std::memcpy(buffer, &bytes[address], length);
        return ::bsp::BSP_OK;
    }

    static constexpr uint32_t NO_LOSS = 0xFFFFFFFFU;

    uint8_t bytes[SIZE];
    uint32_t initCalls      = 0U;
    uint32_t writeCalls     = 0U;
    uint32_t readCalls      = 0U;
    uint32_t bytesWritten   = 0U;
    uint32_t powerLossAfter = NO_LOSS; // bytes still written before the "power loss"
    bool refuseReads        = false;
    bool refuseWrites       = false;
    bool dropWrites         = false;
    uint32_t lossFrom       = 0U; // NVM-18: power loss when a write reaches [lossFrom, lossTo)
    uint32_t lossTo         = 0U;
    bool dead               = false;
    uint32_t refuseReadFrom = 0U; // NVM-30: reads touching [refuseReadFrom, refuseReadTo) fail
    uint32_t refuseReadTo   = 0U;
    uint32_t flipAtRead     = 0U; // NVM-30: before read call number flipAtRead (1 = the first), flip flipAddress
    uint32_t flipAddress    = 0U;
};
