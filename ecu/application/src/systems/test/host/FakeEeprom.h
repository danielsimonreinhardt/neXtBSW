// NVM-02 host tests: a virtual EEPROM in RAM behind OpenBSW's eeprom::IEepromDriver (the memory attrappe of the NvM).
// Erased bytes read 0xFF like an EEPROM emulation after preparation. Faults for the tests: refuse reads, refuse
// writes, stop after a number of written bytes (power loss in the middle of a record), drop writes silently.

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
        if (refuseWrites || (buffer == nullptr) || (address > SIZE) || (length > (SIZE - address)))
        {
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
};
