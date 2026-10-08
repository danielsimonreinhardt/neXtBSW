/* PLAT-110 host test stub of the OpenBSW PWM output manager (outputPwm/OutputPwm.h): the interface
 * bios::OutputPwmTester uses, with the channels of the test profile "irrigation_ctrl" (OpenBSW's static dummy channel,
 * then the PWM channels FAN and VALVE). setDuty(), setPeriod() and getName() are defined by the test, which records
 * every call; the uint16_t channel parameter is OpenBSW's. */
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
class OutputPwm
{
public:
    enum outputPwm
    {
        _pwmStaticStart = 0,
        dummyPwm        = _pwmStaticStart,
        _pwmStaticEnd,
        _pwmDynamicStart = _pwmStaticEnd,
        FAN              = _pwmDynamicStart,
        VALVE,
        _pwmDynamicMark,
        _pwmChannelMax   = _pwmDynamicMark,
        PORT_UNAVAILABLE = _pwmChannelMax
    };

    static uint8_t const outputPwmStaticNumber = _pwmStaticEnd;
    static uint8_t const outputPwmNumberAll    = _pwmChannelMax;
    static uint32_t const MAX_DUTY_CYCLE       = 10000;

    static bsp::BspReturnCode setDuty(uint16_t chan, uint16_t duty, bool immediateUpdate = false);
    static bsp::BspReturnCode setPeriod(uint16_t chan, uint16_t period);
    static char const* getName(uint16_t channel);
};
} // namespace bios
