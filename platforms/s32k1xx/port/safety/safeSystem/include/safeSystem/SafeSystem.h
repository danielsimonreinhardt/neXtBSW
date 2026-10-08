/********************************************************************************
 * Copyright (c) 2025 Accenture
 *
 * This program and the accompanying materials are made available under the
 * terms of the Apache License Version 2.0 which is available at
 * https://www.apache.org/licenses/LICENSE-2.0
 *
 * SPDX-License-Identifier: Apache-2.0
 ********************************************************************************/

#pragma once

#include <cstdint>

// Project parameters of this module (module.yaml, project.yaml modules.safeSystem, IO-19)
#include <integration/safeSystemConfiguration.h>

namespace safety
{
class SafeSystem
{
    // [METHODS_START]
public:
    /**
     * Diagnostic counters of the ADC readings (IO-16). A failed reading is counted, it does not
     * enter the safe state on its own: see checkAdcReference.
     */
    struct AdcDiagnostics
    {
        uint32_t readErrors;           ///< failed AnalogInput::get calls of both channels
        uint32_t consecutiveRefErrors; ///< failed readings of the bandgap in a row
        uint32_t skippedRefChecks;     ///< reference checks left out because no value was read
        uint32_t skippedSupplyChecks;  ///< supply checks left out for the same reason
        uint32_t refVoltageMv;         ///< last valid reference voltage
        bool refVoltageValid;          ///< false until the first successful reading
        uint32_t consecutiveSupplyErrors; ///< failed readings of the supply channel in a row (SYS-41)
    };

    /**
     * Context passed to the monitors of the SafeSupervisor, so the recorded event says what
     * happened (SafeSupervisor::Event has one event for both causes).
     */
    enum AdcErrorContext : uint32_t
    {
        ADC_CONTEXT_OUT_OF_RANGE = 1U, ///< a reading succeeded, the value is out of range
        ADC_CONTEXT_READ_FAILURE = 2U, ///< no reading succeeded for ADC_REF_ERROR_LIMIT cycles
        /// SYS-95 (DFA-3): the bandgap and the internal supply channel do not stand in the ratio their voltages
        /// demand - one code for two different channels (a stuck converter) or a supply result that did not follow
        /// the supply multiplexer (a converter that stopped converting); ADC_REFERENCE_ERROR only
        ADC_CONTEXT_CONVERTER_STUCK = 3U,
    };

    /**
     * Failed readings of the bandgap in a row before the reference monitor is triggered, from the
     * project (modules.safeSystem.adc_read_error_limit, IO-19). It counts cycles of the safety
     * task, so the time it corresponds to follows from that task's period (10 ms here: 100 ms
     * without a usable reading).
     *
     * SYS-41 (finding F14 of doc/safety/fmea.md, SM-20): the same limit applies to the internal
     * supply channel - ADC_REF_ERROR_LIMIT failed readings of it in a row trigger the supplies
     * monitor once with ADC_CONTEXT_READ_FAILURE. Same channel type, same converter, same reason
     * (a transient must not reset the ECU, a permanent failure must not end the supervision
     * silently), so no second parameter.
     */
    static constexpr uint32_t ADC_REF_ERROR_LIMIT = NX_MODULE_SAFESYSTEM_PARAM_ADC_READ_ERROR_LIMIT;

    /**
     * Initializes the SafeSystem instance, setting up necessary variables and states.
     */
    SafeSystem();

    /**
     * This method prepares the system for voltage monitoring. It should be called once
     * during system initialization, typically from the safetyManager's init function.
     */
    void init();

    /**
     * This method checks the ADC reference voltage and internal supply voltages
     * to ensure they are within safe operating ranges. It should be called cyclically, typically
     * from the safetyManager's cyclic function.
     */
    void cyclic();

    /**
     * Diagnostic counters of the ADC readings, for the console and for a diagnostic service.
     */
    AdcDiagnostics getAdcDiagnostics() const;

    /**
     * Clears the diagnostic counters (console "safeadc reset"). The series of failures in progress, the last
     * reference voltage and its validity stay: they are state of the supervision, not statistics.
     */
    void resetAdcDiagnostics();

private:
    /**
     * Compares the raw ADC value with predefined thresholds to ensure the ADC reference voltage
     * remains within acceptable limits. If is outside the range, it will trigger
     * adcReferenceMonitor to take appropriate action via safetySupervisor.
     *
     * A reading that failed (valid == false, or a raw value of 0, which the bandgap can never
     * deliver) is counted and the check is left out; the last valid reference voltage is kept.
     * A single failure must not enter the safe state: SafeSupervisor::handle() answers
     * ADC_REFERENCE_ERROR with limp home and a software reset, while a failed
     * AnalogInput::get is not evidence of a wrong reference voltage. It can be a transient of
     * the serialized access itself (lock timeout of the RTOS mutex variant, polling timeout of
     * the converter, a conversion aborted by another caller). Rebooting the ECU for one such
     * transient would trade a diagnosis for an availability loss. Only readings that keep
     * failing (ADC_REF_ERROR_LIMIT in a row) mean that the reference can no longer be
     * monitored at all, which is itself safety relevant: then the monitor is triggered with the
     * context ADC_CONTEXT_READ_FAILURE.
     */
    void checkAdcReference(bool valid, uint32_t bandgapRawValue);

    /**
     * Ensures that critical internal supply voltages such as 3.3V Flash, 3.3V Oscillator,
     * and 1.2V Core are within safe operating ranges. If any voltage is outside the range, it will
     * trigger internalSuppliesMonitor to take appropriate action via safetySupervisor.
     *
     * The supply multiplexer is stepped on in every cycle, whether the reading worked or not.
     * The comparison is left out while the reading failed or no valid reference voltage is known:
     * with a raw value of 0 it would always report an undervoltage.
     *
     * SYS-41 (F14): failed readings of the supply channel are counted in a row (every cycle reads
     * the channel); at ADC_REF_ERROR_LIMIT the supplies monitor is triggered once with
     * ADC_CONTEXT_READ_FAILURE - before, a permanently failing channel ended the supervision of
     * all three supplies without any event. A missing reference is not counted here: that is the
     * bandgap's own limit (checkAdcReference).
     */
    void checkInternalSupplies(bool valid, uint32_t internalSupplyRawValue);

    /**
     * SYS-95 (finding DFA-3 of doc/safety/dfa.md, DF-46): the two internal channels of one converter read in the same
     * cycle must lie on the expected side of each other. The bandgap (about 1.0 V) and the internal supply channel
     * share the converter, and the reference check alone cannot tell a converter that answers every channel with
     * the same plausible code from a good one - it would blind the reference check (SM-18/SM-19), the supply check
     * (SM-20) and every analogue safety input (SM-30) together. The supply channel is the second channel: it is read
     * in every cycle anyway and has a clearly different expected code. The comparison is a ratio of the two raw
     * codes, so it holds for every VREFH:
     * - while the multiplexer selects a 3.3 V supply (flash, oscillator), the supply code must be more than twice the
     *   bandgap code (healthy at least 3.1 V / 1.03 V = 3.0 within the bands of the two checks; one code for both
     *   channels is 1.0);
     * - while it selects the 1.2 V core supply, the supply code must be less than twice the bandgap code (healthy at
     *   most 1.4 V / 0.97 V = 1.44) - a converter that stopped converting keeps the previous 3.3 V result there.
     * Both readings must have succeeded (a failed one is the case of the read-error limits) and the selector must be
     * one of the three supplies; otherwise nothing is compared. A deviation triggers the reference monitor with
     * ADC_CONTEXT_CONVERTER_STUCK in that cycle (S1 + S2 like any reference error).
     */
    void checkConverterSeparation(
        bool bandgapOk, uint32_t bandgapRawValue, bool supplyOk, uint32_t supplyRawValue, uint32_t supplySelector);

    /**
     * Reads the raw ADC value representing the ADC reference voltage. Returns false if the
     * conversion failed; the value is then not meaningful.
     */
    bool getBandgapRawValue(uint32_t& value);

    /**
     * Reads the raw ADC value representing the internal supply voltage. Returns false if the
     * conversion failed.
     */
    bool getInternalSupplyRawValue(uint32_t& value);
    // [METHODS_END]

    uint32_t _counter;
    uint32_t _adcRef;
    bool _adcRefValid;
    uint32_t _adcReadErrors;
    uint32_t _consecutiveRefErrors;
    uint32_t _skippedRefChecks;
    uint32_t _skippedSupplyChecks;
    uint32_t _consecutiveSupplyErrors;
};

/**
 * The SafeSystem instance of this ECU. It is created next to the other safety objects (safeLifecycle
 * SafetyManager.cpp); without this accessor its diagnostic counters could not leave the safety task
 * (IO-19, console group "safeadc", SafeSystemCommand.h).
 */
SafeSystem& getSafeSystem();

} // namespace safety
