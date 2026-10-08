// ARCH-38: host test of the SPI pin multiplexing of the board port (platforms/s32k1xx/port/main/src/lifecycle/
// SpiBusPins.h, called by StaticBsp::hwInit). Compiled by tools/bswgen/test_bswgen.py (BswGenSpiPinsHostTest) with
// the generat of a project, the real OpenBSW Io driver (bspIo/src/io/Io.cpp) and a RAM model of PORTA..PORTE.
//
// Starts from all PCRs at 0 (reset: pin disabled), runs exactly the function of the board port and prints every
// PCR it changed as "PCR PT<port><pin> 0x<value>". The Python side compares that with board.yaml and the pin
// multiplexing table of mcudb: every pin of every used SPI bus must carry the LPSPI function, nothing else may be
// touched. That is the gap of the board test 2026-09-18 - no host test saw that nobody muxed PTB0..PTB3.

#include "SpiBusPins.h"
#include "mcu/mcu.h"

#include <cstdio>

GPIO_Type fakeGpio[5] = {};
PORT_Type fakePort[5] = {};

int main()
{
    ::platform::muxSpiBusPins();

    char const portNames[] = "ABCDE";
    unsigned changed       = 0U;
    for (unsigned port = 0U; port < 5U; ++port)
    {
        for (unsigned pin = 0U; pin < PORT_PCR_COUNT; ++pin)
        {
            uint32_t const pcr = fakePort[port].PCR[pin];
            if (pcr != 0U)
            {
                std::printf("PCR PT%c%u 0x%08lX\n", portNames[port], pin, static_cast<unsigned long>(pcr));
                ++changed;
            }
        }
    }
    std::printf("LISTED %d CHANGED %u\n", NX_SPI_BUS_PIN_COUNT, changed);
    return 0;
}
