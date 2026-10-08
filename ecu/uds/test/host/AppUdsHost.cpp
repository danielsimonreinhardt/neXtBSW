// Host build of the application UDS stack (ecu/uds DiagStack with the OpenBSW uds/docan/transport
// libraries): simulated async context and time, CAN transceiver with frame queues, boot request and
// reset flags. Driven from Python (tools: udsoncan/can-isotp, see test_app_uds_host.py).

#include "uds/app/DeviceKeySecurity.h"
#include "uds/app/DiagStack.h"
#include "uds/app/IEcuControl.h"

#include <bl_config.h>

#ifdef APP_UDS_HOST_DEM
// NVM-04: the fault memory with the tables of the test description (build_app_uds_host.py generate_dem)
#include <RteDem.h>
#include <dem/Dem.h>
#endif

#include <async/Async.h>
#include <busid/BusId.h>
#include <can/canframes/CANFrame.h>
#include <can/filter/BitFieldFilter.h>
#include <can/framemgmt/ICANFrameListener.h>
#include <can/transceiver/AbstractCANTransceiver.h>
#include <etl/error_handler.h>
#include <etl/optional.h>
#include <time/TimestampProvider.h>
#include <uds/udsBusConfiguration.h>

#include <cstring>
#include <deque>
#include <vector>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

namespace
{
uint64_t nowUs = 0U;

std::deque< ::async::RunnableType*> readyQueue;
std::vector< ::async::TimeoutType*> timers;

struct Frame
{
    uint32_t id;
    uint8_t len;
    uint8_t data[8];
};
} // namespace

// ---------------------------------------------------------------------------------------------
// async (single context, event driven simulated time)
namespace async
{
TimeoutType::TimeoutType()
: _runnable(nullptr)
, _context(CONTEXT_INVALID)
, _dueUs(0U)
, _periodUs(0U)
, _active(false)
, _nextTimeout(nullptr)
{}

TimeoutType::~TimeoutType() { cancel(); }

void TimeoutType::cancel()
{
    _active = false;
    for (auto it = timers.begin(); it != timers.end(); ++it)
    {
        if (*it == this)
        {
            timers.erase(it);
            break;
        }
    }
}

void execute(ContextType /* context */, RunnableType& runnable)
{
    if (!runnable.isEnqueued())
    {
        runnable.enqueue();
        readyQueue.push_back(&runnable);
    }
}

static void startTimer(RunnableType& runnable, TimeoutType& timeout, uint32_t delayUs, uint32_t period)
{
    if (timeout._active)
    {
        return;
    }
    timeout._runnable = &runnable;
    timeout._context  = 0U;
    timeout._dueUs    = nowUs + delayUs;
    timeout._periodUs = period;
    timeout._active   = true;
    timers.push_back(&timeout);
}

void schedule(
    ContextType /* context */,
    RunnableType& runnable,
    TimeoutType& timeout,
    uint32_t const delay,
    TimeUnitType const unit)
{
    startTimer(runnable, timeout, delay * static_cast<uint32_t>(unit), 0U);
}

void scheduleAtFixedRate(
    ContextType /* context */,
    RunnableType& runnable,
    TimeoutType& timeout,
    uint32_t const period,
    TimeUnitType const unit)
{
    uint32_t const us = period * static_cast<uint32_t>(unit);
    startTimer(runnable, timeout, us, us);
}
} // namespace async

namespace bsw
{
namespace time
{
uint32_t TimestampProvider::getTimestampUs32Bit() { return static_cast<uint32_t>(nowUs); }

uint64_t TimestampProvider::getTimestampUs64Bit() { return nowUs; }

uint32_t TimestampProvider::getTimestampMs32Bit() { return static_cast<uint32_t>(nowUs / 1000U); }
} // namespace time
} // namespace bsw

extern "C" uint32_t getSystemTimeUs32Bit() { return static_cast<uint32_t>(nowUs); }

namespace
{
// ETL_USE_ASSERT_FUNCTION (etl_profile.h): count asserts instead of stopping
uint32_t assertCount = 0U;

void onEtlAssert(::etl::exception const& /* e */) { ++assertCount; }

void runReady()
{
    while (!readyQueue.empty())
    {
        ::async::RunnableType* const runnable = readyQueue.front();
        readyQueue.pop_front();
        (void)runnable->dequeue();
        runnable->execute();
    }
}

/** Advances the simulated time by \p us, running every runnable and timer on the way. */
void advance(uint64_t const us)
{
    uint64_t const target = nowUs + us;
    runReady();
    while (true)
    {
        ::async::TimeoutType* next = nullptr;
        for (auto* t : timers)
        {
            if ((t->_dueUs <= target) && ((next == nullptr) || (t->_dueUs < next->_dueUs)))
            {
                next = t;
            }
        }
        if (next == nullptr)
        {
            break;
        }
        nowUs = (next->_dueUs > nowUs) ? next->_dueUs : nowUs;
        if (next->_periodUs != 0U)
        {
            next->_dueUs += next->_periodUs;
        }
        else
        {
            next->cancel();
        }
        next->_runnable->execute();
        runReady();
    }
    nowUs = target;
}

// ---------------------------------------------------------------------------------------------
// CAN transceiver: like CanFlex2Transceiver, listener filters are applied on reception and the
// transmit confirmation arrives asynchronously in the transceiver context.
class HostCanTransceiver final
: public ::can::AbstractCANTransceiver
, private ::async::RunnableType
{
public:
    // bus of the diagnostic stack of this profile (test/host/include/busid, uds/udsBusConfiguration.h)
    HostCanTransceiver() : AbstractCANTransceiver(::busid::NX_UDS_BUS) { _state = State::OPEN; }

    ErrorCode init() override { return ErrorCode::CAN_ERR_OK; }

    void shutdown() override {}

    ErrorCode open(::can::CANFrame const& /* frame */) override { return ErrorCode::CAN_ERR_OK; }

    ErrorCode open() override { return ErrorCode::CAN_ERR_OK; }

    ErrorCode close() override { return ErrorCode::CAN_ERR_OK; }

    ErrorCode mute() override { return ErrorCode::CAN_ERR_OK; }

    ErrorCode unmute() override { return ErrorCode::CAN_ERR_OK; }

    uint32_t getBaudrate() const override { return 500000U; }

    uint16_t getHwQueueTimeout() const override { return 10U; }

    ErrorCode write(::can::CANFrame const& frame) override { return queue(frame, nullptr); }

    ErrorCode write(::can::CANFrame const& frame, ::can::ICANFrameSentListener& listener) override
    {
        return queue(frame, &listener);
    }

    void receive(Frame const& f)
    {
        ::can::CANFrame frame(f.id, f.data, f.len);
        notifyListeners(frame);
    }

    bool popTx(Frame& f)
    {
        if (_tx.empty())
        {
            return false;
        }
        f = _tx.front();
        _tx.pop_front();
        return true;
    }

    void reset()
    {
        _tx.clear();
        _pendingListener = nullptr;
    }

    bool failNextWrite = false;

private:
    ErrorCode queue(::can::CANFrame const& frame, ::can::ICANFrameSentListener* listener)
    {
        if (failNextWrite)
        {
            failNextWrite = false;
            return ErrorCode::CAN_ERR_TX_FAIL;
        }
        if ((listener != nullptr) && (_pendingListener != nullptr))
        {
            return ErrorCode::CAN_ERR_TX_HW_QUEUE_FULL;
        }
        Frame f{frame.getId(), frame.getPayloadLength(), {0U}};
        ::memcpy(f.data, frame.getPayload(), f.len);
        _tx.push_back(f);
        if (listener != nullptr)
        {
            _pendingListener = listener;
            _pendingFrame    = frame;
            ::async::execute(0U, *this);
        }
        return ErrorCode::CAN_ERR_OK;
    }

    void execute() override
    {
        ::can::ICANFrameSentListener* const listener = _pendingListener;
        _pendingListener                             = nullptr;
        if (listener != nullptr)
        {
            listener->canFrameSent(_pendingFrame);
        }
    }

    std::deque<Frame> _tx;
    ::can::ICANFrameSentListener* _pendingListener = nullptr;
    ::can::CANFrame _pendingFrame;
};

class HostEcuControl : public ::uds::app::IEcuControl
{
public:
    void enterBootloader() override { bootRequest = true; }

    void reset() override { resetRequested = true; }

    // BL-21: preprogramming step of ISO 14229-1 Table 501 (services 0x28 and 0x85)
    void setApplicationMessages(bool const tx, bool const rx) override
    {
        applicationTx = tx;
        applicationRx = rx;
        ++communicationChanges;
    }

    void setDtcUpdatesEnabled(bool const enabled) override
    {
        dtcUpdates = enabled;
        ++dtcChanges;
        // NVM-04: as DiagSystem does it, the fault memory takes the state of 85 (none in the plain host build)
        if (::dem::registry.memory != nullptr)
        {
            ::dem::registry.memory->setDtcSetting(enabled);
        }
    }

    bool bootRequest    = false;
    bool resetRequested = false;
    bool applicationTx  = true;
    bool applicationRx  = true;
    bool dtcUpdates     = true;
    // how often the state really changed: a repeated request must not notify the ECU again
    uint32_t communicationChanges = 0U;
    uint32_t dtcChanges           = 0U;
};

/**
 * Seed and key of the host build only (COM-28): seed bytes from a counter, key byte i = seed byte (i mod seed
 * length) XOR 0xA5 XOR level. test_app_uds_host.py computes the same key. Not a product algorithm: it keeps the
 * service tests independent of the key record; the product algorithm DeviceKeySecurity is tested on its own
 * (COM-30, security mode 2).
 */
class HostSecurityAlgorithm final : public ::uds::app::ISecurityKeyAlgorithm
{
public:
    bool makeSeed(::uds::app::SecurityLevel const& level, ::etl::span<uint8_t> const seed) override
    {
        ++_counter;
        for (size_t i = 0U; i < seed.size(); ++i)
        {
            seed[i] = static_cast<uint8_t>((0x11U * (i + 1U)) + _counter + level.requestSeed);
        }
        seed[0] = (seed[0] == 0U) ? 1U : seed[0];
        return true;
    }

    bool checkKey(
        ::uds::app::SecurityLevel const& level,
        ::etl::span<uint8_t const> const seed,
        ::etl::span<uint8_t const> const key) override
    {
        for (size_t i = 0U; i < key.size(); ++i)
        {
            if (key[i] != static_cast<uint8_t>(seed[i % seed.size()] ^ 0xA5U ^ level.requestSeed))
            {
                return false;
            }
        }
        return true;
    }

private:
    uint8_t _counter = 0U;
};

HostSecurityAlgorithm securityAlgorithm;
// 0: no algorithm, 1: HostSecurityAlgorithm, 2: DeviceKeySecurity with the simulated key sector (COM-30)
int securityMode = 1;
// COM-30: simulated key record at the start of the key sector (erased: not provisioned) and chip UID
uint8_t keyRecord[BL_KEY_RECORD_LEN];
uint8_t chipUid[BL_UID_LEN];
::etl::optional< ::uds::app::DeviceKeySecurity> deviceKeySecurity;

struct HostSecurityInit
{
    HostSecurityInit()
    {
        ::memset(keyRecord, 0xFF, sizeof(keyRecord));
        for (size_t i = 0U; i < sizeof(chipUid); ++i)
        {
            chipUid[i] = static_cast<uint8_t>(0xC0U + i);
        }
    }
} hostSecurityInit;

::uds::app::ISecurityKeyAlgorithm* securityAlgorithmOfMode()
{
    if (securityMode == 1)
    {
        return &securityAlgorithm;
    }
    if (securityMode == 2)
    {
        return &deviceKeySecurity.emplace(
            ::etl::span<uint8_t const>(keyRecord), ::etl::span<uint8_t const>(chipUid));
    }
    return nullptr;
}

char const APPLICATION_SOFTWARE_ID[] = "nextbsw-app host";

// COM-44: identification as DiagSystem hands it over - the hardware number of the generated memory layout, a
// neXtBSW version text the test may change, the validity record of the application (erased until the test writes one)
char const HARDWARE_NUMBER[] = BL_HARDWARE_ID;
char nextbswVersion[80]      = "0.0.0 host";
bool nextbswVersionKnown     = true;
uint8_t applicationRecord[256];
size_t applicationRecordSize = 0U;
// PLAT-79: configuration identity (64 hex digits in the image, NX_IDENTITY_CONFIGURATION), the test may change it
char configurationIdentity[96]  = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
bool configurationIdentityKnown = true;
char workspaceVersion[64]       = "-";
bool workspaceVersionKnown      = true;

struct HostIdentificationInit
{
    HostIdentificationInit()
    {
        ::memset(applicationRecord, 0xFF, sizeof(applicationRecord));
        applicationRecordSize = ::uds::app::applicationRecordBytes();
    }
} hostIdentificationInit;

HostCanTransceiver* transceiver = nullptr;
HostEcuControl ecuControl;

// NVM-04: groups of DTCs service 14 clears (Annex D.1), set by the test before the power on
constexpr size_t MAX_DTC_GROUPS = 8U;
::uds::app::DtcGroup dtcGroups[MAX_DTC_GROUPS];
size_t dtcGroupCount = 0U;

#ifdef APP_UDS_HOST_DEM
// The host simulation runs in one thread: the lock of the fault memory has nothing to protect.
struct HostDemLock
{
    HostDemLock() {}
};

namespace demtables = ::rte::demtables;

::dem::DtcState demStates[demtables::CONFIG.dtcCount];
::dem::Entry demEntries[demtables::CONFIG.maxEntries];
uint8_t demData[static_cast<size_t>(demtables::CONFIG.maxEntries) * demtables::CONFIG.entryDataBytes];
uint8_t demScratch[demtables::CONFIG.entryDataBytes];
::etl::optional< ::dem::Manager<HostDemLock>> faultMemory;

/** Every event of the test description is reported (app_dem_report); conditions hold, cycles run with the ECU. */
class HostDemSource final : public ::dem::ISource
{
public:
    ::dem::Result sample(uint16_t /* dtc */) override { return ::dem::Result::NONE; }

    bool condition(uint8_t /* condition */) override { return true; }

    uint8_t cycleState(uint8_t /* cycle */) override { return CYCLE_AUTOMATIC; }
} demSource;

/** A fresh fault memory as after the first start with an empty NvM, registered like DemRte.h does it. */
void startFaultMemory()
{
    auto& memory = faultMemory.emplace(demtables::CONFIG, demStates, demEntries, demData, demScratch);
    memory.start();
    ::dem::registry.memory = &memory;
}
#endif
::etl::optional< ::uds::app::DiagStack> stack;
// same range as the target scans (BOOTLOADER_SCAN_START .. BOOTLOADER_SCAN_END)
uint8_t bootloaderFlash[::uds::app::BOOTLOADER_SCAN_END - ::uds::app::BOOTLOADER_SCAN_START];
bool shutdownDone = false;

void onShutdownDone() { shutdownDone = true; }

bool halted() { return ecuControl.bootRequest || ecuControl.resetRequested; }

} // namespace

extern "C"
{
EXPORT void app_power_on_area(uint8_t const* area, uint32_t size);

/** Starts the application stack; \p blVersion is placed in the simulated bootloader area. */
EXPORT void app_power_on(char const* const blVersion)
{
    uint8_t area[sizeof(bootloaderFlash)];
    ::memset(area, 0xFF, sizeof(area));
    if (blVersion != nullptr)
    {
        ::memcpy(&area[40], blVersion, ::strlen(blVersion) + 1U); // with NUL, like a string literal
    }
    app_power_on_area(area, sizeof(area));
}

/** Starts the application stack with the bootloader flash from BOOTLOADER_SCAN_START. */
EXPORT void app_power_on_area(uint8_t const* const area, uint32_t const size)
{
    stack.reset();
    readyQueue.clear();
    timers.clear();
    ::etl::set_assert_function(&onEtlAssert);
    assertCount = 0U;
    // fresh transceiver: the old stack's listeners are still linked into the previous one
    static ::etl::optional<HostCanTransceiver> trx;
    trx.reset();
    transceiver = &trx.emplace();
    ecuControl   = HostEcuControl();
    shutdownDone = false;
    ::memset(bootloaderFlash, 0xFF, sizeof(bootloaderFlash));
    ::memcpy(bootloaderFlash, area, (size < sizeof(bootloaderFlash)) ? size : sizeof(bootloaderFlash));
    auto& s = stack.emplace(
        *transceiver,
        0U,
        ecuControl,
        APPLICATION_SOFTWARE_ID,
        securityAlgorithmOfMode(),
        ::etl::span<uint8_t const>(chipUid),
        ::etl::span< ::uds::app::DtcGroup const>(dtcGroups, dtcGroupCount),
        ::uds::app::DiagIdentification{
            HARDWARE_NUMBER,
            nextbswVersionKnown ? nextbswVersion : nullptr,
            ::etl::span<uint8_t const>(applicationRecord, applicationRecordSize),
            configurationIdentityKnown ? configurationIdentity : nullptr,
            workspaceVersionKnown ? workspaceVersion : nullptr});
    s.init(::etl::span<uint8_t const>(bootloaderFlash));
#ifdef APP_UDS_HOST_DEM
    startFaultMemory();
#endif
    // 10 ms cycle like DiagSystem
    static struct Cyclic : ::async::RunnableType
    {
        void execute() override { stack->cyclicTask(); }
    } cyclic;
    static ::async::TimeoutType cyclicTimeout;
    cyclicTimeout.cancel();
    ::async::scheduleAtFixedRate(0U, cyclic, cyclicTimeout, 10U, ::async::TimeUnit::MILLISECONDS);
}

EXPORT int app_can_rx(uint32_t const id, uint8_t const* const data, uint8_t const len)
{
    if (halted())
    {
        return 0; // MCU in reset: frame not acknowledged
    }
    Frame f{id, static_cast<uint8_t>((len > 8U) ? 8U : len), {0U}};
    ::memcpy(f.data, data, f.len);
    transceiver->receive(f);
    return 1;
}

EXPORT int app_can_tx(uint32_t* const id, uint8_t* const data, uint8_t* const len)
{
    Frame f;
    if (!transceiver->popTx(f))
    {
        return 0;
    }
    *id  = f.id;
    *len = f.len;
    ::memcpy(data, f.data, f.len);
    return 1;
}

EXPORT void app_poll_us(uint32_t const advanceUs)
{
    if (!halted())
    {
        advance(advanceUs);
    }
    else
    {
        nowUs += advanceUs;
    }
}

EXPORT uint32_t app_millis(void) { return static_cast<uint32_t>(nowUs / 1000U); }

EXPORT int app_boot_request(void) { return ecuControl.bootRequest ? 1 : 0; }

EXPORT int app_reset_requested(void) { return ecuControl.resetRequested ? 1 : 0; }

EXPORT uint8_t app_session(void) { return stack->getActiveSession().getSessionByte(); }

// BL-21: what CommunicationControl (0x28) and ControlDTCSetting (0x85) asked the ECU to switch
EXPORT int app_messages_tx(void) { return ecuControl.applicationTx ? 1 : 0; }

EXPORT int app_messages_rx(void) { return ecuControl.applicationRx ? 1 : 0; }

EXPORT int app_dtc_updates(void) { return ecuControl.dtcUpdates ? 1 : 0; }

EXPORT uint32_t app_communication_changes(void) { return ecuControl.communicationChanges; }

EXPORT uint32_t app_dtc_changes(void) { return ecuControl.dtcChanges; }

EXPORT uint8_t const* app_ram_test_data(void) { return stack->getRamTestData().data(); }

// COM-28: security level and routines of the diagnostic description
/** Key algorithm of the next power on: 0 none (27 requestSeed answers NRC 0x22), 1 the host test algorithm,
 * 2 DeviceKeySecurity with the simulated key record (COM-30). */
EXPORT void app_use_security_algorithm(int const mode) { securityMode = mode; }

/** COM-30: content of the simulated key sector (at most BL_KEY_RECORD_LEN bytes, the rest erased) and chip UID;
 * both are read by the next power on (the key record also at every seed and key). */
EXPORT void app_set_key_record(uint8_t const* const data, uint32_t const size)
{
    ::memset(keyRecord, 0xFF, sizeof(keyRecord));
    ::memcpy(keyRecord, data, (size < sizeof(keyRecord)) ? size : sizeof(keyRecord));
}

EXPORT void app_set_uid(uint8_t const* const data, uint32_t const size)
{
    ::memcpy(chipUid, data, (size < sizeof(chipUid)) ? size : sizeof(chipUid));
}

EXPORT uint32_t app_uid_length(void) { return static_cast<uint32_t>(sizeof(chipUid)); }

// ---- COM-44: identification of the application, read by the next power on

/** neXtBSW version text (nextbsw.version); nullptr: not known (NRC 0x22). */
EXPORT void app_set_nextbsw_version(char const* const text)
{
    nextbswVersionKnown = (text != nullptr);
    if (text != nullptr)
    {
        ::strncpy(nextbswVersion, text, sizeof(nextbswVersion) - 1U);
        nextbswVersion[sizeof(nextbswVersion) - 1U] = '\0';
    }
}

/** PLAT-79: configuration identity (configuration.identity); nullptr: not known (NRC 0x22). */
EXPORT void app_set_configuration_identity(char const* const text)
{
    configurationIdentityKnown = (text != nullptr);
    if (text != nullptr)
    {
        ::strncpy(configurationIdentity, text, sizeof(configurationIdentity) - 1U);
        configurationIdentity[sizeof(configurationIdentity) - 1U] = '\0';
    }
}

/** PLAT-93: commit of the customer workspace (workspace.version); nullptr: not known (NRC 0x22). */
EXPORT void app_set_workspace_version(char const* const text)
{
    workspaceVersionKnown = (text != nullptr);
    if (text != nullptr)
    {
        ::strncpy(workspaceVersion, text, sizeof(workspaceVersion) - 1U);
        workspaceVersion[sizeof(workspaceVersion) - 1U] = '\0';
    }
}

/** Validity record of the application at BL_APP_RECORD_ADDR (the rest erased); \p size 0: not readable at all. */
EXPORT void app_set_application_record(uint8_t const* const data, uint32_t const size)
{
    ::memset(applicationRecord, 0xFF, sizeof(applicationRecord));
    size_t const used = (size < ::uds::app::applicationRecordBytes()) ? size : ::uds::app::applicationRecordBytes();
    if (data != nullptr)
    {
        ::memcpy(applicationRecord, data, used);
    }
    applicationRecordSize = (size == 0U) ? 0U : ::uds::app::applicationRecordBytes();
}

/** Bytes of the validity record the compatibility reads (sizeof app_record_t). */
EXPORT uint32_t app_application_record_bytes(void)
{
    return static_cast<uint32_t>(::uds::app::applicationRecordBytes());
}

/** State of the key record as DeviceKeySecurity sees it (0 missing, 1 provisioned, 2 invalid), -1 without it. */
EXPORT int app_key_state(void)
{
    return deviceKeySecurity.has_value() ? static_cast<int>(deviceKeySecurity->keyState()) : -1;
}

EXPORT int app_security_unlocked(uint32_t const index)
{
    return stack->getAccessState().isUnlocked(static_cast<uint8_t>(index)) ? 1 : 0;
}

EXPORT int app_routine_running(uint32_t const index)
{
    return stack->getRoutineControl().isRunning(index) ? 1 : 0;
}

EXPORT uint32_t app_router_locked(void) { return stack->getRouter().getLockedCount(); }

EXPORT uint32_t app_router_failures(void) { return stack->getRouter().getAllocationFailures(); }

EXPORT uint32_t app_assert_count(void) { return assertCount; }

/** NVM-03: an identifier as the fault memory reads it for a snapshot (dem::registry.didReader, set by init()):
 * 1 read, 0 refused, -1 no reader registered. */
EXPORT int app_snapshot_did(uint32_t const did, uint8_t* const data, uint32_t const bytes)
{
    ::dem::IDidReader* const reader = ::dem::registry.didReader;
    if (reader == nullptr)
    {
        return -1;
    }
    return reader->read(static_cast<uint16_t>(did), data, static_cast<uint16_t>(bytes)) ? 1 : 0;
}

// ---- NVM-04: fault memory of the test description (only with APP_UDS_HOST_DEM, see build_app_uds_host.py)

/** Groups of DTCs of the next power on: \p data holds \p count triples group, first, last. */
EXPORT void app_set_dtc_groups(uint32_t const* const data, uint32_t const count)
{
    dtcGroupCount = (count < MAX_DTC_GROUPS) ? count : MAX_DTC_GROUPS;
    for (size_t i = 0U; i < dtcGroupCount; ++i)
    {
        dtcGroups[i] = ::uds::app::DtcGroup{data[3U * i], data[(3U * i) + 1U], data[(3U * i) + 2U]};
    }
}

/** 1 if the DLL has a fault memory. */
EXPORT int app_dem_present(void)
{
#ifdef APP_UDS_HOST_DEM
    return 1;
#else
    return 0;
#endif
}

#ifdef APP_UDS_HOST_DEM
/** A BSW monitor reports \p result (dem::Result) for the DTC with \p number; -1: no such DTC. */
EXPORT int app_dem_report(uint32_t const number, uint8_t const result)
{
    int32_t const dtc = faultMemory->findDtc(number);
    if (dtc >= 0)
    {
        faultMemory->report(static_cast<uint16_t>(dtc), static_cast< ::dem::Result>(result));
    }
    return (dtc >= 0) ? 0 : -1;
}

/** \p cycles cycles of the fault memory (the product runs one every fault_memory.cycle_ms). */
EXPORT void app_dem_main(uint32_t const cycles)
{
    for (uint32_t i = 0U; i < cycles; ++i)
    {
        faultMemory->mainFunction(demSource);
    }
}

/** Ends the running operation cycle \p cycle and starts it again (a power cycle of the fault memory). */
EXPORT void app_dem_restart_cycle(uint8_t const cycle) { faultMemory->setCycle(cycle, true); }

/** Status byte of the DTC with \p number, -1 if there is none. */
EXPORT int app_dem_status(uint32_t const number)
{
    int32_t const dtc = faultMemory->findDtc(number);
    ::dem::DtcInfo info{};
    return ((dtc >= 0) && faultMemory->dtcInfo(static_cast<uint16_t>(dtc), info)) ? info.status : -1;
}

/** Fault detection counter of the DTC with \p number. */
EXPORT int app_dem_fdc(uint32_t const number)
{
    ::dem::DtcInfo info{};
    (void)faultMemory->dtcInfo(static_cast<uint16_t>(faultMemory->findDtc(number)), info);
    return info.fdc;
}

/** 1 if the DTC with \p number has an entry (snapshots and extended data identifiers stored). */
EXPORT int app_dem_stored(uint32_t const number)
{
    ::dem::DtcInfo info{};
    (void)faultMemory->dtcInfo(static_cast<uint16_t>(faultMemory->findDtc(number)), info);
    return info.stored ? 1 : 0;
}

/** Takes the fault memory out of dem::registry (not started yet) or puts it back. */
EXPORT void app_dem_registered(int const registered)
{
    ::dem::registry.memory = (registered != 0) ? &*faultMemory : nullptr;
}
#endif

// ARCH-07: bus of the diagnostic stack and of its CAN transceiver, must be the configured one (uds.bus)
EXPORT uint8_t app_uds_bus_id(void) { return stack->getBusId(); }

EXPORT uint8_t app_transceiver_bus_id(void) { return transceiver->getBusId(); }

EXPORT void app_fail_next_write(void) { transceiver->failNextWrite = true; }

EXPORT int app_shutdown(void)
{
    stack->shutdown(::etl::delegate<void()>::create<&onShutdownDone>());
    advance(50000U);
    return shutdownDone ? 1 : 0;
}
}
