/* Type test of the AUTOSAR base types (ARCH-45): sizes, signedness and limits of Platform_Types.h and the
 * values of Std_Types.h, compile time (static assertions) and run time. ARCH-46: uint8..uint64/sint8..sint64 are
 * the very types of <stdint.h> (type identity via _Generic, pointers interchangeable without cast).
 *
 * Host: tools/test -k autosar-base (ziglang cc, C11). Target core profiles (D11: ARM Cortex-M and Cortex-R, 32 bit,
 * little endian): tools/test -k autosar-base-cross compiles this file with arm-none-eabi-gcc -fsyntax-only and
 * AUTOSARBASE_TARGET_PROBE=1, which adds the checks that only hold on the target (32-bit pointers).
 */

#include "Std_Types.h"

#include <float.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if defined(__cplusplus)
#define AB_STATIC_ASSERT(cond, msg) static_assert(cond, msg)
#else
#define AB_STATIC_ASSERT(cond, msg) _Static_assert(cond, msg)
#endif

/* all-ones pattern: large and positive for an unsigned type, -1 for a signed one (no "< 0" for -Wtype-limits) */
#define AB_IS_SIGNED(type) (!((type)((type)0 - 1) > (type)1))

/* ------------------------------------------------------------------ Platform_Types.h: CPU description */

#if !defined(CPU_TYPE) || !defined(CPU_BIT_ORDER) || !defined(CPU_BYTE_ORDER)
#error "Platform_Types.h must define CPU_TYPE, CPU_BIT_ORDER and CPU_BYTE_ORDER"
#endif
#if CPU_TYPE != CPU_TYPE_32 || CPU_TYPE_32 != 32
#error "CPU_TYPE must be CPU_TYPE_32 (D11: 32-bit cores only)"
#endif
#if CPU_BIT_ORDER != LSB_FIRST || LSB_FIRST == MSB_FIRST
#error "CPU_BIT_ORDER must be LSB_FIRST"
#endif
#if CPU_BYTE_ORDER != LOW_BYTE_FIRST || LOW_BYTE_FIRST == HIGH_BYTE_FIRST
#error "CPU_BYTE_ORDER must be LOW_BYTE_FIRST (D11: little endian only)"
#endif
#if CPU_TYPE_8 != 8 || CPU_TYPE_16 != 16 || CPU_TYPE_64 != 64
#error "CPU_TYPE_8/16/64 have the wrong values"
#endif

/* The compiler must agree with the header: a big-endian target would need its own Platform_Types.h. */
#if defined(__BYTE_ORDER__) && defined(__ORDER_LITTLE_ENDIAN__)
AB_STATIC_ASSERT(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "CPU_BYTE_ORDER says little endian, compiler does not");
#endif

#if AUTOSARBASE_TARGET_PROBE
/* Only on the target: CPU_TYPE_32 means 32-bit addresses. The 64-bit host keeps 8-byte pointers. */
AB_STATIC_ASSERT(sizeof(VoidPtr) * CHAR_BIT == CPU_TYPE, "VoidPtr must be CPU_TYPE bits wide");
AB_STATIC_ASSERT(sizeof(ConstVoidPtr) * CHAR_BIT == CPU_TYPE, "ConstVoidPtr must be CPU_TYPE bits wide");
AB_STATIC_ASSERT(sizeof(int) * CHAR_BIT == CPU_TYPE, "int must be CPU_TYPE bits wide");
#endif

/* ------------------------------------------------------------------ Platform_Types.h: sizes and signedness */

AB_STATIC_ASSERT(CHAR_BIT == 8, "8-bit bytes");

AB_STATIC_ASSERT(sizeof(uint8) == 1 && !AB_IS_SIGNED(uint8), "uint8");
AB_STATIC_ASSERT(sizeof(uint16) == 2 && !AB_IS_SIGNED(uint16), "uint16");
AB_STATIC_ASSERT(sizeof(uint32) == 4 && !AB_IS_SIGNED(uint32), "uint32");
AB_STATIC_ASSERT(sizeof(uint64) == 8 && !AB_IS_SIGNED(uint64), "uint64");
AB_STATIC_ASSERT(sizeof(sint8) == 1 && AB_IS_SIGNED(sint8), "sint8");
AB_STATIC_ASSERT(sizeof(sint16) == 2 && AB_IS_SIGNED(sint16), "sint16");
AB_STATIC_ASSERT(sizeof(sint32) == 4 && AB_IS_SIGNED(sint32), "sint32");
AB_STATIC_ASSERT(sizeof(sint64) == 8 && AB_IS_SIGNED(sint64), "sint64");

/* ------------------------------------------------------------------ Platform_Types.h: same types as <stdint.h>
 * ARCH-46 (option B): not only the same size and signedness - the very same type, so that pointers are
 * interchangeable. _Generic on the pointer type tells apart types of equal size (unsigned int vs unsigned long). */

#define AB_SAME_TYPE(a, b) _Generic((a*)0, b*: 1, default: 0)

AB_STATIC_ASSERT(AB_SAME_TYPE(uint8, uint8_t), "uint8 is uint8_t");
AB_STATIC_ASSERT(AB_SAME_TYPE(uint16, uint16_t), "uint16 is uint16_t");
AB_STATIC_ASSERT(AB_SAME_TYPE(uint32, uint32_t), "uint32 is uint32_t");
AB_STATIC_ASSERT(AB_SAME_TYPE(uint64, uint64_t), "uint64 is uint64_t");
AB_STATIC_ASSERT(AB_SAME_TYPE(sint8, int8_t), "sint8 is int8_t");
AB_STATIC_ASSERT(AB_SAME_TYPE(sint16, int16_t), "sint16 is int16_t");
AB_STATIC_ASSERT(AB_SAME_TYPE(sint32, int32_t), "sint32 is int32_t");
AB_STATIC_ASSERT(AB_SAME_TYPE(sint64, int64_t), "sint64 is int64_t");
/* boolean stays the AUTOSAR unsigned 8-bit type, not _Bool */
AB_STATIC_ASSERT(AB_SAME_TYPE(boolean, uint8_t) && !AB_SAME_TYPE(boolean, _Bool), "boolean is uint8_t, not _Bool");
AB_STATIC_ASSERT(AB_SAME_TYPE(float32, float) && AB_SAME_TYPE(float64, double), "float32/float64 are float/double");

AB_STATIC_ASSERT(sizeof(uint8_least) >= 1 && !AB_IS_SIGNED(uint8_least), "uint8_least");
AB_STATIC_ASSERT(sizeof(uint16_least) >= 2 && !AB_IS_SIGNED(uint16_least), "uint16_least");
AB_STATIC_ASSERT(sizeof(uint32_least) >= 4 && !AB_IS_SIGNED(uint32_least), "uint32_least");
AB_STATIC_ASSERT(sizeof(sint8_least) >= 1 && AB_IS_SIGNED(sint8_least), "sint8_least");
AB_STATIC_ASSERT(sizeof(sint16_least) >= 2 && AB_IS_SIGNED(sint16_least), "sint16_least");
AB_STATIC_ASSERT(sizeof(sint32_least) >= 4 && AB_IS_SIGNED(sint32_least), "sint32_least");

AB_STATIC_ASSERT(sizeof(boolean) == 1 && !AB_IS_SIGNED(boolean), "boolean is an unsigned 8-bit type");
AB_STATIC_ASSERT(TRUE == 1 && FALSE == 0, "TRUE/FALSE");

/* IEEE 754 binary32/binary64 */
AB_STATIC_ASSERT(sizeof(float32) == 4 && FLT_MANT_DIG == 24 && FLT_MAX_EXP == 128, "float32");
AB_STATIC_ASSERT(sizeof(float64) == 8 && DBL_MANT_DIG == 53 && DBL_MAX_EXP == 1024, "float64");

/* Limits: the full two's-complement range of the fixed-width types */
AB_STATIC_ASSERT((uint8)~(uint8)0 == 0xFFu, "uint8 max");
AB_STATIC_ASSERT((uint16)~(uint16)0 == 0xFFFFu, "uint16 max");
AB_STATIC_ASSERT((uint32)~(uint32)0 == 0xFFFFFFFFul, "uint32 max");
AB_STATIC_ASSERT((uint64)~(uint64)0 == 0xFFFFFFFFFFFFFFFFull, "uint64 max");
AB_STATIC_ASSERT((sint8)INT8_MIN == -128 && (sint8)INT8_MAX == 127, "sint8 range");
AB_STATIC_ASSERT((sint16)INT16_MIN == -32768 && (sint16)INT16_MAX == 32767, "sint16 range");
AB_STATIC_ASSERT((sint32)INT32_MIN == -2147483647L - 1 && (sint32)INT32_MAX == 2147483647L, "sint32 range");
AB_STATIC_ASSERT((sint64)INT64_MIN == -9223372036854775807LL - 1 && (sint64)INT64_MAX == 9223372036854775807LL,
                 "sint64 range");

/* ------------------------------------------------------------------ Std_Types.h */

AB_STATIC_ASSERT(sizeof(Std_ReturnType) == 1 && !AB_IS_SIGNED(Std_ReturnType), "Std_ReturnType is uint8");
AB_STATIC_ASSERT(E_OK == 0u && E_NOT_OK == 1u, "E_OK/E_NOT_OK");
AB_STATIC_ASSERT(STD_HIGH == 1u && STD_LOW == 0u, "STD_HIGH/STD_LOW");
AB_STATIC_ASSERT(STD_ACTIVE == 1u && STD_IDLE == 0u, "STD_ACTIVE/STD_IDLE");
AB_STATIC_ASSERT(STD_ON == 1u && STD_OFF == 0u, "STD_ON/STD_OFF");

/* ------------------------------------------------------------------ run time */

static unsigned checks;
static unsigned failures;

static void check(int ok, const char* what, int line)
{
    ++checks;
    if (!ok)
    {
        ++failures;
        printf("FAIL line %d: %s\n", line, what);
    }
}

#define CHECK(cond) check((cond) ? 1 : 0, #cond, __LINE__)

/* volatile inputs so that the compiler cannot fold the run-time checks away */
static volatile uint8 v_u8_max = 0xFFu;
static volatile uint16 v_u16_max = 0xFFFFu;
static volatile uint32 v_u32_max = 0xFFFFFFFFul;
static volatile uint64 v_u64_max = 0xFFFFFFFFFFFFFFFFull;
static volatile sint8 v_s8_min = -128;
static volatile sint16 v_s16_min = -32768;
static volatile sint32 v_s32_min = -2147483647L - 1;
static volatile sint64 v_s64_min = -9223372036854775807LL - 1;
static volatile uint32 v_word = 0x11223344ul;

static Std_ReturnType returnOk(void) { return E_OK; }
static Std_ReturnType returnNotOk(void) { return E_NOT_OK; }

static void testUnsignedWrapAround(void)
{
    CHECK((uint8)(v_u8_max + 1u) == 0u);
    CHECK((uint16)(v_u16_max + 1u) == 0u);
    CHECK((uint32)(v_u32_max + 1u) == 0u);
    CHECK((uint64)(v_u64_max + 1u) == 0u);
    CHECK(v_u64_max > (uint64)v_u32_max); /* 64 bits really hold more than 32 */
}

static void testSignedMinimum(void)
{
    CHECK(v_s8_min < 0 && (sint8)(v_s8_min + 1) == -127);
    CHECK(v_s16_min < 0 && (sint16)(v_s16_min + 1) == -32767);
    CHECK(v_s32_min < 0 && v_s32_min + 1 == -2147483647L);
    CHECK(v_s64_min < 0 && v_s64_min + 1 == -9223372036854775807LL);
    /* the bit pattern of the minimum is the sign bit alone (two's complement) */
    {
        sint32 s = v_s32_min;
        uint32 u;
        memcpy(&u, &s, sizeof u);
        CHECK(u == 0x80000000ul);
    }
}

static void testFloat(void)
{
    volatile float32 f = 16777216.0f; /* 2^24: the next integer is not representable in binary32 */
    volatile float64 d = 16777216.0;
    CHECK((float32)(f + 1.0f) == f);
    CHECK((float64)(d + 1.0) != d);
    {
        float32 one = 1.0f;
        uint32 bits;
        memcpy(&bits, &one, sizeof bits);
        CHECK(bits == 0x3F800000ul); /* IEEE 754 binary32 */
    }
}

static void testByteAndBitOrder(void)
{
    uint32 word = v_word;
    uint8 bytes[4];
    memcpy(bytes, &word, sizeof bytes);
    CHECK(bytes[0] == 0x44u && bytes[3] == 0x11u); /* LOW_BYTE_FIRST */

    /* LSB_FIRST: the first bit-field occupies the least significant bit */
    {
        struct
        {
            unsigned first : 1;
            unsigned rest : 7;
        } bits;
        uint8 raw = 0u;
        memset(&bits, 0, sizeof bits);
        bits.first = 1u;
        memcpy(&raw, &bits, 1u);
        CHECK(raw == 0x01u);
    }
}

static void testBooleanAndStdValues(void)
{
    boolean b = TRUE;
    CHECK(b == 1u && !(boolean)FALSE);
    b = (boolean)(b + 255u); /* 8 bits: TRUE + 255 wraps to FALSE */
    CHECK(b == FALSE);
    CHECK(returnOk() == E_OK && returnNotOk() == E_NOT_OK && returnOk() != returnNotOk());
    CHECK(STD_HIGH != STD_LOW && STD_ON != STD_OFF && STD_ACTIVE != STD_IDLE);
}

/* ARCH-46: pointers to AUTOSAR and <stdint.h> types are assigned without cast in both directions; with
 * -Werror -pedantic an incompatible pointer type would already stop the compile. */
static void readWord(const uint32_t* in, uint32* out) { *out = *in; }
static void readSigned(const sint32* in, int32_t* out) { *out = *in; }

static void testPointerInterchange(void)
{
    uint32 a = v_word;
    uint32_t b = 0u;
    uint32_t* pb = &a;
    uint64* p64 = (uint64_t*)0;
    readWord(&a, &b);
    CHECK(b == 0x11223344ul && *pb == a && p64 == NULL_PTR);
    {
        int32_t s = -5;
        sint32 t = 0;
        readSigned(&s, &t);
        CHECK(t == -5);
    }
}

static void testCompilerAbstraction(void)
{
    uint8 value = 7u;
    P2VAR(uint8, AUTOMATIC, APPL_DATA) p = &value;
    P2CONST(uint8, AUTOMATIC, APPL_DATA) pc = &value;
    CONST(uint16, APPL_CONST) c = 0x1234u;
    *p = 8u;
    CHECK(*pc == 8u && c == 0x1234u);
    CHECK(NULL_PTR == (void*)0);
}

int main(void)
{
    testUnsignedWrapAround();
    testSignedMinimum();
    testFloat();
    testByteAndBitOrder();
    testBooleanAndStdValues();
    testPointerInterchange();
    testCompilerAbstraction();
    printf("AutosarBaseHostTest: CPU_TYPE %d, sizeof(VoidPtr) %u on this build\n", CPU_TYPE,
           (unsigned)sizeof(VoidPtr));
    printf("%u checks, %u failures\n", checks, failures);
    return failures == 0u ? 0 : 1;
}
