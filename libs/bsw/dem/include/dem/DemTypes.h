// NVM-03: types of the fault memory (simplified AUTOSAR DEM, decision of the user 2026-09-21: no warning lamp, no
// OBD). Configuration structures are generated from the diagnostic description of the project (diag.yaml,
// ARCH-51) by tools/rtegen; nothing here knows a DTC, a board or a derivative.
//
// Terms and numbers follow ISO 14229-1 (2026), Annex D: status bits (D.2.2), operation cycle and confirmation
// (D.2.1), fault detection counter (D.6), aging (D.7). Only clause numbers are cited, the wording is ours (the
// standard is licensed and lies in doc/confidential, never checked in).

#pragma once

#include <cstddef>
#include <cstdint>

namespace dem
{

/** Result of one monitor sample (the values of AUTOSAR Dem_EventStatusType, plus NONE). */
enum class Result : uint8_t
{
    PASSED    = 0U,   // test passed: the debouncing jumps to its passed end at once
    FAILED    = 1U,   // test failed: the debouncing jumps to its failed end at once
    PREPASSED = 2U,   // one passed sample, debounced
    PREFAILED = 3U,   // one failed sample, debounced
    NONE      = 0xFFU // no result this time (source not valid yet): nothing changes
};

/** The eight bits of the DTC status byte (ISO 14229-1, D.2.2). */
namespace status
{
constexpr uint8_t TEST_FAILED                           = 0x01U; // bit 0
constexpr uint8_t TEST_FAILED_THIS_OPERATION_CYCLE      = 0x02U; // bit 1
constexpr uint8_t PENDING_DTC                           = 0x04U; // bit 2
constexpr uint8_t CONFIRMED_DTC                         = 0x08U; // bit 3
constexpr uint8_t TEST_NOT_COMPLETED_SINCE_LAST_CLEAR   = 0x10U; // bit 4
constexpr uint8_t TEST_FAILED_SINCE_LAST_CLEAR          = 0x20U; // bit 5
constexpr uint8_t TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE = 0x40U; // bit 6
constexpr uint8_t WARNING_INDICATOR_REQUESTED           = 0x80U; // bit 7: no warning lamp, never set here

/** Status after a clear and before the first test: bits 4 and 6 (D.2.2). */
constexpr uint8_t INITIAL = TEST_NOT_COMPLETED_SINCE_LAST_CLEAR | TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE;
/** Bits this fault memory supports (19 01/02 availability mask): all but warningIndicatorRequested. */
constexpr uint8_t AVAILABILITY_MASK = 0x7FU;
} // namespace status

/** Group of all DTCs (ClearDiagnosticInformation 14 FF FF FF). */
constexpr uint32_t GROUP_ALL_DTCS = 0xFFFFFFU;

enum class DebounceKind : uint8_t
{
    COUNTER, // fault detection counter moved by samples (AUTOSAR counter based debouncing)
    TIME     // the result has to stay for a time (AUTOSAR time based debouncing)
};

/** Counter debouncing: internal counter from passedThreshold (-1..-128) to failedThreshold (1..127). */
struct CounterDebounce
{
    int8_t failedThreshold;
    int8_t passedThreshold;
    uint8_t increment;
    uint8_t decrement;
    int8_t jumpUp;   // first failed sample below this value: counter jumps here, then steps (D.6: over zero)
    int8_t jumpDown; // first passed sample above this value: counter jumps here, then steps
};

/** Time debouncing: the same raw result for failedMs / passedMs matures it. */
struct TimeDebounce
{
    uint32_t failedMs;
    uint32_t passedMs;
};

/** One data identifier in a snapshot record: its data is stored in the entry (\p bytes long). */
struct DataItem
{
    uint16_t did;
    uint16_t bytes;
};

/** Content of one value of an extended data record. */
enum class Counter : uint8_t
{
    DID,                      // a data identifier, stored in the entry like a snapshot
    OCCURRENCE,               // how often the test matured to failed (TEST_FAILED 0 -> 1), saturated at 255
    AGING,                    // operation cycles counted towards aging
    FAULT_DETECTION,          // the fault detection counter (D.6), one signed byte
    FAILED_CYCLES,            // operation cycles with a failed test since the pending bit was set
    CYCLES_SINCE_FIRST_FAILED // operation cycles ended since the first failed test after the last clear
};

struct ExtendedItem
{
    Counter content;
    uint16_t did;   // only for Counter::DID
    uint16_t bytes; // 1 for a counter, the length of the identifier for Counter::DID
};

struct SnapshotRecord
{
    uint8_t number;
    DataItem const* items;
    uint8_t count;
};

struct ExtendedRecord
{
    uint8_t number;
    ExtendedItem const* items;
    uint8_t count;
};

/** One DTC with its event (generated from diag.yaml dtcs[]). */
struct DtcConfig
{
    char const* name;
    uint32_t number;               // 3-byte DTC (ISO 14229-1 DTC format)
    uint8_t severity;              // severity byte of 19 (D.3), with the class bits
    uint8_t priority;              // 1 most important; a larger number is displaced first
    uint8_t confirmationThreshold; // operation cycles with a failed test before the DTC is confirmed and stored
    uint8_t agingThreshold;        // passed operation cycles until a confirmed DTC ages; 0: no aging
    uint8_t cycle;                 // operation cycle of the status bits and the confirmation
    uint8_t agingCycle;            // operation cycle the aging counts in
    uint32_t conditions;           // bit i: enable condition i has to hold
    DebounceKind debounce;
    CounterDebounce counter;
    TimeDebounce time;
    SnapshotRecord const* snapshots;
    uint8_t snapshotCount;
    ExtendedRecord const* extended;
    uint8_t extendedCount;
    char const* reportedBy;        // BSW event name if the BSW reports it (report()); nullptr: sampled (ISource)
};

/** The fault memory as a whole (generated from diag.yaml fault_memory, operation_cycles, enable_conditions). */
struct Config
{
    DtcConfig const* dtcs;
    uint16_t dtcCount;
    uint8_t maxEntries;
    bool replaceOldest;       // overflow: displace the least important, oldest entry (else keep the old ones)
    uint8_t cycleCount;       // at most 8
    uint8_t autostartCycles;  // bit c: cycle c starts with the ECU and ends at its run-down
    uint8_t conditionCount;   // at most 32
    uint32_t initialConditions;
    uint32_t cycleMs;         // one sample of every sampled event per cycle of mainFunction()
    uint16_t entryDataBytes;  // largest stored data of one DTC (snapshots + extended data identifiers), >= 1
};

/** Bytes a DTC stores in its entry: every snapshot data identifier and every identifier of its extended data. */
inline uint16_t storedBytes(DtcConfig const& dtc)
{
    uint32_t bytes = 0U;
    for (uint8_t r = 0U; r < dtc.snapshotCount; ++r)
    {
        for (uint8_t i = 0U; i < dtc.snapshots[r].count; ++i)
        {
            bytes += dtc.snapshots[r].items[i].bytes;
        }
    }
    for (uint8_t r = 0U; r < dtc.extendedCount; ++r)
    {
        for (uint8_t i = 0U; i < dtc.extended[r].count; ++i)
        {
            if (dtc.extended[r].items[i].content == Counter::DID)
            {
                bytes += dtc.extended[r].items[i].bytes;
            }
        }
    }
    return static_cast<uint16_t>(bytes);
}

// ---- persistent image (NvM blocks derived by tools/rtegen, which computes the same sizes: DEM_EVENT_RECORD ...)
// Events block:  [0] 0xDE [1] format 1 [2..3] DTC count (LE), then per DTC: status, occurrence, aging counter,
//                failed cycles, cycles since first failed.
// Entries block: [0] 0xDE [1] format 2 [2] max entries [3] 0, then per entry: DTC index + 1 (LE, 0 free), stamp (LE,
//                order of storage), stored data (entryDataBytes).
// Both blocks start as zeros (NvM default): a wrong header means "never stored", the memory starts cleared.
constexpr uint8_t NVM_MAGIC          = 0xDEU;
constexpr uint8_t NVM_FORMAT_EVENTS  = 1U;
constexpr uint8_t NVM_FORMAT_ENTRIES = 2U;
constexpr size_t NVM_HEADER_BYTES    = 4U;
constexpr size_t NVM_EVENT_RECORD    = 5U;
constexpr size_t NVM_ENTRY_HEADER    = 6U;

constexpr size_t eventsBlockBytes(size_t const dtcCount) { return NVM_HEADER_BYTES + (NVM_EVENT_RECORD * dtcCount); }

constexpr size_t entriesBlockBytes(size_t const maxEntries, size_t const entryDataBytes)
{
    return NVM_HEADER_BYTES + (maxEntries * (NVM_ENTRY_HEADER + entryDataBytes));
}

/** State of one DTC: the persistent part and the debouncing (RAM only, restarts with every cycle). */
struct DtcState
{
    uint8_t status;
    uint8_t occurrence;
    uint8_t aging;
    uint8_t failedCycles;
    uint8_t cyclesSinceFirstFailed;
    int8_t direction;   // time debouncing: +1 failed, -1 passed, 0 none yet
    int16_t counter;    // counter debouncing: internal counter
    uint32_t elapsedMs; // time debouncing: time of the current direction
    int8_t fdc;         // fault detection counter -128..127 (D.6)
    // BSW event interface (report()), under the lock
    bool latchValid;
    bool latchFailed;   // a failed report since the last cycle (a short failure is not lost)
    Result latchLast;
};

/** One stored entry of the memory. */
struct Entry
{
    uint16_t dtc;   // DTC index + 1, 0: free
    uint32_t stamp; // order of storage: smaller is older
};

} // namespace dem
