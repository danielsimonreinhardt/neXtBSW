#pragma once

/* NVM-03: the data identifiers of the diagnostic description for the fault memory. When a DTC is stored, the fault
 * memory (libs/bsw/dem) reads the identifiers of its snapshot records and extended data through dem::IDidReader;
 * this reader answers with exactly what ReadDataByIdentifier (22) would put behind the identifier - the same read
 * function of the generated table (COM-28), without session or security check (the fault memory is not a tester).
 *
 * The diagnostic stack registers it in dem::registry while it runs (DiagStack::init/shutdown). It is called in the
 * context of the fault memory: the read functions take values of components under the lock of the RTE, the values of
 * the stack (session, version texts) are single bytes or constant after the start. */

#include "uds/app/DiagDescription.h"

#include <dem/IFaultMemory.h>
#include <uds/session/IDiagSessionManager.h>

#include <cstdint>

namespace uds
{
namespace app
{
class DiagDidReader final : public ::dem::IDidReader
{
public:
    /** \p sessions: the active session at the moment of the read (uds.session), like service 22 takes it. */
    DiagDidReader(
        DiagDescriptionTables const& tables, DiagValues const& values, IDiagSessionManager const& sessions);

    /** Data record of \p did, exactly \p bytes long; false if the description has no readable identifier of that
     * length or its read function refuses now (e.g. NRC 0x22 without a bootloader text). */
    bool read(uint16_t did, uint8_t* data, uint16_t bytes) override;

private:
    DiagDescriptionTables const& _tables;
    DiagValues const& _values;
    IDiagSessionManager const& _sessions;
};

} // namespace app
} // namespace uds
