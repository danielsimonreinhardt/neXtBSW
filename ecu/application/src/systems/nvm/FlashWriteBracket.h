// SYS-44: the persistent memory of the NvM with every write bracketed for the ROM check (SM-14).
//
// A write of the virtual EEPROM ends in a flash operation (on the S32K1 an EEPROM record of the FlexNVM emulation,
// written by the flash module after the store into the FlexRAM; elsewhere a data-flash command or an external
// memory). The ROM check must not start a pass while one is open and discards a pass during which one began, so a
// write can never produce a false ROM_CHECK_FAILURE. The decorator opens a Scope (safety::FlashWriteScope on a
// family with a ROM check) around write() and init() of the memory it wraps; read() is not bracketed. It is
// storage and family neutral: Scope is any RAII type, the host test counts with its own.

#pragma once

#include <bsp/eeprom/IEepromDriver.h>

#include <cstdint>

namespace nvm
{

template<class Scope>
class FlashWriteBracket final : public ::eeprom::IEepromDriver
{
public:
    explicit FlashWriteBracket(::eeprom::IEepromDriver& memory) : _memory(memory) {}

    FlashWriteBracket(FlashWriteBracket const&)            = delete;
    FlashWriteBracket& operator=(FlashWriteBracket const&) = delete;

    ::bsp::BspReturnCode init() override
    {
        Scope const scope; // preparing the memory is a flash command (never called by the NvM, NVM-02)
        return _memory.init();
    }

    ::bsp::BspReturnCode write(uint32_t const address, uint8_t const* const buffer, uint32_t const length) override
    {
        Scope const scope;
        return _memory.write(address, buffer, length);
    }

    ::bsp::BspReturnCode read(uint32_t const address, uint8_t* const buffer, uint32_t const length) override
    {
        return _memory.read(address, buffer, length);
    }

private:
    ::eeprom::IEepromDriver& _memory;
};

} // namespace nvm
