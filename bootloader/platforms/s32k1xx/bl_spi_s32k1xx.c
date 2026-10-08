/* SPI master of the bootloader, family s32k1xx (LPSPI, polling). Implements bl_spi.h for the device hooks in
 * bootloader/devices/<module>, which stay free of family registers (BL-05).
 *
 * Wiring (instance, chip select, pins) and the dividers come from the generated bootloader/bl_board.h, which
 * derives them from the board and the clock model (tools/nxconfig/clock.py: SOSCDIV2 -> PRESCALE/SCKDIV,
 * ARCH-08). The frame format (bits, CPOL, CPHA) comes from the device hook. */

#include "bl_spi.h"

#include "bl_hal.h"
#include "bootloader/bl_board.h"
#include "mcu/mcu.h"

/* blgen names the SPI macros of the generated board part after the device hook that needs the port
 * (tools/bswgen/blgen.py, hook_sbc_uja113x: BL_SBC_SPI*). The family HAL maps them once, so that no device name
 * appears below; proposal of BL-05: blgen emits a neutral name (BL_SPI_*) per bootloader SPI port. */
#if defined(BL_SBC_SPI)
#define SPI          BL_SBC_SPI
#define SPI_PCC_INDEX BL_SBC_SPI_PCC_INDEX
#define SPI_PCS      BL_SBC_SPI_PCS
#define SPI_PRESCALE BL_SBC_SPI_PRESCALE
#define SPI_SCKDIV   BL_SBC_SPI_SCKDIV
#define SPI_PINS()   bl_sbc_spi_pins()
#else
#error "the generated bootloader/bl_board.h has no SPI port, but a device hook needs one (blgen hook macros)"
#endif

#define SPI_TIMEOUT_MS   10UL
#define PCC_PCS_SOSCDIV2 1U

static bool wait_flag(uint32_t mask)
{
    uint32_t const start = hal_millis();
    while ((SPI->SR & mask) == 0U)
    {
        if ((hal_millis() - start) > SPI_TIMEOUT_MS)
        {
            return false;
        }
    }
    return true;
}

void bl_spi_init(bl_spi_frame_t const* frame)
{
    SPI_PINS();

    IP_PCC->PCCn[SPI_PCC_INDEX] = 0U;
    IP_PCC->PCCn[SPI_PCC_INDEX] = PCC_PCCn_PCS(PCC_PCS_SOSCDIV2);
    IP_PCC->PCCn[SPI_PCC_INDEX] |= PCC_PCCn_CGC_MASK;

    SPI->CR    = LPSPI_CR_RST_MASK;
    SPI->CR    = 0U;
    SPI->CFGR1 = LPSPI_CFGR1_MASTER_MASK | LPSPI_CFGR1_NOSTALL_MASK;
    /* SOSCDIV2 / 2^PRESCALE / (SCKDIV + 2), delays one SCK period (values and rate: bl_board.h) */
    SPI->CCR = LPSPI_CCR_SCKDIV(SPI_SCKDIV) | LPSPI_CCR_DBT(SPI_SCKDIV) | LPSPI_CCR_PCSSCK(SPI_SCKDIV)
               | LPSPI_CCR_SCKPCS(SPI_SCKDIV);
    SPI->FCR = 0U;
    SPI->CR  = LPSPI_CR_MEN_MASK | LPSPI_CR_DBGEN_MASK;
    /* Frame of the device; most significant bit first (TCR.LSBF = 0) */
    SPI->TCR = (frame->cpol ? LPSPI_TCR_CPOL_MASK : 0UL) | (frame->cpha ? LPSPI_TCR_CPHA_MASK : 0UL)
               | LPSPI_TCR_PRESCALE(SPI_PRESCALE) | LPSPI_TCR_PCS(SPI_PCS)
               | LPSPI_TCR_FRAMESZ((uint32_t)frame->bits - 1UL);
}

bool bl_spi_transfer(uint32_t tx, uint32_t* rx)
{
    if (!wait_flag(LPSPI_SR_TDF_MASK))
    {
        return false;
    }
    SPI->SR  = LPSPI_SR_TCF_MASK;
    SPI->TDR = tx;
    if (!wait_flag(LPSPI_SR_RDF_MASK))
    {
        return false;
    }
    *rx = SPI->RDR;
    return wait_flag(LPSPI_SR_TCF_MASK);
}
