/* ARCH-52: host test of the generated module binding with the synthetic fourth driver (tools/bswgen/test/binding).
 *
 * Compiled by tools/bswgen/test_bswgen_binding.py against the generat of testproject_binding: the generated block
 * integration/moduleBinding.h, the trait of the synthetic module, the framework of the family integration
 * (ModuleBinding.h: the safe_off adapter) and the host binders of this directory instead of a family's. Nothing of
 * the synthetic driver is written in a family port. Checks what the generated block promises:
 * - lifecycle registration in the level and task of the manifest, named after the console group, in board order;
 * - the configuration of each instance from its own parameters (types: unsigned, signed, choice);
 * - pins, optional pin, analog channel and SPI bus of each instance are its own (nothing swapped);
 * - the safe_off adapters: debug halt switch-off, explicit enable, lock of the safe state, the lock asked per instance;
 * - the RTE reader: a snapshot per instance, "not started" before the system exists. */

#include <new> // placement new of etl::typed_storage (the target gets it through the OpenBSW headers)

#include "ModuleBinders.h"
#include "ModuleBinding.h"

#include <cstdio>
#include <cstring>

namespace host
{
Board board{};
bool safeStateLatched = false;
uint32_t lockRequests = 0U;
uint32_t pwmBoundRequests = 0U;

bool testLock() { return safeStateLatched; }
} // namespace host

namespace integration
{
namespace binding
{
// the family integration's lock provider (Integration.cpp on the target)
SafeOffLock safeOffLock(bool const pwmBound)
{
    ++host::lockRequests;
    if (pwmBound)
    {
        ++host::pwmBoundRequests;
    }
    return &host::testLock;
}
} // namespace binding
} // namespace integration

#include "integration/moduleBinding.h"

namespace
{
int failures = 0;
int checks   = 0;

void check(bool const ok, char const* const what, int const line)
{
    ++checks;
    if (!ok)
    {
        ++failures;
        std::printf("FAIL line %d: %s\n", line, what);
    }
}

#define CHECK(expr) check((expr), #expr, __LINE__)

bool same(char const* a, char const* b) { return std::strcmp(a, b) == 0; }

using host::board;
using host::index;
using host::Pin;
namespace b = ::integration::binding;

::integration::IPowerStageOutput* const safeOff[] = {NX_BINDING_SAFE_OFF_OUTPUT_REFS};
} // namespace

int main()
{
    static_assert(NX_BINDING_INSTANCE_COUNT == 2, "testproject_binding binds two instances");
    static_assert(NX_BINDING_SAFE_OFF_COUNT == 2, "one safe_off adapter per instance");
    static_assert(sizeof(safeOff) / sizeof(safeOff[0]) == 2U, "refs of both adapters");

    // --- before the lifecycle: no system, the RTE reports "not started", no adapter holds anything
    ::synth::Reading reading{7U, 7U};
    ::integration::rte::readSynth_SYN_A(reading);
    CHECK((reading.value == 0U) && (reading.quality == 1U));
    CHECK(!b::SYN_A_SYSTEM.has_value() && !b::SYN_B_SYSTEM.has_value());
    CHECK(!safeOff[0]->holdForSafeState());      // not attached yet: the hold would report it
    safeOff[0]->switchOffAfterDebugHalt();       // harmless without a system
    CHECK(!safeOff[0]->enabledSinceDebugHaltSwitchOff());

    // --- lifecycle: level 1 of the manifest, TASK_BSP, component = console group, board order
    ::lifecycle::LifecycleManager manager;
    b::addComponents(manager, 2U);
    CHECK(manager.count() == 0U);
    b::addComponents(manager, 1U);
    CHECK(manager.count() == 2U);
    CHECK(same(manager.entry(0).name, "syna") && same(manager.entry(1).name, "synb"));
    CHECK((manager.entry(0).level == 1U) && (manager.entry(1).level == 1U));
    CHECK(b::SYN_A_SYSTEM.has_value() && b::SYN_B_SYSTEM.has_value());
    ::synth::SynthSystem& a = *b::SYN_A_SYSTEM;
    ::synth::SynthSystem& bb = *b::SYN_B_SYSTEM;
    CHECK(manager.entry(0).component == &a);
    CHECK(manager.entry(1).component == &bb);
    CHECK((a.context() == TASK_BSP) && (bb.context() == TASK_BSP));
    CHECK(same(a.command(), "syna") && same(a.label(), "SYNA"));
    CHECK(same(bb.command(), "synb") && same(bb.label(), "SYNB"));
    CHECK((host::lockRequests == 2U) && (host::pwmBoundRequests == 0U));

    // --- configuration from each instance's own parameters (project values and module defaults)
    CHECK(a.driver().config().cycleMs == 20U);
    CHECK(a.driver().config().offsetC == -5);
    CHECK(a.driver().config().fast);
    CHECK(bb.driver().config().cycleMs == 10U);
    CHECK(bb.driver().config().offsetC == 0);
    CHECK(!bb.driver().config().fast);

    // --- SPI: each instance on its own board bus and chip select, with the facts of the trait
    CHECK((b::SYN_A_SPI.bus().bus == host::Bus::LPSPI1) && (b::SYN_A_SPI.bus().pcs == 0U));
    CHECK((b::SYN_B_SPI.bus().bus == host::Bus::LPSPI0) && (b::SYN_B_SPI.bus().pcs == 1U));
    CHECK((b::SYN_A_SPI.frameBits() == 16U) && (b::SYN_A_SPI.maxHz() == 2000000U));
    b::SYN_A_SPI.answer = ::synth::DEVICE_ID;
    b::SYN_B_SPI.answer = 0x1234U; // wrong device
    board.level[index(Pin::SYN_A_EN)] = true;
    manager.entry(0).component->init();
    manager.entry(1).component->init();
    CHECK((b::SYN_A_SPI.transfers() == 1U) && (b::SYN_A_SPI.lastTx() == ::synth::ID_FRAME));
    CHECK(b::SYN_B_SPI.transfers() == 1U);
    CHECK(!board.level[index(Pin::SYN_A_EN)]); // init writes the enable off

    // --- pins: each instance its own enable; the optional fault only where the board wires it
    CHECK(a.driver().enable());
    CHECK(board.level[index(Pin::SYN_A_EN)] && !board.level[index(Pin::SYN_B_EN)]);
    CHECK(!bb.driver().enable()); // wrong device: refused
    b::SYN_B_SPI.answer = ::synth::DEVICE_ID;
    bb.init();
    board.level[index(Pin::SYN_A_FAULT)] = true; // A's fault input active: B has none wired and must not see it
    CHECK(bb.driver().enable());
    CHECK(board.level[index(Pin::SYN_B_EN)]);
    a.driver().disable();
    CHECK(!a.driver().enable()); // A sees its own fault
    board.level[index(Pin::SYN_A_FAULT)] = false;
    CHECK(a.driver().enable());

    // --- analog: each instance its own channel and characteristic
    board.raw[index(Pin::SYN_A_SENSE)] = 100U;
    board.raw[index(Pin::SYN_B_SENSE)] = 100U;
    manager.entry(0).component->run();
    manager.entry(1).component->run();
    ::integration::rte::readSynth_SYN_A(reading);
    CHECK((reading.value == 195U) && (reading.quality == 0U)); // 2 * 100 - 5
    ::integration::rte::readSynth_SYN_B(reading);
    CHECK((reading.value == 1200U) && (reading.quality == 0U)); // 2 * 100 + 1000

    // --- safe_off: debug halt switch-off, then only an explicit enable releases
    uint32_t const writesA = board.writes[index(Pin::SYN_A_EN)];
    for (auto* output : safeOff)
    {
        output->switchOffAfterDebugHalt();
    }
    CHECK(!board.level[index(Pin::SYN_A_EN)] && !board.level[index(Pin::SYN_B_EN)]);
    CHECK(board.writes[index(Pin::SYN_A_EN)] > writesA);
    CHECK(!safeOff[0]->enabledSinceDebugHaltSwitchOff() && !safeOff[1]->enabledSinceDebugHaltSwitchOff());
    manager.entry(0).component->run(); // the driver's own cycle is no enable
    CHECK(!safeOff[0]->enabledSinceDebugHaltSwitchOff());
    CHECK(a.driver().enable());
    CHECK(safeOff[0]->enabledSinceDebugHaltSwitchOff() && !safeOff[1]->enabledSinceDebugHaltSwitchOff());

    // --- safe state: the lock attached by the generated block holds both outputs off
    CHECK(!safeOff[0]->holdForSafeState()); // lock attached, not set: nothing locked
    host::safeStateLatched = true;
    CHECK(safeOff[0]->holdForSafeState() && safeOff[1]->holdForSafeState());
    CHECK(!board.level[index(Pin::SYN_A_EN)] && !board.level[index(Pin::SYN_B_EN)]);
    CHECK(!a.driver().enable() && !bb.driver().enable());
    CHECK(!board.level[index(Pin::SYN_A_EN)] && !board.level[index(Pin::SYN_B_EN)]);
    host::safeStateLatched = false;
    CHECK(bb.driver().enable() && board.level[index(Pin::SYN_B_EN)] && !board.level[index(Pin::SYN_A_EN)]);

    // --- shutdown switches off
    manager.entry(1).component->shutdown();
    CHECK(!board.level[index(Pin::SYN_B_EN)]);

    std::printf("module binding host test: %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
