#pragma once

#include "bswService/BswServiceRte.h"
#include "sbc/Uja113x.h"

#include <cstdint>

/**
 * RTE-16: the UJA113x battery monitor as provider of the BSW service bsw.power (bswService/BswServiceRte.h).
 * SbcSystem reads Uja113x::readBatteryVoltages() and hands the result over with publish(); the mapping is here,
 * without SPI, so the host test (test/host/SbcPowerReportHostTest.cpp) checks it.
 */
namespace systems
{
namespace sbcpower
{
struct Report
{
    bool failed; // no reading: SBC not in Normal mode (startup failed, re-initialization) or SPI error
    ::bswservice::power::State state;
    uint16_t supplyMv;
};

/**
 * Result of Uja113x::readBatteryVoltages() -> service value. The voltage is the one the SBC compares with the
 * thresholds (BatteryMonitorConfig::source); BMUVS/BMOVS give the state. Without a monitor configuration
 * (configure = false) the SBC has no thresholds of the project: the voltage of pin BAT is reported, the state stays
 * UNKNOWN - an "OK" without limits would claim more than the SBC knows.
 */
inline Report reportOf(
    ::sbc::Uja113x::Status const status,
    ::sbc::Uja113x::BatteryVoltages const& voltages,
    ::sbc::BatteryMonitorConfig const& monitor)
{
    using State = ::bswservice::power::State;
    if (status != ::sbc::Uja113x::Status::OK)
    {
        return Report{true, State::UNKNOWN, 0U};
    }
    if (!monitor.configure)
    {
        return Report{false, State::UNKNOWN, voltages.batMv};
    }
    uint16_t const mv
        = (monitor.source == ::sbc::BatteryMonitorSource::BATSENSE) ? voltages.batSenseMv : voltages.batMv;
    if (voltages.undervoltage)
    {
        return Report{false, State::UNDERVOLTAGE, mv};
    }
    if (voltages.overvoltage)
    {
        return Report{false, State::OVERVOLTAGE, mv};
    }
    return Report{false, State::OK, mv};
}

/** Hands \p report over to the service: publish() or, without a reading, publishFailure() (last values stay). */
inline void publish(Report const& report)
{
    if (report.failed)
    {
        ::bswservice::power::publishFailure();
    }
    else
    {
        ::bswservice::power::publish(report.state, report.supplyMv);
    }
}
} // namespace sbcpower
} // namespace systems
