/* NVM-19: host test stub of async/Types.h - the lock type of the RTE, which the NvM takes for the images, the changed
 * flags and the quality. Counts its depth, so the test sees that nothing is read or written outside it. */

#pragma once

namespace async
{

struct LockType
{
    LockType() { ++depth; }

    ~LockType() { --depth; }

    LockType(LockType const&)            = delete;
    LockType& operator=(LockType const&) = delete;

    static int depth;
};

} // namespace async
