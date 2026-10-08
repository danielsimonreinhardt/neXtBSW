#include "spi/Lpspi.h"

#include "bsp/timer/SystemTimer.h"
#include "spi/LpspiBaudrate.h"

namespace bsp
{
namespace spi
{

Lpspi::Lpspi(LpspiConfig const& config)
: _config(config), _baudrateHz(0U), _errorCount(0U), _initialized(false)
{}

void Lpspi::init()
{
    LPSPI_Type* const spi     = _config.base;
    LpspiDivider const divider = calculateLpspiDivider(_config.functionalClockHz, _config.baudrateHz);
    _baudrateHz               = divider.baudrateHz;

    // Half an SCK period between chip select and clock edges and between frames.
    uint32_t const halfPeriodTicks = (static_cast<uint32_t>(divider.sckdiv) + 2UL) / 2UL;
    uint32_t const delay           = (halfPeriodTicks > 1UL) ? (halfPeriodTicks - 1UL) : 0UL;

    spi->CR    = LPSPI_CR_RST_MASK;
    spi->CR    = 0U;
    spi->CFGR1 = LPSPI_CFGR1_MASTER_MASK | LPSPI_CFGR1_NOSTALL_MASK;
    spi->CCR   = LPSPI_CCR_SCKDIV(divider.sckdiv) | LPSPI_CCR_DBT(delay) | LPSPI_CCR_PCSSCK(delay)
               | LPSPI_CCR_SCKPCS(delay);
    spi->FCR   = 0U;
    spi->CR    = LPSPI_CR_MEN_MASK | LPSPI_CR_DBGEN_MASK;
    spi->TCR   = LPSPI_TCR_CPOL(_config.cpol ? 1U : 0U) | LPSPI_TCR_CPHA(_config.cpha ? 1U : 0U)
               | LPSPI_TCR_PRESCALE(divider.prescale) | LPSPI_TCR_PCS(_config.pcs)
               | LPSPI_TCR_FRAMESZ(static_cast<uint32_t>(_config.frameBits) - 1UL);
    _initialized = true;
}

bool Lpspi::waitFor(uint32_t const mask, uint32_t const startUs) const
{
    // SYS-95 (DFA-6 b): the poll budget bounds the wait also when the system time stands still
    uint32_t polls = lpspiPollBudget(_config.timeoutUs);
    while ((_config.base->SR & mask) == 0U)
    {
        if (((getSystemTimeUs32Bit() - startUs) > _config.timeoutUs) || (polls == 0U))
        {
            return false;
        }
        --polls;
    }
    return true;
}

bool Lpspi::waitForReceivedWords(uint32_t const words, uint32_t const startUs) const
{
    uint32_t polls = lpspiPollBudget(_config.timeoutUs);
    while (((_config.base->FSR & LPSPI_FSR_RXCOUNT_MASK) >> LPSPI_FSR_RXCOUNT_SHIFT) < words)
    {
        if (((getSystemTimeUs32Bit() - startUs) > _config.timeoutUs) || (polls == 0U))
        {
            return false;
        }
        --polls;
    }
    return true;
}

void Lpspi::startFrame() const
{
    LPSPI_Type* const spi = _config.base;
    // Drop stale data from a previous timed-out frame and clear sticky flags.
    spi->CR = spi->CR | LPSPI_CR_RRF_MASK | LPSPI_CR_RTF_MASK;
    spi->SR = LPSPI_SR_TCF_MASK | LPSPI_SR_TEF_MASK | LPSPI_SR_REF_MASK;
}

bool Lpspi::transferLong(uint64_t const tx, uint64_t& rx)
{
    uint8_t const bits = _config.frameBits;
    if ((!_initialized) || (!isSupportedLpspiFrameBits(bits)))
    {
        return false;
    }
    if (bits <= 32U)
    {
        uint32_t word = 0U;
        bool const ok = transfer(static_cast<uint32_t>(tx & 0xFFFFFFFFULL), word);
        rx            = word;
        return ok;
    }
    LPSPI_Type* const spi = _config.base;
    uint32_t const start  = getSystemTimeUs32Bit();
    startFrame();

    // RM Rev. 13, 51.3.1.15.4: a frame above 32 bits is divided into words of 32 bits, the last word holds the
    // remainder bits. MSB first (TCR[LSBF] 0), so the first word carries the upper 32 bits of the frame.
    uint32_t const lowBits   = static_cast<uint32_t>(bits) - 32U; // 2..32
    uint64_t const lowMask   = (lowBits >= 32U) ? 0xFFFFFFFFULL : ((1ULL << lowBits) - 1ULL);
    uint64_t const frameMask = (bits >= 64U) ? ~0ULL : ((1ULL << bits) - 1ULL);
    uint64_t const frame     = tx & frameMask;
    bool ok                  = waitFor(LPSPI_SR_TDF_MASK, start);
    if (ok)
    {
        // Both words fit into the emptied 4-word transmit FIFO (RM 51.2.1), the frame keeps PCS asserted.
        spi->TDR = static_cast<uint32_t>(frame >> lowBits);
        spi->TDR = static_cast<uint32_t>(frame & lowMask);
        ok       = waitForReceivedWords(2U, start) && waitFor(LPSPI_SR_TCF_MASK, start);
    }
    if (ok)
    {
        uint64_t const upper = spi->RDR;
        uint64_t const lower = spi->RDR;
        rx                   = ((upper << lowBits) | (lower & lowMask)) & frameMask;
    }
    else
    {
        ++_errorCount;
    }
    return ok;
}

bool Lpspi::transfer(uint32_t const tx, uint32_t& rx)
{
    if ((!_initialized) || (_config.frameBits > 32U))
    {
        return false;
    }
    LPSPI_Type* const spi = _config.base;
    uint32_t const start  = getSystemTimeUs32Bit();
    startFrame();

    uint32_t const frameMask
        = (_config.frameBits >= 32U) ? 0xFFFFFFFFUL : ((1UL << _config.frameBits) - 1UL);
    bool ok = waitFor(LPSPI_SR_TDF_MASK, start);
    if (ok)
    {
        spi->TDR = tx & frameMask;
        ok       = waitFor(LPSPI_SR_RDF_MASK, start) && waitFor(LPSPI_SR_TCF_MASK, start);
    }
    if (ok)
    {
        rx = spi->RDR & frameMask;
    }
    else
    {
        ++_errorCount;
    }
    return ok;
}

} // namespace spi
} // namespace bsp
