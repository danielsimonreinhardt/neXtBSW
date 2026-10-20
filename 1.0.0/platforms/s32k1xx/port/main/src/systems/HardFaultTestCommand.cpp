// Console command "hardfault" (PLAT-20), test builds only (NX_HARDFAULT_TEST_COMMAND). Each subcommand ends in
// a hard fault from the calling task; the dump is printed after the reset.

#include "systems/HardFaultTestCommand.h"

#include <util/command/CommandContext.h>

#include <cstdint>
#include <cstdio>

extern "C"
{
// First address above the RAM (application linker script).
extern uint32_t __hard_fault_ram_end[];
}

namespace
{
enum Id
{
    ID_UDF,
    ID_BUS,
    ID_SP
};

/**
 * Address for "hardfault bus": a reserved region of the s32k1xx address map, i.e. no slave of the crossbar
 * answers and the read is terminated with an error (precise bus fault, escalated to the hard fault because
 * the handler is not enabled).
 *
 * Board finding of PLAT-20 (2026-09-15): the first address above the RAM (__hard_fault_ram_end, 0x20007000 on
 * the S32K144) is **readable** - the SRAM controller still answers above the populated RAM - so the command
 * printed "no fault". The reserved gap between the peripheral bridge (0x40000000..0x400FFFFF) and the private
 * peripheral bus (0xE0000000) has no slave at all on any derivative of the family, which is why the address is
 * a family constant here and not a board or project value: it describes the s32k1xx address map, nothing of a
 * board (architecture section 4). Every other family gets its own port file.
 *
 * The subcommand takes an address as an optional parameter, so a board test can try another one without a new
 * build (test builds only, NX_HARDFAULT_TEST_COMMAND).
 */
constexpr uint32_t RESERVED_ADDRESS = 0x80000000UL;

// Sets the stack pointer of the caller's mode to sp (r0) and faults: the exception frame cannot be stacked.
__attribute__((naked, noinline)) void faultWithStackPointer(uint32_t /* sp */)
{
    asm volatile("mov sp, r0\n udf #2");
}
} // namespace

namespace systems
{

DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(HardFaultTestCommand, "hardfault", "Trigger a hard fault (test build only)")
COMMAND_GROUP_COMMAND(ID_UDF, "udf", "undefined instruction (usage fault, escalated)")
COMMAND_GROUP_COMMAND(
    ID_BUS, "bus", "read a reserved address (bus fault, escalated; param: [<address>], default 0x80000000)")
COMMAND_GROUP_COMMAND(ID_SP, "sp", "stack pointer above the RAM, then fault (dump without frame)")
DEFINE_COMMAND_GROUP_GET_INFO_END

void HardFaultTestCommand::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    // printf: a logger call would not be output before the reset.
    switch (idx)
    {
        case ID_UDF:
        {
            printf("hardfault: undefined instruction\r\n");
            asm volatile("udf #1");
            break;
        }
        case ID_BUS:
        {
            uint32_t const requested
                = context.hasToken() ? context.scanIntToken<uint32_t>() : RESERVED_ADDRESS;
            if (!context.checkEol())
            {
                break;
            }
            auto const* const address = reinterpret_cast<uint32_t const volatile*>(requested);
            printf("hardfault: read 0x%08lx\r\n", static_cast<unsigned long>(requested));
            uint32_t const value = *address;
            // Only reached if the address answers after all: then it is the wrong address for this derivative
            // (the board test of PLAT-20 saw this with the first address above the RAM).
            printf(
                "hardfault: no fault, read 0x%08lx - pick another address (\"hardfault bus <address>\")\r\n",
                static_cast<unsigned long>(value));
            break;
        }
        case ID_SP:
        {
            uint32_t const sp = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__hard_fault_ram_end)) + 0x100U;
            printf("hardfault: stack pointer 0x%08lx\r\n", static_cast<unsigned long>(sp));
            faultWithStackPointer(sp);
            break;
        }
        default:
        {
            break;
        }
    }
}

} // namespace systems
