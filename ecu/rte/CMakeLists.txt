# RTE und Software-Komponenten. Die RTE-Quellen erzeugt tools/rtegen beim Konfigurieren (siehe
# Root-CMakeLists.txt) nach ${RTE_GENERATED_DIR}, der Komponentencode ist eingecheckt
# (swc/<Komponente>/generated/src, erzeugt mit tools/swc/swc_build.m). Sind in der ECU-Beschreibung des Projekts
# (project.rte, z. B. projects/evalboard_s32k144_demo/rte.yaml) CAN-Signale
# verbunden, kommt der Pack/Unpack-Code aus der DBC dazu (RTE_CAN_SOURCES, tools/dbcgen); sind Qualifier mit
# E2E-Profilen konfiguriert, zusätzlich die Bibliothek libs/bsw/e2e (RTE_USES_E2E, COM-03).

include("${RTE_GENERATED_DIR}/rte_components.cmake")

set(_SWC_SOURCES)
foreach (dir ${RTE_COMPONENT_SOURCE_DIRS})
    file(GLOB _sources CONFIGURE_DEPENDS "${dir}/*.c")
    list(APPEND _SWC_SOURCES ${_sources})
endforeach ()

add_library(swcComponents OBJECT ${_SWC_SOURCES} "${RTE_GENERATED_DIR}/Rte.c")
target_include_directories(swcComponents PRIVATE ${RTE_COMPONENT_SOURCE_DIRS} "${RTE_GENERATED_DIR}")
target_link_libraries(swcComponents PRIVATE autosarBase)
# Embedded-Coder-Code wird nicht angepasst; Warnungen darin sollen den Build nicht abbrechen.
set_target_properties(swcComponents PROPERTIES COMPILE_WARNING_AS_ERROR OFF)

add_library(rte "${RTE_GENERATED_DIR}/RteConfig.cpp" ${RTE_CAN_SOURCES} "$<TARGET_OBJECTS:swcComponents>")
target_include_directories(rte PUBLIC "${RTE_GENERATED_DIR}")
target_link_libraries(
    rte
    PUBLIC autosarBase asyncCoreConfiguration
    PRIVATE bspConfiguration bspInputManager bspOutputManager bspAdc async)
if (RTE_CAN_SOURCES)
    target_link_libraries(rte PRIVATE async cpp2can configuration)
endif ()
# BSW-Dienst-Ports (RTE-05): Übergabe für die Verbindungsziele bsw.power.*, bsw.reset.*, bsw.time.*. Das generierte
# RteConfig.cpp deklariert die Lesefunktionen selbst; Anbieter (Rolle power_supply) binden bswServices und
# veröffentlichen über bswService/BswServiceRte.h. Ohne verbundenes bsw.*-Ziel zieht der Linker nichts davon hinzu.
add_library(bswServices services/src/bswService/BswServiceRte.cpp)
target_include_directories(bswServices PUBLIC services/include)
target_link_libraries(bswServices PUBLIC ecuState PRIVATE bsp)
target_link_libraries(rte PRIVATE bswServices)
# RTE-16: Anbieter melden sich über globale Properties an (der Familien-Port wird vor ecu/rte eingelesen):
#   NX_BSW_POWER_PROVIDER    gesetzt, wenn ein Modul power::publish() ruft (s32k1xx: SbcSystem mit UJA113x); ohne
#                            Anbieter meldet bsw.power sofort UNKNOWN mit QLF_ERROR statt dauerhaft QLF_DEBOUNCE
#   NX_BSW_MCU_RESET_REASON  Bibliothek der Familie mit bswservice::reset::mcuStartReasons() (s32k1xx: RCM_SRS),
#                            zweite Quelle für bsw.reset neben den Startgründen der Rolle power_supply
get_property(_NX_BSW_POWER_PROVIDER GLOBAL PROPERTY NX_BSW_POWER_PROVIDER)
get_property(_NX_BSW_MCU_RESET_REASON GLOBAL PROPERTY NX_BSW_MCU_RESET_REASON)
if (_NX_BSW_POWER_PROVIDER)
    target_compile_definitions(bswServices PRIVATE NX_BSW_POWER_PROVIDER=1)
else ()
    target_compile_definitions(bswServices PRIVATE NX_BSW_POWER_PROVIDER=0)
endif ()
if (_NX_BSW_MCU_RESET_REASON)
    target_compile_definitions(bswServices PRIVATE NX_BSW_MCU_RESET_REASON=1)
    target_link_libraries(bswServices PRIVATE ${_NX_BSW_MCU_RESET_REASON})
else ()
    target_compile_definitions(bswServices PRIVATE NX_BSW_MCU_RESET_REASON=0)
endif ()
# Signal-Qualifier mit AUTOSAR-E2E-Profilen (COM-03): nur wenn die ECU-Beschreibung e2e konfiguriert
# (can.qualifiers.<Botschaft>.e2e), damit Projekte ohne Qualifier die Bibliothek nicht linken.
if (RTE_USES_E2E)
    target_link_libraries(rte PRIVATE e2e)
endif ()
