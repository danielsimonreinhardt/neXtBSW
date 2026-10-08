// SPDX-License-Identifier: Apache-2.0

/* ARCH-26 host test stub for mcu/mcu.h: the CMSIS core register accessors CortexMInterruptContext.h reads.
 * The core architecture comes from the command line, like NX_CORE_ARCHITECTURE_* from the generated
 * mcu/derivativeFacts.h. ARMv6-M has no BASEPRI: without __get_BASEPRI() here, reading it on that
 * architecture does not compile. */

#pragma once

#include <cstdint>

namespace coreModel
{
extern uint32_t ipsr;
extern uint32_t primask;
extern uint32_t basepri;
extern uint32_t basepriReads;
} // namespace coreModel

inline uint32_t __get_IPSR() { return coreModel::ipsr; }

inline uint32_t __get_PRIMASK() { return coreModel::primask; }

#if !defined(NX_CORE_ARCHITECTURE_ARMV6M)
inline uint32_t __get_BASEPRI()
{
    ++coreModel::basepriReads;
    return coreModel::basepri;
}
#endif
