// Host test of the hard fault dump reader (PLAT-20): layout consistency, validity (magic, checksum) and report.
// The dump is a plain buffer, independent of any board or linker script.

#include "hardFaultHandler/HardFaultDump.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace
{
int checks   = 0;
int failures = 0;

void check(bool const condition, char const* const what)
{
    ++checks;
    if (!condition)
    {
        ++failures;
        printf("FAIL: %s\n", what);
    }
}

std::vector<std::string> lines;

void collect(char const* const line) { lines.emplace_back(line); }

bool reportContains(char const* const text)
{
    for (auto const& line : lines)
    {
        if (line.find(text) != std::string::npos)
        {
            return true;
        }
    }
    return false;
}

using Dump = uint32_t[::hardfault::DUMP_WORDS];

void set(Dump& dump, size_t const offset, uint32_t const value) { dump[offset / 4U] = value; }

// A dump as the handler leaves it: fields, checksum, magic.
void makeValidDump(Dump& dump)
{
    std::memset(dump, 0, sizeof(Dump));
    set(dump, DUMP_HANDLER_PSR, 3U);
    set(dump, DUMP_HANDLER_LR, 0xFFFFFFFDU);
    set(dump, DUMP_ORIGIN_R0, 0x10U);
    set(dump, DUMP_ORIGIN_R11, 0x11BU);
    set(dump, DUMP_ORIGIN_R12, 0x12CU);
    set(dump, DUMP_ORIGIN_SP, 0x20001000U);
    set(dump, DUMP_ORIGIN_LR, 0x0001A001U);
    set(dump, DUMP_ORIGIN_PC, 0x0001A0F2U);
    set(dump, DUMP_ORIGIN_PSR, 0x61000000U);
    set(dump, DUMP_CFSR, 0x00008200U);
    set(dump, DUMP_HFSR, 0x40000000U);
    set(dump, DUMP_BFAR, 0x20007000U);
    set(dump, DUMP_FLAGS, DUMP_FLAG_FRAME_VALID);
    for (size_t offset = DUMP_STACK_START; offset < DUMP_STACK_END; offset += 4U)
    {
        set(dump, offset, 0xA5000000U + static_cast<uint32_t>(offset));
    }
    set(dump, DUMP_CHECKSUM, ::hardfault::computeChecksum(dump));
    set(dump, DUMP_MAGIC, HARD_FAULT_DUMP_MAGIC);
}

void testLayout()
{
    check((HARD_FAULT_DUMP_SIZE % 4) == 0, "size is a multiple of 4");
    check(DUMP_MAGIC + 4 == HARD_FAULT_DUMP_SIZE, "magic is the last word");
    check(DUMP_CHECKSUM + 4 == DUMP_MAGIC, "checksum directly before the magic");
    check(DUMP_STACK_END == DUMP_CHECKSUM, "stack words end at the checksum");
    check(DUMP_FLAGS + 4 == DUMP_STACK_START, "stack words follow the flags");
    check(DUMP_STACK_START < DUMP_STACK_END, "room for stack words");
    // OpenBSW register offsets are kept (external/openbsw/platforms/s32k1xx/hardFaultHandler)
    check(DUMP_ORIGIN_R4 == 0x018 && DUMP_ORIGIN_SP == 0x03C && DUMP_ORIGIN_PSR == 0x048, "OpenBSW offsets");
}

void testStates()
{
    Dump dump;
    std::memset(dump, 0, sizeof(Dump));
    // Cleared RAM has a matching checksum (0): only the magic makes it a dump.
    check(::hardfault::getDumpState(dump) == ::hardfault::DumpState::NONE, "zero RAM is no dump");

    makeValidDump(dump);
    check(::hardfault::getDumpState(dump) == ::hardfault::DumpState::VALID, "valid dump");

    set(dump, DUMP_ORIGIN_PC, 0x0001A0F4U);
    check(::hardfault::getDumpState(dump) == ::hardfault::DumpState::CORRUPT, "changed word -> corrupt");

    makeValidDump(dump);
    set(dump, DUMP_STACK_END - 4U, 0U);
    check(::hardfault::getDumpState(dump) == ::hardfault::DumpState::CORRUPT, "last checksummed word covered");

    makeValidDump(dump);
    set(dump, DUMP_MAGIC, 0U);
    check(::hardfault::getDumpState(dump) == ::hardfault::DumpState::NONE, "cleared magic -> no dump");

    makeValidDump(dump);
    set(dump, DUMP_MAGIC, HARD_FAULT_DUMP_MAGIC ^ 1U);
    check(::hardfault::getDumpState(dump) == ::hardfault::DumpState::NONE, "wrong magic -> no dump");
}

// SYS-148: the exception number tells the watchdog path (vector 38) from a fault (3) - only from a valid dump
void testException()
{
    Dump dump;
    makeValidDump(dump);
    check(::hardfault::getDumpException(dump) == 3U, "hard fault dump: exception 3");
    set(dump, DUMP_HANDLER_PSR, 0x61000026U); // flags above bit 8 are not part of the number
    set(dump, DUMP_CHECKSUM, ::hardfault::computeChecksum(dump));
    check(::hardfault::getDumpException(dump) == 38U, "watchdog pre-reset interrupt: exception 38");
    set(dump, DUMP_ORIGIN_PC, 0U);
    check(::hardfault::getDumpException(dump) == 0U, "corrupt dump: no exception number");
    makeValidDump(dump);
    set(dump, DUMP_MAGIC, 0U);
    check(::hardfault::getDumpException(dump) == 0U, "no dump: no exception number");
}

void testChecksumWraps()
{
    Dump dump;
    std::memset(dump, 0xFF, sizeof(Dump));
    set(dump, DUMP_CHECKSUM, ::hardfault::computeChecksum(dump));
    set(dump, DUMP_MAGIC, HARD_FAULT_DUMP_MAGIC);
    // 79 words of 0xFFFFFFFF: -79 modulo 2^32
    check(::hardfault::computeChecksum(dump) == static_cast<uint32_t>(0U - (DUMP_CHECKSUM / 4U)), "sum modulo 2^32");
    check(::hardfault::getDumpState(dump) == ::hardfault::DumpState::VALID, "wrapped checksum valid");
}

void testReport()
{
    Dump dump;
    makeValidDump(dump);
    lines.clear();
    check(::hardfault::formatDump(dump, &collect) == ::hardfault::DumpState::VALID, "report state valid");
    check(lines.size() == 6U, "six report lines");
    check(reportContains("HardFault dump: exception 3, EXC_RETURN 0xfffffffd"), "exception and EXC_RETURN");
    check(reportContains("PC 0x0001a0f2 LR 0x0001a001 xPSR 0x61000000 SP 0x20001000"), "PC LR xPSR SP");
    check(reportContains("CFSR 0x00008200 HFSR 0x40000000 MMFAR 0x00000000 BFAR 0x20007000"), "fault status");
    check(reportContains("R0 0x00000010") && reportContains("R12 0x0000012c"), "framed registers");
    check(reportContains("R11 0x0000011b"), "R11");
    check(!reportContains("no frame"), "frame valid");
    for (auto const& line : lines)
    {
        check(line.size() < 80U, "line fits the buffer");
        printf("  | %s\n", line.c_str());
    }

    set(dump, DUMP_FLAGS, 0U);
    set(dump, DUMP_CHECKSUM, ::hardfault::computeChecksum(dump));
    lines.clear();
    (void)::hardfault::formatDump(dump, &collect);
    check(reportContains("SP outside RAM (no frame)"), "invalid frame reported");

    set(dump, DUMP_ORIGIN_R1, 1U);
    lines.clear();
    check(::hardfault::formatDump(dump, &collect) == ::hardfault::DumpState::CORRUPT, "report state corrupt");
    check(lines.size() == 1U && reportContains("corrupt"), "one line for a corrupt dump");

    set(dump, DUMP_MAGIC, 0U);
    lines.clear();
    check(::hardfault::formatDump(dump, &collect) == ::hardfault::DumpState::NONE, "report state none");
    check(lines.empty(), "no output without dump");
}

// PLAT-10: the ARMv6-M handler has no fault status registers; it leaves the fields 0 and sets a flag.
void testReportWithoutFaultStatus()
{
    Dump dump;
    makeValidDump(dump);
    set(dump, DUMP_CFSR, 0U);
    set(dump, DUMP_HFSR, 0U);
    set(dump, DUMP_BFAR, 0U);
    set(dump, DUMP_FLAGS, DUMP_FLAG_FRAME_VALID | DUMP_FLAG_NO_FAULT_STATUS);
    set(dump, DUMP_CHECKSUM, ::hardfault::computeChecksum(dump));
    lines.clear();
    check(::hardfault::formatDump(dump, &collect) == ::hardfault::DumpState::VALID, "armv6m report state valid");
    check(lines.size() == 6U, "armv6m six report lines");
    check(reportContains("CFSR/HFSR/MMFAR/BFAR n/a"), "fault status marked not available");
    check(!reportContains("CFSR 0x"), "no fault status values");
    check(!reportContains("no frame"), "armv6m frame valid");
    check(reportContains("PC 0x0001a0f2"), "armv6m PC");
    check(DUMP_FLAG_NO_FAULT_STATUS != DUMP_FLAG_FRAME_VALID, "distinct flags");
    for (auto const& line : lines)
    {
        check(line.size() < 80U, "armv6m line fits the buffer");
        printf("  | %s\n", line.c_str());
    }
}
} // namespace

int main()
{
    testLayout();
    testStates();
    testException();
    testChecksumWraps();
    testReport();
    testReportWithoutFaultStatus();
    printf("hard_fault_dump_host_test: %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
