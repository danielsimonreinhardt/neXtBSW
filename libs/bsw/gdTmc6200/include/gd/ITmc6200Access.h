#pragma once

#include <cstdint>

namespace gd
{

/**
 * One TMC6200 datagram on its SPI bus: 40 bits, MSB first, chip select low for the whole datagram
 * (datasheet p.18 4.1, p.19 4.2).
 *
 * Thin access adapter (doc/architecture/treiber-granularitaet.md, section 1 case 3): the L1 contract
 * bsp::spi::ISpiDevice carries at most 32 bits per frame, the integration implements this interface on top
 * of an SPI master that can send 40-bit frames (open point of IO-05, libs/bsw/gdTmc6200/doc/gdTmc6200.md).
 */
class ITmc6200Spi
{
public:
    /**
     * Full-duplex transfer of one 40-bit datagram (low-order 40 bits of \p tx and \p rx).
     * \return false on timeout or bus error; \p rx is then undefined
     */
    virtual bool transfer(uint64_t tx, uint64_t& rx) = 0;

protected:
    ~ITmc6200Spi() = default;
};

/**
 * Pins of one TMC6200 besides SPI: the driver enable input DRV_EN (p.8 pin 27, positive active) and the
 * diagnostics output FAULT (p.8 pin 26, high upon driver error). The integration implements it on top of the
 * digital outputs and inputs of the project, so the driver stays board and family independent.
 */
class ITmc6200Io
{
public:
    /** Drives DRV_EN. \return false if the output could not be written */
    virtual bool setDriverEnable(bool on) = 0;

    /**
     * Reads the FAULT output (true = error).
     * \return false if FAULT is not wired on this board or could not be read; \p active is then ignored
     */
    virtual bool readFault(bool& active) = 0;

protected:
    ~ITmc6200Io() = default;
};

} // namespace gd
