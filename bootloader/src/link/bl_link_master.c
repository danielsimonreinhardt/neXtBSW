/* The master's end of one link (BL-15). See bl_link_master.h for what this is and is not. */

#include "bl_link_master.h"

#include "bl_link_hal.h"

bool bl_link_master_open(bl_link_master_t* m, uint8_t port, mcuLinkConfig_t const* cfg, uint8_t* txFrame,
                         uint8_t* rxFrame, uint32_t nowMs)
{
    mcuLinkConfig_t own = *cfg;
    own.master          = true;
    if (!mcuLink_init(&m->link, &own, nowMs))
    {
        return false;
    }
    m->txFrame  = txFrame;
    m->rxFrame  = rxFrame;
    m->port     = port;
    m->frameLen = own.frameLen;
    return true;
}

void bl_link_master_poll(bl_link_master_t* m, uint32_t nowMs)
{
    /* Read the line before asking, so that a slave which has just raised it is served in this cycle. A board
     * without the line reports true and the poll period decides instead (section 9.4). */
    mcuLink_peerReady(&m->link, bl_link_ready_in(m->port));

    if (mcuLink_exchangeDue(&m->link, nowMs))
    {
        mcuLink_frameOut(&m->link, m->txFrame, nowMs);
        if (bl_link_transfer(m->port, m->txFrame, m->rxFrame, m->frameLen))
        {
            mcuLink_frameIn(&m->link, m->rxFrame, nowMs);
        }
        /* A port that does not answer needs no special case: the transaction simply did not happen, the
         * acknowledgement stays out, and the retry of the protocol handles it like any lost frame. */
    }

    mcuLink_poll(&m->link, nowMs);
}
