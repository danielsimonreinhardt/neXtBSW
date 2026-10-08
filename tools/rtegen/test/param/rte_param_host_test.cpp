// Host test of the calibration parameters of the generated RTE (RTE-03): ECU description test/param/rte.yaml,
// synthetic component CalSwc with a ParameterReceiver port (scalar, array, fixed point with slope and with bias,
// float, boolean) and its data file CalSwc_data.m.
// Checked: start values from the data file (stored values after the fixed-point conversion), the parameter table
// rte::PARAMETERS (name, type, element count, conversion, unit, address = RAM storage), and that a write through
// the table address - what a calibration tool (XCP, TOOL-01) will do - changes what the component reads next cycle.
// Build and run: tools/rtegen/test_rtegen.py (RteParamHostTest).

#include "HostStubs.h"
#include "RteConfig.h"
#include "Rte_Internal.h"

#include <cstdio>
#include <cstring>

extern "C"
{
#include "CalSwc.h"
}

bool bios::DigitalInput::value         = false;
uint32_t bios::AnalogInputScale::value = 0U;
uint8_t bios::Output::level[2]         = {0U, 0U};
int async::LockType::depth             = 0;

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

::rte::Parameter const* find(char const* const name)
{
    for (size_t i = 0U; i < ::rte::PARAMETER_COUNT; ++i)
    {
        if (std::strcmp(::rte::PARAMETERS[i].name, name) == 0)
        {
            return &::rte::PARAMETERS[i];
        }
    }
    return nullptr;
}

void cycle()
{
    ::rte::readInputs(0U);
    Run10ms();
    ::rte::writeOutputs(0U);
}
} // namespace

int main()
{
    // start values: converted by the generator from the physical values of CalSwc_data.m
    CHECK(Rte_Prm_CalSwc_CalPrm_Gain_data == 3U);
    CHECK(Rte_Prm_CalSwc_CalPrm_Table_data[0] == 10U);
    CHECK(Rte_Prm_CalSwc_CalPrm_Table_data[3] == 40U);
    CHECK(Rte_Prm_CalSwc_CalPrm_Factor_data == 1500U); // 1.5 with lsb 0.001
    CHECK(Rte_Prm_CalSwc_CalPrm_Temp_data == 650);     // 25 degC with lsb 0.1 and bias -40
    CHECK(Rte_Prm_CalSwc_CalPrm_Alpha_data == 0.125F);
    CHECK(Rte_Prm_CalSwc_CalPrm_Enable_data == 1U);

    // table for XCP/A2L: address is the RAM storage, conversion as in the ARXML
    CHECK(::rte::PARAMETER_COUNT == 6U);
    ::rte::Parameter const* const gain   = find("CalSwc.CalPrm.Gain");
    ::rte::Parameter const* const table  = find("CalSwc.CalPrm.Table");
    ::rte::Parameter const* const factor = find("CalSwc.CalPrm.Factor");
    ::rte::Parameter const* const temp   = find("CalSwc.CalPrm.Temp");
    ::rte::Parameter const* const alpha  = find("CalSwc.CalPrm.Alpha");
    CHECK((gain != nullptr) && (table != nullptr) && (factor != nullptr) && (temp != nullptr) && (alpha != nullptr));
    if ((gain == nullptr) || (table == nullptr) || (factor == nullptr) || (temp == nullptr) || (alpha == nullptr))
    {
        std::printf("%u checks, %u failures\n", checks, failures);
        return 1;
    }
    CHECK(gain->value == &Rte_Prm_CalSwc_CalPrm_Gain_data);
    CHECK(gain->type == ::rte::SignalType::UINT16);
    CHECK(gain->count == 1U);
    CHECK((gain->numerator[0] == 0) && (gain->numerator[1] == 1) && (gain->denominator == 1));
    CHECK(table->value == Rte_Prm_CalSwc_CalPrm_Table_data);
    CHECK(table->count == 4U);
    CHECK((factor->numerator[0] == 0) && (factor->numerator[1] == 1) && (factor->denominator == 1000));
    CHECK(temp->type == ::rte::SignalType::SINT16);
    CHECK((temp->numerator[0] == -400) && (temp->numerator[1] == 1) && (temp->denominator == 10));
    CHECK(std::strcmp(temp->unit, "degC") == 0);
    CHECK(std::strcmp(gain->unit, "") == 0);
    CHECK(alpha->type == ::rte::SignalType::FLOAT32);

    // the component reads the data file values
    CalSwc_Init();
    cycle();
    CHECK(Rte_Buf_CalSwc_Out_Out == (3U * 100U) + 1500U + 1U);
    CHECK(Rte_Buf_CalSwc_FOut_FOut == 1.0F);
    CHECK(CalSwc_TempSeen == 650);

    // a calibration tool writes through the table address (XCP DOWNLOAD to the A2L address): next cycle sees it
    uint16_t const newGain = 5U;
    std::memcpy(gain->value, &newGain, sizeof(newGain));
    uint16_t const newRow[4] = {1U, 2U, 3U, 4U};
    std::memcpy(table->value, newRow, sizeof(newRow));
    float const newAlpha = 0.5F;
    std::memcpy(alpha->value, &newAlpha, sizeof(newAlpha));
    cycle();
    CHECK(Rte_Buf_CalSwc_Out_Out == (5U * 10U) + 1500U + 1U);
    CHECK(Rte_Buf_CalSwc_FOut_FOut == 4.0F);

    std::printf("rte param host test: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
