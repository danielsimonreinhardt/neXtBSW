// SPDX-License-Identifier: Apache-2.0
// Host stub of io/Io.h (bspIo) for the FlexCAN host test (COM-26): the driver only sets the pins of its bus to their
// default configuration; the test counts the calls.
#pragma once

#include <cstdint>

namespace bios
{
class Io
{
public:
    static void setDefaultConfiguration(uint16_t pin);
};
} // namespace bios
