// Hardware-independent part of the hard fault dump reader (host test: test/host).

#include "hardFaultHandler/HardFaultDump.h"

#include <cstdarg>
#include <cstdio>

// DUMP_FLAG_NO_FAULT_STATUS is only set by the ARMv6-M handler (PLAT-10). ARMv7-M/ARMv8-M targets never see it and
// keep the report code unchanged; the host test covers both reports.
#if !defined(__arm__) || (defined(__ARM_ARCH) && (__ARM_ARCH == 6))
#define HARD_FAULT_DUMP_FAULT_STATUS_OPTIONAL 1
#else
#define HARD_FAULT_DUMP_FAULT_STATUS_OPTIONAL 0
#endif

namespace
{
unsigned long field(uint32_t const* const dump, size_t const offset)
{
    return static_cast<unsigned long>(dump[offset / 4U]);
}

__attribute__((format(printf, 2, 3)))
void writeLine(::hardfault::LineWriter const writer, char const* const format, ...)
{
    char line[80];
    va_list args;
    va_start(args, format);
    (void)vsnprintf(line, sizeof(line), format, args);
    va_end(args);
    writer(line);
}
} // namespace

namespace hardfault
{
uint32_t computeChecksum(uint32_t const* const dump)
{
    uint32_t sum = 0U;
    for (size_t i = 0U; i < (DUMP_CHECKSUM / 4U); ++i)
    {
        sum += dump[i];
    }
    return sum;
}

DumpState getDumpState(uint32_t const* const dump)
{
    if (dump[DUMP_MAGIC / 4U] != HARD_FAULT_DUMP_MAGIC)
    {
        return DumpState::NONE;
    }
    return (computeChecksum(dump) == dump[DUMP_CHECKSUM / 4U]) ? DumpState::VALID : DumpState::CORRUPT;
}

uint32_t getDumpException(uint32_t const* const dump)
{
    return (getDumpState(dump) == DumpState::VALID) ? (dump[DUMP_HANDLER_PSR / 4U] & 0x1FFU) : 0U;
}

DumpState formatDump(uint32_t const* const dump, LineWriter const writer)
{
    DumpState const state = getDumpState(dump);
    if (state == DumpState::CORRUPT)
    {
        writeLine(
            writer,
            "HardFault dump corrupt: checksum 0x%08lx, computed 0x%08lx",
            field(dump, DUMP_CHECKSUM),
            static_cast<unsigned long>(computeChecksum(dump)));
    }
    if (state != DumpState::VALID)
    {
        return state;
    }
    bool const frameValid = (field(dump, DUMP_FLAGS) & DUMP_FLAG_FRAME_VALID) != 0UL;
    writeLine(
        writer,
        "HardFault dump: exception %lu, EXC_RETURN 0x%08lx%s",
        field(dump, DUMP_HANDLER_PSR) & 0x1FFUL,
        field(dump, DUMP_HANDLER_LR),
        frameValid ? "" : ", SP outside RAM (no frame)");
    writeLine(
        writer,
        "  PC 0x%08lx LR 0x%08lx xPSR 0x%08lx SP 0x%08lx",
        field(dump, DUMP_ORIGIN_PC),
        field(dump, DUMP_ORIGIN_LR),
        field(dump, DUMP_ORIGIN_PSR),
        field(dump, DUMP_ORIGIN_SP));
#if HARD_FAULT_DUMP_FAULT_STATUS_OPTIONAL
    if ((field(dump, DUMP_FLAGS) & DUMP_FLAG_NO_FAULT_STATUS) != 0UL)
    {
        writeLine(writer, "  CFSR/HFSR/MMFAR/BFAR n/a (core without fault status registers)");
    }
    else
#endif
    {
        writeLine(
            writer,
            "  CFSR 0x%08lx HFSR 0x%08lx MMFAR 0x%08lx BFAR 0x%08lx",
            field(dump, DUMP_CFSR),
            field(dump, DUMP_HFSR),
            field(dump, DUMP_MMFAR),
            field(dump, DUMP_BFAR));
    }
    writeLine(
        writer,
        "  R0 0x%08lx R1 0x%08lx R2 0x%08lx R3 0x%08lx R12 0x%08lx",
        field(dump, DUMP_ORIGIN_R0),
        field(dump, DUMP_ORIGIN_R1),
        field(dump, DUMP_ORIGIN_R2),
        field(dump, DUMP_ORIGIN_R3),
        field(dump, DUMP_ORIGIN_R12));
    writeLine(
        writer,
        "  R4 0x%08lx R5 0x%08lx R6 0x%08lx R7 0x%08lx",
        field(dump, DUMP_ORIGIN_R4),
        field(dump, DUMP_ORIGIN_R5),
        field(dump, DUMP_ORIGIN_R6),
        field(dump, DUMP_ORIGIN_R7));
    writeLine(
        writer,
        "  R8 0x%08lx R9 0x%08lx R10 0x%08lx R11 0x%08lx",
        field(dump, DUMP_ORIGIN_R8),
        field(dump, DUMP_ORIGIN_R9),
        field(dump, DUMP_ORIGIN_R10),
        field(dump, DUMP_ORIGIN_R11));
    return state;
}

} // namespace hardfault
