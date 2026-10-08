// NVM-23: the data flash areas of the project (project.yaml nvm.data_flash, generated nvm/nvmDataFlash.h) in the
// family port - follow-up of NVM-12, which built the contract (libs/bsw/nvmDataFlash) and the s32k1xx driver
// (platforms/s32k1xx/bsp/bspFlexNvm flexnvm/FlexNvmDataFlash.h) but linked them into no application.
//
// What the integration adds (src/systems/DataFlashSystem.cpp, only for a project with areas, NX_DFLASH_AREA_COUNT > 0):
//  - one FlexNvmDataFlash with the region of the generated header and the cache hook (cacheInvalidate() on a
//    derivative with the LMEM code cache, NX_FEATURE_LMEM_CACHE; none otherwise);
//  - the gate of the run-down (systems/DataFlashRunDown.h, safety review NVM12-S2) between the driver and
//  - one dataflash::AreaService over the generated table, with the write hooks of the ROM check
//    (safety::romCheckFlashWrites.begin()/end(), SYS-44 - per flash command);
//  - this lifecycle component: in the task of the power supply (TASK_BSP) it advances the job every 10 ms
//    (dflash::advance(), never waits), unlocks the gate in run() and locks it in shutdown();
//  - ShutdownStep::NVM of the run-down: dataFlashRunDownStep() wraps the NvM step of the application;
//  - in test builds only (CMake option NX_DFLASH_COMMAND): the console command "dflash" for the board check
//    (show, read, erase, write; areas by name).
// Users of the areas call dataFlashAreas() from the owner task only (TASK_BSP; from the console through
// console::runInOwnerContext) and read through AreaService::read() or, in place, only within a held
// AreaService::holdForDirectRead() scope via areaAddress() (safety review NVM12-S1 b, NVM-25: the block lock shared with
// the EEPROM stores of the NvM, flexnvm/FlexNvmBlockLock.h). No new erase/write job from STANDBY on (NVM23-S1 b,
// NVM-26: admission from ecustate::rte::publishedState()); in test builds "dflash hold" checks the lock on the board.

#pragma once

#include "console/AsyncCommandWrapper.h"
#include "lifecycle/SingleContextLifecycleComponent.h"

#include <dataflash/DataFlashAreas.h>
#include <ecuState/IShutdownStep.h>
#include <util/command/GroupCommand.h>

#include <cstdint>

namespace systems
{

class DataFlashSystem final
: public ::lifecycle::SingleContextLifecycleComponent
, private ::async::RunnableType
{
public:
    /** Period of the job steps, ms (one flash command per period at most, plus its poll). */
    static constexpr uint32_t CYCLE_MS = 10U;
    /** Steps of AreaService::cyclic() per period (dflash::advance()). */
    static constexpr uint8_t STEPS_PER_CYCLE = 4U;

    explicit DataFlashSystem(::async::ContextType context);
    DataFlashSystem(DataFlashSystem const&)            = delete;
    DataFlashSystem& operator=(DataFlashSystem const&) = delete;

    void init() override;
    void run() override;
    void shutdown() override;

private:
#ifdef NX_DFLASH_COMMAND
    class Command : public ::util::command::GroupCommand
    {
    public:
        explicit Command(DataFlashSystem& system) : _system(system) {}

    protected:
        DECLARE_COMMAND_GROUP_GET_INFO
        void executeCommand(::util::command::CommandContext& context, uint8_t idx) override;

    private:
        DataFlashSystem& _system;
    };
#endif

    void execute() override;

    ::async::ContextType const _context;
    ::async::TimeoutType _timeout;
#ifdef NX_DFLASH_COMMAND
    Command _command;
    ::console::AsyncCommandWrapper _asyncCommand;
#endif
};

/** The areas of the project (owner task only, see the file comment). */
::dataflash::AreaService& dataFlashAreas();

/**
 * ShutdownStep::NVM of a project with data flash: locks the data flash, runs \p nvmStep (nullptr: the project has
 * no NvM step) and ends only when no data flash command is in flight (systems/DataFlashRunDown.h). Constant
 * initialised, so it may be called during static initialisation (integration shutdown sequence).
 */
::ecustate::IShutdownStep& dataFlashRunDownStep(::ecustate::IShutdownStep* nvmStep);

} // namespace systems
