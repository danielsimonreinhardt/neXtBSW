// NvM core (NVM-02, NVM-18): storage-neutral non-volatile blocks on a virtual EEPROM, with the integrity of each block
// as a mechanism of the BSW - the memory driver only reads and writes bytes.
//
// The memory is OpenBSW's L1 contract eeprom::IEepromDriver: a byte-addressed virtual EEPROM (read/write at an
// address). The FlexNVM EEPROM emulation of the S32K1 (FlexRAM) is one implementation; a data-flash EEPROM emulation
// (S32K3, STM32, RM4x F021 FEE) or an external SPI EEPROM implements the same contract. The manager never calls
// IEepromDriver::init(): preparing the memory (on the S32K1 the irreversible FlexNVM partitioning, PGMPART) is not
// part of the normal start-up and stays with an explicit, separately released action.
//
// Every block has two places (A at address, B at address + copyBytes()). What they hold depends on the integrity the
// project gives the block (project.yaml nvm.blocks.<Block>.integrity, tools/rtegen):
//
// QM (as since NVM-02): one current value. A record per place, little endian (D11):
//   [0..1] block id   [2..3] data size   [4..5] sequence   [6..6+size) data   [6+size..8+size) CRC-16/CCITT-FALSE
//   A write goes to the place that does not hold the current value, so a write torn by a power loss leaves the
//   previous value readable (the torn place fails its CRC). Reading takes the valid place with the newer sequence
//   (16 bit, wrap-around compare). NVM-30: a place that fails its CRC (or cannot be read) next to a valid one is
//   reported (event PLACE_DEFECTIVE; state VALID, quality STORED): from the defective bytes it cannot be told
//   whether it held the previous record or a newer one - a write torn by a power loss, or the newest record damaged -
//   so the loaded value may be the previous one. The next write of the block goes to the defective place.
//
// ASIL (NVM-18, decision of the user 2026-09-29, nvm-block-integritaet: zwei_kopien): two copies of the current value
//   [0..1] block id   [2..3] data size   [4..7] write counter   [8..8+size) data   [8+size..12+size) CRC-32
//   Reading checks both copies (id, size, CRC-32) and compares them. The valid copy with the higher counter wins.
//   Both valid with the same counter must hold the same data. One copy defective (CRC, other id or size, erased,
//   unreadable, or data differing at the same counter): the good copy is written over it (repair) and the event
//   sink hears COPY_REPAIRED (or COPY_REPAIR_FAILED). Both defective: the default, event DEFAULT_LOADED and the
//   quality UNSECURED for the application. A valid copy with a lower counter is a write interrupted between the two
//   copies, not a defect: it is brought up to date without an event.
//   A write puts the same record (counter + 1) into both copies one after the other, first into the copy that does
//   not hold the winning value, and reads each back before it goes on; a power loss at any byte leaves at least
//   one valid copy (the one not being written).
//
// The CRC covers id, size, sequence/counter and data. Only bytes that differ from the memory content are written
// (wear of an EEPROM emulation), then the record is read back and compared. The id is derived by the generator from
// block name, type and (for ASIL) the record format, so a changed layout (renamed, resized, retyped, moved block or
// other integrity) is detected and the block starts from its default instead of misreading foreign data.
//
// The RAM image of a block belongs to the RTE (tools/rtegen): the RTE copies a changed value into the image and sets
// `changed`, both under the lock of the RTE; the manager takes the image under the same lock. Lock is that lock type
// (::async::LockType on the target, a stub in host tests). The quality of a block (where its value came from at the
// start) goes to the RTE the same way (BlockConfig::quality); the RTE turns it into the qualifier nvm.qlf.<Block>.
//
// Events for the fault memory (NVM-16 makes them DTCs): IEventSink, called from readAll() outside the lock.

#pragma once

#include <bsp/eeprom/IEepromDriver.h>

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace nvm
{

/** CRC-16/CCITT-FALSE (polynomial 0x1021, init 0xFFFF, no reflection, no final XOR; check "123456789" = 0x29B1). */
inline uint16_t crc16(uint8_t const* const data, size_t const length, uint16_t crc = 0xFFFFU)
{
    for (size_t i = 0U; i < length; ++i)
    {
        crc = static_cast<uint16_t>(crc ^ static_cast<uint16_t>(static_cast<uint16_t>(data[i]) << 8U));
        for (uint8_t bit = 0U; bit < 8U; ++bit)
        {
            crc = ((crc & 0x8000U) != 0U) ? static_cast<uint16_t>(static_cast<uint16_t>(crc << 1U) ^ 0x1021U)
                                          : static_cast<uint16_t>(crc << 1U);
        }
    }
    return crc;
}

/**
 * CRC-32 (IEEE 802.3: reflected polynomial 0xEDB88320, init 0xFFFFFFFF, final XOR 0xFFFFFFFF; check "123456789" =
 * 0xCBF43926). Bitwise, without a table: the blocks are small and the NvM runs in the background.
 */
inline uint32_t crc32(uint8_t const* const data, size_t const length)
{
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0U; i < length; ++i)
    {
        crc ^= static_cast<uint32_t>(data[i]);
        for (uint8_t bit = 0U; bit < 8U; ++bit)
        {
            crc = ((crc & 1U) != 0U) ? ((crc >> 1U) ^ 0xEDB88320U) : (crc >> 1U);
        }
    }
    return crc ^ 0xFFFFFFFFU;
}

/** Integrity of a block (project.yaml nvm.blocks.<Block>.integrity: qm, or asil_a..asil_d - one format for all). */
enum class Integrity : uint8_t
{
    QM,  // one current value, CRC-16
    ASIL // two copies of the current value, CRC-32 and write counter each
};

constexpr size_t RECORD_HEADER   = 6U;                 // QM: id, size, sequence
constexpr size_t RECORD_OVERHEAD = RECORD_HEADER + 2U; // + CRC-16
constexpr size_t ASIL_HEADER     = 8U;                 // ASIL: id, size, write counter
constexpr size_t ASIL_OVERHEAD   = ASIL_HEADER + 4U;   // + CRC-32

/** Bytes of one QM place of a block with \p size data bytes (4-byte aligned, as the generator lays them out). */
constexpr uint32_t slotBytes(uint32_t const size) { return (size + RECORD_OVERHEAD + 3U) & ~3U; }

/** Bytes of one ASIL copy of a block with \p size data bytes (4-byte aligned). */
constexpr uint32_t asilCopyBytes(uint32_t const size) { return (size + ASIL_OVERHEAD + 3U) & ~3U; }

/** Bytes of one of the two places of a block. */
constexpr uint32_t copyBytes(uint32_t const size, Integrity const integrity)
{
    return (integrity == Integrity::ASIL) ? asilCopyBytes(size) : slotBytes(size);
}

/** Bytes a block occupies in the memory (both places) - tools/nxconfig/nvm.py block_bytes(). */
constexpr uint32_t blockBytes(uint32_t const size, Integrity const integrity)
{
    return 2U * copyBytes(size, integrity);
}

/** When a changed block is written. */
enum class WritePolicy : uint8_t
{
    SHUTDOWN, // at the run-down of the ECU (writeAll) and on request; the RAM value is lost on a power cut
    CHANGE    // additionally by mainFunction(), at most once per minIntervalMs (wear of the memory)
};

/**
 * Where the value of a block came from at the start, as the application sees it (nvm.qlf.<Block>). The numbers are
 * a contract with tools/rtegen (NVM_QUALITY), which maps them to the qualifier values QLF_* of Rte_Qlf.h (RTE-28:
 * not read and never written give the qualifier "not available", QLF_NOT_AVAILABLE; doc/nvm.md has the table).
 */
enum class Quality : uint8_t
{
    NOT_READ  = 0U, // readAll() not run yet
    STORED    = 1U, // the stored value (for ASIL: at least one copy was good, a defective one is repaired)
    DEFAULT   = 2U, // the default: the block was never written (erased memory)
    UNSECURED = 3U, // the default: the stored value is lost (no valid place, or the layout changed)
    NO_MEMORY = 4U  // the default: no persistent memory, or the block lies beyond it
};

/** One block, generated (rte::NVM_BLOCKS). */
struct BlockConfig
{
    char const* name       = nullptr;               // block name (nvm.<Block> in the ECU description)
    uint16_t id            = 0U;                    // record id, from name, type and format; never 0x0000 or 0xFFFF
    uint16_t size          = 0U;                    // data bytes
    uint32_t address       = 0U;                    // first place; the second at address + copyBytes(size, integrity)
    void* image            = nullptr;               // RAM image (RTE), taken under the lock
    void const* defaults   = nullptr;               // default value (init value of the writing port)
    bool* changed          = nullptr;               // set by the RTE under the lock when the image changed
    WritePolicy policy     = WritePolicy::SHUTDOWN;
    uint32_t minIntervalMs = 0U;                    // CHANGE: shortest distance between two writes
    Integrity integrity    = Integrity::QM;         // NVM-18
    uint8_t* quality       = nullptr;               // NVM-18: Quality for the RTE (written under the lock), nullptr: none
};

/** Where the value in the image came from after readAll(). */
enum class BlockState : uint8_t
{
    NOT_READ,  // readAll() not called yet
    VALID,     // stored value
    EMPTY,     // default: both places erased, the block was never written
    CORRUPT,   // default: a record of this block, but no place passes its CRC (torn write, bit error)
    FOREIGN,   // default: other data in the places (layout changed: other id or size at this address)
    NO_MEMORY, // default: no persistent memory ready (not prepared, not present in this port)
    OUTSIDE,   // default: the block lies beyond the usable size of the memory
    REPAIRED,  // ASIL: stored value; one copy was defective and has been rewritten from the other
    DEGRADED   // ASIL: stored value from the one good copy; rewriting the defective copy failed
};

enum class WriteResult : uint8_t
{
    NONE,      // not written since the start
    OK,        // written and read back
    FAILED,    // the memory refused a read or write
    VERIFY,    // read back differs from what was written
    NO_MEMORY  // not written: no persistent memory or block outside of it
};

/** What readAll() tells the fault memory (NVM-16 turns them into DTCs of the neXtBSW range). */
enum class BlockEvent : uint8_t
{
    COPY_REPAIRED,      // ASIL: one copy was defective and has been rewritten from the other
    COPY_REPAIR_FAILED, // ASIL: one copy was defective and could not be rewritten (the value is still stored once)
    DEFAULT_LOADED,     // no valid place: the default was loaded (QM and ASIL; not for an erased or moved block)
    PLACE_DEFECTIVE     // QM (NVM-30): one place defective (CRC, unreadable), the value of the other one loaded -
                        // the previous value if the defective place held the newer record
};

/** Receiver of the block events (the integration forwards them to the fault memory). */
class IEventSink
{
public:
    virtual void onBlockEvent(size_t index, BlockConfig const& block, BlockEvent event) = 0;

protected:
    IEventSink()                             = default;
    ~IEventSink()                            = default;
    IEventSink(IEventSink const&)            = default;
    IEventSink& operator=(IEventSink const&) = default;
};

struct BlockStatus
{
    BlockState state;
    WriteResult last;
    uint8_t slot;         // place of the current stored value (the next write goes to the other one first)
    uint32_t sequence;    // sequence (QM, 16 bit) or write counter (ASIL) of the current stored value
    uint32_t writes;      // successful writes since the start
    uint32_t errors;      // failed writes and failed repairs since the start
    uint32_t lastWriteMs; // time of the last successful write (mainFunction/writeAll argument)
    uint32_t repairs;     // ASIL: copies rewritten from the other one at the start (defect or interrupted write)
    Quality quality;
};

inline char const* stateName(BlockState const state)
{
    switch (state)
    {
        case BlockState::NOT_READ: return "not read";
        case BlockState::VALID: return "stored";
        case BlockState::EMPTY: return "default (empty)";
        case BlockState::CORRUPT: return "default (crc)";
        case BlockState::FOREIGN: return "default (layout)";
        case BlockState::NO_MEMORY: return "default (no memory)";
        case BlockState::OUTSIDE: return "default (outside)";
        case BlockState::REPAIRED: return "stored (copy repaired)";
        case BlockState::DEGRADED: return "stored (one copy)";
        default: return "?";
    }
}

inline char const* resultName(WriteResult const result)
{
    switch (result)
    {
        case WriteResult::NONE: return "-";
        case WriteResult::OK: return "ok";
        case WriteResult::FAILED: return "failed";
        case WriteResult::VERIFY: return "verify";
        case WriteResult::NO_MEMORY: return "no memory";
        default: return "?";
    }
}

inline char const* integrityName(Integrity const integrity)
{
    return (integrity == Integrity::ASIL) ? "asil" : "qm";
}

/** Quality the application sees for a block state. */
inline Quality qualityOf(BlockState const state)
{
    switch (state)
    {
        case BlockState::VALID:
        case BlockState::REPAIRED:
        case BlockState::DEGRADED: return Quality::STORED;
        case BlockState::EMPTY: return Quality::DEFAULT;
        case BlockState::CORRUPT:
        case BlockState::FOREIGN: return Quality::UNSECURED;
        case BlockState::NO_MEMORY:
        case BlockState::OUTSIDE: return Quality::NO_MEMORY;
        default: return Quality::NOT_READ;
    }
}

/**
 * The blocks of one ECU on one memory. Not thread safe by itself: readAll() runs once before the application tasks
 * start, mainFunction()/writeAll() in one context (the NvM context of the integration). Only the image, the changed
 * flag and the quality are shared with the RTE, always under Lock.
 */
template<class Lock>
class Manager
{
public:
    /**
     * \param scratch at least copyBytes() of the largest block (rte::NVM_SLOT_MAX), used for one record at a time.
     */
    Manager(
        BlockConfig const* const blocks,
        BlockStatus* const status,
        size_t const count,
        uint8_t* const scratch,
        size_t const scratchBytes)
    : _blocks(blocks)
    , _status(status)
    , _count(count)
    , _scratch(scratch)
    , _scratchBytes(scratchBytes)
    , _memory(nullptr)
    , _usableBytes(0U)
    , _sink(nullptr)
    {
        for (size_t i = 0U; i < _count; ++i)
        {
            _status[i]
                = BlockStatus{BlockState::NOT_READ, WriteResult::NONE, 1U, 0U, 0U, 0U, 0U, 0U, Quality::NOT_READ};
        }
    }

    Manager(Manager const&)            = delete;
    Manager& operator=(Manager const&) = delete;

    /**
     * The persistent memory, or nullptr if the port has none ready (the blocks then keep their defaults and
     * nothing is written). \p usableBytes: size of the memory as the port reports it (e.g. the partitioned EEPROM
     * emulation); a block beyond it is OUTSIDE.
     */
    void attach(::eeprom::IEepromDriver* const memory, uint32_t const usableBytes)
    {
        _memory      = memory;
        _usableBytes = (memory != nullptr) ? usableBytes : 0U;
    }

    /** Receiver of the block events of readAll() (nullptr: none). */
    void setEventSink(IEventSink* const sink) { _sink = sink; }

    bool hasMemory() const { return _memory != nullptr; }

    uint32_t usableBytes() const { return _usableBytes; }

    size_t count() const { return _count; }

    BlockConfig const& block(size_t const i) const { return _blocks[i]; }

    BlockStatus const& status(size_t const i) const { return _status[i]; }

    /** Bytes the blocks occupy (end of the last place). */
    uint32_t layoutBytes() const
    {
        uint32_t end = 0U;
        for (size_t i = 0U; i < _count; ++i)
        {
            uint32_t const blockEnd = _blocks[i].address + blockBytes(_blocks[i].size, _blocks[i].integrity);
            end                     = (blockEnd > end) ? blockEnd : end;
        }
        return end;
    }

    /** Loads every block into its image: the stored value, or its default with the reason in status(). */
    void readAll()
    {
        for (size_t i = 0U; i < _count; ++i)
        {
            if (_blocks[i].integrity == Integrity::ASIL)
            {
                readAsil(i);
            }
            else
            {
                readQm(i);
            }
            setQuality(i);
        }
    }

    /** true if a write of block \p i is pending (its image changed since it was read or written). */
    bool isPending(size_t const i) const
    {
        Lock const lock;
        return *_blocks[i].changed;
    }

    /**
     * Cyclic part: writes at most one changed block with WritePolicy::CHANGE whose interval has passed. Returns the
     * number of blocks written (0 or 1), so one call blocks for one block at most (two records for ASIL).
     */
    size_t mainFunction(uint32_t const nowMs)
    {
        for (size_t n = 0U; n < _count; ++n)
        {
            size_t const i                 = (_next + n) % _count;
            BlockConfig const& blockConfig = _blocks[i];
            if ((blockConfig.policy != WritePolicy::CHANGE) || !isPending(i))
            {
                continue;
            }
            BlockStatus const& s = _status[i];
            bool const due       = ((s.writes == 0U) && (s.errors == 0U))
                             || ((nowMs - s.lastWriteMs) >= blockConfig.minIntervalMs);
            if (!due)
            {
                continue;
            }
            _next = (i + 1U) % _count;
            return writeBlock(i, nowMs) ? 1U : 0U;
        }
        return 0U;
    }

    /** Writes every changed block (run-down, console). Returns the number of blocks written successfully. */
    size_t writeAll(uint32_t const nowMs)
    {
        size_t written = 0U;
        for (size_t i = 0U; i < _count; ++i)
        {
            if (isPending(i) && writeBlock(i, nowMs))
            {
                ++written;
            }
        }
        return written;
    }

    /** Writes block \p i now if it changed; true if it was written (or nothing was pending). */
    bool writeIfPending(size_t const i, uint32_t const nowMs) { return !isPending(i) || writeBlock(i, nowMs); }

#ifdef NX_NVM_FAULT_INJECTION
    /**
     * Test builds only (board proof of NVM-18): flips one bit of the checksum of place \p copy (0 = A, 1 = B) of
     * block \p i in the memory, so the next start finds that place defective. Never compiled into a product.
     */
    bool injectCopyFault(size_t const i, uint8_t const copy)
    {
        BlockConfig const& blockConfig = _blocks[i];
        if ((_memory == nullptr) || !inside(blockConfig) || (copy > 1U))
        {
            return false;
        }
        uint32_t const overhead = (blockConfig.integrity == Integrity::ASIL) ? ASIL_HEADER : RECORD_HEADER;
        uint32_t const address  = placeAddress(blockConfig, copy) + overhead + blockConfig.size;
        uint8_t value           = 0U;
        if (_memory->read(address, &value, 1U) != ::bsp::BSP_OK)
        {
            return false;
        }
        value = static_cast<uint8_t>(value ^ 0x01U);
        return _memory->write(address, &value, 1U) == ::bsp::BSP_OK;
    }
#endif

private:
    static void put16(uint8_t* const p, uint16_t const v)
    {
        p[0] = static_cast<uint8_t>(v & 0xFFU);
        p[1] = static_cast<uint8_t>(v >> 8U);
    }

    static uint16_t get16(uint8_t const* const p)
    {
        return static_cast<uint16_t>(static_cast<uint16_t>(p[0]) | static_cast<uint16_t>(p[1] << 8U));
    }

    static void put32(uint8_t* const p, uint32_t const v)
    {
        put16(p, static_cast<uint16_t>(v & 0xFFFFU));
        put16(&p[2], static_cast<uint16_t>(v >> 16U));
    }

    static uint32_t get32(uint8_t const* const p)
    {
        return static_cast<uint32_t>(get16(p)) | (static_cast<uint32_t>(get16(&p[2])) << 16U);
    }

    /** a newer than b in 16-bit wrap-around order */
    static bool newer(uint16_t const a, uint16_t const b)
    {
        return static_cast<int16_t>(static_cast<uint16_t>(a - b)) > 0;
    }

    bool inside(BlockConfig const& block) const
    {
        uint32_t const bytes = blockBytes(block.size, block.integrity);
        return (block.address <= _usableBytes) && (bytes <= (_usableBytes - block.address))
               && (copyBytes(block.size, block.integrity) <= _scratchBytes);
    }

    uint32_t placeAddress(BlockConfig const& block, uint8_t const place) const
    {
        return block.address + (static_cast<uint32_t>(place) * copyBytes(block.size, block.integrity));
    }

    static uint32_t recordLength(BlockConfig const& block)
    {
        return ((block.integrity == Integrity::ASIL) ? ASIL_OVERHEAD : RECORD_OVERHEAD) + block.size;
    }

    enum class Slot : uint8_t
    {
        ERASED,
        VALID,
        CORRUPT,
        FOREIGN,
        UNREADABLE
    };

    /** Reads place \p slot into the scratch and checks it; \p sequence: its sequence (QM) or counter (ASIL). */
    Slot readSlot(BlockConfig const& block, uint8_t const slot, uint32_t& sequence)
    {
        uint32_t const length = recordLength(block);
        if (_memory->read(placeAddress(block, slot), _scratch, length) != ::bsp::BSP_OK)
        {
            return Slot::UNREADABLE;
        }
        uint16_t const id   = get16(&_scratch[0]);
        uint16_t const size = get16(&_scratch[2]);
        if ((id == 0xFFFFU) && (size == 0xFFFFU))
        {
            return Slot::ERASED;
        }
        if ((id != block.id) || (size != block.size))
        {
            return Slot::FOREIGN;
        }
        if (block.integrity == Integrity::ASIL)
        {
            if (crc32(_scratch, ASIL_HEADER + block.size) != get32(&_scratch[ASIL_HEADER + block.size]))
            {
                return Slot::CORRUPT;
            }
            sequence = get32(&_scratch[4]);
            return Slot::VALID;
        }
        if (crc16(_scratch, RECORD_HEADER + block.size) != get16(&_scratch[RECORD_HEADER + block.size]))
        {
            return Slot::CORRUPT;
        }
        sequence = get16(&_scratch[4]);
        return Slot::VALID;
    }

    void takeImage(size_t const i, uint8_t const* const data)
    {
        BlockConfig const& blockConfig = _blocks[i];
        Lock const lock;
        (void)std::memcpy(blockConfig.image, data, blockConfig.size);
        *blockConfig.changed = false;
    }

    void useDefault(size_t const i, BlockState const state)
    {
        BlockConfig const& blockConfig = _blocks[i];
        {
            Lock const lock;
            (void)std::memcpy(blockConfig.image, blockConfig.defaults, blockConfig.size);
            *blockConfig.changed = false;
        }
        _status[i].state    = state;
        _status[i].slot     = 1U; // the first write goes to place A
        _status[i].sequence = 0U;
    }

    void setQuality(size_t const i)
    {
        _status[i].quality = qualityOf(_status[i].state);
        if (_blocks[i].quality != nullptr)
        {
            Lock const lock;
            *_blocks[i].quality = static_cast<uint8_t>(_status[i].quality);
        }
    }

    void report(size_t const i, BlockEvent const event)
    {
        if (_sink != nullptr)
        {
            _sink->onBlockEvent(i, _blocks[i], event);
        }
    }

    static BlockState defaultState(Slot const s0, Slot const s1)
    {
        if ((s0 == Slot::CORRUPT) || (s1 == Slot::CORRUPT) || (s0 == Slot::UNREADABLE) || (s1 == Slot::UNREADABLE))
        {
            return BlockState::CORRUPT;
        }
        if ((s0 == Slot::FOREIGN) || (s1 == Slot::FOREIGN))
        {
            return BlockState::FOREIGN;
        }
        return BlockState::EMPTY;
    }

    /** No valid place: the default; a defect is an event for the fault memory (an erased or moved block is not). */
    void loadDefault(size_t const i, Slot const s0, Slot const s1)
    {
        BlockState const state = defaultState(s0, s1);
        useDefault(i, state);
        if (state == BlockState::CORRUPT)
        {
            report(i, BlockEvent::DEFAULT_LOADED);
        }
    }

    bool prepared(size_t const i)
    {
        if (_memory == nullptr)
        {
            useDefault(i, BlockState::NO_MEMORY);
            return false;
        }
        if (!inside(_blocks[i]))
        {
            useDefault(i, BlockState::OUTSIDE);
            return false;
        }
        return true;
    }

    void readQm(size_t const i)
    {
        BlockConfig const& blockConfig = _blocks[i];
        if (!prepared(i))
        {
            return;
        }
        uint32_t sequence[2] = {0U, 0U};
        Slot const s0        = readSlot(blockConfig, 0U, sequence[0]);
        Slot const s1        = readSlot(blockConfig, 1U, sequence[1]);
        if ((s0 == Slot::VALID) || (s1 == Slot::VALID))
        {
            uint16_t const seq0 = static_cast<uint16_t>(sequence[0]);
            uint16_t const seq1 = static_cast<uint16_t>(sequence[1]);
            uint8_t const slot  = ((s0 == Slot::VALID) && ((s1 != Slot::VALID) || !newer(seq1, seq0))) ? 0U : 1U;
            // read the chosen slot again: the scratch holds the last slot read
            uint32_t check = 0U;
            if (readSlot(blockConfig, slot, check) == Slot::VALID)
            {
                takeImage(i, &_scratch[RECORD_HEADER]);
                // NVM-30: a defective place next to the loaded one may have held a newer record - never silent
                Slot const other    = (slot == 0U) ? s1 : s0;
                bool const lost     = (other == Slot::CORRUPT) || (other == Slot::UNREADABLE);
                _status[i].state    = BlockState::VALID;
                _status[i].slot     = slot;
                _status[i].sequence = check;
                if (lost)
                {
                    report(i, BlockEvent::PLACE_DEFECTIVE);
                }
                return;
            }
            // NVM-30: the chosen place changed between two reads - it is defective, not erased or foreign
            loadDefault(i, (slot == 0U) ? Slot::CORRUPT : s0, (slot == 1U) ? Slot::CORRUPT : s1);
            return;
        }
        loadDefault(i, s0, s1);
        // keep the sequence of a torn or foreign slot from winning over the first write: both start at 1
    }

    /** Writes the record in the scratch (\p length bytes) to place \p place and reads it back. */
    WriteResult putRecord(BlockConfig const& block, uint8_t const place, uint32_t const length)
    {
        uint32_t const address = placeAddress(block, place);
        if (!writeDifferent(address, _scratch, length))
        {
            return WriteResult::FAILED;
        }
        return verify(address, _scratch, length) ? WriteResult::OK : WriteResult::VERIFY;
    }

    void readAsil(size_t const i)
    {
        BlockConfig const& blockConfig = _blocks[i];
        if (!prepared(i))
        {
            return;
        }
        uint32_t const length = recordLength(blockConfig);
        uint32_t counter[2]   = {0U, 0U};
        Slot const s0         = readSlot(blockConfig, 0U, counter[0]);
        Slot const s1         = readSlot(blockConfig, 1U, counter[1]);
        if ((s0 != Slot::VALID) && (s1 != Slot::VALID))
        {
            loadDefault(i, s0, s1);
            return;
        }
        // the winner: the valid copy with the higher counter (A on a tie)
        uint8_t const winner = ((s0 == Slot::VALID) && ((s1 != Slot::VALID) || (counter[0] >= counter[1]))) ? 0U : 1U;
        uint8_t const loser  = static_cast<uint8_t>(winner ^ 1U);
        bool defect          = ((winner == 0U) ? s1 : s0) != Slot::VALID;
        uint32_t check       = 0U;
        if (readSlot(blockConfig, winner, check) != Slot::VALID)
        {
            // the winner changed between two reads: no stable copy, treat both as defective
            loadDefault(i, Slot::CORRUPT, Slot::CORRUPT);
            return;
        }
        bool const tie = !defect && (counter[0] == counter[1]);
        bool same      = false;
        if (tie)
        {
            same   = verify(placeAddress(blockConfig, loser), _scratch, length);
            defect = !same; // same counter, other data: the other copy is defective
        }
        takeImage(i, &_scratch[ASIL_HEADER]);
        BlockStatus& s = _status[i];
        s.state        = BlockState::VALID;
        s.slot         = winner;
        s.sequence     = check;
        if (same)
        {
            return; // both copies hold the value
        }
        // bring the other copy up to date: a defect (event) or a write interrupted between the copies (none)
        ++s.repairs;
        if (putRecord(blockConfig, loser, length) == WriteResult::OK)
        {
            s.slot = loser;
            if (defect)
            {
                s.state = BlockState::REPAIRED;
                report(i, BlockEvent::COPY_REPAIRED);
            }
            return;
        }
        ++s.errors;
        if (defect)
        {
            s.state = BlockState::DEGRADED;
            report(i, BlockEvent::COPY_REPAIR_FAILED);
        }
    }

    /** Writes \p length bytes at \p address, only where the memory differs (reads in chunks first). */
    bool writeDifferent(uint32_t const address, uint8_t const* const data, uint32_t const length)
    {
        uint8_t chunk[16];
        uint32_t offset = 0U;
        while (offset < length)
        {
            uint32_t const n = ((length - offset) < sizeof(chunk)) ? (length - offset) : sizeof(chunk);
            if (_memory->read(address + offset, chunk, n) != ::bsp::BSP_OK)
            {
                return false;
            }
            uint32_t k = 0U;
            while (k < n)
            {
                if (chunk[k] == data[offset + k])
                {
                    ++k;
                    continue;
                }
                uint32_t end = k + 1U;
                while ((end < n) && (chunk[end] != data[offset + end]))
                {
                    ++end;
                }
                if (_memory->write(address + offset + k, &data[offset + k], end - k) != ::bsp::BSP_OK)
                {
                    return false;
                }
                k = end;
            }
            offset += n;
        }
        return true;
    }

    /** Reads back \p length bytes at \p address and compares them with \p data. */
    bool verify(uint32_t const address, uint8_t const* const data, uint32_t const length)
    {
        uint8_t chunk[16];
        uint32_t offset = 0U;
        while (offset < length)
        {
            uint32_t const n = ((length - offset) < sizeof(chunk)) ? (length - offset) : sizeof(chunk);
            if ((_memory->read(address + offset, chunk, n) != ::bsp::BSP_OK)
                || (std::memcmp(chunk, &data[offset], n) != 0))
            {
                return false;
            }
            offset += n;
        }
        return true;
    }

    void fail(size_t const i, WriteResult const result)
    {
        {
            Lock const lock;
            *_blocks[i].changed = true; // still to be written
        }
        _status[i].last = result;
        ++_status[i].errors;
    }

    void succeed(size_t const i, uint32_t const nowMs)
    {
        BlockStatus& s = _status[i];
        s.last         = WriteResult::OK;
        s.lastWriteMs  = nowMs;
        ++s.writes;
    }

    bool writeBlock(size_t const i, uint32_t const nowMs)
    {
        BlockConfig const& blockConfig = _blocks[i];
        BlockStatus& s                 = _status[i];
        if ((_memory == nullptr) || !inside(blockConfig))
        {
            s.last = WriteResult::NO_MEMORY;
            return false;
        }
        bool const asil     = blockConfig.integrity == Integrity::ASIL;
        size_t const header = asil ? ASIL_HEADER : RECORD_HEADER;
        {
            Lock const lock;
            (void)std::memcpy(&_scratch[header], blockConfig.image, blockConfig.size);
            *blockConfig.changed = false;
        }
        uint32_t const length = recordLength(blockConfig);
        uint8_t const first   = static_cast<uint8_t>(s.slot ^ 1U);
        put16(&_scratch[0], blockConfig.id);
        put16(&_scratch[2], blockConfig.size);
        if (!asil)
        {
            uint16_t const sequence = static_cast<uint16_t>(s.sequence + 1U);
            put16(&_scratch[4], sequence);
            put16(&_scratch[RECORD_HEADER + blockConfig.size], crc16(_scratch, RECORD_HEADER + blockConfig.size));
            WriteResult const result = putRecord(blockConfig, first, length);
            if (result != WriteResult::OK)
            {
                fail(i, result);
                return false;
            }
            s.state    = BlockState::VALID;
            s.slot     = first;
            s.sequence = sequence;
            succeed(i, nowMs);
            return true;
        }
        // ASIL: the same record into both copies, the one that does not hold the winning value first
        uint32_t const counter = s.sequence + 1U;
        put32(&_scratch[4], counter);
        put32(&_scratch[ASIL_HEADER + blockConfig.size], crc32(_scratch, ASIL_HEADER + blockConfig.size));
        WriteResult result = putRecord(blockConfig, first, length);
        if (result != WriteResult::OK)
        {
            fail(i, result); // the other copy still holds the previous value
            return false;
        }
        s.state    = BlockState::VALID;
        s.slot     = first; // the newest value is in this copy now
        s.sequence = counter;
        result     = putRecord(blockConfig, static_cast<uint8_t>(first ^ 1U), length);
        if (result != WriteResult::OK)
        {
            fail(i, result); // stored once; the next write starts with the other copy
            return false;
        }
        s.slot = static_cast<uint8_t>(first ^ 1U);
        succeed(i, nowMs);
        return true;
    }

    BlockConfig const* const _blocks;
    BlockStatus* const _status;
    size_t const _count;
    uint8_t* const _scratch;
    size_t const _scratchBytes;
    ::eeprom::IEepromDriver* _memory;
    uint32_t _usableBytes;
    IEventSink* _sink;
    size_t _next = 0U; // round robin start of mainFunction()
};

} // namespace nvm
