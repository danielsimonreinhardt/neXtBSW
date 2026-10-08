/* SYS-44: host test of nvm::FlashWriteBracket (ecu/application/src/systems/nvm/FlashWriteBracket.h) - the NvM memory
 * with every write bracketed for the ROM check (SM-14). Storage and family neutral: the scope is a counting RAII type
 * of this test (on the S32K1 it is safety::FlashWriteScope, the indicator of safeMemory's RomCheck).
 *
 * What it shows:
 * - write() and init() of the wrapped memory run inside exactly one open scope each, read() in none;
 * - arguments and return codes pass through unchanged (BSP_OK and BSP_ERROR), the data reaches the memory;
 * - the scope is closed again when the memory refuses (no write left open, which would keep the ROM check off);
 * - the NvM core (nvm::Manager of libs/bsw/nvm, the one the application uses since NVM-19) writing a block through
 *   the bracket opens one scope per memory write and none for its reads, so a ROM-check pass is only suspended while
 *   the flash is written - for a QM block, and (NVM-19) for an ASIL block with both copies and the repair of a
 *   defective copy at the start.
 *
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Werror \
 *       -Iecu/application/src/systems -Iecu/application/src/systems/test/host -Ilibs/bsw/nvm/include \
 *       -isystem external/openbsw/libs/bsw/bsp/include \
 *       ecu/application/src/systems/test/host/FlashWriteBracketHostTest.cpp -o build/host/flashwritebracket.exe
 */

#include "nvm/FlashWriteBracket.h"

#include <nvm/Nvm.h>

#include "FakeEeprom.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

namespace
{
int checks   = 0;
int failures = 0;

#define CHECK(cond)                                                     \
    do                                                                  \
    {                                                                   \
        ++checks;                                                       \
        if (!(cond))                                                    \
        {                                                               \
            ++failures;                                                 \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                               \
    } while (false)

int openScopes  = 0;
int scopesTotal = 0;

struct CountingScope
{
    CountingScope()
    {
        ++openScopes;
        ++scopesTotal;
    }

    ~CountingScope() { --openScopes; }
};

/** The memory records how many scopes were open during each call. */
struct RecordingMemory : ::eeprom::IEepromDriver
{
    FakeEeprom<256U> inner;
    int scopesAtWrite = -1, scopesAtRead = -1, scopesAtInit = -1;
    int writes = 0, reads = 0;
    ::bsp::BspReturnCode initResult = ::bsp::BSP_OK;

    ::bsp::BspReturnCode init() override
    {
        scopesAtInit = openScopes;
        return initResult;
    }

    ::bsp::BspReturnCode write(uint32_t const address, uint8_t const* const buffer, uint32_t const length) override
    {
        scopesAtWrite = openScopes;
        ++writes;
        CHECK(openScopes == 1);
        return inner.write(address, buffer, length);
    }

    ::bsp::BspReturnCode read(uint32_t const address, uint8_t* const buffer, uint32_t const length) override
    {
        scopesAtRead = openScopes;
        ++reads;
        CHECK(openScopes == 0);
        return inner.read(address, buffer, length);
    }
};

void testWriteAndInitAreBracketedReadIsNot()
{
    RecordingMemory memory;
    ::nvm::FlashWriteBracket<CountingScope> bracket(memory);
    ::eeprom::IEepromDriver& driver = bracket;

    uint8_t const data[4] = {1U, 2U, 3U, 4U};
    CHECK(driver.write(8U, data, 4U) == ::bsp::BSP_OK);
    CHECK(memory.scopesAtWrite == 1);
    CHECK(openScopes == 0);
    uint8_t back[4] = {};
    CHECK(driver.read(8U, back, 4U) == ::bsp::BSP_OK);
    CHECK(memory.scopesAtRead == 0);
    CHECK(std::memcmp(back, data, 4U) == 0);
    CHECK(driver.init() == ::bsp::BSP_OK);
    CHECK(memory.scopesAtInit == 1);
    memory.initResult = ::bsp::BSP_ERROR;
    CHECK(driver.init() == ::bsp::BSP_ERROR);
    CHECK(openScopes == 0);
}

void testRefusedWriteClosesTheScope()
{
    RecordingMemory memory;
    ::nvm::FlashWriteBracket<CountingScope> bracket(memory);
    memory.inner.refuseWrites = true;
    uint8_t const data[2] = {0xAAU, 0x55U};
    CHECK(bracket.write(0U, data, 2U) == ::bsp::BSP_ERROR);
    CHECK(memory.scopesAtWrite == 1);
    CHECK(openScopes == 0);
    CHECK(bracket.write(300U, data, 2U) == ::bsp::BSP_ERROR); // outside: the inner memory refuses
    CHECK(openScopes == 0);
}

/** Lock of the RTE for the NvM core; checks that no memory write runs under it (and no scope is open under it). */
struct NoLock
{
    NoLock() { CHECK(openScopes == 0); }

    ~NoLock() { CHECK(openScopes == 0); }
};

void testNvmWritesOnlyInsideTheBracket()
{
    RecordingMemory memory;
    ::nvm::FlashWriteBracket<CountingScope> bracket(memory);

    uint32_t image         = 0x11223344U;
    uint32_t const initial = 0U;
    bool changed           = false;
    ::nvm::BlockConfig const blocks[1]
        = {{"Counter", 0x1234U, 4U, 0U, &image, &initial, &changed, ::nvm::WritePolicy::SHUTDOWN, 0U}};
    ::nvm::BlockStatus status[1];
    uint8_t scratch[::nvm::slotBytes(4U)];
    ::nvm::Manager<NoLock> manager(blocks, status, 1U, scratch, sizeof(scratch));
    manager.attach(&bracket, 256U);
    manager.readAll();
    CHECK(memory.reads > 0);
    CHECK(memory.writes == 0);
    int const before = scopesTotal;
    image   = 0xCAFEF00DU;
    changed = true;
    CHECK(manager.writeAll(1000U) == 1U);
    CHECK(memory.writes > 0);
    CHECK(scopesTotal - before == memory.writes); // one scope per memory write, none for the read-back
    CHECK(openScopes == 0);
}

/**
 * NVM-19: an ASIL block (two copies) through the bracket - its write puts a record into both copies, and the repair of
 * a defective copy at the start is a write as well: every one of them inside one scope, none for reading/comparing.
 */
void testAsilWritesAndRepairOnlyInsideTheBracket()
{
    RecordingMemory memory;
    ::nvm::FlashWriteBracket<CountingScope> bracket(memory);

    uint32_t image         = 0x11223344U;
    uint32_t const initial = 0U;
    bool changed           = false;
    ::nvm::BlockConfig const blocks[1] = {{"Counter",
                                           0x2345U,
                                           4U,
                                           0U,
                                           &image,
                                           &initial,
                                           &changed,
                                           ::nvm::WritePolicy::SHUTDOWN,
                                           0U,
                                           ::nvm::Integrity::ASIL,
                                           nullptr}};
    ::nvm::BlockStatus status[1];
    uint8_t scratch[::nvm::asilCopyBytes(4U)];
    ::nvm::Manager<NoLock> manager(blocks, status, 1U, scratch, sizeof(scratch));
    manager.attach(&bracket, 256U);
    manager.readAll();
    CHECK(memory.writes == 0);
    int before = scopesTotal;
    image      = 0xCAFEF00DU;
    changed    = true;
    CHECK(manager.writeAll(1000U) == 1U);
    int const writesOfBoth = memory.writes;
    CHECK(writesOfBoth >= 2); // both copies
    CHECK(scopesTotal - before == writesOfBoth);
    // damage the checksum of copy A behind the bracket, then start again: the repair writes inside the bracket
    uint32_t const crcA = ::nvm::ASIL_HEADER + 4U;
    uint8_t value       = 0U;
    CHECK(memory.inner.read(crcA, &value, 1U) == ::bsp::BSP_OK);
    value = static_cast<uint8_t>(value ^ 0x01U);
    CHECK(memory.inner.write(crcA, &value, 1U) == ::bsp::BSP_OK);
    before = scopesTotal;
    manager.readAll();
    CHECK(status[0].state == ::nvm::BlockState::REPAIRED);
    CHECK(image == 0xCAFEF00DU);
    CHECK(memory.writes > writesOfBoth);
    CHECK(scopesTotal - before == memory.writes - writesOfBoth);
    CHECK(openScopes == 0);
}
} // namespace

int main()
{
    testWriteAndInitAreBracketedReadIsNot();
    testRefusedWriteClosesTheScope();
    testNvmWritesOnlyInsideTheBracket();
    testAsilWritesAndRepairOnlyInsideTheBracket();

    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
