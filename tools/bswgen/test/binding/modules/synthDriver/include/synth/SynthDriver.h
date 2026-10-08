#pragma once

/* ARCH-52: synthetic device driver of the module binding test (test data, no hardware). A driver of this shape lives
 * in libs/bsw/<module>: hardware independent, only contracts (ISpiDevice, its own pin interface) and a lifecycle
 * component. On the host the lifecycle and async headers are the stubs of tools/bswgen/test/binding_host/stubs. */

#include <async/Types.h>
#include <lifecycle/ILifecycleComponent.h>
#include <spi/ISpiDevice.h>

#include <cstdint>

namespace synth
{
/** Answer of the device to the identification frame. */
constexpr uint32_t DEVICE_ID = 0x5A17U;
/** Identification frame (16 bit, read of register 0). */
constexpr uint32_t ID_FRAME = 0x8000U;

struct Config
{
    uint32_t cycleMs;
    int32_t offsetC;
    bool fast;
};

/**
 * A rule of the driver the manifest does not state (the trait checks it at compile time): an even cycle and an offset
 * of at most 40 degC. A project that passes nxconfig but breaks it stops the build with the trait's message.
 */
constexpr bool isValidConfig(Config const& c)
{
    return (c.cycleMs >= 2U) && ((c.cycleMs % 2U) == 0U) && (c.offsetC >= -40) && (c.offsetC <= 40);
}

class ISynthIo
{
public:
    virtual bool setEnable(bool on)       = 0;
    virtual bool readFault(bool& active)  = 0;
    virtual bool faultWired() const       = 0;
    virtual bool readSense(int32_t& value) = 0;

protected:
    ~ISynthIo() = default;
};

/** One reading for the RTE (module.yaml rte). quality: 0 ok, 1 not started yet, 2 device error. */
struct Reading
{
    uint16_t value;
    uint8_t quality;
};

class SynthDriver
{
public:
    using Lock = bool (*)();

    SynthDriver(::bsp::spi::ISpiDevice& spi, ISynthIo& io, Config const& config)
    : _spi(spi), _io(io), _config(config)
    {}

    bool start()
    {
        uint32_t rx = 0U;
        _started    = true;
        _ok         = _spi.transfer(ID_FRAME, rx) && (rx == DEVICE_ID);
        (void)_io.setEnable(false);
        return _ok;
    }

    /** Explicit enable of the application; refused before a good start, while locked and with an active fault. */
    bool enable()
    {
        bool fault = false;
        if (!_ok || locked() || (_io.faultWired() && (!_io.readFault(fault) || fault)))
        {
            return false;
        }
        if (!_io.setEnable(true))
        {
            return false;
        }
        _enabled = true;
        ++_enables;
        return true;
    }

    void disable()
    {
        (void)_io.setEnable(false);
        _enabled = false;
    }

    void setLock(Lock const lock) { _lock = lock; }

    /** Evaluates the lock now: output off while it is set. \return true if a lock is attached and set. */
    bool updateLock()
    {
        if (!locked())
        {
            return false;
        }
        disable();
        return true;
    }

    void cycle()
    {
        int32_t value = 0;
        if (_io.readSense(value))
        {
            value += _config.offsetC;
            _value = static_cast<uint16_t>((value < 0) ? 0 : ((value > 0xFFFF) ? 0xFFFF : value));
        }
    }

    Reading reading() const
    {
        return Reading{_value, static_cast<uint8_t>(!_started ? 1U : (_ok ? 0U : 2U))};
    }

    bool isEnabled() const { return _enabled; }

    uint32_t enables() const { return _enables; }

    Config const& config() const { return _config; }

private:
    bool locked() const { return (_lock != nullptr) && _lock(); }

    ::bsp::spi::ISpiDevice& _spi;
    ISynthIo& _io;
    Config const _config;
    Lock _lock        = nullptr;
    uint32_t _enables = 0U;
    uint16_t _value   = 0U;
    bool _started     = false;
    bool _ok          = false;
    bool _enabled     = false;
};

/** Lifecycle component of one instance: start at init, a sense sample per run, off at shutdown. */
class SynthSystem : public ::lifecycle::ILifecycleComponent
{
public:
    SynthSystem(
        ::async::ContextType const context,
        ::bsp::spi::ISpiDevice& spi,
        ISynthIo& io,
        Config const& config,
        char const* const command,
        char const* const label)
    : _context(context), _driver(spi, io, config), _command(command), _label(label)
    {}

    void init() override { (void)_driver.start(); }

    void run() override { _driver.cycle(); }

    void shutdown() override { _driver.disable(); }

    SynthDriver& driver() { return _driver; }

    SynthDriver const& driver() const { return _driver; }

    ::async::ContextType context() const { return _context; }

    char const* command() const { return _command; }

    char const* label() const { return _label; }

private:
    ::async::ContextType const _context;
    SynthDriver _driver;
    char const* const _command;
    char const* const _label;
};

} // namespace synth
