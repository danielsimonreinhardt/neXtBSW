/* DOC-28 host test stub: only what NoHeapOperatorDelete.cpp uses of the safe supervisor. reportEtlAssert() is defined
 * by the test (NoHeapOperatorDeleteHostTest.cpp); on the target it does not return. */
#pragma once

#include <cstdint>

namespace safety
{
class SafeSupervisor
{
public:
    static SafeSupervisor& getInstance();
    void reportEtlAssert(uint32_t callSite) const;
};
} // namespace safety
