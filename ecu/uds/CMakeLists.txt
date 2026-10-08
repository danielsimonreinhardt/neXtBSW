# UDS in der Anwendung (COM-04): DoCAN auf dem Diagnose-Bus des Projekts (rte.yaml uds.bus, ARCH-07),
# eigener schlanker Router, UDS-Server aus OpenBSW.

# COM-28: Diagnosejobs aus der Diagnosebeschreibung des Projekts (project.diag, z. B.
# projects/evalboard_s32k144_demo/diag.yaml; ohne eigene Beschreibung tools/diaggen/default_diag.yaml). Laeuft beim
# Konfigurieren wie bswgen und rtegen, damit UdsConfig.h (P2/P2* der Sitzungen) vor dem ersten Compile der
# OpenBSW-Bibliothek uds vorliegt.
set(DIAG_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated/diag")
file(GLOB _DIAGGEN_INPUTS "${NX_ROOT}/tools/diaggen/*.py" "${NX_ROOT}/tools/diaggen/*.yaml")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${_DIAGGEN_INPUTS})
if (NX_DIAG_FILE)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${NX_DIAG_FILE}")
endif ()
# COM-43: im Produkt-Build (NX_PRODUCT_BUILD, SYS-74) verlangen 28 und 85 Security Access; diaggen prueft, dass die
# Beschreibung dafuer eine Stufe der Extended-Sitzung hat, und schreibt die Maske nach diag/DiagPreprogramming.h.
set(_DIAGGEN_BUILD_ARGS)
if (NX_PRODUCT_BUILD)
    list(APPEND _DIAGGEN_BUILD_ARGS --product)
endif ()
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/diaggen/diaggen.py" "${NX_PROJECT_FILE}"
            "${DIAG_GENERATED_DIR}" ${_DIAGGEN_BUILD_ARGS}
    RESULT_VARIABLE _DIAGGEN_RESULT)
if (NOT _DIAGGEN_RESULT EQUAL 0)
    message(FATAL_ERROR "diaggen failed for ${NX_PROJECT_FILE}")
endif ()
include("${DIAG_GENERATED_DIR}/diag_components.cmake")

# Configuration targets expected by the OpenBSW libraries uds and transportRouterSimple
add_library(transportConfiguration configuration/src/transport/TransportConfiguration.cpp)
target_include_directories(transportConfiguration PUBLIC configuration/include)
target_link_libraries(transportConfiguration PUBLIC transport etl configuration)

add_library(udsConfiguration INTERFACE)
# DIAG_GENERATED_DIR: diag/DiagConfig.h (session timing for UdsConfig.h, COM-28)
target_include_directories(udsConfiguration INTERFACE configuration/include "${DIAG_GENERATED_DIR}")

add_library(udsConfigurationImpl configuration/src/uds/session/DiagSession.cpp)
target_link_libraries(udsConfigurationImpl PRIVATE uds)

add_library(
    appUds
    src/systems/DiagSystem.cpp
    src/transport/routing/DiagTransportRouter.cpp
    src/uds/app/BootloaderIdentification.cpp
    # COM-44: identification of the application (F191 hardware number, F1A0 compatibility record)
    src/uds/app/ApplicationIdentification.cpp
    src/uds/app/DiagDescriptionServices.cpp
    src/uds/app/AppProgrammingPreconditions.cpp
    src/uds/app/DiagStack.cpp
    src/uds/app/DeviceKeySecurity.cpp
    # NVM-03: identifiers of the description for the snapshots of the fault memory (libs/bsw/dem)
    src/uds/app/DiagDidReader.cpp
    # NVM-04: ReadDTCInformation (19) and ClearDiagnosticInformation (14) over the fault memory
    src/uds/app/FaultMemoryServices.cpp
    "${DIAG_GENERATED_DIR}/DiagDescription.cpp"
    # COM-30: SHA-256/HMAC and CRC-32 of the bootloader core for the security levels (method hmac-sha256): the
    # same implementation and the same key record as the bootloader's security access (BL-03), no second one
    "${NX_ROOT}/bootloader/src/core/sha256.c"
    "${NX_ROOT}/bootloader/src/core/crc32.c")

# RTE_GENERATED_DIR: uds/udsBusConfiguration.h (diagnostic bus, tools/rtegen)
target_include_directories(appUds PUBLIC include "${NX_ROOT}/bootloader/include"
                                         "${RTE_GENERATED_DIR}" "${DIAG_GENERATED_DIR}")
target_include_directories(appUds PRIVATE "${NX_ROOT}/bootloader/src/core")

target_compile_definitions(appUds PRIVATE APP_SOFTWARE_VERSION="${PROJECT_VERSION}")

# COM-44: neXtBSW-Version und Commit des Builds fuer die Diagnose (nextbsw.version, 0xF195): laeuft bei jedem Build
# wie das EPK (ecu/xcp), schreibt diag/DiagBuildId.h aber nur, wenn sich der Text aendert.
add_custom_target(
    diagBuildId
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/diaggen/buildid.py" -o "${DIAG_GENERATED_DIR}/diag/DiagBuildId.h"
            --tree "${NX_ROOT}" --workspace "${NX_WORKSPACE}"
    BYPRODUCTS "${DIAG_GENERATED_DIR}/diag/DiagBuildId.h"
    COMMENT "neXtBSW version and commit for the diagnosis (tools/diaggen/buildid.py)"
    VERBATIM)
add_dependencies(appUds diagBuildId)

# PLAT-93: the console ("info") shows the neXtBSW commit and the workspace commit from the same header, so console
# and diagnosis cannot disagree (identity/Identity.cpp includes it where it exists).
if (TARGET consoleCommands)
    add_dependencies(consoleCommands diagBuildId)
    target_include_directories(consoleCommands PRIVATE "${DIAG_GENERATED_DIR}")
endif ()

# COM-29: CommunicationControl (UDS 0x28) schaltet die zyklischen Anwendungs-Botschaften ab. Der Zustand
# gehoert der Kommunikationsschicht: die RTE haelt ihn, die Diagnose schreibt ihn (Richtung wie Dcm -> Com in
# AUTOSAR). Den Setter rte::setApplicationTxEnabled() gibt es nur, wenn die ECU-Beschreibung ueberhaupt
# Botschaften sendet - das sagt der Generator selbst (RTE_APPLICATION_TX_GATE, rte_components.cmake). Sonst
# wird der Aufruf gar nicht erst uebersetzt; ein Stub oder ein schwaches Symbol ist dafuer nicht noetig.
include("${RTE_GENERATED_DIR}/rte_components.cmake")
if (RTE_APPLICATION_TX_GATE AND TARGET rte)
    target_compile_definitions(appUds PRIVATE NX_RTE_APPLICATION_TX_GATE=1)
    target_link_libraries(appUds PRIVATE rte)
endif ()
# COM-28: Kennungen mit Port-Quelle und Routinen lesen und schreiben ueber die RTE (rte::diag, RteDiag.h von
# tools/rtegen) - gleiche Richtung wie bei COM-29: die Diagnose ruft die RTE, nie umgekehrt.
if (DIAG_USES_RTE)
    if (NOT TARGET rte)
        message(FATAL_ERROR "the diagnostic description reads component values, but the project has no RTE")
    endif ()
    target_link_libraries(appUds PRIVATE rte)
endif ()

target_link_libraries(
    appUds
    PUBLIC uds
           dem
           udsConfigurationImpl
           docan
           transport
           transportConfiguration
           transportRouterSimple
           cpp2can
           lifecycle
           configuration
           etl
           time
    PRIVATE bspMcu util canSystem)
