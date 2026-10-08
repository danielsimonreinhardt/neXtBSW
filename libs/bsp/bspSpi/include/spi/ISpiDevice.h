#pragma once

#include <cstdint>

namespace bsp
{
namespace spi
{

/**
 * One SPI slave behind a master: bus instance, chip select, mode and frame size are fixed by the
 * implementation's configuration, so device drivers stay hardware independent.
 */
class ISpiDevice
{
public:
    /**
     * Full-duplex transfer of one frame with chip select asserted for its duration.
     * Only the configured number of low-order bits of \p tx is sent and returned in \p rx.
     * \return false on timeout or bus error; \p rx is then undefined
     */
    virtual bool transfer(uint32_t tx, uint32_t& rx) = 0;

    /**
     * Full-duplex transfer of one frame of up to 64 bits (e.g. 40-bit TMC6200 datagrams, 48-bit MC33771C frames)
     * with chip select asserted for the whole frame, most significant configured bit first on the wire.
     * Only the configured number of low-order bits of \p tx is sent and returned in \p rx.
     * Not pure virtual: implementations limited to 32 bits and test fakes stay unchanged.
     * \return false if the implementation does not support the configured frame size, on timeout or bus error;
     *         \p rx is then undefined
     */
    virtual bool transferLong(uint64_t tx, uint64_t& rx)
    {
        (void)tx;
        (void)rx;
        return false;
    }

protected:
    ~ISpiDevice() = default;
};

} // namespace spi
} // namespace bsp
