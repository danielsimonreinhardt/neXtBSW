// Console command "flexnvm" (NVM-07), see systems/FlexNvmCommand.h. Runs in the console context.
//
// "flexnvm partition" is compiled only with NX_FLEXNVM_PARTITION_COMMAND (test builds) and only for a project with
// nvm.partition (NX_NVM_PARTITION). It is IRREVERSIBLE: the partition codes go into the data flash IFR, only a mass
// erase undoes them. It is used with the user at the board, after "flexnvm show" and after the bootloader has been
// saved and the J-Link rescue path checked (board checklist NVM-07). The command blocks the core with interrupts
// suspended for the duration of PGMPART (data sheet: typical 71..78 ms). NVM-22: the safety task's period check then
// resets the ECU - expected, the result is on the console and in the journal before (flexnvmrun::runPartition(),
// systems/FlexNvmCommand.h); "flexnvm show" prints the journal after the start.

#include "systems/FlexNvmCommand.h"

#include "flexnvm/FlexNvmChip.h"
#include "flexnvm/FlexNvmEeprom.h"
#include "mcu/mcu.h"

#include <nvm/nvmPartition.h>
#include <util/command/CommandContext.h>
#include <util/format/SharedStringWriter.h>

#ifdef NX_FLEXNVM_PARTITION_COMMAND
#include "flexnvm/FlexNvmPartition.h"
#include "integration/Integration.h"

#include <interrupts/suspendResumeAllInterrupts.h>
#include <safeSupervisor/SafeConsole.h>
#endif

#include <cstdint>

namespace platform
{
// lifecycle/StaticBsp.cpp: the EEPROM driver the NvM uses
FlexNvmEeprom& flexNvmEepromInstance();
} // namespace platform

namespace
{
enum Id
{
    ID_SHOW,
#ifdef NX_FLEXNVM_PARTITION_COMMAND
    ID_PARTITION,
#endif
};

#if defined(NX_FLEXNVM_PARTITION_COMMAND) && defined(NX_NVM_PARTITION)
::platform::FlexNvmPartition::Plan const PLAN{
    static_cast<uint8_t>(NX_NVM_PARTITION_EEPROM_CODE),
    static_cast<uint8_t>(NX_NVM_PARTITION_CODE),
    static_cast<uint8_t>(NX_NVM_PARTITION_LOAD_CODE),
    NX_NVM_PARTITION_EEPROM_BYTES,
#ifdef NX_NVM_WINDOW_START
    NX_NVM_WINDOW_START,
#else
    0U,
#endif
    NX_NVM_PARTITION_CONFIRM};
#endif

constexpr uint32_t CONFIRM_MAX = 31U;

#ifdef NX_FLEXNVM_PARTITION_COMMAND
using ::systems::flexnvmrun::Journal;

// NVM-22: no-init RAM of the application linker script (.noinit.*, not cleared by startUp.S, outside the MPU protected
// safety RAM): the journal of the last "flexnvm partition" - it survives the reset of the safety task's period check
// that follows PGMPART (and a watchdog reset during it)
__attribute__((section(".noinit.flexnvm"))) Journal volatile flexNvmJournal;

void printJournal(::util::format::SharedStringWriter& out)
{
    if (!flexNvmJournal.valid())
    {
        (void)out.printf("last flexnvm partition: none since power-on\r\n");
        return;
    }
    uint8_t const state   = flexNvmJournal.state();
    unsigned const depart = static_cast<unsigned>(flexNvmJournal.departBefore());
    if (state == Journal::STATE_DONE)
    {
        (void)out.printf(
            "last flexnvm partition (DEPART before 0x%x): %s\r\n",
            depart,
            ::platform::FlexNvmPartition::resultText(
                static_cast<::platform::FlexNvmPartition::Result>(flexNvmJournal.result())));
    }
    else if (state == Journal::STATE_LAUNCHING)
    {
        (void)out.printf("last flexnvm partition (DEPART before 0x%x): INTERRUPTED - reset while the command ran, the "
                         "codes may be indeterminate: see the chip line\r\n",
                         depart);
    }
    else
    {
        (void)out.printf("last flexnvm partition: journal state %u unknown\r\n", static_cast<unsigned>(state));
    }
}
#endif

#if defined(NX_FLEXNVM_PARTITION_COMMAND) && defined(NX_NVM_PARTITION)
// NVM-22: the target side of flexnvmrun::runPartition(); Launch calls FlexNvmPartition::partition() (in
// executeCommand(), the only caller - test_flexnvm.py)
template<class Launch>
class PartitionOps
{
public:
    PartitionOps(::util::format::SharedStringWriter& out, Launch const& launch) : _out(out), _launch(launch) {}

    bool externalWatchdogAllows()
    {
        uint32_t const allowed = ::integration::externalWatchdogAllowanceMs();
        if (allowed >= ::systems::flexnvmrun::PGMPART_BUDGET_MS)
        {
            return true;
        }
        (void)_out.printf(
            "flexnvm: PGMPART needs up to %u ms with interrupts suspended, the watchdog outside the MCU (SBC) allows %u "
            "ms - nothing written. Test build with that watchdog off (software development mode) or a longer "
            "period.\r\n",
            static_cast<unsigned>(::systems::flexnvmrun::PGMPART_BUDGET_MS),
            static_cast<unsigned>(allowed));
        return false;
    }

    static uint8_t departCode() { return ::platform::FlexNvmChip::departCode(); }

    void announce()
    {
        (void)_out.printf("flexnvm: PGMPART size code 0x%x, partition code 0x%x, load byte %u\r\n",
                          static_cast<unsigned>(PLAN.eeeSizeCode),
                          static_cast<unsigned>(PLAN.partitionCode),
                          static_cast<unsigned>(PLAN.loadCode));
        (void)_out.printf("flexnvm: if launched, the core stops for PGMPART (interrupts suspended); then the safety "
                          "task's period check resets the ECU (SAFETY_CYCLE_PERIOD_VIOLATION, expected). The result "
                          "follows, and after the start in flexnvm show\r\n");
        (void)::safety::safeConsoleFlush();
    }

    static uint32_t suspendAllInterrupts() { return getMachineStateRegisterValueAndSuspendAllInterrupts(); }

    uint8_t partition() { return static_cast<uint8_t>(_launch()); }

    // interrupts suspended: no printf, no console stream - the synchronous line of the safe console
    static void resultLine(uint8_t const result)
    {
        char line[224]      = {};
        size_t const length = ::safety::formatConsoleLine(
            line,
            sizeof(line),
            "\r\nflexnvm: %s\r\n",
            ::platform::FlexNvmPartition::resultText(static_cast<::platform::FlexNvmPartition::Result>(result)));
        (void)::safety::safeConsoleWrite(line, static_cast<uint32_t>(length));
        (void)::safety::safeConsoleFlush();
    }

    static void resumeAllInterrupts(uint32_t const primask) { ::resumeAllInterrupts(primask); }

private:
    ::util::format::SharedStringWriter& _out;
    Launch const& _launch;
};
#endif
} // namespace

namespace systems
{

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(FlexNvmCommand, "flexnvm", "FlexNVM partition (NVM-07)")
COMMAND_GROUP_COMMAND(ID_SHOW, "show", "partition of the chip, EEPROM driver, partition of the project")
#ifdef NX_FLEXNVM_PARTITION_COMMAND
COMMAND_GROUP_COMMAND(ID_PARTITION, "partition", "IRREVERSIBLE: write the partition (param: <confirmation word>, see show) (board rule)")
#endif
DEFINE_COMMAND_GROUP_GET_INFO_END

void FlexNvmCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    ::util::format::SharedStringWriter out(context);
    if (idx == ID_SHOW)
    {
        ::platform::FlexNvmEeprom const& eeprom = ::platform::flexNvmEepromInstance();
        // NVM-10: part from SIM SDID; partition from DEPART and the FlexRAM flags (EEERAMSIZE is no IFR code)
        ::platform::FlexNvmChip::DeviceId const id = ::platform::FlexNvmChip::deviceId();
        (void)out.printf(
            "device: %s, %s, revision %u, SDID 0x%08x, CSEc %s\r\n",
            ::platform::FlexNvmChip::deviceName(id),
            ::platform::FlexNvmChip::packageText(id.package),
            static_cast<unsigned>(id.revision),
            static_cast<unsigned>(id.raw),
            id.security ? "present (SDID FEATURES bit 7)" : "not present");
        (void)out.printf(
            "chip: DEPART 0x%x, EEERAMSIZE 0x%x, RAMRDY %u, EEERDY %u: %s\r\n",
            static_cast<unsigned>(::platform::FlexNvmChip::departCode()),
            static_cast<unsigned>(::platform::FlexNvmChip::eeeRamSizeCode()),
            static_cast<unsigned>(::platform::FlexNvmChip::ramReady() ? 1U : 0U),
            static_cast<unsigned>(::platform::FlexNvmChip::eeeReady() ? 1U : 0U),
            ::platform::FlexNvmChip::partitionText(::platform::FlexNvmChip::partition()));
        (void)out.printf(
            "driver: %s, write limit %u us, longest write %u us, time-outs %u\r\n",
            ::platform::FlexNvmEeprom::stateText(eeprom.state()),
            static_cast<unsigned>(eeprom.writeLimitUs()),
            static_cast<unsigned>(eeprom.longestWriteUs()),
            static_cast<unsigned>(eeprom.timeouts()));
#ifdef NX_NVM_PARTITION
        (void)out.printf("project nvm.partition:\r\n%s", NX_NVM_PARTITION_TEXT);
#ifdef NX_FLEXNVM_PARTITION_COMMAND
        (void)out.printf("to write it (user at the board only): flexnvm partition %s\r\n", NX_NVM_PARTITION_CONFIRM);
#else
        (void)out.printf("partition command not in this build (NX_FLEXNVM_PARTITION_COMMAND off)\r\n");
#endif
#else
        (void)out.printf("project: no nvm.partition, nothing to write\r\n");
#endif
#ifdef NX_FLEXNVM_PARTITION_COMMAND
        printJournal(out);
#endif
        return;
    }
#ifdef NX_FLEXNVM_PARTITION_COMMAND
    if (idx == ID_PARTITION)
    {
        char word[CONFIRM_MAX + 1U] = {};
        if (context.hasToken())
        {
            ::util::string::ConstString const token = context.scanToken();
            size_t const length = (token.length() < CONFIRM_MAX) ? token.length() : CONFIRM_MAX;
            for (size_t i = 0U; i < length; ++i)
            {
                word[i] = token.data()[i];
            }
        }
        if (!context.checkEol())
        {
            return;
        }
#ifdef NX_NVM_PARTITION
        // NVM-22: announce, suspend, partition, journal and result line, resume - see systems/FlexNvmCommand.h. The
        // result line is written before the interrupts come back (a refusal or a short block: no reset follows).
        auto const launch = [&word]() { return ::platform::FlexNvmPartition::partition(PLAN, word); };
        PartitionOps<decltype(launch)> ops(out, launch);
        (void)::systems::flexnvmrun::runPartition(ops, flexNvmJournal);
#else
        (void)word;
        (void)out.printf("flexnvm: the project has no nvm.partition, nothing written\r\n");
#endif
    }
#endif
}

} // namespace systems
