/* Host test for the UJA113x driver against a register model of the SBC.
 * Build and run: see README (zig c++, no target hardware needed). */

#include "sbc/Uja113x.h"
#include "sbc/Uja113xResetCounter.h"
#include "spi/LpspiBaudrate.h"

#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                                  \
    do                                                                               \
    {                                                                                \
        ++checks;                                                                    \
        if (!(cond))                                                                 \
        {                                                                            \
            ++failures;                                                              \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);            \
        }                                                                            \
    } while (false)

uint8_t const BOARD_ID = 0x51U;

/* Register model with the behaviour the driver relies on (data sheet / AH1501). */
class FakeUja113x : public ::bsp::spi::ISpiDevice
{
public:
    uint8_t reg[128];
    bool spiFails        = false;
    uint32_t nowMs       = 0U;
    bool systemReset     = false; // illegal watchdog change, illegal Sleep, window violation
    bool sleeping        = false;
    uint8_t transceiverIrqEnableAtSleep = 0U;
    uint8_t wakeIrqEnableAtSleep        = 0U; // register 0x37 when Sleep was entered (SYS-02)
    uint8_t watchdogControlAtSleep      = 0U; // register 0x00 when Sleep was entered (SYS-02)
    uint8_t canControlAtSleep           = 0U; // register 0x20 when Sleep was entered (COM-39)
    uint32_t lastTriggerMs = 0U;
    uint32_t maxTriggerGapMs = 0U;
    bool triggeredOnce   = false;
    std::vector<uint8_t> writtenAddresses;
    std::vector<uint8_t> modeHistory;
    uint32_t reads[128]; // read accesses per register (COM-08: transceiver status supervision)
    bool ctsFollowsCmc = false; // COM-23: a write of CMC switches the transmitter status CTS (off by default: the
                                // older tests set 0x22 themselves)

    FakeUja113x()
    {
        std::memset(reg, 0, sizeof(reg));
        std::memset(reads, 0, sizeof(reads));
        // State after SBC power-up with the eval board MTPNV (0x73 = 0x00, 0x74 = 0x06).
        reg[UJA113X_REG_IDENTIFICATION_0]   = BOARD_ID;
        reg[UJA113X_REG_WATCHDOG_CONTROL]   = (UJA113X_WMC_AUTONOMOUS << 5U) | UJA113X_NWP_128MS;
        reg[UJA113X_REG_MODE_CONTROL]       = UJA113X_MC_STANDBY;
        reg[UJA113X_REG_MAIN_STATUS]        = UJA113X_NMS_MASK | UJA113X_RSS_EXIT_OFF;
        reg[UJA113X_REG_WATCHDOG_STATUS]    = UJA113X_SDMS_MASK;
        reg[UJA113X_REG_REGULATOR_CONTROL]  = 0x00U;
        reg[UJA113X_REG_CAN_CONTROL]        = 0x04U; // CSC = 01, CMC offline
        reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK | UJA113X_CPNERR_MASK;
        reg[UJA113X_REG_SYSTEM_IRQ_STATUS]  = UJA113X_POSI_MASK;
        reg[UJA113X_REG_SBC_CONFIG_CONTROL] = 0x06U;
        // Partial networking defaults (data sheet table 87): CDR 101, PNDM 1, DLC 0, data masks all ones.
        reg[UJA113X_REG_PN_DATA_RATE]     = UJA113X_CDR_500K;
        reg[UJA113X_REG_PN_FRAME_CONTROL] = UJA113X_PNDM_MASK;
        std::memset(&reg[UJA113X_REG_PN_DATA_MASK_0], 0xFF, 8U);
    }

    bool transfer(uint32_t const tx, uint32_t& rx) override
    {
        if (spiFails)
        {
            return false;
        }
        uint8_t const address = static_cast<uint8_t>((tx >> 9U) & 0x7FU);
        bool const readOnly   = (tx & 0x100U) != 0U;
        uint8_t const data    = static_cast<uint8_t>(tx & 0xFFU);
        updateAnalog(); // status and conversion registers are up to date when they are read
        rx = (static_cast<uint32_t>(address) << 9U) | (tx & 0x100U) | readRegister(address);
        if (readOnly)
        {
            ++reads[address];
        }
        else
        {
            writtenAddresses.push_back(address);
            writeRegister(address, data);
            updateAnalog(); // a new configuration takes effect right away
        }
        return true;
    }

    uint8_t global() const
    {
        uint8_t g = 0U;
        for (uint8_t i = 0U; i < 7U; ++i)
        {
            if (reg[0x61U + i] != 0U)
            {
                g = static_cast<uint8_t>(g | (1U << i));
            }
        }
        return g;
    }

    uint8_t mode() const { return reg[UJA113X_REG_MODE_CONTROL] & UJA113X_MC_MASK; }

    bool forcedSleep = false;

    /* MCU reset seen on RSTN (JP1 closed): counted in RCC; with RCC = 3 the SBC goes through forced
     * sleep preparation (LHC) into Sleep instead (MTPNV SLPC = 0), the MCU loses its supply.
     * Returns false in that case. */
    bool rstnReset()
    {
        uint8_t const failSafe = reg[UJA113X_REG_FAIL_SAFE_CONTROL];
        uint8_t const rcc      = failSafe & UJA113X_RCC_MASK;
        if (rcc == 3U)
        {
            reg[UJA113X_REG_FAIL_SAFE_CONTROL] = static_cast<uint8_t>(failSafe | UJA113X_LHC_MASK);
            forcedSleep = true;
            sleeping    = true;
            return false;
        }
        reg[UJA113X_REG_FAIL_SAFE_CONTROL]
            = static_cast<uint8_t>((failSafe & ~UJA113X_RCC_MASK) | (rcc + 1U));
        reg[UJA113X_REG_MAIN_STATUS] = UJA113X_NMS_MASK | UJA113X_RSS_RSTN_PULLED_DOWN;
        reg[UJA113X_REG_MODE_CONTROL] = UJA113X_MC_STANDBY;
        return true;
    }

    uint8_t rcc() const { return reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_RCC_MASK; }

    bool wroteAny(uint8_t const from, uint8_t const to) const
    {
        for (uint8_t a : writtenAddresses)
        {
            if ((a >= from) && (a <= to))
            {
                return true;
            }
        }
        return false;
    }

    /* ---- partial networking (COM-07), behaviour after the UJA113x data sheet section 7.9.1.3 ---- */

    struct BusFrame
    {
        uint32_t id;
        bool extended;
        bool remote;
        bool fd;
        uint8_t dlc;
        uint8_t data[8];
    };

    /* ---- battery monitor and HVIO (IO-10), data sheet sections 7.8.2 and 7.10 ---- */

    uint16_t batMv      = 13000U; // voltage at pin BAT
    uint16_t batSenseMv = 13000U; // voltage at pin BATSENSE
    uint16_t bathsMv[2] = {13000U, 13000U};
    int16_t hvioLoadMa[UJA113X_HVIO_COUNT]   = {};  // driver current, positive into the IC (low side)
    uint16_t hvioInputMv[UJA113X_HVIO_COUNT] = {};  // voltage at the pin seen by the wake-up comparator
    bool hvioLevel[UJA113X_HVIO_COUNT]       = {};  // last sampled level, for the edge detection
    bool enHigh    = false;                         // level of the EN pin as the model drives it
    bool limpLow   = false;                         // LIMP pin pulled low
    bool canShutDown = false;                       // transceiver switched off by the battery monitor
    bool v2ShutDown  = false;
    bool bmUnder = false, bmOver = false;           // latched comparator state (with hysteresis)

    uint16_t monitoredMv() const
    {
        return ((reg[UJA113X_REG_BM_SOURCE_CONTROL] & UJA113X_BMSC_MASK) != 0U) ? batSenseMv : batMv;
    }

    static uint16_t thresholdMv(uint8_t const code)
    {
        return static_cast<uint16_t>((static_cast<uint32_t>(code) * UJA113X_BM_FULL_SCALE_MV) / 255U);
    }

    static uint16_t hysteresisMv(uint8_t const code)
    {
        return static_cast<uint16_t>((static_cast<uint32_t>(code) * 4U * UJA113X_BM_FULL_SCALE_MV) / 255U);
    }

    static uint16_t adcCode(uint16_t const mv)
    {
        uint32_t const code = (static_cast<uint32_t>(mv) * UJA113X_BM_ADC_STEPS) / UJA113X_BM_FULL_SCALE_MV;
        return static_cast<uint16_t>((code > UJA113X_BM_ADC_STEPS) ? UJA113X_BM_ADC_STEPS : code);
    }

    uint8_t shutdownOf(uint8_t const field) const { return static_cast<uint8_t>(field & 0x03U); }

    bool shutdownActive(uint8_t const field) const
    {
        uint8_t const code = shutdownOf(field);
        return (bmUnder && ((code & UJA113X_SHUTDOWN_UV) != 0U))
               || (bmOver && ((code & UJA113X_SHUTDOWN_OV) != 0U));
    }

    /** Sets an interrupt status bit if its enable bit is set; \p wakeRss < 0xFF also wakes from Sleep. */
    void setInterrupt(
        uint8_t const address, uint8_t const enableAddress, uint8_t const bit, uint8_t const wakeRss = 0xFFU)
    {
        if ((reg[enableAddress] & bit) == 0U)
        {
            return;
        }
        reg[address] = static_cast<uint8_t>(reg[address] | bit);
        if (sleeping && (wakeRss != 0xFFU))
        {
            reg[UJA113X_REG_MAIN_STATUS]  = static_cast<uint8_t>(UJA113X_NMS_MASK | wakeRss);
            reg[UJA113X_REG_MODE_CONTROL] = UJA113X_MC_STANDBY;
            sleeping                      = false;
            ++wakeups;
        }
    }

    /* Everything the analogue world of the SBC does on its own; recomputed after every SPI access and
     * whenever a test changes a voltage or a load current. */
    void updateAnalog()
    {
        bool const normal = (mode() == UJA113X_MC_NORMAL);

        // ---- battery monitor: only enabled in Normal mode (data sheet 7.8.2) ----
        if (normal)
        {
            uint16_t const mv = monitoredMv();
            reg[UJA113X_REG_BM_VBAT_HIGH]  = static_cast<uint8_t>(adcCode(batMv) >> 2U);
            reg[UJA113X_REG_BM_VBAT_LOW]   = static_cast<uint8_t>(
                UJA113X_BM_ADC_READ_MASK | (adcCode(batMv) & UJA113X_BM_ADC_LSB_MASK));
            reg[UJA113X_REG_BM_VBATSENSE_HIGH] = static_cast<uint8_t>(adcCode(batSenseMv) >> 2U);
            reg[UJA113X_REG_BM_VBATSENSE_LOW]  = static_cast<uint8_t>(
                UJA113X_BM_ADC_READ_MASK | (adcCode(batSenseMv) & UJA113X_BM_ADC_LSB_MASK));

            uint16_t const uv = thresholdMv(reg[UJA113X_REG_BM_UV_THRESHOLD]);
            uint16_t const ov = thresholdMv(reg[UJA113X_REG_BM_OV_THRESHOLD]);
            uint16_t const uvRelease = static_cast<uint16_t>(
                uv + hysteresisMv(reg[UJA113X_REG_BM_HYSTERESIS] & UJA113X_BMHUC_MASK));
            uint16_t const ovHys = hysteresisMv(
                static_cast<uint8_t>((reg[UJA113X_REG_BM_HYSTERESIS] & UJA113X_BMHOC_MASK) >> 4U));
            uint16_t const ovRelease = static_cast<uint16_t>((ov > ovHys) ? (ov - ovHys) : 0U);

            bool const under = bmUnder ? (mv < uvRelease) : (mv < uv);
            bool const over  = bmOver ? (mv > ovRelease) : (mv > ov);
            if (under && !bmUnder)
            {
                setInterrupt(UJA113X_REG_SUPPLY_IRQ_STATUS, UJA113X_REG_SUPPLY_IRQ_ENABLE, UJA113X_BMUI_MASK);
            }
            if (over && !bmOver)
            {
                setInterrupt(UJA113X_REG_SUPPLY_IRQ_STATUS, UJA113X_REG_SUPPLY_IRQ_ENABLE, UJA113X_BMOI_MASK);
            }
            bmUnder = under;
            bmOver  = over;
        }
        else
        {
            bmUnder = false;
            bmOver  = false;
        }
        uint8_t supply = static_cast<uint8_t>(reg[UJA113X_REG_SUPPLY_STATUS]
                                              & ~(UJA113X_BMUVS_MASK | UJA113X_BMOVS_MASK));
        if (bmUnder) { supply = static_cast<uint8_t>(supply | UJA113X_BMUVS_MASK); }
        if (bmOver)  { supply = static_cast<uint8_t>(supply | UJA113X_BMOVS_MASK); }
        reg[UJA113X_REG_SUPPLY_STATUS] = supply;

        // ---- automatic reactions (CSC, V2SC, ENSC), only in Normal mode ----
        canShutDown = normal
                      && shutdownActive(static_cast<uint8_t>(
                          (reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) >> 2U));
        v2ShutDown = normal
                     && shutdownActive(static_cast<uint8_t>(
                         (reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2SC_MASK) >> UJA113X_V2SC_SHIFT));
        bool const enShutDown = normal
                                && shutdownActive(static_cast<uint8_t>(
                                    (reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENSC_MASK)
                                    >> UJA113X_ENSC_SHIFT));
        uint8_t const enc = static_cast<uint8_t>(
            (reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENC_MASK) >> UJA113X_ENC_SHIFT);
        enHigh = !enShutDown
                 && ((enc == UJA113X_ENC_HIGH_NORMAL_RESET_STANDBY)
                     || ((enc == UJA113X_ENC_HIGH_NORMAL) && normal));
        limpLow = (reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_LHC_MASK) != 0U;

        // ---- HVIOs ----
        for (uint8_t bank = 0U; bank < hvioBanks(); ++bank)
        {
            uint8_t const base       = static_cast<uint8_t>(UJA113X_REG_BANK0_HVIO_CONTROL + (bank * 0x10U));
            uint8_t const scReg      = static_cast<uint8_t>(UJA113X_REG_BANK0_SC_THRESHOLD + (bank * 0x10U));
            uint8_t const olReg      = static_cast<uint8_t>(UJA113X_REG_BANK0_OL_THRESHOLD + (bank * 0x10U));
            uint8_t const levelReg   = static_cast<uint8_t>(UJA113X_REG_BANK0_WAKE_LEVEL + (bank * 0x10U));
            uint8_t const statusReg  = static_cast<uint8_t>(UJA113X_REG_BANK0_DRIVER_STATUS + (bank * 0x10U));
            uint8_t const wakeIrqReg = static_cast<uint8_t>(UJA113X_REG_BANK0_WAKE_IRQ_ENABLE + (bank * 0x10U));
            uint8_t const failIrqReg = static_cast<uint8_t>(UJA113X_REG_BANK0_FAIL_IRQ_ENABLE + (bank * 0x10U));
            uint8_t const wakeStatus = static_cast<uint8_t>(UJA113X_REG_BANK0_WAKE_IRQ_STATUS + (bank * 0x02U));
            uint8_t const failStatus = static_cast<uint8_t>(UJA113X_REG_BANK0_FAIL_IRQ_STATUS + (bank * 0x02U));
            bool const absolute = (reg[static_cast<uint8_t>(UJA113X_REG_BANK0_WAKE_THRESHOLD + (bank * 0x10U))]
                                   & UJA113X_WTC_MASK)
                                  != 0U;
            uint8_t levels = 0U;
            uint8_t driverStatus = 0U;
            for (uint8_t i = 0U; i < UJA113X_HVIO_PER_BANK; ++i)
            {
                uint8_t const index   = static_cast<uint8_t>((bank * UJA113X_HVIO_PER_BANK) + i);
                uint8_t const control = reg[static_cast<uint8_t>(base + i)];
                uint8_t const cc      = static_cast<uint8_t>(control & UJA113X_IOCC_MASK);
                uint8_t const ac      = static_cast<uint8_t>((control & UJA113X_IOAC_MASK) >> UJA113X_IOAC_SHIFT);
                bool const shed       = normal
                                  && shutdownActive(static_cast<uint8_t>(
                                      (control & UJA113X_IOSC_MASK) >> UJA113X_IOSC_SHIFT));
                bool const input = (cc == UJA113X_IOCC_WAKE_INPUT) || (cc == UJA113X_IOCC_HS_WAKE_SLOPE)
                                   || (cc == UJA113X_IOCC_HS_WAKE);
                bool const highSide = (cc == UJA113X_IOCC_HS_SLOPE) || (cc == UJA113X_IOCC_HS)
                                      || (cc == UJA113X_IOCC_HS_WAKE_SLOPE) || (cc == UJA113X_IOCC_HS_WAKE);
                bool const lowSide  = (cc == UJA113X_IOCC_LS_SLOPE) || (cc == UJA113X_IOCC_LS);
                bool const active   = (cc != UJA113X_IOCC_OFF) && (ac != UJA113X_IOAC_OFF) && !shed;

                // Wake-up level and edges
                if (input && active)
                {
                    uint16_t const threshold
                        = absolute ? 3000U : static_cast<uint16_t>(bathsMv[bank] / 2U);
                    bool const level = hvioInputMv[index] > threshold;
                    if (level != hvioLevel[index])
                    {
                        uint8_t const bit = static_cast<uint8_t>(
                            1U << ((2U * i) + (level ? UJA113X_HVIO_RISING_SHIFT : UJA113X_HVIO_FALLING_SHIFT)));
                        // Reset source "HVIOn wake-up" is RSS 4 + index (data sheet table 6).
                        setInterrupt(wakeStatus, wakeIrqReg, bit, static_cast<uint8_t>(4U + index));
                        hvioLevel[index] = level;
                    }
                    if (level) { levels = static_cast<uint8_t>(levels | (1U << i)); }
                }
                else
                {
                    hvioLevel[index] = false;
                }

                // Driver diagnosis. A short circuit latches and switches the driver off until the
                // activation control goes back to 000 (data sheet 7.10.3).
                uint8_t previous = static_cast<uint8_t>((reg[statusReg] >> (2U * i)) & 0x03U);
                if ((cc == UJA113X_IOCC_OFF) || (ac == UJA113X_IOAC_OFF))
                {
                    previous = UJA113X_IODS_OFF; // reset of the latch
                }
                uint8_t state = previous;
                if (active && (highSide || lowSide) && (previous != UJA113X_IODS_SHORT_CIRCUIT))
                {
                    uint16_t const scMa = ::sbc::Uja113x::shortCircuitThresholdMa(
                        static_cast<uint8_t>((reg[scReg] >> (2U * i)) & 0x03U));
                    uint16_t const olMa = ::sbc::Uja113x::openLoadThresholdMa(
                        static_cast<uint8_t>((reg[olReg] >> (2U * i)) & 0x03U));
                    int32_t const current = lowSide ? hvioLoadMa[index] : -hvioLoadMa[index];
                    if (current >= static_cast<int32_t>(scMa))
                    {
                        state = UJA113X_IODS_SHORT_CIRCUIT;
                        setInterrupt(failStatus, failIrqReg,
                                     static_cast<uint8_t>(1U << ((2U * i) + UJA113X_HVIO_SHORT_CIRCUIT_SHIFT)));
                    }
                    else if (current <= static_cast<int32_t>(olMa))
                    {
                        if (state != UJA113X_IODS_OPEN_LOAD)
                        {
                            setInterrupt(failStatus, failIrqReg,
                                         static_cast<uint8_t>(1U << ((2U * i) + UJA113X_HVIO_OPEN_LOAD_SHIFT)));
                        }
                        state = UJA113X_IODS_OPEN_LOAD;
                    }
                    else
                    {
                        state = UJA113X_IODS_OK;
                    }
                }
                else if (!active || (!highSide && !lowSide))
                {
                    state = UJA113X_IODS_OFF;
                }
                driverStatus = static_cast<uint8_t>(driverStatus | (state << (2U * i)));
            }
            reg[levelReg]  = levels;
            reg[statusReg] = driverStatus;
        }
    }

    uint8_t hvioBanks() const
    {
        return ::sbc::Uja113x::hvioBankCount(reg[UJA113X_REG_IDENTIFICATION_0]);
    }

    /* ---- IO-68: pin INTN1 and Standby (data sheet 7.12) ---- */

    uint32_t intnHighUntilMs = 0U; // interrupt delay timer tto(int) after a clear
    uint8_t supplyIrqOnStandby = 0U; // fault injection: supply interrupt raised by the change to Standby

    /** INTN1 low: an interrupt pending and the delay timer after the last clear expired. */
    bool intn1Low() const { return (global() != 0U) && (nowMs >= intnHighUntilMs); }

    /** Bus activity while the SBC is in Standby: CAN wake-up interrupt if CWIE is set (no mode change). */
    bool standbyBusActivity()
    {
        if ((mode() != UJA113X_MC_STANDBY) || sleeping
            || ((reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U))
        {
            return false;
        }
        reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] |= UJA113X_CWI_MASK;
        return true;
    }

    /** A reset of the SBC itself in Standby: back to Standby with the enables at their power-up value 0. */
    void resetInStandby()
    {
        reg[UJA113X_REG_MODE_CONTROL]            = UJA113X_MC_STANDBY;
        reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE]  = 0U;
        reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE]   = 0U;
        reg[UJA113X_REG_MAIN_STATUS]             = UJA113X_NMS_MASK | UJA113X_RSS_RSTN_PULLED_DOWN;
    }

    bool lockTransceiverControl = false; // LK2C: writes to 0x20..0x2F ignored
    bool cpnsStuckLow           = false; // fault injection: SBC never confirms the configuration
    uint8_t pnErrorCounter      = 0U;
    uint16_t wakeups            = 0U;

    static bool isPnRegister(uint8_t const address)
    {
        return ((address >= UJA113X_REG_PN_DATA_RATE) && (address <= UJA113X_REG_PN_FRAME_CONTROL))
               || ((address >= UJA113X_REG_PN_DATA_MASK_0) && (address <= 0x6FU));
    }

    /* A frame on the bus while the SBC sleeps. Returns true if it woke the SBC. */
    bool busFrame(BusFrame const& frame)
    {
        if (!sleeping || forcedSleep
            || ((reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U))
        {
            return false;
        }
        uint8_t const canControl = reg[UJA113X_REG_CAN_CONTROL];
        bool const selective     = ((canControl & UJA113X_CPNC_MASK) != 0U) && ((canControl & UJA113X_PNCOK_MASK) != 0U);
        if (!selective)
        {
            return wake(UJA113X_CWI_MASK); // standard wake-up pattern: any bus activity
        }
        // COM-39: selective wake-up only with the transceiver in Offline, CMC = 00 (data sheet 7.9.1.3, last
        // paragraph: not Offline "(CMC != 00)" -> all wake-up patterns ignored). Board 2026-09-28 with CMC = 01
        // in Sleep: the standard wake-up (above) worked, with partial networking nothing woke the SBC - neither
        // the matching frame nor hundreds of error frames (no PNFDEI). The model keeps it deaf the same way.
        if ((canControl & UJA113X_CMC_MASK) != UJA113X_CMC_OFFLINE)
        {
            return false;
        }
        if (frame.fd)
        {
            if ((canControl & UJA113X_CFDC_MASK) != 0U)
            {
                return false; // FD passive: ignored, never a wake-up frame
            }
            if (++pnErrorCounter > 31U)
            {
                return wake(UJA113X_PNFDEI_MASK);
            }
            return false;
        }
        if (pnErrorCounter > 0U)
        {
            --pnErrorCounter;
        }
        uint8_t const frameControl = reg[UJA113X_REG_PN_FRAME_CONTROL];
        if (frame.extended != ((frameControl & UJA113X_IDE_MASK) != 0U))
        {
            return false;
        }
        uint32_t stored = 0U;
        uint32_t dontCare = 0U;
        for (uint8_t i = 0U; i < 4U; ++i)
        {
            stored |= static_cast<uint32_t>(reg[UJA113X_REG_PN_ID_0 + i]) << (8U * i);
            dontCare |= static_cast<uint32_t>(reg[UJA113X_REG_PN_ID_MASK_0 + i]) << (8U * i);
        }
        uint32_t const field  = frame.extended ? frame.id : (frame.id << 18U);
        uint32_t const width  = frame.extended ? 0x1FFFFFFFUL : (0x7FFUL << 18U);
        if (((field ^ stored) & ~dontCare & width) != 0U)
        {
            return false;
        }
        if ((frameControl & UJA113X_PNDM_MASK) != 0U)
        {
            uint8_t const expected = ((frameControl & 0x0FU) > 8U) ? 8U : (frameControl & 0x0FU);
            uint8_t const received = (frame.dlc > 8U) ? 8U : frame.dlc;
            if (frame.remote || (received != expected))
            {
                return false;
            }
            if (expected > 0U)
            {
                bool match = false;
                for (uint8_t i = 0U; i < expected; ++i)
                {
                    match = match || ((frame.data[i] & reg[UJA113X_REG_PN_DATA_MASK_0 + 8U - expected + i]) != 0U);
                }
                if (!match)
                {
                    return false;
                }
            }
        }
        return wake(UJA113X_CWI_MASK);
    }

private:
    bool wake(uint8_t const transceiverIrq)
    {
        reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] |= transceiverIrq;
        reg[UJA113X_REG_MAIN_STATUS]  = UJA113X_NMS_MASK | UJA113X_RSS_CAN_WAKEUP;
        reg[UJA113X_REG_MODE_CONTROL] = UJA113X_MC_STANDBY;
        sleeping = false;
        ++wakeups;
        return true;
    }

    uint8_t readRegister(uint8_t const address) const
    {
        if (address == UJA113X_REG_TRANSCEIVER_STATUS)
        {
            bool const pnOk   = ((reg[UJA113X_REG_CAN_CONTROL] & UJA113X_PNCOK_MASK) != 0U) && !cpnsStuckLow;
            bool const pnFdei = (reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] & UJA113X_PNFDEI_MASK) != 0U;
            uint8_t status    = reg[address];
            if (ctsFollowsCmc)
            {
                // COM-23: the transmitter is on in CAN Active mode only (CMC 01/10 in Normal mode, data sheet
                // 7.3.1); listen-only and offline switch it off at once
                uint8_t const cmc = reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CMC_MASK;
                bool const active = (mode() == UJA113X_MC_NORMAL)
                                    && ((cmc == UJA113X_CMC_ACTIVE) || (cmc == UJA113X_CMC_ACTIVE_NO_UV));
                status = static_cast<uint8_t>((status & ~UJA113X_CTS_MASK) | (active ? UJA113X_CTS_MASK : 0U));
            }
            return static_cast<uint8_t>(
                (status & ~(UJA113X_CPNERR_MASK | UJA113X_CPNS_MASK)) | (pnOk ? UJA113X_CPNS_MASK : 0U)
                | ((!pnOk || pnFdei) ? UJA113X_CPNERR_MASK : 0U));
        }
        return (address == UJA113X_REG_GLOBAL_IRQ_STATUS) ? global() : reg[address];
    }

    void watchdogWrite(uint8_t const data)
    {
        uint8_t const old = reg[UJA113X_REG_WATCHDOG_CONTROL];
        if ((mode() == UJA113X_MC_NORMAL) && ((old & 0xEFU) != (data & 0xEFU)))
        {
            systemReset = true; // data sheet 7.2, 7.3.3
            return;
        }
        uint8_t const wmc = static_cast<uint8_t>((data >> 5U) & 0x07U);
        uint32_t const period = ::sbc::Uja113x::watchdogPeriodMs(
            static_cast<::sbc::WatchdogPeriod>(data & UJA113X_NWP_MASK));
        if (triggeredOnce)
        {
            uint32_t const gap = nowMs - lastTriggerMs;
            if (gap > maxTriggerGapMs)
            {
                maxTriggerGapMs = gap;
            }
            // Window mode in Normal: trigger only between 55 % and 90 % of the period (10 % tolerance).
            // A second write right after a trigger lands in the closed window as well.
            if ((mode() == UJA113X_MC_NORMAL) && (wmc == UJA113X_WMC_WINDOW)
                && ((gap * 100U < period * 55U) || (gap * 100U > period * 90U)))
            {
                systemReset = true;
            }
        }
        triggeredOnce = true;
        lastTriggerMs = nowMs;
        reg[UJA113X_REG_WATCHDOG_CONTROL] = data;
    }

    void writeRegister(uint8_t const address, uint8_t const data)
    {
        if (lockTransceiverControl && (address >= UJA113X_REG_CAN_CONTROL) && (address <= 0x2FU))
        {
            reg[UJA113X_REG_SYSTEM_IRQ_STATUS] |= UJA113X_SPIFI_MASK; // locked: command ignored
            return;
        }
        if (address == UJA113X_REG_CAN_CONTROL)
        {
            reg[address] = static_cast<uint8_t>(data & 0x7FU); // bit 7 reserved
            return;
        }
        if (isPnRegister(address))
        {
            // Reserved bits read as 0; any write to the partial networking configuration clears PNCOK.
            uint8_t writable = 0xFFU;
            switch (address)
            {
                case UJA113X_REG_PN_DATA_RATE: writable = 0x07U; break;
                case 0x2AU:
                case 0x2EU: writable = 0x1FU; break;
                case UJA113X_REG_PN_FRAME_CONTROL: writable = 0xCFU; break;
                default: break;
            }
            reg[address] = static_cast<uint8_t>(data & writable);
            reg[UJA113X_REG_CAN_CONTROL] &= static_cast<uint8_t>(~UJA113X_PNCOK_MASK);
            return;
        }
        if ((address >= UJA113X_REG_SYSTEM_IRQ_STATUS) && (address <= 0x67U))
        {
            if ((reg[address] & data) != 0U)
            {
                intnHighUntilMs = nowMs + 1U; // IO-68: INTN1 released, held high for tto(int) (about 1 ms)
            }
            reg[address] = static_cast<uint8_t>(reg[address] & ~data); // write 1 to clear
            return;
        }
        switch (address)
        {
            case UJA113X_REG_WATCHDOG_CONTROL: watchdogWrite(data); return;
            case UJA113X_REG_MODE_CONTROL:
            {
                uint8_t const mc = data & UJA113X_MC_MASK;
                if ((mc != UJA113X_MC_SLEEP) && (mc != UJA113X_MC_STANDBY) && (mc != UJA113X_MC_NORMAL))
                {
                    reg[UJA113X_REG_SYSTEM_IRQ_STATUS] |= UJA113X_SPIFI_MASK;
                    return;
                }
                if (mc == UJA113X_MC_SLEEP)
                {
                    // Wake-up source: CAN wake-up, a bank 0 wake-up input, or a watchdog that keeps
                    // running in Sleep (cyclic wake-up, SYS-02). Without one the SBC resets.
                    uint8_t const sleepWmc = static_cast<uint8_t>(
                        (reg[UJA113X_REG_WATCHDOG_CONTROL] & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT);
                    bool const wakeSource = ((reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) != 0U)
                                            || (reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] != 0U)
                                            || (sleepWmc != UJA113X_WMC_AUTONOMOUS);
                    if ((global() != 0U) || !wakeSource)
                    {
                        systemReset = true; // illegal Sleep request
                        return;
                    }
                    sleeping                    = true;
                    transceiverIrqEnableAtSleep = reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE];
                    wakeIrqEnableAtSleep        = reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE];
                    watchdogControlAtSleep      = reg[UJA113X_REG_WATCHDOG_CONTROL];
                    canControlAtSleep           = reg[UJA113X_REG_CAN_CONTROL];
                }
                modeHistory.push_back(mc);
                reg[UJA113X_REG_MODE_CONTROL] = mc;
                if (mc == UJA113X_MC_STANDBY)
                {
                    reg[UJA113X_REG_SUPPLY_IRQ_STATUS] |= supplyIrqOnStandby;
                }
                return;
            }
            case UJA113X_REG_MAIN_STATUS:
            case UJA113X_REG_WATCHDOG_STATUS:
            case UJA113X_REG_SUPPLY_STATUS:
            case UJA113X_REG_TRANSCEIVER_STATUS:
            case UJA113X_REG_GLOBAL_IRQ_STATUS:
            // Battery monitor conversion results and the HVIO status registers are read only as well (IO-10).
            case UJA113X_REG_BM_VBAT_HIGH:
            case UJA113X_REG_BM_VBAT_LOW:
            case UJA113X_REG_BM_VBATSENSE_HIGH:
            case UJA113X_REG_BM_VBATSENSE_LOW:
            case UJA113X_REG_BANK0_WAKE_LEVEL:
            case UJA113X_REG_BANK0_DRIVER_STATUS:
            case UJA113X_REG_BANK1_WAKE_LEVEL:
            case UJA113X_REG_BANK1_DRIVER_STATUS: return;
            // Reserved bits read back as 0 - a driver that writes them would fail its read-back check.
            case UJA113X_REG_BM_SOURCE_CONTROL:
                reg[address] = static_cast<uint8_t>(data & UJA113X_BMSC_MASK);
                return;
            case UJA113X_REG_BANK0_WAKE_THRESHOLD:
            case UJA113X_REG_BANK1_WAKE_THRESHOLD:
                reg[address] = static_cast<uint8_t>(data & UJA113X_WTC_MASK);
                return;
            default: break;
        }
        // Registers of an HVIO bank the device does not have are reserved and stay 0 (data sheet 7.10.7).
        if ((address >= UJA113X_REG_BANK0_HVIO_CONTROL) && (address <= 0x4FU)
            && (((address - UJA113X_REG_BANK0_HVIO_CONTROL) / 0x10U) >= hvioBanks()))
        {
            return;
        }
        if (address >= UJA113X_REG_MTPNV_STATUS)
        {
            return; // MTPNV: the test fails through wroteAny() if the driver ever tries
        }
        reg[address] = data;
    }
};

::sbc::Uja113xConfig boardConfig()
{
    ::sbc::Uja113xConfig c{};
    c.identification       = BOARD_ID;
    c.startupControl       = 0x00U;
    c.sbcConfig            = 0x06U;
    c.watchdogMode         = ::sbc::WatchdogMode::AUTONOMOUS;
    c.watchdogPeriod       = ::sbc::WatchdogPeriod::MS_128;
    c.v2                   = ::sbc::V2Mode::ON_NORMAL_STANDBY_RESET;
    c.can                  = ::sbc::CanMode::ACTIVE;
    c.systemIrqEnable      = UJA113X_OTWIE_MASK | UJA113X_SPIFIE_MASK;
    c.supplyIrqEnable      = UJA113X_V1UI_MASK | UJA113X_VEXTUI_MASK | UJA113X_VEXTOI_MASK;
    c.transceiverIrqEnable = UJA113X_CFI_MASK;
    c.resetCounter         = 0U;
    return c;
}

using Status = ::sbc::Uja113x::Status;
using State  = ::sbc::Uja113x::State;

void runCyclic(::sbc::Uja113x& sbc, FakeUja113x& fake, uint32_t fromMs, uint32_t toMs)
{
    for (uint32_t t = fromMs; t <= toMs; t += 10U)
    {
        fake.nowMs = t;
        sbc.cyclic(t);
    }
}

void startupFromPowerOn()
{
    FakeUja113x fake;
    fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL]    = 0x02U; // RCC = 2 from earlier resets
    fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = UJA113X_CWI_MASK;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);

    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.getState() == State::NORMAL);
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    CHECK((fake.reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2C_MASK) == (UJA113X_V2C_ON_NORMAL_STANDBY_RESET << 2U));
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CMC_MASK) == UJA113X_CMC_ACTIVE);
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) == 0x04U); // shutdown control kept
    CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_RCC_MASK) == 0U);
    CHECK(fake.global() == 0U); // POSI and CWI cleared
    CHECK(sbc.getStartupInfo().interrupts.system == UJA113X_POSI_MASK);
    CHECK(sbc.getStartupInfo().interrupts.transceiver == UJA113X_CWI_MASK);
    CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
    CHECK(!sbc.getStartupInfo().mtpnvMismatch);
    CHECK(!fake.wroteAny(UJA113X_REG_MTPNV_STATUS, 0x7FU));
    CHECK(!fake.wroteAny(UJA113X_REG_WATCHDOG_CONTROL, UJA113X_REG_WATCHDOG_CONTROL)); // development mode: untouched
    CHECK(!fake.systemReset);
    CHECK(sbc.isCanTransceiverReady());
    CHECK(sbc.getResetSource() == UJA113X_RSS_EXIT_OFF);
    CHECK(sbc.getCounters().addressEchoMismatches == 0U);
    CHECK(sbc.getCounters().verifyErrors == 0U);
}

void wrongDeviceIsNotWritten()
{
    FakeUja113x fake;
    fake.reg[UJA113X_REG_IDENTIFICATION_0] = 0x49U;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::WRONG_DEVICE);
    CHECK(sbc.getState() == State::FAILED);
    CHECK(fake.writtenAddresses.empty());
}

void spiFailureIsRetried()
{
    FakeUja113x fake;
    fake.spiFails     = true;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::SPI_ERROR);
    CHECK(sbc.getState() == State::FAILED);
    fake.spiFails = false;
    sbc.cyclic(500U);
    CHECK(sbc.getCounters().startupAttempts == 1U);
    sbc.cyclic(1000U);
    CHECK(sbc.getCounters().startupAttempts == 2U);
    CHECK(sbc.getState() == State::NORMAL);
}

void mtpnvMismatchIsReportedNotProgrammed()
{
    FakeUja113x fake;
    fake.reg[UJA113X_REG_SBC_CONFIG_CONTROL] = 0x0AU; // e.g. forced normal mode after factory preset
    fake.reg[UJA113X_REG_MTPNV_STATUS]       = UJA113X_NVMPS_MASK;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.getStartupInfo().mtpnvMismatch);
    CHECK(!fake.wroteAny(UJA113X_REG_MTPNV_STATUS, 0x7FU));
}

void limpHomeIsReleased()
{
    FakeUja113x fake;
    fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = UJA113X_ENC_MASK | UJA113X_LHC_MASK | 0x03U;
    fake.reg[UJA113X_REG_MAIN_STATUS]       = UJA113X_RSS_WAKEUP_FROM_FSP;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.getStartupInfo().limpHomeWasActive);
    CHECK(fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] == UJA113X_ENC_MASK); // EN setting kept
    CHECK(sbc.getResetSource() == UJA113X_RSS_WAKEUP_FROM_FSP);
}

void canFailureInterrupt()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.isCanTransceiverReady());
    (void)sbc.takeInterruptEvents();

    fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = UJA113X_CFI_MASK;
    fake.reg[UJA113X_REG_TRANSCEIVER_STATUS]     = UJA113X_CTS_MASK | UJA113X_VCS_MASK;
    runCyclic(sbc, fake, 10U, 10U);
    CHECK(!sbc.isCanTransceiverReady());
    CHECK(fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] == 0U);
    CHECK(sbc.getCounters().canNotReady == 1U);
    auto const events = sbc.takeInterruptEvents();
    CHECK(events.transceiver == UJA113X_CFI_MASK);
    CHECK(!sbc.takeInterruptEvents().any());

    fake.reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK;
    runCyclic(sbc, fake, 20U, 120U); // regular 100 ms poll
    CHECK(sbc.isCanTransceiverReady());
}

void timeoutWatchdogConfiguredInStandby()
{
    FakeUja113x fake;
    fake.reg[UJA113X_REG_MODE_CONTROL] = UJA113X_MC_NORMAL; // bootloader left it in Normal mode
    auto config           = boardConfig();
    config.watchdogMode   = ::sbc::WatchdogMode::TIMEOUT;
    config.watchdogPeriod = ::sbc::WatchdogPeriod::MS_256;
    ::sbc::Uja113x sbc(fake, config);

    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(!fake.systemReset);
    CHECK(fake.reg[UJA113X_REG_WATCHDOG_CONTROL] == ((UJA113X_WMC_TIMEOUT << 5U) | UJA113X_NWP_256MS));
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    CHECK(fake.modeHistory.size() >= 2U);
    runCyclic(sbc, fake, 10U, 3000U);
    CHECK(!fake.systemReset);
    CHECK(fake.maxTriggerGapMs <= 256U);
    CHECK(sbc.getCounters().watchdogTriggers >= 10U);
}

void windowWatchdogHitsOpenWindow()
{
    FakeUja113x fake;
    auto config           = boardConfig();
    config.watchdogMode   = ::sbc::WatchdogMode::WINDOW;
    config.watchdogPeriod = ::sbc::WatchdogPeriod::MS_128;
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    runCyclic(sbc, fake, 10U, 5000U);
    CHECK(!fake.systemReset);
    CHECK(fake.maxTriggerGapMs * 100U <= 128U * 90U);
    CHECK(sbc.getCounters().watchdogTriggers >= 40U);
}

void autonomousWatchdogOutsideDevelopmentModeIsServed()
{
    FakeUja113x fake;
    fake.reg[UJA113X_REG_WATCHDOG_STATUS] = 0x01U; // SDMS = 0, watchdog running
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    runCyclic(sbc, fake, 10U, 2000U);
    CHECK(!fake.systemReset);
    CHECK(fake.maxTriggerGapMs <= 128U);
}

void windowWatchdogAfterMcuOnlyReset()
{
    FakeUja113x fake;
    // SBC kept running in Normal mode with a window watchdog of unknown phase.
    fake.reg[UJA113X_REG_MODE_CONTROL]     = UJA113X_MC_NORMAL;
    fake.reg[UJA113X_REG_WATCHDOG_STATUS]  = 0x01U;
    fake.reg[UJA113X_REG_WATCHDOG_CONTROL] = (UJA113X_WMC_WINDOW << 5U) | UJA113X_NWP_128MS;
    fake.triggeredOnce                     = true;
    fake.lastTriggerMs                     = 0U;
    fake.nowMs                             = 20U; // closed window
    auto config         = boardConfig();
    config.watchdogMode = ::sbc::WatchdogMode::WINDOW;
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(20U) == Status::OK);
    CHECK(!fake.systemReset);
    runCyclic(sbc, fake, 30U, 2000U);
    CHECK(!fake.systemReset);
}

void sleepAbortsOnPendingInterrupt()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
    CHECK(sbc.enterSleep(10U) == Status::INTERRUPT_PENDING);
    CHECK(!fake.sleeping);
    CHECK(!fake.systemReset);
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
    CHECK(sbc.getState() == State::NORMAL);
    runCyclic(sbc, fake, 20U, 20U);
    CHECK(sbc.takeInterruptEvents().supply == UJA113X_VEXTUI_MASK);
}

void sleepSequence()
{
    FakeUja113x fake;
    auto config           = boardConfig();
    config.watchdogMode   = ::sbc::WatchdogMode::TIMEOUT;
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    fake.modeHistory.clear();
    fake.nowMs = 50U;
    CHECK(sbc.enterSleep(50U) == Status::OK);
    CHECK(fake.sleeping);
    CHECK(!fake.systemReset);
    CHECK((fake.transceiverIrqEnableAtSleep & UJA113X_CWI_MASK) != 0U);
    CHECK(((fake.reg[UJA113X_REG_WATCHDOG_CONTROL] >> 5U) & 0x07U) == UJA113X_WMC_AUTONOMOUS);
    CHECK((fake.modeHistory.size() == 2U) && (fake.modeHistory[0] == UJA113X_MC_STANDBY)
          && (fake.modeHistory[1] == UJA113X_MC_SLEEP));
}

/* ---- SYS-02: the wake-up sources of the Sleep come from the caller ---- */

void sleepWithoutWakeupSourceIsRefused()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    fake.writtenAddresses.clear();
    CHECK(sbc.enterSleep(10U, ::sbc::SleepWakeup{false, 0U, false, config.watchdogPeriod})
          == Status::NOT_ALLOWED);
    CHECK(!fake.sleeping);
    CHECK(!fake.systemReset); // the driver refuses before the SBC could reset instead of sleeping
    CHECK(fake.writtenAddresses.empty());
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    CHECK(sbc.getState() == State::NORMAL);
}

void sleepWithWakeInputsAndCyclicWakeup()
{
    // WAKE inputs only: no CAN wake-up. `wakeInputs` is an HVIO input mask (IO-10): HVIO2 and HVIO4
    // without a configured edge are armed for both edges, which is 0xCC in register 0x37.
    {
        FakeUja113x fake;
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U); // startup switched them off
        CHECK(sbc.enterSleep(10U, ::sbc::SleepWakeup{false, 0x0AU, false, config.watchdogPeriod})
              == Status::OK);
        CHECK(fake.sleeping);
        CHECK(!fake.systemReset);
        CHECK(fake.wakeIrqEnableAtSleep == 0xCCU);
        CHECK((fake.transceiverIrqEnableAtSleep & UJA113X_CWI_MASK) == 0U);
    }
    // Cyclic wake-up: the watchdog stays on as timeout watchdog with the requested period
    {
        FakeUja113x fake;
        auto const config = boardConfig(); // development mode with autonomous watchdog: off in Normal
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK(sbc.enterSleep(10U, ::sbc::SleepWakeup{true, 0U, true, ::sbc::WatchdogPeriod::MS_1024})
              == Status::OK);
        CHECK(fake.sleeping);
        CHECK(!fake.systemReset); // WMC changed in Standby, not in Normal mode
        CHECK(((fake.watchdogControlAtSleep & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT)
              == UJA113X_WMC_TIMEOUT);
        CHECK((fake.watchdogControlAtSleep & UJA113X_NWP_MASK) == UJA113X_NWP_1024MS);
    }
    // Abort with a pending interrupt: WAKE inputs off again, watchdog back to the configuration, Normal
    {
        FakeUja113x fake;
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
        CHECK(sbc.enterSleep(10U, ::sbc::SleepWakeup{true, 0x01U, true, ::sbc::WatchdogPeriod::MS_64})
              == Status::INTERRUPT_PENDING);
        CHECK(!fake.sleeping);
        CHECK(!fake.systemReset);
        CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U);
        CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
        CHECK(fake.reg[UJA113X_REG_WATCHDOG_CONTROL]
              == static_cast<uint8_t>(
                  (UJA113X_WMC_AUTONOMOUS << UJA113X_WMC_SHIFT)
                  | static_cast<uint8_t>(config.watchdogPeriod)));
        CHECK(fake.mode() == UJA113X_MC_NORMAL);
        CHECK(sbc.getState() == State::NORMAL);
    }
}

void watchdogPeriodGrid()
{
    CHECK(::sbc::Uja113x::watchdogPeriodFor(0U) == ::sbc::WatchdogPeriod::MS_8);
    CHECK(::sbc::Uja113x::watchdogPeriodFor(8U) == ::sbc::WatchdogPeriod::MS_8);
    CHECK(::sbc::Uja113x::watchdogPeriodFor(9U) == ::sbc::WatchdogPeriod::MS_16);
    CHECK(::sbc::Uja113x::watchdogPeriodFor(64U) == ::sbc::WatchdogPeriod::MS_64);
    CHECK(::sbc::Uja113x::watchdogPeriodFor(257U) == ::sbc::WatchdogPeriod::MS_1024);
    CHECK(::sbc::Uja113x::watchdogPeriodFor(4096U) == ::sbc::WatchdogPeriod::MS_4096);
    CHECK(::sbc::Uja113x::watchdogPeriodFor(0xFFFFFFFFU) == ::sbc::WatchdogPeriod::MS_4096);
}

void protectedRegisters()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.writeRegister(UJA113X_REG_WATCHDOG_CONTROL, 0x44U) == Status::NOT_ALLOWED);
    CHECK(sbc.writeRegister(UJA113X_REG_MODE_CONTROL, UJA113X_MC_SLEEP) == Status::NOT_ALLOWED);
    CHECK(sbc.writeRegister(UJA113X_REG_MTPNV_CRC_CONTROL, 0xF6U) == Status::NOT_ALLOWED);
    CHECK(fake.writtenAddresses.empty());
    CHECK(sbc.writeRegister(UJA113X_REG_MEMORY_0, 0x5AU) == Status::OK);
    uint8_t value = 0U;
    CHECK(sbc.readRegister(UJA113X_REG_MEMORY_0, value) == Status::OK);
    CHECK(value == 0x5AU);
}

void sbcResetWhileMcuRuns()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    // SBC reset without MCU reset (JP1 open): registers back to defaults.
    fake.reg[UJA113X_REG_MODE_CONTROL]      = UJA113X_MC_STANDBY;
    fake.reg[UJA113X_REG_REGULATOR_CONTROL] = 0x00U;
    fake.reg[UJA113X_REG_CAN_CONTROL]       = 0x04U;
    runCyclic(sbc, fake, 10U, 1000U);
    CHECK(sbc.getCounters().modeLost == 1U);
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    CHECK((fake.reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2C_MASK) != 0U);
    CHECK(sbc.getState() == State::NORMAL);
}

void verifyErrorFailsStartup()
{
    // A register that does not take the value (e.g. SPI MOSI broken) must not end in NORMAL.
    class StuckRegulator : public FakeUja113x
    {
    public:
        bool transfer(uint32_t const tx, uint32_t& rx) override
        {
            bool const ok = FakeUja113x::transfer(tx, rx);
            reg[UJA113X_REG_REGULATOR_CONTROL] = 0x00U;
            return ok;
        }
    } fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::VERIFY_ERROR);
    CHECK(sbc.getState() == State::FAILED);
    CHECK(fake.mode() != UJA113X_MC_NORMAL);
}

void stuckSdoIsSpiError()
{
    // No SBC answering: SDO pulled high gives 0xFFFF, which must not look like a wrong device ID.
    class StuckSdo : public FakeUja113x
    {
    public:
        bool transfer(uint32_t const, uint32_t& rx) override
        {
            rx = 0xFFFFU;
            return true;
        }
    } fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::SPI_ERROR);
    CHECK(sbc.getCounters().addressEchoMismatches == 1U);
    CHECK(sbc.getState() == State::FAILED);
}

void lpspiDivider()
{
    using ::bsp::spi::calculateLpspiDivider;
    auto const d48 = calculateLpspiDivider(48000000UL, 500000UL);
    CHECK(d48.baudrateHz == 500000UL);
    CHECK((48000000UL >> d48.prescale) / (d48.sckdiv + 2UL) == 500000UL);
    auto const d40 = calculateLpspiDivider(40000000UL, 300000UL);
    CHECK(d40.baudrateHz <= 300000UL);
    CHECK(d40.baudrateHz > 290000UL);
    auto const slow = calculateLpspiDivider(48000000UL, 100UL);
    CHECK((slow.prescale == 7U) && (slow.sckdiv == 255U));
    for (uint32_t hz = 1500UL; hz < 20000000UL; hz = hz * 3UL / 2UL) // slowest possible: 1459 Hz
    {
        auto const d = calculateLpspiDivider(48000000UL, hz);
        CHECK(d.baudrateHz <= hz);
        CHECK((48000000UL >> d.prescale) / (d.sckdiv + 2UL) == d.baudrateHz);
    }
}

/* ---- SYS-07: RCC compensation for the MCU watchdog fast-test resets ---- */

using Compensation = ::sbc::ResetCounterCompensation;
using CompResult   = Compensation::Result;

bool onlyFailSafeWrittenSince(FakeUja113x const& fake, size_t const from)
{
    for (size_t i = from; i < fake.writtenAddresses.size(); ++i)
    {
        if (fake.writtenAddresses[i] != UJA113X_REG_FAIL_SAFE_CONTROL)
        {
            return false;
        }
    }
    return true;
}

/* MCU side of a start: bootloader (keeps WDOG_CS.TST), main() with the early compensation, OpenBSW
 * Watchdog::executeFastTest (test low -> reset, test high -> reset, then user mode TST = 1). */
struct McuModel
{
    uint8_t tst        = 0U;    // WDOG_CS.TST, 0 after power-on, kept over other resets
    bool watchdogReset = false; // RCM_SRS.WDOG of the last reset
    bool compensate    = true;  // false: behaviour before SYS-07
    std::vector<CompResult> results;
    uint8_t rccAtSbcStart = 0xFFU;
};

enum class Boot
{
    SBC_STARTED,
    FAILED_START, // reset before the SBC start (e.g. crash in the BSP initialization)
    FORCED_SLEEP, // SBC cut the MCU supply
};

bool mcuReset(FakeUja113x& fake, McuModel& mcu, bool const watchdog)
{
    mcu.watchdogReset = watchdog;
    return fake.rstnReset();
}

Boot boot(FakeUja113x& fake, McuModel& mcu, bool const failBeforeSbcStart)
{
    ::sbc::Uja113xConfig const config = boardConfig();
    for (;;)
    {
        if (mcu.compensate)
        {
            ::sbc::Uja113x early(fake, config);
            size_t const writes = fake.writtenAddresses.size();
            mcu.results.push_back(
                ::sbc::compensateWatchdogTestReset(early, mcu.watchdogReset, mcu.tst).result);
            CHECK(onlyFailSafeWrittenSince(fake, writes));
        }
        if (mcu.tst == ::sbc::MCU_WATCHDOG_TEST_LOW)
        {
            mcu.tst = ::sbc::MCU_WATCHDOG_TEST_HIGH;
            if (!mcuReset(fake, mcu, true))
            {
                return Boot::FORCED_SLEEP;
            }
            continue;
        }
        if (mcu.tst != ::sbc::MCU_WATCHDOG_TEST_HIGH)
        {
            mcu.tst = ::sbc::MCU_WATCHDOG_TEST_LOW;
            if (!mcuReset(fake, mcu, true))
            {
                return Boot::FORCED_SLEEP;
            }
            continue;
        }
        mcu.tst = 1U;
        if (failBeforeSbcStart)
        {
            return mcuReset(fake, mcu, false) ? Boot::FAILED_START : Boot::FORCED_SLEEP;
        }
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(fake.nowMs) == Status::OK);
        mcu.rccAtSbcStart = sbc.getStartupInfo().failSafeBefore & UJA113X_RCC_MASK;
        return Boot::SBC_STARTED;
    }
}

void compensationPowerOnStart()
{
    FakeUja113x fake;
    McuModel mcu;
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    CHECK(mcu.rccAtSbcStart == 0U);
    CHECK((mcu.results.size() == 3U) && (mcu.results[0] == CompResult::NOT_TEST_RESET)
          && (mcu.results[1] == CompResult::COMPENSATED) && (mcu.results[2] == CompResult::COMPENSATED));
    CHECK(!fake.forcedSleep);
    CHECK(!fake.wroteAny(UJA113X_REG_MTPNV_STATUS, 0x7FU));

    // Model check against the board measurement without compensation: RCC 2 after power-on.
    FakeUja113x before;
    McuModel old;
    old.compensate = false;
    CHECK(boot(before, old, false) == Boot::SBC_STARTED);
    CHECK(old.rccAtSbcStart == 2U);
}

void compensationMcuResetStart()
{
    FakeUja113x fake;
    McuModel mcu;
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    CHECK(mcuReset(fake, mcu, false)); // e.g. lc reboot
    mcu.results.clear();
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    CHECK(mcu.rccAtSbcStart == 1U);
    CHECK((mcu.results.size() == 3U) && (mcu.results[0] == CompResult::NOT_TEST_RESET));

    // Board measurement without compensation: RCC 3 after an MCU reset.
    FakeUja113x before;
    McuModel old;
    old.compensate = false;
    CHECK(boot(before, old, false) == Boot::SBC_STARTED);
    CHECK(mcuReset(before, old, false));
    CHECK(boot(before, old, false) == Boot::SBC_STARTED);
    CHECK(old.rccAtSbcStart == 3U);
}

void compensationKeepsRealWatchdogReset()
{
    FakeUja113x fake;
    McuModel mcu;
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    CHECK(mcu.tst == 1U);
    CHECK(mcuReset(fake, mcu, true)); // watchdog timeout in the running application
    mcu.results.clear();
    size_t const writes = fake.writtenAddresses.size();
    ::sbc::Uja113xConfig const config = boardConfig();
    ::sbc::Uja113x early(fake, config);
    Compensation const c = ::sbc::compensateWatchdogTestReset(early, true, 1U);
    CHECK(c.result == CompResult::NOT_TEST_RESET);
    CHECK(fake.writtenAddresses.size() == writes);
    CHECK(fake.rcc() == 1U);
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    CHECK(mcu.rccAtSbcStart == 1U);

    CHECK(!::sbc::isMcuWatchdogTestReset(false, ::sbc::MCU_WATCHDOG_TEST_LOW)); // e.g. pin reset during test
    CHECK(!::sbc::isMcuWatchdogTestReset(true, 0U));
    CHECK(::sbc::isMcuWatchdogTestReset(true, ::sbc::MCU_WATCHDOG_TEST_LOW));
    CHECK(::sbc::isMcuWatchdogTestReset(true, ::sbc::MCU_WATCHDOG_TEST_HIGH));
}

void compensationCounterZeroAndOtherBits()
{
    ::sbc::Uja113xConfig const config = boardConfig();
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = UJA113X_ENC_MASK | UJA113X_LHC_MASK; // RCC 0
        ::sbc::Uja113x sbc(fake, config);
        Compensation const c = ::sbc::compensateWatchdogTestReset(sbc, true, ::sbc::MCU_WATCHDOG_TEST_HIGH);
        CHECK(c.result == CompResult::COUNTER_ZERO);
        CHECK(fake.writtenAddresses.empty());
        CHECK(fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] == (UJA113X_ENC_MASK | UJA113X_LHC_MASK));
    }
    for (uint8_t rcc = 1U; rcc <= 3U; ++rcc)
    {
        FakeUja113x fake;
        uint8_t const others = UJA113X_ENSC_MASK | UJA113X_ENDC_MASK | UJA113X_LHC_MASK;
        fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = static_cast<uint8_t>(others | rcc);
        ::sbc::Uja113x sbc(fake, config);
        Compensation const c = ::sbc::compensateWatchdogTestReset(sbc, true, ::sbc::MCU_WATCHDOG_TEST_LOW);
        CHECK(c.result == CompResult::COMPENSATED);
        CHECK(fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] == static_cast<uint8_t>(others | (rcc - 1U)));
        CHECK(c.failSafeBefore == static_cast<uint8_t>(others | rcc));
        CHECK(c.failSafeAfter == fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL]);
        CHECK((fake.writtenAddresses.size() == 1U) && onlyFailSafeWrittenSince(fake, 0U));
    }
}

void compensationSpiErrors()
{
    ::sbc::Uja113xConfig const config = boardConfig();
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = 2U;
        fake.spiFails = true;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(::sbc::compensateWatchdogTestReset(sbc, true, 2U).result == CompResult::SPI_ERROR);
        CHECK(fake.writtenAddresses.empty());
        CHECK(fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] == 2U);
    }
    {
        // No SBC answering (SDO high): no write at all, even though 0xFF would look like RCC 3.
        class StuckSdo : public FakeUja113x
        {
        public:
            uint32_t frames = 0U;
            bool transfer(uint32_t const tx, uint32_t& rx) override
            {
                ++frames;
                if ((tx & UJA113X_SPI_RO_BIT) == 0U)
                {
                    writtenAddresses.push_back(static_cast<uint8_t>((tx >> 9U) & 0x7FU));
                }
                rx = 0xFFFFU;
                return true;
            }
        } fake;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(::sbc::compensateWatchdogTestReset(sbc, true, 3U).result == CompResult::SPI_ERROR);
        CHECK(fake.writtenAddresses.empty());
    }
    {
        // Echo fails only on the fail-safe register read: ID ok, still nothing written.
        class EchoFailsOnFailSafe : public FakeUja113x
        {
        public:
            bool transfer(uint32_t const tx, uint32_t& rx) override
            {
                bool const ok = FakeUja113x::transfer(tx, rx);
                if (((tx >> 9U) & 0x7FU) == UJA113X_REG_FAIL_SAFE_CONTROL)
                {
                    rx = 0x0003U;
                }
                return ok;
            }
        } fake;
        fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = 3U;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(::sbc::compensateWatchdogTestReset(sbc, true, 2U).result == CompResult::SPI_ERROR);
        CHECK(fake.writtenAddresses.empty());
        CHECK(fake.rcc() == 3U);
    }
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_IDENTIFICATION_0]  = 0x49U;
        fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = 2U;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(::sbc::compensateWatchdogTestReset(sbc, true, 2U).result == CompResult::WRONG_DEVICE);
        CHECK(fake.writtenAddresses.empty());
    }
    {
        // Register does not take the value: reported, not retried.
        class StuckFailSafe : public FakeUja113x
        {
        public:
            bool transfer(uint32_t const tx, uint32_t& rx) override
            {
                bool const ok = FakeUja113x::transfer(tx, rx);
                reg[UJA113X_REG_FAIL_SAFE_CONTROL] = 2U;
                return ok;
            }
        } fake;
        fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = 2U;
        ::sbc::Uja113x sbc(fake, config);
        Compensation const c = ::sbc::compensateWatchdogTestReset(sbc, true, 2U);
        CHECK(c.result == CompResult::VERIFY_ERROR);
        CHECK((c.failSafeBefore == 2U) && (c.failSafeAfter == 2U));
        CHECK(fake.writtenAddresses.size() == 1U);
    }
}

int failedStartsTolerated(bool const afterMcuReset, bool const compensate)
{
    FakeUja113x fake;
    McuModel mcu;
    mcu.compensate = compensate;
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    if (afterMcuReset)
    {
        CHECK(mcuReset(fake, mcu, false));
    }
    else
    {
        fake = FakeUja113x(); // power cycle: SBC off, MCU test field 0
        mcu  = McuModel();
        mcu.compensate = compensate;
    }
    // n failed starts in a row, then check whether a regular start still reaches the SBC start.
    for (int n = 0; n < 8; ++n)
    {
        FakeUja113x probeFake = fake;
        McuModel probeMcu     = mcu;
        if (boot(probeFake, probeMcu, false) != Boot::SBC_STARTED)
        {
            CHECK(probeFake.forcedSleep && ((probeFake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_LHC_MASK) != 0U));
            return n - 1;
        }
        if (boot(fake, mcu, true) == Boot::FORCED_SLEEP)
        {
            return n;
        }
    }
    return 99;
}

void compensationFailedStartsBeforeSbcStart()
{
    // Power-on: RCC 0, each failed start adds one real reset; the test resets need one step of
    // headroom (counted first, compensated in the next start). 2 failed starts are tolerated, after
    // the 3rd the next start ends in forced sleep. After an MCU reset (RCC 1) it is 1.
    CHECK(failedStartsTolerated(false, true) == 2);
    CHECK(failedStartsTolerated(true, true) == 1);
    // Before SYS-07: not a single failed start.
    CHECK(failedStartsTolerated(false, false) == 0);
    CHECK(failedStartsTolerated(true, false) == 0);

    // Board test sequence "sbc failstart 1": reset from the console, one failed start, regular start.
    FakeUja113x fake;
    McuModel mcu;
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    CHECK(mcuReset(fake, mcu, false));
    CHECK(boot(fake, mcu, true) == Boot::FAILED_START);
    CHECK(boot(fake, mcu, false) == Boot::SBC_STARTED);
    CHECK(mcu.rccAtSbcStart == 2U);
    CHECK(!fake.forcedSleep);
    // "sbc failstart 2": the second failed start ends in forced sleep.
    CHECK(mcuReset(fake, mcu, false));
    CHECK(boot(fake, mcu, true) == Boot::FAILED_START);
    Boot const second = boot(fake, mcu, true);
    Boot const next   = (second == Boot::FORCED_SLEEP) ? second : boot(fake, mcu, false);
    CHECK(next == Boot::FORCED_SLEEP);
    CHECK(fake.forcedSleep);
    CHECK(!fake.wroteAny(UJA113X_REG_MTPNV_STATUS, 0x7FU));
}

/* ---- COM-07: selective CAN wake-up (partial networking) ---- */

using BusFrame = FakeUja113x::BusFrame;

BusFrame standardFrame(uint32_t const id)
{
    BusFrame f{};
    f.id = id;
    return f;
}

BusFrame dataFrame(uint32_t const id, bool const extended, uint8_t const dlc, uint8_t const b0, uint8_t const b1)
{
    BusFrame f{};
    f.id       = id;
    f.extended = extended;
    f.dlc      = dlc;
    f.data[0]  = b0;
    f.data[1]  = b1;
    return f;
}

/* Test profile, not the eval board: 11 bit ID 0x1A0 with the three low bits don't care, 250 kbit/s. */
::sbc::CanWakeupFrame standardWakeupFrame()
{
    ::sbc::CanWakeupFrame w{};
    w.bitrate       = 250000UL;
    w.id            = 0x1A0UL;
    w.idCompareMask = 0x7F8UL;
    return w;
}

/* Test profile: 29 bit ID, DLC 2, node in group bit 2 of data byte 1, CAN FD passive. */
::sbc::CanWakeupFrame extendedWakeupFrame()
{
    ::sbc::CanWakeupFrame w{};
    w.bitrate       = 125000UL;
    w.id            = 0x18DA10F1UL;
    w.idCompareMask = 0x1FFFFFFFUL;
    w.extendedId    = true;
    w.evaluateData  = true;
    w.dlc           = 2U;
    w.dataMask[1]   = 0x04U;
    w.fdPassive     = true;
    return w;
}

/* Fresh SBC in Sleep with the given filter (nullptr: standard wake-up); returns whether \p frame woke it. */
bool wakesOn(::sbc::CanWakeupFrame const* const filter, BusFrame const& frame)
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.setCanWakeupFrame(filter) == Status::OK);
    CHECK(sbc.enterSleep(10U) == Status::OK);
    CHECK(fake.sleeping && !fake.systemReset);
    return fake.busFrame(frame);
}

void partialNetworkingRegisters()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & (UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK)) == 0U);
    static ::sbc::CanWakeupFrame const standard = standardWakeupFrame();
    CHECK(sbc.setCanWakeupFrame(&standard) == Status::OK);
    CHECK(sbc.getCanWakeupFrame() == &standard);
    fake.writtenAddresses.clear();
    CHECK(sbc.enterSleep(10U) == Status::OK);
    CHECK(fake.sleeping);
    CHECK(!fake.systemReset);
    // Data sheet example: 0x1A0 in ID registers 2/3, the three don't care bits in bits 2..4 of mask register 2.
    CHECK(fake.reg[UJA113X_REG_PN_DATA_RATE] == UJA113X_CDR_250K);
    CHECK((fake.reg[0x27] == 0x00U) && (fake.reg[0x28] == 0x00U) && (fake.reg[0x29] == 0x80U)
          && (fake.reg[0x2A] == 0x06U));
    CHECK((fake.reg[0x2B] == 0x00U) && (fake.reg[0x2C] == 0x00U) && (fake.reg[0x2D] == 0x1CU)
          && (fake.reg[0x2E] == 0x00U));
    CHECK(fake.reg[UJA113X_REG_PN_FRAME_CONTROL] == 0x00U); // standard format, ID only
    uint8_t const canControl = fake.reg[UJA113X_REG_CAN_CONTROL];
    CHECK((canControl & (UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK)) == (UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK));
    CHECK((canControl & UJA113X_CFDC_MASK) == 0U);
    CHECK((canControl & UJA113X_CSC_MASK) == 0x04U); // shutdown control kept
    CHECK((canControl & UJA113X_CMC_MASK) == UJA113X_CMC_OFFLINE); // COM-39: Offline in Sleep
    CHECK(fake.canControlAtSleep == canControl);
    CHECK((fake.transceiverIrqEnableAtSleep & UJA113X_CWI_MASK) != 0U);
    CHECK(!fake.wroteAny(UJA113X_REG_MTPNV_STATUS, 0x7FU));

    // Extended frame with data field: right aligned data masks, IDE/PNDM/DLC, CAN FD passive.
    FakeUja113x fake2;
    ::sbc::Uja113x sbc2(fake2, config);
    CHECK(sbc2.startup(0U) == Status::OK);
    static ::sbc::CanWakeupFrame const extended = extendedWakeupFrame();
    CHECK(sbc2.setCanWakeupFrame(&extended) == Status::OK);
    CHECK(sbc2.enterSleep(10U) == Status::OK);
    CHECK(fake2.reg[UJA113X_REG_PN_DATA_RATE] == UJA113X_CDR_125K);
    CHECK((fake2.reg[0x27] == 0xF1U) && (fake2.reg[0x28] == 0x10U) && (fake2.reg[0x29] == 0xDAU)
          && (fake2.reg[0x2A] == 0x18U));
    CHECK((fake2.reg[0x2B] | fake2.reg[0x2C] | fake2.reg[0x2D] | fake2.reg[0x2E]) == 0U);
    CHECK(fake2.reg[UJA113X_REG_PN_FRAME_CONTROL] == (UJA113X_IDE_MASK | UJA113X_PNDM_MASK | 2U));
    CHECK((fake2.reg[0x6E] == 0x00U) && (fake2.reg[0x6F] == 0x04U));
    for (uint8_t k = 0U; k < 6U; ++k)
    {
        CHECK(fake2.reg[UJA113X_REG_PN_DATA_MASK_0 + k] == 0x00U);
    }
    CHECK((fake2.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CFDC_MASK) != 0U);
}

void partialNetworkingWakesOnlyOnConfiguredId()
{
    static ::sbc::CanWakeupFrame const standard = standardWakeupFrame();
    CHECK(wakesOn(&standard, standardFrame(0x1A0U)));
    CHECK(wakesOn(&standard, standardFrame(0x1A7U)));  // don't care bits
    CHECK(!wakesOn(&standard, standardFrame(0x1A8U)));
    CHECK(!wakesOn(&standard, standardFrame(0x0A0U)));
    CHECK(!wakesOn(&standard, standardFrame(0x3A0U)));
    CHECK(!wakesOn(&standard, dataFrame(0x1A0UL << 18U, true, 0U, 0U, 0U))); // same bits, extended format

    static ::sbc::CanWakeupFrame const extended = extendedWakeupFrame();
    CHECK(wakesOn(&extended, dataFrame(0x18DA10F1UL, true, 2U, 0x00U, 0x04U)));
    CHECK(wakesOn(&extended, dataFrame(0x18DA10F1UL, true, 2U, 0x01U, 0xFFU)));
    CHECK(!wakesOn(&extended, dataFrame(0x18DA10F0UL, true, 2U, 0x00U, 0x04U))); // exact ID
    CHECK(!wakesOn(&extended, dataFrame(0x18DA10F1UL, true, 2U, 0xFFU, 0xFBU))); // other groups only
    CHECK(!wakesOn(&extended, dataFrame(0x18DA10F1UL, true, 3U, 0x00U, 0x04U))); // DLC differs
    CHECK(!wakesOn(&extended, dataFrame(0x18DA10F1UL, false, 2U, 0x00U, 0x04U)));
    BusFrame remote = dataFrame(0x18DA10F1UL, true, 2U, 0x00U, 0x04U);
    remote.remote   = true;
    CHECK(!wakesOn(&extended, remote));

    // Without a filter any bus activity wakes (standard wake-up pattern), as before COM-07.
    CHECK(wakesOn(nullptr, standardFrame(0x123U)));
}

void partialNetworkingFdPassive()
{
    auto const config = boardConfig();
    BusFrame fd = dataFrame(0x18DA10F1UL, true, 2U, 0x00U, 0x04U);
    fd.fd       = true;
    for (bool const passive : {true, false})
    {
        FakeUja113x fake;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        static ::sbc::CanWakeupFrame frame;
        frame           = extendedWakeupFrame();
        frame.fdPassive = passive;
        CHECK(sbc.setCanWakeupFrame(&frame) == Status::OK);
        CHECK(sbc.enterSleep(10U) == Status::OK);
        bool woke = false;
        for (uint8_t i = 0U; (i < 40U) && !woke; ++i)
        {
            woke = fake.busFrame(fd); // a CAN FD frame is never a wake-up frame
        }
        if (passive)
        {
            CHECK(!woke);
            CHECK(fake.sleeping);
        }
        else
        {
            // FD frames count as errors: the error counter overflow wakes the SBC with PNFDEI.
            CHECK(woke);
            CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] & UJA113X_PNFDEI_MASK) != 0U);
        }
    }
}

void partialNetworkingRefusedAndAborted()
{
    auto config = boardConfig();
    // Invalid filters are refused, the previous one stays.
    {
        FakeUja113x fake;
        ::sbc::Uja113x sbc(fake, config);
        static ::sbc::CanWakeupFrame const good = standardWakeupFrame();
        CHECK(sbc.setCanWakeupFrame(&good) == Status::OK);
        static ::sbc::CanWakeupFrame bad[6];
        for (auto& b : bad)
        {
            b = extendedWakeupFrame();
        }
        bad[0].bitrate      = 83333UL;
        bad[1].extendedId   = false; // 0x18DA10F1 does not fit 11 bit
        bad[2].idCompareMask = 0x3FFFFFFFUL;
        bad[3].dlc          = 9U;
        bad[4].dataMask[1]  = 0U; // DLC 2 without any group bit: would never wake
        bad[5].bitrate      = 0UL;
        for (auto const& b : bad)
        {
            CHECK(!::sbc::Uja113x::isValidCanWakeupFrame(b));
            CHECK(sbc.setCanWakeupFrame(&b) == Status::NOT_ALLOWED);
        }
        CHECK(sbc.getCanWakeupFrame() == &good);
        CHECK(::sbc::Uja113x::isValidCanWakeupFrame(extendedWakeupFrame()));
        CHECK(sbc.setCanWakeupFrame(nullptr) == Status::OK);
        CHECK(sbc.getCanWakeupFrame() == nullptr);
    }
    // Device without partial networking (UJA1131HW/5V0): Sleep with a filter refused before any write.
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_IDENTIFICATION_0] = 0x11U;
        config.identification                  = 0x11U;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(!::sbc::Uja113x::supportsPartialNetworking(0x11U));
        CHECK(::sbc::Uja113x::supportsPartialNetworking(0x51U) && ::sbc::Uja113x::supportsPartialNetworking(0x60U));
        CHECK(sbc.startup(0U) == Status::OK);
        static ::sbc::CanWakeupFrame const standard = standardWakeupFrame();
        CHECK(sbc.setCanWakeupFrame(&standard) == Status::OK);
        fake.writtenAddresses.clear();
        CHECK(sbc.enterSleep(10U) == Status::NOT_ALLOWED);
        CHECK(fake.writtenAddresses.empty());
        CHECK(!fake.sleeping && (fake.mode() == UJA113X_MC_NORMAL));
        // WAKE inputs only: the filter does not matter
        CHECK(sbc.enterSleep(10U, ::sbc::SleepWakeup{false, 0x01U, false, config.watchdogPeriod}) == Status::OK);
        CHECK(fake.sleeping);
        config.identification = BOARD_ID;
    }
    // SBC does not confirm the configuration (transceiver control locked): no Sleep that would wake on anything.
    {
        FakeUja113x fake;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        static ::sbc::CanWakeupFrame const standard = standardWakeupFrame();
        CHECK(sbc.setCanWakeupFrame(&standard) == Status::OK);
        fake.lockTransceiverControl = true;
        uint16_t const verifyErrors = sbc.getCounters().verifyErrors;
        CHECK(sbc.enterSleep(10U) != Status::OK);
        CHECK(sbc.getCounters().verifyErrors > verifyErrors);
        CHECK(!fake.sleeping && !fake.systemReset);
        CHECK(fake.mode() == UJA113X_MC_NORMAL);
        CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
        CHECK(sbc.getState() == State::NORMAL);
    }
    // PNCOK reads back, but CPNS does not confirm: refused as well.
    {
        FakeUja113x fake;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        static ::sbc::CanWakeupFrame const standard = standardWakeupFrame();
        CHECK(sbc.setCanWakeupFrame(&standard) == Status::OK);
        fake.cpnsStuckLow = true;
        CHECK(sbc.enterSleep(10U) == Status::VERIFY_ERROR);
        CHECK(!fake.sleeping && (fake.mode() == UJA113X_MC_NORMAL));
        CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & (UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK)) == 0U);
        CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
    }
    // Pending interrupt: abort, partial networking off again, a new startup keeps it off.
    {
        FakeUja113x fake;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        static ::sbc::CanWakeupFrame const standard = standardWakeupFrame();
        CHECK(sbc.setCanWakeupFrame(&standard) == Status::OK);
        fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
        CHECK(sbc.enterSleep(10U) == Status::INTERRUPT_PENDING);
        CHECK(!fake.sleeping && !fake.systemReset);
        CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & (UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK | UJA113X_CFDC_MASK))
              == 0U);
        CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CMC_MASK) == UJA113X_CMC_ACTIVE);
        CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
        CHECK(fake.mode() == UJA113X_MC_NORMAL);
        fake.reg[UJA113X_REG_CAN_CONTROL] |= UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK; // left over, e.g. MCU reset
        CHECK(sbc.startup(20U) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & (UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK)) == 0U);
    }
}

/* ---- COM-39: board finding 2026-09-28, selective wake-up did not wake at all ---- */

bool isTransceiverArea(uint8_t const address)
{
    return ((address >= UJA113X_REG_CAN_CONTROL) && (address <= UJA113X_REG_PN_FRAME_CONTROL))
           || ((address >= UJA113X_REG_PN_DATA_MASK_0) && (address <= 0x6FU));
}

/* Sleep sequence: CWIE before partial networking, CAN control (CPNC, PNCOK, CMC = 00) is the last write into the
 * transceiver/PN area, CPNS read again after Standby, right before Sleep. */
void partialNetworkingSleepSequence()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    static ::sbc::CanWakeupFrame const extended = extendedWakeupFrame();
    CHECK(sbc.setCanWakeupFrame(&extended) == Status::OK);
    fake.writtenAddresses.clear();
    fake.modeHistory.clear();
    uint32_t const statusReads = fake.reads[UJA113X_REG_TRANSCEIVER_STATUS];
    CHECK(sbc.enterSleep(10U) == Status::OK);
    CHECK(fake.sleeping);
    auto const& w   = fake.writtenAddresses;
    size_t lastArea = w.size();
    size_t firstPn  = w.size();
    size_t cwie     = w.size();
    for (size_t i = 0U; i < w.size(); ++i)
    {
        if (isTransceiverArea(w[i]))
        {
            lastArea = i;
        }
        if ((firstPn == w.size()) && (w[i] >= UJA113X_REG_PN_DATA_RATE) && isTransceiverArea(w[i]))
        {
            firstPn = i;
        }
        if ((cwie == w.size()) && (w[i] == UJA113X_REG_TRANSCEIVER_IRQ_ENABLE))
        {
            cwie = i;
        }
    }
    CHECK(lastArea < w.size());
    CHECK((lastArea < w.size()) && (w[lastArea] == UJA113X_REG_CAN_CONTROL));
    CHECK(cwie < firstPn);
    uint8_t const pnOn = UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK;
    CHECK((fake.canControlAtSleep & pnOn) == pnOn);
    CHECK((fake.canControlAtSleep & UJA113X_CMC_MASK) == UJA113X_CMC_OFFLINE);
    CHECK((fake.canControlAtSleep & UJA113X_CFDC_MASK) != 0U);
    // CPNS once after the configuration and once more before the Sleep command
    CHECK(fake.reads[UJA113X_REG_TRANSCEIVER_STATUS] >= statusReads + 2U);
    CHECK((fake.modeHistory.size() == 2U) && (fake.modeHistory[0] == UJA113X_MC_STANDBY)
          && (fake.modeHistory[1] == UJA113X_MC_SLEEP));

    // An HVIO input of a missing bank is refused before anything is written, also with a filter set.
    FakeUja113x fake2;
    ::sbc::Uja113x sbc2(fake2, config);
    CHECK(sbc2.startup(0U) == Status::OK);
    CHECK(sbc2.setCanWakeupFrame(&extended) == Status::OK);
    fake2.writtenAddresses.clear();
    CHECK(sbc2.enterSleep(10U, ::sbc::SleepWakeup{true, 0x10U, false, config.watchdogPeriod})
          == Status::NOT_ALLOWED);
    CHECK(fake2.writtenAddresses.empty());
    CHECK(!fake2.sleeping);
}

/* Board profile of the finding (only here, the other PN tests use their own profiles): ID 0x123, 500 kbit/s,
 * DLC 0 (project can_wakeup_frame with dlc 0, PNDM = 1) - the values "sbc pn arm" + "sbc dump" must show. */
::sbc::CanWakeupFrame boardFindingFrame()
{
    ::sbc::CanWakeupFrame w{};
    w.bitrate       = 500000UL;
    w.id            = 0x123UL;
    w.idCompareMask = 0x7FFUL;
    w.evaluateData  = true;
    w.dlc           = 0U;
    return w;
}

void partialNetworkingArmWithoutSleep()
{
    auto const config = boardConfig();
    // Refused: not initialized, no filter, device without partial networking - nothing written.
    {
        FakeUja113x fake;
        ::sbc::Uja113x sbc(fake, config);
        static ::sbc::CanWakeupFrame const frame = boardFindingFrame();
        CHECK(sbc.setCanWakeupFrame(&frame) == Status::OK);
        CHECK(sbc.armPartialNetworking() == Status::NOT_READY);
        CHECK(fake.writtenAddresses.empty());
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK(sbc.setCanWakeupFrame(nullptr) == Status::OK);
        fake.writtenAddresses.clear();
        CHECK(sbc.armPartialNetworking() == Status::NOT_ALLOWED);
        CHECK(fake.writtenAddresses.empty());
    }
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_IDENTIFICATION_0] = 0x11U;
        auto config11           = boardConfig();
        config11.identification = 0x11U;
        ::sbc::Uja113x sbc(fake, config11);
        CHECK(sbc.startup(0U) == Status::OK);
        static ::sbc::CanWakeupFrame const frame = boardFindingFrame();
        CHECK(sbc.setCanWakeupFrame(&frame) == Status::OK);
        fake.writtenAddresses.clear();
        CHECK(sbc.armPartialNetworking() == Status::NOT_ALLOWED);
        CHECK(fake.writtenAddresses.empty());
    }
    // Armed in Normal mode: registers as enterSleep() writes them, CAN stays active (CMC from the configuration).
    FakeUja113x fake;
    fake.ctsFollowsCmc = true;
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    static ::sbc::CanWakeupFrame const frame = boardFindingFrame();
    CHECK(sbc.setCanWakeupFrame(&frame) == Status::OK);
    CHECK(sbc.armPartialNetworking() == Status::OK);
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    CHECK(!fake.sleeping);
    CHECK(fake.reg[UJA113X_REG_PN_DATA_RATE] == 0x05U); // CDR 101 = 500 kbit/s (table 29)
    // 0x123 << 18: ID5..ID0 = 100011 in bits 7:2 of 0x29, ID10..ID6 = 00100 in bits 4:0 of 0x2A (table 30)
    CHECK((fake.reg[0x27] == 0x00U) && (fake.reg[0x28] == 0x00U) && (fake.reg[0x29] == 0x8CU)
          && (fake.reg[0x2A] == 0x04U));
    CHECK((fake.reg[0x2B] | fake.reg[0x2C] | fake.reg[0x2D] | fake.reg[0x2E]) == 0U); // all bits must match
    CHECK(fake.reg[UJA113X_REG_PN_FRAME_CONTROL] == 0x40U);                          // IDE 0, PNDM 1, DLC 0
    for (uint8_t k = 0U; k < UJA113X_PN_DATA_MASK_COUNT; ++k)
    {
        CHECK(fake.reg[UJA113X_REG_PN_DATA_MASK_0 + k] == 0x00U); // DLC 0: no data mask in use
    }
    // Board: CSC 00 (0x20 = 0x01 before) -> 0x31; the fake starts with CSC 01 -> 0x35.
    CHECK(fake.reg[UJA113X_REG_CAN_CONTROL] == 0x35U);
    uint8_t status = 0U;
    CHECK(sbc.readRegister(UJA113X_REG_TRANSCEIVER_STATUS, status) == Status::OK);
    CHECK((status & (UJA113X_CPNS_MASK | UJA113X_CPNERR_MASK)) == UJA113X_CPNS_MASK);
    CHECK((status & UJA113X_CTS_MASK) != 0U); // transmitter still on
    runCyclic(sbc, fake, 10U, 200U);
    CHECK(sbc.isCanTransceiverReady());
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_PNCOK_MASK) != 0U); // cyclic() leaves it alone

    // Then Sleep: 0x123 with DLC 0 wakes, other IDs, another DLC and a remote frame do not.
    CHECK(sbc.enterSleep(300U) == Status::OK);
    CHECK(fake.canControlAtSleep == 0x34U);
    CHECK(!fake.busFrame(standardFrame(0x124U)));
    CHECK(!fake.busFrame(standardFrame(0x323U)));
    CHECK(!fake.busFrame(dataFrame(0x123U, false, 1U, 0xFFU, 0U)));
    BusFrame remote = standardFrame(0x123U);
    remote.remote   = true;
    CHECK(!fake.busFrame(remote));
    CHECK(fake.sleeping);
    CHECK(fake.busFrame(standardFrame(0x123U)));
    CHECK(sbc.startup(400U) == Status::OK);
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & (UJA113X_CPNC_MASK | UJA113X_PNCOK_MASK)) == 0U);
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CMC_MASK) == UJA113X_CMC_ACTIVE);

    // Identifier only (no dlc in the project, PNDM 0): any DLC wakes.
    static ::sbc::CanWakeupFrame idOnly;
    idOnly              = boardFindingFrame();
    idOnly.evaluateData = false;
    CHECK(wakesOn(&idOnly, dataFrame(0x123U, false, 3U, 0U, 0U)));
    CHECK(!wakesOn(&idOnly, dataFrame(0x122U, false, 3U, 0U, 0U)));
}

/* ---- COM-08: latency of the transceiver status while CAN is active ---- */

/* Loss of VCAN and a transmitter that goes off raise no interrupt in the model (as nothing in AH1501 promises
 * one): with CAN active the driver sees both in the next 10 ms cycle, not up to 100 ms later. */
void transceiverStatusWithinOneCycleWhenCanActive()
{
    FakeUja113x fake;
    auto const config = boardConfig(); // CanMode::ACTIVE
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    runCyclic(sbc, fake, 10U, 50U);
    CHECK(sbc.isCanTransceiverReady());

    fake.reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK | UJA113X_VCS_MASK; // VCAN lost at t = 55 ms
    runCyclic(sbc, fake, 60U, 60U);
    CHECK(!sbc.isCanTransceiverReady());
    CHECK(sbc.getCounters().canNotReady == 1U);

    fake.reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK;
    runCyclic(sbc, fake, 70U, 70U);
    CHECK(sbc.isCanTransceiverReady());

    fake.reg[UJA113X_REG_TRANSCEIVER_STATUS] = 0U; // transmitter off (CTS = 0)
    runCyclic(sbc, fake, 80U, 80U);
    CHECK(!sbc.isCanTransceiverReady());
    CHECK(sbc.getCounters().canNotReady == 2U);

    fake.reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK | UJA113X_CFS_MASK; // TXD clamped dominant
    runCyclic(sbc, fake, 90U, 90U);
    CHECK(!sbc.isCanTransceiverReady());
    fake.reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK;
    runCyclic(sbc, fake, 100U, 100U);
    CHECK(sbc.isCanTransceiverReady());

    // Active mode without undervoltage detection keeps the transmitter on as well.
    FakeUja113x fake2;
    auto config2 = boardConfig();
    config2.can  = ::sbc::CanMode::ACTIVE_NO_UV;
    ::sbc::Uja113x sbc2(fake2, config2);
    CHECK(sbc2.startup(0U) == Status::OK);
    fake2.reg[UJA113X_REG_TRANSCEIVER_STATUS] = 0U;
    runCyclic(sbc2, fake2, 10U, 10U);
    CHECK(!sbc2.isCanTransceiverReady());
}

/* Exactly one status read per cycle with CAN active, also in the cycle a CAN failure interrupt reads it. */
void transceiverStatusReadOncePerCycle()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    uint32_t const afterStartup = fake.reads[UJA113X_REG_TRANSCEIVER_STATUS];
    runCyclic(sbc, fake, 10U, 1000U); // 100 cycles
    CHECK(fake.reads[UJA113X_REG_TRANSCEIVER_STATUS] - afterStartup == 100U);

    fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = UJA113X_CFI_MASK;
    uint32_t const before = fake.reads[UJA113X_REG_TRANSCEIVER_STATUS];
    runCyclic(sbc, fake, 1010U, 1010U);
    CHECK(fake.reads[UJA113X_REG_TRANSCEIVER_STATUS] - before == 1U);
    CHECK(sbc.takeInterruptEvents().transceiver == UJA113X_CFI_MASK);
}

/* Offline or listen-only: nobody may send, the 100 ms poll stays (no extra SPI load). */
void transceiverStatusSlowPollWhenCanNotActive()
{
    for (auto const mode : {::sbc::CanMode::OFFLINE, ::sbc::CanMode::LISTEN_ONLY})
    {
        FakeUja113x fake;
        auto config = boardConfig();
        config.can  = mode;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        uint32_t const afterStartup = fake.reads[UJA113X_REG_TRANSCEIVER_STATUS];
        runCyclic(sbc, fake, 10U, 1000U);
        CHECK(fake.reads[UJA113X_REG_TRANSCEIVER_STATUS] - afterStartup == 10U);

        fake.reg[UJA113X_REG_TRANSCEIVER_STATUS] = UJA113X_CTS_MASK | UJA113X_VCS_MASK;
        runCyclic(sbc, fake, 1010U, 1090U);
        CHECK(sbc.isCanTransceiverReady()); // not yet read
        runCyclic(sbc, fake, 1100U, 1100U);
        CHECK(!sbc.isCanTransceiverReady());
    }
}

/* COM-23: a write of the CAN control register (console "sbc write 0x20 ...") reads the transceiver status right after
 * it, so the readiness follows the transmitter at once and not up to one cycle later. */
void canControlWriteReadsTransceiverStatus()
{
    FakeUja113x fake;
    fake.ctsFollowsCmc = true;
    auto const config  = boardConfig(); // CanMode::ACTIVE
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.isCanTransceiverReady());
    uint8_t canControl = 0U;
    CHECK(sbc.readRegister(UJA113X_REG_CAN_CONTROL, canControl) == Status::OK);
    CHECK((canControl & UJA113X_CMC_MASK) == UJA113X_CMC_ACTIVE);

    // listen-only (the board test of COM-22): not ready before any cyclic() call, one status read
    uint32_t const before = fake.reads[UJA113X_REG_TRANSCEIVER_STATUS];
    uint8_t const listenOnly
        = static_cast<uint8_t>((canControl & static_cast<uint8_t>(~UJA113X_CMC_MASK)) | UJA113X_CMC_LISTEN_ONLY);
    CHECK(sbc.writeRegister(UJA113X_REG_CAN_CONTROL, listenOnly) == Status::OK);
    CHECK(!sbc.isCanTransceiverReady());
    CHECK((sbc.getTransceiverStatus() & UJA113X_CTS_MASK) == 0U);
    CHECK(fake.reads[UJA113X_REG_TRANSCEIVER_STATUS] - before == 1U);
    CHECK(sbc.getCounters().canNotReady == 1U);
    // the cycle keeps it (the driver does not write 0x20 back, only a restart of the SBC does)
    runCyclic(sbc, fake, 10U, 50U);
    CHECK(!sbc.isCanTransceiverReady());

    // back to active: ready at once
    CHECK(sbc.writeRegister(UJA113X_REG_CAN_CONTROL, canControl) == Status::OK);
    CHECK(sbc.isCanTransceiverReady());

    // other registers do not read the status; a refused write neither
    uint32_t const reads = fake.reads[UJA113X_REG_TRANSCEIVER_STATUS];
    CHECK(sbc.writeRegister(UJA113X_REG_MEMORY_0, 0x5AU) == Status::OK);
    CHECK(sbc.writeRegister(UJA113X_REG_MODE_CONTROL, UJA113X_MC_SLEEP) == Status::NOT_ALLOWED);
    CHECK(fake.reads[UJA113X_REG_TRANSCEIVER_STATUS] == reads);

    // not in Normal mode (startup failed): the write goes out, the readiness stays "not ready" and is not read
    FakeUja113x dead;
    dead.ctsFollowsCmc = true;
    dead.reg[UJA113X_REG_IDENTIFICATION_0] = 0x00U; // wrong device: startup refused
    ::sbc::Uja113x failed(dead, config);
    CHECK(failed.startup(0U) != Status::OK);
    uint32_t const deadReads = dead.reads[UJA113X_REG_TRANSCEIVER_STATUS];
    (void)failed.writeRegister(UJA113X_REG_CAN_CONTROL, canControl);
    CHECK(!failed.isCanTransceiverReady());
    CHECK(dead.reads[UJA113X_REG_TRANSCEIVER_STATUS] == deadReads);
}

/* ============ IO-10: HVIO, EN pin, LIMP and battery monitoring ============================== */

/** Board configuration with an armed battery monitor in physical units. */
::sbc::Uja113xConfig monitorConfig()
{
    auto c                            = boardConfig();
    c.supplyIrqEnable                 = static_cast<uint8_t>(
        c.supplyIrqEnable | UJA113X_BMUI_MASK | UJA113X_BMOI_MASK);
    c.battery.configure               = true;
    c.battery.source                  = ::sbc::BatteryMonitorSource::BAT;
    c.battery.undervoltageMv          = 9000U;
    c.battery.overvoltageMv           = 18000U;
    c.battery.undervoltageHysteresisMv = 500U;  // one hysteresis step
    c.battery.overvoltageHysteresisMv  = 1000U; // three steps, deliberately different from the other half
    c.battery.canShutdown             = ::sbc::SupplyShutdown::NONE;
    c.battery.v2Shutdown              = ::sbc::SupplyShutdown::NONE;
    return c;
}

/** Bank 0 with the four HVIO functions the driver can express. */
::sbc::Uja113xConfig hvioConfig()
{
    auto c                     = monitorConfig();
    auto& bank                 = c.hvio[0];
    bank.configure             = true;
    bank.wakeThreshold         = ::sbc::HvioWakeThreshold::ABSOLUTE;
    // HVIO1: low-side driver with slope control, load shedding on undervoltage
    bank.pin[0] = {::sbc::HvioFunction::LOW_SIDE, true, ::sbc::HvioActivation::ON,
                   ::sbc::SupplyShutdown::UNDERVOLTAGE, false, false, true, true, 60U, 10U};
    // HVIO2: biased switch input (high-side driver plus wake-up input), both edges wake
    bank.pin[1] = {::sbc::HvioFunction::HIGH_SIDE_AND_WAKE, false, ::sbc::HvioActivation::ON,
                   ::sbc::SupplyShutdown::NONE, true, true, true, false, 45U, 5U};
    // HVIO3: plain wake-up input, only the rising edge
    bank.pin[2] = {::sbc::HvioFunction::WAKE_INPUT, false, ::sbc::HvioActivation::ON,
                   ::sbc::SupplyShutdown::NONE, true, false, false, false, 0U, 0U};
    // HVIO4: not used
    bank.pin[3] = {};
    return c;
}

void hvioBankCountFromIdentification()
{
    using Sbc = ::sbc::Uja113x;
    // Data sheet table 78: HW variants have two banks, /FD/4 one, /FD/0 none.
    CHECK(Sbc::hvioBankCount(0x00U) == 2U);
    CHECK(Sbc::hvioBankCount(0x01U) == 2U);
    CHECK(Sbc::hvioBankCount(0x10U) == 2U);
    CHECK(Sbc::hvioBankCount(0x11U) == 2U);
    CHECK(Sbc::hvioBankCount(0x40U) == 1U);
    CHECK(Sbc::hvioBankCount(0x41U) == 1U);
    CHECK(Sbc::hvioBankCount(0x50U) == 1U);
    CHECK(Sbc::hvioBankCount(BOARD_ID) == 1U); // UJA1131HW/FD/5V/4 of the eval board
    CHECK(Sbc::hvioBankCount(0x60U) == 0U);
    CHECK(Sbc::hvioBankCount(0x71U) == 0U);
    // The driver reports what it found, not what a configuration claims
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.getHvioBanks() == 0U); // before the startup
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.getHvioBanks() == 1U);
}

void batteryThresholdConversion()
{
    using Sbc = ::sbc::Uja113x;
    // 20 V over 255 steps: 78.4 mV per step. The undervoltage threshold rounds up, the overvoltage one
    // down, so the SBC never reacts later than the project asked for.
    CHECK(Sbc::batteryThresholdCode(0U, true) == 0U);
    CHECK(Sbc::batteryThresholdCode(20000U, false) == 255U);
    CHECK(Sbc::batteryThresholdCode(25000U, false) == 255U); // above full scale: never triggers
    CHECK(Sbc::batteryThresholdCode(9000U, true) == 115U);   // 9000 * 255 / 20000 = 114.75 -> 115
    CHECK(Sbc::batteryThresholdCode(9000U, false) == 114U);
    CHECK(Sbc::batteryThresholdCode(18000U, false) == 229U); // 229.5 -> 229
    CHECK(Sbc::batteryThresholdCode(18000U, true) == 230U);
    CHECK(Sbc::batteryThresholdMv(115U) == 9020U);
    CHECK(Sbc::batteryThresholdMv(229U) == 17961U);
    CHECK(Sbc::batteryThresholdMv(255U) == 20000U);
    // Conservative in both directions: rounding up never lowers the undervoltage threshold and
    // rounding down never raises the overvoltage threshold.
    for (uint16_t mv = 0U; mv < 20000U; mv = static_cast<uint16_t>(mv + 37U))
    {
        CHECK(Sbc::batteryThresholdMv(Sbc::batteryThresholdCode(mv, true)) >= mv);
        CHECK(Sbc::batteryThresholdMv(Sbc::batteryThresholdCode(mv, false)) <= mv);
    }
    // Hysteresis grid: four threshold steps, rounded down, 15 codes
    CHECK(Sbc::batteryHysteresisCode(0U) == 0U);
    CHECK(Sbc::batteryHysteresisCode(313U) == 0U); // one step is 313.7 mV, not 313
    CHECK(Sbc::batteryHysteresisCode(314U) == 1U);
    CHECK(Sbc::batteryHysteresisCode(500U) == 1U);
    CHECK(Sbc::batteryHysteresisCode(1000U) == 3U);
    CHECK(Sbc::batteryHysteresisCode(20000U) == 15U);
    CHECK(Sbc::batteryHysteresisMv(1U) == 314U);
    CHECK(Sbc::batteryHysteresisMv(3U) == 941U);
    CHECK(Sbc::batteryHysteresisMv(15U) == 4706U);
    // The window is never wider than requested
    for (uint16_t mv = 0U; mv < 5000U; mv = static_cast<uint16_t>(mv + 13U))
    {
        CHECK(Sbc::batteryHysteresisMv(Sbc::batteryHysteresisCode(mv)) <= mv);
    }
    // Conversion results: 10 bit over the same 20 V
    CHECK(Sbc::batteryAdcMv(0U) == 0U);
    CHECK(Sbc::batteryAdcMv(1023U) == 20000U);
    CHECK(Sbc::batteryAdcMv(2000U) == 20000U); // clamped
    CHECK(Sbc::batteryAdcMv(665U) == 13001U);  // about 13 V
}

void hvioThresholdGrid()
{
    using Sbc = ::sbc::Uja113x;
    // Typical values of data sheet table 90; the grid value at or below the request is chosen.
    CHECK(Sbc::shortCircuitThresholdCode(0U) == 0U);   // below the smallest: the most sensitive one
    CHECK(Sbc::shortCircuitThresholdCode(29U) == 0U);
    CHECK(Sbc::shortCircuitThresholdCode(30U) == 0U);
    CHECK(Sbc::shortCircuitThresholdCode(44U) == 0U);
    CHECK(Sbc::shortCircuitThresholdCode(45U) == 1U);
    CHECK(Sbc::shortCircuitThresholdCode(89U) == 2U);
    CHECK(Sbc::shortCircuitThresholdCode(90U) == 3U);
    CHECK(Sbc::shortCircuitThresholdCode(1000U) == 3U);
    CHECK(Sbc::shortCircuitThresholdMa(0U) == 30U);
    CHECK(Sbc::shortCircuitThresholdMa(3U) == 90U);
    CHECK(Sbc::openLoadThresholdCode(1U) == 0U);
    CHECK(Sbc::openLoadThresholdCode(4U) == 0U);
    CHECK(Sbc::openLoadThresholdCode(5U) == 1U);
    CHECK(Sbc::openLoadThresholdCode(19U) == 2U);
    CHECK(Sbc::openLoadThresholdCode(20U) == 3U);
    CHECK(Sbc::openLoadThresholdMa(0U) == 2U);
    CHECK(Sbc::openLoadThresholdMa(3U) == 20U);
    // Never less sensitive than asked for
    for (uint16_t ma = 1U; ma < 200U; ++ma)
    {
        CHECK(Sbc::shortCircuitThresholdMa(Sbc::shortCircuitThresholdCode(ma)) <= ((ma < 30U) ? 30U : ma));
        CHECK(Sbc::openLoadThresholdMa(Sbc::openLoadThresholdCode(ma)) <= ((ma < 2U) ? 2U : ma));
    }
}

void batteryMonitorConfigured()
{
    FakeUja113x fake;
    auto const config = monitorConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);

    CHECK(fake.reg[UJA113X_REG_BM_UV_THRESHOLD] == 115U); // 9.0 V rounded up
    CHECK(fake.reg[UJA113X_REG_BM_OV_THRESHOLD] == 229U); // 18.0 V rounded down
    // BMHOC in the upper nibble (1000 mV -> three steps), BMHUC in the lower one (500 mV -> one step)
    CHECK(fake.reg[UJA113X_REG_BM_HYSTERESIS] == 0x31U);
    CHECK((fake.reg[UJA113X_REG_BM_SOURCE_CONTROL] & UJA113X_BMSC_MASK) == 0U); // pin BAT
    CHECK((fake.reg[UJA113X_REG_SUPPLY_IRQ_ENABLE] & (UJA113X_BMUI_MASK | UJA113X_BMOI_MASK))
          == (UJA113X_BMUI_MASK | UJA113X_BMOI_MASK));
    // The automatic shutdown responses are configured, not inherited: the power-up default of CSC is
    // "CAN off on undervoltage", which would silence the bus as soon as a threshold is armed.
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) == 0U);
    CHECK((fake.reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2SC_MASK) == 0U);
    CHECK(!fake.wroteAny(UJA113X_REG_MTPNV_STATUS, 0x7FU));

    auto const t = sbc.getBatteryThresholds();
    CHECK(t.undervoltageMv == 9020U);
    CHECK(t.overvoltageMv == 17961U);
    CHECK(t.undervoltageHysteresisMv == 314U);
    CHECK(t.overvoltageHysteresisMv == 941U);

    // Reading the measurement: 10 bit, both channels, plus the comparator status. Both voltages are
    // chosen so that the two least significant bits are not zero - a driver that only reads the eight
    // MSBs would land 40 mV lower.
    fake.batMv      = 13100U; // code 670 (LSBs 10) -> 13099 mV, without the LSBs it would be 13060 mV
    fake.batSenseMv = 13500U; // code 690 (LSBs 10) -> 13490 mV, without the LSBs 13451 mV
    ::sbc::Uja113x::BatteryVoltages v{};
    CHECK(sbc.readBatteryVoltages(v) == Status::OK);
    CHECK(v.batMv == 13099U);
    CHECK(v.batSenseMv == 13490U);
    CHECK(!v.undervoltage);
    CHECK(!v.overvoltage);

    // Without a startup there is nothing to read
    FakeUja113x other;
    ::sbc::Uja113x notStarted(other, config);
    CHECK(notStarted.readBatteryVoltages(v) == Status::NOT_READY);
}

void batteryMonitorEventsAndHysteresis()
{
    FakeUja113x fake;
    auto const config = monitorConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    runCyclic(sbc, fake, 10U, 50U);
    CHECK(!sbc.takeInterruptEvents().any());

    // Undervoltage: below 9020 mV the SBC reports BMUI and BMUVS
    fake.batMv = 8500U;
    runCyclic(sbc, fake, 60U, 80U);
    auto events = sbc.takeInterruptEvents();
    CHECK((events.supply & UJA113X_BMUI_MASK) != 0U);
    ::sbc::Uja113x::BatteryVoltages v{};
    CHECK(sbc.readBatteryVoltages(v) == Status::OK);
    CHECK(v.undervoltage);
    CHECK(!v.overvoltage);

    // Hysteresis: back above the threshold but inside the release window, still undervoltage
    fake.batMv = 9100U;
    runCyclic(sbc, fake, 90U, 110U);
    CHECK(sbc.readBatteryVoltages(v) == Status::OK);
    CHECK(v.undervoltage);
    fake.batMv = 9500U; // above 9020 + 314
    runCyclic(sbc, fake, 120U, 140U);
    CHECK(sbc.readBatteryVoltages(v) == Status::OK);
    CHECK(!v.undervoltage);

    // Overvoltage on the other end
    fake.batMv = 19000U;
    runCyclic(sbc, fake, 150U, 170U);
    events = sbc.takeInterruptEvents();
    CHECK((events.supply & UJA113X_BMOI_MASK) != 0U);
    CHECK(sbc.readBatteryVoltages(v) == Status::OK);
    CHECK(v.overvoltage);
    CHECK((v.batMv > 18800U) && (v.batMv < 19200U));

    // The monitored source is a configured value: on BATSENSE the same BAT voltage is no longer an event
    FakeUja113x senseFake;
    auto senseConfig    = monitorConfig();
    senseConfig.battery.source = ::sbc::BatteryMonitorSource::BATSENSE;
    ::sbc::Uja113x senseSbc(senseFake, senseConfig);
    CHECK(senseSbc.startup(0U) == Status::OK);
    CHECK((senseFake.reg[UJA113X_REG_BM_SOURCE_CONTROL] & UJA113X_BMSC_MASK) != 0U);
    senseFake.batMv      = 8000U;
    senseFake.batSenseMv = 13000U;
    runCyclic(senseSbc, senseFake, 10U, 40U);
    CHECK(senseSbc.readBatteryVoltages(v) == Status::OK);
    CHECK(!v.undervoltage);
}

void batteryShutdownIsAConfiguredDecision()
{
    // Default: nothing switches off on its own, the application decides
    {
        FakeUja113x fake;
        auto const config = monitorConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        fake.batMv = 8000U;
        runCyclic(sbc, fake, 10U, 40U);
        CHECK(!fake.canShutDown);
        CHECK(!fake.v2ShutDown);
        CHECK(fake.enHigh == false); // EN not configured here, stays as the register was
    }
    // A project that wants the automatic reaction gets it
    {
        FakeUja113x fake;
        auto config              = monitorConfig();
        config.battery.canShutdown = ::sbc::SupplyShutdown::UNDER_OR_OVERVOLTAGE;
        config.battery.v2Shutdown  = ::sbc::SupplyShutdown::OVERVOLTAGE;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) == 0x0CU);
        CHECK((fake.reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2SC_MASK) == 0x20U);
        fake.batMv = 8000U;
        runCyclic(sbc, fake, 10U, 40U);
        CHECK(fake.canShutDown);
        CHECK(!fake.v2ShutDown); // undervoltage, V2 only reacts to an overvoltage
        fake.batMv = 19000U;
        runCyclic(sbc, fake, 50U, 80U);
        CHECK(fake.v2ShutDown);
    }
    // Without a configured monitor the register value found is kept, as before IO-10
    {
        FakeUja113x fake;
        auto const config = boardConfig(); // battery.configure = false
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) == 0x04U);
        CHECK(!fake.wroteAny(UJA113X_REG_BM_SOURCE_CONTROL, UJA113X_REG_BM_HYSTERESIS));
    }
}

void hvioConfigurationRegisters()
{
    FakeUja113x fake;
    auto const config = hvioConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);

    // HVIO1 low side with slope control (IOnCC = 010), activation on (001), shed on undervoltage (01)
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL + 0U] == 0x4AU);
    // HVIO2 high side plus wake input without slope control (111), activation on, no shedding
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL + 1U] == 0x0FU);
    // HVIO3 wake-up input (011), activation on
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL + 2U] == 0x0BU);
    // HVIO4 off: every other bit is ignored by the SBC, the driver writes a clean 0
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL + 3U] == 0x00U);

    CHECK((fake.reg[UJA113X_REG_BANK0_WAKE_THRESHOLD] & UJA113X_WTC_MASK) == 1U); // absolute
    // Thresholds in mA on the grid: 60 mA -> code 2, 45 mA -> code 1, unused pins code 0
    CHECK(fake.reg[UJA113X_REG_BANK0_SC_THRESHOLD] == 0x06U);
    // open load 10 mA -> code 2, 5 mA -> code 1
    CHECK(fake.reg[UJA113X_REG_BANK0_OL_THRESHOLD] == 0x06U);
    // Fail interrupts only where there is a driver: HVIO1 short circuit and open load, HVIO2 short circuit
    CHECK(fake.reg[UJA113X_REG_BANK0_FAIL_IRQ_ENABLE] == 0x0BU);
    // Wake interrupts only where there is an input: HVIO2 both edges, HVIO3 rising only
    CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0x1CU);

    // Order: thresholds before the pin that uses them, so no driver switches on with the old ones
    size_t scIndex = 0U, pinIndex = 0U;
    for (size_t i = 0U; i < fake.writtenAddresses.size(); ++i)
    {
        if (fake.writtenAddresses[i] == UJA113X_REG_BANK0_SC_THRESHOLD) { scIndex = i; }
        if (fake.writtenAddresses[i] == UJA113X_REG_BANK0_HVIO_CONTROL) { pinIndex = i; }
    }
    CHECK((scIndex != 0U) && (pinIndex > scIndex));
    CHECK(!fake.wroteAny(UJA113X_REG_MTPNV_STATUS, 0x7FU));
    // Bank 1 does not exist on this device and is never touched
    CHECK(!fake.wroteAny(UJA113X_REG_BANK1_HVIO_CONTROL, 0x4FU));
}

void hvioAbsentOnDeviceWithoutHvio()
{
    FakeUja113x fake;
    fake.reg[UJA113X_REG_IDENTIFICATION_0] = 0x71U; // UJA1131HW/FD/5V/0: no HVIOs, no timers
    auto config            = hvioConfig();
    config.identification  = 0x71U;
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.getHvioBanks() == 0U);
    // Registers 0x30..0x4A are reserved on this variant: not one of them is written, not even 0x37
    CHECK(!fake.wroteAny(UJA113X_REG_BANK0_HVIO_CONTROL, 0x4FU));
    // A Sleep with a local wake-up input is refused instead of arming a reserved register
    ::sbc::Uja113x::HvioBankStatus status{};
    CHECK(sbc.readHvioStatus(0U, status) == Status::NOT_ALLOWED);
    CHECK(sbc.setHvioActivation(0U, ::sbc::HvioActivation::ON) == Status::NOT_ALLOWED);
    CHECK(sbc.enterSleep(10U, ::sbc::SleepWakeup{false, 0x01U, false, config.watchdogPeriod})
          == Status::NOT_ALLOWED);
    CHECK(!fake.sleeping);
    CHECK(!fake.systemReset);
}

void hvioDiagnosisAndRecovery()
{
    FakeUja113x fake;
    auto const config = hvioConfig();
    fake.hvioLoadMa[0] = 30; // HVIO1 low side: 30 mA, between open load (10 mA) and short circuit (60 mA)
    fake.hvioLoadMa[1] = -20; // HVIO2 high side: 20 mA out of the IC
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    runCyclic(sbc, fake, 10U, 50U);
    (void)sbc.takeInterruptEvents();

    ::sbc::Uja113x::HvioBankStatus status{};
    CHECK(sbc.readHvioStatus(0U, status) == Status::OK);
    CHECK(::sbc::Uja113x::hvioDriverStatus(status.driverStatus, 0U) == UJA113X_IODS_OK);
    CHECK(::sbc::Uja113x::hvioDriverStatus(status.driverStatus, 3U) == UJA113X_IODS_OFF); // HVIO4 unused

    // Open load below the configured 10 mA
    fake.hvioLoadMa[0] = 5;
    runCyclic(sbc, fake, 60U, 90U);
    auto events = sbc.takeInterruptEvents();
    CHECK((events.failure & (1U << UJA113X_HVIO_OPEN_LOAD_SHIFT)) != 0U);
    CHECK(events.failureBank1 == 0U);
    CHECK(sbc.readHvioStatus(0U, status) == Status::OK);
    CHECK(::sbc::Uja113x::hvioDriverStatus(status.driverStatus, 0U) == UJA113X_IODS_OPEN_LOAD);

    // Short circuit above the configured 60 mA: the SBC switches the driver off and latches
    fake.hvioLoadMa[0] = 100;
    runCyclic(sbc, fake, 100U, 130U);
    events = sbc.takeInterruptEvents();
    CHECK((events.failure & (1U << UJA113X_HVIO_SHORT_CIRCUIT_SHIFT)) != 0U);
    CHECK(sbc.readHvioStatus(0U, status) == Status::OK);
    CHECK(::sbc::Uja113x::hvioDriverStatus(status.driverStatus, 0U) == UJA113X_IODS_SHORT_CIRCUIT);

    // The latch survives a healthy load: only the way over activation 000 clears it (data sheet 7.10.3)
    fake.hvioLoadMa[0] = 30;
    runCyclic(sbc, fake, 140U, 170U);
    CHECK(sbc.readHvioStatus(0U, status) == Status::OK);
    CHECK(::sbc::Uja113x::hvioDriverStatus(status.driverStatus, 0U) == UJA113X_IODS_SHORT_CIRCUIT);
    CHECK(sbc.clearHvioFailure(0U) == Status::OK);
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL] == 0x4AU); // function and shedding kept
    CHECK(sbc.readHvioStatus(0U, status) == Status::OK);
    CHECK(::sbc::Uja113x::hvioDriverStatus(status.driverStatus, 0U) == UJA113X_IODS_OK);

    // Runtime switching keeps the configured function, slope and thresholds
    CHECK(sbc.setHvioActivation(0U, ::sbc::HvioActivation::OFF) == Status::OK);
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL] == 0x42U);
    CHECK(sbc.setHvioActivation(0U, ::sbc::HvioActivation::TIMER1) == Status::OK);
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL] == 0x52U);
    CHECK(fake.reg[UJA113X_REG_BANK0_SC_THRESHOLD] == 0x06U);
    // An HVIO the configuration does not use cannot be switched on by accident
    CHECK(sbc.setHvioActivation(3U, ::sbc::HvioActivation::ON) == Status::NOT_ALLOWED);
    CHECK(sbc.setHvioActivation(4U, ::sbc::HvioActivation::ON) == Status::NOT_ALLOWED); // bank 1 absent
}

void hvioInputsAndWakeFromSleep()
{
    FakeUja113x fake;
    auto const config = hvioConfig();
    fake.hvioLoadMa[1] = -20;
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);

    // Input levels against the absolute threshold of the bank
    fake.hvioInputMv[1] = 4000U; // HVIO2 high
    fake.hvioInputMv[2] = 1000U; // HVIO3 low
    runCyclic(sbc, fake, 10U, 40U);
    ::sbc::Uja113x::HvioBankStatus status{};
    CHECK(sbc.readHvioStatus(0U, status) == Status::OK);
    CHECK(status.wakeLevels == 0x02U);
    // The rising edge on HVIO2 was reported as an event in Normal mode (wake capability configured)
    auto events = sbc.takeInterruptEvents();
    CHECK((events.wake & (1U << ((2U * 1U) + UJA113X_HVIO_RISING_SHIFT))) != 0U);

    // A falling edge on HVIO3 is not reported: only the rising edge is configured there
    fake.hvioInputMv[2] = 4000U;
    runCyclic(sbc, fake, 50U, 70U);
    CHECK((sbc.takeInterruptEvents().wake & (1U << ((2U * 2U) + UJA113X_HVIO_RISING_SHIFT))) != 0U);
    fake.hvioInputMv[2] = 0U;
    runCyclic(sbc, fake, 80U, 100U);
    CHECK(!sbc.takeInterruptEvents().any());

    // Sleep with HVIO2 as wake-up source: the input mask becomes the two edge bits of that pin
    CHECK(sbc.enterSleep(110U, ::sbc::SleepWakeup{false, 0x02U, false, config.watchdogPeriod})
          == Status::OK);
    CHECK(fake.sleeping);
    CHECK(fake.wakeIrqEnableAtSleep == 0x0CU); // HVIO2 rising and falling, as configured
    // An edge on HVIO3 does not wake: it was not selected for this Sleep
    fake.hvioInputMv[2] = 4000U;
    fake.updateAnalog();
    CHECK(fake.sleeping);
    // An edge on HVIO2 does
    fake.hvioInputMv[1] = 0U;
    fake.updateAnalog();
    CHECK(!fake.sleeping);
    CHECK((fake.reg[UJA113X_REG_MAIN_STATUS] & UJA113X_RSS_MASK) == 5U); // HVIO2 wake-up
}

void enPinAndLimpHome()
{
    // EN and LIMP from the configuration
    {
        FakeUja113x fake;
        auto config              = boardConfig();
        config.enPin.configure   = true;
        config.enPin.mode        = ::sbc::EnMode::HIGH_NORMAL;
        config.enPin.driver      = ::sbc::EnDriver::PUSH_PULL;
        config.enPin.shutdown    = ::sbc::SupplyShutdown::NONE;
        config.resetCounter      = 1U;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        uint8_t const failSafe = fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL];
        CHECK(((failSafe & UJA113X_ENC_MASK) >> UJA113X_ENC_SHIFT) == UJA113X_ENC_HIGH_NORMAL);
        CHECK((failSafe & UJA113X_ENDC_MASK) == 0U);
        CHECK((failSafe & UJA113X_ENSC_MASK) == 0U);
        CHECK((failSafe & UJA113X_RCC_MASK) == 1U);
        CHECK((failSafe & UJA113X_LHC_MASK) == 0U);
        CHECK(fake.enHigh); // in Normal mode the pin is driven high
        CHECK(!fake.limpLow);

        // Runtime change keeps ENDC/ENSC and the reset counter
        CHECK(sbc.setEnMode(::sbc::EnMode::LOW) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENC_MASK) == 0U);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_RCC_MASK) == 1U);
        CHECK(!fake.enHigh);

        // LIMP under software control, RCC and EN untouched
        CHECK(!sbc.isLimpHomeActive());
        CHECK(sbc.setLimpHome(true) == Status::OK);
        CHECK(sbc.isLimpHomeActive());
        CHECK(fake.limpLow);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_RCC_MASK) == 1U);
        CHECK(sbc.setLimpHome(false) == Status::OK);
        CHECK(!fake.limpLow);
    }
    // Open drain plus shutdown on a battery event: EN goes low although ENC says high
    {
        FakeUja113x fake;
        auto config            = monitorConfig();
        config.enPin.configure = true;
        config.enPin.mode      = ::sbc::EnMode::HIGH_NORMAL_RESET_STANDBY;
        config.enPin.driver    = ::sbc::EnDriver::OPEN_DRAIN;
        config.enPin.shutdown  = ::sbc::SupplyShutdown::UNDER_OR_OVERVOLTAGE;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENDC_MASK) != 0U);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENSC_MASK) == UJA113X_ENSC_MASK);
        CHECK(fake.enHigh);
        fake.batMv = 8000U;
        runCyclic(sbc, fake, 10U, 40U);
        CHECK(!fake.enHigh);
    }
    // A project that asks for limp home right after the startup gets it, and the SBC keeps LHC
    {
        FakeUja113x fake;
        auto config              = boardConfig();
        config.limpHomeAtStartup = true;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_LHC_MASK) != 0U);
        CHECK(fake.limpLow);
        CHECK(sbc.isLimpHomeActive());
    }
    // Without a configured EN pin nothing changes, the behaviour before IO-10
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] = UJA113X_ENDC_MASK | 0x08U; // ENC = 01 from elsewhere
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENDC_MASK) != 0U);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENC_MASK) == 0x08U);
        CHECK(sbc.setEnMode(::sbc::EnMode::LOW) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_FAIL_SAFE_CONTROL] & UJA113X_ENDC_MASK) != 0U); // ENDC kept
    }
}

} // namespace

/* ============ IO-33: delayed battery shutdown, wake-up inputs from the board wiring ============ */

/** CAN undervoltage and V2 overvoltage switch-off, delayed by the driver. */
::sbc::Uja113xConfig delayedShutdownConfig(uint16_t const delayMs)
{
    auto c                      = monitorConfig(); // 9020 mV (+314), 17961 mV (-941)
    c.battery.canShutdown       = ::sbc::SupplyShutdown::UNDERVOLTAGE;
    c.battery.v2Shutdown        = ::sbc::SupplyShutdown::OVERVOLTAGE;
    c.battery.shutdownDelayMs   = delayMs;
    return c;
}

uint8_t canMode(FakeUja113x const& fake) { return fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CMC_MASK; }

uint8_t v2Mode(FakeUja113x const& fake)
{
    return static_cast<uint8_t>((fake.reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2C_MASK) >> UJA113X_V2C_SHIFT);
}

void batteryShutdownDelayed()
{
    FakeUja113x fake;
    fake.ctsFollowsCmc = true;
    auto const config  = delayedShutdownConfig(50U);
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    // With a delay the SBC itself must not react (it has no filter): CSC and V2SC stay 00
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) == 0U);
    CHECK((fake.reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2SC_MASK) == 0U);
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    CHECK(v2Mode(fake) == UJA113X_V2C_ON_NORMAL_STANDBY_RESET);
    runCyclic(sbc, fake, 10U, 50U);
    CHECK(sbc.isCanTransceiverReady());

    // IO-13: a dip shorter than the delay (inrush current of a load at KL30B) leaves CAN on
    fake.batMv = 8000U;
    runCyclic(sbc, fake, 60U, 100U); // event seen from 60 ms on, 40 ms < 50 ms
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    CHECK(!fake.canShutDown);
    CHECK(!sbc.isCanShutOffByBattery());
    fake.batMv = 12000U;
    runCyclic(sbc, fake, 110U, 120U);
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    CHECK(sbc.getCounters().supplyShutdowns == 0U);

    // A lasting undervoltage switches CAN off exactly after the delay; V2 only reacts to an overvoltage
    fake.batMv = 8000U;
    runCyclic(sbc, fake, 130U, 170U);
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE); // 40 ms
    runCyclic(sbc, fake, 180U, 180U);             // 50 ms
    CHECK(canMode(fake) == UJA113X_CMC_OFFLINE);
    CHECK(sbc.isCanShutOffByBattery());
    CHECK(!sbc.isCanTransceiverReady());           // the CAN system sees it in the same call
    CHECK(v2Mode(fake) == UJA113X_V2C_ON_NORMAL_STANDBY_RESET);
    CHECK(sbc.getCounters().supplyShutdowns == 1U);

    // Inside the release window of the SBC it stays off, above it CAN is on again at once
    fake.batMv = 9100U;
    runCyclic(sbc, fake, 190U, 260U);
    CHECK(canMode(fake) == UJA113X_CMC_OFFLINE);
    fake.batMv = 12000U;
    runCyclic(sbc, fake, 270U, 270U);
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    CHECK(!sbc.isCanShutOffByBattery());
    CHECK(sbc.isCanTransceiverReady());
    CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) == 0U);

    // Overvoltage: V2 off after the delay, CAN stays on
    fake.batMv = 19000U;
    runCyclic(sbc, fake, 280U, 320U);
    CHECK(v2Mode(fake) == UJA113X_V2C_ON_NORMAL_STANDBY_RESET);
    runCyclic(sbc, fake, 330U, 330U);
    CHECK(v2Mode(fake) == UJA113X_V2C_OFF);
    CHECK(sbc.isV2ShutOffByBattery());
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    CHECK(sbc.getCounters().supplyShutdowns == 2U);
    fake.batMv = 12000U;
    runCyclic(sbc, fake, 340U, 340U);
    CHECK(v2Mode(fake) == UJA113X_V2C_ON_NORMAL_STANDBY_RESET);
    CHECK(!sbc.isV2ShutOffByBattery());

    // A new startup (e.g. after an SBC reset) writes CAN as configured and starts the delay over
    fake.batMv = 8000U;
    runCyclic(sbc, fake, 350U, 400U);
    CHECK(canMode(fake) == UJA113X_CMC_OFFLINE);
    CHECK(sbc.startup(410U) == Status::OK);
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    CHECK(!sbc.isCanShutOffByBattery());
    runCyclic(sbc, fake, 420U, 460U);
    CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    runCyclic(sbc, fake, 470U, 470U);
    CHECK(canMode(fake) == UJA113X_CMC_OFFLINE);
}

void batteryShutdownDelayCosts()
{
    // Delay without a switch-off: no supply status reads in cyclic() (no SPI frame more than before IO-33)
    {
        FakeUja113x fake;
        auto config                    = monitorConfig();
        config.battery.shutdownDelayMs = 100U;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        uint32_t const before = fake.reads[UJA113X_REG_SUPPLY_STATUS];
        fake.batMv            = 8000U;
        runCyclic(sbc, fake, 10U, 300U);
        CHECK(fake.reads[UJA113X_REG_SUPPLY_STATUS] == before);
        CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
    }
    // Without a delay the SBC switches: one conversion, no driver involved (the hardware path of IO-10)
    {
        FakeUja113x fake;
        auto const config = delayedShutdownConfig(0U);
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK((fake.reg[UJA113X_REG_CAN_CONTROL] & UJA113X_CSC_MASK) == (UJA113X_SHUTDOWN_UV << 2U));
        CHECK((fake.reg[UJA113X_REG_REGULATOR_CONTROL] & UJA113X_V2SC_MASK)
              == (UJA113X_SHUTDOWN_OV << UJA113X_V2SC_SHIFT));
        uint32_t const before = fake.reads[UJA113X_REG_SUPPLY_STATUS];
        fake.batMv            = 8000U;
        fake.updateAnalog();
        CHECK(fake.canShutDown); // at once, before any cyclic() call
        runCyclic(sbc, fake, 10U, 100U);
        CHECK(fake.reads[UJA113X_REG_SUPPLY_STATUS] == before);
        CHECK(!sbc.isCanShutOffByBattery());
    }
    // A jump from under- to overvoltage restarts the delay, but does not switch CAN on in between
    {
        FakeUja113x fake;
        auto config                = delayedShutdownConfig(50U);
        config.battery.canShutdown = ::sbc::SupplyShutdown::UNDER_OR_OVERVOLTAGE;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        fake.batMv = 8000U;
        runCyclic(sbc, fake, 10U, 60U);
        CHECK(canMode(fake) == UJA113X_CMC_OFFLINE);
        fake.batMv = 19000U;
        runCyclic(sbc, fake, 70U, 70U);
        CHECK(canMode(fake) == UJA113X_CMC_OFFLINE);
        CHECK(sbc.isCanShutOffByBattery());
        CHECK(sbc.getCounters().supplyShutdowns == 1U);
    }
    // An SPI error while reading the status switches nothing and is decided again in the next call
    {
        FakeUja113x fake;
        auto const config = delayedShutdownConfig(20U);
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        fake.batMv = 8000U;
        runCyclic(sbc, fake, 10U, 20U);
        fake.spiFails = true;
        runCyclic(sbc, fake, 30U, 60U);
        fake.spiFails = false;
        CHECK(canMode(fake) == UJA113X_CMC_ACTIVE);
        runCyclic(sbc, fake, 70U, 70U);
        CHECK(canMode(fake) == UJA113X_CMC_OFFLINE);
    }
}

void wakeInputsFromConfiguration()
{
    // The inputs a configuration sets up as wake-up inputs: configured bank, wake function, input sampled
    auto c                 = monitorConfig();
    CHECK(::sbc::Uja113x::configuredWakeInputs(c) == 0U);
    c.hvio[0].configure    = true;
    c.hvio[0].pin[0]       = {::sbc::HvioFunction::LOW_SIDE, false, ::sbc::HvioActivation::ON,
                              ::sbc::SupplyShutdown::NONE, false, false, false, false, 60U, 10U};
    c.hvio[0].pin[1]       = {::sbc::HvioFunction::WAKE_INPUT, false, ::sbc::HvioActivation::ON,
                              ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U};
    c.hvio[0].pin[2]       = {::sbc::HvioFunction::HIGH_SIDE_AND_WAKE, false, ::sbc::HvioActivation::TIMER1,
                              ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U};
    c.hvio[0].pin[3]       = {::sbc::HvioFunction::WAKE_INPUT, false, ::sbc::HvioActivation::OFF,
                              ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U};
    CHECK(::sbc::Uja113x::configuredWakeInputs(c) == 0x06U); // HVIO2, HVIO3; HVIO4 never sampled
    c.hvio[1].configure = true;
    c.hvio[1].pin[3]    = c.hvio[0].pin[1];
    CHECK(::sbc::Uja113x::configuredWakeInputs(c) == 0x86U); // HVIO8 of bank 1
    c.hvio[0].configure = false;
    CHECK(::sbc::Uja113x::configuredWakeInputs(c) == 0x80U); // a bank not configured offers nothing
}

void boardWakeInputsWakeFromSleep()
{
    // The port's configuration of a pin the board wires as wake_input (bsp/sbc/sbcConfiguration.h, IO-33):
    // wake-up input sampled permanently, no edge named. Here HVIO2..HVIO4 as on a board with three wake inputs.
    auto c              = monitorConfig();
    c.hvio[0].configure = true;
    c.hvio[0].wakeThreshold = ::sbc::HvioWakeThreshold::ABSOLUTE;
    ::sbc::HvioConfig const wake{::sbc::HvioFunction::WAKE_INPUT, false, ::sbc::HvioActivation::ON,
                                 ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U};
    c.hvio[0].pin[1] = wake;
    c.hvio[0].pin[2] = wake;
    c.hvio[0].pin[3] = wake;
    FakeUja113x fake;
    ::sbc::Uja113x sbc(fake, c);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL] == 0U); // HVIO1 not wired: not configured
    CHECK(fake.reg[UJA113X_REG_BANK0_HVIO_CONTROL + 1U]
          == static_cast<uint8_t>((UJA113X_IOAC_ON << UJA113X_IOAC_SHIFT) | UJA113X_IOCC_WAKE_INPUT));
    CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U); // nothing armed in Normal mode (AH1501 2.2.3)
    CHECK((fake.reg[UJA113X_REG_BANK0_WAKE_THRESHOLD] & UJA113X_WTC_MASK) != 0U);
    // A press in Normal mode is no event
    fake.hvioInputMv[1] = 12000U;
    runCyclic(sbc, fake, 10U, 40U);
    CHECK(!sbc.takeInterruptEvents().any());
    fake.hvioInputMv[1] = 0U;
    runCyclic(sbc, fake, 50U, 60U);
    // Sleep with the WAKE button (HVIO2 = LOCAL1): both edges armed, a press wakes
    CHECK(sbc.enterSleep(70U, ::sbc::SleepWakeup{false, 0x02U, false, c.watchdogPeriod}) == Status::OK);
    CHECK(fake.sleeping);
    CHECK(fake.wakeIrqEnableAtSleep == 0x0CU);
    fake.hvioInputMv[2] = 12000U; // HVIO3 not selected: no wake-up
    fake.updateAnalog();
    CHECK(fake.sleeping);
    fake.hvioInputMv[1] = 12000U;
    fake.updateAnalog();
    CHECK(!fake.sleeping);
    CHECK((fake.reg[UJA113X_REG_MAIN_STATUS] & UJA113X_RSS_MASK) == 5U); // HVIO2 wake-up
}

/* ============ IO-68: INTN1 instead of polling, Standby with V1 on ========================== */

using Line = ::sbc::Uja113x::InterruptLine;

/** What a port with a working INTN1 reports: the level of the line right now. */
Line lineOf(FakeUja113x const& fake) { return fake.intn1Low() ? Line::ACTIVE : Line::INACTIVE; }

/** cyclic() every 10 ms with the line as a working pin shows it. */
void runCyclicWithLine(::sbc::Uja113x& sbc, FakeUja113x& fake, uint32_t fromMs, uint32_t toMs)
{
    for (uint32_t t = fromMs; t <= toMs; t += 10U)
    {
        fake.nowMs = t;
        sbc.cyclic(t, lineOf(fake));
    }
}

uint32_t globalReads(FakeUja113x const& fake) { return fake.reads[UJA113X_REG_GLOBAL_IRQ_STATUS]; }

void interruptLineReadsOnlyWhenActive()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    (void)sbc.takeInterruptEvents();
    uint32_t const before = globalReads(fake);

    // Quiet line: no read of 0x60 for 90 ms, the fallback read at 100 ms
    runCyclicWithLine(sbc, fake, 10U, 90U);
    CHECK(globalReads(fake) == before);
    CHECK(sbc.getCounters().interruptReadsSkipped == 9U);
    runCyclicWithLine(sbc, fake, 100U, 100U);
    CHECK(globalReads(fake) == before + 1U);
    CHECK(sbc.getCounters().interruptFallbackReads == 1U);
    CHECK(sbc.getCounters().interruptLineMisses == 0U);

    // An interrupt pulls INTN1 low: read and cleared in the next call, INTN1 released
    fake.nowMs = 105U;
    fake.setInterrupt(UJA113X_REG_SYSTEM_IRQ_STATUS, UJA113X_REG_SYSTEM_IRQ_ENABLE, UJA113X_OTWI_MASK);
    CHECK(fake.intn1Low());
    runCyclicWithLine(sbc, fake, 110U, 110U);
    CHECK(sbc.getCounters().interruptLineReads == 1U);
    CHECK(sbc.takeInterruptEvents().system == UJA113X_OTWI_MASK);
    CHECK(!fake.intn1Low());
    CHECK(fake.reg[UJA113X_REG_SYSTEM_IRQ_STATUS] == 0U);

    // Over one second: one read per fallback period and the one interrupt, nothing more (was 100 reads)
    uint32_t const afterIrq = globalReads(fake);
    runCyclicWithLine(sbc, fake, 120U, 1110U);
    CHECK(globalReads(fake) == afterIrq + 10U);
    CHECK(!sbc.isInterruptLineFaulty());
    CHECK(sbc.getCounters().interruptPolls == 0U);
}

void interruptLineUnknownPollsEveryCall()
{
    // A board without INTN1 wired (or cyclic() without line): the behaviour before IO-68
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    uint32_t const before = globalReads(fake);
    runCyclic(sbc, fake, 10U, 100U);
    CHECK(globalReads(fake) == before + 10U);
    CHECK(sbc.getCounters().interruptPolls == 10U);
    CHECK(sbc.getCounters().interruptReadsSkipped == 0U);
    for (uint32_t t = 110U; t <= 200U; t += 10U)
    {
        fake.nowMs = t;
        sbc.cyclic(t, Line::UNKNOWN);
    }
    CHECK(globalReads(fake) == before + 20U);
}

void interruptLineBrokenIsDetected()
{
    // The port reports INTN1 inactive although interrupts are pending (line broken, pin wrong)
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    (void)sbc.takeInterruptEvents();
    auto const deadLine = [&sbc, &fake](uint32_t const from, uint32_t const to)
    {
        for (uint32_t t = from; t <= to; t += 10U)
        {
            fake.nowMs = t;
            sbc.cyclic(t, Line::INACTIVE);
        }
    };
    // First miss: found by the fallback read within 100 ms, delivered, not yet a fault
    fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
    deadLine(10U, 90U);
    CHECK(!sbc.takeInterruptEvents().any());
    deadLine(100U, 100U);
    CHECK(sbc.takeInterruptEvents().supply == UJA113X_VEXTUI_MASK);
    CHECK(sbc.getCounters().interruptLineMisses == 1U);
    CHECK(!sbc.isInterruptLineFaulty());
    // A clean fallback read in between starts the count anew
    deadLine(110U, 200U);
    CHECK(sbc.getCounters().interruptFallbackReads == 2U);
    fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
    deadLine(210U, 300U);
    CHECK(sbc.getCounters().interruptLineMisses == 2U);
    CHECK(!sbc.isInterruptLineFaulty());
    // Two misses in a row: the line is faulty, 0x60 is read in every call again
    fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
    deadLine(310U, 400U);
    CHECK(sbc.getCounters().interruptLineMisses == 3U);
    CHECK(sbc.isInterruptLineFaulty());
    (void)sbc.takeInterruptEvents();
    uint32_t const before = globalReads(fake);
    deadLine(410U, 450U);
    CHECK(globalReads(fake) == before + 5U);
    fake.reg[UJA113X_REG_SYSTEM_IRQ_STATUS] = UJA113X_OTWI_MASK;
    deadLine(460U, 460U);
    CHECK(sbc.takeInterruptEvents().system == UJA113X_OTWI_MASK); // within one call, line ignored
    // Latched: a new startup (e.g. after a mode loss) does not trust the line again
    CHECK(sbc.startup(470U) == Status::OK);
    CHECK(sbc.isInterruptLineFaulty());
}

void interruptLineStuckLowAndReleaseTime()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    (void)sbc.takeInterruptEvents();
    // INTN1 read right after a clear (within tto(int)) is not compared: no "idle" count
    fake.nowMs = 10U;
    fake.reg[UJA113X_REG_SYSTEM_IRQ_STATUS] = UJA113X_OTWI_MASK;
    sbc.cyclic(10U, Line::ACTIVE);
    fake.nowMs = 11U;
    sbc.cyclic(11U, Line::ACTIVE); // stale sample of the line
    CHECK(sbc.getCounters().interruptLineIdle == 0U);
    // A line that stays low without anything pending (no pull-up) costs a read per call but is no fault
    uint32_t const before = globalReads(fake);
    for (uint32_t t = 20U; t <= 100U; t += 10U)
    {
        fake.nowMs = t;
        sbc.cyclic(t, Line::ACTIVE);
    }
    CHECK(globalReads(fake) == before + 9U);
    CHECK(sbc.getCounters().interruptLineIdle == 9U);
    CHECK(!sbc.isInterruptLineFaulty());
    // A real interrupt with the line active resets nothing wrongly and is delivered
    fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_V1UI_MASK;
    fake.nowMs = 110U;
    sbc.cyclic(110U, Line::ACTIVE);
    CHECK(sbc.takeInterruptEvents().supply == UJA113X_V1UI_MASK);
}

void interruptLineFallbackCloseToClearIsNoMiss()
{
    // An interrupt arriving between the port's sample of INTN1 and the fallback read: one miss, never a fault
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    (void)sbc.takeInterruptEvents();
    for (uint32_t round = 0U; round < 5U; ++round)
    {
        uint32_t const base = 100U + (round * 200U);
        runCyclicWithLine(sbc, fake, base - 90U, base - 10U);
        fake.nowMs = base;
        Line const sampled = lineOf(fake); // inactive
        fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_STATUS] = UJA113X_CFI_MASK; // arrives right after the sample
        sbc.cyclic(base, sampled);
        runCyclicWithLine(sbc, fake, base + 10U, base + 100U);
    }
    CHECK(!sbc.isInterruptLineFaulty());
    CHECK(sbc.getCounters().interruptLineMisses == 5U);
}

void standbyWithCanWakeup()
{
    FakeUja113x fake;
    fake.ctsFollowsCmc = true;
    auto const config  = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.isCanTransceiverReady());
    (void)sbc.takeInterruptEvents();
    fake.modeHistory.clear();
    fake.nowMs = 10U;
    CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::OK);
    CHECK(sbc.getState() == State::STANDBY);
    CHECK(fake.mode() == UJA113X_MC_STANDBY);
    CHECK(!fake.sleeping);
    CHECK(!fake.systemReset);
    CHECK((fake.modeHistory.size() == 1U) && (fake.modeHistory[0] == UJA113X_MC_STANDBY));
    CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) != 0U);
    CHECK(!sbc.isCanTransceiverReady()); // transceiver offline in Standby
    CHECK(!fake.intn1Low());
    // Nothing happens: no read of 0x60 while INTN1 stays high (MCU may stop), Standby kept for seconds
    uint32_t const before = globalReads(fake);
    runCyclicWithLine(sbc, fake, 20U, 90U);
    CHECK(globalReads(fake) == before);
    runCyclicWithLine(sbc, fake, 100U, 2500U);
    CHECK(sbc.getState() == State::STANDBY);
    CHECK(sbc.getCounters().modeLost == 0U);
    CHECK(!sbc.takeInterruptEvents().any());
    // Bus activity: CWI, INTN1 goes low - the wake-up of the MCU
    fake.nowMs = 2505U;
    CHECK(fake.standbyBusActivity());
    CHECK(fake.intn1Low());
    runCyclicWithLine(sbc, fake, 2510U, 2510U);
    auto const events = sbc.takeInterruptEvents();
    CHECK(events.transceiver == UJA113X_CWI_MASK);
    CHECK(!fake.intn1Low());
    CHECK(sbc.getState() == State::STANDBY); // the caller decides when to leave
    // Back to Normal: CWIE removed, transceiver active and ready again
    fake.nowMs = 2520U;
    CHECK(sbc.leaveStandby(2520U) == Status::OK);
    CHECK(sbc.getState() == State::NORMAL);
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
    CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CFI_MASK) != 0U);
    CHECK(sbc.isCanTransceiverReady());
    CHECK(sbc.leaveStandby(2530U) == Status::NOT_READY);
    runCyclicWithLine(sbc, fake, 2530U, 4000U);
    CHECK(sbc.getState() == State::NORMAL);
    CHECK(!fake.systemReset);
}

void standbyRefusedAndAborted()
{
    // Not initialized
    {
        FakeUja113x fake;
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.enterStandby(0U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::NOT_READY);
        CHECK(fake.writtenAddresses.empty());
    }
    // No source, an HVIO of a bank the device lacks: nothing written
    {
        FakeUja113x fake;
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        fake.writtenAddresses.clear();
        CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{false, 0U, false, config.watchdogPeriod})
              == Status::NOT_ALLOWED);
        CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{true, 0x10U, false, config.watchdogPeriod})
              == Status::NOT_ALLOWED); // HVIO5: bank 1 missing on the /FD/4
        CHECK(fake.writtenAddresses.empty());
        CHECK(sbc.getState() == State::NORMAL);
        CHECK(fake.mode() == UJA113X_MC_NORMAL);
    }
    // Pending interrupt: back in Normal, sources removed, watchdog as configured, the interrupt goes to cyclic()
    {
        FakeUja113x fake;
        auto config         = boardConfig();
        config.watchdogMode = ::sbc::WatchdogMode::TIMEOUT;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        fake.reg[UJA113X_REG_SUPPLY_IRQ_STATUS] = UJA113X_VEXTUI_MASK;
        fake.modeHistory.clear();
        CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{true, 0x02U, false, config.watchdogPeriod})
              == Status::INTERRUPT_PENDING);
        CHECK(fake.modeHistory.empty()); // found before the mode change: Normal mode never left
        CHECK(sbc.getState() == State::NORMAL);
        CHECK(fake.mode() == UJA113X_MC_NORMAL);
        CHECK(!fake.systemReset);
        CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
        CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U);
        runCyclicWithLine(sbc, fake, 20U, 20U);
        CHECK(sbc.takeInterruptEvents().supply == UJA113X_VEXTUI_MASK);
        // The interrupt arrives only with the mode change (second check): Standby left again the same way
        fake.supplyIrqOnStandby = UJA113X_VEXTUI_MASK;
        fake.modeHistory.clear();
        CHECK(sbc.enterStandby(30U, ::sbc::SleepWakeup{true, 0x02U, false, config.watchdogPeriod})
              == Status::INTERRUPT_PENDING);
        CHECK((fake.modeHistory.size() == 2U) && (fake.modeHistory[0] == UJA113X_MC_STANDBY)
              && (fake.modeHistory[1] == UJA113X_MC_NORMAL));
        CHECK(sbc.getState() == State::NORMAL);
        CHECK(!fake.systemReset);
        CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
        CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U);
        CHECK(((fake.reg[UJA113X_REG_WATCHDOG_CONTROL] & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT)
              == UJA113X_WMC_TIMEOUT);
        fake.supplyIrqOnStandby = 0U;
        runCyclicWithLine(sbc, fake, 40U, 40U);
        CHECK(sbc.takeInterruptEvents().supply == UJA113X_VEXTUI_MASK);
    }
    // A wake-up interrupt still pending when the caller leaves Standby: left to cyclic(), not lost
    {
        FakeUja113x fake;
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::OK);
        CHECK(fake.standbyBusActivity());
        CHECK(sbc.leaveStandby(20U) == Status::OK);
        CHECK(fake.mode() == UJA113X_MC_NORMAL);
        runCyclicWithLine(sbc, fake, 30U, 30U);
        CHECK(sbc.takeInterruptEvents().transceiver == UJA113X_CWI_MASK); // left to cyclic(), not lost
    }
    // Sleep and settings are refused in Standby
    {
        FakeUja113x fake;
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::OK);
        CHECK(sbc.enterSleep(20U) == Status::NOT_READY);
        CHECK(!fake.sleeping);
        CHECK(sbc.enterStandby(20U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod})
              == Status::NOT_READY);
        ::sbc::Uja113x::BatteryVoltages v{};
        CHECK(sbc.readBatteryVoltages(v) == Status::NOT_READY);
    }
}

void standbyWithWakeInputsBothBanks()
{
    // UJA113x without /FD: two HVIO banks; HVIO2 (bank 0) and HVIO6 (bank 1) as wake-up inputs
    FakeUja113x fake;
    fake.reg[UJA113X_REG_IDENTIFICATION_0] = 0x11U;
    auto config           = boardConfig();
    config.identification = 0x11U;
    ::sbc::HvioConfig const wake{::sbc::HvioFunction::WAKE_INPUT, false, ::sbc::HvioActivation::ON,
                                 ::sbc::SupplyShutdown::NONE, false, false, false, false, 0U, 0U};
    config.hvio[0].configure     = true;
    config.hvio[0].wakeThreshold = ::sbc::HvioWakeThreshold::ABSOLUTE;
    config.hvio[0].pin[1]        = wake;
    config.hvio[1].configure     = true;
    config.hvio[1].wakeThreshold = ::sbc::HvioWakeThreshold::ABSOLUTE;
    config.hvio[1].pin[1]        = wake;
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.getHvioBanks() == 2U);
    CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{false, 0x22U, false, config.watchdogPeriod}) == Status::OK);
    CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0x0CU);
    CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE + 0x10U] == 0x0CU);
    CHECK((fake.reg[UJA113X_REG_TRANSCEIVER_IRQ_ENABLE] & UJA113X_CWI_MASK) == 0U);
    runCyclicWithLine(sbc, fake, 20U, 1500U); // supervision reads the armed sources back: intact
    CHECK(sbc.getState() == State::STANDBY);
    // HVIO6 rises: a bank 1 event, reported in its own field
    fake.nowMs              = 1505U;
    fake.hvioInputMv[5]     = 12000U;
    fake.updateAnalog();
    CHECK(fake.intn1Low());
    runCyclicWithLine(sbc, fake, 1510U, 1510U);
    auto const events = sbc.takeInterruptEvents();
    CHECK(events.wakeBank1 == (1U << ((2U * 1U) + UJA113X_HVIO_RISING_SHIFT)));
    CHECK(events.wake == 0U);
    CHECK(sbc.leaveStandby(1520U) == Status::OK);
    CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE] == 0U);
    CHECK(fake.reg[UJA113X_REG_BANK0_WAKE_IRQ_ENABLE + 0x10U] == 0U);
}

void standbyWatchdog()
{
    // Timeout watchdog outside development mode: autonomous in Standby (off while INTN1 is high), served by
    // cyclic() as long as the MCU runs, back to timeout in Normal - never changed in Normal mode
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_WATCHDOG_STATUS] = 0x01U; // SDMS = 0
        auto config           = boardConfig();
        config.watchdogMode   = ::sbc::WatchdogMode::TIMEOUT;
        config.watchdogPeriod = ::sbc::WatchdogPeriod::MS_128;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        runCyclicWithLine(sbc, fake, 10U, 500U);
        fake.nowMs = 505U;
        CHECK(sbc.enterStandby(505U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::OK);
        CHECK(((fake.reg[UJA113X_REG_WATCHDOG_CONTROL] & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT)
              == UJA113X_WMC_AUTONOMOUS);
        fake.maxTriggerGapMs = 0U;
        fake.writtenAddresses.clear();
        uint16_t const triggersBefore = sbc.getCounters().watchdogTriggers;
        runCyclicWithLine(sbc, fake, 510U, 3000U);
        CHECK(fake.maxTriggerGapMs <= 128U);
        // timeout behaviour in Standby: every trigger written twice (data sheet 7.2.5 case B), nothing else written
        size_t watchdogWrites = 0U;
        for (uint8_t const a : fake.writtenAddresses)
        {
            watchdogWrites += (a == UJA113X_REG_WATCHDOG_CONTROL) ? 1U : 0U;
        }
        uint16_t const triggers = static_cast<uint16_t>(sbc.getCounters().watchdogTriggers - triggersBefore);
        CHECK(triggers >= 30U);
        CHECK(watchdogWrites == (2U * triggers));
        CHECK(watchdogWrites == fake.writtenAddresses.size());
        CHECK(((fake.reg[UJA113X_REG_WATCHDOG_CONTROL] & UJA113X_WMC_MASK) >> UJA113X_WMC_SHIFT)
              == UJA113X_WMC_AUTONOMOUS); // the trigger in Standby keeps the Standby value
        fake.nowMs = 3005U;
        CHECK(sbc.leaveStandby(3005U) == Status::OK);
        CHECK(fake.reg[UJA113X_REG_WATCHDOG_CONTROL] == ((UJA113X_WMC_TIMEOUT << 5U) | UJA113X_NWP_128MS));
        runCyclicWithLine(sbc, fake, 3010U, 5000U);
        CHECK(!fake.systemReset);
        CHECK(fake.maxTriggerGapMs <= 128U);
    }
    // Window watchdog: after Standby synchronized again through Standby, no trigger in the closed window
    {
        FakeUja113x fake;
        fake.reg[UJA113X_REG_WATCHDOG_STATUS] = 0x01U;
        auto config           = boardConfig();
        config.watchdogMode   = ::sbc::WatchdogMode::WINDOW;
        config.watchdogPeriod = ::sbc::WatchdogPeriod::MS_128;
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        runCyclicWithLine(sbc, fake, 10U, 1000U);
        for (uint32_t round = 0U; round < 3U; ++round)
        {
            uint32_t const t = 1005U + (round * 1000U);
            fake.nowMs       = t;
            CHECK(sbc.enterStandby(t, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::OK);
            runCyclicWithLine(sbc, fake, t + 5U, t + 455U);
            fake.nowMs = t + 457U;
            CHECK(sbc.leaveStandby(t + 457U) == Status::OK);
            runCyclicWithLine(sbc, fake, t + 465U, t + 995U);
        }
        CHECK(!fake.systemReset);
        CHECK(fake.mode() == UJA113X_MC_NORMAL);
    }
    // Cyclic wake-up: timeout watchdog with the requested period and no trigger in Standby - its overflow
    // raises WDI, INTN1 goes low
    {
        FakeUja113x fake;
        auto const config = boardConfig();
        ::sbc::Uja113x sbc(fake, config);
        CHECK(sbc.startup(0U) == Status::OK);
        CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{false, 0U, true, ::sbc::WatchdogPeriod::MS_1024})
              == Status::OK);
        CHECK(fake.reg[UJA113X_REG_WATCHDOG_CONTROL] == ((UJA113X_WMC_TIMEOUT << 5U) | UJA113X_NWP_1024MS));
        uint16_t const triggers = sbc.getCounters().watchdogTriggers;
        runCyclicWithLine(sbc, fake, 20U, 1000U);
        CHECK(sbc.getCounters().watchdogTriggers == triggers);
        fake.nowMs = 1034U;
        fake.reg[UJA113X_REG_SYSTEM_IRQ_STATUS] |= UJA113X_WDI_MASK; // overflow, WDI has no enable bit
        CHECK(fake.intn1Low());
        runCyclicWithLine(sbc, fake, 1040U, 1040U);
        CHECK(sbc.takeInterruptEvents().system == UJA113X_WDI_MASK);
        CHECK(sbc.leaveStandby(1050U) == Status::OK);
        CHECK(fake.reg[UJA113X_REG_WATCHDOG_CONTROL] == ((UJA113X_WMC_AUTONOMOUS << 5U) | UJA113X_NWP_128MS));
        CHECK(!fake.systemReset);
    }
}

void standbyResetOfTheSbcIsDetected()
{
    FakeUja113x fake;
    auto const config = boardConfig();
    ::sbc::Uja113x sbc(fake, config);
    CHECK(sbc.startup(0U) == Status::OK);
    CHECK(sbc.enterStandby(10U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::OK);
    runCyclicWithLine(sbc, fake, 20U, 1020U);
    CHECK(sbc.getState() == State::STANDBY);
    CHECK(sbc.getCounters().modeLost == 0U);
    // The SBC resets itself and comes back in Standby - CWIE lost, nothing would wake the MCU any more
    fake.resetInStandby();
    runCyclicWithLine(sbc, fake, 1030U, 2030U);
    CHECK(sbc.getCounters().modeLost == 1U);
    CHECK(sbc.getState() == State::NORMAL); // initialized again
    CHECK(fake.mode() == UJA113X_MC_NORMAL);
    // leaveStandby() after a reset to defaults (mode lost before the supervision saw it): FAILED, restart
    CHECK(sbc.enterStandby(2040U, ::sbc::SleepWakeup{true, 0U, false, config.watchdogPeriod}) == Status::OK);
    fake.reg[UJA113X_REG_MODE_CONTROL] = 0x00U; // neither Standby nor Normal (invalid)
    CHECK(sbc.leaveStandby(2050U) == Status::NOT_READY);
    CHECK(sbc.getState() == State::FAILED);
    CHECK(sbc.getCounters().modeLost == 2U);
    fake.reg[UJA113X_REG_MODE_CONTROL] = UJA113X_MC_STANDBY;
    runCyclicWithLine(sbc, fake, 2060U, 3100U);
    CHECK(sbc.getState() == State::NORMAL);
}

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"startupFromPowerOn", startupFromPowerOn},
        {"wrongDeviceIsNotWritten", wrongDeviceIsNotWritten},
        {"spiFailureIsRetried", spiFailureIsRetried},
        {"mtpnvMismatchIsReportedNotProgrammed", mtpnvMismatchIsReportedNotProgrammed},
        {"limpHomeIsReleased", limpHomeIsReleased},
        {"canFailureInterrupt", canFailureInterrupt},
        {"timeoutWatchdogConfiguredInStandby", timeoutWatchdogConfiguredInStandby},
        {"windowWatchdogHitsOpenWindow", windowWatchdogHitsOpenWindow},
        {"autonomousWatchdogOutsideDevelopmentModeIsServed", autonomousWatchdogOutsideDevelopmentModeIsServed},
        {"windowWatchdogAfterMcuOnlyReset", windowWatchdogAfterMcuOnlyReset},
        {"sleepAbortsOnPendingInterrupt", sleepAbortsOnPendingInterrupt},
        {"sleepSequence", sleepSequence},
        {"sleepWithoutWakeupSourceIsRefused", sleepWithoutWakeupSourceIsRefused},
        {"sleepWithWakeInputsAndCyclicWakeup", sleepWithWakeInputsAndCyclicWakeup},
        {"watchdogPeriodGrid", watchdogPeriodGrid},
        {"protectedRegisters", protectedRegisters},
        {"sbcResetWhileMcuRuns", sbcResetWhileMcuRuns},
        {"verifyErrorFailsStartup", verifyErrorFailsStartup},
        {"stuckSdoIsSpiError", stuckSdoIsSpiError},
        {"lpspiDivider", lpspiDivider},
        {"compensationPowerOnStart", compensationPowerOnStart},
        {"compensationMcuResetStart", compensationMcuResetStart},
        {"compensationKeepsRealWatchdogReset", compensationKeepsRealWatchdogReset},
        {"compensationCounterZeroAndOtherBits", compensationCounterZeroAndOtherBits},
        {"compensationSpiErrors", compensationSpiErrors},
        {"compensationFailedStartsBeforeSbcStart", compensationFailedStartsBeforeSbcStart},
        {"partialNetworkingRegisters", partialNetworkingRegisters},
        {"partialNetworkingWakesOnlyOnConfiguredId", partialNetworkingWakesOnlyOnConfiguredId},
        {"partialNetworkingFdPassive", partialNetworkingFdPassive},
        {"partialNetworkingRefusedAndAborted", partialNetworkingRefusedAndAborted},
        {"partialNetworkingSleepSequence", partialNetworkingSleepSequence},
        {"partialNetworkingArmWithoutSleep", partialNetworkingArmWithoutSleep},
        {"transceiverStatusWithinOneCycleWhenCanActive", transceiverStatusWithinOneCycleWhenCanActive},
        {"transceiverStatusReadOncePerCycle", transceiverStatusReadOncePerCycle},
        {"transceiverStatusSlowPollWhenCanNotActive", transceiverStatusSlowPollWhenCanNotActive},
        {"canControlWriteReadsTransceiverStatus", canControlWriteReadsTransceiverStatus},
        {"hvioBankCountFromIdentification", hvioBankCountFromIdentification},
        {"batteryThresholdConversion", batteryThresholdConversion},
        {"hvioThresholdGrid", hvioThresholdGrid},
        {"batteryMonitorConfigured", batteryMonitorConfigured},
        {"batteryMonitorEventsAndHysteresis", batteryMonitorEventsAndHysteresis},
        {"batteryShutdownIsAConfiguredDecision", batteryShutdownIsAConfiguredDecision},
        {"hvioConfigurationRegisters", hvioConfigurationRegisters},
        {"hvioAbsentOnDeviceWithoutHvio", hvioAbsentOnDeviceWithoutHvio},
        {"hvioDiagnosisAndRecovery", hvioDiagnosisAndRecovery},
        {"hvioInputsAndWakeFromSleep", hvioInputsAndWakeFromSleep},
        {"enPinAndLimpHome", enPinAndLimpHome},
        {"batteryShutdownDelayed", batteryShutdownDelayed},
        {"batteryShutdownDelayCosts", batteryShutdownDelayCosts},
        {"wakeInputsFromConfiguration", wakeInputsFromConfiguration},
        {"boardWakeInputsWakeFromSleep", boardWakeInputsWakeFromSleep},
        {"interruptLineReadsOnlyWhenActive", interruptLineReadsOnlyWhenActive},
        {"interruptLineUnknownPollsEveryCall", interruptLineUnknownPollsEveryCall},
        {"interruptLineBrokenIsDetected", interruptLineBrokenIsDetected},
        {"interruptLineStuckLowAndReleaseTime", interruptLineStuckLowAndReleaseTime},
        {"interruptLineFallbackCloseToClearIsNoMiss", interruptLineFallbackCloseToClearIsNoMiss},
        {"standbyWithCanWakeup", standbyWithCanWakeup},
        {"standbyRefusedAndAborted", standbyRefusedAndAborted},
        {"standbyWithWakeInputsBothBanks", standbyWithWakeInputsBothBanks},
        {"standbyWatchdog", standbyWatchdog},
        {"standbyResetOfTheSbcIsDetected", standbyResetOfTheSbcIsDetected},
    };
    for (auto const& test : tests)
    {
        int const before = failures;
        test.fn();
        std::printf("%s %s\n", (failures == before) ? "ok  " : "FAIL", test.name);
    }
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
