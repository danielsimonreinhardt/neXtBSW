// Host test of the NvM core (NVM-02, NVM-18: libs/bsw/nvm/include/nvm/Nvm.h) against a virtual EEPROM in RAM
// (FakeEeprom.h, OpenBSW eeprom::IEepromDriver). Own test profile: three QM blocks of different size and policy and
// two ASIL blocks (scalar and vector), no board, no RTE.
//
// QM (behaviour of NVM-02, kept): CRC-16 check value, defaults without memory and on an erased memory, write/restart
// round trip, alternating slots and sequence wrap-around, a power loss at every byte of a record (the previous value
// survives), bit flips, changed layout (other id, other size), blocks beyond the usable size, only differing bytes
// written, read-back verification, CHANGE policy with interval and round robin, SHUTDOWN blocks only by writeAll,
// image access under the lock, IEepromDriver::init() never called.
// ASIL (NVM-18, decision nvm-block-integritaet: zwei_kopien): CRC-32 check value, record format of both copies,
// write counter, one copy defective (bit flip, erased, other id, same counter with other data) -> repaired and event,
// repair refused -> event and the value still stored, both defective -> default, event and quality UNSECURED, a write
// interrupted between the copies -> brought up to date without event, a power loss at every byte of both records
// (one valid copy always stays), write order (the copy without the winning value first), fault injection of the
// test build. Quality and events for QM blocks.
// NVM-30: a QM place that fails its CRC or cannot be read next to a valid one is reported (PLACE_DEFECTIVE; state
// VALID, quality STORED) - damaged previous record (board 2026-10-02: nvm inject EcuStatistics a), damaged newest
// record (the previous value is loaded), torn write; an erased or foreign other place is no defect; a place that
// changes between the two reads of the start is defective (default, DEFAULT_LOADED), never "erased".
// Build and run: libs/bsw/nvm/test/test_nvm_core.py, also with deliberate faults (mutations).

#include "FakeEeprom.h"
#include "nvm/Nvm.h"

#include <cstdio>
#include <cstring>

namespace
{
unsigned checks   = 0U;
unsigned failures = 0U;

#define CHECK(c)                                                     \
    do                                                               \
    {                                                                \
        ++checks;                                                    \
        if (!(c))                                                    \
        {                                                            \
            ++failures;                                              \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
        }                                                            \
    } while (0)

struct TestLock
{
    TestLock()
    {
        ++depth;
        ++count;
    }

    ~TestLock() { --depth; }

    static int depth;
    static int count;
};

int TestLock::depth = 0;
int TestLock::count = 0;

using Manager = ::nvm::Manager<TestLock>;
using ::nvm::BlockEvent;
using ::nvm::BlockState;
using ::nvm::Integrity;
using ::nvm::Quality;

struct Record
{
    uint32_t counter;
    uint16_t trim;
    uint8_t flags[2];
};

// images and defaults as the RTE would generate them
uint32_t counterImage              = 0U;
uint32_t const counterDefault      = 7U;
Record recordImage                 = {};
Record const recordDefault         = {100U, 500U, {1U, 2U}};
uint8_t bytesImage[3]              = {};
uint8_t const bytesDefault[3]      = {0xA1U, 0xA2U, 0xA3U};
uint32_t asilImage                 = 0U;
uint32_t const asilDefault         = 0x5AU;
uint16_t vectorImage[3]            = {};
uint16_t const vectorDefault[3]    = {10U, 20U, 30U};
bool changed[5]                    = {false, false, false, false, false};
uint8_t quality[5]                 = {0xEEU, 0xEEU, 0xEEU, 0xEEU, 0xEEU};

constexpr uint16_t ID_COUNTER = 0x1234U;
constexpr uint16_t ID_RECORD  = 0x2345U;
constexpr uint16_t ID_BYTES   = 0x3456U;
constexpr uint16_t ID_ASIL    = 0x4567U;
constexpr uint16_t ID_VECTOR  = 0x5678U;

constexpr uint32_t SLOT_COUNTER = ::nvm::slotBytes(sizeof(uint32_t)); // 12
constexpr uint32_t SLOT_RECORD  = ::nvm::slotBytes(sizeof(Record));   // 16
constexpr uint32_t COPY_ASIL    = ::nvm::asilCopyBytes(4U);           // 16
constexpr uint32_t COPY_VECTOR  = ::nvm::asilCopyBytes(6U);           // 20
constexpr uint32_t ADDR_BYTES   = 0U;
constexpr uint32_t ADDR_COUNTER = ADDR_BYTES + (2U * ::nvm::slotBytes(3U));
constexpr uint32_t ADDR_RECORD  = ADDR_COUNTER + (2U * SLOT_COUNTER);
constexpr uint32_t ADDR_ASIL    = ADDR_RECORD + (2U * SLOT_RECORD);
constexpr uint32_t ADDR_VECTOR  = ADDR_ASIL + (2U * COPY_ASIL);
constexpr uint32_t LEN_ASIL     = ::nvm::ASIL_OVERHEAD + 4U; // 16 bytes of record, no padding
constexpr size_t ASIL           = 3U;                        // index of the ASIL scalar
constexpr size_t VECTOR         = 4U;                        // index of the ASIL vector

::nvm::BlockConfig const BLOCKS[] = {
    {"Bytes", ID_BYTES, 3U, ADDR_BYTES, bytesImage, bytesDefault, &changed[0], ::nvm::WritePolicy::SHUTDOWN, 0U,
     Integrity::QM, &quality[0]},
    {"Counter", ID_COUNTER, 4U, ADDR_COUNTER, &counterImage, &counterDefault, &changed[1],
     ::nvm::WritePolicy::CHANGE, 1000U, Integrity::QM, &quality[1]},
    {"Record", ID_RECORD, static_cast<uint16_t>(sizeof(Record)), ADDR_RECORD, &recordImage, &recordDefault,
     &changed[2], ::nvm::WritePolicy::CHANGE, 500U, Integrity::QM, &quality[2]},
    {"Asil", ID_ASIL, 4U, ADDR_ASIL, &asilImage, &asilDefault, &changed[3], ::nvm::WritePolicy::SHUTDOWN, 0U,
     Integrity::ASIL, &quality[3]},
    {"Vector", ID_VECTOR, 6U, ADDR_VECTOR, vectorImage, vectorDefault, &changed[4], ::nvm::WritePolicy::SHUTDOWN, 0U,
     Integrity::ASIL, &quality[4]},
};
constexpr size_t COUNT = sizeof(BLOCKS) / sizeof(BLOCKS[0]);

using Memory = FakeEeprom<256U>;

/** Records the events of readAll() (the fault memory in the integration, NVM-16). */
struct Sink : ::nvm::IEventSink
{
    void onBlockEvent(size_t const index, ::nvm::BlockConfig const& block, BlockEvent const event) override
    {
        if (count < 8U)
        {
            indices[count] = index;
            events[count]  = event;
        }
        ++count;
        lastName = block.name;
        CHECK(TestLock::depth == 0); // never under the lock of the RTE
    }

    size_t count = 0U;
    size_t indices[8]    = {};
    BlockEvent events[8] = {};
    char const* lastName = nullptr;
};

/** The ECU as the tests see it: blocks, status, scratch - rebuilt for every "start". */
struct Ecu
{
    explicit Ecu(::eeprom::IEepromDriver* const memory, uint32_t const usable = 256U)
    : manager(BLOCKS, status, COUNT, scratch, sizeof(scratch))
    {
        counterImage = 0U;
        recordImage  = Record{};
        asilImage    = 0U;
        std::memset(bytesImage, 0, sizeof(bytesImage));
        std::memset(vectorImage, 0, sizeof(vectorImage));
        std::memset(quality, 0xEE, sizeof(quality));
        manager.attach(memory, usable);
        manager.setEventSink(&sink);
        manager.readAll();
    }

    /** the RTE hands a new value over: image and changed flag under the lock */
    static void set(size_t const i, void const* const value)
    {
        TestLock const lock;
        std::memcpy(BLOCKS[i].image, value, BLOCKS[i].size);
        *BLOCKS[i].changed = true;
    }

    Sink sink;
    ::nvm::BlockStatus status[COUNT];
    uint8_t scratch[32];
    Manager manager;
};

uint32_t get32(uint8_t const* const p)
{
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8U) | (static_cast<uint32_t>(p[2]) << 16U)
           | (static_cast<uint32_t>(p[3]) << 24U);
}

/** ASIL scalar written \p n times with the values 100 + k (both copies hold counter n, value 100 + n). */
void writeAsil(Memory& memory, uint32_t const n)
{
    Ecu ecu(&memory);
    for (uint32_t k = 1U; k <= n; ++k)
    {
        uint32_t const v = 100U + k;
        Ecu::set(ASIL, &v);
        CHECK(ecu.manager.writeAll(k) == 1U);
    }
}

bool copiesEqual(Memory const& memory, uint32_t const address, uint32_t const copy, uint32_t const length)
{
    return std::memcmp(&memory.bytes[address], &memory.bytes[address + copy], length) == 0;
}

// ------------------------------------------------------------------------------------------------ QM (NVM-02)

void testCrc()
{
    uint8_t const text[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    CHECK(::nvm::crc16(text, sizeof(text)) == 0x29B1U);
    CHECK(::nvm::crc32(text, sizeof(text)) == 0xCBF43926U);
    CHECK(::nvm::crc32(text, 0U) == 0U);
    CHECK(::nvm::slotBytes(4U) == 12U);
    CHECK(::nvm::slotBytes(3U) == 12U);
    CHECK(::nvm::slotBytes(8U) == 16U);
    CHECK(::nvm::slotBytes(9U) == 20U);
    CHECK(::nvm::asilCopyBytes(4U) == 16U);
    CHECK(::nvm::asilCopyBytes(5U) == 20U);
    CHECK(::nvm::asilCopyBytes(6U) == 20U);
    CHECK(::nvm::blockBytes(4U, Integrity::QM) == 24U);
    CHECK(::nvm::blockBytes(4U, Integrity::ASIL) == 32U);
    // contract with tools/rtegen NVM_QUALITY
    CHECK(static_cast<int>(Quality::NOT_READ) == 0);
    CHECK(static_cast<int>(Quality::STORED) == 1);
    CHECK(static_cast<int>(Quality::DEFAULT) == 2);
    CHECK(static_cast<int>(Quality::UNSECURED) == 3);
    CHECK(static_cast<int>(Quality::NO_MEMORY) == 4);
}

void testNoMemory()
{
    Ecu ecu(nullptr);
    for (size_t i = 0U; i < COUNT; ++i)
    {
        CHECK(ecu.status[i].state == BlockState::NO_MEMORY);
        CHECK(quality[i] == static_cast<uint8_t>(Quality::NO_MEMORY));
    }
    CHECK(counterImage == counterDefault);
    CHECK(asilImage == asilDefault);
    CHECK(std::memcmp(&recordImage, &recordDefault, sizeof(Record)) == 0);
    CHECK(std::memcmp(bytesImage, bytesDefault, 3U) == 0);
    CHECK(ecu.sink.count == 0U);
    uint32_t const value = 42U;
    Ecu::set(1U, &value);
    CHECK(ecu.manager.writeAll(10U) == 0U);
    CHECK(ecu.status[1].last == ::nvm::WriteResult::NO_MEMORY);
    CHECK(ecu.manager.isPending(1U)); // stays pending, nothing is lost silently
    CHECK(ecu.manager.mainFunction(5000U) == 0U);
}

void testErasedAndRoundTrip()
{
    Memory memory;
    {
        Ecu ecu(&memory);
        for (size_t i = 0U; i < COUNT; ++i)
        {
            CHECK(ecu.status[i].state == BlockState::EMPTY);
            CHECK(ecu.status[i].quality == Quality::DEFAULT);
            CHECK(quality[i] == static_cast<uint8_t>(Quality::DEFAULT));
            CHECK(!ecu.manager.isPending(i));
        }
        CHECK(ecu.sink.count == 0U); // an erased block is no defect
        CHECK(counterImage == counterDefault);
        CHECK(ecu.manager.layoutBytes() == (ADDR_VECTOR + (2U * COPY_VECTOR)));
        uint32_t const value = 0xCAFE0001U;
        Record const record  = {1U, 2U, {3U, 4U}};
        uint8_t const bytes[3] = {9U, 8U, 7U};
        Ecu::set(1U, &value);
        Ecu::set(2U, &record);
        Ecu::set(0U, bytes);
        CHECK(ecu.manager.writeAll(20U) == 3U);
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(ecu.status[1].slot == 0U);
        CHECK(ecu.status[1].sequence == 1U);
        CHECK(ecu.status[1].writes == 1U);
        CHECK(ecu.status[1].lastWriteMs == 20U);
        CHECK(ecu.status[1].last == ::nvm::WriteResult::OK);
        CHECK(!ecu.manager.isPending(1U));
        // record layout in slot 0 of the counter: id, size, sequence, data, crc (little endian)
        uint8_t const* const r = &memory.bytes[ADDR_COUNTER];
        CHECK((r[0] == 0x34U) && (r[1] == 0x12U) && (r[2] == 4U) && (r[3] == 0U) && (r[4] == 1U) && (r[5] == 0U));
        CHECK((r[6] == 0x01U) && (r[9] == 0xCAU));
        CHECK(::nvm::crc16(r, 10U) == static_cast<uint16_t>(r[10] | (r[11] << 8U)));
        CHECK(memory.bytes[ADDR_COUNTER + SLOT_COUNTER] == 0xFFU); // second slot untouched
    }
    {
        Ecu ecu(&memory); // restart
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(quality[1] == static_cast<uint8_t>(Quality::STORED));
        CHECK(counterImage == 0xCAFE0001U);
        CHECK(recordImage.counter == 1U);
        CHECK(recordImage.trim == 2U);
        CHECK((recordImage.flags[0] == 3U) && (recordImage.flags[1] == 4U));
        CHECK((bytesImage[0] == 9U) && (bytesImage[2] == 7U));
        CHECK(!ecu.manager.isPending(0U));
        CHECK(ecu.sink.count == 0U);
    }
    CHECK(memory.initCalls == 0U);
}

void testSlotsAndSequence()
{
    Memory memory;
    uint32_t expected = 0U;
    for (uint32_t n = 1U; n <= 5U; ++n)
    {
        Ecu ecu(&memory);
        CHECK(counterImage == ((n == 1U) ? counterDefault : expected));
        expected = 1000U + n;
        Ecu::set(1U, &expected);
        CHECK(ecu.manager.writeAll(n) == 1U);
        CHECK(ecu.status[1].sequence == n);
        CHECK(ecu.status[1].slot == static_cast<uint8_t>((n - 1U) & 1U)); // 0, 1, 0, 1, ...
    }
    {
        Ecu ecu(&memory);
        CHECK(counterImage == 1005U);
        CHECK(ecu.status[1].sequence == 5U);
        CHECK(ecu.status[1].slot == 0U);
    }
    // wrap-around: slot 1 holds 0xFFFF, slot 0 holds 0x0000 (newer)
    Memory wrap;
    {
        Ecu ecu(&wrap);
        uint32_t v = 1U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U); // slot 0, sequence 1
        ecu.status[1].sequence = 0xFFFEU;
        v = 2U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U); // slot 1, sequence 0xFFFF
        CHECK(ecu.status[1].sequence == 0xFFFFU);
        v = 3U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U); // slot 0, sequence 0x0000
        CHECK(ecu.status[1].sequence == 0U);
    }
    {
        Ecu ecu(&wrap);
        CHECK(counterImage == 3U);
        CHECK(ecu.status[1].slot == 0U);
    }
}

void testPowerLossAtEveryByte()
{
    // A record of the counter is 10 bytes (+2 padding). Stop the write after k bytes for every k: after the restart
    // the block holds the old or the new value, never the default and never a mixture.
    uint32_t const lengths = ::nvm::RECORD_OVERHEAD + 4U;
    unsigned oldSeen  = 0U;
    unsigned newSeen  = 0U;
    unsigned tornSeen = 0U;
    for (uint32_t k = 0U; k <= lengths; ++k)
    {
        Memory memory;
        {
            Ecu ecu(&memory);
            uint32_t v = 0x11111111U;
            Ecu::set(1U, &v);
            (void)ecu.manager.writeAll(0U); // slot 0
            v = 0x22222222U;
            Ecu::set(1U, &v);
            (void)ecu.manager.writeAll(0U); // slot 1
            v = 0x33333333U;                // goes to slot 0, over the oldest record
            Ecu::set(1U, &v);
            memory.powerLossAfter = k;
            bool const ok = (ecu.manager.writeAll(0U) == 1U);
            if (k >= lengths)
            {
                CHECK(ok); // enough bytes for the whole record
            }
            memory.powerLossAfter = Memory::NO_LOSS;
        }
        Ecu ecu(&memory);
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(quality[1] == static_cast<uint8_t>(Quality::STORED));
        CHECK((counterImage == 0x22222222U) || (counterImage == 0x33333333U));
        oldSeen += (counterImage == 0x22222222U) ? 1U : 0U;
        newSeen += (counterImage == 0x33333333U) ? 1U : 0U;
        // NVM-30: a torn record is reported, and then the previous value is the one loaded
        if (ecu.sink.count != 0U)
        {
            ++tornSeen;
            CHECK(counterImage == 0x22222222U);
            CHECK((ecu.sink.count == 1U) && (ecu.sink.events[0] == BlockEvent::PLACE_DEFECTIVE));
            CHECK(ecu.sink.indices[0] == 1U);
        }
    }
    CHECK(oldSeen > 0U);
    CHECK(newSeen > 0U);
    CHECK(tornSeen > 0U);
}

void testCorruptionAndLayout()
{
    Memory memory;
    {
        Ecu ecu(&memory);
        uint32_t v = 5U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U); // slot 0
        v = 6U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U); // slot 1 (newer)
    }
    memory.bytes[ADDR_COUNTER + SLOT_COUNTER + 7U] ^= 0x10U; // bit flip in the newer record
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(counterImage == 5U);
        CHECK(ecu.status[1].slot == 0U);
        CHECK(quality[1] == static_cast<uint8_t>(Quality::STORED));
        // NVM-30: QM falls back to the older value - no longer silently
        CHECK(ecu.sink.count == 1U);
        CHECK((ecu.sink.indices[0] == 1U) && (ecu.sink.events[0] == BlockEvent::PLACE_DEFECTIVE));
        // the next write goes to the damaged slot, the good one stays
        uint32_t v = 8U;
        Ecu::set(1U, &v);
        CHECK(ecu.manager.writeAll(0U) == 1U);
        CHECK(ecu.status[1].slot == 1U);
    }
    memory.bytes[ADDR_COUNTER + SLOT_COUNTER + 6U] ^= 0x01U;
    memory.bytes[ADDR_COUNTER + 6U] ^= 0x01U; // both slots damaged
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[1].state == BlockState::CORRUPT);
        CHECK(counterImage == counterDefault);
        CHECK(quality[1] == static_cast<uint8_t>(Quality::UNSECURED));
        CHECK(ecu.sink.count == 1U);
        CHECK((ecu.sink.indices[0] == 1U) && (ecu.sink.events[0] == BlockEvent::DEFAULT_LOADED));
    }
    // another block id at this address: the layout changed
    Memory foreign;
    {
        Ecu ecu(&foreign);
        uint32_t v = 9U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U);
    }
    foreign.bytes[ADDR_COUNTER] = 0x99U; // id
    {
        Ecu ecu(&foreign);
        CHECK(ecu.status[1].state == BlockState::FOREIGN);
        CHECK(counterImage == counterDefault);
        CHECK(quality[1] == static_cast<uint8_t>(Quality::UNSECURED));
        CHECK(ecu.sink.count == 0U); // a changed layout is no defect of the memory
    }
    // same id, other size (block grew)
    Memory resized;
    {
        Ecu ecu(&resized);
        uint32_t v = 9U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U);
    }
    resized.bytes[ADDR_COUNTER + 2U] = 5U;
    {
        Ecu ecu(&resized);
        CHECK(ecu.status[1].state == BlockState::FOREIGN);
    }
    // read refused: counts as damaged, default
    Memory refusing;
    refusing.refuseReads = true;
    {
        Ecu ecu(&refusing);
        CHECK(ecu.status[0].state == BlockState::CORRUPT);
        CHECK(ecu.status[ASIL].state == BlockState::CORRUPT);
        CHECK(std::memcmp(bytesImage, bytesDefault, 3U) == 0);
        CHECK(asilImage == asilDefault);
        CHECK(ecu.sink.count == COUNT);
    }
}

void testOutside()
{
    Memory memory;
    // usable size ends inside the second slot of the counter
    Ecu ecu(&memory, ADDR_COUNTER + SLOT_COUNTER + 4U);
    CHECK(ecu.status[0].state == BlockState::EMPTY);
    CHECK(ecu.status[1].state == BlockState::OUTSIDE);
    CHECK(ecu.status[2].state == BlockState::OUTSIDE);
    CHECK(ecu.status[ASIL].state == BlockState::OUTSIDE);
    CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::NO_MEMORY));
    uint32_t v = 1U;
    Ecu::set(1U, &v);
    CHECK(ecu.manager.writeAll(0U) == 0U);
    CHECK(ecu.status[1].last == ::nvm::WriteResult::NO_MEMORY);
    CHECK(memory.bytes[ADDR_COUNTER] == 0xFFU);
    // an ASIL block whose second copy ends one byte beyond the memory
    Memory small;
    Ecu edge(&small, ADDR_ASIL + (2U * COPY_ASIL) - 1U);
    CHECK(edge.status[ASIL].state == BlockState::OUTSIDE);
    Ecu fits(&small, ADDR_ASIL + (2U * COPY_ASIL));
    CHECK(fits.status[ASIL].state == BlockState::EMPTY);
}

void testOnlyDifferingBytes()
{
    Memory memory;
    Ecu ecu(&memory);
    Record r = {1U, 2U, {3U, 4U}};
    Ecu::set(2U, &r);
    (void)ecu.manager.writeAll(0U); // slot 0 complete
    (void)ecu.manager.writeAll(0U); // nothing pending: nothing written
    uint32_t const full = memory.bytesWritten;
    CHECK(full <= (::nvm::RECORD_OVERHEAD + sizeof(Record))); // bytes equal to the erased 0xFF are skipped
    CHECK(full >= (::nvm::RECORD_OVERHEAD + sizeof(Record) - 2U));
    Ecu::set(2U, &r);
    (void)ecu.manager.writeAll(0U); // slot 1, erased: complete again
    Ecu::set(2U, &r);
    uint32_t const before = memory.bytesWritten;
    (void)ecu.manager.writeAll(0U); // slot 0 again, same data: sequence and crc only
    uint32_t const delta = memory.bytesWritten - before;
    CHECK(delta > 0U);
    CHECK(delta <= 4U);
    Ecu restart(&memory);
    CHECK(restart.status[2].sequence == 3U);
    CHECK(recordImage.trim == 2U);
}

void testVerifyAndRefusedWrites()
{
    Memory memory;
    Ecu ecu(&memory);
    uint32_t v = 77U;
    Ecu::set(1U, &v);
    memory.dropWrites = true;
    CHECK(ecu.manager.writeAll(0U) == 0U);
    CHECK(ecu.status[1].last == ::nvm::WriteResult::VERIFY);
    CHECK(ecu.status[1].errors == 1U);
    CHECK(ecu.manager.isPending(1U));
    memory.dropWrites   = false;
    memory.refuseWrites = true;
    CHECK(ecu.manager.writeAll(0U) == 0U);
    CHECK(ecu.status[1].last == ::nvm::WriteResult::FAILED);
    CHECK(ecu.status[1].errors == 2U);
    memory.refuseWrites = false;
    CHECK(ecu.manager.writeAll(0U) == 1U);
    CHECK(ecu.status[1].last == ::nvm::WriteResult::OK);
    CHECK(ecu.status[1].sequence == 1U); // failed attempts do not use up a sequence
}

void testPolicies()
{
    Memory memory;
    Ecu ecu(&memory);
    uint8_t const b[3] = {1U, 1U, 1U};
    Ecu::set(0U, b); // SHUTDOWN: never by mainFunction
    CHECK(ecu.manager.mainFunction(100000U) == 0U);
    CHECK(ecu.manager.isPending(0U));
    uint32_t v = 1U;
    Record r   = {5U, 6U, {7U, 8U}};
    Ecu::set(1U, &v); // CHANGE 1000 ms
    Ecu::set(2U, &r); // CHANGE 500 ms
    // one block per call
    CHECK(ecu.manager.mainFunction(0U) == 1U);
    CHECK(ecu.manager.mainFunction(0U) == 1U);
    CHECK(ecu.manager.mainFunction(0U) == 0U);
    CHECK(ecu.status[1].writes == 1U);
    CHECK(ecu.status[2].writes == 1U);
    v = 2U;
    Ecu::set(1U, &v);
    r.trim = 60U;
    Ecu::set(2U, &r);
    CHECK(ecu.manager.mainFunction(499U) == 0U);  // both within their interval
    CHECK(ecu.manager.mainFunction(500U) == 1U);  // record: 500 ms passed
    CHECK(ecu.status[2].writes == 2U);
    CHECK(ecu.manager.mainFunction(999U) == 0U);
    CHECK(ecu.manager.mainFunction(1000U) == 1U); // counter: 1000 ms passed
    CHECK(ecu.status[1].writes == 2U);
    // writeAll takes everything pending, SHUTDOWN too
    CHECK(ecu.manager.writeAll(1001U) == 1U);
    CHECK(!ecu.manager.isPending(0U));
    CHECK(ecu.status[0].writes == 1U);
    // interval measured across the 32-bit wrap of the time
    v = 3U;
    Ecu::set(1U, &v);
    ecu.status[1].lastWriteMs = 0xFFFFFF00U;
    CHECK(ecu.manager.mainFunction(0x00000100U) == 0U); // 512 ms after the wrap
    CHECK(ecu.manager.mainFunction(0x000002E8U) == 1U); // 1000 ms
}

void testLock()
{
    Memory memory;
    int const before = TestLock::count;
    Ecu ecu(&memory);
    CHECK(TestLock::count >= (before + static_cast<int>(2U * COUNT))); // image and quality under the lock
    uint32_t v = 4U;
    Ecu::set(1U, &v);
    int const beforeWrite = TestLock::count;
    (void)ecu.manager.writeAll(0U);
    CHECK(TestLock::count > beforeWrite);
    CHECK(TestLock::depth == 0);
}

// ------------------------------------------------------------------------------------------------ ASIL (NVM-18)

void testAsilFormat()
{
    Memory memory;
    writeAsil(memory, 3U);
    // both copies hold the same record: id, size, counter 3, value 103, CRC-32 over the rest
    uint8_t const* const a = &memory.bytes[ADDR_ASIL];
    CHECK((a[0] == 0x67U) && (a[1] == 0x45U) && (a[2] == 4U) && (a[3] == 0U));
    CHECK(get32(&a[4]) == 3U);
    CHECK(get32(&a[8]) == 103U);
    CHECK(::nvm::crc32(a, ::nvm::ASIL_HEADER + 4U) == get32(&a[12]));
    CHECK(copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL));
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[ASIL].state == BlockState::VALID);
        CHECK(ecu.status[ASIL].sequence == 3U);
        CHECK(ecu.status[ASIL].repairs == 0U);
        CHECK(asilImage == 103U);
        CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::STORED));
        CHECK(ecu.sink.count == 0U);
        // a vector block (3 x uint16)
        uint16_t const v[3] = {1U, 2U, 0xBEEFU};
        Ecu::set(VECTOR, v);
        CHECK(ecu.manager.writeAll(5U) == 1U);
        CHECK(ecu.status[VECTOR].writes == 1U);
        CHECK(copiesEqual(memory, ADDR_VECTOR, COPY_VECTOR, ::nvm::ASIL_OVERHEAD + 6U));
        // only the ASIL block's own area is written: the QM blocks stay erased
        CHECK(memory.bytes[ADDR_RECORD] == 0xFFU);
    }
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[VECTOR].state == BlockState::VALID);
        CHECK((vectorImage[0] == 1U) && (vectorImage[2] == 0xBEEFU));
    }
}

void testAsilOneCopyDefect()
{
    // bit flip in the data of copy A, then of copy B; an erased copy; another id in one copy
    uint32_t const damage[4] = {ADDR_ASIL + 9U, ADDR_ASIL + COPY_ASIL + 13U, 0U, 0U};
    for (unsigned c = 0U; c < 4U; ++c)
    {
        Memory memory;
        writeAsil(memory, 2U);
        if (c < 2U)
        {
            memory.bytes[damage[c]] ^= 0x04U;
        }
        else if (c == 2U)
        {
            std::memset(&memory.bytes[ADDR_ASIL + COPY_ASIL], 0xFF, COPY_ASIL);
        }
        else
        {
            memory.bytes[ADDR_ASIL] = 0x11U; // id of copy A
        }
        {
            Ecu ecu(&memory);
            CHECK(ecu.status[ASIL].state == BlockState::REPAIRED);
            CHECK(asilImage == 102U);
            CHECK(ecu.status[ASIL].sequence == 2U);
            CHECK(ecu.status[ASIL].repairs == 1U);
            CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::STORED));
            CHECK(ecu.sink.count == 1U);
            CHECK((ecu.sink.indices[0] == ASIL) && (ecu.sink.events[0] == BlockEvent::COPY_REPAIRED));
            CHECK(copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL)); // repaired at once
        }
        Ecu again(&memory);
        CHECK(again.status[ASIL].state == BlockState::VALID);
        CHECK(again.sink.count == 0U);
        CHECK(asilImage == 102U);
    }
    // same counter, both CRCs good, other data in copy B: compared, copy A wins, B is rewritten
    Memory memory;
    writeAsil(memory, 2U);
    uint8_t* const b = &memory.bytes[ADDR_ASIL + COPY_ASIL];
    b[8] = 0x77U;
    uint32_t const crc = ::nvm::crc32(b, ::nvm::ASIL_HEADER + 4U);
    b[12] = static_cast<uint8_t>(crc);
    b[13] = static_cast<uint8_t>(crc >> 8U);
    b[14] = static_cast<uint8_t>(crc >> 16U);
    b[15] = static_cast<uint8_t>(crc >> 24U);
    Ecu ecu(&memory);
    CHECK(ecu.status[ASIL].state == BlockState::REPAIRED);
    CHECK(asilImage == 102U);
    CHECK(ecu.sink.count == 1U);
    CHECK(copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL));
}

void testAsilBothDefect()
{
    Memory memory;
    writeAsil(memory, 2U);
    memory.bytes[ADDR_ASIL + 8U] ^= 0x01U;
    memory.bytes[ADDR_ASIL + COPY_ASIL + 8U] ^= 0x80U;
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[ASIL].state == BlockState::CORRUPT);
        CHECK(asilImage == asilDefault);
        CHECK(ecu.status[ASIL].quality == Quality::UNSECURED);
        CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::UNSECURED));
        CHECK(ecu.sink.count == 1U);
        CHECK((ecu.sink.indices[0] == ASIL) && (ecu.sink.events[0] == BlockEvent::DEFAULT_LOADED));
        CHECK(ecu.manager.isPending(ASIL) == false);
        // the next write stores both copies again
        uint32_t const v = 0x600DU;
        Ecu::set(ASIL, &v);
        CHECK(ecu.manager.writeAll(0U) == 1U);
        CHECK(copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL));
    }
    Ecu ecu(&memory);
    CHECK(ecu.status[ASIL].state == BlockState::VALID);
    CHECK(asilImage == 0x600DU);
    CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::STORED));
}

void testAsilStaleCopy()
{
    // a write interrupted after the first copy: counters 3 and 2, both valid - the newer wins, no event
    Memory memory;
    writeAsil(memory, 2U);
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[ASIL].slot == 0U); // both equal: A counts as the winner, the write starts with B
        uint32_t const v = 103U;
        Ecu::set(ASIL, &v);
        memory.lossFrom = ADDR_ASIL; // the power goes when the write reaches copy A
        memory.lossTo   = ADDR_ASIL + COPY_ASIL;
        CHECK(ecu.manager.writeAll(0U) == 0U);
        memory.lossTo = 0U;
        memory.dead   = false;
    }
    CHECK(!copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL));
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[ASIL].state == BlockState::VALID);
        CHECK(asilImage == 103U);
        CHECK(ecu.status[ASIL].sequence == 3U);
        CHECK(ecu.status[ASIL].repairs == 1U);
        CHECK(ecu.sink.count == 0U);
        CHECK(copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL));
    }
}

void testAsilPowerLossAtEveryByte()
{
    // Both copies are 16 bytes. Stop the third write after k bytes, for every k up to both records: after the
    // restart the block holds the old or the new value - never the default, never a lost value.
    unsigned oldSeen = 0U;
    unsigned newSeen = 0U;
    for (uint32_t k = 0U; k <= (2U * LEN_ASIL); ++k)
    {
        Memory memory;
        writeAsil(memory, 2U); // both copies: counter 2, value 102
        {
            Ecu ecu(&memory);
            uint32_t const v = 0x33333333U;
            Ecu::set(ASIL, &v);
            memory.powerLossAfter = k;
            (void)ecu.manager.writeAll(0U);
            memory.powerLossAfter = Memory::NO_LOSS;
        }
        Ecu ecu(&memory);
        bool const stored = (ecu.status[ASIL].state == BlockState::VALID)
                            || (ecu.status[ASIL].state == BlockState::REPAIRED);
        CHECK(stored);
        CHECK((asilImage == 102U) || (asilImage == 0x33333333U));
        CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::STORED));
        for (size_t e = 0U; (e < ecu.sink.count) && (e < 8U); ++e)
        {
            CHECK(ecu.sink.events[e] != BlockEvent::DEFAULT_LOADED);
        }
        CHECK(copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL)); // brought together at the start
        oldSeen += (asilImage == 102U) ? 1U : 0U;
        newSeen += (asilImage == 0x33333333U) ? 1U : 0U;
    }
    CHECK(oldSeen > 0U);
    CHECK(newSeen > 0U);
}

void testAsilRepairFailsAndWriteOrder()
{
    Memory memory;
    writeAsil(memory, 2U);
    memory.bytes[ADDR_ASIL + 9U] ^= 0x01U; // copy A defective
    {
        memory.refuseWrites = true;
        Ecu ecu(&memory);
        memory.refuseWrites = false;
        CHECK(ecu.status[ASIL].state == BlockState::DEGRADED);
        CHECK(asilImage == 102U);
        CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::STORED));
        CHECK(ecu.status[ASIL].errors == 1U);
        CHECK(ecu.sink.count == 1U);
        CHECK(ecu.sink.events[0] == BlockEvent::COPY_REPAIR_FAILED);
        CHECK(ecu.status[ASIL].slot == 1U); // the value is in copy B
        // the next write goes to the defective copy A first: a power loss after A leaves B untouched
        uint32_t const v = 0x4242U;
        Ecu::set(ASIL, &v);
        memory.lossFrom = ADDR_ASIL + COPY_ASIL; // the power goes when the write reaches copy B
        memory.lossTo   = ADDR_ASIL + (2U * COPY_ASIL);
        CHECK(ecu.manager.writeAll(0U) == 0U);
        memory.lossTo = 0U;
        memory.dead   = false;
        CHECK(get32(&memory.bytes[ADDR_ASIL + 8U]) == 0x4242U);            // A new
        CHECK(get32(&memory.bytes[ADDR_ASIL + COPY_ASIL + 8U]) == 102U);   // B old
        CHECK(ecu.status[ASIL].slot == 0U);
        CHECK(ecu.manager.isPending(ASIL));
        // the retry starts with B (A holds the newest value now)
        memory.refuseWrites = true;
        CHECK(ecu.manager.writeAll(0U) == 0U);
        memory.refuseWrites = false;
        CHECK(get32(&memory.bytes[ADDR_ASIL + 8U]) == 0x4242U);
        CHECK(ecu.manager.writeAll(0U) == 1U);
        CHECK(copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL));
        CHECK(ecu.status[ASIL].sequence == 4U);
    }
    Ecu ecu(&memory);
    CHECK(ecu.status[ASIL].state == BlockState::VALID);
    CHECK(asilImage == 0x4242U);
    // a refused first copy leaves the other copy as it was
    uint32_t const v = 0x5151U;
    Ecu::set(ASIL, &v);
    memory.dropWrites = true;
    CHECK(ecu.manager.writeAll(0U) == 0U);
    memory.dropWrites = false;
    CHECK(ecu.status[ASIL].last == ::nvm::WriteResult::VERIFY);
    Ecu restart(&memory);
    CHECK(asilImage == 0x4242U);
}

void testAsilEmptyAndForeign()
{
    Memory memory;
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[ASIL].state == BlockState::EMPTY);
        CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::DEFAULT));
        CHECK(ecu.sink.count == 0U);
    }
    // a QM record of the same block at this place (the integrity of the block changed): the id differs by format
    Memory changedFormat;
    writeAsil(changedFormat, 1U);
    changedFormat.bytes[ADDR_ASIL] = 0x01U;
    changedFormat.bytes[ADDR_ASIL + COPY_ASIL] = 0x01U;
    Ecu ecu(&changedFormat);
    CHECK(ecu.status[ASIL].state == BlockState::FOREIGN);
    CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::UNSECURED));
    CHECK(ecu.sink.count == 0U);
    CHECK(asilImage == asilDefault);
}

void testFaultInjection()
{
    Memory memory;
    writeAsil(memory, 2U);
    {
        Ecu ecu(&memory);
        CHECK(ecu.manager.injectCopyFault(ASIL, 1U));
        CHECK(!ecu.manager.injectCopyFault(ASIL, 2U));
        CHECK(!copiesEqual(memory, ADDR_ASIL, COPY_ASIL, LEN_ASIL));
        // QM: the newer slot damaged, the older value is taken
        uint32_t v = 1U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U); // slot 0
        v = 2U;
        Ecu::set(1U, &v);
        (void)ecu.manager.writeAll(0U); // slot 1
        CHECK(ecu.manager.injectCopyFault(1U, 1U));
    }
    {
        Ecu ecu(&memory);
        CHECK(ecu.status[ASIL].state == BlockState::REPAIRED);
        CHECK(ecu.sink.count == 2U); // COPY_REPAIRED (ASIL) and PLACE_DEFECTIVE (QM, NVM-30)
        CHECK((ecu.sink.indices[0] == 1U) && (ecu.sink.events[0] == BlockEvent::PLACE_DEFECTIVE));
        CHECK(counterImage == 1U);
        // both ASIL copies: the default, event, UNSECURED
        CHECK(ecu.manager.injectCopyFault(ASIL, 0U));
        CHECK(ecu.manager.injectCopyFault(ASIL, 1U));
    }
    Ecu ecu(&memory);
    CHECK(ecu.status[ASIL].state == BlockState::CORRUPT);
    CHECK(quality[ASIL] == static_cast<uint8_t>(Quality::UNSECURED));
    Ecu none(nullptr);
    CHECK(!none.manager.injectCopyFault(ASIL, 0U));
}

/**
 * NVM-30, board 2026-10-02: EcuStatistics (QM) held seq 17 in place A and seq 18 (the newest) in B; "nvm inject
 * EcuStatistics a" damaged A, the next start loaded B (seq 18), its start write (seq 19) went to A - no event. A is
 * the previous record, so nothing was lost, but the defect has to be reported all the same: from a defective place it
 * cannot be told whether it was the previous record or a newer one (newest record damaged, torn write).
 */
void testQmOnePlace()
{
    Memory memory;
    {
        Ecu ecu(&memory);
        for (uint32_t v = 17U; v <= 18U; ++v)
        {
            Ecu::set(1U, &v);
            CHECK(ecu.manager.writeAll(v) == 1U); // seq 1 in A (17), seq 2 in B (18)
        }
        CHECK(ecu.status[1].slot == 1U);
        CHECK(ecu.manager.injectCopyFault(1U, 0U)); // nvm inject Counter a: the previous record
    }
    {
        Ecu ecu(&memory);
        CHECK(counterImage == 18U); // the newest value, nothing lost
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(ecu.status[1].slot == 1U);
        CHECK(ecu.status[1].sequence == 2U);
        CHECK(quality[1] == static_cast<uint8_t>(Quality::STORED));
        CHECK((ecu.sink.count == 1U) && (ecu.sink.events[0] == BlockEvent::PLACE_DEFECTIVE));
        CHECK(std::strcmp(ecu.sink.lastName, "Counter") == 0);
        // the start write of the board (seq 3) goes to the defective place A and replaces it
        uint32_t const v = 19U;
        Ecu::set(1U, &v);
        CHECK(ecu.manager.writeAll(30U) == 1U);
        CHECK(ecu.status[1].slot == 0U);
        CHECK(ecu.status[1].sequence == 3U);
    }
    {
        Ecu ecu(&memory); // both places good again: no event
        CHECK(counterImage == 19U);
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(ecu.sink.count == 0U);
        CHECK(ecu.manager.injectCopyFault(1U, 0U)); // now the newest record (seq 3 in A)
    }
    {
        Ecu ecu(&memory);
        CHECK(counterImage == 18U); // the previous value - reported, not silent
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(ecu.status[1].slot == 1U);
        CHECK((ecu.sink.count == 1U) && (ecu.sink.events[0] == BlockEvent::PLACE_DEFECTIVE));
    }
    // the other place cannot be read: reported the same way
    memory.refuseReadFrom = ADDR_COUNTER;
    memory.refuseReadTo   = ADDR_COUNTER + 1U;
    {
        Ecu ecu(&memory);
        CHECK(counterImage == 18U);
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK((ecu.sink.count == 1U) && (ecu.sink.events[0] == BlockEvent::PLACE_DEFECTIVE));
    }
    memory.refuseReadTo = 0U;
    // a foreign record next to a valid one: the layout changed and only one write since - no defect, no event
    Memory moved;
    {
        Ecu ecu(&moved);
        uint32_t const v = 5U;
        Ecu::set(1U, &v);
        CHECK(ecu.manager.writeAll(0U) == 1U); // A
    }
    moved.bytes[ADDR_COUNTER + SLOT_COUNTER]      = 0x77U; // B: id of another block
    moved.bytes[ADDR_COUNTER + SLOT_COUNTER + 1U] = 0x66U;
    moved.bytes[ADDR_COUNTER + SLOT_COUNTER + 2U] = 0x04U;
    moved.bytes[ADDR_COUNTER + SLOT_COUNTER + 3U] = 0x00U;
    {
        Ecu ecu(&moved);
        CHECK(counterImage == 5U);
        CHECK(ecu.status[1].state == BlockState::VALID);
        CHECK(ecu.sink.count == 0U);
    }
    // the chosen place changes between the two reads of the start: defective, the default and DEFAULT_LOADED - not
    // "erased" (quality DEFAULT, no event) as before NVM-30. Reads of the start: Bytes A, B, then Counter A, B and
    // Counter A again (the chosen place).
    Memory changing;
    {
        Ecu ecu(&changing);
        uint32_t const v = 6U;
        Ecu::set(1U, &v);
        CHECK(ecu.manager.writeAll(0U) == 1U); // A only, B erased
    }
    changing.readCalls   = 0U;
    changing.flipAtRead  = 5U;
    changing.flipAddress = ADDR_COUNTER + 6U;
    {
        Ecu ecu(&changing);
        CHECK(changing.readCalls >= 5U);
        CHECK(ecu.status[1].state == BlockState::CORRUPT);
        CHECK(counterImage == counterDefault);
        CHECK(quality[1] == static_cast<uint8_t>(Quality::UNSECURED));
        CHECK((ecu.sink.count == 1U) && (ecu.sink.events[0] == BlockEvent::DEFAULT_LOADED));
        CHECK(ecu.sink.indices[0] == 1U);
    }
}

void testNames()
{
    CHECK(std::strcmp(::nvm::stateName(BlockState::REPAIRED), "stored (copy repaired)") == 0);
    CHECK(std::strcmp(::nvm::stateName(BlockState::DEGRADED), "stored (one copy)") == 0);
    CHECK(std::strcmp(::nvm::integrityName(Integrity::ASIL), "asil") == 0);
    CHECK(::nvm::qualityOf(BlockState::NOT_READ) == Quality::NOT_READ);
    CHECK(::nvm::qualityOf(BlockState::DEGRADED) == Quality::STORED);
    CHECK(::nvm::qualityOf(BlockState::OUTSIDE) == Quality::NO_MEMORY);
}
} // namespace

int main()
{
    testCrc();
    testNoMemory();
    testErasedAndRoundTrip();
    testSlotsAndSequence();
    testPowerLossAtEveryByte();
    testCorruptionAndLayout();
    testOutside();
    testOnlyDifferingBytes();
    testVerifyAndRefusedWrites();
    testPolicies();
    testLock();
    testAsilFormat();
    testAsilOneCopyDefect();
    testAsilBothDefect();
    testAsilStaleCopy();
    testAsilPowerLossAtEveryByte();
    testAsilRepairFailsAndWriteOrder();
    testAsilEmptyAndForeign();
    testFaultInjection();
    testQmOnePlace();
    testNames();
    std::printf("NvmHostTest: %u checks, %u failures\n", checks, failures);
    return (failures == 0U) ? 0 : 1;
}
