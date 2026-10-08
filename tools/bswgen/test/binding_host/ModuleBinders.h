#pragma once

/* ARCH-52: host binders for the module binding test - the same interface as the binders of a family integration
 * (platforms/<family>/port/main/src/integration/ModuleBinders.h: FamilyBinders and the NX_BINDER_* macros), on a pin
 * and bus model of the synthetic board testboard_binding instead of a microcontroller. The generated block and the
 * trait of the synthetic driver compile against it exactly as against a family. */

#include "integration/moduleBindingConfiguration.h"

#include <spi/ISpiDevice.h>

#include <cstdint>

namespace host
{
// board entries of testboard_binding (dio, adc), in no particular order
enum class Pin : uint8_t
{
    SYN_A_EN,
    SYN_B_EN,
    SYN_A_FAULT,
    SYN_A_SENSE,
    SYN_B_SENSE,
    COUNT
};

// SPI instances the board may name
enum class Bus : uint8_t
{
    LPSPI0,
    LPSPI1,
    LPSPI2
};

constexpr uint8_t index(Pin const pin) { return static_cast<uint8_t>(pin); }

struct Board
{
    bool level[index(Pin::COUNT)];
    uint32_t writes[index(Pin::COUNT)];
    uint16_t raw[index(Pin::COUNT)];
};

extern Board board;
} // namespace host

namespace integration
{
struct HostChannel
{
    host::Pin pin;
};

struct FamilyBinders
{
    using DigitalOutput = host::Pin;
    using DigitalInput  = host::Pin;
    using AnalogInput   = HostChannel;

    static bool set(DigitalOutput const output, bool const on)
    {
        host::board.level[host::index(output)] = on;
        ++host::board.writes[host::index(output)];
        return true;
    }

    static bool get(DigitalInput const input, bool& active)
    {
        active = host::board.level[host::index(input)];
        return true;
    }

    static bool read(AnalogInput const& channel, uint16_t& raw)
    {
        raw = host::board.raw[host::index(channel.pin)];
        return true;
    }

    // a characteristic per channel: SYN_B_SENSE has an offset, so a swapped channel shows
    static int32_t physical(AnalogInput const& channel, uint16_t const raw, uint8_t& quality)
    {
        quality = 0U;
        return (2 * static_cast<int32_t>(raw)) + ((channel.pin == host::Pin::SYN_B_SENSE) ? 1000 : 0);
    }

    struct SpiBus
    {
        host::Bus bus;
        uint8_t pcs;
    };

    /** The contract spi_device: records the bus, the chip select and the device facts of trait T. */
    template<class T>
    class SpiDevice : public ::bsp::spi::ISpiDevice
    {
    public:
        explicit SpiDevice(SpiBus const& bus) : _bus(bus) {}

        SpiDevice(SpiDevice const&)            = delete;
        SpiDevice& operator=(SpiDevice const&) = delete;

        bool transfer(uint32_t const tx, uint32_t& rx) override
        {
            ++_transfers;
            _lastTx = tx;
            rx      = answer;
            return true;
        }

        SpiBus bus() const { return _bus; }

        uint32_t transfers() const { return _transfers; }

        uint32_t lastTx() const { return _lastTx; }

        static constexpr uint8_t frameBits() { return T::SPI_FRAME_BITS; }

        static constexpr uint32_t maxHz() { return T::SPI_MAX_HZ; }

        uint32_t answer = 0U;

    private:
        SpiBus const _bus;
        uint32_t _transfers = 0U;
        uint32_t _lastTx    = 0U;
    };
};
} // namespace integration

#define NX_BINDER_DIGITAL_OUTPUT(entry) (::host::Pin::entry)
#define NX_BINDER_DIGITAL_INPUT(entry)  (::host::Pin::entry)
#define NX_BINDER_ANALOG_INPUT(entry)   (::integration::HostChannel{::host::Pin::entry})
#define NX_BINDER_SPI_BUS(instance, pcs) \
    (::integration::FamilyBinders::SpiBus{::host::Bus::instance, static_cast<uint8_t>(pcs)})
