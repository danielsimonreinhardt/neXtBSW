/* Stub of the generated board fact bsp/adc/adcReferenceConfiguration.h (tools/bswgen, ARCH-32).
 *
 * Deliberately NOT the eval board (4096 mV, LM4132): 5000 mV like a board whose VREFH is the 5 V supply
 * (S32K148 build test, S32K116 EVB). With 5000 mV +- 300 mV a reference of 4096 mV is out of range, so a nominal
 * value hard coded in SafeSystem.cpp again would make testConfiguredReferenceBand fail.
 */

#pragma once

#define NX_ADC_REFERENCE_MV 5000U // mV
