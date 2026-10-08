// SPDX-License-Identifier: Apache-2.0
/* PLAT-98: identification of the image from the build (identity/Identity.h). The macros come from
 * ecu/consoleCommands/CMakeLists.txt (project, tree and board of the build); the host test defines its own. */

#include "identity/Identity.h"

#if defined(__has_include)
#if __has_include(<identity/nxIdentityFields.h>)
// PLAT-79: generated entries of the identification (configuration, generators), see identity/Identity.h
#include <identity/nxIdentityFields.h>
#endif
#endif

#ifndef NX_IDENTITY_GENERATED_FIELDS
#define NX_IDENTITY_GENERATED_FIELDS
#endif

// PLAT-93: neXtBSW commit and commit of the customer workspace, from the header of the diagnosis (diag/DiagBuildId.h,
// tools/diaggen/buildid.py - the same text as the DIDs 0xF195 and 0xF1F2). An image without the diagnostic stack has
// no such header and shows neither entry. The commit state is not part of the configuration identity.
#if defined(__has_include)
#if __has_include(<diag/DiagBuildId.h>)
#include <diag/DiagBuildId.h>
#endif
#endif

#if defined(NX_DIAG_NEXTBSW_COMMIT) && defined(NX_DIAG_WORKSPACE_VERSION)
#define NX_IDENTITY_COMMIT_FIELDS \
    {"commit", NX_DIAG_NEXTBSW_COMMIT, false}, {"workspace", NX_DIAG_WORKSPACE_VERSION, false},
#else
#define NX_IDENTITY_COMMIT_FIELDS
#endif

#if !defined(NX_IDENTITY_APPLICATION) || !defined(NX_IDENTITY_NEXTBSW_VERSION) || !defined(NX_IDENTITY_BOARD)
#error "identity: NX_IDENTITY_APPLICATION, NX_IDENTITY_NEXTBSW_VERSION and NX_IDENTITY_BOARD come from the build"
#endif

static_assert(sizeof(NX_IDENTITY_APPLICATION) > 1U, "identity: empty application name (project.name)");

namespace identity
{
namespace
{
constexpr LogNameText LOG_NAME_TEXT = logNameOf(NX_IDENTITY_APPLICATION);
} // namespace

char const APPLICATION_NAME[] = NX_IDENTITY_APPLICATION;
char const* const LOG_NAME    = LOG_NAME_TEXT.text;
char const NEXTBSW_VERSION[]  = NX_IDENTITY_NEXTBSW_VERSION;
char const BOARD[]            = NX_IDENTITY_BOARD;

namespace
{
Field const BUILD_FIELDS[] = {
    {"application", APPLICATION_NAME, true},
    {"neXtBSW", NEXTBSW_VERSION, true},
    NX_IDENTITY_COMMIT_FIELDS
    {"board", BOARD, true},
    NX_IDENTITY_GENERATED_FIELDS};
} // namespace

::etl::span<Field const> buildFields() { return ::etl::span<Field const>(BUILD_FIELDS); }

} // namespace identity
