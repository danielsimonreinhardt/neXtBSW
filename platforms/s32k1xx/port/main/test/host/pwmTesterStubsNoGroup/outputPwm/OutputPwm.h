/* SYS-162 host test stub of the OpenBSW PWM output manager (outputPwm/OutputPwm.h), second test profile "dosing_unit":
 * a project without a PWM group (ARCH-17), so the generated channel list holds only OpenBSW's static dummy channel.
 * Same interface as pwmTesterStubs/outputPwm/OutputPwm.h; setDuty(), setPeriod() and getName() are defined by the
 * test, which records every call. */
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
        _pwmStaticStart  = 0,
        dummyPwm         = _pwmStaticStart,
        _pwmStaticEnd,
        _pwmDynamicStart = _pwmStaticEnd,
        _pwmDynamicMark  = _pwmDynamicStart,
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
