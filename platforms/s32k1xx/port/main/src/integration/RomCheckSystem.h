#pragma once

/* SYS-38: starts the ROM check (SM-14, safeMemory RomCheck) once per start in the idle context.
 *
 * Before SYS-38 nothing called RomCheck::idle(): SafetyManager::init() only requested the check, the idle handler of
 * the application (ecu/application app.cpp) serves the logger and the console, and the checksum list was removed
 * from the hex. The OpenBSW reference application calls it from its idle handler.
 *
 * Only compiled with NX_ROM_CHECK_IMAGE (cmake/NxRomCheck.cmake), which the build sets exactly when the post-link
 * step writes the reference CRC into the image. The component sits in level 2, after "safety" (level 1, where
 * SafetyManager::init() requests the check). It reports its transition before the check runs, so the lifecycle does
 * not wait for the CRC over the flash (some milliseconds at the lowest priority). A failure or an implausible list
 * reaches the safe supervisor as ROM_CHECK_FAILURE (safe state S1 held, SYS-23). */

#include <lifecycle/SingleContextLifecycleComponent.h>
#include <safeMemory/RomCheck.h>

namespace integration
{
class RomCheckSystem final : public ::lifecycle::SingleContextLifecycleComponent
{
public:
    explicit RomCheckSystem(::async::ContextType const context) : SingleContextLifecycleComponent(context) {}

    void init() override { transitionDone(); }

    void run() override
    {
        transitionDone();
        ::safety::RomCheck::idle();
    }

    void shutdown() override { transitionDone(); }
};

} // namespace integration
