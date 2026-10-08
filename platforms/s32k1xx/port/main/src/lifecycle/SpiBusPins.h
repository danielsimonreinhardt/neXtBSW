/* ARCH-38: pin multiplexing of every SPI bus the project uses, whatever driver uses the bus.
 *
 * CAN and UART drivers of OpenBSW mux their own pins; the SPI master of the family (bsp::spi::Lpspi) takes no pins.
 * Without this step a bus runs internally without pins, reports no error and reads 0 over the pull-down at SDO
 * (TMC6200 on the eval board, board test 2026-09-18). The pins come from the generated bsp/spi/spiBusConfiguration.h
 * (board.yaml spi[], project.yaml use.spi): SCK, SIN, SOUT and PCS of each used bus in board order, with the
 * configuration of bsp/io/io/ioConfiguration.h. Header only, so the host test (tools/bswgen/test/spi_host) runs
 * exactly this function against the generat of its own test profile. */

#pragma once

#include "bsp/spi/spiBusConfiguration.h"
#include "io/Io.h"

namespace platform
{
/* Called by StaticBsp::hwInit in main() before any lifecycle component, i.e. before any driver starts its bus.
 * Writing the same configuration again (the bootloader has set the pins of its own SPI device already) changes no
 * pin. Every listed pin exists in the Io table, so setDefaultConfiguration cannot fail here. */
inline void muxSpiBusPins()
{
#define SPI_BUS_PIN_MUX(pin) (void)::bios::Io::setDefaultConfiguration(::bios::Io::pin);
    NX_SPI_BUS_PINS(SPI_BUS_PIN_MUX)
#undef SPI_BUS_PIN_MUX
}
} // namespace platform
