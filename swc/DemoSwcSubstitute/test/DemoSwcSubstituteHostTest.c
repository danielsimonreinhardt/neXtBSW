/* SPDX-License-Identifier: Apache-2.0 */
/* Host test of the C substitute of DemoSwc (PLAT-119): src/DemoSwc.c against the Rte_DemoSwc.h tools/rtegen
 * generates for it, with the port accesses of that header implemented here as plain variables (no RTE behind).
 * Built and run by test_demoswc_substitute.py; prints "<n> checks, <m> failures".
 *
 * The cases follow the behaviour swc/README.md describes for DemoSwc: Led1 follows Button1, Led2 blinks with
 * 1 Hz (an edge every 50 calls of Run10ms) and only while the qualifier of PC_Command is QLF_OK, the blink phase
 * survives a blocked period, MotorNtcFiltered_mV is a low-pass with FilterAlpha. */

#include "Rte_DemoSwc.h"

#include <stdio.h>

static int failures = 0;
static int checks   = 0;

#define CHECK(cond)                                                                                                    \
    do                                                                                                                 \
    {                                                                                                                  \
        ++checks;                                                                                                      \
        if (!(cond))                                                                                                   \
        {                                                                                                              \
            ++failures;                                                                                                \
            printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                                                   \
        }                                                                                                              \
    } while (0)

/* ---- the RTE side: what Rte_DemoSwc.h declares, as variables of this test */
static boolean button1     = FALSE;
static uint32 motorNtc_mV  = 0U;
static uint8 pcCommandQlf  = QLF_OK;
static boolean led1        = FALSE;
static boolean led2        = FALSE;
static uint32 filtered_mV  = 0U;
static float32 filterAlpha = 0.125F; /* DemoSwc_data.m, checked by the Python half of this test */
static unsigned writes     = 0U;

Std_ReturnType Rte_Read_DemoSwc_Button1_Button1(boolean* data)
{
    *data = button1;
    return RTE_E_OK;
}

Std_ReturnType Rte_Read_DemoSwc_MotorNtc_mV_MotorNtc_mV(uint32* data)
{
    *data = motorNtc_mV;
    return RTE_E_OK;
}

Std_ReturnType Rte_Read_DemoSwc_PcCommandQlf_PcCommandQlf(uint8* data)
{
    *data = pcCommandQlf;
    return RTE_E_OK;
}

Std_ReturnType Rte_Write_DemoSwc_Led1_Led1(boolean data)
{
    led1 = data;
    ++writes;
    return RTE_E_OK;
}

Std_ReturnType Rte_Write_DemoSwc_Led2_Led2(boolean data)
{
    led2 = data;
    ++writes;
    return RTE_E_OK;
}

Std_ReturnType Rte_Write_DemoSwc_MotorNtcFiltered_mV_MotorNtcFiltered_mV(uint32 data)
{
    filtered_mV = data;
    ++writes;
    return RTE_E_OK;
}

float32 Rte_Prm_DemoSwc_CalPrm_FilterAlpha(void) { return filterAlpha; }

/* ---- cases */
static void initWritesNothing(void)
{
    DemoSwc_Init();
    CHECK(writes == 0U);
}

static void buttonDrivesLed1(void)
{
    button1 = TRUE;
    DemoSwc_Run10ms();
    CHECK(led1 == TRUE);
    button1 = FALSE;
    DemoSwc_Run10ms();
    CHECK(led1 == FALSE);
    CHECK(writes == 4U); /* Led1 and Led2 per call, nothing else */
}

static void led2BlinksWithOneHertz(void)
{
    int edges        = 0;
    int lastEdge     = -1;
    int wrongSpacing = 0;
    boolean previous = led2;
    for (int call = 0; call < 1000; ++call)
    {
        DemoSwc_Run10ms();
        if (led2 != previous)
        {
            if ((lastEdge >= 0) && ((call - lastEdge) != 50))
            {
                ++wrongSpacing;
            }
            lastEdge = call;
            ++edges;
            previous = led2;
        }
    }
    CHECK(edges == 20);
    CHECK(wrongSpacing == 0);
}

/* After 1002 calls the blink toggled last at call 1001 (calls 1, 51, ..., 1001 toggle) and is on; the next toggle is
 * due at call 1051, so every change of Led2 below comes from the qualifier. */
static void led2IsReleasedByTheQualifier(void)
{
    static uint8 const errors[]
        = {QLF_ERROR_CRC, QLF_ERROR_ALIVE, QLF_ERROR_TIMEOUT, QLF_DEBOUNCE, QLF_NOT_AVAILABLE, QLF_ERROR_RANGE};

    pcCommandQlf = QLF_OK;
    DemoSwc_Run10ms();
    CHECK(led2 == TRUE);
    for (unsigned i = 0U; i < (sizeof(errors) / sizeof(errors[0])); ++i)
    {
        pcCommandQlf = errors[i];
        DemoSwc_Run10ms();
        CHECK(led2 == FALSE);
        pcCommandQlf = QLF_OK;
        DemoSwc_Run10ms();
        CHECK(led2 == TRUE); /* healing keeps the phase of the blink */
    }
    pcCommandQlf = QLF_ERROR; /* bus error: Led2 blocked, Led1 still follows Button1 */
    button1      = TRUE;
    DemoSwc_Run10ms();
    CHECK(led2 == FALSE);
    CHECK(led1 == TRUE);
    pcCommandQlf = QLF_OK;
    button1      = FALSE;
    DemoSwc_Run10ms();
    CHECK(led2 == TRUE);
    CHECK(led1 == FALSE);
}

static void ntcFilterConverges(void)
{
    motorNtc_mV = 1000U;
    DemoSwc_Run100ms();
    CHECK(filtered_mV == 125U); /* 0 + (1000 - 0) / 8 */
    for (int i = 0; i < 100; ++i)
    {
        DemoSwc_Run100ms();
    }
    CHECK(filtered_mV == 1000U);
    motorNtc_mV = 0U;
    DemoSwc_Run100ms();
    CHECK(filtered_mV == 875U);
}

static void filterAlphaIsLimited(void)
{
    DemoSwc_Init();
    motorNtc_mV = 3300U;
    filterAlpha = 2.0F; /* above 1: limited to 1, the output follows the input at once */
    DemoSwc_Run100ms();
    CHECK(filtered_mV == 3300U);
    filterAlpha = -0.5F; /* below 0: limited to 0, the output holds */
    motorNtc_mV = 0U;
    DemoSwc_Run100ms();
    CHECK(filtered_mV == 3300U);
    filterAlpha = 1.0F;
    motorNtc_mV = 0xFFFFFFFFU; /* largest input: no overflow of the conversion back to uint32 */
    DemoSwc_Run100ms();
    CHECK(filtered_mV == 0xFFFFFFFFU);
    filterAlpha = 0.125F;
}

static void initRestarts(void)
{
    DemoSwc_Init();
    pcCommandQlf = QLF_OK;
    motorNtc_mV  = 800U;
    DemoSwc_Run100ms();
    CHECK(filtered_mV == 100U); /* filter state back at 0 */
    DemoSwc_Run10ms();
    CHECK(led2 == TRUE); /* the first call after Init toggles the blink on */
}

int main(void)
{
    initWritesNothing();
    buttonDrivesLed1();
    led2BlinksWithOneHertz();
    led2IsReleasedByTheQualifier();
    ntcFilterConverges();
    filterAlphaIsLimited();
    initRestarts();
    printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
