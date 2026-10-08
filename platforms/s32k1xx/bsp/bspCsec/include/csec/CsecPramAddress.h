// NVM-21: where the CSEc parameter RAM (CSE_PRAM) of the S32K1 family lies - it depends on the sub-series.
//
// The CSE_PRAM (128 bytes) lies directly behind the FlexRAM: at 0x1400_1000 on the S32K14x and S32K14xW (FlexRAM
// 4 KB, 0x1400_0000-0x1400_0FFF), at 0x1400_0800 on the S32K11x (FlexRAM 2 KB). Evidence: [MAP] S32K1xx_Memory_Map.xlsx
// (file attachment of [RM13]), sheet "Overview Memory Map", the two CSE_PRAM rows with their derivative columns; its
// revision history notes "updated CSEc_PRAM address for S32K14x, added CSEc_PRAM address for S32K11x". The FlexRAM
// ends are the same sheet's (mcudb <derivative>.yaml, region FLEXRAM); test/test_csec.py checks the table below
// against every derivative of the database.
//
// NVM-13 took 0x1400_0800 from the only SVD file at hand - the S32K116's - for the whole family. On an S32K14x that
// address is FlexRAM offset 0x800: with a partition it is the EMULATED EEPROM. Board 2026-09-30 (NVM-21): the first
// word of M1 became an EEPROM record, the second store hit the FlexRAM while that record was written (not accessible
// until CCIF, [RM13] 36.5.2.3) and ended in the imprecise bus fault; no command reached the CSEc.
//
// The address is chosen at run time from SIM SDID SUBSERIES - what the silicon says - and not from the build: no
// device header of the family has CSE_PRAM (mcudb/tools/device_header_rules/s32k1xx.yaml), and the S32K144 builds
// with the S32K148 header. An unknown sub-series gives 0: the caller refuses before anything is written.
// Shared by the target header csec/CsecPram.h and its host stub, so the host test checks this very table.

#pragma once

#include <cstdint>

namespace platform
{
namespace csec
{
constexpr uint8_t SUBSERIES_S32K11X = 1U; // SIM SDID SUBSERIES ([RM13] 11.3.1.8)
constexpr uint8_t SUBSERIES_S32K14X = 4U; // S32K14x and S32K14xW

constexpr uintptr_t PRAM_BASE_S32K11X = 0x14000800U; // behind 2 KB FlexRAM
constexpr uintptr_t PRAM_BASE_S32K14X = 0x14001000U; // behind 4 KB FlexRAM
constexpr uint32_t PRAM_WORDS         = 32U;

/** Address of the CSE_PRAM for the SIM SDID sub-series \p subseries; 0 for a sub-series this table does not know. */
constexpr uintptr_t pramBaseOf(uint8_t const subseries)
{
    return (subseries == SUBSERIES_S32K14X) ? PRAM_BASE_S32K14X
                                            : ((subseries == SUBSERIES_S32K11X) ? PRAM_BASE_S32K11X : 0U);
}
} // namespace csec
} // namespace platform
