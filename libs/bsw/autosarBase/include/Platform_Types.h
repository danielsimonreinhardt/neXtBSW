/* AUTOSAR Platform Types (AUTOSAR_SWS_PlatformTypes) for 32-bit ARM Cortex-M and Cortex-R cores, little endian
 * (core profiles of decision D11, doc/architecture/README.md). */

#ifndef PLATFORM_TYPES_H
#define PLATFORM_TYPES_H

/* ARCH-46 (user decision 2026-09-19, option B): the fixed-width integer types are the <stdint.h> types themselves,
 * not only types of the same size, so that a uint32* can be passed where a uint32_t* is expected and vice versa
 * (C and C++). With newlib (arm-none-eabi-gcc) uint32_t/int32_t are unsigned long/long, so uint32/sint32 are too. */
#include <stdint.h>

#define CPU_TYPE_8  8
#define CPU_TYPE_16 16
#define CPU_TYPE_32 32
#define CPU_TYPE_64 64

#define MSB_FIRST 0
#define LSB_FIRST 1

#define HIGH_BYTE_FIRST 0
#define LOW_BYTE_FIRST  1

#define CPU_TYPE            CPU_TYPE_32
#define CPU_BIT_ORDER       LSB_FIRST
#define CPU_BYTE_ORDER      LOW_BYTE_FIRST

#ifndef TRUE
#define TRUE 1
#endif
#ifndef FALSE
#define FALSE 0
#endif

/* AUTOSAR boolean: unsigned 8 bit with TRUE/FALSE. Deliberately not C99 bool (_Bool), which converts every non-zero
 * value to 1 and would change the generated component code and the RTE data layout semantics. */
typedef uint8_t boolean;

typedef int8_t sint8;
typedef uint8_t uint8;
typedef int16_t sint16;
typedef uint16_t uint16;
typedef int32_t sint32;
typedef uint32_t uint32;
typedef int64_t sint64;
typedef uint64_t uint64;

/* "at least" types in the natural word size of the 32-bit core (not part of option B, unchanged) */
typedef signed int sint8_least;
typedef unsigned int uint8_least;
typedef signed int sint16_least;
typedef unsigned int uint16_least;
typedef signed int sint32_least;
typedef unsigned int uint32_least;

typedef float float32;
typedef double float64;

typedef void* VoidPtr;
typedef void const* ConstVoidPtr;

#endif /* PLATFORM_TYPES_H */
