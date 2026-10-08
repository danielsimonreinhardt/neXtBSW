// SPDX-License-Identifier: Apache-2.0
// Host test of the sample group of the family s32k1xx (IO-62): sampleGroup/SampleGroupChain.h with the configuration
// bswgen generates for tools/bswgen/test/profiles/projects/testproject_sample_group (bridge on FTM3 center aligned,
// trigger at zero_low = channel match -> FTM3_EXT_TRIG; MOT_SAMPLE = PHASE_I_U (ADC0 SE4), PHASE_I_V (ADC1 SE2),
// PHASE_I_W (ADC1 SE3), DC_LINK_U (ADC0 SE10), 500 ns after the trigger point). Register models of ADC0/1, PDB0/1,
// TRGMUX, SIM and FTM3 with the vendor bit macros (test/host/stubs/mcu/mcu.h).
//
// The chain model below (fireTrigger) evaluates the registers the way the reference manual describes the hardware -
// independent of the generator and of the family code - and is the counter-check that the generated values and the
// family code together convert the right channels at the right instants and raise one interrupt at the end:
//   [RM13] 19.4 / Figure 19-2: TRGMUX_PDBx (index 14 + x) SEL0 selects the trigger input of PDBx; FTM3_EXT_TRIG = 0x1D
//   [RM13] 46.4.1: PDBEN, TRGSEL 0 (trigger-in 0); pre-trigger 0 (TOS) at DLY0 + 2 clocks after the trigger;
//          pre-trigger m > 0 with BB 2 clocks after the end of conversion m - 1; a pre-trigger whose lock is still
//          active (no COCO rising edge since the last conversion of that slot) sets ERR[m] and converts nothing
//   [RM13] 43.7.1 / 11.3.1.5: ADCx takes trigger and pre-triggers from PDBx only with SIM_ADCOPT fields of x all 0;
//          ADC SC2[ADTRG] = 1: pre-trigger m converts SC1[m] into R[m]; AIEN of SC1[m] raises the ADC interrupt
// Hand values: PDB clock 80 MHz = 12.5 ns; conversion 1709 ns (tools/nxconfig/adc.py for the bspAdcAccess
// defaults at 48 MHz / 40 MHz bus); first sample (40 + 2) * 12.5 = 525 ns; second 525 + 1709 + 25 = 2259 ns.
// Build and run: tools/bswgen/test_bswgen_samplegroup.py (with mutants).

#include "bsp/sampleGroup/sampleGroupConfiguration.h"

#include <cstdio>
#include <vector>

NxTestAdc fakeAdc[2]   = {};
NxTestPdb fakePdb[2]   = {};
NxTestTrgmux fakeTrgmux = {};
SIM_Type fakeSim       = {};
FTM_Type fakeFtm3      = {};

namespace
{
std::vector<NxTestEvent> events;
} // namespace

void nxTestLog(NxTestWhat const what, uint8_t const index, uint32_t const value)
{
    events.push_back(NxTestEvent{what, index, value});
}

uint32_t nxTestSc1Reads = 0U;

NxTestAdcSc1::operator uint32_t() const
{
    ++nxTestSc1Reads;
    return value;
}

NxTestAdcSc3& NxTestAdcSc3::operator=(uint32_t const written)
{
    value = written;
    if ((written & ADC_SC3_CAL_MASK) != 0U)
    {
        // the calibration ends at once: COCO of SC1[0], CAL clears itself
        for (NxTestAdc& adc : fakeAdc)
        {
            if (&adc.SC3 == this)
            {
                adc.SC1[0] |= ADC_SC1_COCO_MASK;
                ++adc.calibrations;
            }
        }
        value = written & ~ADC_SC3_CAL_MASK;
    }
    return *this;
}

NxTestAdcResult::operator uint32_t() const
{
    for (NxTestAdc& adc : fakeAdc)
    {
        for (uint32_t m = 0U; m < ADC_R_COUNT; ++m)
        {
            if (&adc.R[m] == this)
            {
                adc.SC1[m] &= ~ADC_SC1_COCO_MASK;
            }
        }
    }
    return value;
}

NxTestPdbSc& NxTestPdbSc::operator=(uint32_t const written)
{
    for (uint8_t i = 0U; i < 2U; ++i)
    {
        NxTestPdb& pdb = fakePdb[i];
        if (&pdb.SC == this)
        {
            nxTestLog(NX_PDB_SC_WRITE, i, written);
            if ((written & PDB_SC_PDBEN_MASK) == 0U)
            {
                pdb.loaded = false; // disabled: counter off, locks released, buffers not in effect
            }
            else if ((written & PDB_SC_LDOK_MASK) != 0U)
            {
                pdb.loaded     = true;
                pdb.loadedMod  = pdb.MOD;
                pdb.loadedIdly = pdb.IDLY;
                for (uint32_t m = 0U; m < PDB_DLY_COUNT; ++m)
                {
                    pdb.loadedDly[m] = pdb.CH[0].DLY[m];
                }
            }
        }
    }
    value = written & ~PDB_SC_LDOK_MASK; // LDOK reads 0 once loaded
    return *this;
}

NxTestTrgmuxReg& NxTestTrgmuxReg::operator=(uint32_t const written)
{
    value = written;
    for (uint8_t i = 0U; i < TRGMUX_TRGMUXn_COUNT; ++i)
    {
        if (&fakeTrgmux.TRGMUXn[i] == this)
        {
            nxTestLog(NX_TRGMUX_WRITE, i, written);
        }
    }
    return *this;
}

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

namespace cfg = bios::sampleGroupConfig::MOT_SAMPLE;

// ---------------------------------------------------------------------------------------------- chain model
constexpr uint32_t PDB_CLOCK_PS      = 12500U;   // 80 MHz SYS_CLK
constexpr uint32_t CONVERSION_PS     = 1709000U; // hand value (see header)
constexpr uint8_t FTM3_EXT_TRIG      = 0x1DU;    // [RM13] Table 19-1
constexpr uint8_t FTM0_EXT_TRIG      = 0x17U;
constexpr uint32_t SIM_ADC_FIELDS[2] = {0x3FU, 0x3F00U};

uint16_t analog[2][64]; // raw value the converter would deliver per ADCH

struct Conversion
{
    uint8_t converter;
    uint8_t slot;
    uint8_t adch;
    uint32_t samplePs; // start of the sample phase after the trigger
};

struct Outcome
{
    std::vector<Conversion> conversions;
    std::vector<uint8_t> interrupts; // converters whose ADC interrupt is pending, in time order
    uint32_t endPs;
};

bool lockActive[2][8]; // pre-trigger lock per slot: set at the pre-trigger, released by the COCO rising edge

Outcome fireTrigger(uint8_t const sourceCode)
{
    Outcome out{{}, {}, 0U};
    struct Pending
    {
        uint32_t ps;
        uint8_t converter;
    };
    std::vector<Pending> irqs;
    for (uint8_t c = 0U; c < 2U; ++c)
    {
        uint32_t const sel = fakeTrgmux.TRGMUXn[14U + c] & TRGMUX_TRGMUXn_SEL0_MASK;
        NxTestPdb& pdb     = fakePdb[c];
        NxTestAdc& adc     = fakeAdc[c];
        if ((sel != sourceCode) || ((pdb.SC & PDB_SC_PDBEN_MASK) == 0U) || ((pdb.SC & PDB_SC_TRGSEL_MASK) != 0U)
            || !pdb.loaded)
        {
            continue;
        }
        bool const fromPdb   = (fakeSim.ADCOPT & SIM_ADC_FIELDS[c]) == 0U;
        bool const hwTrigger = (adc.SC2 & ADC_SC2_ADTRG_MASK) != 0U;
        uint32_t const c1    = pdb.CH[0].C1;
        uint32_t endPrevious = 0U;
        bool chainAlive      = true;
        for (uint8_t m = 0U; m < 8U; ++m)
        {
            if ((c1 & PDB_C1_EN(1UL << m)) == 0U)
            {
                continue;
            }
            bool const bb  = (c1 & PDB_C1_BB(1UL << m)) != 0U;
            bool const tos = (c1 & PDB_C1_TOS(1UL << m)) != 0U;
            uint32_t at    = 0U;
            if (bb)
            {
                if ((m == 0U) || !chainAlive)
                {
                    // BB of pre-trigger 0 waits for the acknowledge of pre-trigger 7 (ring of CH0, [RM13] Figure
                    // 46-1): it never fires within this trigger
                    chainAlive = false;
                    continue;
                }
                at = endPrevious + 2U * PDB_CLOCK_PS;
            }
            else
            {
                at = (tos ? pdb.loadedDly[m] + 2U : 2U) * PDB_CLOCK_PS;
            }
            if (lockActive[c][m] || ((m > 0U) && !bb && (at < endPrevious)))
            {
                pdb.CH[0].S |= PDB_S_ERR(1UL << m); // pre-trigger while a conversion of the channel is running
                chainAlive = false;
                continue;
            }
            lockActive[c][m] = true;
            if (!fromPdb || !hwTrigger)
            {
                chainAlive = false; // the ADC does not take the pre-trigger: no conversion, no acknowledge
                continue;
            }
            uint8_t const adch = static_cast<uint8_t>(adc.SC1[m] & ADC_SC1_ADCH_MASK);
            if (adch == ADC_SC1_ADCH_MASK)
            {
                chainAlive = false; // module disabled in this slot
                continue;
            }
            out.conversions.push_back(Conversion{c, m, adch, at});
            endPrevious = at + CONVERSION_PS;
            bool const rising = (adc.SC1[m] & ADC_SC1_COCO_MASK) == 0U;
            adc.R[m].value    = analog[c][adch];
            adc.SC1[m] |= ADC_SC1_COCO_MASK;
            if (rising)
            {
                lockActive[c][m] = false;
            }
            if ((adc.SC1[m] & ADC_SC1_AIEN_MASK) != 0U)
            {
                irqs.push_back(Pending{endPrevious, c});
            }
            if (endPrevious > out.endPs)
            {
                out.endPs = endPrevious;
            }
        }
    }
    for (uint32_t i = 0U; i < irqs.size(); ++i)
    {
        out.interrupts.push_back(irqs[i].converter);
    }
    return out;
}

// ---------------------------------------------------------------------------------------------- consumer
struct Consumer
{
    static unsigned calls;
    static uint16_t raw[8];
    static uint8_t count;
    static uint8_t errors;
    static uint32_t sequence;
    static uint32_t ticks;

    static void complete(bsp::sampleGroup::Result const& r)
    {
        ++calls;
        count = r.count;
        for (uint8_t i = 0U; (i < r.count) && (i < 8U); ++i)
        {
            raw[i] = r.raw[i];
        }
        errors   = r.errors;
        sequence = r.sequence;
        ticks    = r.timerTicks;
    }
};

unsigned Consumer::calls    = 0U;
uint16_t Consumer::raw[8]   = {};
uint8_t Consumer::count     = 0U;
uint8_t Consumer::errors    = 0U;
uint32_t Consumer::sequence = 0U;
uint32_t Consumer::ticks    = 0U;

using Chain = bios::sampleGroup::SampleGroupChain<cfg::SEQUENCER_COUNT, cfg::MEMBER_COUNT, Consumer>;

uint8_t irqConverter() { return (cfg::IRQ == ADC0_IRQn) ? 0U : ((cfg::IRQ == ADC1_IRQn) ? 1U : 0xFFU); }

void reset()
{
    for (NxTestAdc& adc : fakeAdc)
    {
        adc = NxTestAdc{};
    }
    for (NxTestPdb& pdb : fakePdb)
    {
        pdb = NxTestPdb{};
    }
    fakeTrgmux = NxTestTrgmux{};
    fakeSim    = SIM_Type{};
    fakeFtm3   = FTM_Type{};
    for (auto& converter : lockActive)
    {
        for (bool& lock : converter)
        {
            lock = false;
        }
    }
    events.clear();
    Consumer::calls = 0U;
    for (uint8_t a = 0U; a < 64U; ++a)
    {
        analog[0][a] = static_cast<uint16_t>(1000U + a); // ADC0: 1000 + channel
        analog[1][a] = static_cast<uint16_t>(2000U + a); // ADC1: 2000 + channel
    }
}

/** Run one trigger through the model and, if its interrupt is pending, the interrupt of the chain. */
Outcome triggerAndServe(Chain& chain, uint8_t const code, uint32_t const ftmCnt)
{
    Outcome const out = fireTrigger(code);
    fakeFtm3.CNT      = ftmCnt;
    for (uint8_t c : out.interrupts)
    {
        if (c == irqConverter())
        {
            chain.onComplete();
        }
    }
    return out;
}

// ---------------------------------------------------------------------------------------------- tests
void testGeneratedValues()
{
    // the generated values against the hand calculation from the reference manual
    CHECK(cfg::MEMBER_COUNT == 4U);
    CHECK(cfg::SEQUENCER_COUNT == 2U);
    CHECK(cfg::CONFIG.sequencers[0].trgmuxIndex == 14U); // TRGMUX_PDB0
    CHECK(cfg::CONFIG.sequencers[1].trgmuxIndex == 15U); // TRGMUX_PDB1
    CHECK(cfg::CONFIG.sequencers[0].trgmuxSel == FTM3_EXT_TRIG);
    CHECK(cfg::CONFIG.sequencers[0].pdbDly0 == 40U);     // 500 ns at 80 MHz
    CHECK(cfg::CONFIG.sequencers[0].pdbCh0C1 == 0x00020303U);
    CHECK(cfg::CONFIG.sequencers[0].sc1[0] == 4U);       // SE4
    CHECK(cfg::CONFIG.sequencers[0].sc1[1] == (10U | ADC_SC1_AIEN_MASK)); // SE10, last of the completing sequencer
    CHECK(cfg::CONFIG.sequencers[1].sc1[0] == 2U);
    CHECK(cfg::CONFIG.sequencers[1].sc1[1] == 3U);
    CHECK(cfg::IRQ == ADC0_IRQn);
    CHECK(cfg::PHASE_I_W == 2U);
}

void testInit()
{
    reset();
    fakeSim.ADCOPT       = 0x3F3FU | 0x10000U; // both ADCs from TRGMUX with software pre-triggers; other bits kept
    fakeAdc[0].SC2       = ADC_SC2_ADTRG_MASK;
    fakePdb[1].SC.value  = PDB_SC_PDBEN_MASK;
    Chain chain(cfg::CONFIG, cfg::registers());
    CHECK(!chain.arm()); // not before init()
    CHECK(chain.init());
    CHECK(fakeSim.ADCOPT == 0x10000U); // both ADCs from their PDB, nothing else touched
    for (uint8_t c = 0U; c < 2U; ++c)
    {
        CHECK(fakeAdc[c].calibrations == 1U);
        CHECK((fakeAdc[c].SC1[0] & ADC_SC1_COCO_MASK) == 0U); // calibration result read
        CHECK(fakeAdc[c].SC2 == ADC_SC2_ADTRG_MASK);
        CHECK(fakeAdc[c].CFG1 == static_cast<uint32_t>(NX_ADC_CFG1));
        CHECK(fakeAdc[c].CFG2 == static_cast<uint32_t>(NX_ADC_CFG2));
        CHECK(fakeAdc[c].SC3 == static_cast<uint32_t>(NX_ADC_SC3));
        CHECK((fakePdb[c].SC & PDB_SC_PDBEN_MASK) == 0U);
        CHECK(fakeAdc[c].SC1[2] == ADC_SC1_ADCH_MASK); // unused slots disabled
    }
    CHECK((fakeAdc[0].SC1[0] & ADC_SC1_ADCH_MASK) == 4U);
    CHECK((fakeAdc[1].SC1[1] & ADC_SC1_ADCH_MASK) == 3U);
    CHECK(fakeTrgmux.TRGMUXn[14].value == 0U); // nothing routed by init()
    CHECK(!chain.isArmed());
    // no trigger reaches anything before arm()
    Outcome const out = fireTrigger(FTM3_EXT_TRIG);
    CHECK(out.conversions.empty());
}

void testArmOrderAndOneGroup()
{
    reset();
    fakeTrgmux.TRGMUXn[14].value = 0x2A000000U; // other SEL fields of the PDB registers are kept
    Chain chain(cfg::CONFIG, cfg::registers());
    CHECK(chain.init());
    events.clear();
    CHECK(chain.arm());
    CHECK(chain.isArmed());
    // the routes come last: after every PDB was enabled with LDOK
    int lastLdok = -1;
    int firstRoute = -1;
    for (int i = 0; i < static_cast<int>(events.size()); ++i)
    {
        NxTestEvent const& e = events[static_cast<size_t>(i)];
        if ((e.what == NX_PDB_SC_WRITE) && ((e.value & PDB_SC_LDOK_MASK) != 0U))
        {
            lastLdok = i;
        }
        if ((e.what == NX_TRGMUX_WRITE) && (firstRoute < 0))
        {
            firstRoute = i;
        }
    }
    CHECK((lastLdok >= 0) && (firstRoute > lastLdok));
    CHECK(fakeTrgmux.TRGMUXn[14].value == (0x2A000000U | FTM3_EXT_TRIG));
    CHECK(fakeTrgmux.TRGMUXn[15].value == FTM3_EXT_TRIG);
    CHECK((fakeTrgmux.TRGMUXn[14].value & TRGMUX_TRGMUXn_LK_MASK) == 0U); // no lock: disarm must be possible
    CHECK(fakePdb[0].loaded && (fakePdb[0].loadedDly[0] == 40U));

    // a trigger of another timer starts nothing
    CHECK(fireTrigger(FTM0_EXT_TRIG).conversions.empty());

    Outcome const out = triggerAndServe(chain, FTM3_EXT_TRIG, 1600U);
    CHECK(out.conversions.size() == 4U);
    CHECK(out.interrupts.size() == 1U);                   // exactly one interrupt per trigger ...
    CHECK((out.interrupts.size() == 1U) && (out.interrupts[0] == irqConverter())); // ... of the vector of the config
    // channels, slots and sample instants per converter (hand values: 525 ns, 2259 ns)
    struct Expect
    {
        uint8_t converter, slot, adch;
        uint32_t ps;
    };
    Expect const expect[] = {{0U, 0U, 4U, 525000U}, {0U, 1U, 10U, 2259000U}, {1U, 0U, 2U, 525000U},
                             {1U, 1U, 3U, 2259000U}};
    for (Expect const& e : expect)
    {
        bool found = false;
        for (Conversion const& c : out.conversions)
        {
            if ((c.converter == e.converter) && (c.slot == e.slot))
            {
                found = true;
                CHECK(c.adch == e.adch);
                CHECK(c.samplePs == e.ps);
            }
        }
        CHECK(found);
    }
    CHECK(out.endPs == 3968000U); // the whole group ends where nxconfig says (3968 ns)
    // results in project order: PHASE_I_U (ADC0 SE4), PHASE_I_V (ADC1 SE2), PHASE_I_W (ADC1 SE3), DC_LINK_U (ADC0 SE10)
    CHECK(Consumer::calls == 1U);
    CHECK(Consumer::count == 4U);
    CHECK(Consumer::raw[cfg::PHASE_I_U] == 1004U);
    CHECK(Consumer::raw[cfg::PHASE_I_V] == 2002U);
    CHECK(Consumer::raw[cfg::PHASE_I_W] == 2003U);
    CHECK(Consumer::raw[cfg::DC_LINK_U] == 1010U);
    CHECK(Consumer::errors == bsp::sampleGroup::ERROR_NONE);
    CHECK(Consumer::sequence == 1U);
    CHECK(Consumer::ticks == 1600U);
    // every result read: COCO cleared, so the next trigger converts again without a sequence error
    for (uint8_t c = 0U; c < 2U; ++c)
    {
        CHECK((fakeAdc[c].SC1[0] & ADC_SC1_COCO_MASK) == 0U);
        CHECK((fakeAdc[c].SC1[1] & ADC_SC1_COCO_MASK) == 0U);
    }
    analog[1][3] = 777U;
    Outcome const second = triggerAndServe(chain, FTM3_EXT_TRIG, 1601U);
    CHECK(second.conversions.size() == 4U);
    CHECK(Consumer::calls == 2U);
    CHECK(Consumer::sequence == 2U);
    CHECK(Consumer::raw[cfg::PHASE_I_W] == 777U);
    CHECK(Consumer::errors == bsp::sampleGroup::ERROR_NONE);
    CHECK(((fakePdb[0].CH[0].S | fakePdb[1].CH[0].S) & PDB_S_ERR_MASK) == 0U);
    CHECK(chain.statistics().completions == 2U);
    CHECK(chain.statistics().arms == 1U);
}

void testDisarm()
{
    reset();
    Chain chain(cfg::CONFIG, cfg::registers());
    CHECK(chain.init());
    CHECK(chain.arm());
    events.clear();
    chain.disarm();
    CHECK(!chain.isArmed());
    // routes removed first, then the PDBs off
    int lastRoute = -1;
    int firstOff  = -1;
    for (int i = 0; i < static_cast<int>(events.size()); ++i)
    {
        NxTestEvent const& e = events[static_cast<size_t>(i)];
        if (e.what == NX_TRGMUX_WRITE)
        {
            lastRoute = i;
        }
        if ((e.what == NX_PDB_SC_WRITE) && ((e.value & PDB_SC_PDBEN_MASK) == 0U) && (firstOff < 0))
        {
            firstOff = i;
        }
    }
    CHECK((lastRoute >= 0) && (firstOff > lastRoute));
    CHECK((fakeTrgmux.TRGMUXn[14].value & TRGMUX_TRGMUXn_SEL0_MASK) == 0U);
    CHECK((fakeTrgmux.TRGMUXn[15].value & TRGMUX_TRGMUXn_SEL0_MASK) == 0U);
    Outcome const out = triggerAndServe(chain, FTM3_EXT_TRIG, 5U);
    CHECK(out.conversions.empty());
    CHECK(Consumer::calls == 0U);
    // armed again: the sequence starts at 1
    CHECK(chain.arm());
    (void)triggerAndServe(chain, FTM3_EXT_TRIG, 6U);
    CHECK((Consumer::calls == 1U) && (Consumer::sequence == 1U));
    CHECK(chain.statistics().arms == 2U);
}

void testSequenceErrorRestartsThePdbs()
{
    reset();
    Chain chain(cfg::CONFIG, cfg::registers());
    CHECK(chain.init());
    CHECK(chain.arm());
    Outcome const out = fireTrigger(FTM3_EXT_TRIG);
    CHECK(out.interrupts.size() == 1U);
    fakePdb[1].CH[0].S |= PDB_S_ERR(2U); // as if pre-trigger 1 of PDB1 had come while its converter was busy
    events.clear();
    chain.onComplete();
    CHECK((Consumer::errors & bsp::sampleGroup::ERROR_SEQUENCE) != 0U);
    CHECK((fakePdb[1].CH[0].S & PDB_S_ERR_MASK) == 0U); // cleared
    CHECK(chain.statistics().sequenceErrors == 1U);
    CHECK(chain.statistics().recoveries == 1U);
    // every PDB was switched off (releases the locks) and on again with its buffers loaded
    bool off[2] = {false, false};
    for (NxTestEvent const& e : events)
    {
        if ((e.what == NX_PDB_SC_WRITE) && ((e.value & PDB_SC_PDBEN_MASK) == 0U))
        {
            off[e.index] = true;
        }
    }
    CHECK(off[0] && off[1]);
    CHECK(fakePdb[0].loaded && fakePdb[1].loaded);
    // the next trigger is clean again
    (void)triggerAndServe(chain, FTM3_EXT_TRIG, 7U);
    CHECK(Consumer::errors == bsp::sampleGroup::ERROR_NONE);
}

void testMissingConversionIsReportedAndBounded()
{
    reset();
    Chain chain(cfg::CONFIG, cfg::registers());
    CHECK(chain.init());
    CHECK(chain.arm());
    Outcome const out = fireTrigger(FTM3_EXT_TRIG);
    CHECK(out.interrupts.size() == 1U);
    fakeAdc[1].SC1[1] &= ~ADC_SC1_COCO_MASK; // ADC1 has not finished its last conversion
    uint32_t const before = nxTestSc1Reads;
    chain.onComplete(); // must return (bounded wait)
    // the wait polls at most missingPollLimit times (the polls of one conversion), plus the reads of the results
    CHECK((nxTestSc1Reads - before) <= (cfg::CONFIG.missingPollLimit + 8U));
    CHECK((nxTestSc1Reads - before) >= cfg::CONFIG.missingPollLimit);
    CHECK((Consumer::errors & bsp::sampleGroup::ERROR_CONVERSION_MISSING) != 0U);
    CHECK(chain.statistics().missingConversions == 1U);
}

void testUnreadResultsWouldBlockThePdb()
{
    // counter-check of the model: without the reads of onComplete() COCO stays set, the conversion of the next
    // trigger has no rising edge, its lock stays, and the trigger after it is a sequence error - the reason why
    // every R[m] is read in the interrupt
    reset();
    Chain chain(cfg::CONFIG, cfg::registers());
    CHECK(chain.init());
    CHECK(chain.arm());
    (void)fireTrigger(FTM3_EXT_TRIG);
    Outcome const second = fireTrigger(FTM3_EXT_TRIG);
    CHECK(second.conversions.size() == 4U);
    Outcome const again = fireTrigger(FTM3_EXT_TRIG);
    CHECK(again.conversions.empty());
    CHECK((fakePdb[0].CH[0].S & PDB_S_ERR_MASK) != 0U);
}
} // namespace

int main()
{
    testGeneratedValues();
    testInit();
    testArmOrderAndOneGroup();
    testDisarm();
    testSequenceErrorRestartsThePdbs();
    testMissingConversionIsReportedAndBounded();
    testUnreadResultsWouldBlockThePdb();
    std::printf("sample group chain host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
