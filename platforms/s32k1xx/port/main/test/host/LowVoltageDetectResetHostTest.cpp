/* SYS-125 (AN-066, doc/safety/hsi-s32k1xx.md H9, concept.md SM-59): host test of the low-voltage detect reset of the
 * family port (platforms/s32k1xx/port/main/include/pmc/LowVoltageDetectReset.h, called by boardInit()).
 *
 * The real function runs on a register model of PMC_LVDSC1 inside a model of the supply thresholds of the S32K1xx
 * ([DS13] table 8, [RM13] 25.2.1, 25.2.2.2, 41.6, 41.7.1.2):
 *   - VPOR clears LVDRE and LVDIE (power-on reset), every other reset leaves LVDRE as it is; LVDF is level sensitive
 *     and cleared by a reset; LVDACK reads 0;
 *   - VLVR (2.50 ... 2.70 V falling) always resets; VLVD (2.80 ... 3.00 V falling) resets only with LVDRE = 1;
 *   - after a power-on or low-voltage reset the MCU stays in reset until VDD is above VLVD + 50 mV hysteresis.
 * Every case runs at both ends of the VLVD range and with both readings of [RM13] 26.1 (an LVD reset is a "chip POR":
 * it may clear LVDRE as well, or not). The scenario "port without SYS-125" shows the gap of AN-066: the MCU keeps
 * running at 2.71 V, below the specified supply (2.7 V min., [DS13] table 3 note), until the LVR.
 *
 * Build and run (zig c++, no hardware; tools/test -k low-voltage-detect-reset):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Werror \
 *       -Iplatforms/s32k1xx/port/main/include -Iplatforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp/rtd \
 *       platforms/s32k1xx/port/main/test/host/LowVoltageDetectResetHostTest.cpp -o build/host/lvd.exe
 *   build/host/lvd.exe
 */

// the device header first: LowVoltageDetectReset.h checks its family constants against it (static_assert)
#include "S32K116_PMC.h"
#include "pmc/LowVoltageDetectReset.h"

#include <cstdint>
#include <cstdio>
#include <vector>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

using namespace ::platform::pmc;

// RCM_SRS bits of the model ([RM13] 26.4.3)
constexpr uint32_t SRS_LVD = 0x02U;
constexpr uint32_t SRS_POR = 0x80U;
constexpr uint32_t SRS_SW  = 0x400U;
constexpr uint32_t SRS_WDOG = 0x20U;

/** Register model of PMC_LVDSC1. */
class Lvdsc1Model
{
public:
    Lvdsc1Model& operator=(uint8_t const value)
    {
        ++writes;
        if ((value & LVDSC1_LVDACK) != 0U)
        {
            ++acknowledges;
            if (!belowLvd)
            {
                lvdf = false;
            }
        }
        if ((value & 0x0FU) != 0U)
        {
            ++reservedWrites;
        }
        lvdie = (value & LVDSC1_LVDIE) != 0U;
        lvdre = (value & LVDSC1_LVDRE) != 0U;
        return *this;
    }

    operator uint8_t() const
    {
        return static_cast<uint8_t>(
            (lvdf ? LVDSC1_LVDF : 0U) | (lvdie ? LVDSC1_LVDIE : 0U) | (lvdre ? LVDSC1_LVDRE : 0U));
    }

    void powerOnReset()
    {
        lvdre = false;
        lvdie = false;
        lvdf  = false;
    }

    bool lvdre = false;
    bool lvdie = false;
    bool lvdf  = false;
    bool belowLvd = false;
    int writes = 0;
    int acknowledges = 0;
    int reservedWrites = 0;
};

struct Corner
{
    char const* name;
    uint32_t lvdMv;           // VLVD falling
    uint32_t lvrMv;           // VLVR falling
    bool lvdResetClearsLvdre; // reading of [RM13] 26.1
};

constexpr uint32_t POR_MV      = 1600U; // VPOR typ.
constexpr uint32_t LVD_HYST_MV = 50U;   // VLVD_HYST typ.

/** The supply and reset behaviour of one S32K1xx, with boardInit() of the family port as the start. */
class Chip
{
public:
    Chip(Corner const& c, bool withSys125) : corner(c), sys125(withSys125) {}

    void setVdd(uint32_t const mv)
    {
        vddMv        = mv;
        pmc.belowLvd = mv < corner.lvdMv;
        if (running)
        {
            if (mv < POR_MV)
            {
                reset(SRS_POR | SRS_LVD);
            }
            else if (mv < corner.lvrMv)
            {
                reset(SRS_LVD);
            }
            else if (mv < corner.lvdMv)
            {
                pmc.lvdf = true;
                if (pmc.lvdre)
                {
                    reset(SRS_LVD);
                }
            }
        }
        else
        {
            if (mv < POR_MV)
            {
                pmc.powerOnReset();
                porPending = true;
            }
            else if (mv >= corner.lvdMv + LVD_HYST_MV)
            {
                start(porPending ? (SRS_POR | SRS_LVD) : srs);
            }
        }
    }

    /** A reset that does not depend on the supply (software, watchdog, pin): the MCU restarts at once. */
    void resetBy(uint32_t const cause)
    {
        reset(cause);
        start(cause);
    }

    Corner const& corner;
    bool const sys125;
    Lvdsc1Model pmc;
    uint32_t vddMv  = 0U;
    uint32_t srs    = 0U;
    bool running    = false;
    bool porPending = true;
    int starts      = 0;
    std::vector<uint32_t> resets;

private:
    void reset(uint32_t const cause)
    {
        running = false;
        srs     = cause;
        resets.push_back(cause);
        pmc.lvdf = false;
        if ((cause & SRS_POR) != 0U)
        {
            pmc.powerOnReset();
        }
        else if (((cause & SRS_LVD) != 0U) && corner.lvdResetClearsLvdre)
        {
            pmc.lvdre = false;
        }
    }

    void start(uint32_t const cause)
    {
        running    = true;
        porPending = false;
        srs        = cause;
        ++starts;
        // boardInit() of platforms/s32k1xx/port/main/src/main.cpp
        if (sys125)
        {
            enableLowVoltageDetectReset(pmc);
        }
    }
};

Corner const corners[] = {
    {"VLVD 2.80 V, LVD reset keeps LVDRE", 2800U, 2500U, false},
    {"VLVD 3.00 V, LVD reset keeps LVDRE", 3000U, 2700U, false},
    {"VLVD 2.80 V, LVD reset clears LVDRE", 2800U, 2500U, true},
    {"VLVD 3.00 V, LVD reset clears LVDRE", 3000U, 2700U, true},
};

/* ------------------------------------------------------------------------------------------------ constants */

void testConstants()
{
    // [RM13] 41.7.1.2 as literals, and the device header (also checked by static_assert in the header)
    CHECK(LVDSC1_LVDRE == 0x10U);
    CHECK(LVDSC1_LVDIE == 0x20U);
    CHECK(LVDSC1_LVDACK == 0x40U);
    CHECK(LVDSC1_LVDF == 0x80U);
    CHECK(LVDSC1_LVDRE == PMC_LVDSC1_LVDRE_MASK);
    CHECK(LVDSC1_START_VALUE == 0x10U);
    CHECK(sizeof(IP_PMC->LVDSC1) == 1U);

    // the read-back rule: reset on and interrupt off, whatever the flag says
    CHECK(lowVoltageDetectResetOn(0x10U));
    CHECK(lowVoltageDetectResetOn(0x90U));
    CHECK(!lowVoltageDetectResetOn(0x00U));
    CHECK(!lowVoltageDetectResetOn(0x80U));
    CHECK(!lowVoltageDetectResetOn(0x20U));
    CHECK(!lowVoltageDetectResetOn(0x30U));
    CHECK(!lowVoltageDetectResetOn(0xB0U));
}

/* ----------------------------------------------------------------------------------------------- power-on */

void testPowerOn(Corner const& c)
{
    Chip chip(c, true);
    chip.setVdd(1000U);
    CHECK(!chip.running);
    chip.setVdd(c.lvdMv + LVD_HYST_MV - 10U); // above VPOR, below the release: still held
    CHECK(!chip.running);
    chip.setVdd(5000U);
    CHECK(chip.running);
    CHECK(chip.starts == 1);
    CHECK(chip.srs == (SRS_POR | SRS_LVD));
    CHECK(static_cast<uint8_t>(chip.pmc) == LVDSC1_LVDRE);
    CHECK(lowVoltageDetectResetOn(chip.pmc));
    CHECK(chip.pmc.writes == 1);
    CHECK(chip.pmc.acknowledges == 0);
    CHECK(chip.pmc.reservedWrites == 0);
}

/* ------------------------------------------------------------------------- falling supply on a 5 V board */

void testBrownOut5V(Corner const& c)
{
    Chip chip(c, true);
    chip.setVdd(5000U);
    // above the highest VLVD: no reset at any corner
    chip.setVdd(3010U);
    CHECK(chip.running);
    CHECK(chip.resets.empty());
    // below the lowest VLVD, above the highest VLVR: the LVD reset (the gap of AN-066 is closed)
    chip.setVdd(2790U);
    CHECK(!chip.running);
    CHECK(chip.resets.size() == 1U);
    CHECK(!chip.resets.empty() && (chip.resets.back() == SRS_LVD));
    // held in reset while VDD stays below VLVD + hysteresis
    chip.setVdd(c.lvdMv + LVD_HYST_MV - 1U);
    CHECK(!chip.running);
    chip.setVdd(3060U);
    CHECK(chip.running);
    CHECK(chip.starts == 2);
    CHECK(chip.srs == SRS_LVD);
    // the start writes it again, whatever the LVD reset left
    CHECK(lowVoltageDetectResetOn(chip.pmc));
    CHECK(chip.pmc.writes == 2);
    // a second brown-out resets again
    chip.setVdd(5000U);
    chip.setVdd(2790U);
    CHECK(chip.resets.size() == 2U);
    CHECK(!chip.running);
    // a dip below VPOR is a power-on
    chip.setVdd(900U);
    chip.setVdd(5000U);
    CHECK(chip.running);
    CHECK(chip.srs == (SRS_POR | SRS_LVD));
    CHECK(lowVoltageDetectResetOn(chip.pmc));
    CHECK(chip.pmc.acknowledges == 0);
}

/* ------------------------------------------------------------ baseline: the port before SYS-125 (AN-066) */

void testGapWithoutSys125(Corner const& c)
{
    Chip chip(c, false);
    chip.setVdd(5000U);
    CHECK(chip.pmc.writes == 0);
    CHECK(!lowVoltageDetectResetOn(chip.pmc));
    chip.setVdd(2710U); // below VLVD at every corner, above the highest VLVR
    CHECK(chip.running);
    CHECK(chip.resets.empty());
    CHECK(static_cast<uint8_t>(chip.pmc) == LVDSC1_LVDF); // detected, but nobody reacts
    chip.setVdd(2490U); // below the lowest VLVR
    CHECK(!chip.running);
    CHECK(!chip.resets.empty() && (chip.resets.back() == SRS_LVD));
}

/* --------------------------------------------------------------- resets that do not come from the supply */

void testOtherResets(Corner const& c)
{
    Chip chip(c, true);
    chip.setVdd(5000U);
    // safety reset (software), watchdog reset, the two resets of the watchdog fast test: LVDRE is on in every start
    uint32_t const causes[] = {SRS_SW, SRS_WDOG, SRS_WDOG, SRS_WDOG | SRS_SW};
    for (uint32_t const cause : causes)
    {
        chip.resetBy(cause);
        CHECK(chip.running);
        CHECK(chip.srs == cause);
        CHECK(static_cast<uint8_t>(chip.pmc) == LVDSC1_LVDRE);
    }
    // no additional reset from the LVD at a good supply (SYS-149: the follow-up resets are not the LVD's)
    CHECK(chip.resets.size() == 4U);
    for (uint32_t const cause : chip.resets)
    {
        CHECK((cause & SRS_LVD) == 0U);
    }
    CHECK(chip.pmc.writes == 5);

    // an interrupt enable left behind by earlier code is cleared by the next start
    chip.pmc = static_cast<uint8_t>(LVDSC1_LVDRE | LVDSC1_LVDIE);
    CHECK(!lowVoltageDetectResetOn(chip.pmc));
    chip.resetBy(SRS_SW);
    CHECK(static_cast<uint8_t>(chip.pmc) == LVDSC1_LVDRE);
    // and a cleared LVDRE (e.g. a bit flip) is set again by the next start
    chip.pmc = static_cast<uint8_t>(0U);
    chip.resetBy(SRS_WDOG);
    CHECK(lowVoltageDetectResetOn(chip.pmc));
}

/* -------------------------------------------------- 3.3 V board: the margin the integrator has to keep */

void testBoard3V3(Corner const& c)
{
    Chip chip(c, true);
    chip.setVdd(3300U);
    CHECK(chip.running);
    chip.setVdd(3135U); // 3.3 V - 5 %: above VLVD at every corner
    CHECK(chip.running);
    chip.setVdd(2970U); // 3.3 V - 10 %: resets where VLVD is at its maximum (AoU of SM-59)
    CHECK(chip.running == (c.lvdMv <= 2970U));
}
} // namespace

int main()
{
    testConstants();
    for (Corner const& c : corners)
    {
        int const before = failures;
        testPowerOn(c);
        testBrownOut5V(c);
        testGapWithoutSys125(c);
        testOtherResets(c);
        testBoard3V3(c);
        if (failures != before)
        {
            std::printf("  ... at corner '%s'\n", c.name);
        }
    }
    std::printf("low-voltage detect reset (SYS-125): %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
