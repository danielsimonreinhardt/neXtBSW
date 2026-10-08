/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

#include "adc/Adc.h"

// Clock divider, sample time, resolution and hardware averaging of the converter come from the project
// (modules.bspAdcAccess, IO-19); the generator turns them into these register values and derives the conversion
// time and the polling bound from them (bsp/adc/AdcConversionTiming.h).
#include <bsp/adc/adcAccessConfiguration.h>

// PLAT-12: number of ADC converters of the derivative (mcudb instances with class adc). A derivative with one
// converter (S32K116: only ADC0) has no IP_ADC1 in its device header, so everything about the second converter
// is compiled only where it exists.
#include <mcu/derivativeFacts.h>

namespace bios
{
static uint8_t const ADC_0_CH = 0U;
#if NX_ADC_INSTANCE_COUNT > 1
static uint8_t const ADC_1_CH = 1U;
#endif

/**
 * ADC configuration
 */
class Adc12BitConfiguration
{
public:
    enum
    {
        CFG1 = NX_ADC_CFG1, // clock divider and conversion mode (resolution) of the project
        CFG2 = NX_ADC_CFG2, // sample time of the project
        CV1  = 0,
        CV2  = 0,
        SC2  = ADC_SC2_ADTRG(0) | ADC_SC2_DMAEN(0), // SW trigger, DMA is off
        SC3  = NX_ADC_SC3, // Calibration disabled, one conversion, hardware averaging of the project
        BASE_OFS = 0x40UL,
        OFS      = 0x0UL,
        USR_OFS  = 0,
        XOFS     = 0x30UL,
        YOFS     = 0x37UL,
        UG       = 0x4UL,
        CLPS_OFS = 0x0UL,
    };
};

// The result converter below is fixed to 12 bit; the configured resolution has to match it (bswgen checks the
// resolution of the derivative as well, ADC_DRIVER_BITS).
static_assert(NX_ADC_RESOLUTION_BITS == 12U, "AnalogInput converts 12-bit results only");

typedef AdcInResolution<uint16_t, adc12BitResolutionMask, adcDataShift0> tAdc12BitConvertor;

typedef Adc<tAdc12BitConvertor, Adc12BitConfiguration> tAdc12Bit;

class AnalogInput
{
public:
    AnalogInput();

#undef BSP_ANALOGINPUT_PIN_CONFIGURATION
#include "bsp/adc/analogInputConfiguration.h"

    typedef struct
    {
        uint8_t adc;
        uint8_t phChannel;
        uint8_t pin;
        uint8_t slot;
    } tAnalogConfiguration;

    void init();
    void start();

    /**
     * Synchronous conversion. Properties of the ADC contract (adc/AdcAccess.h, IO-19):
     *  - callable from every context: several tasks, ISRs, with the interrupts suspended and
     *    before the scheduler starts. Conversions are serialized by adc::AdcAccess (IO-14).
     *  - returns after a time derived from the conversion time of the configured converter
     *    (bsp/adc/AdcConversionTiming.h: NX_ADC_CONVERSION_NS times the safety factor of the
     *    project), also when the converter never signals completion.
     *  - a failed conversion returns BSP_ERROR and is never substituted by a substitute value;
     *    `value` is then not meaningful and the caller decides what the failure means (IO-16).
     * Statistics and the configured values: console group "adcaccess" (AdcAccessCommand.h).
     */
    static bsp::BspReturnCode get(uint16_t channel, uint16_t& value);
    static bsp::BspReturnCode get(uint16_t channel, uint32_t& value);

    static char const* getName(uint16_t channel);

    void stop();

    void cyclic();

    static uint16_t const AdcCountAll = static_cast<uint16_t>(_AdcChannelMax);

private:
    friend struct AnalogInputConverter;

    static tAdc12Bit fAdc0;
#if NX_ADC_INSTANCE_COUNT > 1
    static tAdc12Bit fAdc1;
#endif

    static tAnalogConfiguration const* fADChannelCfg;
};

} // namespace bios
