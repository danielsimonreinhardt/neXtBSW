/* SYS-29 (gap G5, finding F5): the supervisor's verdict on a device bus for the qualifier of the device values in
 * the RTE. tools/rtegen declares this reader in the generated RteConfig.cpp (device_bus_id there gives the same
 * bus id as the context word of safeSupervisor/DeviceReport.h) and sets the qualifier of every value of a device on
 * a lost bus to QLF_ERROR, on top of what the driver reports about itself - so an application sees a common-cause
 * fault of the bus even while a driver still believes its last reading.
 *
 * Own translation unit on purpose: SafeSupervisor.cpp stays free of references that the host tests of the family
 * port, which include it, would have to define. Read only - it runs in the application tasks, which have no write
 * access to the protected RAM. */

#include "safeSupervisor/SafeSupervisor.h"

#include <cstdint>

namespace safety
{
namespace device
{
namespace rte
{
bool busLost(uint8_t const bus) { return ::safety::SafeSupervisor::getInstance().deviceBusLost(bus); }
} // namespace rte
} // namespace device
} // namespace safety
