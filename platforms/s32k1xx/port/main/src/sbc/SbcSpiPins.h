/* ARCH-40: pin multiplexing of the SBC bus for SbcEarlyStart, which runs from main() before StaticBsp::hwInit muxes
 * the pins of every used SPI bus (lifecycle/SpiBusPins.h). The pins are the four of the SBC's bus in the generat
 * (bsp/spi/spiBusConfiguration.h, NX_SPI_BUS_PINS_<bus>), the bus is the one of the SBC device
 * (bsp/sbc/sbcConfiguration.h, BSP_SBC_SPI(BUS)); no board name is spelled out here. Header only, so the host test
 * (tools/bswgen/test_bswgen.py, BswGenSbcSpiHostTest) runs exactly this function against the generat of its own
 * test profile. */

#pragma once

#include "bsp/sbc/sbcConfiguration.h"
#include "bsp/spi/spiBusConfiguration.h"
#include "io/Io.h"

namespace systems
{
/* Writes the default configuration of bsp/io/io/ioConfiguration.h (the same the bootloader and StaticBsp set) to
 * SCK, SIN, SOUT and PCS of the SBC bus, in that order. No other pin is touched. */
inline void muxSbcSpiPins()
{
#define SBC_SPI_PIN_MUX(pin) (void)::bios::Io::setDefaultConfiguration(::bios::Io::pin);
    BSP_SBC_CONCAT(NX_SPI_BUS_PINS_, BSP_SBC_SPI(BUS))(SBC_SPI_PIN_MUX)
#undef SBC_SPI_PIN_MUX
}
} // namespace systems
