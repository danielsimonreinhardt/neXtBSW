// SYS-39: safety::safeConsoleWrite() of the S32K1 family - the console line of the safe supervisor's hold, written
// synchronously to the terminal LPUART of the project (bsp::Uart::Id::TERMINAL, board/project configuration), by
// polling its transmitter. Contract: ecu/safety/safeSupervisor/include/safeSupervisor/SafeConsole.h.
//
// Linked as objects of its own onto the application (platforms/s32k1xx/port/main/CMakeLists.txt): the only caller,
// the safe supervisor, is an archive that comes after the family port's archive on the link line.

#include "SafeConsoleRecord.h"
#include "SafeConsoleWriter.h"

#include <bsp/Uart.h>
#include <etl/span.h>
#include <safeSupervisor/SafeConsole.h>

#include <cstdint>

extern "C"
{
/* What the last line asked for and what the transmitter took, for the board check without a console (J-Link:
 * mem32 of both symbols, after a hold or after a reset that kept the RAM). requested != 0 and written ==
 * requested: the line has left the MCU; written < requested: the transmitter stopped taking bytes. Ordinary RAM
 * (.bss, not the MPU protected safety RAM): written from the hold, possibly with the MPU gate closed. */
volatile uint32_t nxSafeConsoleRequested;
volatile uint32_t nxSafeConsoleWritten;

/* SYS-137: the lines and flushes of this start in the no-init RAM (.noinit.*, not cleared by startUp.S), so that the
 * start after the reset can say whether the line went out (SafeConsoleRecord.h). Cleared by boardInit() after power-on
 * and low voltage, taken and reported by main(). Ordinary RAM, not the MPU protected safety RAM: the fault handlers
 * write it with the gate in any state. */
__attribute__((section(".noinit.safeConsoleRecord")))::platform::safeconsole::Record volatile nxSafeConsoleRecord;
}

namespace
{
class TerminalPort
{
public:
    explicit TerminalPort(::bsp::Uart& uart) : _uart(uart) {}

    // TDRE polling with the time-out of the driver; initializes the UART first if nobody did (bsp::Uart)
    bool waitForTxReady() { return _uart.waitForTxReady(); }

    bool put(uint8_t const byte)
    {
        return _uart.write(::etl::span<uint8_t const>(&byte, 1U)) == 1U;
    }

private:
    ::bsp::Uart& _uart;
};

// Resolved before main() (static initialization), so that the line itself does not pass the ETL_ASSERT of
// getInstance() and its error handler (an indirect call) - only the transmitter is polled.
::bsp::Uart& terminal = ::bsp::Uart::getInstance(::bsp::Uart::Id::TERMINAL);
} // namespace

namespace safety
{
uint32_t safeConsoleWrite(char const* const data, uint32_t const length)
{
    nxSafeConsoleRequested = length;
    nxSafeConsoleWritten   = 0U;
    TerminalPort port(terminal);
    uint32_t const written = ::platform::writeSynchronously(port, data, length);
    nxSafeConsoleWritten   = written;
    ::platform::safeconsole::noteLine(nxSafeConsoleRecord, length, written);
    return written;
}
} // namespace safety
