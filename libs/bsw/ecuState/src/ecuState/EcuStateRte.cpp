#include "ecuState/EcuStateRte.h"

#include <atomic>

namespace
{
std::atomic<uint8_t> state{static_cast<uint8_t>(::ecustate::State::STARTUP)};
std::atomic<uint32_t> wakeupSources{::ecustate::wakeup::NONE};
std::atomic<bool> sleepRequest{false};
std::atomic<bool> sleepRequestConnected{false};
} // namespace

namespace ecustate
{
namespace rte
{
void readState(uint8_t& value) { value = state.load(); }

void readWakeupSources(uint32_t& value) { value = wakeupSources.load(); }

void writeSleepRequest(bool const request)
{
    sleepRequest.store(request);
    sleepRequestConnected.store(true);
}

void publish(State const newState, uint32_t const startupWakeupSources)
{
    wakeupSources.store(startupWakeupSources);
    state.store(static_cast<uint8_t>(newState));
}

State publishedState() { return static_cast<State>(state.load()); }

bool isSleepRequestConnected() { return sleepRequestConnected.load(); }

bool isSleepRequested() { return sleepRequest.load(); }

void reset()
{
    state.store(static_cast<uint8_t>(State::STARTUP));
    wakeupSources.store(wakeup::NONE);
    sleepRequest.store(false);
    sleepRequestConnected.store(false);
}
} // namespace rte
} // namespace ecustate
