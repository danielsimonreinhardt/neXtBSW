/* Stub of the generated module parameters of safeSystem (integration/safeSystemConfiguration.h, IO-19, ARCH-32).
 *
 * Deliberately NOT the values of the demo project (limit 10, tolerance 200 mV): the test uses the symbols
 * everywhere, so a limit of 4 and a tolerance of 300 mV prove that the production code really takes them from the
 * configuration. A value hard coded in SafeSystem.h/.cpp again would make testConfiguredErrorLimit and
 * testConfiguredReferenceBand fail.
 */

#pragma once

#define NX_MODULE_SAFESYSTEM_PARAM_ADC_READ_ERROR_LIMIT 4U
#define NX_MODULE_SAFESYSTEM_PARAM_ADC_REFERENCE_TOLERANCE_MV 300U // mV

// Marks the stub profile: the checks of the literal values above run only against these stubs. The same test
// compiled against generated headers of another profile (tools/bswgen/test_bswgen.py) checks the configured band
// through the macros alone.
#define NX_SAFESYSTEM_HOST_STUB_PROFILE 1
