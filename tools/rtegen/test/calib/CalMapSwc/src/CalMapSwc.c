/* CalMapSwc: torque limiter over a map, two curves, a shared axis and scalar limits (RTE-26 test profile).
 *
 * Hand-written software component (swc.yaml: implementation: c). It reads every kind of calibration object of
 * stage K1 through the RTE: the shared axis SpeedAxis_rpm, the map TorqueLimit_Nm (X shared, Y own), the curves
 * BoostCurve (shared axis) and DerateCurve (own breakpoints), the value MaxTorque_Nm, the value table Mode, the
 * array Offsets_Nm and the RTE-03 parameter Gain. The lookup takes the segment of the breakpoint at or below the
 * input and holds at both ends (no interpolation: the component decides that, not the calibration layer). */

#include "Rte_CalMapSwc.h"

#define CALMAP_SPEED_POINTS 5U   /* SpeedAxis_rpm */
#define CALMAP_TEMP_POINTS 3U    /* TorqueLimit_Nm_Y */
#define CALMAP_DERATE_POINTS 4U  /* DerateCurve_X */
#define CALMAP_MODE_SPORT 2U     /* value table of Mode */

static uint8 speedIndex(uint16 const speed)
{
    uint16 const* const axis = Rte_Prm_CalPrm_SpeedAxis_rpm();
    uint8 i                  = 0U;
    while (((uint8)(i + 1U) < CALMAP_SPEED_POINTS) && (axis[i + 1U] <= speed))
    {
        ++i;
    }
    return i;
}

static uint8 tempIndex(sint16 const temp_degC)
{
    sint8 const* const axis = Rte_Prm_CalPrm_TorqueLimit_Nm_Y();
    uint8 i                 = 0U;
    while (((uint8)(i + 1U) < CALMAP_TEMP_POINTS) && ((sint16)axis[i + 1U] <= temp_degC))
    {
        ++i;
    }
    return i;
}

static uint8 derateIndex(sint16 const temp_dC)
{
    sint16 const* const axis = Rte_Prm_CalPrm_DerateCurve_X(); /* lsb 0.1 degC */
    uint8 i                  = 0U;
    while (((uint8)(i + 1U) < CALMAP_DERATE_POINTS) && (axis[i + 1U] <= temp_dC))
    {
        ++i;
    }
    return i;
}

void CalMapSwc_Init(void) {}

/* Runnable Run10ms, every 10 ms: torque limit (0.1 Nm) and boost demand (mbar). */
void CalMapSwc_Run10ms(void)
{
    uint16 speed   = 0U;
    sint16 temp    = 0;
    (void)Rte_Read_Speed_rpm_Speed_rpm(&speed);
    (void)Rte_Read_Temp_degC_Temp_degC(&temp);

    uint8 const x      = speedIndex(speed);
    uint8 const y      = tempIndex(temp);
    sint16 limit       = Rte_Prm_CalPrm_TorqueLimit_Nm()[(x * CALMAP_TEMP_POINTS) + y]; /* row by row */
    uint8 const derate = Rte_Prm_CalPrm_DerateCurve()[derateIndex((sint16)(temp * 10))];     /* lsb 0.5 % */
    limit              = (sint16)(((sint32)limit * (sint32)derate) / 200);
    limit              = (sint16)(limit + Rte_Prm_CalPrm_Offsets_Nm()[x < 3U ? x : 2U] * 10);
    if (limit > Rte_Prm_CalPrm_MaxTorque_Nm())
    {
        limit = Rte_Prm_CalPrm_MaxTorque_Nm();
    }
    if (Rte_Prm_CalPrm_Mode() != CALMAP_MODE_SPORT)
    {
        limit = (sint16)((float32)limit * Rte_Prm_CalPrm_Gain());
    }
    (void)Rte_Write_Torque_Nm_Torque_Nm(limit);
    (void)Rte_Write_Boost_mbar_Boost_mbar(Rte_Prm_CalPrm_BoostCurve()[x]);
}
