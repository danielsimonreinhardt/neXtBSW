#pragma once

/* ARCH-52: the binders of this family for the generated module binding (integration/moduleBinding.h, tools/bswgen
 * modules.py render_binding_header) - one per connection kind and contract, not one per driver. This is the only
 * part of a module's integration that knows the family (doc/architecture/treiber-anbindung-analyse.md, section 4:
 * the SPI master of a board bus and the ADC channel with its characteristic). A new device driver with a
 * `binding` in its manifest needs no line here; a new connection kind or contract does (ARCH-53).
 *
 *   digital_output  ::bios::Output::<entry> (OpenBSW output manager, ids generated from the board)
 *   digital_input   ::bios::DigitalInput::<entry> (OpenBSW input manager)
 *   analog_input    ::bios::AnalogInput channel Ai_<entry> and its generated characteristic nxAdcPhys_<entry>
 *                   (bsp/adc/analogSensorConfiguration.h: the board's adc[].sensor, adc.phys)
 *   spi_device      an ISpiDevice on a board SPI bus (LPSPI master of the family): bus, chip select and clock from
 *                   the board, frame size, mode, rate limit and timeout from the module's trait (SPI_FRAME_BITS,
 *                   SPI_CPOL, SPI_CPHA, SPI_MAX_HZ, SPI_TIMEOUT_US). The master is set up with the first transfer,
 *                   i.e. in the task of the driver after the clock driver has enabled the peripheral; the pins of
 *                   every used SPI bus are muxed by staticBsp in main() (ARCH-38).
 *
 * Included by the family integration before the generated block (integration/moduleBinding.h); the generated
 * NX_BINDING_*_COUNT (integration/moduleBindingConfiguration.h) select the parts a project needs, so a project
 * whose bound modules use no SPI or ADC does not include those drivers. */

#include "integration/SafeOffOutputs.h" // SYS-151: the commanded level of a safe_off output
#include "integration/moduleBindingConfiguration.h"
#include "inputManager/DigitalInput.h"
#include "outputManager/Output.h"

#include <cstdint>

#if NX_BINDING_ANALOG_INPUT_COUNT > 0
#include "bsp/adc/AnalogInput.h"
#include "bsp/adc/analogSensorConfiguration.h"
#endif
#if NX_BINDING_SPI_DEVICE_COUNT > 0
#include "bsp/clock/boardClock.h"
#include "mcu/mcu.h"
#include "spi/Lpspi.h"
#endif

namespace integration
{
/** One ADC channel of the board with the characteristic that converts its code into the physical value. */
struct AnalogChannel
{
    uint16_t id;
    int32_t (*physical)(uint16_t raw, uint8_t* quality);
};

struct FamilyBinders
{
    using DigitalOutput = ::bios::Output::OutputId;
    using DigitalInput  = ::bios::DigitalInput::DigitalInputId;
    using AnalogInput   = AnalogChannel;

    /** SYS-151: records the commanded level of a safe_off output first, then writes the pin (SafeOffOutputs.h). */
    static bool set(DigitalOutput const output, bool const on)
    {
        return ::integration::safeoff::setCommandedOutput(output, on);
    }

    static bool get(DigitalInput const input, bool& active)
    {
        return ::bios::DigitalInput::get(input, active) == ::bsp::BSP_OK;
    }

#if NX_BINDING_ANALOG_INPUT_COUNT > 0
    /** One conversion of the channel (converter code). */
    static bool read(AnalogInput const& channel, uint16_t& raw)
    {
        return ::bios::AnalogInput::get(channel.id, raw) == ::bsp::BSP_OK;
    }
#endif

    /** Physical value of a converter code (the unit of the channel's sensor, e.g. mA). */
    static int32_t physical(AnalogInput const& channel, uint16_t const raw, uint8_t& quality)
    {
        return channel.physical(raw, &quality);
    }

#if NX_BINDING_SPI_DEVICE_COUNT > 0
    /** A board SPI bus as the family sees it: register block, functional clock, chip select. */
    struct SpiBus
    {
        LPSPI_Type* base;
        uint32_t clockHz;
        uint8_t pcs;
    };

    /** The contract spi_device of one instance: an LPSPI master for the device facts of trait T. */
    template<class T>
    class SpiDevice : public ::bsp::spi::ISpiDevice
    {
    public:
        static_assert(
            ::bsp::spi::isSupportedLpspiFrameBits(T::SPI_FRAME_BITS),
            "frame size of the module (trait SPI_FRAME_BITS) not supported by the LPSPI master of this family");

        explicit SpiDevice(SpiBus const& bus)
        : _config{bus.base,
                  bus.clockHz,
                  T::SPI_MAX_HZ,
                  bus.pcs,
                  T::SPI_FRAME_BITS,
                  T::SPI_CPOL,
                  T::SPI_CPHA,
                  T::SPI_TIMEOUT_US}
        , _master(_config)
        , _started(false)
        {}

        SpiDevice(SpiDevice const&)            = delete;
        SpiDevice& operator=(SpiDevice const&) = delete;

        bool transfer(uint32_t const tx, uint32_t& rx) override
        {
            start();
            return _master.transfer(tx, rx);
        }

        bool transferLong(uint64_t const tx, uint64_t& rx) override
        {
            start();
            return _master.transferLong(tx, rx);
        }

    private:
        void start()
        {
            if (!_started)
            {
                _master.init();
                _started = true;
            }
        }

        ::bsp::spi::LpspiConfig const _config; // before _master, which keeps a reference to it
        ::bsp::spi::Lpspi _master;
        bool _started;
    };
#endif
};
} // namespace integration

// Values of the generated instance descriptions (Connect::<CONN>) from the board entry name of a connection.
#define NX_BINDER_CONCAT_(a, b) a##b
#define NX_BINDER_CONCAT(a, b)  NX_BINDER_CONCAT_(a, b)
#define NX_BINDER_DIGITAL_OUTPUT(entry) (::bios::Output::entry)
#define NX_BINDER_DIGITAL_INPUT(entry)  (::bios::DigitalInput::entry)
#define NX_BINDER_ANALOG_INPUT(entry) \
    (::integration::AnalogChannel{static_cast<uint16_t>(::bios::AnalogInput::Ai_##entry), &nxAdcPhys_##entry})
// SPI bus of an instance from the generated NX_DEVICE_<device>_SPI_INSTANCE / _SPI_PCS (expanded before pasting)
#define NX_BINDER_SPI_BUS(instance, pcs)                                            \
    (::integration::FamilyBinders::SpiBus{                                          \
        NX_BINDER_CONCAT(IP_, instance),                                            \
        NX_BINDER_CONCAT(NX_BINDER_CONCAT(NX_CLOCK_, instance), _HZ),               \
        static_cast<uint8_t>(pcs)})
