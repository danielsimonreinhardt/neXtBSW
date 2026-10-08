#include "uds/app/DiagDidReader.h"

#include <uds/connection/PositiveResponse.h>

namespace uds
{
namespace app
{
DiagDidReader::DiagDidReader(
    DiagDescriptionTables const& tables, DiagValues const& values, IDiagSessionManager const& sessions)
: _tables(tables), _values(values), _sessions(sessions)
{}

bool DiagDidReader::read(uint16_t const did, uint8_t* const data, uint16_t const bytes)
{
    for (DidEntry const& entry : _tables.dids)
    {
        if (entry.identifier != did)
        {
            continue;
        }
        if ((entry.read == nullptr) || (entry.readLength != bytes))
        {
            return false;
        }
        DiagValues values    = _values;
        values.activeSession = _sessions.getActiveSession().getSessionByte();
        PositiveResponse response;
        response.init(data, bytes);
        return (entry.read(values, response) == DiagReturnCode::OK) && (response.getLength() == bytes);
    }
    return false;
}

} // namespace app
} // namespace uds
