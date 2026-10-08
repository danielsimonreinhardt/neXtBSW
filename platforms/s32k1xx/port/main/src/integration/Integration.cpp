/* Module integration of the board port: which systems exist, their lifecycle level and the wiring of
 * roles, selected by the generated integration/moduleConfiguration.h and integration/<module>Configuration.h
 * (tools/nxconfig checks, tools/bswgen/modules.py renders: board devices with module and connect,
 * libs/bsw/<module>/module.yaml, the project's use: selection and modules: parameters).
 *
 * Step towards the generated integration (doc/architecture/README.md, section 5): wiring and values come
 * from configuration, the composition per module is still written by hand here and contains no board or
 * project values. */

#include "integration/Integration.h"

#include "ConsoleOutputFlush.h"
#include "SafeConsoleFlush.h"
// SYS-137: every flush counted in the no-init record of the safe console (reported by the next start)
#include "../safeConsole/SafeConsoleRecord.h"
#include "async/Config.h"
#include "integration/moduleConfiguration.h"
// ARCH-52: generated integration of every module with a binding (module.yaml binding, tools/bswgen); defines the
// objects of those instances, ::integration::binding::addComponents() and their safe_off adapters. The family
// binders (one per connection kind and contract) and the framework come first.
#include "ModuleBinders.h"
#include "ModuleBinding.h"
#include "integration/moduleBinding.h"
#include "lifecycle/StaticBsp.h"
#include "mcu/mcu.h"
#include "systems/BspSystem.h"
// NVM-23: data flash areas of the project (nvm.data_flash), component "dflash" and the run-down step NVM
#include <nvm/nvmDataFlash.h>
#if NX_DFLASH_AREA_COUNT > 0
#include "systems/DataFlashSystem.h"
#endif
#ifdef NX_FLEXNVM_PARTITION_COMMAND
#include "systems/CsecCommand.h"
#endif

#include <async/AsyncBinding.h>
#include <bsp/Uart.h>
// SYS-173: the direct reset of the ECU state management - safe outputs table, then the software system reset
#include <ecuState/DirectReset.h>
#ifdef NX_SHUTDOWN_HANG_COMMAND
#include "ShutdownHang.h" // SYS-173 test build: level-2 component that hangs in its shutdown
#endif
#include <hardFaultHandler/safeOutputs.h>
#include <reset/softwareSystemReset.h>
#include <bsp/uart/uartConfiguration.h>
#include <etl/alignment.h>
#include <etl/span.h>
#include <lifecycle/LifecycleManager.h>
#include <safeSupervisor/SafeConsole.h>
// SYS-117/IO-45: a project with a degraded mapping (tools/nxconfig derives NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_*)
// locks the drivers of a degraded group; every other project compiles this file as before
#include <integration/safeSupervisorConfiguration.h>
// SYS-196: DTC of a foreign safe_off level taken back, and the values bsw.safeoff.* (with or without the hold)
#include "integration/SafeOffTakeBackReport.h"
#ifdef NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS
#include <safeSupervisor/SafeSupervisor.h>
#endif

#if NX_MODULE_SBCUJA113X_COUNT > 1
#error "more than one UJA113x: SbcSystem supports one SBC (sbcConfiguration.h)"
#endif
#if NX_MODULE_SBCUJA113X_COUNT == 1
#include "sbc/Uja113xPowerSupply.h"
#include "systems/SbcSystem.h"
#else
// Role power_supply without a module that provides it (SYS-01)
#include <ecuState/PermanentPowerSupply.h>
#endif

// Smart high-side switches (ARCH-66): no block here any more - module.yaml binding, the trait
// highSideSwitch/HighSideSwitchBinding.h and the generated integration/moduleBinding.h above create one system per
// instance, register it and attach its safe_off adapter (generic SafeOffOutput, ModuleBinding.h).

// TMC6200 gate drivers (IO-05/IO-25): one system per instance of NX_MODULE_GDTMC6200_INSTANCES (board order), SPI
// bus and PWM wiring from the board (NX_DEVICE_<device>_SPI_*, _PWM_*), device facts from gateDriverConfiguration.h.
#if NX_MODULE_GDTMC6200_COUNT > 0
#define INTEGRATION_GATE_DRIVER 1
#include "bsp/clock/boardClock.h"
#include "bsp/gd/gateDriverConfiguration.h"
#include "integration/gdTmc6200Configuration.h"
#include "io/Io.h"
#include "mcu/mcu.h"
#include "spi/Lpspi.h"
#include "systems/GateDriverSystem.h"
// IO-45: the phase duty source of the enable rule, read from the timer of the PWM group bound to the gate driver
#include "GateDriverDutySource.h"
#if NX_PWM_GROUP_COUNT > 0
#include "ftmPwm/FtmPwmGroup.h"
#endif
#else
#define INTEGRATION_GATE_DRIVER 0
#endif

// IO-32: debug halt guard of the power stage - after a debugger halt the PWM group stays inhibited and every
// power stage enable off until the next explicit enable (DebugHaltGuard.h). Present if the project has a PWM group
// (NX_PWM_GROUP_COUNT, bsp/io/outputPwm/ftmPwmConfiguration.h through lifecycle/StaticBsp.h) or a module with a
// safe_off output - today a gate driver or a module of the generated binding (ARCH-66: the high-side switch, IO-37).
// A project with neither has no power output the deadman could switch off; its safe outputs table is empty.
#if INTEGRATION_GATE_DRIVER || (NX_PWM_GROUP_COUNT > 0) || (NX_BINDING_SAFE_OFF_COUNT > 0)
#define INTEGRATION_DEBUG_HALT_GUARD 1
#include "DebugHaltGuardSystem.h"
#include "SafeOutputsShutdownStep.h" // SYS-174: run-down step SAFE_OUTPUTS
// NX_SAFE_OFF_OUTPUT_COUNT: the safe_off outputs the deadman switches off need an adapter each (static_assert below)
#include "bsp/safeOutputs/safeOutputsConfiguration.h"
#if INTEGRATION_GATE_DRIVER
#include "Tmc6200PowerStageOutput.h"
#endif
// SYS-151: the commanded level of every safe_off output held in the same poll, outside S1 as well
#include "SafeOffCommandHold.h"
#include "SafeOffPins.h"
// SYS-194: the take-backs as DEVICE_REPORT, the hold's box handed to the safety task (safety::safeOffReportBox())
#include <safeLifecycle/SafetyManager.h>
#include <safeSupervisor/DeviceReportBox.h>
// SYS-196: the take-backs also as a DTC of the fault memory (QM, BSP task)
#include "SafeOffTakeBackRecord.h"

#include <dem/OccurrenceReport.h>
// SYS-161 (SR-151-2 a): console "safeoff show", the take-backs of the hold per safe_off output (only with console)
#if !defined(NX_CONSOLE) || NX_CONSOLE
#include "SafeOffHoldCommand.h"

#include <console/AsyncCommandWrapper.h>
#include <console/ConsoleContext.h>
#endif
#else
#define INTEGRATION_DEBUG_HALT_GUARD 0
#endif

// SYS-139 (decision C2 (a)): a project in which a PWM group allows an active short keeps it (ShortHold.h). The build
// (NX_SAFE_OUTPUT_SELECT, hardFaultHandler/CMakeLists.txt) and the generat must agree, as in SafeOutputSelection.cpp.
#if defined(NX_SAFE_OUTPUT_SELECT)
#if !INTEGRATION_DEBUG_HALT_GUARD || !defined(NX_SAFE_OUTPUT_SELECT_GENERATED)
#error "SYS-139: the build (NX_SAFE_OUTPUT_SELECT) and the generated safe outputs table disagree - configure again"
#endif
#include "ShortHold.h"
#elif INTEGRATION_DEBUG_HALT_GUARD && defined(NX_SAFE_OUTPUT_SELECT_GENERATED)
#error "SYS-139: the generated safe outputs table has value sets, but the build lacks NX_SAFE_OUTPUT_SELECT"
#endif

// SYS-38: ROM check (SM-14) only in an image whose reference CRC the build wrote (cmake/NxRomCheck.cmake)
#ifdef NX_ROM_CHECK_IMAGE
#include "RomCheckSystem.h"
#endif

// IO-28: console group "i2c" over the I2C buses of the project (use.i2c, generated bsp/i2c/i2cConfiguration.h;
// buses and command in bspConfiguration, bsp/i2c/I2cBuses.h)
#include <bsp/i2c/i2cConfiguration.h>
#if NX_I2C_BUS_COUNT > 0
#include <bsp/i2c/I2cCommand.h>
#include <console/AsyncCommandWrapper.h>
#include <console/ConsoleContext.h>
#endif

// ARCH-52: the hand-written blocks below register these modules at level 1 in TASK_BSP. Their manifests must declare
// the same lifecycle (generated NX_MODULE_<MODULE>_LIFECYCLE_*), otherwise the build stops instead of ignoring the
// manifest - until the modules move to the generated binding, which registers them where the manifest says (ARCH-66:
// the high-side switch has moved).
#if NX_MODULE_SBCUJA113X_COUNT == 1
static_assert(
    (NX_MODULE_SBCUJA113X_LIFECYCLE_LEVEL == 1U) && (NX_MODULE_SBCUJA113X_LIFECYCLE_TASK == TASK_BSP),
    "sbcUja113x: module.yaml lifecycle differs from its hand-written integration (level 1, task bsp)");
#endif
#if INTEGRATION_GATE_DRIVER
static_assert(
    (NX_MODULE_GDTMC6200_LIFECYCLE_LEVEL == 1U) && (NX_MODULE_GDTMC6200_LIFECYCLE_TASK == TASK_BSP),
    "gdTmc6200: module.yaml lifecycle differs from its hand-written integration (level 1, task bsp)");
#endif

// Pastes after macro expansion of both arguments (board entry names from NX_DEVICE_<device>_IO_*).
#define INTEGRATION_CONCAT_(a, b) a##b
#define INTEGRATION_CONCAT(a, b)  INTEGRATION_CONCAT_(a, b)

#ifdef PLATFORM_SUPPORT_CAN
#include "CanShutdownStep.h"
#include "systems/CanSystem.h"

#if NX_CAN_PHY_COUNT_CANPHYPASSIVE > 0
// module canPhyPassive: linked by main/CMakeLists.txt when a bus of the project uses it (NX_MODULES)
#include "can/PassiveCanPhy.h"
#endif

#if NX_CAN_BUS_COUNT == 0
#error "CAN support is on, but the project uses no CAN bus (use.can)"
#endif
// Role can_phy per bus (ARCH-07): the buses of the modules wired below must be all buses of the project.
static_assert(
    NX_CAN_PHY_COUNT_SBCUJA113X + NX_CAN_PHY_COUNT_CANPHYPASSIVE == NX_CAN_BUS_COUNT,
    "a CAN bus of the project has a transceiver module this integration does not wire (see below)");
#if NX_CAN_PHY_COUNT_SBCUJA113X > 0 && NX_MODULE_SBCUJA113X_COUNT == 0
#error "a CAN bus is served by the SBC module, but the project does not use the SBC (use.spi)"
#endif
#endif

namespace
{
using Hook = void (*)();

// Role early_init: first in main(), before the MCU watchdog fast test.
Hook const earlyInitHooks[] = {
#if NX_MODULE_SBCUJA113X_COUNT == 1
    // SYS-07: take back the SBC reset count of the watchdog fast-test reset that led to this start.
    &::systems::SbcEarlyStart::compensateResetCounter,
#endif
    nullptr,
};

// After the MCU watchdog is enabled, before the BSP initialization.
Hook const preBspInitHooks[] = {
#if (NX_MODULE_SBCUJA113X_COUNT == 1) && !defined(NX_PRODUCT_BUILD) // SYS-74: board test, not in the product
    // Board test "sbc failstart <n>": reset before the SBC start like a crash in the BSP init would.
    &::systems::SbcEarlyStart::failStartIfArmed,
#endif
#ifdef NX_FLEXNVM_PARTITION_COMMAND
    // NVM-17 (test build): CMD_INIT_RNG requested by "csec rng" before the reset - here, before the safety task runs,
    // because it blocks the core for several hundred ms (systems/CsecCommand.h)
    &::systems::csecInitRngAtStart,
#endif
    nullptr,
};

void runHooks(Hook const* hook)
{
    for (; *hook != nullptr; ++hook)
    {
        (*hook)();
    }
}

#if INTEGRATION_GATE_DRIVER
/* SYS-16: the label of a gate driver (its instance name) is a "%s" argument of the GateDriverSystem log lines, which
 * are sized for at most this many characters - OpenBSW's buffered logger copies it into one entry of 128 bytes and
 * cuts what does not fit ("<?>"). tools/logcheck (bounds.yaml) relies on the static_assert per instance below. */
constexpr size_t GATE_DRIVER_LABEL_MAX_CHARS = 16U;

/* One 40-bit TMC6200 datagram (gd::ITmc6200Spi) as one long frame of the SPI master (L1 contract
 * bsp::spi::ISpiDevice::transferLong). The master is set up with the first datagram, i.e. in the task of the gate
 * driver system (GateDriverSystem::init), after the clock driver has enabled the peripheral. The four pins of the
 * SPI bus (board.yaml spi[]) are muxed by staticBsp in main() for every SPI bus the project uses, before any system
 * starts (ARCH-38, lifecycle/SpiBusPins.h) - without them the LPSPI runs internally, reports no error and reads 0
 * (board test 2026-09-18). A failed transfer is a communication error of the driver: DRV_EN stays off, no register
 * is written. */
class Tmc6200SpiDevice : public ::gd::ITmc6200Spi
{
public:
    explicit Tmc6200SpiDevice(::bsp::spi::LpspiConfig const& config) : _master(config), _started(false) {}

    bool transfer(uint64_t const tx, uint64_t& rx) override
    {
        if (!_started)
        {
            _master.init();
            _started = true;
        }
        ::bsp::spi::ISpiDevice& device = _master;
        return device.transferLong(tx, rx);
    }

private:
    ::bsp::spi::Lpspi _master;
    bool _started;
};

/* Per instance: register configuration (project parameters, GCONF singleline 0 when a complementary PWM group
 * drives the device - board pwm[].gate_driver - otherwise the device reset value), SPI master on the board bus,
 * names and the DRV_EN/FAULT pins (board.yaml devices[].connect). Datasheet SPI mode and SCK limit:
 * bsp/gd/gateDriverConfiguration.h. */
// clang-format off
#define GATE_DRIVER_DATA(device)                                                                          \
    static_assert(NX_DEVICE_##device##_SPI_COUNT == 1, "TMC6200 " #device " needs exactly one SPI bus");   \
    constexpr ::gd::Tmc6200Config device##_GD_CONFIG = GATE_DRIVER_CONFIG(                                \
        device, (NX_DEVICE_##device##_PWM_COMPLEMENTARY != 0) ? static_cast<int16_t>(0) : ::gd::KEEP);    \
    static_assert(::gd::isValidTmc6200Config(device##_GD_CONFIG),                                         \
                  "TMC6200 configuration of " #device " invalid (project.yaml modules." #device ")");      \
    ::bsp::spi::LpspiConfig const device##_GD_SPI_CONFIG = {                                              \
        INTEGRATION_CONCAT(IP_, NX_DEVICE_##device##_SPI_INSTANCE),                                       \
        INTEGRATION_CONCAT(INTEGRATION_CONCAT(NX_CLOCK_, NX_DEVICE_##device##_SPI_INSTANCE), _HZ),       \
        ::bsp::gd::SPI_BAUDRATE_LIMIT_HZ,                                                                 \
        static_cast<uint8_t>(NX_DEVICE_##device##_SPI_PCS),                                               \
        ::bsp::gd::SPI_FRAME_BITS,                                                                        \
        ::bsp::gd::SPI_CPOL,                                                                              \
        ::bsp::gd::SPI_CPHA,                                                                              \
        ::bsp::gd::SPI_TIMEOUT_US};                                                                       \
    static_assert(::bsp::spi::isSupportedLpspiFrameBits(::bsp::gd::SPI_FRAME_BITS),                       \
                  "TMC6200 frame size not supported by the LPSPI master");                                \
    Tmc6200SpiDevice device##_GD_SPI{device##_GD_SPI_CONFIG};                                             \
    constexpr ::systems::GateDriverNames device##_GD_NAMES                                                \
        = {NX_DEVICE_##device##_PARAM_CONSOLE, #device, "TMC6200 gate driver " #device};                  \
    static_assert(sizeof(#device) - 1U <= GATE_DRIVER_LABEL_MAX_CHARS,                                    \
                  "instance name " #device " longer than GATE_DRIVER_LABEL_MAX_CHARS (log lines)");       \
    ::systems::BiosTmc6200Io device##_GD_IO{                                                              \
        ::bios::Output::NX_DEVICE_##device##_IO_DRV_EN,                                                   \
        ::bios::DigitalInput::NX_DEVICE_##device##_IO_FAULT};
// clang-format on
NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_DATA)
#undef GATE_DRIVER_DATA

#if NX_PWM_GROUP_COUNT > 0
/* IO-45: the phase duty source of a gate driver bound to the PWM group of the project (board pwm[].gate_driver,
 * generated as NX_DEVICE_<device>_PWM_COMPLEMENTARY 1). This family port drives one PWM group per project
 * (tools/bswgen collect_pwm, ComplementaryPwm), so a bound gate driver is bound to exactly that one. The source reads
 * the timer, not a duty value of a writer, and converts to per mille once (GateDriverDutySource.h); Tmc6200::enable()
 * calls it in TASK_BSP. */
static_assert(NX_PWM_GROUP_COUNT == 1, "IO-45: the gate driver source reads the one PWM group of the project");

bool pwmGroupPhaseDuty(::gd::PhaseDuty& duty)
{
    ::bios::ftmPwm::FtmPwmGroup const group(::bios::ftmPwm::ftm());
    return ::integration::readPhaseDuty(group, ::bios::ftmPwm::PAIR_COUNT, ::bios::ftmPwm::PERIOD_TICKS, duty);
}
#endif
#endif

#if INTEGRATION_DEBUG_HALT_GUARD
/* IO-32: outputs the guard switches off after a debug halt - the DRV_EN of every gate driver instance and the
 * safe_off outputs of the generated binding (ARCH-66: the IN of every high-side switch instance, IO-37), each attached
 * to its driver when the system is created - and the PWM group of the project. One adapter per safe_off output of the
 * generated table (module instances in board order, gate drivers first is not assumed: the count is what the
 * static_assert below checks, the order inside the list does not matter, every entry is switched off). */
#if INTEGRATION_GATE_DRIVER
#define GATE_DRIVER_HALT_OUTPUT(device) ::integration::Tmc6200PowerStageOutput device##_GD_HALT_OUTPUT;
NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_HALT_OUTPUT)
#undef GATE_DRIVER_HALT_OUTPUT
#define GATE_DRIVER_HALT_OUTPUT_REF(device) &device##_GD_HALT_OUTPUT,
#endif
#if INTEGRATION_GATE_DRIVER || (NX_BINDING_SAFE_OFF_COUNT > 0)
::integration::IPowerStageOutput* const debugHaltOutputs[] = {
#if INTEGRATION_GATE_DRIVER
    NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_HALT_OUTPUT_REF)
#endif
    // ARCH-52: the safe_off adapters of the generated binding (integration/moduleBinding.h)
    NX_BINDING_SAFE_OFF_OUTPUT_REFS
};
constexpr uint8_t DEBUG_HALT_OUTPUT_COUNT
    = static_cast<uint8_t>(sizeof(debugHaltOutputs) / sizeof(debugHaltOutputs[0]));
#else
::integration::IPowerStageOutput* const* const debugHaltOutputs = nullptr;
constexpr uint8_t DEBUG_HALT_OUTPUT_COUNT                        = 0U;
#endif
#if INTEGRATION_GATE_DRIVER
#undef GATE_DRIVER_HALT_OUTPUT_REF
#endif
/* IO-32 (user decision 2026-09-19, question 6): the halt deadman switches the whole safe outputs table off, behind
 * the back of the drivers. Every output a module declares safe_off in its module.yaml therefore needs an adapter
 * here, so that its driver state follows the pin and an explicit enable of it releases the PWM again. A new module
 * with safe_off and no adapter stops the build instead of leaving a driver that believes it is still enabled.
 * SYS-36: the same adapters carry the safe state lock (attach() below hands each driver the S1 latch), so this
 * check also makes sure that no safe_off output of the manifests escapes the hold of S1. */
static_assert(
    DEBUG_HALT_OUTPUT_COUNT == NX_SAFE_OFF_OUTPUT_COUNT,
    "every safe_off output of a module needs an integration::IPowerStageOutput adapter for the debug halt guard "
    "(gdTmc6200 drv_en in Tmc6200PowerStageOutput.h, every module with a binding: SafeOffOutput of "
    "integration/moduleBinding.h)");
#if NX_PWM_GROUP_COUNT > 0
::integration::FtmPwmInhibit debugHaltPwmInhibit;
::integration::IPwmInhibit* const debugHaltPwm = &debugHaltPwmInhibit;
#else
::integration::IPwmInhibit* const debugHaltPwm = nullptr;
#endif
::integration::DebugHaltGuard debugHaltGuard(debugHaltOutputs, DEBUG_HALT_OUTPUT_COUNT, debugHaltPwm);
// SYS-36: S1 held in every poll of the guard system while it is latched - the table again and every safe_off
// driver locked (SafeStateHold.h). Same adapter list as the guard: one per safe_off output of the manifests.
// SYS-35: every entry into S1 hands the PWM inhibit of the table walk to the guard, which releases it at the next
// explicit enable - possible only after SafeState::leaveSafeState() has cleared the latch.
::integration::SafeStateHold safeStateHold(
    debugHaltOutputs,
    DEBUG_HALT_OUTPUT_COUNT,
    &::integration::safeStateLatched,
    &::integration::safeOutputsSwitchOff,
    &::integration::safeStateEntryCount,
    debugHaltGuard);

/* SYS-151 (safety review night 2026-10-01, SR-147-3 b): in every poll of the guard system a safe_off output that
 * reads active without being commanded active by its driver is driven inactive - outside S1 too, for every safe_off
 * output of the generated table (SafeOffCommandHold.h, the pins and the commanded level in SafeOffPins.h). */
// SYS-161 (safety review 2026-10-01, SR-151-2 a): the take-backs per safe_off output, written by the hold (TASK_BSP)
uint32_t safeOffTakeBacks[(::integration::safeoff::COUNT > 0U) ? ::integration::safeoff::COUNT : 1U] = {};

/* SYS-194 (safety review BL38-P1 2026-10-03, SYS147-B1 b): every take-back is reported to the safe supervisor as
 * DEVICE_REPORT (S3, never switching off) - the hold's own event box in ordinary RAM (one box per source, SYS-37),
 * posted by the hold in TASK_BSP and delivered by the safety task (safety::safeOffReportBox() below,
 * SafetyManager::cyclic). Word: NO_BUS, NO_DEVICE (no board device: a mechanism of the integration), fault class
 * ACTUATOR_STATE (an output active although commanded off), detail = the output's number of the console group out. */
uint32_t safeOffReportedTakeBacks[(::integration::safeoff::COUNT > 0U) ? ::integration::safeoff::COUNT : 1U] = {};
} // namespace

namespace integration
{
::safety::DeviceReportBox safeOffReports;
} // namespace integration

namespace
{
constexpr uint32_t SAFE_OFF_REPORT_WORD = ::safety::device::context(
    ::safety::device::NO_BUS, ::safety::device::NO_DEVICE, ::safety::device::FaultClass::ACTUATOR_STATE, 0U);
static_assert(::safety::device::isWellFormed(SAFE_OFF_REPORT_WORD), "SYS-194: the box refuses a malformed word");

/* SYS-196 (decision SYS194-4 b): the same reports also as the DTC of safety.safe_off_take_back, so the incident
 * survives the reset (fault memory, NVM-03). The record turns every report with new take-backs into one occurrence
 * (SafeOffTakeBackRecord.h: at most one per second and output, independent of the box), the occurrence report hands
 * them to the fault memory in the BSP task (integration::reportSafeOffTakeBacks below). QM RAM, recording only. */
uint32_t safeOffRecordedTakeBacks[(::integration::safeoff::COUNT > 0U) ? ::integration::safeoff::COUNT : 1U] = {};
::dem::OccurrenceReport safeOffTakeBackDtc("safety.safe_off_take_back");

void safeOffTakeBackOccurred() { safeOffTakeBackDtc.occurred(); }

::integration::SafeOffTakeBackRecord safeOffTakeBackRecord(
    ::integration::safeoff::COUNT, safeOffTakeBacks, safeOffRecordedTakeBacks, &safeOffTakeBackOccurred);

bool reportSafeOffTakeBack(uint8_t const index)
{
    int32_t const output = ::integration::safeoff::outputOf(index);
    // the console number names the output in the detail byte; one beyond it would name another output - 0xFF then
    uint8_t const detail = ((output >= 0) && (output < 0xFF)) ? static_cast<uint8_t>(output) : uint8_t{0xFFU};
    safeOffTakeBackRecord.onReport(index, detail); // SYS-196: before the box, whatever it answers
    return ::integration::safeOffReports.report(SAFE_OFF_REPORT_WORD | detail);
}

::integration::SafeOffCommandHold safeOffCommandHold(
    ::integration::safeoff::COUNT,
    &::integration::safeoffpins::commanded,
    &::integration::safeoffpins::active,
    &::integration::safeoffpins::switchOff,
    safeOffTakeBacks,
    &reportSafeOffTakeBack,
    safeOffReportedTakeBacks);

// SYS-161 (SR-151-2 a, PLAT-106): console "safeoff show" only in an image with console (NX_CONSOLE). The command
// runs in the console context; the snapshot is taken in the task of the hold (TASK_BSP) and only copies words, so the
// output never holds the 1-ms poll (IO-42).
#if !defined(NX_CONSOLE) || NX_CONSOLE
void takeSafeOffSnapshot(::integration::SafeOffHoldCommand::Snapshot& snapshot)
{
    auto work = [&snapshot]()
    {
        uint8_t const count = safeOffCommandHold.getCount();
        snapshot.count      = count;
        snapshot.total      = safeOffCommandHold.getTakeBacks();
        snapshot.reported   = safeOffCommandHold.getReportedTakeBacks(); // SYS-194
        snapshot.reports    = safeOffCommandHold.getReports();
        for (uint8_t i = 0U; (i < count) && (i < ::integration::SafeOffHoldCommand::MAX_OUTPUTS); ++i)
        {
            snapshot.takeBacks[i] = safeOffCommandHold.getTakeBacksOf(i);
            snapshot.commanded[i] = ::integration::safeoffpins::commanded(i);
            snapshot.active[i]    = ::integration::safeoffpins::active(i);
        }
    };
    ::console::runInOwnerContext(TASK_BSP, work);
}

::integration::SafeOffHoldCommand safeOffHoldCommand(&takeSafeOffSnapshot);
::console::AsyncCommandWrapper asyncSafeOffHoldCommand(safeOffHoldCommand, ::console::CONSOLE_CONTEXT);
#endif

// SYS-174 (safety review 2026-10-02, SYS69-S5): run-down step ShutdownStep::SAFE_OUTPUTS - the walk of the safe
// outputs table, the PWM inhibit handed to the guard, and the lock below set for the rest of the run-down
// (SafeOutputsShutdownStep.h). Runs in TASK_BSP like the guard (powerSupplyContext()).
::integration::SafeOutputsShutdownStep safeOutputsShutdownStep(&::integration::safeOutputsSwitchOff, &debugHaltGuard);

// SYS-35: the lock attach() hands every safe_off driver - the S1 latch, and an entry the hold has not handled yet
// (entered and left again between two polls), so that no driver drives its output again before the hold has switched
// it off and handed the PWM inhibit to the guard. SYS-174: and the run-down from its safe outputs step on.
bool safeStateLock() { return safeStateHold.isLocking() || safeOutputsShutdownStep.isLocking(); }

#if (INTEGRATION_GATE_DRIVER || (NX_BINDING_SAFE_OFF_COUNT > 0)) && (NX_PWM_GROUP_COUNT > 0) \
    && defined(NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS)
/* IO-45 (partner-and-degraded.md 5.4, decision C2 (a): the gate-driver enable bound to a PWM group follows the state
 * of its group, latch per group): the enable of a gate driver bound to the PWM group is in the safe-output group of
 * that PWM group (tools/nxconfig/degraded.py), so the degraded walk pulls it low with the channels - and its driver is
 * locked like in S1 while that group is degraded: enable() refused ("refused: safe state"), DRV_EN low without an
 * ENABLE_MISMATCH fault. The group is the one of the PWM group's table entry (the first entry: tools/bswgen puts the PWM
 * groups first, and this family port has one), which by construction also holds the enable. Until the power cycle
 * (decision B6 (a)); an implausible latch of the supervisor locks as well. */
constexpr uint32_t PWM_GROUP_TABLE_ENTRY = 0U;

bool gateDriverGroupLock()
{
    return safeStateLock() // S1 and (SYS-174) the run-down from its safe outputs step on
           || ::safety::SafeSupervisor::getInstance().isEntryDegraded(PWM_GROUP_TABLE_ENTRY);
}
#endif
#endif
} // namespace

#if NX_BINDING_SAFE_OFF_COUNT > 0
namespace integration
{
namespace binding
{
// ARCH-52: the lock of the safe_off drivers of the generated binding - the same as for the hand-written adapters
// above: the S1 latch, and for an instance a PWM group drives also the degraded state of that group (IO-45)
SafeOffLock safeOffLock(bool const pwmBound)
{
#if (NX_PWM_GROUP_COUNT > 0) && defined(NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS)
    return pwmBound ? &gateDriverGroupLock : &safeStateLock;
#else
    (void)pwmBound;
    return &safeStateLock;
#endif
}
} // namespace binding
} // namespace integration
#endif

#ifdef NX_PWM_GROUP_LOCK
#ifndef NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS
#error "SYS-123: NX_PWM_GROUP_LOCK without a degraded mapping of the safe supervisor (tools/bswgen, tools/nxconfig)"
#endif
namespace bios
{
namespace ftmPwm
{
/* SYS-123: the lock of the PWM group of the project (bsp/io/outputPwm/ftmPwmConfiguration.h, NX_PWM_GROUP_LOCK):
 * true while the safe-output group that holds the group's entry of the safe outputs table is degraded - the latch of
 * the safe supervisor (protected RAM, read only, from any task; an implausible latch reads as every group degraded).
 * The group is the first entry of the table (tools/bswgen puts the PWM groups first; this family port has one).
 * Asked by ComplementaryPwm (init after every reset, every duty) and by the debug halt guard's release. */
bool groupLocked() { return ::safety::SafeSupervisor::getInstance().isEntryDegraded(0U); }
#if NX_FTM_PWM_SHORT_HOLD
/* SYS-139 (ShortHold.h rule 3): a degraded group gets its selected safe state - the value of the PWM inhibit. */
uint32_t lockedOutputControl() { return ::integration::FtmPwmInhibit::forcedValue(); }
#endif
} // namespace ftmPwm
} // namespace bios
#endif

#if INTEGRATION_DEBUG_HALT_GUARD
/* SYS-133 (BEF-003, BEF-004): the two strong definitions of the halt deadman that belong to no class, out of line in
 * this .cpp since SYS-133 (they stood in DebugHaltGuardSystem.h: a second #include would have defined both twice).
 * Only a project with a power stage has them - a project without one keeps the weak vector and the weak default and
 * MCR[DBG_EN] = 0.
 *
 * **Never make them inline or move them into a header.** deadmanExpectedConfiguration() overrides the weak default of
 * safeSupervisionCheck (nullptr: nothing to check); an inline definition is no strong definition for the linker, the
 * weak default wins and SM-06 no longer checks the deadman - no host test sees that. They also stay in this file
 * rather than in a new one: Integration.cpp is pulled from the archive of `main` in every case, a file of its own
 * would be an archive member that nothing needs, since the weak definitions already satisfy the references.
 * integration/test/test_deadman_expected_elf.py looks at the image (exactly one definition, strong, returns
 * EXPECTED_CONFIGURATION). */

/* Strong definition of the vector the startup leaves weak (ARMv7-M platforms/s32k1xx/port/main/src/bsp/startUp.S,
 * ARMv6-M the generated startup/nxIrqVectors.inc with tools/bswgen FAMILY_IRQ_HANDLERS). */
extern "C" void LPIT0_Ch1_IRQHandler();

extern "C" void LPIT0_Ch1_IRQHandler() { ::integration::LpitHaltDeadman::onInterrupt(); }

/* SYS-170: the startup routes every vector through Safe_<handler>; the deadman is a safety path without the hook
 * wrapper (outputs off before any call), so its vector name is an alias of the handler itself (safeLifecycle/
 * IsrWrapper.cpp has the weak default for projects without the guard). */
extern "C" void Safe_LPIT0_Ch1_IRQHandler() __attribute__((alias("LPIT0_Ch1_IRQHandler")));

namespace safety
{
namespace supervision
{
/* SYS-48 (open point 1 of SYS-41): the owner of the deadman hands its configuration to the check of the safety task
 * (SafetyManager::checkSupervisionPeripherals, SM-06). The values are the ones LpitHaltDeadman::init() writes
 * (EXPECTED_CONFIGURATION), a constant in flash; the check starts with the run of "safety", after the init of
 * "haltguard" in the same level. Before SYS-48 the weak default won on the target and the deadman was never
 * re-checked. */
LpitDeadmanConfiguration const* deadmanExpectedConfiguration()
{
    return &::integration::LpitHaltDeadman::EXPECTED_CONFIGURATION;
}
} // namespace supervision

/* SYS-194: the event box of the safe_off hold for the safety task (safeLifecycle/SafetyManager.h) - the address of an
 * object in ordinary RAM, a constant; the same rule as above: out of line here, never inline. */
DeviceReportBox* safeOffReportBox() { return &::integration::safeOffReports; }
} // namespace safety
#endif

/* SYS-196: the DTC of a foreign safe_off level taken back and the values bsw.safeoff.* (integration/
 * SafeOffTakeBackReport.h). A project without the hold reports nothing and answers "none" (nxconfig refuses the DTC
 * and the values there anyway). */
namespace integration
{
#if INTEGRATION_DEBUG_HALT_GUARD
void reportSafeOffTakeBacks() { safeOffTakeBackDtc.update(); }
#else
void reportSafeOffTakeBacks() {}
#endif
} // namespace integration

namespace bswstatus
{
namespace rte
{
void readSafeOffStatus(uint8_t& output, uint32_t& outputTakeBacks, uint32_t& takeBacks)
{
#if INTEGRATION_DEBUG_HALT_GUARD
    output          = safeOffTakeBackRecord.lastOutput();
    outputTakeBacks = safeOffTakeBackRecord.lastOutputTakeBacks();
    takeBacks       = safeOffCommandHold.getTakeBacks();
#else
    output          = 0xFFU; // SafeOffTakeBackRecord::NO_OUTPUT: no safe_off output, nothing taken back
    outputTakeBacks = 0U;
    takeBacks       = 0U;
#endif
}
} // namespace rte
} // namespace bswstatus

// PLAT-106: console "i2c" only in an image with console (project.yaml console.enabled, NX_CONSOLE)
#if (NX_I2C_BUS_COUNT > 0) && (!defined(NX_CONSOLE) || NX_CONSOLE)
namespace
{
// IO-28: the transfers run in the console task, the only user of the buses so far (bsp/i2c/I2cBuses.h: one task
// context per bus). A device driver on a bus later needs the same context or a lock in front of the bus.
::bsp::i2c::I2cCommand i2cCommand;
::console::AsyncCommandWrapper asyncI2cCommand(i2cCommand, ::console::CONSOLE_CONTEXT);
} // namespace
#endif

namespace integration
{
void earlyInit() { runHooks(earlyInitHooks); }

void preBspInit() { runHooks(preBspInitHooks); }
} // namespace integration

namespace platform
{
extern StaticBsp staticBsp;

::etl::typed_storage<::systems::BspSystem> bspSystem;
#if NX_DFLASH_AREA_COUNT > 0
::etl::typed_storage<::systems::DataFlashSystem> dataFlashSystem;
#endif
#if NX_MODULE_SBCUJA113X_COUNT == 1
::etl::typed_storage<::systems::SbcSystem> sbcSystem;
#endif
#if INTEGRATION_GATE_DRIVER
#define GATE_DRIVER_STORAGE(device) ::etl::typed_storage<::systems::GateDriverSystem> device##_GD_SYSTEM;
NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_STORAGE)
#undef GATE_DRIVER_STORAGE
#endif
#if INTEGRATION_DEBUG_HALT_GUARD
::etl::typed_storage<::integration::DebugHaltGuardSystem> debugHaltGuardSystem;
#endif
#if INTEGRATION_GATE_DRIVER && defined(NX_SAFE_OUTPUT_SELECT)
/* SYS-139 (decision C2 (a), ShortHold.h rule 1): the lock and the kept enable of a gate driver bound to the PWM group.
 * The base lock is the one the driver had before (S1, and the degraded group of IO-45); it reads "not locked" only
 * while the group is in a short and the driver is enabled - so the enable stays on, and a driver that is off stays
 * locked (enable() refused in S1: nothing is switched on). */
bool gateDriverBaseLock()
{
#if (NX_PWM_GROUP_COUNT > 0) && defined(NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS)
    return gateDriverGroupLock();
#else
    return safeStateLock();
#endif
}

#define GATE_DRIVER_SHORT_HOLD(device)                                                                   \
    bool device##_GD_KEEP_ENABLE()                                                                       \
    {                                                                                                    \
        return ::integration::shortHold::keepEnable(device##_GD_SYSTEM->getDriver().isEnabled());        \
    }                                                                                                    \
    bool device##_GD_SHORT_LOCK()                                                                        \
    {                                                                                                    \
        return ::integration::shortHold::lock(gateDriverBaseLock(), device##_GD_SYSTEM->getDriver().isEnabled()); \
    }
NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_SHORT_HOLD)
#undef GATE_DRIVER_SHORT_HOLD
#endif
#ifdef NX_ROM_CHECK_IMAGE
::integration::RomCheckSystem romCheckSystem(::async::AsyncBinding::AdapterType::TASK_IDLE);
#endif
#ifdef PLATFORM_SUPPORT_CAN
::etl::typed_storage<::systems::CanSystem> canSystem;
// Transceivers (role can_phy) in the order of the CAN buses, filled in platformLifecycleAdd. A passive
// transceiver has no state, one object serves every bus that has one.
::bios::CanPhy* canPhys[NX_CAN_BUS_COUNT] = {nullptr};
#if NX_CAN_PHY_COUNT_CANPHYPASSIVE > 0
::can::PassiveCanPhy passiveCanPhy;
#endif
#endif

/**
 * Callout from main application to give platform the chance to add a
 * ::lifecycle::ILifecycleComponent to the \p lifecycleManager at a given \p level.
 */
void platformLifecycleAdd(::lifecycle::LifecycleManager& lifecycleManager, uint8_t const level)
{
    if (level == 1U)
    {
#if NX_MODULE_SBCUJA113X_COUNT == 1
        // The SBC keeps V2 (VREFH) and the CAN transceiver on; the bootloader has switched them on already.
        lifecycleManager.addComponent("sbc", sbcSystem.create(TASK_BSP), level);
#endif
        lifecycleManager.addComponent("bsp", bspSystem.create(TASK_BSP, staticBsp), level);
#if NX_DFLASH_AREA_COUNT > 0
        // NVM-23: in the task of the power supply (TASK_BSP, powerSupplyContext()), so its jobs, the console work and
        // the run-down step (ShutdownStep::NVM below) share one task; steps every 10 ms, never waiting
        lifecycleManager.addComponent("dflash", dataFlashSystem.create(TASK_BSP), level);
#endif
        // ARCH-52: instances of the modules with a binding whose lifecycle level is 1, each in the task of its
        // module; generated from the manifests. ARCH-66: right after "bsp", where the hand-written high-side switch
        // block was (same registration order as before: after "bsp" - ADC initialized by staticBsp - and before the
        // gate drivers and the debug halt guard). Each safe_off adapter is attached with the lock of the safe state
        // (SYS-36: the S1 latch, SYS-35: or an entry the hold has not handled yet) as soon as its system exists.
        ::integration::binding::addComponents(lifecycleManager, level);
#if INTEGRATION_GATE_DRIVER
        // After "bsp" (DRV_EN output low and the pins of every used SPI bus muxed by staticBsp in main(), ARCH-38).
        // init: DRV_EN low, then reading and only
        // the configured register fields; supervision every cycle_ms. DRV_EN is never switched on here, neither by
        // the system nor by the RTE (no such target). Console: never in the standard build; only a test build with
        // the CMake option NX_GD_ENABLE_COMMAND (default OFF, IO-34) has "<command> enable", which calls
        // Tmc6200::enable() only (refused outside DISABLED or with shutdown faults) - on the eval board only with
        // the user at the board (MOT1_GD_DRV_EN, CLAUDE.md). Component name = console command group.
#define GATE_DRIVER_ADD(device)                                                                      \
    lifecycleManager.addComponent(                                                                   \
        NX_DEVICE_##device##_PARAM_CONSOLE,                                                          \
        device##_GD_SYSTEM.create(                                                                   \
            TASK_BSP, device##_GD_SPI, device##_GD_IO, device##_GD_CONFIG,                           \
            static_cast<uint32_t>(NX_DEVICE_##device##_PARAM_CYCLE_MS), device##_GD_NAMES),          \
        level);
        NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_ADD)
#undef GATE_DRIVER_ADD
        // IO-32: DRV_EN of each instance as power stage output of the debug halt guard.
        // SYS-36: and the S1 latch as enable lock - enable() refused ("refused: safe state"), DRV_EN low without a mismatch fault.
#if defined(NX_SAFE_OUTPUT_SELECT)
        // SYS-139: an instance bound to the PWM group keeps its enable in the group's active short (ShortHold.h)
#define GATE_DRIVER_HALT_ATTACH(device)                                                                  \
    device##_GD_HALT_OUTPUT.attach(                                                                      \
        device##_GD_SYSTEM->getDriver(),                                                                 \
        (NX_DEVICE_##device##_PWM_COMPLEMENTARY != 0) ? &device##_GD_SHORT_LOCK : &safeStateLock,        \
        (NX_DEVICE_##device##_PWM_COMPLEMENTARY != 0) ? &device##_GD_KEEP_ENABLE : nullptr);
#elif (NX_PWM_GROUP_COUNT > 0) && defined(NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUPS)
        // IO-45: an instance bound to the PWM group is also locked while its safe-output group is degraded
#define GATE_DRIVER_HALT_ATTACH(device)                                                                \
    device##_GD_HALT_OUTPUT.attach(                                                                      \
        device##_GD_SYSTEM->getDriver(),                                                                 \
        (NX_DEVICE_##device##_PWM_COMPLEMENTARY != 0) ? &gateDriverGroupLock : &safeStateLock);
#else
#define GATE_DRIVER_HALT_ATTACH(device) \
    device##_GD_HALT_OUTPUT.attach(device##_GD_SYSTEM->getDriver(), &safeStateLock);
#endif
        NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_HALT_ATTACH)
#undef GATE_DRIVER_HALT_ATTACH
#if NX_PWM_GROUP_COUNT > 0
        // IO-45: the phase duty source for the enable rule of every instance bound to the PWM group (board
        // pwm[].gate_driver) with one pair per phase (module.yaml phases: 3, nxconfig warns otherwise): enable() is
        // refused unless all three phases are in one state (off, switching or full). An instance without a bound
        // group keeps the behaviour of before IO-44.
#define GATE_DRIVER_DUTY_SOURCE(device)                                                           \
    if ((NX_DEVICE_##device##_PWM_COMPLEMENTARY != 0) && (::bios::ftmPwm::PAIR_COUNT == 3U))     \
    {                                                                                             \
        device##_GD_SYSTEM->getDriver().setPhaseDutySource(&pwmGroupPhaseDuty);                  \
    }
        NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_DUTY_SOURCE)
#undef GATE_DRIVER_DUTY_SOURCE
#endif
#endif
#if INTEGRATION_DEBUG_HALT_GUARD
        // IO-32: after "bsp" (PWM set up) and the gate drivers, in their task: polls the debug events of the core
        // every DebugHaltGuardSystem::POLL_MS; after a halt PWM inhibited and DRV_EN off until the next enable.
        // SYS-36: the same poll holds the safe state S1 while it is latched.
        // SYS-151: and the commanded level of every safe_off output, outside S1 as well.
        lifecycleManager.addComponent(
            "haltguard",
            debugHaltGuardSystem.create(TASK_BSP, debugHaltGuard, safeStateHold, &safeOffCommandHold),
            level);
#endif
    }
    if (level == 2U)
    {
#ifdef PLATFORM_SUPPORT_CAN
        // After the transceiver providers (level 1): a bus only sends while its phy is ready. One entry
        // per bus, the module serving the role can_phy of a bus comes from the board and the project.
#define INTEGRATION_CAN_PHY_SBC(bus, index) canPhys[index] = &sbcSystem->getCanPhy();
        NX_CAN_PHY_BUSES_SBCUJA113X(INTEGRATION_CAN_PHY_SBC)
#undef INTEGRATION_CAN_PHY_SBC
#define INTEGRATION_CAN_PHY_PASSIVE(bus, index) canPhys[index] = &passiveCanPhy;
        NX_CAN_PHY_BUSES_CANPHYPASSIVE(INTEGRATION_CAN_PHY_PASSIVE)
#undef INTEGRATION_CAN_PHY_PASSIVE
        lifecycleManager.addComponent(
            "can", canSystem.create(TASK_CAN, staticBsp, ::etl::span<::bios::CanPhy* const>(canPhys)), level);
#endif
#ifdef NX_ROM_CHECK_IMAGE
        // SYS-38: after "safety" (level 1, requests the check); the CRC runs in the idle context (RomCheckSystem.h)
        lifecycleManager.addComponent("romcheck", romCheckSystem, level);
#endif
        // ARCH-52: instances of the modules with a binding whose lifecycle level is 2 (after the CAN system)
        ::integration::binding::addComponents(lifecycleManager, level);
#ifdef NX_SHUTDOWN_HANG_COMMAND
        // SYS-173 test build only: console "shutdownhang" makes this component hang in its next shutdown
        lifecycleManager.addComponent("shutdownhang", ::integration::shutdownHangComponent(), level);
#endif
    }
}
} // namespace platform

#ifdef PLATFORM_SUPPORT_CAN
namespace systems
{
::can::ICanSystem& getCanSystem() { return *::platform::canSystem; }
} // namespace systems
#endif

#if INTEGRATION_GATE_DRIVER
namespace integration
{
namespace rte
{
/* RTE inputs gd.<device>.faults/enabled/qlf (IO-25, tools/rtegen declares these readers in RteConfig.cpp). Read
 * from application tasks while the system runs in TASK_BSP: single bytes and words, a value may be one cycle old.
 * health: 0 system not created or not started yet, 1 configured (DRV_EN off or on), 2 communication or device
 * error (retried by the driver). Nothing here writes to the driver. */
#define GATE_DRIVER_RTE_READER(device)                                                                \
    void readGateDriver_##device(uint16_t& faults, bool& enabled, uint8_t& health)                     \
    {                                                                                                 \
        faults  = 0U;                                                                                 \
        enabled = false;                                                                              \
        health  = 0U;                                                                                 \
        if (::platform::device##_GD_SYSTEM.has_value())                                               \
        {                                                                                             \
            ::gd::Tmc6200 const& driver = ::platform::device##_GD_SYSTEM->getDriver();                \
            ::gd::Tmc6200::State const state = driver.getState();                                     \
            faults  = driver.getLatchedFaults();                                                      \
            enabled = (state == ::gd::Tmc6200::State::ENABLED);                                       \
            health  = (state == ::gd::Tmc6200::State::FAILED)          ? uint8_t{2U}                  \
                      : (state == ::gd::Tmc6200::State::UNINITIALIZED) ? uint8_t{0U}                  \
                                                                       : uint8_t{1U};                 \
        }                                                                                             \
    }
NX_MODULE_GDTMC6200_INSTANCES(GATE_DRIVER_RTE_READER)
#undef GATE_DRIVER_RTE_READER
} // namespace rte
} // namespace integration
#endif

// Role power_supply and the run-down steps of the ECU state management (SYS-01, SYS-02; here since SYS-08,
// before in ecu/application/src/systems/PowerSupplyIntegration.cpp with extern access to the objects above).
namespace
{
#if NX_MODULE_SBCUJA113X_COUNT == 1
// CAN wake-up of the UJA113x: index of the bus its integrated transceiver serves (role can_phy, ARCH-07), 0xFF if
// the project uses that transceiver on no bus (CAN wake-up then reported as unknown).
#if NX_CAN_PHY_COUNT_SBCUJA113X > 1
#error "a UJA113x has one integrated CAN transceiver, but serves more than one CAN bus"
#endif
#define INTEGRATION_SBC_BUS_INDEX(bus, index) (index),
constexpr uint8_t sbcCanBusIndex[] = {NX_CAN_PHY_BUSES_SBCUJA113X(INTEGRATION_SBC_BUS_INDEX) 0xFFU};
#undef INTEGRATION_SBC_BUS_INDEX
// WAKE/HVIO inputs of bank 0 that are wired on this board (SYS-02). The board schema does not describe them
// yet (board.yaml devices[].connect knows DIO and ADC only, ARCH-12), so none is offered: a project that
// selects modules.ecuState.wakeup_local is told that this board has no such source instead of arming an open
// input.
constexpr uint8_t SBC_WAKE_INPUT_MASK = 0x00U;
::etl::typed_storage<::sbc::Uja113xPowerSupply> sbcPowerSupply;
#else
// No switchable supply described: start reasons unknown, sleep requests are refused.
::ecustate::PermanentPowerSupply permanentPowerSupply;
#endif

// SYS-09: the log of the run-down reaches the console before the power down. Console UART of the project and its
// baud rate from the generated UART configuration (project use.uart, uart.<name>.baudrate); the OpenBSW application
// outputs the logger and the console in TASK_IDLE (ecu/application app.cpp, IdleHandler).
// PLAT-100: the baud rate is looked up by the enumerator Uart::Id::TERMINAL in a table of all used UARTs, not by
// pasting NX_UART_ and the value of NX_UART_CONSOLE. That value is the board name of the UART; a UART named
// `console` made the pasted name NX_UART_CONSOLE itself, which expanded once more (to CONSOLE_BAUDRATE). Here the
// name is pasted once, from the argument of the X macro, and nothing is expanded twice.
#define INTEGRATION_UART_BAUDRATE(uart, index, regs, label, baud, irqn, handler, level) NX_UART_##uart##_BAUDRATE,
constexpr uint32_t uartBaudRates[] = {NX_UART_INSTANCES(INTEGRATION_UART_BAUDRATE)};
#undef INTEGRATION_UART_BAUDRATE
static_assert(
    static_cast<size_t>(::bsp::Uart::Id::TERMINAL) < (sizeof(uartBaudRates) / sizeof(uartBaudRates[0])),
    "the console UART is one of the used UARTs");
::integration::ConsoleOutputFlush consoleOutputFlush(
    ::async::AsyncBinding::AdapterType::TASK_IDLE,
    uartBaudRates[static_cast<size_t>(::bsp::Uart::Id::TERMINAL)]);

#ifdef PLATFORM_SUPPORT_CAN
// Resolved when the step runs (operation), long after platformLifecycleAdd(level 2) created the CAN system.
::systems::CanShutdownStep canShutdownStep(
    &::systems::getCanSystem, ::platform::canPhys, static_cast<uint8_t>(NX_CAN_BUS_COUNT));
#endif

/* Providers of the run-down steps of this project. ShutdownStep::NVM (NVM-07) comes from the application
 * (integration::nvmShutdownStep(), NvM of the RTE); a project without NvM blocks has none, and the ECU state
 * management reports the step as "not present" instead of pretending. The NvM step runs first, while CAN is still
 * up. The console output is flushed before the power down on every board, with or without SBC (SYS-09). */
struct SequenceHolder
{
    ::ecustate::ShutdownSequence sequence;

    SequenceHolder()
    {
        ::ecustate::IShutdownStep* const nvm = ::integration::nvmShutdownStep();
#if NX_DFLASH_AREA_COUNT > 0
        // NVM-23 (safety review NVM12-S2): with data flash the step first locks it, then runs the NvM step, and ends
        // only when no data flash command is in flight - the power down never meets an erase
        sequence.set(::ecustate::ShutdownStep::NVM, ::systems::dataFlashRunDownStep(nvm));
#else
        if (nvm != nullptr)
        {
            sequence.set(::ecustate::ShutdownStep::NVM, *nvm);
        }
#endif
#ifdef PLATFORM_SUPPORT_CAN
        sequence.set(::ecustate::ShutdownStep::CAN_STOP, canShutdownStep);
#endif
#if INTEGRATION_DEBUG_HALT_GUARD
        // SYS-174: the power outputs safe and locked last, right before the lifecycle shutdown; a project without power
        // outputs (no PWM group, no safe_off output) has an empty table and no step ("not present")
        sequence.set(::ecustate::ShutdownStep::SAFE_OUTPUTS, safeOutputsShutdownStep);
#endif
        sequence.setOutputFlush(consoleOutputFlush);
    }
};

// after canShutdownStep and consoleOutputFlush: initialized in this order
SequenceHolder const shutdownSequenceHolder;

/* SYS-173 (safety review 2026-10-02, SYS69-S4): the direct reset of the ECU state management (ecuState/DirectReset.h),
 * called when the lifecycle has not carried a requested restart out within the shutdown time-out. The safe outputs
 * table first (the same walk as the fault handlers before their reset), then the software system reset of OpenBSW.
 * Registered by a static constructor, long before the run level is reached. */
void directResetOfTheEcuStateManagement()
{
    nxSafeOutputsSwitchOff();
    softwareSystemReset();
}

struct DirectResetRegistration
{
    DirectResetRegistration() { ::ecustate::setDirectReset(&directResetOfTheEcuStateManagement); }
};

DirectResetRegistration const directResetRegistration;
} // namespace

namespace integration
{
// NVM-07: weak default - no NvM step; the application's NvM (ecu/application, NX_RTE_HAS_NVM) defines the strong one
__attribute__((weak))::ecustate::IShutdownStep* nvmShutdownStep() { return nullptr; }

::ecustate::IPowerSupply& powerSupply()
{
#if NX_MODULE_SBCUJA113X_COUNT == 1
    if (!sbcPowerSupply.has_value())
    {
        (void)sbcPowerSupply.create(::platform::sbcSystem->getSbc(), sbcCanBusIndex[0], SBC_WAKE_INPUT_MASK);
    }
    return *sbcPowerSupply;
#else
    return permanentPowerSupply;
#endif
}

::async::ContextType powerSupplyContext()
{
    // UJA113x driver: task of SbcSystem (created with TASK_BSP above, module.yaml lifecycle.task bsp); the
    // permanent supply has no requirement.
    return TASK_BSP;
}

::ecustate::ShutdownSequence const& shutdownSequence() { return shutdownSequenceHolder.sequence; }
} // namespace integration

// SYS-59: the console flush of the safe supervisor before and after its SAFETY RESET line (contract
// safeSupervisor/SafeConsole.h, bounded loop SafeConsoleFlush.h). The console is the UART behind bsp::Uart::Id::
// TERMINAL (project use.uart, generated); only its STAT register is read - no write, no interrupt, no RTOS.
namespace
{
#define INTEGRATION_UART_REGISTERS(uart, index, regs, label, baud, irqn, handler, level) (regs),
LPUART_Type* const uartRegisters[] = {NX_UART_INSTANCES(INTEGRATION_UART_REGISTERS)};
#undef INTEGRATION_UART_REGISTERS
static_assert(
    static_cast<size_t>(::bsp::Uart::Id::TERMINAL) < (sizeof(uartRegisters) / sizeof(uartRegisters[0])),
    "the console UART is one of the used UARTs");

class TerminalTransmitter
{
public:
    explicit TerminalTransmitter(LPUART_Type const& uart) : _uart(uart) {}

    // STAT[TC]: data register, FIFO and shift register empty - the last character has left the pin
    bool transmissionComplete() const { return (_uart.STAT & LPUART_STAT_TC_MASK) != 0U; }

private:
    LPUART_Type const& _uart;
};
} // namespace

namespace safety
{
bool safeConsoleFlush()
{
    TerminalTransmitter port(*uartRegisters[static_cast<size_t>(::bsp::Uart::Id::TERMINAL)]);
    bool const idle = ::platform::flushTransmitter(port, ::platform::TRANSMITTER_FLUSH_POLLS);
    // SYS-137: counted in the no-init RAM, so the next start can tell a skipped line (time-out) from a lost one
    ::platform::safeconsole::noteFlush(nxSafeConsoleRecord, idle);
    return idle;
}
} // namespace safety

#ifdef NX_FLEXNVM_PARTITION_COMMAND
namespace integration
{
// NVM-17 (test build): the watchdog outside the MCU is the SBC's, if the project has one; without, only the MCU
// watchdog runs (board without SBC, question 1 of the architecture checks) and the CSEc module keeps that one quiet.
uint32_t externalWatchdogAllowanceMs()
{
#if NX_MODULE_SBCUJA113X_COUNT == 1
    return ::platform::sbcSystem.has_value() ? ::platform::sbcSystem->watchdogAllowanceMs() : 0U;
#else
    return 0xFFFFFFFFU;
#endif
}
} // namespace integration
#endif
