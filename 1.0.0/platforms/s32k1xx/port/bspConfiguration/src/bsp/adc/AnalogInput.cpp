/********************************************************************************
 * Copyright (c) 2024 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#include "bsp/adc/AnalogInput.h"

#include "bsp/adc/AdcAccessCommand.h"
#include "bsp/adc/AdcConversionTiming.h"
#include "bsp/adc/AnalogInputScale.h"
#include "io/Io.h"
#include "util/format/SharedStringWriter.h"

// Serialized access (IO-14), libs/bsp/bspAdcAccess. Lock policy and lock timeout come from the project
// (modules.bspAdcAccess, IO-19); the build may override the policy for a comparison build, see
// bspConfiguration/CMakeLists.txt (BSP_ADC_ACCESS_RTOS_MUTEX).
#include <adc/AdcAccess.h>
// IO-31: channels averaged over one PWM period (project adc_sampling, generated analogInputSamplingConfiguration.h)
#include <adc/AdcAveraging.h>
#include <integration/bspAdcAccessConfiguration.h>

#if defined(BSP_ADC_ACCESS_RTOS_MUTEX) || (NX_MODULE_BSPADCACCESS_PARAM_LOCK_POLICY_IS_RTOS_MUTEX == 1)
#define NX_ADC_ACCESS_USE_RTOS_MUTEX 1
#include <adc/FreeRtosAdcLock.h>
// ARCH-26: "may this caller block?" asks the core registers, a platform question, not one of the contract
#include "CortexMInterruptContext.h"
#else
#define NX_ADC_ACCESS_USE_RTOS_MUTEX 0
#include <adc/InterruptLockAdcLock.h>
#endif

#ifdef BSP_ANALOGINPUT_PIN_CONFIGURATION
#undef BSP_ANALOGINPUT_PIN_CONFIGURATION
#endif
#define BSP_ANALOGINPUT_PIN_CONFIGURATION 1

namespace bios
{

#include "bsp/adc/analogInputConfiguration.h"
#include "bsp/adc/analogInputConfigurationStrings.h"
#include "bsp/adc/analogInputScaleConfiguration.h"
#include "bsp/adc/analogInputSamplingConfiguration.h"

static_assert(
    sizeof(analogInputScaleConfiguration) / sizeof(AnalogInputScale::scale)
        == static_cast<size_t>(AnalogInput::_AdcChannelMax),
    "one scale function per analog input");
#if NX_ADC_AVERAGED_CHANNEL_COUNT > 0
static_assert(
    sizeof(analogInputSamplingConfiguration) / sizeof(::adc::AveragingSpec)
        == static_cast<size_t>(AnalogInput::_AdcChannelMax),
    "one sampling entry per analog input");
#endif

tAdc12Bit AnalogInput::fAdc0(*ADC0);
#if NX_ADC_INSTANCE_COUNT > 1
tAdc12Bit AnalogInput::fAdc1(*IP_ADC1);
#endif

AnalogInput::tAnalogConfiguration const* AnalogInput::fADChannelCfg = 0;

/**
 * Converter of adc::AdcAccess for the S32K1 ADC of OpenBSW (software trigger, SC1[0], COCO
 * polling). Instance = AnalogInput::ADC_0_CH / ADC_1_CH.
 *
 * convert() does the steps of OpenBSW Adc::getValueSync but with a polling bound derived from
 * the conversion time (bsp/adc/AdcConversionTiming.h, IO-16) instead of the 0xFFFFFF00
 * iterations of the driver, which would suspend the interrupts for minutes if the converter
 * never signalled completion. A timeout is counted per instance and shown by "adcaccess stats".
 */
struct AnalogInputConverter
{
    // PLAT-12: converters of the derivative (generated mcu/derivativeFacts.h), not a fixed 2
    static constexpr size_t INSTANCE_COUNT = NX_ADC_INSTANCE_COUNT;

    /// Timeouts of the polling loop, per instance. Written inside the conversion, read by the
    /// console; a lost increment between the task path and a preempting exclusive conversion of
    /// the same instance would only lose one count of a statistic.
    uint32_t pollTimeouts[INSTANCE_COUNT]    = {};
    uint32_t restartTimeouts[INSTANCE_COUNT] = {};

    static tAdc12Bit* adcOf(size_t const instance)
    {
        return (instance == ADC_0_CH) ? &AnalogInput::fAdc0
#if NX_ADC_INSTANCE_COUNT > 1
               : (instance == ADC_1_CH) ? &AnalogInput::fAdc1
#endif
                                        : nullptr;
    }

    bsp::BspReturnCode convert(size_t const instance, uint8_t const input, uint16_t& value)
    {
        tAdc12Bit* const adc = adcOf(instance);
        if ((adc == nullptr) || (!adc->fIsInitialized) || (!adc->fIsStarted))
        {
            return bsp::BSP_ERROR;
        }
        ADC_Type& reg = adc->fAdc;
        if (((reg.SC2 & ADC_SC2_ADTRG_MASK) != 0U) || ((reg.SC2 & ADC_SC2_DMAEN_MASK) != 0U))
        {
            return bsp::BSP_ERROR; // not a software triggered, polled converter
        }
        uint32_t const stale = reg.R[0]; // a pending COCO is cleared by reading R[0]
        static_cast<void>(stale);
        reg.SC1[0]     = ADC_SC1A_ADCH(input); // software trigger, aborts a running conversion
        uint32_t polls = 0U;
        while ((reg.SC1[0] & ADC_SC1A_COCO_MASK) == 0U)
        {
            ++polls;
            if (polls > adcTiming::POLL_LIMIT)
            {
                ++pollTimeouts[instance];
                return bsp::BSP_ERROR;
            }
        }
        value = adc->fAdcInResolution(reg.R[0]);
        return bsp::BSP_OK;
    }

    /**
     * Called with interrupts suspended after a conversion that interrupted a task conversion of
     * the same instance: writing SC1[0] starts the preempted input again; waiting for COCO without
     * reading R[0] leaves its result for the preempted conversion, whether it still polls COCO
     * or is about to read R[0].
     */
    void restart(size_t const instance, uint8_t const input)
    {
        tAdc12Bit* const adc = adcOf(instance);
        if ((adc == nullptr) || (!adc->fIsInitialized) || (!adc->fIsStarted))
        {
            return;
        }
        adc->fAdc.SC1[0] = ADC_SC1A_ADCH(input);
        uint32_t polls   = 0U;
        while ((adc->fAdc.SC1[0] & ADC_SC1A_COCO_MASK) == 0U)
        {
            ++polls;
            if (polls > adcTiming::POLL_LIMIT)
            {
                ++restartTimeouts[instance];
                return;
            }
        }
    }
};

namespace
{
AnalogInputConverter adcConverter;

#if NX_ADC_ACCESS_USE_RTOS_MUTEX
// Slow converters: FreeRTOS mutex with priority inheritance, maximum wait from the project.
using AdcAccessLock
    = ::adc::FreeRtosAdcLock<AnalogInputConverter::INSTANCE_COUNT, CortexMInterruptContext>;
AdcAccessLock adcAccessLock(pdMS_TO_TICKS(NX_MODULE_BSPADCACCESS_PARAM_LOCK_TIMEOUT_MS));
char const* const adcAccessPolicy = "FreeRTOS mutex, priority inheritance";
#else
// Short conversions (bsp/adc/AdcConversionTiming.h): suspend the interrupts for one conversion.
using AdcAccessLock = ::adc::InterruptLockAdcLock;
AdcAccessLock adcAccessLock;
char const* const adcAccessPolicy = "interrupt lock per conversion";
#endif

::adc::AdcAccess<AdcAccessLock, AnalogInputConverter, AnalogInputConverter::INSTANCE_COUNT>
    adcAccess(adcAccessLock, adcConverter);

#if NX_ADC_AVERAGED_CHANNEL_COUNT > 0
// IO-31: every sample is one serialized conversion of adcAccess; the time source and the exclusive section of
// the statistics are those of the lock policy.
::adc::AdcAveraging<AdcAccessLock> adcAveraging(adcAccessLock);
#endif
} // namespace

AnalogInput::AnalogInput() { fADChannelCfg = &ADChannelCfg[0]; }

void AnalogInput::init()
{
#if NX_ADC_ACCESS_USE_RTOS_MUTEX
    adcAccessLock.init();
#endif
    AnalogInputScale::init(
        static_cast<uint8_t>(
            sizeof(analogInputScaleConfiguration) / sizeof(AnalogInputScale::scale)),
        &analogInputScaleConfiguration[0]);

    SIM->ADCOPT  = 0U;
#ifdef SIM_CHIPCTL_PDB_BB_SEL_MASK
    // PDB_BB_SEL (back-to-back PDB to ADC) exists only on derivatives whose device header has the field (S32K14x);
    // on the S32K11x bit 13 of SIM_CHIPCTL is reserved (PLAT-48)
    SIM->CHIPCTL = SIM->CHIPCTL & ~SIM_CHIPCTL_PDB_BB_SEL_MASK;
#endif

    // Only calibrate instances that have channels in board.yaml
    if (ADC0_CHANNEL_COUNT > 0U)
    {
        (void)fAdc0.init();
    }
#if NX_ADC_INSTANCE_COUNT > 1
    if (ADC1_CHANNEL_COUNT > 0U)
    {
        (void)fAdc1.init();
    }
#endif

    for (uint16_t i = 0; i < static_cast<uint16_t>(_AdcChannelMax); ++i)
    {
        if (fADChannelCfg[i].adc == ADC_0_CH)
        {
            (void)Io::setDefaultConfiguration(fADChannelCfg[i].pin);
            fAdc0.enableChannel(fADChannelCfg[i].slot, fADChannelCfg[i].phChannel);
        }
#if NX_ADC_INSTANCE_COUNT > 1
        else if (fADChannelCfg[i].adc == ADC_1_CH)
        {
            (void)Io::setDefaultConfiguration(fADChannelCfg[i].pin);
            fAdc1.enableChannel(fADChannelCfg[i].slot, fADChannelCfg[i].phChannel);
        }
#endif
        else
        {
            // nothing to do
        }
    }
}

void AnalogInput::start()
{
    if (ADC0_CHANNEL_COUNT > 0U)
    {
        (void)fAdc0.start();
    }
#if NX_ADC_INSTANCE_COUNT > 1
    if (ADC1_CHANNEL_COUNT > 0U)
    {
        (void)fAdc1.start();
    }
#endif
}

bsp::BspReturnCode AnalogInput::get(uint16_t channel, uint16_t& value)
{
    if (channel >= static_cast<uint16_t>(_AdcChannelMax))
    {
        return bsp::BSP_ERROR;
    }
#if NX_ADC_AVERAGED_CHANNEL_COUNT > 0
    tAnalogConfiguration const& cfg = fADChannelCfg[channel];
    return adcAveraging.read(
        analogInputSamplingConfiguration[channel],
        [&cfg](uint16_t& raw) { return adcAccess.convert(cfg.adc, cfg.phChannel, raw); },
        value);
#else
    return adcAccess.convert(
        fADChannelCfg[channel].adc, fADChannelCfg[channel].phChannel, value);
#endif
}

bsp::BspReturnCode AnalogInput::get(uint16_t const channel, uint32_t& value)
{
    uint16_t value16             = 0U;
    bsp::BspReturnCode const ret = get(channel, value16);
    value                        = static_cast<uint32_t>(value16);
    return ret;
}

void AnalogInput::cyclic() {}

void AnalogInput::stop()
{
    (void)fAdc0.stop();
#if NX_ADC_INSTANCE_COUNT > 1
    if (ADC1_CHANNEL_COUNT > 0U)
    {
        (void)fAdc1.stop();
    }
#endif
}

char const* AnalogInput::getName(uint16_t const channel)
{
    if (channel < sizeof(adcChannelsNamingStrings) / (sizeof(char*)))
    {
        return adcChannelsNamingStrings[channel];
    }
    else
    {
        return ("??? ->");
    }
}

using ::util::command::CommandContext;

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(AdcAccessCommand, "adcaccess", "Serialized ADC access")
COMMAND_GROUP_COMMAND(1U, "stats", "conversions, waits, maximum times")
COMMAND_GROUP_COMMAND(2U, "reset", "clear statistics")
DEFINE_COMMAND_GROUP_GET_INFO_END;

void AdcAccessCommand::executeCommand(CommandContext& context, uint8_t const idx)
{
    // SharedStringWriter::printf is variadic by design.
    // NOLINTBEGIN(cppcoreguidelines-pro-type-vararg)
    ::util::format::SharedStringWriter out(context);
    switch (idx)
    {
        case 1: // "stats"
        {
            ::adc::AdcAccessStatistics const s = adcAccess.getStatistics();
            (void)out.printf("policy: %s\r\n", adcAccessPolicy);
            (void)out.printf(
                "conversions %u, exclusive %u, nested %u, contended %u, lock timeouts %u, "
                "errors %u\r\n",
                s.conversions,
                s.exclusive,
                s.nested,
                s.contended,
                s.lockTimeouts,
                s.errors);
            (void)out.printf(
                "max wait %u ns, max conversion (task) %u ns, max exclusive %u ns\r\n",
                s.maxWaitNs,
                s.maxConversionNs,
                s.maxExclusiveNs);
#if NX_ADC_INSTANCE_COUNT > 1
            (void)out.printf(
                "poll timeouts %u/%u, restart timeouts %u/%u (ADC0/ADC1)\r\n",
                adcConverter.pollTimeouts[ADC_0_CH],
                adcConverter.pollTimeouts[ADC_1_CH],
                adcConverter.restartTimeouts[ADC_0_CH],
                adcConverter.restartTimeouts[ADC_1_CH]);
#else
            (void)out.printf(
                "poll timeouts %u, restart timeouts %u (ADC0)\r\n",
                adcConverter.pollTimeouts[ADC_0_CH],
                adcConverter.restartTimeouts[ADC_0_CH]);
#endif
            (void)out.printf(
                "conversion %u ns (%u ADC clocks at %u Hz), poll limit %u (factor %u)\r\n",
                adcTiming::CONVERSION_NS,
                adcTiming::CONVERSION_CLOCKS,
                adcTiming::ADC_CLOCK_HZ,
                adcTiming::POLL_LIMIT,
                adcTiming::POLL_SAFETY_FACTOR);
            // Project parameters behind the numbers above (modules.bspAdcAccess, IO-19): what the sample phase
            // and the averaging of the converter are set to, how long a conversion may suspend the interrupts
            // and how long a waiting task may wait (the latter only with the RTOS mutex).
            (void)out.printf(
                "config: sample %u + hold %u + compare %u + adder %u ADC clocks, %u average(s), divider %u\r\n",
                NX_ADC_SAMPLE_CLOCKS,
                NX_ADC_HOLD_CLOCKS,
                NX_ADC_COMPARE_CLOCKS,
                NX_ADC_ADDER_CLOCKS,
                NX_ADC_AVERAGES,
                NX_ADC_CLOCK_DIVIDER);
            (void)out.printf(
                "limits: interrupt suspend <= %u ns, lock timeout %u ms (%s)\r\n",
                NX_ADC_MAX_INTERRUPT_SUSPEND_NS,
                NX_MODULE_BSPADCACCESS_PARAM_LOCK_TIMEOUT_MS,
                NX_ADC_ACCESS_USE_RTOS_MUTEX ? "used" : "unused with this policy");
#if NX_ADC_AVERAGED_CHANNEL_COUNT > 0
            // IO-31: reads of the channels averaged over one PWM period (project adc_sampling)
            {
                ::adc::AdcAveragingStatistics const a = adcAveraging.getStatistics();
                (void)out.printf(
                    "averaged: %u channel(s), reads %u, late %u, errors %u, max window %u ns, max late %u ns\r\n",
                    NX_ADC_AVERAGED_CHANNEL_COUNT,
                    a.reads,
                    a.late,
                    a.errors,
                    a.maxWindowNs,
                    a.maxLateNs);
            }
#endif
            break;
        }
        case 2: // "reset"
        {
            adcAccess.resetStatistics();
#if NX_ADC_AVERAGED_CHANNEL_COUNT > 0
            adcAveraging.resetStatistics();
#endif
            for (size_t i = 0U; i < AnalogInputConverter::INSTANCE_COUNT; ++i)
            {
                adcConverter.pollTimeouts[i]    = 0U;
                adcConverter.restartTimeouts[i] = 0U;
            }
            (void)out.printf("adcaccess statistics cleared\r\n");
            break;
        }
        default:
        {
            break;
        }
    }
    // NOLINTEND(cppcoreguidelines-pro-type-vararg)
}

} // namespace bios
