// Host stub of mcu/mcu.h for the SPI pin test (ARCH-38): real PORT and GPIO register layout and bit macros (NXP
// S32K148_PORT.h/S32K148_GPIO.h), the five ports are a RAM model the OpenBSW Io driver writes into.
#pragma once

#include <cstdint>

#define __IO volatile
#define __I  volatile const
#define __O  volatile
#include "S32K148_GPIO.h"
#include "S32K148_PORT.h"

extern GPIO_Type fakeGpio[5];
extern PORT_Type fakePort[5];
#define GPIO_BASE_PTRS {&fakeGpio[0], &fakeGpio[1], &fakeGpio[2], &fakeGpio[3], &fakeGpio[4]}
#define PORT_BASE_PTRS {&fakePort[0], &fakePort[1], &fakePort[2], &fakePort[3], &fakePort[4]}
