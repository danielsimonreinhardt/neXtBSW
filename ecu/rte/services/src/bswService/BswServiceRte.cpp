#include "bswService/BswServiceRte.h"

#include <bsp/timer/SystemTimer.h>
#include <ecuState/EcuState.h>
#include <ecuState/EcuStateRte.h>

#include <atomic>

// RTE-16: set by ecu/rte/CMakeLists.txt from the providers the family port registered; host tests set them
// themselves. Not defined: a provider is assumed (power) and no MCU reset reason is read (reset), as in RTE-05.
#ifndef NX_BSW_POWER_PROVIDER
#define NX_BSW_POWER_PROVIDER 1
#endif
#ifndef NX_BSW_MCU_RESET_REASON
#define NX_BSW_MCU_RESET_REASON 0
#endif
#if NX_BSW_MCU_RESET_REASON
#include "bswService/McuResetReason.h"
#endif

namespace
{
using ::bswservice::Health;

// bsw.power: state (bits 0..7), health (bits 8..15), supply voltage in mV (bits 16..31) in one word, so a reader in
// another task always gets a consistent triple. Only load and store (no read-modify-write): plain word accesses on
// every Cortex-M/R core, no libatomic on ARMv6-M (PLAT-28).
constexpr uint32_t packPower(uint8_t const state, uint8_t const health, uint16_t const mv)
{
    return static_cast<uint32_t>(state) | (static_cast<uint32_t>(health) << 8U) | (static_cast<uint32_t>(mv) << 16U);
}

// Before the first reading: UNKNOWN, 0 mV and PENDING (QLF_DEBOUNCE) while a provider will come; without a provider
// (board without supply monitor, RTE-16) FAILED (QLF_ERROR) right away, because nobody will ever publish.
constexpr uint32_t POWER_INITIAL = packPower(
    static_cast<uint8_t>(::bswservice::power::State::UNKNOWN),
    static_cast<uint8_t>((NX_BSW_POWER_PROVIDER != 0) ? Health::PENDING : Health::FAILED),
    0U);

std::atomic<uint32_t> powerWord{POWER_INITIAL};
} // namespace

namespace bswservice
{
namespace power
{
void publish(State const state, uint16_t const supplyMv)
{
    powerWord.store(packPower(static_cast<uint8_t>(state), static_cast<uint8_t>(Health::OK), supplyMv));
}

void publishFailure()
{
    // one provider per ECU: nobody else writes between load and store
    uint32_t const word = powerWord.load();
    powerWord.store((word & ~0x0000FF00UL) | (static_cast<uint32_t>(Health::FAILED) << 8U));
}
} // namespace power

namespace reset
{
Reason reasonOf(uint32_t const startReasons)
{
    namespace wakeup = ::ecustate::wakeup;
    if ((startReasons & wakeup::WATCHDOG) != 0U)
    {
        return Reason::WATCHDOG;
    }
    if ((startReasons & wakeup::SUPPLY) != 0U)
    {
        return Reason::SUPPLY;
    }
    if ((startReasons & (wakeup::CAN_BUSES | wakeup::LOCALS | wakeup::TIMER)) != 0U)
    {
        return Reason::WAKEUP;
    }
    if ((startReasons & wakeup::POWER) != 0U)
    {
        return Reason::POWER_ON;
    }
    if ((startReasons & wakeup::RESET) != 0U)
    {
        return Reason::RESET;
    }
    return Reason::UNKNOWN;
}

Health healthOf(uint32_t const startReasons)
{
    if (startReasons == ::ecustate::wakeup::NONE)
    {
        return Health::PENDING;
    }
    return (startReasons == ::ecustate::wakeup::UNKNOWN) ? Health::FAILED : Health::OK;
}

uint32_t combine(uint32_t const supplyReasons, uint32_t const mcuReasons)
{
    if (mcuReasons == ::ecustate::wakeup::NONE)
    {
        return supplyReasons;
    }
    return (supplyReasons & ~::ecustate::wakeup::UNKNOWN) | mcuReasons;
}
} // namespace reset

namespace rte
{
void readPower(uint8_t& state, uint16_t& supplyMv, uint8_t& health)
{
    uint32_t const word = powerWord.load();
    state               = static_cast<uint8_t>(word & 0xFFU);
    health              = static_cast<uint8_t>((word >> 8U) & 0xFFU);
    supplyMv            = static_cast<uint16_t>(word >> 16U);
}

void readReset(uint8_t& reason, uint32_t& startReasons, uint8_t& health)
{
    uint32_t sources = 0U;
    ::ecustate::rte::readWakeupSources(sources);
#if NX_BSW_MCU_RESET_REASON
    sources = ::bswservice::reset::combine(sources, ::bswservice::reset::mcuStartReasons());
#endif
    startReasons = sources;
    reason       = static_cast<uint8_t>(::bswservice::reset::reasonOf(sources));
    health       = static_cast<uint8_t>(::bswservice::reset::healthOf(sources));
}

void readTime(uint32_t& ms, uint32_t& us)
{
    uint64_t const now = getSystemTimeUs64Bit();
    us                 = static_cast<uint32_t>(now);
    ms                 = static_cast<uint32_t>(now / 1000U);
}

void reset() { powerWord.store(POWER_INITIAL); }
} // namespace rte
} // namespace bswservice
