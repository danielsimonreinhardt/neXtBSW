// NVM-23: data flash areas of the project in the family port, see systems/DataFlashSystem.h and
// systems/DataFlashRunDown.h. Compiled empty for a project without nvm.data_flash (NX_DFLASH_AREA_COUNT 0).
// NVM-25: block lock shared with the EEPROM stores (flexnvm/FlexNvmBlockLock.h), "dflash hold" in test builds.
// NVM-26: no new job from STANDBY on (admission of the AreaService from the published ECU state).

#include <nvm/nvmDataFlash.h>

#if defined(NX_DFLASH_AREA_COUNT) && (NX_DFLASH_AREA_COUNT > 0)

#include "systems/DataFlashSystem.h"

#include "console/ConsoleContext.h"
#include "features/derivativeFeatures.h"
#include "flexnvm/FlexNvmDataFlash.h"
#include "systems/DataFlashRunDown.h"

#include <ecuState/EcuStateRte.h>
#include <safeMemory/RomCheckFlashWrites.h>
#include <util/logger/Logger.h>

#if NX_FEATURE_LMEM_CACHE
#include "cache/cache.h"
#endif

#ifdef NX_DFLASH_COMMAND
#include "flexnvm/FlexNvmEeprom.h"

#include <bsp/timer/SystemTimer.h>
#include <etl/optional.h>
#include <util/command/CommandContext.h>
#include <util/format/SharedStringWriter.h>
#endif

#include <cstdint>

DECLARE_LOGGER_COMPONENT(BSP)

#ifdef NX_DFLASH_COMMAND
namespace platform
{
FlexNvmEeprom& flexNvmEepromInstance(); // lifecycle/StaticBsp.cpp (NVM-25: waits of the stores in "dflash show")
} // namespace platform
#endif

namespace
{
#if NX_FEATURE_LMEM_CACHE
// FlexNvmDataFlash: after every command, so no line of the sector as it was before is read. The start-up makes the
// FlexNVM region non-cacheable (main.cpp, NVM12-S4); the hook stays as the driver's contract for the cache.
void invalidateCodeCache() { cacheInvalidate(NX_DFLASH_START, NX_DFLASH_SIZE); }

constexpr ::platform::FlexNvmDataFlash::Hook CACHE_HOOK = &invalidateCodeCache;
#else
constexpr ::platform::FlexNvmDataFlash::Hook CACHE_HOOK = nullptr; // derivative without code cache (S32K11x)
#endif

// SYS-44: every data flash command is bracketed for the ROM check (AreaService opens and closes per command)
void flashWriteBegin() { ::safety::romCheckFlashWrites.begin(); }

void flashWriteEnd() { ::safety::romCheckFlashWrites.end(); }

// NVM-26 (safety review NVM23-S1 b): a new job only in STARTUP and RUN - asked at every erase()/write() with the state
// the ECU state management published last (std::atomic; the same task as this system on the s32k1xx port)
bool admitJob() { return ::systems::dflash::admitsJobs(::ecustate::rte::publishedState()); }

#define DFLASH_AREA_ENTRY(name, offset, size, purpose)                                                    \
    ::dataflash::Area{                                                                                    \
        #name,                                                                                            \
        (offset),                                                                                         \
        (size),                                                                                           \
        ((purpose) == 0) ? ::dataflash::Purpose::CALIBRATION : ::dataflash::Purpose::DATA},
::dataflash::Area const areaTable[] = {NX_DFLASH_AREAS(DFLASH_AREA_ENTRY)};
#undef DFLASH_AREA_ENTRY
static_assert(sizeof(areaTable) / sizeof(areaTable[0]) == NX_DFLASH_AREA_COUNT, "generated area table");

// constant initialised (constexpr constructors): the run-down step is wired during static initialisation
::platform::FlexNvmDataFlash flexNvmDataFlash(CACHE_HOOK);
::systems::dflash::DataFlashGate dataFlashGate(flexNvmDataFlash);
::dataflash::AreaService areaService(
    dataFlashGate,
    areaTable,
    static_cast<uint32_t>(NX_DFLASH_AREA_COUNT),
    ::dataflash::WriteHooks{&flashWriteBegin, &flashWriteEnd},
    &admitJob);
::systems::dflash::DataFlashRunDownStep runDownStep(dataFlashGate);

::platform::FlexNvmDataFlash::Region const REGION{
    static_cast<uintptr_t>(NX_DFLASH_START),
    static_cast<uint32_t>(NX_DFLASH_SIZE),
    static_cast<uint32_t>(NX_DFLASH_SECTOR_SIZE),
    static_cast<uint8_t>(NX_DFLASH_PARTITION_CODE)};

#ifdef NX_DFLASH_COMMAND
enum Id : uint8_t
{
    ID_SHOW = 1U,
    ID_READ,
    ID_ERASE,
    ID_WRITE,
    ID_HOLD,
};

constexpr uint32_t READ_MAX  = 64U;  // bytes per "dflash read"
constexpr uint32_t WRITE_MAX = 256U; // bytes per "dflash write"
constexpr size_t AREA_NAME_MAX = 31U;
constexpr uint32_t HOLD_MAX_MS = 5000U; // "dflash hold": longest hold of the board check (NVM-25)

// data of the last "dflash write" - stays valid until its job has ended (refused while a job runs)
uint8_t writeBuffer[WRITE_MAX];

// NVM-25 "dflash hold": a read in place held over several cycles (owner task only), released in execute()
struct Hold
{
    ::etl::optional<::dataflash::DirectRead> scope;
    uint32_t area;
    uint32_t sumBefore;
    uint32_t startUs;
    uint32_t durationUs;
};

Hold hold{};

/** Sum of the bytes of area \p index, read in place through the address the held scope gives (0: not held). */
uint32_t inPlaceSum(::dataflash::DirectRead const& scope, uint32_t const index)
{
    ::dataflash::Area const& a = *areaService.area(index);
    uintptr_t const address    = areaService.areaAddress(scope, index, 0U, a.size);
    if (address == 0U)
    {
        return 0U;
    }
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast, performance-no-int-to-ptr): read in place, held
    uint8_t const volatile* const bytes = reinterpret_cast<uint8_t const volatile*>(address);
    uint32_t sum                        = 0U;
    for (uint32_t i = 0U; i < a.size; ++i)
    {
        sum += bytes[i];
    }
    return sum;
}

/** Ends a "dflash hold" whose time has passed (owner task, every cycle). */
void endHoldIfDue()
{
    if (!hold.scope.has_value())
    {
        return;
    }
    uint32_t const elapsed = getSystemTimeUs32Bit() - hold.startUs;
    if ((elapsed < hold.durationUs) && !dataFlashGate.locked()) // the run-down ends a hold at once
    {
        return;
    }
    uint32_t const sumAfter = inPlaceSum(*hold.scope, hold.area);
    hold.scope.reset(); // releases the block: waiting stores and commands go on
    ::util::logger::Logger::info(
        ::util::logger::BSP,
        "dflash hold of area %u: released after %u ms, sum 0x%x before, 0x%x after (%s)",
        static_cast<unsigned>(hold.area), // index as in "dflash show" (SYS-16: no unbounded %s in the log)
        static_cast<unsigned>(elapsed / 1000U),
        static_cast<unsigned>(hold.sumBefore),
        static_cast<unsigned>(sumAfter),
        (sumAfter == hold.sumBefore) ? "same" : "DIFFERENT");
}
#endif
} // namespace

namespace systems
{
using ::util::logger::BSP;
using ::util::logger::Logger;

::dataflash::AreaService& dataFlashAreas() { return areaService; }

::ecustate::IShutdownStep& dataFlashRunDownStep(::ecustate::IShutdownStep* const nvmStep)
{
    runDownStep.setInner(nvmStep);
    return runDownStep;
}

DataFlashSystem::DataFlashSystem(::async::ContextType const context)
: ::lifecycle::SingleContextLifecycleComponent(context)
, _context(context)
, _timeout()
#ifdef NX_DFLASH_COMMAND
, _command(*this)
// SYS-13: the command runs in the console context, the data flash work in this system's task
, _asyncCommand(_command, ::console::CONSOLE_CONTEXT)
#endif
{}

void DataFlashSystem::init()
{
    flexNvmDataFlash.setRegion(REGION);
    ::platform::FlexNvmDataFlash::State const state = flexNvmDataFlash.state();
    if (state == ::platform::FlexNvmDataFlash::State::WRONG_PARTITION)
    {
        Logger::warn(
            BSP,
            "data flash: chip DEPART 0x%x, project 0x%x - areas not usable",
            static_cast<unsigned>(::platform::FlexNvmDataFlash::departCode()),
            static_cast<unsigned>(NX_DFLASH_PARTITION_CODE));
    }
    else if (areaService.check() != ::dataflash::Status::OK)
    {
        Logger::warn(BSP, "data flash: area table does not fit the data flash (%u)",
                     static_cast<unsigned>(areaService.check()));
    }
    transitionDone();
}

void DataFlashSystem::run()
{
    dataFlashGate.unlock(); // a run-down given up by a wake-up restarts the lifecycle without an MCU reset
    ::async::scheduleAtFixedRate(_context, *this, _timeout, CYCLE_MS, ::async::TimeUnit::MILLISECONDS);
    transitionDone();
}

void DataFlashSystem::shutdown()
{
    dataFlashGate.lock(); // nothing new after the lifecycle went down (the run-down step locked it already)
    _timeout.cancel();
#ifdef NX_DFLASH_COMMAND
    hold.scope.reset(); // NVM-25: no hold of the test command outlives the lifecycle
#endif
    transitionDone();
}

void DataFlashSystem::execute()
{
#ifdef NX_DFLASH_COMMAND
    endHoldIfDue();
#endif
    (void)dflash::advance(areaService, dataFlashGate, STEPS_PER_CYCLE);
}

#ifdef NX_DFLASH_COMMAND
DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(DataFlashSystem::Command, "dflash", "data flash areas (NVM-23, test build)")
COMMAND_GROUP_COMMAND(ID_SHOW, "show", "chip, driver, areas, job")
COMMAND_GROUP_COMMAND(ID_READ, "read", "read through read() (param: <area> <offset> <length up to 64>)")
COMMAND_GROUP_COMMAND(ID_ERASE, "erase", "erase every sector of an area (param: <area>)")
COMMAND_GROUP_COMMAND(
    ID_WRITE, "write", "program bytes first, first+1, ... (param: <area> <offset> <length up to 256, n x 8> <first>)")
COMMAND_GROUP_COMMAND(
    ID_HOLD, "hold", "read an area in place under the block lock for a time (param: <area> <ms up to 5000>; NVM-25)")
DEFINE_COMMAND_GROUP_GET_INFO_END

namespace
{
/** Area index of the next token (by name); NO_AREA and a line if there is none. */
uint32_t scanArea(::util::command::CommandContext& context, ::util::format::SharedStringWriter& out)
{
    char name[AREA_NAME_MAX + 1U] = {};
    if (context.hasToken())
    {
        ::util::string::ConstString const token = context.scanToken();
        size_t const length = (token.length() < AREA_NAME_MAX) ? token.length() : AREA_NAME_MAX;
        for (size_t i = 0U; i < length; ++i)
        {
            name[i] = token.data()[i];
        }
    }
    uint32_t const index = areaService.find(name);
    if (index == ::dataflash::AreaService::NO_AREA)
    {
        (void)out.printf("dflash: no area '%s' (dflash show lists them)\r\n", name);
    }
    return index;
}
} // namespace

void DataFlashSystem::Command::executeCommand(::util::command::CommandContext& context, uint8_t const idx)
{
    // SYS-13: this runs in the console context (TASK_IDLE); every call of the area service and the driver runs in
    // the task of the system (inOwner), the output is printed here
    ::async::ContextType const owner = _system._context;
    auto const inOwner               = [owner](auto& work) { ::console::runInOwnerContext(owner, work); };
    ::util::format::SharedStringWriter out(context);
    switch (idx)
    {
        case ID_SHOW:
        {
            if (!context.checkEol())
            {
                break;
            }
            struct
            {
                ::platform::FlexNvmDataFlash::State state;
                uint8_t depart;
                ::dataflash::Geometry geometry;
                ::dataflash::Status check;
                bool busy;
                bool locked;
                ::dataflash::Status last;
                uint32_t commands;
                uint32_t refused;
                uint32_t longestErase;
                uint32_t longestProgram;
                uint32_t timeouts;
                uint32_t collisions;
                uint32_t bufferFlushes;
                uint32_t speculationNotCleared;
                uint32_t heldOff;
                uint32_t refusedJobs;
                uint8_t ecuState;
                uint8_t holds;
                uint8_t reads;
                uint32_t refusedHolds;
                uint32_t unbalanced;
                uint32_t storeWaits;
                uint32_t storeHoldWaits;
                uint32_t storeTimeouts;
            } s{};
            auto take = [&s]()
            {
                s.state          = flexNvmDataFlash.state();
                s.depart         = ::platform::FlexNvmDataFlash::departCode();
                s.geometry       = dataFlashGate.geometry();
                s.check          = areaService.check();
                s.busy           = areaService.busy();
                s.locked         = dataFlashGate.locked();
                s.last           = areaService.lastResult();
                s.commands       = areaService.commands();
                s.refused        = dataFlashGate.refused();
                s.longestErase   = flexNvmDataFlash.longestEraseUs();
                s.longestProgram = flexNvmDataFlash.longestProgramUs();
                s.timeouts       = flexNvmDataFlash.timeouts();
                s.collisions     = flexNvmDataFlash.collisions();
                s.bufferFlushes  = flexNvmDataFlash.bufferFlushes();
                s.speculationNotCleared = flexNvmDataFlash.speculationNotCleared();
                s.heldOff        = flexNvmDataFlash.heldOff();
                s.refusedJobs    = areaService.refusedJobs();
                s.ecuState       = static_cast<uint8_t>(::ecustate::rte::publishedState());
                s.holds          = ::platform::flexNvmBlockLock.holds();
                s.reads          = ::platform::flexNvmBlockLock.reads();
                s.refusedHolds   = ::platform::flexNvmBlockLock.refusedHolds();
                s.unbalanced     = ::platform::flexNvmBlockLock.unbalanced();
                s.storeWaits     = ::platform::flexNvmEepromInstance().commandWaits();
                s.storeHoldWaits = ::platform::flexNvmEepromInstance().holdWaits();
                s.storeTimeouts  = ::platform::flexNvmEepromInstance().timeouts();
            };
            inOwner(take);
            (void)out.printf(
                "dflash: %s, chip DEPART 0x%x, project 0x%x\r\n",
                ::platform::FlexNvmDataFlash::stateText(s.state),
                static_cast<unsigned>(s.depart),
                static_cast<unsigned>(NX_DFLASH_PARTITION_CODE));
            (void)out.printf(
                "usable 0x%08x, %u bytes, sector %u, program unit %u; areas %s\r\n",
                static_cast<unsigned>(NX_DFLASH_START),
                static_cast<unsigned>(s.geometry.size),
                static_cast<unsigned>(s.geometry.sectorSize),
                static_cast<unsigned>(s.geometry.programSize),
                dflash::statusText(s.check));
            for (uint32_t i = 0U; i < areaService.count(); ++i)
            {
                ::dataflash::Area const& a = *areaService.area(i);
                (void)out.printf(
                    "  %u %s: offset 0x%x, size 0x%x, %s\r\n",
                    static_cast<unsigned>(i),
                    a.name,
                    static_cast<unsigned>(a.offset),
                    static_cast<unsigned>(a.size),
                    (a.purpose == ::dataflash::Purpose::CALIBRATION) ? "calibration" : "data");
            }
            (void)out.printf(
                "job %s, last result %s, commands %u, gate %s, refused %u\r\n",
                s.busy ? "running" : "none",
                dflash::statusText(s.last),
                static_cast<unsigned>(s.commands),
                s.locked ? "locked (run-down)" : "open",
                static_cast<unsigned>(s.refused));
            // NVM-29: the end of a command is seen at the next job step - the times are upper bounds (step period)
            (void)out.printf(
                "longest erase <= %u us, longest program <= %u us (end seen at the %u ms job step), time-outs %u, "
                "read collisions %u\r\n",
                static_cast<unsigned>(s.longestErase),
                static_cast<unsigned>(s.longestProgram),
                static_cast<unsigned>(CYCLE_MS),
                static_cast<unsigned>(s.timeouts),
                static_cast<unsigned>(s.collisions));
            // NVM-29: FMC read buffers of the FlexNVM flushed after every command
            (void)out.printf(
                "read buffers: flushes %u, speculation not cleared %u\r\n",
                static_cast<unsigned>(s.bufferFlushes),
                static_cast<unsigned>(s.speculationNotCleared));
            // NVM-26: jobs refused from STANDBY on; NVM-25: the block lock and the EEPROM stores that waited for it
            (void)out.printf(
                "ecu state %s, new jobs %s, refused jobs %u\r\n",
                ::ecustate::stateName(static_cast<::ecustate::State>(s.ecuState)),
                dflash::admitsJobs(static_cast<::ecustate::State>(s.ecuState)) ? "accepted" : "refused (standby)",
                static_cast<unsigned>(s.refusedJobs));
            (void)out.printf(
                "block lock: holds %u, reads %u, refused holds %u, unbalanced %u, launches held off %u\r\n",
                static_cast<unsigned>(s.holds),
                static_cast<unsigned>(s.reads),
                static_cast<unsigned>(s.refusedHolds),
                static_cast<unsigned>(s.unbalanced),
                static_cast<unsigned>(s.heldOff));
            (void)out.printf(
                "EEPROM stores: waited %u (for a hold %u), time-outs %u\r\n",
                static_cast<unsigned>(s.storeWaits),
                static_cast<unsigned>(s.storeHoldWaits),
                static_cast<unsigned>(s.storeTimeouts));
            break;
        }
        case ID_READ:
        {
            uint32_t const index = scanArea(context, out);
            if (index == ::dataflash::AreaService::NO_AREA)
            {
                break;
            }
            uint32_t const offset = context.scanIntToken<uint32_t>();
            uint32_t const length = context.scanIntToken<uint32_t>();
            if (!context.check((length > 0U) && (length <= READ_MAX)) || !context.checkEol())
            {
                break;
            }
            uint8_t data[READ_MAX]       = {};
            ::dataflash::Status status = ::dataflash::Status::OK;
            auto read = [&status, &data, index, offset, length]()
            { status = areaService.read(index, offset, data, length); };
            inOwner(read);
            if (status != ::dataflash::Status::OK)
            {
                (void)out.printf("dflash read: %s\r\n", dflash::statusText(status));
                break;
            }
            for (uint32_t line = 0U; line < length; line += 16U)
            {
                (void)out.printf("0x%04x:", static_cast<unsigned>(offset + line));
                for (uint32_t i = line; (i < length) && (i < (line + 16U)); ++i)
                {
                    (void)out.printf(" %02x", static_cast<unsigned>(data[i]));
                }
                (void)out.printf("\r\n");
            }
            break;
        }
        case ID_ERASE:
        {
            uint32_t const index = scanArea(context, out);
            if ((index == ::dataflash::AreaService::NO_AREA) || !context.checkEol())
            {
                break;
            }
            ::dataflash::Status status = ::dataflash::Status::OK;
            auto erase = [&status, index]()
            {
                status = dataFlashGate.locked() ? ::dataflash::Status::NOT_AVAILABLE : areaService.erase(index);
            };
            inOwner(erase);
            (void)out.printf(
                "dflash erase %s: %s\r\n",
                areaService.area(index)->name,
                (status == ::dataflash::Status::OK) ? "started (result: dflash show)" : dflash::statusText(status));
            break;
        }
        case ID_WRITE:
        {
            uint32_t const index = scanArea(context, out);
            if (index == ::dataflash::AreaService::NO_AREA)
            {
                break;
            }
            uint32_t const offset = context.scanIntToken<uint32_t>();
            uint32_t const length = context.scanIntToken<uint32_t>();
            uint32_t const first  = context.scanIntToken<uint32_t>();
            if (!context.check((length > 0U) && (length <= WRITE_MAX) && (first <= 0xFFU)) || !context.checkEol())
            {
                break;
            }
            ::dataflash::Status status = ::dataflash::Status::OK;
            auto write = [&status, index, offset, length, first]()
            {
                if (dataFlashGate.locked())
                {
                    status = ::dataflash::Status::NOT_AVAILABLE;
                    return;
                }
                if (areaService.busy())
                {
                    status = ::dataflash::Status::BUSY; // the buffer may belong to the running job
                    return;
                }
                for (uint32_t i = 0U; i < length; ++i)
                {
                    writeBuffer[i] = static_cast<uint8_t>(first + i);
                }
                status = areaService.write(index, offset, writeBuffer, length);
            };
            inOwner(write);
            (void)out.printf(
                "dflash write %s: %s\r\n",
                areaService.area(index)->name,
                (status == ::dataflash::Status::OK) ? "started (result: dflash show)" : dflash::statusText(status));
            break;
        }
        case ID_HOLD:
        {
            uint32_t const index = scanArea(context, out);
            if (index == ::dataflash::AreaService::NO_AREA)
            {
                break;
            }
            uint32_t const ms = context.scanIntToken<uint32_t>();
            if (!context.check((ms > 0U) && (ms <= HOLD_MAX_MS)) || !context.checkEol())
            {
                break;
            }
            ::dataflash::Status status = ::dataflash::Status::OK;
            uint32_t sum               = 0U;
            auto take = [&status, &sum, index, ms]()
            {
                if (hold.scope.has_value())
                {
                    status = ::dataflash::Status::BUSY; // one hold at a time
                    return;
                }
                hold.scope.emplace(dataFlashGate);
                if (!hold.scope->held())
                {
                    hold.scope.reset();
                    status = ::dataflash::Status::BUSY; // a command or an EEPROM store runs
                    return;
                }
                hold.area       = index;
                hold.sumBefore  = inPlaceSum(*hold.scope, index);
                hold.startUs    = getSystemTimeUs32Bit();
                hold.durationUs = ms * 1000U;
                sum             = hold.sumBefore;
            };
            inOwner(take);
            if (status != ::dataflash::Status::OK)
            {
                (void)out.printf("dflash hold: %s\r\n", dflash::statusText(status));
                break;
            }
            (void)out.printf(
                "dflash hold %s: held for %u ms, sum 0x%x; EEPROM stores and data flash commands wait (result in "
                "the log)\r\n",
                areaService.area(index)->name,
                static_cast<unsigned>(ms),
                static_cast<unsigned>(sum));
            break;
        }
        default: break;
    }
}
#endif

} // namespace systems

#endif
