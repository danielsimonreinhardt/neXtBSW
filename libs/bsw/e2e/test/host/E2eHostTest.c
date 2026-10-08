/* Host test of the E2E library (COM-10): CRC check values, protocol examples of the AUTOSAR specification and
 * check behaviour (counter wrap, repetition, jumps, Data ID nibble, input errors) with own test profiles.
 * Sources of the expected values (R22-11, www.autosar.org):
 *   AUTOSAR_SWS_CRCLibrary Tables 7.1-7.4; AUTOSAR_PRS_E2EProtocol Tables 6.18-6.27 (P01), 6.34-6.36 (P02),
 *   6.86-6.92 (P11).
 * Build and run (from the repository root):
 *   .venv/Scripts/python -m ziglang cc -std=c99 -O1 -Wall -Wextra -Wconversion -Werror -Ilibs/bsw/e2e/include \
 *       libs/bsw/e2e/src/e2e/E2E.c libs/bsw/e2e/src/e2e/E2E_P01.c libs/bsw/e2e/src/e2e/E2E_P02.c \
 *       libs/bsw/e2e/src/e2e/E2E_P11.c libs/bsw/e2e/test/host/E2eHostTest.c -o build/host/e2e_test.exe
 *   build/host/e2e_test.exe
 * The randomised comparison with the Python reference model is tools/e2e/test_e2e_host.py.
 */

#include "e2e/E2E_P01.h"
#include "e2e/E2E_P02.h"
#include "e2e/E2E_P11.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);      \
        }                                                                 \
    } while (0)

static uint8_t const CHECK_STRING[9] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};

typedef struct
{
    uint8_t data[9];
    uint8_t length;
    uint8_t crc8;
    uint8_t crc8h2f;
} CrcVector;

/* SWS_CRCLibrary Tables 7.2 and 7.4 */
static CrcVector const CRC_VECTORS[] = {
    {{0x00, 0x00, 0x00, 0x00}, 4U, 0x59U, 0x12U},
    {{0xF2, 0x01, 0x83}, 3U, 0x37U, 0xC2U},
    {{0x0F, 0xAA, 0x00, 0x55}, 4U, 0x79U, 0xC6U},
    {{0x00, 0xFF, 0x55, 0x11}, 4U, 0xB8U, 0x77U},
    {{0x33, 0x22, 0x55, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF}, 9U, 0xCBU, 0x11U},
    {{0x92, 0x6B, 0x55}, 3U, 0x8CU, 0x33U},
    {{0xFF, 0xFF, 0xFF, 0xFF}, 4U, 0x74U, 0x6CU},
};

static void testCrc(void)
{
    CHECK(E2E_Crc8(CHECK_STRING, 9U, 0U, true) == 0x4BU);
    CHECK(E2E_Crc8H2F(CHECK_STRING, 9U, 0U, true) == 0xDFU);
    for (size_t i = 0U; i < sizeof(CRC_VECTORS) / sizeof(CRC_VECTORS[0]); ++i)
    {
        CrcVector const* v = &CRC_VECTORS[i];
        CHECK(E2E_Crc8(v->data, v->length, 0x00U, true) == v->crc8);
        CHECK(E2E_Crc8H2F(v->data, v->length, 0x00U, true) == v->crc8h2f);
        /* call sequence: first call plus subsequent call gives the same result as one call */
        uint8_t const part = E2E_Crc8(v->data, 2U, 0x00U, true);
        CHECK(E2E_Crc8(&v->data[2], (uint32_t)v->length - 2U, part, false) == v->crc8);
        uint8_t const partH2f = E2E_Crc8H2F(v->data, 1U, 0x00U, true);
        CHECK(E2E_Crc8H2F(&v->data[1], (uint32_t)v->length - 1U, partH2f, false) == v->crc8h2f);
        /* magic check (Tables 7.1/7.3) */
        uint8_t withCrc[10];
        memcpy(withCrc, v->data, v->length);
        withCrc[v->length] = v->crc8;
        CHECK((uint8_t)(E2E_Crc8(withCrc, (uint32_t)v->length + 1U, 0U, true) ^ 0xFFU) == 0xC4U);
        withCrc[v->length] = v->crc8h2f;
        CHECK((uint8_t)(E2E_Crc8H2F(withCrc, (uint32_t)v->length + 1U, 0U, true) ^ 0xFFU) == 0x42U);
    }
}

/* PRS Table 6.18 / 6.86 */
static E2E_P01ConfigType p01SpecConfig(E2E_P01DataIDMode mode)
{
    E2E_P01ConfigType const cfg = {8U, 0U, 0x123U, 12U, mode, 64U, 1U, 15U, 0U};
    return cfg;
}

static E2E_P11ConfigType p11SpecConfig(E2E_P11DataIDMode mode)
{
    E2E_P11ConfigType const cfg = {8U, 0U, 0x123U, 12U, mode, 64U, 1U};
    return cfg;
}

static void testSpecExamplesP01P11(void)
{
    /* Tables 6.20-6.27 and 6.88-6.91: bytes 0 and 1 of an all-zero 8-byte message for counter 0 and 1 */
    static const struct
    {
        E2E_P01DataIDMode mode;
        uint8_t bytes[2][2];
    } examples[] = {
        {E2E_P01_DATAID_BOTH, {{0xCC, 0x00}, {0x91, 0x01}}},
        {E2E_P01_DATAID_ALT, {{0xCE, 0x00}, {0x02, 0x01}}},
        {E2E_P01_DATAID_LOW, {{0xCE, 0x00}, {0x93, 0x01}}},
        {E2E_P01_DATAID_NIBBLE, {{0x2A, 0x10}, {0x77, 0x11}}},
    };
    uint8_t const zeros[8] = {0};
    for (size_t i = 0U; i < sizeof(examples) / sizeof(examples[0]); ++i)
    {
        E2E_P01ConfigType const cfg = p01SpecConfig(examples[i].mode);
        E2E_P01ProtectStateType st;
        CHECK(E2E_P01ProtectInit(&st) == E2E_E_OK);
        for (uint8_t counter = 0U; counter < 2U; ++counter)
        {
            uint8_t data[8] = {0};
            CHECK(E2E_P01Protect(&cfg, &st, data, 8U) == E2E_E_OK);
            CHECK(data[0] == examples[i].bytes[counter][0]);
            CHECK(data[1] == examples[i].bytes[counter][1]);
            CHECK(memcmp(&data[2], zeros, 6U) == 0);
        }
        CHECK(st.Counter == 2U);

        if ((examples[i].mode == E2E_P01_DATAID_BOTH) || (examples[i].mode == E2E_P01_DATAID_NIBBLE))
        {
            E2E_P11ConfigType const cfg11 = p11SpecConfig(examples[i].mode);
            E2E_P11ProtectStateType st11;
            CHECK(E2E_P11ProtectInit(&st11) == E2E_E_OK);
            for (uint8_t counter = 0U; counter < 2U; ++counter)
            {
                uint8_t data[8] = {0};
                CHECK(E2E_P11Protect(&cfg11, &st11, data, 8U) == E2E_E_OK);
                CHECK(data[0] == examples[i].bytes[counter][0]);
                CHECK(data[1] == examples[i].bytes[counter][1]);
                CHECK(memcmp(&data[2], zeros, 6U) == 0);
            }
        }
    }

    /* Table 6.92: P11 NIBBLE with CRCOffset 64, CounterOffset 72, DataIDNibbleOffset 76 */
    E2E_P11ConfigType const cfg64 = {72U, 64U, 0x123U, 76U, E2E_P11_DATAID_NIBBLE, 128U, 1U};
    E2E_P11ProtectStateType st64  = {0U};
    uint8_t data64[16]            = {0};
    CHECK(E2E_P11Protect(&cfg64, &st64, data64, 16U) == E2E_E_OK);
    CHECK((data64[8] == 0x7DU) && (data64[9] == 0x10U));
    CHECK((memcmp(data64, zeros, 8U) == 0) && (memcmp(&data64[10], zeros, 6U) == 0));
}

static E2E_P02ConfigType p02SpecConfig(void)
{
    /* Table 6.34 */
    E2E_P02ConfigType const cfg
        = {64U, {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10},
           1U, 15U, 0U};
    return cfg;
}

static void testSpecExamplesP02(void)
{
    /* Table 6.36: CRC for counter 1..15, 0 */
    static uint8_t const crcs[16]
        = {0x1B, 0x98, 0x31, 0x0D, 0x18, 0x9B, 0x65, 0x08, 0x1D, 0x9E, 0x37, 0x0B, 0x1E, 0x9D, 0xCD, 0x0E};
    E2E_P02ConfigType const cfg = p02SpecConfig();
    E2E_P02ProtectStateType st;
    CHECK(E2E_P02ProtectInit(&st) == E2E_E_OK);
    for (uint8_t i = 0U; i < 16U; ++i)
    {
        uint8_t data[8]         = {0};
        uint8_t const expected  = (uint8_t)((i + 1U) % 16U);
        CHECK(E2E_P02Protect(&cfg, &st, data, 8U) == E2E_E_OK);
        CHECK(data[1] == expected);
        CHECK(data[0] == crcs[i]);
    }
    /* Protect keeps the user bits of Data[1] (PRS_E2E_00124) */
    uint8_t data[8] = {0, 0xA0, 1, 2, 3, 4, 5, 6};
    CHECK(E2E_P02Protect(&cfg, &st, data, 8U) == E2E_E_OK);
    CHECK((data[1] == 0xA1U) && (data[7] == 6U));
}

/* Own test profile (not a board value): 3-byte message, CRC last, counter in the high nibble of byte 1, Data ID
 * nibble in the low nibble of byte 1, i.e. a layout different from variants 1A/1C. */
static void testCounterWrapAndSequenceP11(void)
{
    E2E_P11ConfigType const cfg = {12U, 16U, 0x0ABCU, 8U, E2E_P11_DATAID_NIBBLE, 24U, 2U};
    E2E_P11ProtectStateType tx;
    E2E_P11CheckStateType rx;
    CHECK(E2E_P11CheckConfig(&cfg) == E2E_E_OK);
    CHECK(E2E_P11ProtectInit(&tx) == E2E_E_OK);
    CHECK(E2E_P11CheckInit(&rx) == E2E_E_OK);
    CHECK((rx.Counter == 0x0EU) && (rx.Status == E2E_P11STATUS_ERROR));

    uint8_t data[3];
    /* 40 messages: counter 0..14 repeats, never 15, every check OK (initial state 0x0E accepts counter 0) */
    for (int i = 0; i < 40; ++i)
    {
        data[0] = (uint8_t)i;
        data[1] = 0U;
        CHECK(E2E_P11Protect(&cfg, &tx, data, 3U) == E2E_E_OK);
        uint8_t const counter = (uint8_t)(data[1] >> 4U);
        CHECK(counter == (uint8_t)(i % 15));
        CHECK((data[1] & 0x0FU) == 0x0AU);
        CHECK(E2E_P11Check(&cfg, &rx, data, 3U) == E2E_E_OK);
        CHECK(rx.Status == E2E_P11STATUS_OK);
        CHECK(E2E_P11MapStatusToSM(E2E_E_OK, rx.Status) == E2E_P_OK);
    }
    /* repetition */
    CHECK(E2E_P11Check(&cfg, &rx, data, 3U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P11STATUS_REPEATED);
    CHECK(E2E_P11MapStatusToSM(E2E_E_OK, rx.Status) == E2E_P_REPEATED);
    /* one lost message across the wrap (counter 14 -> 1 is delta 2) */
    uint8_t last[3];
    for (int i = 0; i < 2; ++i)
    {
        data[0] = 0x55U;
        CHECK(E2E_P11Protect(&cfg, &tx, data, 3U) == E2E_E_OK);
    }
    CHECK(E2E_P11Check(&cfg, &rx, data, 3U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P11STATUS_OKSOMELOST);
    CHECK(E2E_P11MapStatusToSM(E2E_E_OK, rx.Status) == E2E_P_OK);
    memcpy(last, data, sizeof(last));
    /* jump by 3 with MaxDeltaCounter 2 */
    for (int i = 0; i < 3; ++i)
    {
        CHECK(E2E_P11Protect(&cfg, &tx, data, 3U) == E2E_E_OK);
    }
    CHECK(E2E_P11Check(&cfg, &rx, data, 3U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P11STATUS_WRONGSEQUENCE);
    CHECK(E2E_P11MapStatusToSM(E2E_E_OK, rx.Status) == E2E_P_WRONGSEQUENCE);
    /* the receiver resynchronises on the received counter: the next message is OK again (healing) */
    CHECK(E2E_P11Protect(&cfg, &tx, data, 3U) == E2E_E_OK);
    CHECK(E2E_P11Check(&cfg, &rx, data, 3U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P11STATUS_OK);
    /* an older message (counter going back) is a wrong sequence, not OK */
    CHECK(E2E_P11Check(&cfg, &rx, last, 3U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P11STATUS_WRONGSEQUENCE);
    /* no new data */
    CHECK(E2E_P11Check(&cfg, &rx, NULL, 0U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P11STATUS_NONEWDATA);
    CHECK(E2E_P11MapStatusToSM(E2E_E_OK, rx.Status) == E2E_P_NONEWDATA);

    /* every single bit flip of a valid message is an error */
    uint8_t good[3] = {0x12U, 0U, 0U};
    CHECK(E2E_P11Protect(&cfg, &tx, good, 3U) == E2E_E_OK);
    for (uint8_t bit = 0U; bit < 24U; ++bit)
    {
        E2E_P11CheckStateType probe = rx;
        memcpy(data, good, sizeof(data));
        data[bit / 8U] = (uint8_t)(data[bit / 8U] ^ (1U << (bit % 8U)));
        CHECK(E2E_P11Check(&cfg, &probe, data, 3U) == E2E_E_OK);
        CHECK(probe.Status == E2E_P11STATUS_ERROR);
        CHECK(probe.Counter == rx.Counter);
        CHECK(E2E_P11MapStatusToSM(E2E_E_OK, probe.Status) == E2E_P_ERROR);
    }
}

static void testDataIdNibble(void)
{
    /* Same low byte, different nibble: CRC over (low byte, 0x00, data) matches, only the nibble check detects it */
    E2E_P11ConfigType const sender11   = {8U, 0U, 0x0223U, 12U, E2E_P11_DATAID_NIBBLE, 64U, 1U};
    E2E_P11ConfigType const receiver11 = {8U, 0U, 0x0123U, 12U, E2E_P11_DATAID_NIBBLE, 64U, 1U};
    E2E_P11ProtectStateType tx11       = {0U};
    E2E_P11CheckStateType rx11;
    uint8_t data[8] = {0};
    CHECK(E2E_P11CheckInit(&rx11) == E2E_E_OK);
    CHECK(E2E_P11Protect(&sender11, &tx11, data, 8U) == E2E_E_OK);
    CHECK((data[1] >> 4U) == 0x2U);
    CHECK(E2E_P11Check(&receiver11, &rx11, data, 8U) == E2E_E_OK);
    CHECK(rx11.Status == E2E_P11STATUS_ERROR);
    CHECK(E2E_P11Check(&sender11, &rx11, data, 8U) == E2E_E_OK);
    CHECK(rx11.Status == E2E_P11STATUS_OK);

    E2E_P01ConfigType sender01   = p01SpecConfig(E2E_P01_DATAID_NIBBLE);
    E2E_P01ConfigType receiver01 = p01SpecConfig(E2E_P01_DATAID_NIBBLE);
    sender01.DataID              = 0x0223U;
    E2E_P01ProtectStateType tx01 = {0U};
    E2E_P01CheckStateType rx01;
    memset(data, 0, sizeof(data));
    CHECK(E2E_P01CheckInit(&rx01) == E2E_E_OK);
    CHECK(E2E_P01Protect(&sender01, &tx01, data, 8U) == E2E_E_OK);
    CHECK(E2E_P01Check(&receiver01, &rx01, data, 8U) == E2E_E_OK);
    CHECK(rx01.Status == E2E_P01STATUS_WRONGCRC);
    CHECK(rx01.WaitForFirstData);
    CHECK(E2E_P01Check(&sender01, &rx01, data, 8U) == E2E_E_OK);
    CHECK(rx01.Status == E2E_P01STATUS_INITIAL);

    /* mode BOTH: the high byte is part of the CRC, so another high byte is a CRC error */
    E2E_P01ConfigType other = p01SpecConfig(E2E_P01_DATAID_BOTH);
    E2E_P01ConfigType mine  = p01SpecConfig(E2E_P01_DATAID_BOTH);
    other.DataID            = 0x0223U;
    memset(data, 0, sizeof(data));
    tx01.Counter = 0U;
    CHECK(E2E_P01CheckInit(&rx01) == E2E_E_OK);
    CHECK(E2E_P01Protect(&other, &tx01, data, 8U) == E2E_E_OK);
    CHECK(E2E_P01Check(&mine, &rx01, data, 8U) == E2E_E_OK);
    CHECK(rx01.Status == E2E_P01STATUS_WRONGCRC);
}

static E2E_P01CheckStatusType p01Step(
    E2E_P01ConfigType const* cfg, E2E_P01ProtectStateType* tx, E2E_P01CheckStateType* rx, int sends, int receive)
{
    uint8_t data[8] = {0};
    for (int i = 0; i < sends; ++i)
    {
        CHECK(E2E_P01Protect(cfg, tx, data, 8U) == E2E_E_OK);
    }
    CHECK(E2E_P01Check(cfg, rx, receive ? data : NULL, receive ? 8U : 0U) == E2E_E_OK);
    return rx->Status;
}

static void testLegacyP01(void)
{
    E2E_P01ConfigType cfg = p01SpecConfig(E2E_P01_DATAID_ALT);
    cfg.MaxNoNewOrRepeatedData = 2U;
    cfg.SyncCounterInit        = 2U;
    E2E_P01ProtectStateType tx = {5U};
    E2E_P01CheckStateType rx;
    CHECK(E2E_P01CheckInit(&rx) == E2E_E_OK);
    CHECK(rx.WaitForFirstData && (rx.Status == E2E_P01STATUS_NONEWDATA));

    CHECK(p01Step(&cfg, &tx, &rx, 1, 1) == E2E_P01STATUS_INITIAL);
    CHECK(E2E_P01MapStatusToSM(E2E_E_OK, rx.Status, true) == E2E_P_WRONGSEQUENCE);
    CHECK(E2E_P01MapStatusToSM(E2E_E_OK, rx.Status, false) == E2E_P_OK);
    /* counter wrap 14 -> 0 */
    for (int i = 0; i < 20; ++i)
    {
        CHECK(p01Step(&cfg, &tx, &rx, 1, 1) == E2E_P01STATUS_OK);
    }
    /* two lost messages are a wrong sequence with MaxDeltaCounterInit 1 */
    CHECK(p01Step(&cfg, &tx, &rx, 3, 1) == E2E_P01STATUS_WRONGSEQUENCE);
    /* with SyncCounterInit 2 the receiver takes the counter and needs two further valid messages */
    CHECK(rx.SyncCounter == 2U);
    CHECK(p01Step(&cfg, &tx, &rx, 1, 1) == E2E_P01STATUS_SYNC);
    CHECK(E2E_P01MapStatusToSM(E2E_E_OK, rx.Status, true) == E2E_P_OK);
    CHECK(E2E_P01MapStatusToSM(E2E_E_OK, rx.Status, false) == E2E_P_WRONGSEQUENCE);
    CHECK(p01Step(&cfg, &tx, &rx, 1, 1) == E2E_P01STATUS_SYNC);
    CHECK(p01Step(&cfg, &tx, &rx, 1, 1) == E2E_P01STATUS_OK);
    /* MaxDeltaCounterInit 1 accepts one lost message (counter +2, Table 6.17) */
    CHECK(p01Step(&cfg, &tx, &rx, 2, 1) == E2E_P01STATUS_OKSOMELOST);
    CHECK(rx.LostData == 1U);
    /* a check without new data widens the tolerance by one: counter +3 after one NONEWDATA is OKSOMELOST */
    CHECK(p01Step(&cfg, &tx, &rx, 1, 0) == E2E_P01STATUS_NONEWDATA);
    CHECK(E2E_P01MapStatusToSM(E2E_E_OK, rx.Status, true) == E2E_P_NONEWDATA);
    CHECK(p01Step(&cfg, &tx, &rx, 2, 1) == E2E_P01STATUS_OKSOMELOST);
    CHECK(rx.LostData == 2U);
    /* repeated data */
    uint8_t data[8] = {0};
    CHECK(E2E_P01Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
    CHECK(E2E_P01Check(&cfg, &rx, data, 8U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P01STATUS_OK);
    for (uint8_t i = 1U; i <= 3U; ++i)
    {
        CHECK(E2E_P01Check(&cfg, &rx, data, 8U) == E2E_E_OK);
        CHECK(rx.Status == E2E_P01STATUS_REPEATED);
        CHECK(rx.NoNewOrRepeatedDataCounter == i);
    }
    CHECK(E2E_P01MapStatusToSM(E2E_E_OK, rx.Status, true) == E2E_P_REPEATED);
    /* more repetitions than MaxNoNewOrRepeatedData: next valid message is SYNC */
    CHECK(p01Step(&cfg, &tx, &rx, 1, 1) == E2E_P01STATUS_SYNC);
    CHECK((rx.NoNewOrRepeatedDataCounter == 0U) && (rx.SyncCounter == 2U));
    /* corrupted data */
    CHECK(E2E_P01Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
    data[5] ^= 0x01U;
    CHECK(E2E_P01Check(&cfg, &rx, data, 8U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P01STATUS_WRONGCRC);
    CHECK(E2E_P01MapStatusToSM(E2E_E_OK, rx.Status, true) == E2E_P_ERROR);
    /* received counter 15 is an input error (Figure 6.58) */
    data[1] = (uint8_t)(data[1] | 0x0FU);
    CHECK(E2E_P01Check(&cfg, &rx, data, 8U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P01MapStatusToSM(E2E_E_INPUTERR_WRONG, E2E_P01STATUS_OK, true) == E2E_P_ERROR);
    /* the no-new-data counter saturates at 14 */
    for (int i = 0; i < 30; ++i)
    {
        CHECK(E2E_P01Check(&cfg, &rx, NULL, 0U) == E2E_E_OK);
    }
    CHECK((rx.NoNewOrRepeatedDataCounter == 14U) && (rx.MaxDeltaCounter == 14U));
}

static void testLegacyP02(void)
{
    E2E_P02ConfigType const cfg = p02SpecConfig();
    E2E_P02ProtectStateType tx  = {13U};
    E2E_P02CheckStateType rx;
    uint8_t data[8] = {0};
    CHECK(E2E_P02CheckInit(&rx) == E2E_E_OK);
    CHECK(E2E_P02Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
    CHECK(E2E_P02Check(&cfg, &rx, data, 8U) == E2E_E_OK);
    CHECK((rx.Status == E2E_P02STATUS_INITIAL) && (rx.LastValidCounter == 14U));
    /* wrap 15 -> 0, all counter values used */
    for (int i = 0; i < 18; ++i)
    {
        CHECK(E2E_P02Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
        CHECK(E2E_P02Check(&cfg, &rx, data, 8U) == E2E_E_OK);
        CHECK(rx.Status == E2E_P02STATUS_OK);
    }
    CHECK(E2E_P02Check(&cfg, &rx, data, 8U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P02STATUS_REPEATED);
    CHECK(E2E_P02MapStatusToSM(E2E_E_OK, rx.Status, true) == E2E_P_REPEATED);
    /* tolerance is now 3 (+1 per check since the last OK): counter +4 is a wrong sequence */
    CHECK(E2E_P02Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
    CHECK(E2E_P02Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
    CHECK(E2E_P02Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
    CHECK(E2E_P02Protect(&cfg, &tx, data, 8U) == E2E_E_OK);
    CHECK(E2E_P02Check(&cfg, &rx, data, 8U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P02STATUS_WRONGSEQUENCE);
    CHECK(E2E_P02MapStatusToSM(E2E_E_OK, rx.Status, false) == E2E_P_WRONGSEQUENCE);
    /* wrong Data ID for the counter: CRC error */
    E2E_P02ConfigType wrongList = cfg;
    wrongList.DataIDList[data[1] & 0x0FU] ^= 0x80U;
    CHECK(E2E_P02Check(&wrongList, &rx, data, 8U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P02STATUS_WRONGCRC);
    CHECK(E2E_P02MapStatusToSM(E2E_E_OK, rx.Status, true) == E2E_P_ERROR);
    CHECK(E2E_P02Check(&cfg, &rx, NULL, 0U) == E2E_E_OK);
    CHECK(rx.Status == E2E_P02STATUS_NONEWDATA);
    for (int i = 0; i < 30; ++i)
    {
        CHECK(E2E_P02Check(&cfg, &rx, NULL, 0U) == E2E_E_OK);
    }
    CHECK((rx.NoNewOrRepeatedDataCounter == 15U) && (rx.MaxDeltaCounter == 15U));
}

/* Receiver state of a P01 or P02 message (separate types with the same members) */
typedef struct
{
    E2E_P02ConfigType const* p02; /* NULL: P01 */
    E2E_P01CheckStateType s01;
    E2E_P02CheckStateType s02;
} LegacyRx;

/* Sends one message with the given counter and checks it; returns the mapped status (R4.2 behaviour). */
static E2E_PCheckStatusType legacyFrame(E2E_P01ConfigType const* c01, LegacyRx* rx, uint8_t counter)
{
    uint8_t data[8] = {0};
    if (rx->p02 == NULL)
    {
        E2E_P01ProtectStateType tx = {counter};
        CHECK(E2E_P01Protect(c01, &tx, data, 8U) == E2E_E_OK);
        CHECK(E2E_P01Check(c01, &rx->s01, data, 8U) == E2E_E_OK);
        return E2E_P01MapStatusToSM(E2E_E_OK, rx->s01.Status, true);
    }
    E2E_P02ProtectStateType tx = {(uint8_t)((counter + 15U) % 16U)}; /* P02 increments before use */
    CHECK(E2E_P02Protect(rx->p02, &tx, data, 8U) == E2E_E_OK);
    CHECK(E2E_P02Check(rx->p02, &rx->s02, data, 8U) == E2E_E_OK);
    return E2E_P02MapStatusToSM(E2E_E_OK, rx->s02.Status, true);
}

static uint8_t legacyStatus(LegacyRx const* rx) { return (rx->p02 == NULL) ? rx->s01.Status : rx->s02.Status; }

/* COM-14, board capture 2026-09-16: counter valid up to 4, then 0 for good, Check once per received frame
 * (as the RTE calls it). SyncCounterInit 0 keeps LastValidCounter at 4 while MaxDeltaCounter grows by one per
 * call, so the tenth frame of counter 0 (delta 11) is OKSOMELOST = E2E_P_OK. SyncCounterInit 1 takes the
 * counter of the first wrong-sequence frame as reference: the stalled counter stays REPEATED. */
static void testStalledCounterLegacy(void)
{
    E2E_P01ConfigType c01 = p01SpecConfig(E2E_P01_DATAID_BOTH);
    E2E_P02ConfigType c02 = p02SpecConfig();
    c01.MaxNoNewOrRepeatedData = 14U; /* as generated by rtegen */
    for (int profile = 0; profile < 2; ++profile)
    {
        uint8_t const modulo = (profile == 0) ? 15U : 16U;
        for (uint8_t sync = 0U; sync <= 1U; ++sync)
        {
            c01.SyncCounterInit = sync;
            c02.SyncCounterInit = sync;
            LegacyRx rx;
            rx.p02 = (profile == 0) ? NULL : &c02;
            CHECK(E2E_P01CheckInit(&rx.s01) == E2E_E_OK);
            CHECK(E2E_P02CheckInit(&rx.s02) == E2E_E_OK);
            (void)legacyFrame(&c01, &rx, 0U); /* INITIAL */
            for (uint8_t c = 1U; c <= 4U; ++c)
            {
                CHECK(legacyFrame(&c01, &rx, c) == E2E_P_OK);
            }
            int firstOk = 0;
            for (int n = 1; n <= 30; ++n)
            {
                E2E_PCheckStatusType const s = legacyFrame(&c01, &rx, 0U);
                if ((s == E2E_P_OK) && (firstOk == 0))
                {
                    firstOk = n;
                }
                if (n == 1)
                {
                    CHECK(s == E2E_P_WRONGSEQUENCE); /* the jump 4 -> 0 */
                }
                else if (sync != 0U)
                {
                    CHECK(s == E2E_P_REPEATED);
                }
            }
            /* delta 4 -> 0 is 11 (P01) or 12 (P02): accepted with the (delta - 1)th frame without sync */
            CHECK(firstOk == ((sync == 0U) ? (int)(modulo - 4U - 1U) : 0));
            if (sync != 0U)
            {
                /* the sender runs again: one frame after the stalled one in sequence resynchronises */
                CHECK(legacyFrame(&c01, &rx, 1U) == E2E_P_OK);
                CHECK(legacyStatus(&rx) == E2E_P01STATUS_SYNC);
                CHECK(legacyFrame(&c01, &rx, 2U) == E2E_P_OK);
                CHECK(legacyStatus(&rx) == E2E_P01STATUS_OK);
            }
        }
    }
}

static void testTwoMessagesIndependent(void)
{
    /* two messages (e.g. on two buses) with own configuration and state do not influence each other */
    E2E_P11ConfigType const a = {8U, 0U, 0x0010U, 0U, E2E_P11_DATAID_BOTH, 16U, 1U};
    E2E_P11ConfigType const b = {4U, 8U, 0x0020U, 0U, E2E_P11_DATAID_BOTH, 32U, 1U};
    E2E_P11ProtectStateType txA = {0U};
    E2E_P11ProtectStateType txB = {7U};
    E2E_P11CheckStateType rxA;
    E2E_P11CheckStateType rxB;
    CHECK((E2E_P11CheckInit(&rxA) == E2E_E_OK) && (E2E_P11CheckInit(&rxB) == E2E_E_OK));
    rxB.Counter = 6U;
    for (int i = 0; i < 20; ++i)
    {
        uint8_t dataA[2] = {(uint8_t)i, 0U};
        uint8_t dataB[4] = {0U, 0U, (uint8_t)i, 0xEEU};
        CHECK(E2E_P11Protect(&a, &txA, dataA, 2U) == E2E_E_OK);
        CHECK(E2E_P11Protect(&b, &txB, dataB, 4U) == E2E_E_OK);
        CHECK(E2E_P11Check(&a, &rxA, dataA, 2U) == E2E_E_OK);
        CHECK(E2E_P11Check(&b, &rxB, dataB, 4U) == E2E_E_OK);
        CHECK((rxA.Status == E2E_P11STATUS_OK) && (rxB.Status == E2E_P11STATUS_OK));
        /* message A checked with the configuration of B fails */
        E2E_P11CheckStateType cross = rxB;
        uint8_t fake[4]             = {dataA[0], dataA[1], 0U, 0U};
        CHECK(E2E_P11Check(&b, &cross, fake, 4U) == E2E_E_OK);
        CHECK(cross.Status == E2E_P11STATUS_ERROR);
    }
}

static void testInputErrors(void)
{
    E2E_P01ConfigType p01 = p01SpecConfig(E2E_P01_DATAID_BOTH);
    E2E_P11ConfigType p11 = p11SpecConfig(E2E_P11_DATAID_BOTH);
    E2E_P02ConfigType p02 = p02SpecConfig();
    E2E_P01ProtectStateType t01 = {0U};
    E2E_P02ProtectStateType t02 = {0U};
    E2E_P11ProtectStateType t11 = {0U};
    E2E_P01CheckStateType c01;
    E2E_P02CheckStateType c02;
    E2E_P11CheckStateType c11;
    uint8_t data[8] = {0};
    (void)E2E_P01CheckInit(&c01);
    (void)E2E_P02CheckInit(&c02);
    (void)E2E_P11CheckInit(&c11);

    CHECK(E2E_P01ProtectInit(NULL) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P01CheckInit(NULL) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P02ProtectInit(NULL) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P02CheckInit(NULL) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P11ProtectInit(NULL) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P11CheckInit(NULL) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P01CheckConfig(NULL) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P01Protect(NULL, &t01, data, 8U) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P01Protect(&p01, &t01, NULL, 8U) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P02Protect(&p02, NULL, data, 8U) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P11Protect(&p11, &t11, NULL, 8U) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P01Check(&p01, NULL, data, 8U) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P02Check(NULL, &c02, data, 8U) == E2E_E_INPUTERR_NULL);
    CHECK(E2E_P11Check(&p11, NULL, data, 8U) == E2E_E_INPUTERR_NULL);

    /* wrong length, inconsistent NULL/length: state untouched */
    CHECK(E2E_P01Protect(&p01, &t01, data, 7U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P02Protect(&p02, &t02, data, 9U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P11Protect(&p11, &t11, data, 7U) == E2E_E_INPUTERR_WRONG);
    CHECK((t01.Counter == 0U) && (t02.Counter == 0U) && (t11.Counter == 0U));
    CHECK(E2E_P01Check(&p01, &c01, data, 7U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P01Check(&p01, &c01, NULL, 8U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P02Check(&p02, &c02, data, 0U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P11Check(&p11, &c11, data, 9U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P11Check(&p11, &c11, NULL, 8U) == E2E_E_INPUTERR_WRONG);
    CHECK((c01.MaxDeltaCounter == 0U) && (c01.Status == E2E_P01STATUS_NONEWDATA));
    CHECK((c11.Counter == 0x0EU) && (c11.Status == E2E_P11STATUS_ERROR));
    CHECK(E2E_P11MapStatusToSM(E2E_E_INPUTERR_WRONG, E2E_P11STATUS_OK) == E2E_P_ERROR);
    CHECK(E2E_P02MapStatusToSM(E2E_E_INPUTERR_NULL, E2E_P02STATUS_OK, true) == E2E_P_ERROR);
    /* invalid protect state counter */
    t11.Counter = 15U;
    CHECK(E2E_P11Protect(&p11, &t11, data, 8U) == E2E_E_INPUTERR_WRONG);
    t01.Counter = 15U;
    CHECK(E2E_P01Protect(&p01, &t01, data, 8U) == E2E_E_INPUTERR_WRONG);

    /* configuration errors */
    E2E_P11ConfigType bad = p11;
    bad.DataLength        = 60U;
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad            = p11;
    bad.DataLength = 264U;
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad           = p11;
    bad.CRCOffset = 4U;
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad           = p11;
    bad.CRCOffset = 64U;
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad               = p11;
    bad.CounterOffset = 6U;
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad               = p11;
    bad.CounterOffset = 4U; /* counter inside the CRC byte */
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad            = p11;
    bad.DataIDMode = E2E_P01_DATAID_ALT; /* not a P11 mode */
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad                    = p11;
    bad.DataIDMode         = E2E_P11_DATAID_NIBBLE;
    bad.DataIDNibbleOffset = 8U; /* same nibble as the counter */
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    bad.DataIDNibbleOffset = 12U;
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_OK);
    bad.DataID = 0x1123U; /* 13-bit ID in mode NIBBLE */
    CHECK(E2E_P11CheckConfig(&bad) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P11Protect(&bad, &t11, data, 8U) == E2E_E_INPUTERR_WRONG);
    CHECK(E2E_P11Check(&bad, &c11, NULL, 0U) == E2E_E_INPUTERR_WRONG);

    E2E_P01ConfigType bad01 = p01;
    bad01.DataIDMode        = 4U;
    CHECK(E2E_P01CheckConfig(&bad01) == E2E_E_INPUTERR_WRONG);
    bad01                    = p01;
    bad01.DataIDMode         = E2E_P01_DATAID_NIBBLE;
    bad01.DataIDNibbleOffset = 3U;
    CHECK(E2E_P01CheckConfig(&bad01) == E2E_E_INPUTERR_WRONG);
    bad01            = p01;
    bad01.DataIDMode = E2E_P01_DATAID_LOW; /* the example uses 0x123 in mode LOW: the high byte is ignored */
    CHECK(E2E_P01CheckConfig(&bad01) == E2E_E_OK);

    E2E_P02ConfigType bad02 = p02;
    bad02.DataLength        = 8U;
    CHECK(E2E_P02CheckConfig(&bad02) == E2E_E_INPUTERR_WRONG);
    bad02.DataLength = 2048U;
    CHECK(E2E_P02CheckConfig(&bad02) == E2E_E_OK);
    bad02.DataLength = 2056U;
    CHECK(E2E_P02CheckConfig(&bad02) == E2E_E_INPUTERR_WRONG);
}

int main(void)
{
    testCrc();
    testSpecExamplesP01P11();
    testSpecExamplesP02();
    testCounterWrapAndSequenceP11();
    testDataIdNibble();
    testLegacyP01();
    testLegacyP02();
    testStalledCounterLegacy();
    testTwoMessagesIndependent();
    testInputErrors();
    printf("E2E host test: %d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
