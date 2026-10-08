// SPDX-License-Identifier: Apache-2.0
/* Host test of the LPSPI master (IO-25) against a register model that follows the S32K1xx Reference Manual
 * Rev. 13, chapter 51 (stubs/mcu/mcu.h): frames of 16, 32, 40 (TMC6200), 48 (MC33771C) and 64 bits, word order of
 * frames above 32 bits, unsupported frame sizes, timeout and recovery. The model turns the TDR words into the bit
 * sequence on MOSI itself (independent of the driver) and answers from a slave value MSB first on MISO.
 * Build and run (zig c++, no hardware):
 *   .venv/Scripts/python -m ziglang c++ -std=c++17 -O1 -Wall -Wextra -Wconversion -Werror \
 *       -Iplatforms/s32k1xx/bsp/bspLpspi/test/host/stubs -Iplatforms/s32k1xx/bsp/bspLpspi/include \
 *       -Ilibs/bsp/bspSpi/include -isystemexternal/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp \
 *       platforms/s32k1xx/bsp/bspLpspi/src/spi/Lpspi.cpp platforms/s32k1xx/bsp/bspLpspi/test/host/LpspiHostTest.cpp \
 *       -o build/host/lpspi_test.exe
 *   build/host/lpspi_test.exe
 */

#include "spi/Lpspi.h"

#include "bsp/timer/SystemTimer.h" // SYS-95: the scripted time of the stub

#include <cstdio>
#include <deque>
#include <vector>

uint32_t nxTestNowUs = 0U;

namespace
{
int failures = 0;
int checks   = 0;

#define CHECK(cond)                                                       \
    do                                                                    \
    {                                                                     \
        ++checks;                                                         \
        if (!(cond))                                                      \
        {                                                                 \
            ++failures;                                                   \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                 \
    } while (false)

constexpr uint32_t FIFO_WORDS = 4U; // RM 51.2.1: transmit and receive FIFO of 4 words

/* Behaviour of one LPSPI master with one slave on the wire. */
struct Model
{
    uint32_t cr  = 0U;
    uint32_t tcr = 0x1FU; // reset value: FRAMESZ 31 (RM 51.3.1.15.3)
    bool tcf     = false;
    std::deque<uint32_t> tx; // words of the frame in progress
    std::deque<uint32_t> rx;
    std::vector<uint8_t> mosi;          // every bit shifted out, all frames
    std::vector<uint32_t> tdrWrites;    // raw TDR words
    std::vector<uint32_t> frameLengths; // bits per chip select period
    uint64_t slave       = 0U;          // value the slave shifts out MSB first in the next frame
    bool silent          = false;       // clock stuck: nothing is shifted, no receive data, no TCF
    bool remainderDirty  = false;       // bits above the width of a word were set in a TDR write
    bool txOverflow      = false;
    bool continuousFrame = false;       // TCR[CONT] seen while shifting

    uint32_t frameBits() const { return (tcr & LPSPI_TCR_FRAMESZ_MASK) + 1U; }

    static uint32_t wordWidth(uint32_t const frame, uint32_t const index)
    {
        uint32_t const words = (frame + 31U) / 32U;
        return (index + 1U < words) ? 32U : (frame - (32U * (words - 1U)));
    }

    void shiftFrame()
    {
        uint32_t const frame = frameBits();
        uint32_t const words = (frame + 31U) / 32U;
        continuousFrame      = continuousFrame || ((tcr & LPSPI_TCR_CONT_MASK) != 0U);
        // MOSI: word by word, each from its most significant bit (TCR[LSBF] 0)
        for (uint32_t k = 0U; k < words; ++k)
        {
            uint32_t const width = wordWidth(frame, k);
            uint32_t const word  = tx[k];
            if ((width < 32U) && ((word >> width) != 0U))
            {
                remainderDirty = true;
            }
            for (uint32_t b = width; b > 0U; --b)
            {
                mosi.push_back(static_cast<uint8_t>((word >> (b - 1U)) & 1U));
            }
        }
        // MISO: the slave value MSB first, stored the same way (one word per load of the receive FIFO)
        uint32_t bit = frame;
        for (uint32_t k = 0U; k < words; ++k)
        {
            uint32_t const width = wordWidth(frame, k);
            uint32_t word        = 0U;
            for (uint32_t b = 0U; b < width; ++b)
            {
                --bit;
                word = (word << 1U) | static_cast<uint32_t>((slave >> bit) & 1U);
            }
            rx.push_back(word);
        }
        tx.clear();
        frameLengths.push_back(frame);
        tcf = true;
    }
};

Model model;

void resetModel()
{
    model       = Model();
    nxTestNowUs = 0U;
}

std::vector<uint8_t> bitsMsbFirst(uint64_t const value, uint32_t const bits)
{
    std::vector<uint8_t> out;
    for (uint32_t b = bits; b > 0U; --b)
    {
        out.push_back(static_cast<uint8_t>((value >> (b - 1U)) & 1U));
    }
    return out;
}

NxTestLpspi fakeLpspi;

::bsp::spi::LpspiConfig makeConfig(uint8_t const bits)
{
    // TMC6200/MC33771C style: SPI mode 3 resp. 1, 1 MHz from 48 MHz
    return {&fakeLpspi, 48000000UL, 1000000UL, 0U, bits, true, true, 5000UL};
}

} // namespace

// ----------------------------------------------------------------------------------------- register model hooks
uint32_t nxTestLpspiReadCr() { return model.cr; }

void nxTestLpspiWriteCr(uint32_t const value)
{
    if ((value & LPSPI_CR_RTF_MASK) != 0U)
    {
        model.tx.clear();
    }
    if ((value & LPSPI_CR_RRF_MASK) != 0U)
    {
        model.rx.clear();
    }
    if ((value & LPSPI_CR_RST_MASK) != 0U)
    {
        model.tx.clear();
        model.rx.clear();
        model.tcf = false;
    }
    model.cr = value & ~(LPSPI_CR_RTF_MASK | LPSPI_CR_RRF_MASK);
}

uint32_t nxTestLpspiReadSr()
{
    uint32_t sr = 0U;
    if (model.tx.size() < 1U) // TXWATER 0: TDF while the transmit FIFO is empty
    {
        sr |= LPSPI_SR_TDF_MASK;
    }
    if (!model.rx.empty()) // RXWATER 0: RDF with at least one word
    {
        sr |= LPSPI_SR_RDF_MASK;
    }
    if (model.tcf)
    {
        sr |= LPSPI_SR_TCF_MASK;
    }
    return sr;
}

void nxTestLpspiWriteSr(uint32_t const value)
{
    if ((value & LPSPI_SR_TCF_MASK) != 0U)
    {
        model.tcf = false;
    }
}

uint32_t nxTestLpspiReadFsr()
{
    return LPSPI_FSR_RXCOUNT(static_cast<uint32_t>(model.rx.size()))
           | LPSPI_FSR_TXCOUNT(static_cast<uint32_t>(model.tx.size()));
}

uint32_t nxTestLpspiReadTcr() { return model.tcr; }

void nxTestLpspiWriteTcr(uint32_t const value) { model.tcr = value; }

void nxTestLpspiWriteTdr(uint32_t const value)
{
    model.tdrWrites.push_back(value);
    if (model.tx.size() >= FIFO_WORDS)
    {
        model.txOverflow = true;
        return;
    }
    model.tx.push_back(value);
    uint32_t const words = (model.frameBits() + 31U) / 32U;
    if ((!model.silent) && (model.tx.size() == words))
    {
        model.shiftFrame();
    }
}

uint32_t nxTestLpspiReadRdr()
{
    if (model.rx.empty())
    {
        return 0xDEADBEEFUL; // reading an empty FIFO is a driver error the checks below would show
    }
    uint32_t const word = model.rx.front();
    model.rx.pop_front();
    return word;
}

namespace
{
using ::bsp::spi::isSupportedLpspiFrameBits;
using ::bsp::spi::Lpspi;
using ::bsp::spi::LpspiConfig;

/** One long frame: wire order, words, received value; returns the model state for further checks. */
void checkLongFrame(uint8_t const bits, uint64_t const tx, uint64_t const slave)
{
    resetModel();
    LpspiConfig const config = makeConfig(bits);
    Lpspi spi(config);
    spi.init();
    CHECK(model.frameBits() == bits);
    CHECK((model.tcr & LPSPI_TCR_LSBF_MASK) == 0U);
    model.slave     = slave;
    uint64_t rx     = 0U;
    uint64_t const mask = (bits >= 64U) ? ~0ULL : ((1ULL << bits) - 1ULL);
    CHECK(spi.transferLong(tx, rx));
    CHECK(rx == (slave & mask));
    CHECK(model.frameLengths.size() == 1U);
    CHECK(model.frameLengths[0] == bits);
    CHECK(model.mosi == bitsMsbFirst(tx & mask, bits));
    CHECK(model.tdrWrites.size() == ((bits > 32U) ? 2U : 1U));
    if (bits > 32U)
    {
        uint32_t const low = static_cast<uint32_t>(bits) - 32U;
        // RM 51.3.1.15.4: first word = upper 32 bits, last word = remainder bits
        CHECK(model.tdrWrites[0] == static_cast<uint32_t>((tx & mask) >> low));
        CHECK(model.tdrWrites[1] == static_cast<uint32_t>((tx & mask) & ((low >= 32U) ? 0xFFFFFFFFULL : ((1ULL << low) - 1ULL))));
    }
    CHECK(!model.remainderDirty);
    CHECK(!model.txOverflow);
    CHECK(!model.continuousFrame);
    CHECK(model.rx.empty());
    CHECK(spi.getErrorCount() == 0U);
}

void frame40BitsTmc6200()
{
    // TMC6200 read of IOIN (address 0x04, datasheet p.18): 0x04 00000000; answer version 0x10 in bits 31:24
    checkLongFrame(40U, 0x0400000000ULL, 0x0410000040ULL);
    // write datagram GCONF with bits in both words and a dirty upper part of tx (not on the wire)
    checkLongFrame(40U, 0xFF8012345678ULL, 0xA5C35AF00FULL);
    checkLongFrame(40U, 0x00000000FFULL, 0xFF00000000ULL);
}

void frame48BitsMc33771c()
{
    checkLongFrame(48U, 0x0123456789ABULL, 0xFEDCBA987654ULL);
    checkLongFrame(48U, 0x800000000001ULL, 0x000000010080ULL);
}

void otherFrameSizes()
{
    checkLongFrame(34U, 0x3FFFFFFFEULL, 0x200000001ULL); // smallest remainder word: 2 bits
    checkLongFrame(64U, 0x0123456789ABCDEFULL, 0xF0E1D2C3B4A59687ULL);
    checkLongFrame(32U, 0x89ABCDEFULL, 0x13579BDFULL);    // single word through transferLong
    checkLongFrame(16U, 0xA55AULL, 0x1234ULL);
}

void shortTransferUnchanged()
{
    resetModel();
    LpspiConfig const config = makeConfig(16U);
    Lpspi spi(config);
    spi.init();
    model.slave = 0x0F0FU;
    uint32_t rx = 0U;
    CHECK(spi.transfer(0x12345U, rx)); // bits above the frame are not sent
    CHECK(rx == 0x0F0FU);
    CHECK(model.mosi == bitsMsbFirst(0x2345U, 16U));
    CHECK(model.tdrWrites.size() == 1U);
}

void unsupportedFrameSizes()
{
    CHECK(!isSupportedLpspiFrameBits(7U));
    CHECK(isSupportedLpspiFrameBits(8U));
    CHECK(isSupportedLpspiFrameBits(32U));
    CHECK(!isSupportedLpspiFrameBits(33U)); // RM: "a frame size of 33 bits (or similar) is not supported"
    CHECK(isSupportedLpspiFrameBits(34U));
    CHECK(isSupportedLpspiFrameBits(64U));
    CHECK(!isSupportedLpspiFrameBits(65U));

    for (uint8_t const bits : {uint8_t{33U}, uint8_t{65U}, uint8_t{7U}})
    {
        resetModel();
        LpspiConfig const config = makeConfig(bits);
        Lpspi spi(config);
        spi.init();
        uint64_t rx = 0U;
        CHECK(!spi.transferLong(0x1ULL, rx));
        CHECK(model.tdrWrites.empty());
    }

    // a long frame through the 32-bit call: refused, nothing on the wire
    resetModel();
    LpspiConfig const config = makeConfig(40U);
    Lpspi spi(config);
    spi.init();
    uint32_t rx32 = 0U;
    CHECK(!spi.transfer(0x1U, rx32));
    CHECK(model.tdrWrites.empty());
}

void notInitialized()
{
    resetModel();
    LpspiConfig const config = makeConfig(40U);
    Lpspi spi(config);
    uint64_t rx = 0U;
    CHECK(!spi.transferLong(0x0400000000ULL, rx));
    CHECK(model.tdrWrites.empty());
}

void timeoutAndRecovery()
{
    resetModel();
    LpspiConfig const config = makeConfig(40U);
    Lpspi spi(config);
    spi.init();
    model.silent = true;
    uint64_t rx  = 0U;
    CHECK(!spi.transferLong(0x0400000000ULL, rx));
    CHECK(spi.getErrorCount() == 1U);
    CHECK(model.frameLengths.empty());
    // clock back: the stale word of the failed frame must not shift the next frame by one word
    model.silent   = false;
    model.slave    = 0x0410000040ULL;
    uint32_t const before = static_cast<uint32_t>(model.mosi.size());
    CHECK(before == 0U);
    CHECK(spi.transferLong(0x0400000000ULL, rx));
    CHECK(rx == 0x0410000040ULL);
    CHECK(model.mosi == bitsMsbFirst(0x0400000000ULL, 40U));
    CHECK(spi.getErrorCount() == 1U);
}

void stoppedSystemTime()
{
    // SYS-95 (DFA-6 b of doc/safety/dfa.md, SPI-3): the system time stands still (DWT switched off) and the clock of
    // the bus is stuck - before SYS-95 the wait for RDF never ended. Now each wait gives up after its poll budget.
    using ::bsp::spi::lpspiPollBudget;
    resetModel();
    LpspiConfig const config = makeConfig(40U);
    Lpspi spi(config);
    spi.init();
    model.silent      = true;
    nxTestTimeStopped = true;
    nxTestTimeReads   = 0U;
    uint64_t rx       = 0U;
    CHECK(!spi.transferLong(0x0400000000ULL, rx));
    CHECK(spi.getErrorCount() == 1U);
    uint32_t const budget = lpspiPollBudget(config.timeoutUs);
    CHECK(nxTestTimeReads > budget);                // the budget was used, not the time
    CHECK(nxTestTimeReads <= (3U * (budget + 1U)) + 1U); // at most three waits per frame, each bounded
    nxTestTimeStopped = false;
    // the 32-bit path as well
    resetModel();
    LpspiConfig const shortConfig = makeConfig(16U);
    Lpspi shortSpi(shortConfig);
    shortSpi.init();
    model.silent      = true;
    nxTestTimeStopped = true;
    uint32_t word     = 0U;
    CHECK(!shortSpi.transfer(0x1234U, word));
    nxTestTimeStopped = false;
    model.silent      = false;
    // budget arithmetic: 112 polls per us, saturated, at least one
    CHECK(lpspiPollBudget(5000U) == 560000U);
    CHECK(lpspiPollBudget(0U) == 1U);
    CHECK(lpspiPollBudget(0xFFFFFFFFU) == 0xFFFFFFFFU);
    CHECK(lpspiPollBudget(38347922U) == 0xFFFFFFE0U); // the largest time-out without saturation
}

void interfaceDefault()
{
    // An ISpiDevice that knows only 32-bit frames refuses long frames (default of the interface).
    class ShortOnly : public ::bsp::spi::ISpiDevice
    {
    public:
        bool transfer(uint32_t const tx, uint32_t& rx) override
        {
            rx = tx;
            return true;
        }
    };

    ShortOnly device;
    ::bsp::spi::ISpiDevice& spi = device;
    uint64_t rx                 = 7U;
    CHECK(!spi.transferLong(0x0400000000ULL, rx));
}

} // namespace

int main()
{
    struct
    {
        char const* name;
        void (*fn)();
    } const tests[] = {
        {"frame40BitsTmc6200", frame40BitsTmc6200},
        {"frame48BitsMc33771c", frame48BitsMc33771c},
        {"otherFrameSizes", otherFrameSizes},
        {"shortTransferUnchanged", shortTransferUnchanged},
        {"unsupportedFrameSizes", unsupportedFrameSizes},
        {"notInitialized", notInitialized},
        {"timeoutAndRecovery", timeoutAndRecovery},
        {"stoppedSystemTime", stoppedSystemTime},
        {"interfaceDefault", interfaceDefault},
    };
    for (auto const& test : tests)
    {
        int const before = failures;
        test.fn();
        std::printf("%s %s\n", (failures == before) ? "ok  " : "FAIL", test.name);
    }
    std::printf("%d checks, %d failures\n", checks, failures);
    return (failures == 0) ? 0 : 1;
}
