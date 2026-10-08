/* SYS-150/SYS-151 host test stub of the OpenBSW output manager (outputManager/Output.h): the interface the family port
 * and bios::OutputTester use, with the outputs of the test profile "irrigation_ctrl" in an order that differs from the
 * safe outputs table (HEATER_N before PUMP_EN, FAN - the relay that shares its name with the PWM group - between
 * them). set(), get() and getName() are defined by the test, which drives its register model with them. */
#pragma once

#include <cstdint>

namespace bsp
{
enum BspReturnCode
{
    BSP_OK,
    BSP_ERROR,
    BSP_NOT_SUPPORTED
};
} // namespace bsp

namespace bios
{
class Output
{
public:
    enum OutputId
    {
        /*  0 */ LED_GREEN,
        /*  1 */ HEATER_N,
        /*  2 */ FAN,
        /*  3 */ PUMP_EN,
        /*  4 */ BRAKE_LIGHT,

        NUMBER_OF_INTERNAL_OUTPUTS,
        END_EXTERNAL_OUTPUTS,
        NUMBER_OF_EXTERNAL_OUTPUTS = END_EXTERNAL_OUTPUTS - NUMBER_OF_INTERNAL_OUTPUTS,
        TOTAL_NUMBER_OF_OUTPUTS    = NUMBER_OF_INTERNAL_OUTPUTS + NUMBER_OF_EXTERNAL_OUTPUTS,
        PORT_UNAVAILABLE           = TOTAL_NUMBER_OF_OUTPUTS
    };

    Output() {}

    static bsp::BspReturnCode set(OutputId chan, uint8_t vol, bool latch = true);
    static bsp::BspReturnCode get(OutputId chan, bool& result);
    static char const* getName(uint16_t channel);
};
} // namespace bios
