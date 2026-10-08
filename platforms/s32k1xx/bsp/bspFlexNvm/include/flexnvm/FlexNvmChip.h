// NVM-10: what the chip says about its FlexNVM partition and about itself - register reads only, no flash command.
//
// The S32K1 family has no FTFC command to read the data flash IFR (no Read Resource 0x03 as on Kinetis: not in the
// command table [RM13] 36.5.9.2, FEATURE_FLS_HAS_READ_RESOURCE_CMD 0 for every S32K1 derivative of the NXP SDK). The
// partition is therefore judged from what the reset sequence loads:
//  - SIM FCFG1 DEPART: loaded from the data flash IFR (0x03FC) during reset ([RM13] 11.3.1.10 note); 0xF = erased;
//  - FTFC FCNFG RAMRDY/EEERDY: set during reset from the partition ([RM13] 36.4.4.1.3): not partitioned for emulated
//    EEPROM -> RAMRDY 1, EEERDY 0; partitioned and loaded -> EEERDY 1 (RAMRDY 0).
// SIM FCFG1 EEERAMSIZE is not used for the decision: the reference manual names only DEPART as loaded from the IFR,
// and the NXP SDK (flash_driver.c, "temporary solution for FTFC and S32K144 CSEc part") derives the EEPROM size from
// DEPART as well. Board 2026-09-22 (capture 93): an S32K144 as delivered shows DEPART 0xF with EEERAMSIZE 0x2 (4 KB,
// the FlexRAM size) - not a partition. Should the IFR EEESIZE byte (0x03FE) nevertheless be programmed, PGMPART
// checks both bytes itself and refuses with ACCERR before it erases anything ([RM13] 36.5.11.14, Table 36-69).
//
// SIM SDID ([RM13] 11.3.1.8) tells the part: sub-series and derivate (S32K144 = 1/4/4), package, silicon revision and
// FEATURES bit 7 "Security" (CSEc). With CSEc, the EEPROM size must be 4 KB (EEESIZE 0010b) on 4 KB FlexRAM parts;
// PGMPART with FCCOB1 = 0 (no key space) stays valid.

#pragma once

#include "mcu/mcu.h"

#include <cstdint>

namespace platform
{

class FlexNvmChip
{
public:
    enum class Partition : uint8_t
    {
        NOT_PARTITIONED,   // DEPART erased, FlexRAM is RAM: as delivered or after a mass erase - PGMPART possible
        EEPROM_READY,      // DEPART programmed, FlexRAM holds the emulated EEPROM (EEERDY 1)
        FLEXRAM_AS_RAM,    // DEPART programmed, FlexRAM is RAM: data flash only, or EEPROM not loaded during reset
        EEPROM_NOT_LOADED, // DEPART programmed, FlexRAM neither RAM nor EEPROM (loading or failed)
        INCONSISTENT       // DEPART erased, but FlexRAM not RAM or EEERDY set - do not partition, check at the board
    };

    static constexpr uint8_t ERASED_CODE = 0xFU;

    /** SIM FCFG1 DEPART (loaded from data flash IFR 0x03FC during reset; 0xF = erased). */
    static uint8_t departCode()
    {
        return static_cast<uint8_t>((SIM->FCFG1 & SIM_FCFG1_DEPART_MASK) >> SIM_FCFG1_DEPART_SHIFT);
    }

    /** SIM FCFG1 EEERAMSIZE (shown for information only, see above). */
    static uint8_t eeeRamSizeCode()
    {
        return static_cast<uint8_t>((SIM->FCFG1 & SIM_FCFG1_EEERAMSIZE_MASK) >> SIM_FCFG1_EEERAMSIZE_SHIFT);
    }

    /** EEERAMSIZE is a code of the SIM table ([RM13] 11.3.1.10: 0010b..1001b sizes, 1111b 0 bytes). */
    static bool definedEeeRamSizeCode(uint8_t const code)
    {
        return ((code >= 0x2U) && (code <= 0x9U)) || (code == ERASED_CODE);
    }

    static bool ramReady() { return (FTFC->FCNFG & FTFC_FCNFG_RAMRDY_MASK) != 0U; }

    static bool eeeReady() { return (FTFC->FCNFG & FTFC_FCNFG_EEERDY_MASK) != 0U; }

    static Partition partition()
    {
        bool const ram = ramReady();
        bool const eee = eeeReady();
        if (departCode() == ERASED_CODE)
        {
            return (ram && !eee && definedEeeRamSizeCode(eeeRamSizeCode())) ? Partition::NOT_PARTITIONED
                                                                              : Partition::INCONSISTENT;
        }
        if (eee)
        {
            return Partition::EEPROM_READY;
        }
        return ram ? Partition::FLEXRAM_AS_RAM : Partition::EEPROM_NOT_LOADED;
    }

    static char const* partitionText(Partition const partition)
    {
        switch (partition)
        {
            case Partition::NOT_PARTITIONED:
                return "not partitioned (IFR DEPART erased, FlexRAM as RAM; EEERAMSIZE is no IFR code)";
            case Partition::EEPROM_READY: return "partitioned, emulated EEPROM ready";
            case Partition::FLEXRAM_AS_RAM:
                return "partitioned, FlexRAM as RAM (data flash only, or EEPROM not loaded during reset)";
            case Partition::EEPROM_NOT_LOADED: return "partitioned, FlexRAM not loaded (EEERDY 0, RAMRDY 0)";
            case Partition::INCONSISTENT:
                return "INCONSISTENT: DEPART erased but FlexRAM not plain RAM - do not partition, check at the board";
            default: return "?";
        }
    }

    /** SIM SDID fields ([RM13] 11.3.1.8). */
    struct DeviceId
    {
        uint32_t raw;
        uint8_t subseries; // 4: S32K14x(W), 1: S32K11x
        uint8_t derivate;  // S32K14x: 8/6/4/2, 5 = 144W, 3 = 142W; S32K11x: 8/6
        uint8_t revision;
        uint8_t package;
        uint8_t ramSizeCode;
        bool security; // FEATURES bit 7: CSEc present
    };

    static DeviceId deviceId()
    {
        uint32_t const sdid = SIM->SDID;
        return DeviceId{
            sdid,
            field(sdid, SIM_SDID_SUBSERIES_MASK, SIM_SDID_SUBSERIES_SHIFT),
            field(sdid, SIM_SDID_DERIVATE_MASK, SIM_SDID_DERIVATE_SHIFT),
            field(sdid, SIM_SDID_REVID_MASK, SIM_SDID_REVID_SHIFT),
            field(sdid, SIM_SDID_PACKAGE_MASK, SIM_SDID_PACKAGE_SHIFT),
            field(sdid, SIM_SDID_RAMSIZE_MASK, SIM_SDID_RAMSIZE_SHIFT),
            (field(sdid, SIM_SDID_FEATURES_MASK, SIM_SDID_FEATURES_SHIFT) & 0x80U) != 0U};
    }

    /** Part name of the SDID ("S32K144", "S32K142W"), "S32K1?" for codes the reference manual does not list. */
    static char const* deviceName(DeviceId const& id)
    {
        if (id.subseries == 4U)
        {
            switch (id.derivate)
            {
                case 8U: return "S32K148";
                case 6U: return "S32K146";
                case 4U: return "S32K144";
                case 5U: return "S32K144W";
                case 2U: return "S32K142";
                case 3U: return "S32K142W";
                default: break;
            }
        }
        else if (id.subseries == 1U)
        {
            switch (id.derivate)
            {
                case 8U: return "S32K118";
                case 6U: return "S32K116";
                default: break;
            }
        }
        return "S32K1?";
    }

    /** Package of the SDID code ([RM13] 11.3.1.8). */
    static char const* packageText(uint8_t const code)
    {
        switch (code)
        {
            case 0x2U: return "48 LQFP";
            case 0x3U: return "64 LQFP";
            case 0x4U: return "100 LQFP";
            case 0x6U: return "144 LQFP";
            case 0x7U: return "176 LQFP";
            case 0x8U: return "100 MAP BGA";
            default: return "reserved";
        }
    }

private:
    static uint8_t field(uint32_t const value, uint32_t const mask, uint32_t const shift)
    {
        return static_cast<uint8_t>((value & mask) >> shift);
    }
};

} // namespace platform
