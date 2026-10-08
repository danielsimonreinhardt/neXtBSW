#pragma once

#include "mcu/mcu.h"
#include "spi/ISpiDevice.h"

#include <cstdint>

namespace bsp
{
namespace spi
{

struct LpspiConfig
{
    LPSPI_Type* base;
    uint32_t functionalClockHz; // PCC clock source after its DIV2 divider
    uint32_t baudrateHz;        // upper limit, the next slower achievable rate is used
    uint8_t pcs;                // chip select index
    uint8_t frameBits;          // 8..32 (transfer), 8..64 except 33 (transferLong), see isSupportedLpspiFrameBits
    bool cpol;
    bool cpha;
    uint32_t timeoutUs; // per frame
};

/**
 * SYS-95 (finding DFA-6 (b) of doc/safety/dfa.md, SPI-3): every wait of a frame is bounded twice - by timeoutUs on
 * the OpenBSW system time, and by a budget of polls that does not depend on it, like the clock driver's waits
 * (PLAT-85). If the system time stops (DWT switched off, PLAT-67), a wait for a flag the LPSPI never sets would
 * otherwise never end. A poll takes at least one core cycle and the core of the S32K1 family runs at most at 112 MHz
 * (HSRUN, [DS] S32K1xx Data Sheet Rev. 13 "Operating modes"), so the budget timeoutUs x 112 never gives up before
 * timeoutUs has passed; in practice a poll (peripheral read, compare, count) takes several cycles, so with a stopped
 * system time a wait ends after a few times its time-out - bounded.
 */
constexpr uint32_t LPSPI_POLLS_PER_US = 112U;

/** Poll budget of one wait for a time-out of `timeoutUs` (saturated, at least one poll). */
constexpr uint32_t lpspiPollBudget(uint32_t const timeoutUs)
{
    return (timeoutUs == 0U) ? 1U
           : (timeoutUs > (0xFFFFFFFFU / LPSPI_POLLS_PER_US)) ? 0xFFFFFFFFU
                                                              : (timeoutUs * LPSPI_POLLS_PER_US);
}

/**
 * Frame sizes the LPSPI master supports (S32K1xx RM Rev. 13, 51.3.1.15.4 TCR[FRAMESZ]): at least 8 bits; a frame
 * above 32 bits is split into 32-bit words with the remainder in the last word, whose minimum is 2 bits (33 bits
 * "is not supported"). Two words at most here (64 bits), both fit into the 4-word FIFOs (51.2.1).
 */
constexpr bool isSupportedLpspiFrameBits(uint8_t const bits)
{
    return (bits >= 8U) && (bits <= 64U) && (bits != 33U);
}

/**
 * Polling LPSPI master for a single device (S32K1xx).
 *
 * Pin muxing comes from the IO configuration and the peripheral clock from the clock driver.
 * Not thread safe: all transfers must run in one task context.
 */
class Lpspi : public ISpiDevice
{
public:
    explicit Lpspi(LpspiConfig const& config);

    void init();
    /** Frames of up to 32 bits; false for a longer configured frame (use transferLong). */
    bool transfer(uint32_t tx, uint32_t& rx) override;
    /**
     * Frames of 8..64 bits (not 33). Above 32 bits the frame goes out as two TDR words in one chip select period:
     * first the upper 32 bits, then the remaining (frameBits - 32) low-order bits, each MSB first (RM 51.3.1.15.4:
     * "the last load of the transmit FIFO ... will contain the remainder bits"); the received words are joined the
     * same way. Up to 32 bits it is the same as transfer().
     */
    bool transferLong(uint64_t tx, uint64_t& rx) override;

    uint32_t getBaudrateHz() const { return _baudrateHz; }
    uint32_t getErrorCount() const { return _errorCount; }

private:
    bool waitFor(uint32_t mask, uint32_t startUs) const;
    bool waitForReceivedWords(uint32_t words, uint32_t startUs) const;
    void startFrame() const;

    LpspiConfig const& _config;
    uint32_t _baudrateHz;
    uint32_t _errorCount;
    bool _initialized;
};

} // namespace spi
} // namespace bsp
