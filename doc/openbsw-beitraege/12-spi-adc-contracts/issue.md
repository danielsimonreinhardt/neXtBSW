<!-- SPDX-License-Identifier: Apache-2.0 -->

# Issue text: hardware-neutral contracts for SPI devices and synchronous ADC reads in `libs/bsp`

Prepared for <https://github.com/eclipse-openbsw/openbsw/issues>. Upstream state checked against `main`
commit `43ca1e2` and the open pull requests on 2026-09-18. This is a proposal for discussion, not a bug.

**This package has no `fix.patch` on purpose** - see "Why no patch" at the end.

---

**Is your feature request related to a problem?**

`libs/bsp` holds the hardware-neutral interfaces of OpenBSW (`bspCharInputOutput`, `bspInputManager`,
`bspOutputManager`, `bspOutputPwm`, `bspInterrupts`, `bspDynamicClient`). Two kinds of peripheral that
almost every ECU needs have no neutral interface there:

1. **SPI.** There is no SPI driver or interface anywhere in the repository (`platforms/s32k1xx/bsp` has 14
   modules, none for LPSPI; the stm32 platform has none either). A device driver for an SPI part - a
   system basis chip, a gate driver, an external ADC - therefore cannot be written against anything in
   OpenBSW; it has to invent its own abstraction or talk to the peripheral registers.
2. **ADC.** The ADC is reached through `bios::AnalogInput::get(channel, value)`
   (`executables/referenceApp/platforms/s32k148evb/bspConfiguration/include/bsp/adc/AnalogInput.h`), a
   static function of a board configuration class. It works, but its contract is not written down:
   from which contexts it may be called (task, ISR, interrupts locked, before the scheduler starts),
   whether two callers on the same converter are serialised, how long a call can take, and what a
   failure means. `SafeSystem` calls it with interrupts suspended and discards the return code (see our
   separate issue about the division by zero there); application code calls it from tasks. Whether those
   two may overlap is not stated.

**Describe the solution you'd like**

Two small interfaces in `libs/bsp`, in the style of the existing ones, each with a documented contract,
a mock and googletest coverage:

*`libs/bsp/bspSpi` - one SPI device behind a bus*

```cpp
namespace bsp { namespace spi {
/// One SPI slave behind a master. Bus instance, chip select, mode and frame size are fixed by the
/// implementation's configuration, so device drivers stay hardware independent.
class ISpiDevice
{
public:
    /// Full-duplex transfer of one frame, chip select asserted for its duration.
    /// \return false on timeout or bus error; rx is then undefined
    virtual bool transfer(uint32_t tx, uint32_t& rx) = 0;
    /// Same for frames of up to 64 bits (e.g. 40- or 48-bit datagrams of gate drivers and
    /// monitoring ICs), most significant configured bit first.
    virtual bool transferLong(uint64_t tx, uint64_t& rx);   // default: return false
protected:
    ~ISpiDevice() = default;
};
}}
```

The boundary is the *device*, not the bus: a driver for a part gets an `ISpiDevice&` and never sees the
bus instance, the chip select or the baud rate. A platform implements it on top of its SPI peripheral
(we have an S32K1 LPSPI implementation, polled, with a configured per-frame timeout).

*ADC - write the contract down (and optionally give it an interface)*

The properties we found we needed, and which we would propose as the documented contract of a
synchronous read:

1. It may be called from every context: task, ISR, with interrupts or the scheduler suspended, and
   before the scheduler starts. Serialising callers on the same converter is the implementation's job,
   not the caller's.
2. It returns after a bounded time derived from the conversion time of the configured converter, never
   after an arbitrary constant, and it does so even if the converter never signals completion.
3. How long a read may keep interrupts suspended is a decision of the project, not of the driver.
4. A failed read is reported, never replaced by a value. The caller decides what a failure means; a
   safety monitor must not read a failure as a measurement.

We implement that as a small header-only wrapper around the existing converter with two static
policies (a lock policy - RTOS mutex in task context, short exclusive section otherwise - and the
converter itself). It would sit behind `AnalogInput::get()` without changing its signature, or become a
neutral `IAnalogInput` if you prefer an interface there.

**Describe alternatives you've considered**

- Keep these downstream (what we do today). It works, but every project on OpenBSW that uses SPI parts
  or reads the ADC from more than one context solves the same problem again.
- Put SPI into `platforms/<family>/bsp` only. That is where the implementation belongs, but device
  drivers in `libs/bsw` then depend on a platform.

**Additional context**

- #199 ("Discuss about Abstraction of Application from Drivers", open) asks the same question from the
  application side - where the hardware-neutral interfaces should live. This proposal is one concrete
  answer for two peripherals.
- #599 (RP2xxx platform, open) adds another `bspAdc`, again platform-specific; a written ADC contract
  would tell that port what it has to guarantee.

**Why no patch**

This is an interface decision for the project, not a fix: it adds a module to `libs/bsp`, fixes a
naming and namespace choice, and defines a behaviour that other platforms then have to meet.
`CONTRIBUTING.md` asks to agree such work in an issue first, so we would like to hear whether you want
it, and in which form, before sending code. In addition our versions are not yet in OpenBSW shape: they
have a `module.yaml` instead of `module.spec`, host tests with our own runner instead of googletest, and
no `doc/index.rst`. If you are interested we would port them (interface + mock + googletest + doc) as
two separate pull requests, SPI first because nothing exists there yet.
