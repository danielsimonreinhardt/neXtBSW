/* SYS-151 host test stub of the OpenBSW input manager, as far as the family binders (ModuleBinders.h) need it. */
#pragma once

#include "outputManager/Output.h" // bsp::BspReturnCode of the stub

#include <cstdint>

namespace bios
{
class DigitalInput
{
public:
    enum DigitalInputId
    {
        KEY
    };

    static bsp::BspReturnCode get(DigitalInputId, bool& result)
    {
        result = false;
        return bsp::BSP_OK;
    }
};
} // namespace bios
