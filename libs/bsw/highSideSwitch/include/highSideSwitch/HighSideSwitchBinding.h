#pragma once

/* ARCH-66: trait of the smart high-side switch for the generated module binding (module.yaml binding.trait
 * ::highside::HighSideSwitchBinding, contract: tools/bswgen/modules.py section "binding (ARCH-52)"). It replaces the
 * hand-written block of the family integration (HIGH_SIDE_SWITCH_DATA, BiosHighSideSwitchIo,
 * HighSideSwitchPowerStageOutput): tools/bswgen generates the objects per instance, the lifecycle registration in
 * level and task of the manifest and the safe_off adapter; this header holds the few driver-specific lines.
 *
 * Family neutral: the pins are reached through the family binders B (B::set for IN/DEN, B::read and B::physical
 * for the sense channel IS with the board's characteristic, IO-21), never through a family driver. */

#include "highSideSwitch/HighSideSwitch.h"
#include "highSideSwitch/HighSideSwitchNames.h"
#include "highSideSwitch/HighSideSwitchSafeOff.h"
#include "highSideSwitch/HighSideSwitchSense.h"
#include "highSideSwitch/HighSideSwitchSystem.h"
#include "highSideSwitch/IHighSideSwitchIo.h"

#include <async/Types.h>
#include <etl/alignment.h>

#include <cstddef>
#include <cstdint>

namespace highside
{
inline HighSideSwitchChannel& channelOf(HighSideSwitchSystem& system) { return system.getChannel(); }

inline HighSideSwitchChannel const& channelOf(HighSideSwitchSystem const& system) { return system.getChannel(); }

namespace detail
{
/** A string built at compile time (static storage when it is a static constexpr member). */
template<size_t N>
struct Text
{
    char chars[N];
};

/** a + b + c + d without the three inner terminators. */
template<size_t A, size_t B, size_t C, size_t D>
constexpr Text<A + B + C + D - 3U>
join(char const (&a)[A], char const (&b)[B], char const (&c)[C], char const (&d)[D])
{
    Text<A + B + C + D - 3U> text{};
    size_t n = 0U;
    for (size_t i = 0U; i + 1U < A; ++i)
    {
        text.chars[n++] = a[i];
    }
    for (size_t i = 0U; i + 1U < B; ++i)
    {
        text.chars[n++] = b[i];
    }
    for (size_t i = 0U; i + 1U < C; ++i)
    {
        text.chars[n++] = c[i];
    }
    for (size_t i = 0U; i + 1U < D; ++i)
    {
        text.chars[n++] = d[i];
    }
    text.chars[n] = '\0';
    return text;
}
} // namespace detail

struct HighSideSwitchBinding
{
    using System = HighSideSwitchSystem;

    /** Configuration of instance I: the parameters of module.yaml, current levels in mA load current (IO-21). */
    template<class I>
    static constexpr HighSideSwitchConfig config()
    {
        return HighSideSwitchConfig{
            I::Param::CYCLE_MS,
            I::Param::OVERCURRENT_MA,
            I::Param::OVERCURRENT_DEBOUNCE_MS,
            I::Param::FAULT_MA,
            I::Param::FAULT_DEBOUNCE_MS,
            I::Param::SWITCH_ON_BLANKING_MS,
            I::Param::OPEN_LOAD_MA,
            I::Param::OFF_MAX_MA,
            I::Param::OFF_DEBOUNCE_MS};
    }

    static constexpr bool valid(HighSideSwitchConfig const& c) { return isValidHighSideSwitchConfig(c); }

    /**
     * Constants of instance I in static storage (the state machine keeps a reference to its configuration, the
     * system copies the names): console group, label and help text "<LABEL> high-side switch (<part>)".
     */
    template<class I>
    struct Instance
    {
        static constexpr HighSideSwitchConfig CONFIG = config<I>();
        static constexpr auto DESCRIPTION            = detail::join(I::LABEL, " high-side switch (", I::PART, ")");
        static constexpr HighSideSwitchNames NAMES   = {I::COMPONENT, I::LABEL, DESCRIPTION.chars};
    };

    /** Pins of instance I on the family binders B: IN and DEN digital outputs, IS the sense channel in mA. */
    template<class I, class B>
    class Parts final : public IHighSideSwitchIo
    {
    public:
        Parts() = default;

        Parts(Parts const&)            = delete;
        Parts& operator=(Parts const&) = delete;

        bool setEnable(bool const on) override { return B::set(I::Connect::IN, on); }

        bool setDiagEnable(bool const on) override { return B::set(I::Connect::DEN, on); }

        bool readSense(SenseSample& sample) override
        {
            uint16_t raw = 0U;
            if (!B::read(I::Connect::IS, raw))
            {
                return false;
            }
            sample.loadMa = loadCurrentMa(&convert, raw);
            sample.raw    = raw;
            return true;
        }

    private:
        /** The characteristic of the board channel wired to IS (board adc[].sensor, k_ILIS and R_IS). */
        static int32_t convert(uint16_t const raw, uint8_t* const quality)
        {
            uint8_t q           = 0U;
            int32_t const value = B::physical(I::Connect::IS, raw, q);
            *quality            = q;
            return value;
        }
    };

    template<class I, class B>
    static System&
    create(::etl::typed_storage<System>& storage, ::async::ContextType const context, Parts<I, B>& parts)
    {
        // SYS-16: every log line of HighSideSwitchSystem is sized for this label (tools/logcheck bounds.yaml)
        static_assert(
            hasValidLabel(Instance<I>::NAMES),
            "highSideSwitch: label longer than HighSideSwitchNames::LABEL_MAX_CHARS (log lines)");
        return storage.create(context, parts, Instance<I>::CONFIG, Instance<I>::NAMES);
    }

    /** safe_off connection in (IO-37): HighSideSwitchSafeOff.h. */
    using SafeOff_in = HighSideSwitchSafeOff<HighSideSwitchSystem>;
};

} // namespace highside
