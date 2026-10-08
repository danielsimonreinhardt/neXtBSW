/* NVM-19: host test profile in place of the generated RteConfig.h (tools/rtegen) for the NvM integration
 * (NvmRteBlocks.h, NvmEvents.h). Same shape as the generated header (rte::NvmBlock with integrity and quality of
 * NVM-18), deliberately not the eval board: three blocks - Trim (uint16, qm, change), Counter (uint32, asil_b, change)
 * and Table (uint8[6], asil_d, shutdown) - on their own addresses, no fault memory blocks. The tables and
 * rte::nvmRestore() are defined in NvmIntegrationHostTest.cpp, as the generated RteConfig.cpp defines them. */

#pragma once

#include <cstddef>
#include <cstdint>

#define NX_RTE_HAS_NVM 1

namespace rte
{

enum class NvmWrite : uint8_t
{
    SHUTDOWN,
    CHANGE,
};

enum class NvmIntegrity : uint8_t
{
    QM,
    ASIL,
};

struct NvmBlock
{
    char const* name;
    uint16_t id;
    uint16_t size;
    uint32_t address;
    void* image;
    void const* defaults;
    bool* changed;
    NvmWrite write;
    uint32_t minIntervalMs;
    NvmIntegrity integrity;
    uint8_t* quality;
};

constexpr size_t NVM_BLOCK_COUNT    = 3U;
constexpr uint32_t NVM_SLOT_MAX     = 20U; // Counter: ASIL copy 4 + 12 -> 16, Table: 6 + 12 -> 20 (4-byte aligned)
constexpr uint32_t NVM_LAYOUT_BYTES = 128U;

extern NvmBlock const NVM_BLOCKS[];

void nvmRestore();

} // namespace rte
