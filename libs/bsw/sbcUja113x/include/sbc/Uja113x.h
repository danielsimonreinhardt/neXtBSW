#pragma once

#include "sbc/uja113xRegisters.h"
#include "spi/ISpiDevice.h"

#include <cstdint>

namespace sbc
{

enum class WatchdogMode : uint8_t
{
    AUTONOMOUS = UJA113X_WMC_AUTONOMOUS, // off in software development mode
    TIMEOUT    = UJA113X_WMC_TIMEOUT,
    WINDOW     = UJA113X_WMC_WINDOW,
};

enum class WatchdogPeriod : uint8_t
{
    MS_8    = UJA113X_NWP_8MS,
    MS_16   = UJA113X_NWP_16MS,
    MS_32   = UJA113X_NWP_32MS,
    MS_64   = UJA113X_NWP_64MS,
    MS_128  = UJA113X_NWP_128MS,
    MS_256  = UJA113X_NWP_256MS,
    MS_1024 = UJA113X_NWP_1024MS,
    MS_4096 = UJA113X_NWP_4096MS,
};

enum class V2Mode : uint8_t
{
    OFF                    = UJA113X_V2C_OFF,
    ON_NORMAL              = UJA113X_V2C_ON_NORMAL,
    ON_NORMAL_STANDBY_RESET = UJA113X_V2C_ON_NORMAL_STANDBY_RESET,
    ON_ALWAYS              = UJA113X_V2C_ON_ALWAYS,
};

enum class CanMode : uint8_t
{
    OFFLINE     = UJA113X_CMC_OFFLINE,
    ACTIVE      = UJA113X_CMC_ACTIVE,
    ACTIVE_NO_UV = UJA113X_CMC_ACTIVE_NO_UV,
    LISTEN_ONLY = UJA113X_CMC_LISTEN_ONLY,
};

/**
 * What a battery under- or overvoltage event of the monitor switches off on its own, without the MCU (IO-10).
 * Same coding for the EN pin (ENSC), V2/VEXT (V2SC), the CAN transceiver (CSC) and every HVIO (IOnSC).
 * Only effective in Normal mode and only with thresholds configured; the power-up default of CSC is
 * SHUTDOWN_UV, so a project that arms the monitor has to say what it wants here.
 */
enum class SupplyShutdown : uint8_t
{
    NONE          = UJA113X_SHUTDOWN_NONE,
    UNDERVOLTAGE  = UJA113X_SHUTDOWN_UV,
    OVERVOLTAGE   = UJA113X_SHUTDOWN_OV,
    UNDER_OR_OVERVOLTAGE = UJA113X_SHUTDOWN_UV_OV,
};

/** Which pin of the battery monitor triggers the under-/overvoltage events (BMSC). */
enum class BatteryMonitorSource : uint8_t
{
    BAT      = 0U,
    BATSENSE = 1U,
};

/**
 * Battery monitoring (IO-10). The project gives the thresholds as **physical voltages in mV** at the
 * monitored pin; the driver converts them to the register codes of the 20 V full-scale monitor
 * (step 20000 mV / 255 = 78 mV, hysteresis step 314 mV) and never asks for register bits.
 *
 * Rounding is conservative, so the event never comes later than asked for: the undervoltage threshold is
 * rounded up, the overvoltage threshold down. getBatteryThresholds() reports what the SBC really uses.
 */
struct BatteryMonitorConfig
{
    bool configure              = false; // false: registers 0x11..0x14 stay untouched (no thresholds, no events)
    BatteryMonitorSource source = BatteryMonitorSource::BAT;
    uint16_t undervoltageMv     = 0U;    // event below this voltage (0: never)
    uint16_t overvoltageMv      = 0U;    // event above this voltage (20000: never)
    uint16_t undervoltageHysteresisMv = 0U; // release window of the undervoltage event, 0 .. 4710 mV
    uint16_t overvoltageHysteresisMv  = 0U; // release window of the overvoltage event, 0 .. 4710 mV
    SupplyShutdown canShutdown  = SupplyShutdown::NONE; // CSC: CAN transceiver off on the event
    SupplyShutdown v2Shutdown   = SupplyShutdown::NONE; // V2SC: V2/VEXT off on the event
    /**
     * IO-33: 0 = the SBC switches CAN/V2 off by itself (CSC/V2SC), within one conversion of its monitor (6 us);
     * the data sheet offers no filter. > 0 = CSC/V2SC stay 00 and cyclic() switches CAN (CMC offline) and V2
     * (V2C off) only after the event (BMUVS/BMOVS) has lasted this long without a break, and back on as soon as it
     * has ended (the SBC's release window applies). Rides through short dips such as the inrush current of a load.
     */
    uint16_t shutdownDelayMs = 0U;
};

/** EN output configuration (data sheet 7.4). ENC decides in which SBC modes EN is driven high. */
enum class EnMode : uint8_t
{
    LOW                       = UJA113X_ENC_LOW,
    HIGH_NORMAL_RESET_STANDBY = UJA113X_ENC_HIGH_NORMAL_RESET_STANDBY,
    HIGH_NORMAL               = UJA113X_ENC_HIGH_NORMAL,
    TIMER2                    = UJA113X_ENC_TIMER2,
};

/** EN driver stage: push-pull, or open drain with an external pull-up to V1. */
enum class EnDriver : uint8_t
{
    PUSH_PULL  = 0U,
    OPEN_DRAIN = 1U,
};

struct EnPinConfig
{
    bool configure;           // false: ENSC/ENDC/ENC stay as found (behaviour before IO-10)
    EnMode mode;
    EnDriver driver;
    SupplyShutdown shutdown;  // ENSC: EN low on a battery monitor event, overruling `mode`
};

/**
 * Function of one HVIO pin (IOnCC without the slope control bit, which `HvioConfig::slopeControl` carries).
 * There is no separate pull configuration on an HVIO: a pull-up to BATHSx is the high-side driver
 * (HIGH_SIDE_AND_WAKE for a biased switch input), a pull-down is the low-side driver.
 */
enum class HvioFunction : uint8_t
{
    OFF                = 0U,
    HIGH_SIDE          = 1U,
    LOW_SIDE           = 2U,
    WAKE_INPUT         = 3U,
    HIGH_SIDE_AND_WAKE = 4U,
};

/** When the configured driver or input is actually active (IOnAC). */
enum class HvioActivation : uint8_t
{
    OFF             = UJA113X_IOAC_OFF,
    ON              = UJA113X_IOAC_ON,
    TIMER1          = UJA113X_IOAC_TIMER1,
    TIMER2          = UJA113X_IOAC_TIMER2,
    TIMER3          = UJA113X_IOAC_TIMER3,
    TIMER4          = UJA113X_IOAC_TIMER4,
    DIRECT_INVERTED = UJA113X_IOAC_DIRECT_INV, // bank 0 only, driven by HVIO(n+4)
    DIRECT          = UJA113X_IOAC_DIRECT,     // bank 0 only, driven by HVIO(n+4)
};

/** Wake-up detection threshold of a whole bank (B0WTC/B1WTC). */
enum class HvioWakeThreshold : uint8_t
{
    RATIOMETRIC = 0U, // 0.38 .. 0.6 of the BATHSx supply of the bank
    ABSOLUTE    = 1U, // 2.4 V .. 4.1 V against ground
};

/**
 * One HVIO pin. The detection thresholds are **load currents in mA** (function view), not register codes:
 * the driver picks the grid value of the SBC that is at least as sensitive as asked for
 * (short circuit 30/45/60/90 mA, open load 2/5/10/20 mA typical).
 */
struct HvioConfig
{
    HvioFunction function;
    bool slopeControl;               // limits dV/dt of the driver (EMC), data sheet 7.10.1.1
    HvioActivation activation;
    SupplyShutdown shutdown;         // IOnSC: automatic load shedding on a battery monitor event
    bool wakeOnRisingEdge;           // wake capability of an input: IOnREIE
    bool wakeOnFallingEdge;          // IOnFEIE
    bool shortCircuitInterrupt;      // IOnSCIE (the driver is switched off by the SBC in any case)
    bool openLoadInterrupt;          // IOnOLIE
    uint16_t shortCircuitThresholdMa;
    uint16_t openLoadThresholdMa;
};

/** One bank of four HVIOs: bank 0 is HVIO1..HVIO4, bank 1 is HVIO5..HVIO8. */
struct HvioBankConfig
{
    bool configure;               // false: the HVIO registers of this bank stay untouched
    HvioWakeThreshold wakeThreshold;
    HvioConfig pin[UJA113X_HVIO_PER_BANK];
};

/**
 * Wake-up sources the SBC arms for Sleep (SYS-02). At least one of them is needed, otherwise the SBC makes a
 * reset instead of sleeping. `cyclic` leaves the watchdog running in Sleep (timeout watchdog, data sheet 7.2.3,
 * Table 8): its overflow resets the SBC and shows up as reset source UJA113X_RSS_WD_OVERFLOW_SLEEP.
 */
struct SleepWakeup
{
    bool can;                     // CAN wake-up of the integrated transceiver (CWI, register 0x23)
    /**
     * HVIO inputs that may wake the SBC, bit i = HVIO(i + 1) over both banks (IO-10). This is an input
     * mask, not a register value: registers 0x37/0x47 hold two enable bits per HVIO (rising, falling
     * edge), which the driver derives from the edges configured for that pin. A pin whose configuration
     * names neither edge is armed for both, because a Sleep source that never fires is worse than one
     * edge too many.
     */
    uint8_t wakeInputs;
    bool cyclic;                  // cyclic wake-up through the watchdog
    WatchdogPeriod cyclicPeriod;  // period of the cyclic wake-up (only used with cyclic)

    bool any() const { return can || (wakeInputs != 0U) || cyclic; }
};

/**
 * Selective CAN wake-up (partial networking, ISO 11898-2/-6), UJA113x/FD variants only (COM-07). With a filter
 * set, a CAN wake-up in Sleep happens only on a matching wake-up frame instead of any bus activity. The values
 * are project data (bus bit rate, wake-up message); the driver only translates them into registers.
 */
struct CanWakeupFrame
{
    uint32_t bitrate;        // bit/s of the bus: 50000, 100000, 125000, 250000, 500000 or 1000000
    uint32_t id;             // identifier of the wake-up frame (11 or 29 bit)
    uint32_t idCompareMask;  // bit = 1: identifier bit must match, 0: don't care (all ones: exactly id)
    bool extendedId;         // 29 bit identifier
    bool evaluateData;       // DLC and data field are part of the match (PNDM)
    uint8_t dlc;             // expected data length 0..8 (only with evaluateData)
    uint8_t dataMask[8];     // byte i of the data field: a frame wakes if it sets at least one of these bits
    bool fdPassive;          // CAN FD frames on the bus are tolerated instead of counted as errors (CFDC)
};

/**
 * SPI settings of a UJA113x (ARCH-41): values of the device and of this driver, the same on every board. The port
 * fills its SPI master configuration from them; instance, chip select and functional clock come from the project.
 *
 * - Frame, clock polarity and phase: data sheet 7.16 (uja113xRegisters.h).
 * - SCK: an upper limit, the SPI master takes the fastest rate at or below it. 500 kHz is the rate verified on the
 *   eval board with application and bootloader (tools/bswgen/blgen.py uses the same value); the data sheet allows
 *   4 MHz. One 16-bit frame takes 32 us at this rate.
 * - Timeout: bound for one frame, only reached when the bus hangs; the driver reports it as SPI_ERROR.
 */
struct Uja113xSpi
{
    static constexpr uint32_t BAUDRATE_HZ = 500000UL;
    static constexpr uint8_t FRAME_BITS   = UJA113X_SPI_FRAME_BITS;
    static constexpr bool CPOL            = (UJA113X_SPI_CPOL != 0U);
    static constexpr bool CPHA            = (UJA113X_SPI_CPHA != 0U);
    static constexpr uint32_t TIMEOUT_US  = 5000UL;
};

static_assert(Uja113xSpi::BAUDRATE_HZ <= UJA113X_SPI_MAX_SCK_HZ, "UJA113x SCK above the data sheet limit");

struct Uja113xConfig
{
    uint8_t identification; // expected content of register 0x7E
    // Expected MTPNV content. Only compared, the driver never programs the MTPNV.
    uint8_t startupControl; // 0x73
    uint8_t sbcConfig;      // 0x74
    WatchdogMode watchdogMode;
    WatchdogPeriod watchdogPeriod;
    V2Mode v2;
    CanMode can;
    uint8_t systemIrqEnable;      // 0x04 (OTWIE, SPIFIE)
    uint8_t supplyIrqEnable;      // 0x1C, battery monitor events are BMUI/BMOI
    uint8_t transceiverIrqEnable; // 0x23 in Normal mode, CAN wake-up is added for Sleep only
    uint8_t resetCounter;         // RCC written after every successful startup
    /* The IO-10 fields carry a default, so that a configuration which only names the older ones stays
     * valid and means "leave HVIO, EN, LIMP and the battery monitor as they are". */
    /** LIMP pin after a successful startup: false releases it (LHC = 0), the normal case. */
    bool limpHomeAtStartup = false;
    EnPinConfig enPin{};
    BatteryMonitorConfig battery{};
    /** Bank 0 = HVIO1..HVIO4, bank 1 = HVIO5..HVIO8. Banks the device does not have are never written. */
    HvioBankConfig hvio[UJA113X_HVIO_BANKS]{};
};

/**
 * Complex device driver for the UJA113x SBC family, following NXP AH1501 (software flow):
 * startup operation, watchdog service, interrupt handling, transceiver supervision and the
 * transition to Sleep mode.
 *
 * Hardware independent (SPI via bsp::spi::ISpiDevice, time passed in by the caller).
 * Not thread safe: call everything from one task context.
 */
class Uja113x
{
public:
    enum class Status : uint8_t
    {
        OK,
        SPI_ERROR,
        WRONG_DEVICE,
        VERIFY_ERROR,
        NOT_ALLOWED,
        INTERRUPT_PENDING,
        NOT_READY,
    };

    enum class State : uint8_t
    {
        UNINITIALIZED,
        NORMAL,
        FAILED,
        STANDBY, // IO-68: SBC Standby with V1 on, entered by enterStandby(), left by leaveStandby()
    };

    /**
     * IO-68: what the caller knows about pin INTN1 at the time of a cyclic() call. INTN1 is an open-drain output,
     * active low, that the SBC holds low while any enabled interrupt is pending (data sheet 7.12: "Pin INTN1 is
     * always driven LOW when an interrupt is pending", released when all status bits are cleared, then held high
     * for at least tto(int) = 0.9..1.1 ms even if a new interrupt comes). A level sample therefore cannot miss an
     * interrupt; an edge seen by a pin interrupt since the previous call counts as ACTIVE as well.
     */
    enum class InterruptLine : uint8_t
    {
        UNKNOWN,  // no pin information (board without INTN1 wired): 0x60 is read in every call, as before IO-68
        INACTIVE, // INTN1 high and no edge since the previous call: 0x60 is read only as fallback
        ACTIVE,   // INTN1 low now, or its pin interrupt saw it go low since the previous call
    };

    struct InterruptFlags
    {
        uint8_t system;      // 0x61
        uint8_t supply;      // 0x62
        uint8_t transceiver; // 0x63
        uint8_t wake;        // 0x64, bank 0 HVIO edges (two bits per HVIO)
        uint8_t failure;     // 0x65, bank 0 HVIO short circuit / open load
        uint8_t wakeBank1;   // 0x66, only on devices with two HVIO banks
        uint8_t failureBank1; // 0x67

        bool any() const
        {
            return (system | supply | transceiver | wake | failure | wakeBank1 | failureBank1) != 0U;
        }
    };

    /** Battery monitor thresholds as the SBC really uses them after the rounding to its grid (IO-10). */
    struct BatteryThresholds
    {
        uint16_t undervoltageMv;
        uint16_t overvoltageMv;
        uint16_t undervoltageHysteresisMv;
        uint16_t overvoltageHysteresisMv;
    };

    /** A measurement of the battery monitor. Only valid in Normal mode, the monitor is off elsewhere. */
    struct BatteryVoltages
    {
        uint16_t batMv;      // pin BAT
        uint16_t batSenseMv; // pin BATSENSE
        bool undervoltage;   // BMUVS of the selected source
        bool overvoltage;    // BMOVS
    };

    /** Levels and driver diagnosis of one HVIO bank. */
    struct HvioBankStatus
    {
        uint8_t wakeLevels;    // bit i = input of HVIO(i + 1) above the wake-up threshold (0x35/0x45)
        uint8_t driverStatus;  // two bits per HVIO (0x36/0x46), see hvioDriverStatus()
    };

    struct StartupInfo
    {
        uint8_t identification;
        uint8_t mtpnvStatus;
        uint8_t startupControl;
        uint8_t sbcConfig;
        uint8_t mainStatus;
        uint8_t watchdogStatus;
        uint8_t modeBefore;
        uint8_t failSafeBefore; // RCC / LHC as found
        InterruptFlags interrupts;
        bool mtpnvMismatch;
        bool limpHomeWasActive;
    };

    struct Counters
    {
        uint16_t spiErrors;
        uint16_t verifyErrors;
        uint16_t addressEchoMismatches; // AH1501 2.6 check on reads, reported as SPI_ERROR
        uint16_t watchdogTriggers;
        uint16_t interruptEvents;
        uint16_t canNotReady;
        uint16_t modeLost;
        uint16_t startupAttempts;
        uint16_t supplyShutdowns; // IO-33: delayed switch-offs of CAN/V2 on a battery event (shutdownDelayMs > 0)
        // IO-68: reads of the global interrupt register 0x60, by reason
        uint16_t interruptPolls;         // no line information or line faulty: read in every call
        uint16_t interruptLineReads;     // INTN1 active (or an edge seen)
        uint16_t interruptFallbackReads; // INTN1 inactive, read anyway after INTERRUPT_FALLBACK_POLL_MS
        uint16_t interruptReadsSkipped;  // calls without a read because INTN1 was inactive
        uint16_t interruptLineMisses;    // a fallback read found an interrupt INTN1 did not show
        uint16_t interruptLineIdle;      // INTN1 active, but nothing pending (stuck low, missing pull-up)
    };

    static uint32_t const STARTUP_RETRY_MS         = 1000U;
    // Transceiver status poll with CAN offline or listen-only; with CAN active it is read in every cyclic() call.
    static uint32_t const TRANSCEIVER_POLL_MS      = 100U;
    static uint32_t const MODE_SUPERVISION_MS      = 1000U;
    /**
     * IO-68: with INTN1 inactive the global interrupt register is still read this often, so an interrupt the line
     * does not show (line broken, pull-up missing to the high side, pin misconfigured) is seen after this time at
     * the latest - and the comparison with the line detects the broken line.
     */
    static uint32_t const INTERRUPT_FALLBACK_POLL_MS = 100U;
    /**
     * IO-68: fallback reads in a row that find an interrupt INTN1 did not show before the line counts as faulty;
     * then the driver reads 0x60 in every call again (as without line) until the next MCU reset. Two, so a single
     * interrupt that arrives between the caller's pin sample and the read is no fault.
     */
    static uint8_t const INTERRUPT_LINE_MISS_LIMIT   = 2U;
    /** IO-68: INTN1 stays high for up to tto(int) = 1.1 ms after the status bits were cleared (data sheet table 91). */
    static uint32_t const INTERRUPT_RELEASE_MS       = 2U;

    Uja113x(::bsp::spi::ISpiDevice& spi, Uja113xConfig const& config);

    /** AH1501 2.1: identification, MTPNV check, watchdog, reset source, interrupts, init, RCC, Normal. */
    Status startup(uint32_t nowMs);

    /** Call periodically (10 ms recommended): watchdog, interrupts, transceiver, mode supervision. */
    void cyclic(uint32_t const nowMs) { cyclic(nowMs, InterruptLine::UNKNOWN); }

    /**
     * IO-68: cyclic() with the state of pin INTN1. The global interrupt register 0x60 is read only when \p line is
     * ACTIVE, every INTERRUPT_FALLBACK_POLL_MS as fallback, and in every call with UNKNOWN or after the line was
     * found faulty (INTERRUPT_LINE_MISS_LIMIT fallback reads in a row with an interrupt the line did not show).
     * Watchdog, transceiver status and mode supervision are not affected. In State::STANDBY the call serves the
     * watchdog as set for Standby, collects the wake-up interrupts (takeInterruptEvents()) and supervises the mode.
     */
    void cyclic(uint32_t nowMs, InterruptLine line);

    /** IO-68: INTN1 was found faulty (see cyclic()); latched until the driver is constructed again (MCU reset). */
    bool isInterruptLineFaulty() const { return _lineFault; }

    /**
     * IO-68: Standby with V1 on - the MCU keeps its supply (e.g. for a low-power mode of its own, PLAT-108) and is
     * woken through INTN1: a wake-up event raises its interrupt, which pulls INTN1 low (data sheet 7.12). Arms the
     * sources of \p wakeup like enterSleep(): CAN wake-up (CWIE, any bus activity - partial networking stays a Sleep
     * feature), the HVIO wake-up inputs, and with `cyclic` the watchdog as timeout watchdog whose overflow raises
     * WDI (a second overflow with WDI pending resets the SBC, so the MCU has one period to react). Without `cyclic`
     * a watchdog not configured autonomous is switched to autonomous: off in Standby while INTN1 is high, timeout
     * watchdog once a wake-up pulls INTN1 low and the device is not in software development mode (data sheet 7.2.4,
     * Table 8).
     * The CAN transceiver is offline in Standby: isCanTransceiverReady() is false until leaveStandby().
     * NOT_READY outside Normal mode, NOT_ALLOWED without a source or with an HVIO input of a bank the device does
     * not have (nothing written), INTERRUPT_PENDING (back in Normal mode, sources removed) if an interrupt is
     * pending - it would wake the MCU at once.
     */
    Status enterStandby(uint32_t nowMs, SleepWakeup const& wakeup);

    /**
     * IO-68: back from Standby to Normal mode: watchdog as configured (written in Standby), wake-up sources of
     * enterStandby() removed, transceiver status read. Interrupts still pending are left to the next cyclic().
     * NOT_READY if not in Standby. If the SBC is no longer in Standby (it went through a reset of its own) or the
     * mode change fails, the driver goes to State::FAILED and cyclic() runs a new startup.
     */
    Status leaveStandby(uint32_t nowMs);

    /**
     * AH1501 2.3.2: enables the wake-up sources of \p wakeup, enters Standby and then Sleep. On success V1 is
     * switched off and the call does not return. Returns INTERRUPT_PENDING (back in Normal mode) if an
     * interrupt is pending, which would otherwise make the SBC reset instead of sleeping, and NOT_ALLOWED if
     * \p wakeup names no source at all (the SBC would reset as well).
     * With CAN wake-up and a filter from setCanWakeupFrame() the partial networking registers are written and
     * verified (NOT_ALLOWED on a device without partial networking, VERIFY_ERROR if the SBC does not
     * confirm the configuration with CPNS); an abort switches partial networking off again.
     * COM-39: CWIE is armed before partial networking, the last write into 0x20..0x2F is CAN control with
     * CPNC = PNCOK = 1 and CMC = 00 (transceiver Offline, data sheet 7.9.1.3), and CPNS is read again right
     * before the Sleep command.
     */
    Status enterSleep(uint32_t nowMs, SleepWakeup const& wakeup);

    /** Sleep with CAN wake-up only, as the board test console "sbc sleep" uses it. */
    Status enterSleep(uint32_t const nowMs)
    {
        return enterSleep(nowMs, SleepWakeup{true, 0U, false, _config.watchdogPeriod});
    }

    /**
     * Selective CAN wake-up for the following Sleep requests (COM-07). \p frame is kept by reference and must
     * outlive the driver like the configuration; nullptr returns to the standard wake-up (any bus activity).
     * Returns NOT_ALLOWED for a filter the SBC cannot represent (see isValidCanWakeupFrame()), the previous
     * setting stays then. Nothing is written here: the registers are programmed by enterSleep(), and a device
     * without partial networking (no /FD variant) refuses the Sleep with a filter set.
     */
    Status setCanWakeupFrame(CanWakeupFrame const* frame);
    CanWakeupFrame const* getCanWakeupFrame() const { return _canWakeupFrame; }

    /**
     * COM-39, board diagnosis ("sbc pn arm"): writes the partial networking registers of the filter from
     * setCanWakeupFrame() and CAN control with CPNC = PNCOK = 1 exactly as enterSleep() does, but stays in Normal
     * mode and keeps the configured CAN mode (the transceiver stays active; selective wake-up only acts in
     * Offline). Afterwards the registers can be read back. NOT_READY outside Normal mode, NOT_ALLOWED without a
     * filter or on a device without partial networking (nothing written), VERIFY_ERROR if the SBC does not
     * confirm with CPNS. The next startup switches partial networking off again.
     */
    Status armPartialNetworking();

    /** Bit rate supported, identifier and compare mask within 11/29 bit, DLC <= 8, a data mask for a DLC > 0. */
    static bool isValidCanWakeupFrame(CanWakeupFrame const& frame);

    /** True for the identification codes of the /FD variants (partial networking, CAN FD passive). */
    static bool supportsPartialNetworking(uint8_t identification)
    {
        return (identification & UJA113X_ID_FD_MASK) != 0U;
    }

    /**
     * How many banks of four HVIOs the device has (data sheet table 78, IO-10): two on the HW variants,
     * one (HVIO1..4) on /FD/4 and none on /FD/0. A board fact, but one the SBC itself reports - so it is
     * derived here and not asked from the configuration a second time.
     */
    static uint8_t hvioBankCount(uint8_t const identification)
    {
        if ((identification & UJA113X_ID_FD_MASK) == 0U)
        {
            return 2U;
        }
        return ((identification & UJA113X_ID_NO_HVIO_MASK) != 0U) ? 0U : 1U;
    }

    /* ---- battery monitor, physical units in and out (IO-10) ---- */

    /** Register code of a threshold in mV; \p roundUp for the undervoltage, down for the overvoltage. */
    static uint8_t batteryThresholdCode(uint16_t mv, bool roundUp);
    static uint16_t batteryThresholdMv(uint8_t code);
    /** Release window in mV to its code (rounded down, so the window is never wider than asked for). */
    static uint8_t batteryHysteresisCode(uint16_t mv);
    static uint16_t batteryHysteresisMv(uint8_t code);
    /** 10 bit conversion result of the monitor ADC to mV (20 V full scale, accuracy +/- 300 mV). */
    static uint16_t batteryAdcMv(uint16_t code);

    /** The thresholds of the configuration after the rounding to the grid of the SBC. */
    BatteryThresholds getBatteryThresholds() const;

    /** IO-33: CAN / V2 currently switched off by the delayed battery shutdown (only with shutdownDelayMs > 0). */
    bool isCanShutOffByBattery() const { return _canShutOff; }
    bool isV2ShutOffByBattery() const { return _v2ShutOff; }

    /**
     * IO-33: HVIO inputs \p config sets up as wake-up inputs (bit i = HVIO(i + 1) over both banks): pins of a
     * configured bank with a wake-up function whose input is sampled (activation not OFF). The board describes
     * which pins are wired that way; the power supply offers exactly these as local wake-up sources.
     */
    static uint8_t configuredWakeInputs(Uja113xConfig const& config);

    /** Reads both conversion results and the supply status. NOT_READY outside Normal mode. */
    Status readBatteryVoltages(BatteryVoltages& voltages);

    /* ---- HVIO, physical units in and out (IO-10) ---- */

    /** Code of the short-circuit detection threshold: the grid value at or below \p ma (30/45/60/90 mA). */
    static uint8_t shortCircuitThresholdCode(uint16_t ma);
    static uint16_t shortCircuitThresholdMa(uint8_t code);
    /** Code of the open-load detection threshold: the grid value at or below \p ma (2/5/10/20 mA). */
    static uint8_t openLoadThresholdCode(uint16_t ma);
    static uint16_t openLoadThresholdMa(uint8_t code);

    /** Two-bit driver status of HVIO \p index (0 = HVIO1) out of a HvioBankStatus::driverStatus. */
    static uint8_t hvioDriverStatus(uint8_t bankStatus, uint8_t indexInBank)
    {
        return static_cast<uint8_t>((bankStatus >> (2U * indexInBank)) & 0x03U);
    }

    /** Levels and driver diagnosis of \p bank (0 or 1). NOT_ALLOWED if the device has no such bank. */
    Status readHvioStatus(uint8_t bank, HvioBankStatus& status);

    /**
     * Switches HVIO \p index (0 = HVIO1) on or off at runtime, keeping its configured function, slope,
     * shut-down response and thresholds. Setting OFF is also the way out of a short circuit: the SBC
     * clears the driver status only when the activation control goes back to 000 (data sheet 7.10.3).
     */
    Status setHvioActivation(uint8_t index, HvioActivation activation);

    /** Activation OFF and back to the configured value, to restart a driver after a short circuit. */
    Status clearHvioFailure(uint8_t index);

    /* ---- EN pin and LIMP (IO-10) ---- */

    /** Drives the LIMP pin low (\p active) or releases it (LHC in the fail-safe control register). */
    Status setLimpHome(bool active);
    /** Changes the EN output mode at runtime, keeping ENDC and ENSC of the configuration. */
    Status setEnMode(EnMode mode);

    /**
     * Register access for diagnosis. Writes to watchdog, mode and MTPNV registers are refused. A write to the CAN
     * control register (0x20) in Normal mode reads the transceiver status right after it (COM-23), so
     * isCanTransceiverReady() follows a transmitter switched off or on by the write at once, not in the next
     * cyclic() call (up to one cycle, during which the CAN controller would keep writing into a transmitter that
     * is off).
     */
    Status readRegister(uint8_t address, uint8_t& value);
    Status writeRegister(uint8_t address, uint8_t value);

    State getState() const { return _state; }
    bool isCanTransceiverReady() const { return _canReady; }
    uint8_t getTransceiverStatus() const { return _transceiverStatus; }
    uint8_t getResetSource() const { return static_cast<uint8_t>(_startup.mainStatus & UJA113X_RSS_MASK); }
    /** HVIO banks of the device found at startup (0, 1 or 2), 0 before a successful startup. */
    uint8_t getHvioBanks() const { return _hvioBanks; }
    bool isLimpHomeActive() const { return _limpHome; }
    StartupInfo const& getStartupInfo() const { return _startup; }
    Counters const& getCounters() const { return _counters; }
    Uja113xConfig const& getConfig() const { return _config; }

    /** Interrupts seen and cleared since the previous call (startup interrupts excluded). */
    InterruptFlags takeInterruptEvents();

    static uint32_t watchdogPeriodMs(WatchdogPeriod period);

    /** Shortest watchdog period that is at least \p ms, MS_4096 above the longest one (SYS-02). */
    static WatchdogPeriod watchdogPeriodFor(uint32_t ms);

    static char const* resetSourceName(uint8_t rss);

private:
    Status read(uint8_t address, uint8_t& value);
    Status write(uint8_t address, uint8_t value);
    Status writeVerified(uint8_t address, uint8_t value, uint8_t mask);
    Status readAndClearInterrupts(uint8_t global, InterruptFlags& flags);
    Status runStartup(uint32_t nowMs);
    Status configureWatchdog(uint32_t nowMs);
    Status triggerWatchdog(uint32_t nowMs, bool inNormalMode);
    Status enterNormal();
    /** Returns true if a transceiver interrupt made it read the transceiver status already. */
    bool handleInterrupts(uint32_t nowMs, InterruptLine line);
    /** IO-68: whether this call reads 0x60 (\p fallback: only because the fallback period has passed). */
    bool interruptReadDue(uint32_t nowMs, InterruptLine line, bool& fallback);
    /** IO-68: cyclic() in Standby: watchdog for Standby, wake-up interrupts, mode supervision. */
    void cyclicStandby(uint32_t nowMs, InterruptLine line);
    /** Mode supervision every MODE_SUPERVISION_MS: a mode other than \p expected means the SBC reset itself. */
    void superviseMode(uint32_t nowMs, uint8_t expected);
    /** Watchdog for Sleep or Standby (written in Standby): timeout with the cyclic period, else autonomous. */
    Status writeLowPowerWatchdog(SleepWakeup const& wakeup);
    /** Value of 0x00 that writeLowPowerWatchdog() writes; the configured value if it writes nothing. */
    uint8_t lowPowerWatchdogValue(SleepWakeup const& wakeup) const;
    /**
     * Way back from Standby (abort of enterSleep()/enterStandby(), leaveStandby()): if the SBC is in Standby,
     * watchdog as configured and Normal mode; then the wake-up sources of \p wakeup removed and, with \p selective,
     * partial networking off. OK if the SBC is in Normal mode afterwards.
     */
    Status returnToNormal(uint32_t nowMs, SleepWakeup const& wakeup, bool selective);
    /** CAN mode with the transmitter on in Normal mode: status supervision in every cyclic() call (COM-08). */
    bool isTransmitterRequested() const;
    void pollTransceiver();
    void serviceWatchdog(uint32_t nowMs);
    uint8_t watchdogControlValue() const;
    uint8_t canControlNormalValue(uint8_t current) const;
    /** \p offline: CMC = 00 in the same write as CPNC/PNCOK (Sleep); otherwise the configured CAN mode. */
    Status configurePartialNetworking(CanWakeupFrame const& frame, bool offline);
    /** CPNS in 0x22: the SBC still holds PNCOK (VERIFY_ERROR otherwise). */
    Status checkPartialNetworkingStatus();
    Status configureBatteryMonitor();
    /** IO-33: the response the SBC itself applies (CSC/V2SC): the configured one, or none with a delay. */
    SupplyShutdown hardwareShutdown(SupplyShutdown configured) const;
    /** IO-33: delayed switch-off of CAN/V2 from the battery status (shutdownDelayMs > 0), called by cyclic(). */
    void superviseBatteryShutdown(uint32_t nowMs);
    Status applyCanShutOff(bool off);
    Status applyV2ShutOff(bool off);
    Status configureHvio();
    Status configureHvioBank(uint8_t bank);
    Status updateFailSafe(uint8_t mask, uint8_t value);
    uint8_t failSafeValue() const;
    /** Register value for 0x37/0x47 out of the HVIO input mask of a Sleep request. */
    uint8_t wakeIrqEnableValue(uint8_t bank, uint8_t wakeInputs) const;
    /** Register value for 0x37/0x47 in Normal mode, as the startup leaves it. */
    uint8_t normalWakeIrqEnable(uint8_t bank) const;

    ::bsp::spi::ISpiDevice& _spi;
    Uja113xConfig const& _config;
    CanWakeupFrame const* _canWakeupFrame;
    State _state;
    StartupInfo _startup;
    Counters _counters;
    InterruptFlags _events;
    uint32_t _lastStartupAttemptMs;
    uint32_t _lastTriggerMs;
    uint32_t _lastTransceiverPollMs;
    uint32_t _lastModeCheckMs;
    uint8_t _transceiverStatus;
    uint8_t _hvioBanks;   // banks the device reported at startup (0, 1 or 2)
    bool _canReady;
    bool _watchdogActive; // watchdog running in Normal mode, needs service
    bool _limpHome;       // LHC as the driver last wrote it
    // IO-33: delayed battery shutdown - event bits (BMUVS/BMOVS) seen last, since when, what is switched off
    uint8_t _batteryEvent;
    uint32_t _batteryEventSinceMs;
    bool _canShutOff;
    bool _v2ShutOff;
    // IO-68: INTN1 and Standby
    uint32_t _lastInterruptReadMs;  // last read of 0x60
    uint32_t _lastInterruptClearMs; // last clear of status bits (INTN1 released, tto(int) running)
    uint8_t _lineMissesInRow;
    bool _lineFault;
    SleepWakeup _standbyWakeup;     // sources armed by enterStandby(), removed by leaveStandby()
    uint8_t _standbyWatchdog;       // value of 0x00 in Standby, written again as trigger
    bool _standbyWatchdogService;   // trigger in Standby (not with cyclic wake-up: its overflow is the wake-up)
};

} // namespace sbc
