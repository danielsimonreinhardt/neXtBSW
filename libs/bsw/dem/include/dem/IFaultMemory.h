// NVM-03: interfaces of the fault memory towards its users.
//
// IFaultMemory  what the diagnostic services read and do (19 ReadDTCInformation, 14 ClearDiagnosticInformation,
//               85 ControlDTCSetting - the services themselves are NVM-04), the console and BSW monitors that report
//               an event (safe supervisor, CAN bus-off, supply: only reporting, the reaction stays with them).
// ISource       where the sampled events, the enable conditions and the application driven operation cycles come
//               from: the generated RTE (rte::dem*, tools/rtegen) in the product, a table in host tests.
// IDidReader    reads the data identifiers of snapshots and extended data when an entry is stored: the diagnostic
//               description of ecu/uds (the same read functions as service 22).
//
// Registry: the integration registers the one fault memory of the ECU and the diagnostic stack its reader. A user
// that starts before the fault memory finds nullptr and asks again later; nothing is created here.

#pragma once

#include "dem/DemTypes.h"

#include <cstddef>
#include <cstdint>

namespace dem
{

/** What one DTC looks like to a reader (19 02/04/06/14, console). */
struct DtcInfo
{
    uint32_t number;
    uint8_t status;   // masked with status::AVAILABILITY_MASK
    uint8_t severity;
    int8_t fdc;       // fault detection counter (D.6); 19 14 reports 0 < fdc < 127 (prefailed)
    uint8_t occurrence;
    uint8_t aging;
    uint8_t failedCycles;
    uint8_t cyclesSinceFirstFailed;
    bool stored;      // an entry holds its snapshot and extended data identifiers
};

/** Counters of the memory as a whole (console, tests). */
struct MemoryInfo
{
    uint8_t entriesUsed;
    uint8_t maxEntries;
    uint32_t overflows;     // DTCs that could not be stored (memory full, nothing displaceable)
    uint32_t displaced;     // entries displaced by a more important or newer DTC
    uint32_t conditions;    // enable conditions currently fulfilled (bit i)
    uint8_t cyclesRunning;  // operation cycles currently running (bit c)
    bool dtcSetting;        // false: ControlDTCSetting off, no status bit changes except by clearing
    bool loaded;            // the persistent state was valid (false: started cleared)
};

class IFaultMemory
{
public:
    // ---- read (any context; every call copies under the lock of the memory)
    virtual uint16_t dtcCount() const                         = 0;
    virtual DtcConfig const& dtcConfig(uint16_t dtc) const   = 0;
    virtual bool dtcInfo(uint16_t dtc, DtcInfo& info) const   = 0;
    /** Index of the DTC with \p number, -1 if the memory has none. */
    virtual int32_t findDtc(uint32_t number) const            = 0;
    /** DTCs whose status has at least one bit of \p mask (19 01). */
    virtual uint16_t countByStatusMask(uint8_t mask) const    = 0;
    virtual MemoryInfo memoryInfo() const                     = 0;
    /**
     * Data of snapshot record \p record of \p dtc as stored: the data of its identifiers back to back, in the
     * order of SnapshotRecord::items. -1: the DTC has no such record, 0: nothing stored (no entry), else bytes
     * copied (at most \p max).
     */
    virtual int32_t readSnapshot(uint16_t dtc, uint8_t record, uint8_t* out, size_t max) const = 0;
    /** Extended data record \p record of \p dtc: counters now, identifiers as stored. -1: no such record. */
    virtual int32_t readExtended(uint16_t dtc, uint8_t record, uint8_t* out, size_t max) const = 0;

    // ---- act (any context)
    /** Clears \p group: GROUP_ALL_DTCS or one DTC number (14). false: no DTC with that number. */
    virtual bool clear(uint32_t group)                        = 0;
    /** ControlDTCSetting (85): off stops every status change except clearing (D.2.1). */
    virtual void setDtcSetting(bool enabled)                  = 0;

    // ---- BSW event interface: only reporting; a safety reaction stays with the monitor (safe supervisor)
    /** Index of the DTC whose event the BSW reports under \p name (diag.yaml event.bsw), -1: none. */
    virtual int32_t findReportedEvent(char const* name) const = 0;
    /** Any context, interrupts too: one result of a BSW monitor, taken over in the next cycle. */
    virtual void report(uint16_t dtc, Result result)          = 0;

protected:
    ~IFaultMemory() = default;
};

class ISource
{
public:
    static constexpr uint8_t CYCLE_AUTOMATIC = 0xFFU;

    /** One sample of the event of \p dtc (not called for events the BSW reports). */
    virtual Result sample(uint16_t dtc)          = 0;
    /** Enable condition \p condition fulfilled now. */
    virtual bool condition(uint8_t condition)    = 0;
    /** Operation cycle \p cycle: 1 running, 0 stopped, CYCLE_AUTOMATIC (autostart) not controlled by the source. */
    virtual uint8_t cycleState(uint8_t cycle)    = 0;

protected:
    ~ISource() = default;
};

class IDidReader
{
public:
    /** Data record of identifier \p did, exactly \p bytes long. false: not available now (stored as 0xFF). */
    virtual bool read(uint16_t did, uint8_t* data, uint16_t bytes) = 0;

protected:
    ~IDidReader() = default;
};

/** The one fault memory of the ECU and the reader of the diagnostic stack; both nullptr until registered. */
struct Registry
{
    IFaultMemory* memory;
    IDidReader* didReader;
};

inline Registry registry{nullptr, nullptr};

} // namespace dem
