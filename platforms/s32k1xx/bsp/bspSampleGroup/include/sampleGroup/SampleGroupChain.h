// SPDX-License-Identifier: Apache-2.0
/* IO-62: sample group of the family s32k1xx - hardware trigger chain FTM trigger -> TRGMUX -> PDB -> ADC
 * (doc/architecture/fast-control-loop.md section 3.2, contract libs/bsp/bspSampleGroup sampleGroup/SampleGroup.h).
 *
 * One PDB per ADC (PDB0 -> ADC0, PDB1 -> ADC1). The trigger output of the FTM of the PWM group (FTMx_INIT_TRIG or
 * FTMx_EXT_TRIG, IO-61) reaches the trigger input of every PDB of the group through TRGMUX_PDBx SEL0; channel 0 of
 * the PDB raises pre-trigger 0 after the delay DLY0 and pre-trigger m > 0 back-to-back after the end of conversion
 * m - 1; pre-trigger m selects ADC SC1[m] (hardware trigger ADTRG), the result lands in R[m]. The last SC1 of the
 * sequencer that ends last has AIEN: its ADC interrupt is "group complete". No instruction is needed between the
 * PWM trigger and the last conversion.
 *
 * Header only, no dynamic state beyond the result buffer. The register values come from the generated
 * bsp/sampleGroup/sampleGroupConfiguration.h (tools/bswgen, plan checked by tools/nxconfig/samplegroup.py). The ADC
 * instances of a group are exclusive to it (the trigger mode SC2[ADTRG] is one bit per converter); the software
 * converter of the port (AnalogInput) does not initialise them, init() here does, with the converter settings of
 * the project (modules.bspAdcAccess: CFG1, CFG2, SC3) and a calibration.
 *
 * Order of the steps (the chain must never see a trigger half configured):
 *   init()    SIM_ADCOPT: trigger and pre-trigger of the used ADCs from the PDB; PDB off; per ADC: clock, sample
 *             time, calibration (bounded), averaging, the SC1 slots, then SC2 = hardware trigger
 *   arm()     per PDB: SC with PDBEN, MOD, IDLY, CH0 C1 (EN, TOS, BB), DLY0, then LDOK; the TRGMUX route LAST
 *   disarm()  TRGMUX routes FIRST (trigger disabled), then every PDB off (which also releases its pre-trigger locks)
 *   onComplete()  in the ADC interrupt of the completing sequencer: PWM counter, the other converters' last
 *             conversion (bounded wait), every R[m] in project order (reading clears COCO, which the PDB needs for the
 *             next trigger), PDB sequence errors (cleared; the PDBs are restarted), consumer call
 *
 * Register facts ([RM13] S32K1xx Series Reference Manual Rev. 13): SIM_ADCOPT 11.3.1.5 (S. 188-189: ADCxTRGSEL 0 =
 * PDB, ADCxPRETRGSEL 00 = PDB pre-trigger, ADCxSWPRETRG 000 = off); ADC 43.7.1 (S. 1199), 44 (SC1 ADCH/AIEN/COCO,
 * SC2 ADTRG, SC3 CAL); PDB 46.3.1 SC (S. 1324-1327: PDBEN, TRGSEL 0 = trigger-in 0, LDOK only with PDBEN), 46.3.5
 * CH0C1 (S. 1329), 46.3.6 CH0S ERR (write 0 to clear), 46.4.1 (S. 1337-1338: lock per pre-trigger released by the
 * COCO rising edge or by disabling the PDB); TRGMUX 19.4 (S. 424: SELn 7 bit at 8 * n, no LK here).
 */
#pragma once

#include "mcu/mcu.h"
#include "sampleGroup/SampleGroup.h"

#include <cstdint>

namespace bios
{
namespace sampleGroup
{
constexpr uint8_t MAX_SLOTS       = 8U;  // pre-triggers of PDB channel 0 ([RM13] Table 46-1)
constexpr uint8_t MAX_CONVERTERS  = 2U;  // ADC0/PDB0, ADC1/PDB1 of the S32K14x
constexpr uint32_t TRGMUX_SEL_MAX = 0x7FU;
constexpr uint8_t FIRST_PDB_INDEX = 14U; // TRGMUX_PDB0_INDEX, TRGMUX_PDB1_INDEX = 15 (device header)

/** One PDB and its ADC. */
struct SequencerConfig
{
    uint8_t converter;       // ADC and PDB number (0: ADC0/PDB0)
    uint8_t trgmuxIndex;     // TRGMUX register of the PDB trigger input
    uint32_t trgmuxSelMask;  // SEL0 field: the only trigger input of a PDB ([RM13] Figure 19-2)
    uint32_t trgmuxSel;      // code of the trigger output of the FTM in that field
    uint32_t pdbSc;          // PDBEN, TRGSEL 0 (trigger-in 0 = TRGMUX), one-shot, no prescaler; without LDOK
    uint32_t pdbMod;
    uint32_t pdbIdly;
    uint32_t pdbCh0C1;       // EN and TOS of every used pre-trigger, BB of all but the first
    uint32_t pdbDly0;        // delay of pre-trigger 0 in PDB clocks
    uint32_t simAdcoptMask;  // ADCxTRGSEL, ADCxSWPRETRG, ADCxPRETRGSEL of this ADC: all 0 = PDB
    uint8_t slots;           // conversions per trigger
    uint32_t sc1[MAX_SLOTS]; // ADCH per slot, AIEN on the last slot of the completing sequencer
};

/** Where the value of one project channel comes from. */
struct Member
{
    uint8_t sequencer; // index into GroupConfig::sequencers
    uint8_t slot;      // SC1[slot] / R[slot]
};

template<uint8_t SEQUENCERS, uint8_t MEMBERS>
struct GroupConfig
{
    SequencerConfig sequencers[SEQUENCERS];
    Member members[MEMBERS];      // project order
    uint8_t completing;           // sequencer whose last conversion completes the group
    uint32_t adcCfg1;             // converter settings of the project (bsp/adc/adcAccessConfiguration.h)
    uint32_t adcCfg2;
    uint32_t adcSc3;
    uint32_t calibrationPollLimit;
    uint32_t missingPollLimit;    // bounded wait for another converter at group complete
};

/** Register blocks the chain works on (the real ones in the generated configuration, models in the host test). */
struct Registers
{
    ADC_Type* adc[MAX_CONVERTERS];
    PDB_Type* pdb[MAX_CONVERTERS];
    TRGMUX_Type* trgmux;
    SIM_Type* sim;
    FTM_Type* ftm; // timer of the PWM group: its counter is the time base of the result
};

/** Structure of a configuration: what the generator must never produce (also a static_assert of the generat). */
template<uint8_t SEQUENCERS, uint8_t MEMBERS>
constexpr bool isValid(GroupConfig<SEQUENCERS, MEMBERS> const& c)
{
    if ((SEQUENCERS == 0U) || (SEQUENCERS > MAX_CONVERTERS) || (c.completing >= SEQUENCERS)
        || (c.calibrationPollLimit == 0U) || (c.missingPollLimit == 0U))
    {
        return false;
    }
    uint32_t usedConverters = 0U;
    uint32_t members        = 0U;
    for (uint8_t s = 0U; s < SEQUENCERS; ++s)
    {
        SequencerConfig const& q = c.sequencers[s];
        if ((q.converter >= MAX_CONVERTERS) || ((usedConverters & (1UL << q.converter)) != 0U) || (q.slots == 0U)
            || (q.slots > MAX_SLOTS) || (q.trgmuxIndex != (FIRST_PDB_INDEX + q.converter)))
        {
            return false;
        }
        usedConverters |= 1UL << q.converter;
        uint32_t const used = (1UL << q.slots) - 1U;
        uint32_t const c1   = PDB_C1_EN(used) | PDB_C1_TOS(used) | PDB_C1_BB(used & ~1UL);
        if ((q.pdbCh0C1 != c1) || ((q.pdbSc & PDB_SC_PDBEN_MASK) == 0U)
            || ((q.pdbSc & (PDB_SC_TRGSEL_MASK | PDB_SC_CONT_MASK | PDB_SC_LDOK_MASK | PDB_SC_DMAEN_MASK)) != 0U)
            || (q.pdbDly0 > q.pdbMod) || (q.trgmuxSelMask != TRGMUX_SEL_MAX) || (q.trgmuxSel == 0U)
            || (q.trgmuxSel > TRGMUX_SEL_MAX))
        {
            return false;
        }
        for (uint8_t m = 0U; m < MAX_SLOTS; ++m)
        {
            bool const last = (s == c.completing) && (m == (q.slots - 1U));
            if ((m < q.slots) && (((q.sc1[m] & ADC_SC1_AIEN_MASK) != 0U) != last))
            {
                return false; // exactly one interrupt: the last conversion of the completing sequencer
            }
        }
    }
    for (uint8_t i = 0U; i < MEMBERS; ++i)
    {
        Member const& m = c.members[i];
        if ((m.sequencer >= SEQUENCERS) || (m.slot >= c.sequencers[m.sequencer].slots))
        {
            return false;
        }
        uint32_t const bit = 1UL << ((8U * m.sequencer) + m.slot);
        if ((members & bit) != 0U)
        {
            return false;
        }
        members |= bit;
    }
    uint32_t slots = 0U;
    for (uint8_t s = 0U; s < SEQUENCERS; ++s)
    {
        slots += c.sequencers[s].slots;
    }
    return slots == MEMBERS; // every slot is a member, no member twice
}

template<uint8_t SEQUENCERS, uint8_t MEMBERS, typename Consumer = ::bsp::sampleGroup::NoConsumer>
class SampleGroupChain
{
public:
    using Config = GroupConfig<SEQUENCERS, MEMBERS>;

    SampleGroupChain(Config const& config, Registers const& registers) : _cfg(config), _reg(registers) {}

    /** Converters and sequencers configured, calibrated, nothing routed. False if a calibration never ended. */
    bool init()
    {
        _armed    = false;
        bool ok   = true;
        uint32_t mask = 0U;
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            mask |= _cfg.sequencers[s].simAdcoptMask;
        }
        _reg.sim->ADCOPT = _reg.sim->ADCOPT & ~mask; // trigger and pre-triggers of these ADCs from their PDB
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            SequencerConfig const& q = _cfg.sequencers[s];
            _reg.pdb[q.converter]->SC = 0U;
            ok = initConverter(*_reg.adc[q.converter], q) && ok;
        }
        _initialised = ok;
        return ok;
    }

    /** The chain follows the triggers of the PWM group from the next one on. */
    bool arm()
    {
        if (!_initialised)
        {
            return false;
        }
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            SequencerConfig const& q = _cfg.sequencers[s];
            PDB_Type& pdb            = *_reg.pdb[q.converter];
            pdb.CH[0].S              = 0U;
            pdb.SC                   = q.pdbSc;
            pdb.MOD                  = q.pdbMod;
            pdb.IDLY                 = q.pdbIdly;
            pdb.CH[0].C1             = q.pdbCh0C1;
            pdb.CH[0].DLY[0]         = q.pdbDly0;
            pdb.SC                   = q.pdbSc | PDB_SC_LDOK_MASK; // buffered MOD, IDLY, DLY take effect
        }
        _book.armed();
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            route(_cfg.sequencers[s], true);
        }
        _armed = true;
        return true;
    }

    /** The chain ignores the triggers; a conversion under way ends normally. The PWM group is not touched. */
    void disarm()
    {
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            route(_cfg.sequencers[s], false);
        }
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            _reg.pdb[_cfg.sequencers[s].converter]->SC = 0U;
        }
        _armed = false;
    }

    /** Group complete: to be called from the interrupt of the completing ADC (vector in the generated config). */
    void onComplete()
    {
        uint32_t const ticks = _reg.ftm->CNT;
        uint8_t errors       = ::bsp::sampleGroup::ERROR_NONE;
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            SequencerConfig const& q = _cfg.sequencers[s];
            if ((s != _cfg.completing) && !lastConverted(*_reg.adc[q.converter], q))
            {
                errors = static_cast<uint8_t>(errors | ::bsp::sampleGroup::ERROR_CONVERSION_MISSING);
            }
        }
        for (uint8_t i = 0U; i < MEMBERS; ++i)
        {
            Member const& m = _cfg.members[i];
            _raw[i]         = static_cast<uint16_t>(
                _reg.adc[_cfg.sequencers[m.sequencer].converter]->R[m.slot] & 0xFFFFU);
        }
        bool sequenceError = false;
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            PDB_Type& pdb = *_reg.pdb[_cfg.sequencers[s].converter];
            if ((pdb.CH[0].S & PDB_S_ERR_MASK) != 0U)
            {
                sequenceError = true;
            }
            pdb.CH[0].S = 0U; // ERR and CF are cleared by writing 0
        }
        if (sequenceError)
        {
            errors = static_cast<uint8_t>(errors | ::bsp::sampleGroup::ERROR_SEQUENCE);
            restartSequencers();
        }
        uint32_t const sequence = _book.complete(errors);
        ::bsp::sampleGroup::Result const result{_raw, MEMBERS, errors, sequence, ticks};
        Consumer::complete(result);
    }

    bool isArmed() const { return _armed; }

    ::bsp::sampleGroup::Statistics const& statistics() const { return _book.statistics(); }

    ::bsp::sampleGroup::Bookkeeping& bookkeeping() { return _book; }

private:
    bool initConverter(ADC_Type& adc, SequencerConfig const& q)
    {
        adc.SC2  = 0U; // software trigger while calibrating
        adc.CFG1 = _cfg.adcCfg1;
        adc.CFG2 = _cfg.adcCfg2;
        for (uint8_t m = 0U; m < MAX_SLOTS; ++m)
        {
            adc.SC1[m] = ADC_SC1_ADCH_MASK; // module disabled in every slot
        }
        adc.SC3 = ADC_SC3_CAL_MASK | ADC_SC3_AVGE_MASK | ADC_SC3_AVGS(3U); // calibration with 32 samples
        uint32_t n = 0U;
        while ((adc.SC1[0] & ADC_SC1_COCO_MASK) == 0U)
        {
            if (++n >= _cfg.calibrationPollLimit)
            {
                return false;
            }
        }
        static_cast<void>(static_cast<uint32_t>(adc.R[0])); // reading R clears COCO
        adc.SC3 = _cfg.adcSc3;
        for (uint8_t m = 0U; m < q.slots; ++m)
        {
            adc.SC1[m] = q.sc1[m];
        }
        adc.SC2 = ADC_SC2_ADTRG_MASK; // hardware trigger: the pre-triggers of the PDB select the slot
        return true;
    }

    void route(SequencerConfig const& q, bool const on)
    {
        uint32_t const reg                  = _reg.trgmux->TRGMUXn[q.trgmuxIndex];
        _reg.trgmux->TRGMUXn[q.trgmuxIndex] = (reg & ~q.trgmuxSelMask) | (on ? q.trgmuxSel : 0U);
    }

    bool lastConverted(ADC_Type const& adc, SequencerConfig const& q) const
    {
        uint32_t n = 0U;
        while ((adc.SC1[q.slots - 1U] & ADC_SC1_COCO_MASK) == 0U)
        {
            if (++n >= _cfg.missingPollLimit)
            {
                return false;
            }
        }
        return true;
    }

    /** After a sequence error the pre-trigger locks may hold: disabling a PDB releases them ([RM13] 46.4.1). */
    void restartSequencers()
    {
        for (uint8_t s = 0U; s < SEQUENCERS; ++s)
        {
            SequencerConfig const& q = _cfg.sequencers[s];
            PDB_Type& pdb            = *_reg.pdb[q.converter];
            pdb.SC                   = 0U;
            pdb.SC                   = q.pdbSc;
            pdb.SC                   = q.pdbSc | PDB_SC_LDOK_MASK;
        }
        _book.recovered();
    }

    Config const& _cfg;
    Registers _reg;
    ::bsp::sampleGroup::Bookkeeping _book;
    uint16_t _raw[MEMBERS] = {};
    bool _initialised      = false;
    bool _armed            = false;
};

} // namespace sampleGroup
} // namespace bios
