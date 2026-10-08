cmake_minimum_required(VERSION 3.28 FATAL_ERROR)

# Aufbau angelehnt an external/openbsw/CMakeLists.txt, aber mit eigenem Executable (ecu/)
# und eigenem Board-Port. Das Submodul selbst bleibt unverändert.

# >>> Baum und Workspace (ARCH-57, customer-project.md N4) - Anfang
# Zwei Wurzeln statt einer (doc/architecture/customer-project.md Abschnitt 5.2):
#   NX_ROOT       der neXtBSW-Baum: Generatoren, platforms, libs, ecu, bootloader, OpenBSW-Submodul.
#   NX_WORKSPACE  die Wurzel des Workspace: Projekte, eigene Boards, Board-Ports, .venv, build.
# Im Entwicklungsfall (dieses Repository baut seine eigenen Projekte) sind beide gleich. In einem Kunden-Repository
# ist NX_WORKSPACE dessen Wurzel und NX_ROOT der gepinnte Baum darunter (nextbsw.yaml: nextbsw.path); dort setzt die
# dünne Wurzeldatei des Workspace NX_ROOT und bindet **diese** Datei ein (Vorlage cmake/workspace/CMakeLists.txt.in).
# Dieselbe Datei läuft also in beiden Fällen - es gibt keinen zweiten Aufbau, der auseinanderlaufen könnte.
#
# Achtung bei Pfaden: CMAKE_CURRENT_SOURCE_DIR ist hier die Wurzel des **Workspace** (CMake setzt sie aus -S), nicht
# der Baum. Alles, was im Baum liegt, wird über NX_ROOT angesprochen - auch in den Unterverzeichnissen
# (ecu/, bootloader/, platforms/), wo bisher CMAKE_SOURCE_DIR stand. CMAKE_SOURCE_DIR lässt sich dafür nicht
# umbiegen: CMake setzt die Variable in jedem Verzeichnis neu auf die oberste Quellwurzel.
if (NOT DEFINED NX_ROOT)
    set(NX_ROOT "${CMAKE_CURRENT_LIST_DIR}")
endif ()
get_filename_component(NX_ROOT "${NX_ROOT}" ABSOLUTE)
set(NX_WORKSPACE "${CMAKE_CURRENT_SOURCE_DIR}")
if (NOT NX_WORKSPACE STREQUAL NX_ROOT)
    message(STATUS "workspace ${NX_WORKSPACE}, neXtBSW tree ${NX_ROOT}")
endif ()
# <<< Baum und Workspace (ARCH-57, customer-project.md N4) - Ende

set(CMAKE_CXX_SCAN_FOR_MODULES FALSE)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_C_STANDARD 99)
# ISO mode (-std=c99/-std=c++17) instead of GNU mode (gnu99/gnu++17), NXCPP-02 (DOC-29, dev-environment.md DE2): a GNU
# language extension (typeof, statement expressions, asm without the __ spelling) is an error instead of silently
# accepted. Attributes and __asm__ stay valid; OpenBSW code is built in the same mode as its own CMake does.
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_CXX_EXTENSIONS OFF)
# ISO mode also switches GCC's default -ffp-contract from "fast" to "off": without this line a*b+c would no longer be
# fused into vfma (DemoSwc.c changed in the object-code comparison). The old behaviour is kept explicitly here, so the
# language mode is the only change of DOC-29; whether to fuse or not is a separate decision (open point of DOC-29).
add_compile_options("$<$<AND:$<COMPILE_LANGUAGE:C,CXX>,$<C_COMPILER_ID:GNU>>:-ffp-contract=fast>")

set(OPENBSW_DIR "${NX_ROOT}/external/openbsw")
set(OPENBSW_APP_DIR "${NX_ROOT}/ecu")

# OpenBSW unterscheidet nur zwischen "unitTest" und Target-Build ("referenceApp").
set(BUILD_EXECUTABLE "referenceApp")

set(BUILD_TARGET_RTOS
    "FREERTOS"
    CACHE STRING "Target RTOS")

# Projektauswahl (doc/architecture/README.md Abschnitte 4 und 8): NX_PROJECT ist die einzige Auswahl. Board, Derivat,
# Familie und Kern liest tools/nxconfig aus Projekt -> Board -> Derivat und schreibt sie als NX_* in eine
# Include-Datei (Befehl "nxconfig cmake"). Läuft vor project(), weil Toolchain und Plattform davon abhängen.
# >>> Interpreter des Workspace (ARCH-57) - Anfang
# Die Basis-Presets zeigen mit ${sourceDir}/.venv auf das **Python des Workspace** (customer-project.md 5.3): ein
# Kunden-Repository bekommt seine eigene Umgebung aus den requirements des gepinnten Baums (N8). Solange es sie
# nicht gibt, fällt der Bau auf das .venv des Baums zurück und sagt es - das sind dieselben Pakete.
# Gibt es beides nicht, bricht der Bau hier ab, statt sich still irgendein Python vom PATH zu nehmen: die
# Generatoren brauchen pydantic und jinja2, und ein fremder Interpreter fällt sonst erst im Generator auf.
set(_NX_VENV_PYTHON "bin/python3")
if (WIN32)
    set(_NX_VENV_PYTHON "Scripts/python.exe")
endif ()
if (Python3_EXECUTABLE AND NOT EXISTS "${Python3_EXECUTABLE}")
    set(_NX_PYTHON_MISSING "${Python3_EXECUTABLE}")
    unset(Python3_EXECUTABLE CACHE)
    unset(Python3_EXECUTABLE)
    if (EXISTS "${NX_ROOT}/.venv/${_NX_VENV_PYTHON}")
        set(Python3_EXECUTABLE
            "${NX_ROOT}/.venv/${_NX_VENV_PYTHON}"
            CACHE FILEPATH "Python interpreter of the build" FORCE)
        message(STATUS "python: ${_NX_PYTHON_MISSING} does not exist, using the .venv of the neXtBSW tree "
                       "(${Python3_EXECUTABLE}); the workspace gets its own environment with N8")
    else ()
        message(FATAL_ERROR "python: neither ${_NX_PYTHON_MISSING} nor ${NX_ROOT}/.venv/${_NX_VENV_PYTHON} exists. "
                            "Create the environment of the workspace from the requirements of the neXtBSW tree "
                            "(tools/requirements.txt, gap N8) or configure with "
                            "-DPython3_EXECUTABLE=<interpreter with pydantic and jinja2>")
    endif ()
endif ()
# <<< Interpreter des Workspace (ARCH-57) - Ende
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(NX_PROJECT
    ""
    CACHE STRING "neXtBSW project: projects/<project> or its project.yaml (relative to the workspace or absolute)")
if (NX_PROJECT STREQUAL "")
    message(FATAL_ERROR "NX_PROJECT is not set: cmake --preset <project> or -DNX_PROJECT=projects/<project> "
                        "(projects/README.md)")
endif ()
if (DEFINED BUILD_TARGET_PLATFORM)
    message(WARNING "BUILD_TARGET_PLATFORM (${BUILD_TARGET_PLATFORM}) is no longer used: the board follows from "
                    "NX_PROJECT (ARCH-04)")
endif ()
get_filename_component(_NX_PROJECT_PATH "${NX_PROJECT}" ABSOLUTE BASE_DIR "${NX_WORKSPACE}")
set(NX_SETTINGS_FILE "${CMAKE_BINARY_DIR}/generated/nxconfig/project.cmake")
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/nxconfig" cmake "${_NX_PROJECT_PATH}"
            "${NX_SETTINGS_FILE}"
    RESULT_VARIABLE _NXCONFIG_RESULT
    OUTPUT_VARIABLE _NXCONFIG_OUTPUT)
if (NOT _NXCONFIG_RESULT EQUAL 0)
    message(FATAL_ERROR "nxconfig: configuration errors in ${_NX_PROJECT_PATH} (messages above)")
endif ()
string(STRIP "${_NXCONFIG_OUTPUT}" _NXCONFIG_OUTPUT)
message(STATUS "nxconfig: ${_NXCONFIG_OUTPUT}")
include("${NX_SETTINGS_FILE}")
file(GLOB _NXCONFIG_INPUTS "${NX_ROOT}/tools/nxconfig/*.py")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${NX_CONFIG_FILES} ${_NXCONFIG_INPUTS})

# Toolchain: generisches arm-none-eabi-gcc aus dem PATH (Arbeitsplatz: Umgebungsvariable NX_ARM_GCC_BIN, siehe
# projects/README.md). Die Kern-Optionen (NX_MCPU, NX_MFLOAT_ABI, NX_MFPU) bildet nxconfig aus derivative.core und
# derivative.fpu (PLAT-09), die eigene Toolchain-Datei gilt für alle Kerne.
if (NOT CMAKE_TOOLCHAIN_FILE)
    set(CMAKE_TOOLCHAIN_FILE "${NX_ROOT}/cmake/toolchains/NxArmNoneEabi-gcc.cmake")
endif ()
# Bau-Verzeichnis-Prüfung (PLAT-47) vor und nach project(). Stand bis ARCH-57 als ${sourceDir}/cmake/... in den
# Presets; ${sourceDir} ist in einem Workspace dessen Wurzel, und ${fileDir} einer eingebundenen Preset-Datei
# löst CMake ebenfalls gegen die oberste Preset-Datei auf. Die Datei liegt deshalb hier am Baum fest - der
# einzige Ort, der beide Fälle kennt.
set(CMAKE_PROJECT_neXtBSW_INCLUDE_BEFORE "${NX_ROOT}/cmake/NxBuildDirCheck.cmake")
set(CMAKE_PROJECT_neXtBSW_INCLUDE "${NX_ROOT}/cmake/NxBuildDirCheck.cmake")
if (DEFINED CACHE{NX_CONFIGURED_CORE} AND NOT NX_CONFIGURED_CORE STREQUAL NX_CORE)
    message(FATAL_ERROR "this build directory was configured for core ${NX_CONFIGURED_CORE}, "
                        "project ${NX_PROJECT_NAME} needs ${NX_CORE}: use a new build directory")
endif ()
if (DEFINED CACHE{NX_CONFIGURED_FPU} AND NOT NX_CONFIGURED_FPU STREQUAL NX_FPU)
    message(FATAL_ERROR "this build directory was configured for FPU ${NX_CONFIGURED_FPU}, "
                        "project ${NX_PROJECT_NAME} needs ${NX_FPU}: use a new build directory")
endif ()
set(NX_CONFIGURED_CORE
    "${NX_CORE}"
    CACHE INTERNAL "core the compiler flags were set up for")
set(NX_CONFIGURED_FPU
    "${NX_FPU}"
    CACHE INTERNAL "FPU variant the compiler flags were set up for")
if (NOT DEFINED CACHE{CMAKE_C_COMPILER})
    if (DEFINED ENV{CC})
        set(_NX_CC "$ENV{CC}")
    else ()
        set(_NX_CC arm-none-eabi-gcc)
    endif ()
    find_program(_NX_CC_PATH NAMES "${_NX_CC}" NO_CACHE)
    if (NOT _NX_CC_PATH)
        message(FATAL_ERROR "C compiler ${_NX_CC} not found: put the bin directory of arm-none-eabi-gcc on PATH or set "
                            "the environment variable NX_ARM_GCC_BIN before configuring with a preset; the pinned "
                            "xPack GCC 11.3.1 comes with `python -m tools.ci setup` (projects/README.md)")
    endif ()
endif ()

# Port des Boards: seit PLAT-23 (Befund ARCH-09 B6) ist der handgeschriebene Teil eine **Familienvorlage** unter
# platforms/<familie>/port (bspConfiguration, safety, main, freeRtosCoreConfiguration; Architektur Abschnitte 5
# und 8). Der Board-Port ecu/platforms/<board> ist seit ARCH-58 **optional**: er trägt nur noch wirkliche
# Board-Eigenheiten und begründete Ausnahmen. Ein Board-Port zeigt nie auf einen anderen. Die OpenBSW-Plattform ist
# die Familie des Derivats.
set(OPENBSW_TARGET "${NX_BOARD}")
# ARCH-57: gesucht wird zuerst im Workspace, dann im Baum - ein Kunden-Board bringt seine Eigenheiten im eigenen
# Repository mit, ohne den gepinnten Baum anzufassen. Dass ein Workspace-Board keinen Namen eines Baum-Boards
# benutzt, prüft nxconfig (ARCH-56, beide Pfade in der Meldung).
# ARCH-58 (customer-project.md N5): **kein Board-Port ist kein Fehler mehr.** Bis dahin verlangte diese Datei von
# jedem baubaren Board ein handgeschriebenes ecu/platforms/<board>/Options.cmake mit OPENBSW_PLATFORM und den zehn
# PLATFORM_SUPPORT_*-Schaltern. Die Schalter kommen jetzt aus Projekt, Board und Port (tools/nxconfig/features.py,
# unten); ein Kunden-Board braucht die Datei nur noch für eine begründete Ausnahme.
set(ECU_PLATFORM_DIR "")
foreach (_NX_PORT_BASE "${NX_WORKSPACE}" "${NX_ROOT}")
    if (IS_DIRECTORY "${_NX_PORT_BASE}/ecu/platforms/${OPENBSW_TARGET}")
        set(ECU_PLATFORM_DIR "${_NX_PORT_BASE}/ecu/platforms/${OPENBSW_TARGET}")
        break()
    endif ()
endforeach ()
# >>> Plattform der Familie (ARCH-25) - Anfang
# Eine eigene neXtBSW-Plattform-CMake platforms/<familie>/CMakeLists.txt genügt (PLAT-14/ARCH-25): sie bindet die
# OpenBSW-Module der Familie selbst ein (s32k1xx, stm32) oder bringt alles mit (Familien ohne OpenBSW-Grundlage, z. B.
# rm4x). Die OpenBSW-Plattform external/openbsw/platforms/<familie> wird nur verlangt, wenn es keine eigene gibt.
# Dieser Block ist ohne Compiler lauffähig: platforms/common/test/test_platform_selection.py führt ihn im
# Skriptmodus aus - dort gibt es kein NX_ROOT, deshalb fällt er auf CMAKE_CURRENT_SOURCE_DIR zurück (ARCH-57).
if (NOT NX_ROOT)
    set(NX_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
endif ()
set(NX_FAMILY_PLATFORM_DIR "${NX_ROOT}/platforms/${NX_FAMILY}")
if (EXISTS "${NX_FAMILY_PLATFORM_DIR}/CMakeLists.txt")
    set(NX_FAMILY_OWN_PLATFORM ON)
elseif (EXISTS "${OPENBSW_DIR}/platforms/${NX_FAMILY}/CMakeLists.txt")
    set(NX_FAMILY_OWN_PLATFORM OFF)
else ()
    message(FATAL_ERROR "family ${NX_FAMILY} of ${NX_DERIVATIVE} has no platform: neither platforms/${NX_FAMILY}/"
                        "CMakeLists.txt (neXtBSW platform of the family) nor external/openbsw/platforms/${NX_FAMILY}")
endif ()
# <<< Plattform der Familie (ARCH-25) - Ende
set(NX_FAMILY_PORT_DIR "${NX_ROOT}/platforms/${NX_FAMILY}/port")
if (NOT EXISTS "${NX_FAMILY_PORT_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "family ${NX_FAMILY} of ${NX_DERIVATIVE} has no port template ${NX_FAMILY_PORT_DIR} "
                        "(PLAT-23: platforms/<family>/port carries bspConfiguration, safety, main and "
                        "freeRtosCoreConfiguration for every board of the family)")
endif ()
set(OPENBSW_PLATFORM "${NX_FAMILY}")

# >>> Funktionsschalter aus Projekt und Port (ARCH-58, customer-project.md N5) - Anfang
# Bis ARCH-58 setzte jedes Board die Schalter von Hand in ecu/platforms/<board>/Options.cmake - eine Datei an einer
# Stelle, die einem Kunden-Board nicht gehört, und ihr Inhalt wiederholte, was project.yaml schon sagt (Architektur
# Abschnitt 9, Zeile 2). tools/nxconfig/features.py leitet sie jetzt ab und schreibt sie als NX_FEATURE_<NAME> in
# das Generat project.cmake, zusammen mit NX_FEATURE_<NAME>_WHY - der Begründung, die hier als Beschreibung im
# Cache landet und in `cmake -LAH` sichtbar ist. Woher welcher Schalter kommt, steht im Kopf von features.py:
#   IO, CAN, TRANSPORT, UDS  aus dem Projekt (use.dio/adc/input_capture/pwm, use.can, diagnostics)
#   ETHERNET                 aus der Bau-Option NX_ETHERNET (Block unten), nicht aus dem Board
#   WATCHDOG, MPU            aus der Aussage der Familie, ecu/platforms/<familie>/features.yaml
# Nicht abgeleitet und deshalb hier nicht gesetzt: STORAGE, ROM_CHECK, MIDDLEWARE und die übrigen Schalter der
# OpenBSW-Referenzanwendung - sie haben in neXtBSW keinen Leser (nur external/openbsw/executables/referenceApp,
# das dieser Baum nicht baut) und standen in jedem Board-Port auf OFF.
foreach (_NX_FEATURE IO CAN ETHERNET TRANSPORT UDS WATCHDOG MPU)
    if (NOT DEFINED NX_FEATURE_${_NX_FEATURE})
        message(FATAL_ERROR "nxconfig did not derive NX_FEATURE_${_NX_FEATURE} for project ${NX_PROJECT_NAME}: "
                            "the generated ${NX_SETTINGS_FILE} is older than this CMakeLists.txt (ARCH-58). "
                            "Configure again in a fresh build directory")
    endif ()
    set(PLATFORM_SUPPORT_${_NX_FEATURE}
        ${NX_FEATURE_${_NX_FEATURE}}
        CACHE BOOL "${NX_FEATURE_${_NX_FEATURE}_WHY}" FORCE)
endforeach ()
# Der Board-Port darf danach einen Schalter überschreiben. Das ist die **begründete Ausnahme** eines Boards
# (ecu/platforms/README.md) und keine Pflicht mehr: ein Board ohne diese Datei baut.
if (ECU_PLATFORM_DIR AND EXISTS "${ECU_PLATFORM_DIR}/Options.cmake")
    include("${ECU_PLATFORM_DIR}/Options.cmake")
endif ()
if (NOT OPENBSW_PLATFORM STREQUAL NX_FAMILY)
    message(FATAL_ERROR "board port ${ECU_PLATFORM_DIR} sets OPENBSW_PLATFORM ${OPENBSW_PLATFORM}, "
                        "but ${NX_DERIVATIVE} belongs to family ${NX_FAMILY}")
endif ()
# <<< Funktionsschalter aus Projekt und Port (ARCH-58, customer-project.md N5) - Ende

# >>> Optionen offen halten (PLAT-56, ARCH-30 F7) - Anfang
# Entscheidung des Nutzers 2026-09-17: Ethernet/lwIP bleibt einbindbar, der Standard (kein Ethernet) bleibt. Die
# Ableitung oben setzt PLATFORM_SUPPORT_ETHERNET mit FORCE auf OFF (ARCH-58: Ethernet ist keine Board-Aussage,
# sondern diese Bau-Option); NX_ETHERNET (nur Bau-Test-Presets, z. B.
# buildtest-s32k148-ethernet) schaltet es danach ein. Ohne NX_ETHERNET ändert sich nichts. Voraussetzung ist ein
# Derivat mit Ethernet-MAC (mcudb: Peripherie mit class: ethernet).
option(NX_ETHERNET "Ethernet/lwIP stack in the application (build test of the option, PLAT-56)" OFF)
if (NX_ETHERNET)
    file(READ "${NX_DERIVATIVE_FILE}" _nx_derivative_text)
    if (NOT _nx_derivative_text MATCHES "class: *ethernet")
        message(FATAL_ERROR "NX_ETHERNET: derivative ${NX_DERIVATIVE} has no Ethernet MAC (no peripheral with "
                            "class: ethernet in ${NX_DERIVATIVE_FILE})")
    endif ()
    set(PLATFORM_SUPPORT_ETHERNET
        ON
        CACHE BOOL "Ethernet/lwIP stack (NX_ETHERNET, PLAT-56)" FORCE)
endif ()
# SYS-74: Produkt-Abbild ohne Test- und Fehlereinspeisungsbefehle (NX_PRODUCT_BUILD, Standard OFF); vor allen
# Unterverzeichnissen, damit die Definition jedes Ziel erreicht. Die Option steht hier, damit tools/docgen sie kennt.
option(NX_PRODUCT_BUILD "product image without test and fault-injection console commands (SYS-74)" OFF)
include("${NX_ROOT}/cmake/NxProductBuild.cmake")
# Betriebssystem wie in external/openbsw/CMakeLists.txt: Standard FREERTOS, THREADX bleibt als Weg offen (die
# Familienvorlage platforms/<familie>/port kennt heute nur FreeRTOS, siehe doc/changelog.d/PLAT-56.md).
if (BUILD_TARGET_RTOS STREQUAL "FREERTOS")
    add_compile_definitions(SUPPORT_FREERTOS)
elseif (BUILD_TARGET_RTOS STREQUAL "THREADX")
    add_compile_definitions(SUPPORT_THREADX)
else ()
    message(FATAL_ERROR "BUILD_TARGET_RTOS ${BUILD_TARGET_RTOS}: FREERTOS or THREADX")
endif ()
# <<< Optionen offen halten (PLAT-56, ARCH-30 F7) - Ende

get_cmake_property(_all_vars VARIABLES)
foreach (var ${_all_vars})
    if (var MATCHES "^PLATFORM_SUPPORT_" OR var MATCHES "^PLATFORM_CAN")
        if (${var})
            add_compile_definitions(${var}=1)
            message(STATUS "${var} ON")
        endif ()
    endif ()
endforeach ()

# neXtBSW-Version (ARCH-55): einzige Quelle ist die Datei VERSION im Wurzelverzeichnis des Baums, eine Zeile
# MAJOR.MINOR.PATCH (SemVer). nxconfig, Studio und tools/ci lesen dieselbe Datei (tools/nxconfig/version.py).
file(STRINGS "${NX_ROOT}/VERSION" NX_VERSION LIMIT_COUNT 1)
string(STRIP "${NX_VERSION}" NX_VERSION)
if (NOT NX_VERSION MATCHES "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$")
    message(FATAL_ERROR "VERSION must hold one line MAJOR.MINOR.PATCH (is '${NX_VERSION}')")
endif ()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${NX_ROOT}/VERSION")

project(
    "neXtBSW"
    VERSION ${NX_VERSION}
    LANGUAGES C CXX ASM)

set(OPENBSW_PLATFORM_DIR "${OPENBSW_DIR}/platforms/${OPENBSW_PLATFORM}")

# Board-Konfiguration erzeugen. Läuft beim Konfigurieren, damit alle OpenBSW-Bibliotheken, die die Header
# einbinden, sie vor dem ersten Compile vorfinden.
set(BOARD_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated/board")
file(GLOB _BSWGEN_INPUTS "${NX_ROOT}/tools/bswgen/*.py"
     "${NX_ROOT}/tools/bswgen/templates/*")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${_BSWGEN_INPUTS})
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/bswgen/bswgen.py" "${NX_PROJECT_FILE}"
            "${BOARD_GENERATED_DIR}"
    RESULT_VARIABLE _BSWGEN_RESULT)
if (NOT _BSWGEN_RESULT EQUAL 0)
    message(FATAL_ERROR "bswgen failed for ${NX_PROJECT_FILE}")
endif ()
# Module-Integration (ARCH-05): genutzte Module und ihre Ziele (NX_MODULES, NX_MODULE_TARGETS), aus den Manifesten
# libs/bsw/<modul>/module.yaml und den Bausteinen des Boards
include("${BOARD_GENERATED_DIR}/integration/modules.cmake")
file(GLOB _NX_MODULE_MANIFESTS "${NX_ROOT}/libs/bsw/*/module.yaml")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${_NX_MODULE_MANIFESTS})

# RTE aus der ECU-Beschreibung des Projekts (project.rte, z. B. projects/<projekt>/rte.yaml) und dem ARXML der
# Software-Komponenten erzeugen (gleiches Prinzip wie bswgen). Komponenten liegen wiederverwendbar unter swc/ oder
# projektspezifisch unter projects/<projekt>/swc/.
set(RTE_YAML "${NX_RTE_FILE}")
set(RTE_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated/rte")
set(_SWC_INPUT_GLOBS)
foreach (root "${NX_ROOT}/swc" "${NX_PROJECT_DIR}/swc")
    list(APPEND _SWC_INPUT_GLOBS "${root}/*/generated/arxml/*.arxml" "${root}/*/generated/model.sha256"
         "${root}/*/*.slx")
endforeach ()
file(GLOB _RTEGEN_INPUTS "${NX_ROOT}/tools/rtegen/*.py"
     "${NX_ROOT}/tools/rtegen/templates/*" ${_SWC_INPUT_GLOBS})
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${RTE_YAML}" ${_RTEGEN_INPUTS})
# RTE-02: BSW tasks for the generated task list (async/Config.h, RteTasks.h)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
                                       "${NX_ROOT}/ecu/asyncCoreConfiguration/tasks.yaml")
# CAN-Signale: DBC-Dateien des Projekts und Pack/Unpack-Generator (tools/dbcgen), von rtegen eingebunden
file(GLOB _DBCGEN_INPUTS "${NX_ROOT}/tools/dbcgen/*.py" "${NX_ROOT}/tools/dbcgen/templates/*"
     "${NX_PROJECT_DIR}/*.dbc")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${_DBCGEN_INPUTS})
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${NX_ROOT}/tools/rtegen/rtegen.py" "${NX_PROJECT_FILE}"
            "${RTE_GENERATED_DIR}" --repo "${NX_ROOT}"
    RESULT_VARIABLE _RTEGEN_RESULT)
if (NOT _RTEGEN_RESULT EQUAL 0)
    message(FATAL_ERROR "rtegen failed for ${NX_PROJECT_FILE}")
endif ()

# >>> Flash-Ziele je Debug-Probe (PLAT-18) - Anfang
# Flash-Ziele (ecu/application: flash, bootloader: flash-bootloader) je Debug-Probe. board.debug.probe wählt die
# Implementierungen (tools/nxconfig: NX_DEBUG_PROBE_KIND, NX_FLASH_TOOLS in Vorzugsreihenfolge), Gerätenamen kommen aus
# dem Derivat, Schnittstelle und Takt aus board.debug. Welches Werkzeug installiert ist und wo, ist Arbeitsplatz-Ebene
# (nicht eingecheckt): Cache-Variable des Werkzeugs, dessen bin-Ordner in der gleichnamigen Umgebungsvariablen
# (NX_JLINK_BIN, ...) oder PATH; NX_FLASH_TOOL erzwingt eine Implementierung. Fehlt das Werkzeug oder eine Angabe,
# entsteht das Ziel trotzdem und bricht beim Aufruf mit der Begründung ab (keine stille Auslassung).
# Dieser Block ist ohne Compiler lauffähig: tools/nxconfig/test_flash_targets.py bindet ihn mit Test-Profilen ein.
set(NX_FLASH_TOOL
    ""
    CACHE STRING "Flash tool of the debug probe (one of NX_FLASH_TOOLS); empty: the first one found")
set(NX_DEBUG_PROBE_SERIAL
    ""
    CACHE STRING "Serial number of the debug probe when several are connected (workstation)")
set(NX_DSLITE_CCXML
    ""
    CACHE FILEPATH "UniFlash target configuration (.ccxml: XDS110 and device) for dslite")

# Finds `tool` (one of NX_FLASH_TOOLS); `exe` gets its path, `missing` what else is missing (empty: usable).
function (_nx_find_flash_tool tool exe missing)
    set(why "")
    if (tool STREQUAL "jlink")
        find_program(JLINK_EXE NAMES JLink JLink.exe HINTS ENV NX_JLINK_BIN PATHS "C:/Program Files/SEGGER/JLink")
        set(var JLINK_EXE)
        set(env NX_JLINK_BIN)
    elseif (tool STREQUAL "stm32cubeprogrammer")
        find_program(NX_STM32_PROGRAMMER_CLI NAMES STM32_Programmer_CLI HINTS ENV NX_STM32_PROGRAMMER_BIN)
        set(var NX_STM32_PROGRAMMER_CLI)
        set(env NX_STM32_PROGRAMMER_BIN)
    elseif (tool STREQUAL "openocd")
        find_program(NX_OPENOCD_EXE NAMES openocd HINTS ENV NX_OPENOCD_BIN)
        set(var NX_OPENOCD_EXE)
        set(env NX_OPENOCD_BIN)
        if (NOT NX_OPENOCD_TARGET)
            set(why "derivative ${NX_DERIVATIVE} names no OpenOCD target script (derivative.openocd_target in mcudb)")
        endif ()
    elseif (tool STREQUAL "pyocd")
        find_program(NX_PYOCD_EXE NAMES pyocd HINTS ENV NX_PYOCD_BIN)
        set(var NX_PYOCD_EXE)
        set(env NX_PYOCD_BIN)
    elseif (tool STREQUAL "dslite")
        find_program(NX_DSLITE_EXE NAMES dslite dslite.bat HINTS ENV NX_UNIFLASH_DIR)
        set(var NX_DSLITE_EXE)
        set(env NX_UNIFLASH_DIR)
        if (NOT NX_DSLITE_CCXML OR NOT EXISTS "${NX_DSLITE_CCXML}")
            set(why "NX_DSLITE_CCXML names no UniFlash target configuration (.ccxml, workstation)")
        endif ()
    else ()
        message(FATAL_ERROR "flash targets: unknown flash tool ${tool} (tools/nxconfig config.PROBES)")
    endif ()
    if (NOT ${var})
        set(why "not found (cache variable ${var}, environment variable ${env} with its directory, or PATH)")
    endif ()
    set(${exe}
        "${${var}}"
        PARENT_SCOPE)
    set(${missing}
        "${why}"
        PARENT_SCOPE)
endfunction ()

set(_NX_FLASH_TOOL "")
set(_NX_FLASH_EXE "")
set(_NX_FLASH_PROBLEM "")
if (NOT NX_FLASH_TOOLS)
    set(_NX_FLASH_PROBLEM "board ${NX_BOARD} describes no debug probe the flash targets know (board.debug, "
                          "nxconfig warnings when configuring)")
else ()
    set(_nx_candidates ${NX_FLASH_TOOLS})
    if (NX_FLASH_TOOL)
        if (NX_FLASH_TOOL IN_LIST NX_FLASH_TOOLS)
            set(_nx_candidates ${NX_FLASH_TOOL})
        else ()
            string(REPLACE ";" ", " _nx_tools "${NX_FLASH_TOOLS}")
            message(WARNING "NX_FLASH_TOOL ${NX_FLASH_TOOL} does not drive probe ${NX_DEBUG_PROBE} "
                            "(${NX_DEBUG_PROBE_KIND}: ${_nx_tools}), ignored")
        endif ()
    endif ()
    set(_nx_reasons "")
    foreach (_nx_tool IN LISTS _nx_candidates)
        _nx_find_flash_tool(${_nx_tool} _nx_exe _nx_missing)
        if (_nx_missing STREQUAL "")
            set(_NX_FLASH_TOOL ${_nx_tool})
            set(_NX_FLASH_EXE "${_nx_exe}")
            break()
        endif ()
        string(APPEND _nx_reasons " ${_nx_tool} ${_nx_missing}.")
    endforeach ()
    if (NOT _NX_FLASH_TOOL)
        set(_NX_FLASH_PROBLEM "no flash tool for probe ${NX_DEBUG_PROBE} (${NX_DEBUG_PROBE_KIND}) on this "
                              "workstation:${_nx_reasons}")
    endif ()
endif ()
string(REPLACE ";" "" _NX_FLASH_PROBLEM "${_NX_FLASH_PROBLEM}")
if (_NX_FLASH_TOOL)
    message(STATUS "flash targets: ${_NX_FLASH_TOOL} (${_NX_FLASH_EXE}) for probe ${NX_DEBUG_PROBE}")
else ()
    message(STATUS "flash targets report when called: ${_NX_FLASH_PROBLEM}")
endif ()

# nx_add_flash_target(<name> DEPENDS <target> WHAT <text> IMAGES <hex>...): flashes the images in this order and lets
# the MCU run afterwards (J-Link `connect` resets and halts it, every implementation ends with reset and go). The
# command file lies next to the images of the configuration: <binary dir>/<config>/<name>.<tool suffix>.
function (nx_add_flash_target name)
    cmake_parse_arguments(PARSE_ARGV 1 arg "" "DEPENDS;WHAT" "IMAGES")
    if (NOT _NX_FLASH_TOOL)
        add_custom_target(
            ${name}
            COMMAND "${CMAKE_COMMAND}" -E echo "${name}: ${_NX_FLASH_PROBLEM}"
            COMMAND "${CMAKE_COMMAND}" -E false
            COMMENT "Flashing ${arg_WHAT} is not possible"
            VERBATIM)
        return()
    endif ()
    set(dir "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>")
    set(serial "${NX_DEBUG_PROBE_SERIAL}")
    if (_NX_FLASH_TOOL STREQUAL "jlink")
        set(script "${dir}/${name}.jlink")
        set(content "device ${NX_JLINK_DEVICE}\nsi ${NX_DEBUG_INTERFACE}\nspeed ${NX_DEBUG_SPEED_KHZ}\n")
        if (NX_DEBUG_INTERFACE STREQUAL "JTAG")
            string(APPEND content "jtagconf -1,-1\n")
        endif ()
        # PLAT-86: the script ends with the application running; without SetSkipDebugDeInit the J-Link clears
        # DEMCR.TRCENA when it closes the session and stops the DWT cycle counter (doc/hardware/debug-session-close.md).
        # The value is required ("Expected a dec value" without it); J-Link V6.44b or newer.
        string(APPEND content "connect\nexec SetSkipDebugDeInit = 1\nr\n")
        foreach (image IN LISTS arg_IMAGES)
            string(APPEND content "loadfile \"${image}\"\n")
        endforeach ()
        string(APPEND content "r\ng\nexit\n")
        set(command "${_NX_FLASH_EXE}")
        if (serial)
            list(APPEND command -USB ${serial})
        endif ()
        list(APPEND command -ExitOnError 1 -CommandFile "${script}")
        set(via "J-Link (${NX_JLINK_DEVICE}, ${NX_DEBUG_INTERFACE})")
    elseif (_NX_FLASH_TOOL STREQUAL "openocd")
        set(script "${dir}/${name}.openocd.cfg")
        set(content "source [find ${NX_OPENOCD_INTERFACE}]\n")
        if (serial)
            string(APPEND content "adapter serial ${serial}\n")
        endif ()
        string(APPEND content "transport select ${NX_OPENOCD_TRANSPORT}\nsource [find ${NX_OPENOCD_TARGET}]\n"
                              "adapter speed ${NX_DEBUG_SPEED_KHZ}\ninit\nreset init\n")
        foreach (image IN LISTS arg_IMAGES)
            string(APPEND content "flash write_image erase \"${image}\"\nverify_image \"${image}\"\n")
        endforeach ()
        string(APPEND content "reset run\nshutdown\n")
        set(command "${_NX_FLASH_EXE}" -f "${script}")
        set(via "OpenOCD (${NX_OPENOCD_TARGET}, ${NX_DEBUG_PROBE_KIND}, ${NX_DEBUG_INTERFACE})")
    elseif (_NX_FLASH_TOOL STREQUAL "stm32cubeprogrammer")
        # no script: the command line is the command file (-w per image with -v, -rst resets and runs)
        set(script "")
        set(command "${_NX_FLASH_EXE}" -c port=${NX_DEBUG_INTERFACE} freq=${NX_DEBUG_SPEED_KHZ})
        if (serial)
            list(APPEND command sn=${serial})
        endif ()
        foreach (image IN LISTS arg_IMAGES)
            list(APPEND command -w "${image}" -v)
        endforeach ()
        list(APPEND command -rst)
        set(via "STM32CubeProgrammer (${NX_DEBUG_INTERFACE})")
    elseif (_NX_FLASH_TOOL STREQUAL "pyocd")
        set(script "")
        set(command "${_NX_FLASH_EXE}" flash -t ${NX_PYOCD_TARGET} -f ${NX_DEBUG_SPEED_KHZ}000)
        string(TOLOWER "${NX_DEBUG_INTERFACE}" protocol)
        list(APPEND command -O dap_protocol=${protocol})
        if (serial)
            list(APPEND command -u ${serial})
        endif ()
        list(APPEND command ${arg_IMAGES})
        set(via "pyOCD (${NX_PYOCD_TARGET}, ${NX_DEBUG_INTERFACE})")
    elseif (_NX_FLASH_TOOL STREQUAL "dslite")
        # XDS110 and device are described by the UniFlash target configuration of the workstation
        set(script "")
        set(command "${_NX_FLASH_EXE}" --mode flash "--config=${NX_DSLITE_CCXML}" --flash --verify ${arg_IMAGES})
        set(via "UniFlash dslite (${NX_DSLITE_CCXML})")
    endif ()
    if (script)
        file(
            GENERATE
            OUTPUT "${script}"
            CONTENT "${content}")
    endif ()
    add_custom_target(
        ${name}
        COMMAND ${command}
        DEPENDS ${arg_DEPENDS}
        COMMENT "Flashing ${arg_WHAT} via ${via}"
        VERBATIM)
endfunction ()
# <<< Flash-Ziele je Debug-Probe (PLAT-18) - Ende

add_compile_options(
    "$<$<COMPILE_LANG_AND_ID:CXX,GNU>:-Wno-error=stringop-overflow;-Wno-error=maybe-uninitialized>"
)

set(INCLUDE_OPENBSW_LIBS_BSP
    ON
    CACHE BOOL "Include openbsw/libs/bsp/ in build")

# Plattform der Familie: Hat die Familie eine eigene neXtBSW-Plattform-CMake (PLAT-14, `platforms/<familie>`,
# geprüft oben im Block „Plattform der Familie“), gilt sie; sie bindet die unveränderten OpenBSW-Module der Familie
# selbst ein und wählt das Derivat über NX_DERIVATIVE, oder bringt als Familie ohne OpenBSW-Grundlage (rm4x) alles
# selbst mit (ARCH-25). Für stm32 ist das nötig, weil `external/openbsw/platforms/stm32/CMakeLists.txt` das Derivat
# aus einer Liste von zwei Namen wählt (`STM32_CHIP`) und für alles andere mit FATAL_ERROR abbricht. Familien ohne
# eigene CMake nehmen die Plattform des Submoduls.
if (NX_FAMILY_OWN_PLATFORM)
    add_subdirectory("${NX_FAMILY_PLATFORM_DIR}" platforms/${NX_FAMILY} EXCLUDE_FROM_ALL)
else ()
    add_subdirectory("${OPENBSW_DIR}/platforms" openbsw/platforms EXCLUDE_FROM_ALL)
endif ()
# FreeRTOS-Port nach Kernarchitektur (PLAT-10, seit ARCH-25 profilneutral unter platforms/common, Befund PLAT-14):
# nxFreeRtosPort/nxFreeRtosPortImpl aus NX_CORE_ARCHITECTURE statt des festen freertos_cm4_sysTick der
# OpenBSW-Plattform (dessen Ziele bleiben definiert, gebaut wird nur Gelinktes). Architekturen ohne Port (armv8m,
# armv7r) brechen dort mit „port missing“ ab. Nach der Plattform, weil armv7m deren CM4F-Ziele verwendet.
if (BUILD_TARGET_RTOS STREQUAL "FREERTOS")
    add_subdirectory("${NX_ROOT}/platforms/common/3rdparty/freertosPort" platforms/common/3rdparty/freertosPort
                     EXCLUDE_FROM_ALL)
endif ()
add_subdirectory("${OPENBSW_DIR}/libs" openbsw/libs EXCLUDE_FROM_ALL)

# bspMcu verweist per CMAKE_SOURCE_DIR auf CMSIS und bindet den Derivat-Header ein.
# Hier werden die Pfade je Familie korrigiert und die mcu.h der **Familie** vorangestellt
# (platforms/<familie>/bsp/bspMcu, PLAT-11; vor PLAT-23 lag je Board eine Kopie unter
# ecu/platforms/<board>/mcu/include). Das Generat-Verzeichnis liefert mcu/mcuDerivative.h (Derivat-Header aus
# mcudb, ARCH-03), mcu/derivativeFacts.h (NVIC-Bits, MPU-Regionen, ARCH-16) und bootloader/bl_memory.h
# (Speicheraufteilung für bl_config.h, ARCH-08).
# Hat die Familie eine eigene Plattform-CMake (PLAT-14), setzt deren nxBspMcu die Reihenfolge selbst - dort
# stehen zusätzlich die Hersteller-Header, die diese Familie mitbringt (platforms/<familie>/.../3rdparty).
# Ohne OpenBSW-Ziel bspMcu (Familie ohne OpenBSW-Grundlage, ARCH-25) gibt es nichts zu korrigieren.
if (NOT TARGET nxBspMcu AND TARGET bspMcu)
    # >>> CMSIS-Pfade nach Kernprofil (ARCH-25) - Anfang
    # CMSIS-Core gibt es nur für das M-Profil: cmsis und cmsis/m-profile nur für ARMv6-M/ARMv7-M/ARMv8-M, damit ein
    # Cortex-R-Build ein versehentliches core_cm4.h nicht still übersetzt (Befund ARCH-19 #3.2-5). Reihenfolge für
    # das M-Profil unverändert. Ohne Compiler lauffähig (platforms/common/test/test_platform_selection.py) - dort
    # gibt es kein NX_ROOT, deshalb derselbe Rückfall wie im Block der Familien-Plattform (ARCH-57).
    if (NOT NX_ROOT)
        set(NX_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
    endif ()
    set(_NX_M_PROFILE_ARCHITECTURES armv6m armv7m armv8m)
    set(_BSP_MCU_INCLUDES "${NX_ROOT}/platforms/${NX_FAMILY}/bsp/bspMcu/include")
    # Kern-Header von CMSIS: core_cm0plus.h liefert nur platforms/common (ARMv6-M, PLAT-10), core_cm4.h OpenBSW.
    if (NX_CORE_ARCHITECTURE STREQUAL "armv6m")
        list(APPEND _BSP_MCU_INCLUDES "${NX_ROOT}/platforms/common/3rdparty/cmsis")
    endif ()
    list(APPEND _BSP_MCU_INCLUDES "${OPENBSW_DIR}/platforms/${NX_FAMILY}/bsp/bspMcu/include")
    if (NX_CORE_ARCHITECTURE IN_LIST _NX_M_PROFILE_ARCHITECTURES)
        list(APPEND _BSP_MCU_INCLUDES "${OPENBSW_DIR}/libs/3rdparty/cmsis"
             "${OPENBSW_DIR}/libs/3rdparty/cmsis/m-profile")
    endif ()
    list(APPEND _BSP_MCU_INCLUDES "${BOARD_GENERATED_DIR}")
    # <<< CMSIS-Pfade nach Kernprofil (ARCH-25) - Ende
    set_target_properties(bspMcu PROPERTIES INCLUDE_DIRECTORIES "${_BSP_MCU_INCLUDES}"
                                            INTERFACE_INCLUDE_DIRECTORIES "${_BSP_MCU_INCLUDES}")
endif ()

target_include_directories(etl BEFORE INTERFACE "${OPENBSW_APP_DIR}/etl_profile")

# >>> Größe des OpenBSW-Logpuffers (PLAT-04) - Anfang
# Der Ringpuffer des Loggers ist ein Template-Argument in
# external/openbsw/libs/bsw/loggerIntegration/include/logger/Config.h (1024u * 8u, liegt als Member in .bss). Das
# Submodul wird nie geändert, deshalb kommt der Wert über die **Include-Reihenfolge**: dieser Block schreibt
# <build>/generated/logger/logger/Config.h und stellt das Verzeichnis bei loggerIntegration vor dessen eigenes
# include/ - dasselbe Mittel wie das etl-Profil darüber. Beide Einbindungen greifen: LoggerComposition.h bindet
# "logger/Config.h" ein (in seinem eigenen Verzeichnis liegt kein logger/Config.h, also entscheidet der Suchpfad),
# platforms/<familie>/port/main bindet <logger/Config.h> ein (SYS-10). Jede Übersetzungseinheit sieht damit
# denselben Wert, und die Zeitschranke des Konsolen-Flush (consoleFlushLimitMs, SYS-09/SYS-10) bleibt daraus
# abgeleitet statt konfiguriert.
# Vorgabe für alle Boards: 8192 Byte (PLAT-04; 4096 verlor am Board beim Start 9 Logzeilen, 2026-09-18). Ein Board-Port kann NX_LOGGER_BUFFER_BYTES_DEFAULT in Options.cmake
# setzen, und -DNX_LOGGER_BUFFER_BYTES=<byte> übersteuert beides. Die Vorgabe steht bewusst hier und nicht je
# Board-Port: der Rückfallwert des Host-Tests in IdleOutputDrain.h (SYS-10) prüft per static_assert gegen genau
# diesen Wert, ein Board ohne eigene Vorgabe fiel sonst auf die 8192 des Submoduls zurück und brach den Bau ab.
# **PLAT-27** ersetzt nur die Herkunft (Puffergröße aus der Projektebene, Konsole/Logger abwählbar): dann setzt der
# Generator NX_LOGGER_BUFFER_BYTES, dieser Block bleibt.
if (NOT DEFINED NX_LOGGER_BUFFER_BYTES_DEFAULT)
    set(NX_LOGGER_BUFFER_BYTES_DEFAULT 8192)
endif ()
if (TARGET loggerIntegration AND (DEFINED NX_LOGGER_BUFFER_BYTES OR DEFINED NX_LOGGER_BUFFER_BYTES_DEFAULT))
    # An explicit value (-DNX_LOGGER_BUFFER_BYTES=<byte>, or later a generator from the project, PLAT-27) wins. The
    # default is deliberately NOT written to the cache: a changed default has to reach existing build directories.
    # The first version of this block cached the default, so a build directory kept 4096 after the default went back
    # to 8192 (2026-09-18) - such a stale entry is recognised by its help string and dropped once.
    get_property(_nxLoggerHelp CACHE NX_LOGGER_BUFFER_BYTES PROPERTY HELPSTRING)
    if (_nxLoggerHelp STREQUAL "Size of the OpenBSW logger ring buffer in bytes (default: board port, PLAT-04)")
        unset(NX_LOGGER_BUFFER_BYTES CACHE)
    endif ()
    if (NOT DEFINED NX_LOGGER_BUFFER_BYTES)
        set(NX_LOGGER_BUFFER_BYTES "${NX_LOGGER_BUFFER_BYTES_DEFAULT}")
    endif ()
    if (NOT NX_LOGGER_BUFFER_BYTES MATCHES "^[0-9]+$")
        message(FATAL_ERROR "NX_LOGGER_BUFFER_BYTES (${NX_LOGGER_BUFFER_BYTES}) is not a number of bytes")
    endif ()
    # Lower bound: on overflow EntryBuffer drops whole entries from the front, and one entry is at most
    # MaxEntrySize (128) + 1 length byte long - below a handful of entries the buffer says nothing. Upper bound:
    # the RAM of a small derivative.
    if (NX_LOGGER_BUFFER_BYTES LESS 512 OR NX_LOGGER_BUFFER_BYTES GREATER 65536)
        message(FATAL_ERROR "NX_LOGGER_BUFFER_BYTES (${NX_LOGGER_BUFFER_BYTES}) is outside 512..65536 bytes")
    endif ()
    # Der erzeugte Header wiederholt den Aufbau des Originals (Lock, MaxEntrySize, Typen) und ändert nur die
    # Größe. Ändert OpenBSW das Original, darf das nicht stillschweigend untergehen - Vergleich über den Inhalt
    # ohne Zeilenenden, weil der Checkout CRLF liefern kann.
    set(_NX_LOGGER_CONFIG_UPSTREAM "${OPENBSW_DIR}/libs/bsw/loggerIntegration/include/logger/Config.h")
    file(READ "${_NX_LOGGER_CONFIG_UPSTREAM}" _nx_logger_config_text)
    string(REPLACE "\r\n" "\n" _nx_logger_config_text "${_nx_logger_config_text}")
    string(SHA256 _nx_logger_config_hash "${_nx_logger_config_text}")
    if (NOT _nx_logger_config_hash STREQUAL "b3d39402154d2e0fbec79ed22ea7307f93e7e4a9b0c928996e7edb5c475573f6")
        message(
            WARNING
                "${_NX_LOGGER_CONFIG_UPSTREAM} has changed (SHA-256 of its content without line endings is "
                "${_nx_logger_config_hash}): compare it with the replacement generated below (PLAT-04) and record "
                "the new hash there")
    endif ()
    set(NX_LOGGER_CONFIG_DIR "${CMAKE_BINARY_DIR}/generated/logger")
    file(
        CONFIGURE
        OUTPUT "${NX_LOGGER_CONFIG_DIR}/logger/Config.h"
        @ONLY
        CONTENT
            [=[
/* Generated by CMake (neXtBSW PLAT-04) - do not edit, change NX_LOGGER_BUFFER_BYTES instead.
 *
 * Replaces logger/Config.h of the OpenBSW loggerIntegration through the include order (this directory comes
 * before external/openbsw/libs/bsw/loggerIntegration/include). The submodule itself stays unchanged. Everything
 * except the buffer size is identical to the original; the root CMakeLists checks the original for changes.
 *
 * SPDX-License-Identifier: Apache-2.0
 * Copyright (c) 2024 Accenture (structure of the original)
 */

#pragma once

#include <interrupts/SuspendResumeAllInterruptsScopedLock.h>
#include <logger/BufferedLoggerOutput.h>

namespace logger
{
using EntryIndexType = uint32_t;
using TimestampType  = uint32_t;

using BufferedLoggerOutputType = ::logger::declare::BufferedLoggerOutput<
    @NX_LOGGER_BUFFER_BYTES@u,                          // BufferSize (neXtBSW: configurable, PLAT-04)
    ::interrupts::SuspendResumeAllInterruptsScopedLock, // Lock
    128,                                                // MaxEntrySize
    uint16_t,                                           // T
    EntryIndexType,                                     // EntryIndexType
    TimestampType>;                                     // TimestampType

} // namespace logger

/* Default of NX_LOGGER_BUFFER_BYTES (root CMakeLists.txt or board port). It differs from BufferSize above only in a
 * test build with an explicit -DNX_LOGGER_BUFFER_BYTES=<byte> (SYS-12); the host-test value of the console flush
 * (IdleOutputDrain.h, SYS-10) is checked against this default, not against the size of a test build. */
#define NX_LOGGER_BUFFER_BYTES_DEFAULT @NX_LOGGER_BUFFER_BYTES_DEFAULT@u
]=])
    target_include_directories(loggerIntegration BEFORE PUBLIC "${NX_LOGGER_CONFIG_DIR}")
    message(STATUS "logger buffer: ${NX_LOGGER_BUFFER_BYTES} bytes (PLAT-04, generated/logger/logger/Config.h)")
endif ()
# <<< Größe des OpenBSW-Logpuffers (PLAT-04) - Ende

# Eigene Module im OpenBSW-Aufbau: Schnittstellen und Gerätetreiber (libs), S32K1xx-Treiber (platforms).
# PLAT-25 (Folgepunkt aus PLAT-24, Befund ARCH-09 B8): **EXCLUDE_FROM_ALL** wie bei den OpenBSW-Bibliotheken
# oben. Die Module sind Angebote; gebaut wird, was ein Ziel linkt (`ecu`, `bootloader` und was daran hängt).
# Vorher übersetzte `all` jedes Modulverzeichnis, auch wenn es niemand linkt: ein Board ohne SBC baute
# `sbcUja113x` und `bspLpspi` mit, ein Board ohne High-Side-Schalter dessen Bibliothek. Die Auswahl selbst
# steht in der Konfiguration (NX_MODULES/NX_MODULE_TARGETS und NX_USE_SPI/DIO/ADC aus
# integration/modules.cmake, tools/bswgen/modules.py); `add_subdirectory` macht die Ziele nur bekannt.
# Wichtig: Modulcode ohne Ziel wird damit nicht mehr nebenbei mitübersetzt - dafür sind die Host-Tests je Modul
# zuständig (libs/bsw/<modul>/test, tools/test --group host) und die Bau-Profile, die das Modul nutzen.
foreach (
    _NX_MODULE_DIR
    libs/bsp/bspSpi
    libs/bsp/bspAdcAccess
    # IO-52: pin edge contract (header only) and the pin-interrupt dispatcher of the family
    libs/bsp/bspPinEdge
    platforms/${NX_FAMILY}/bsp/bspPinIrq
    # IO-54: events on digital inputs (bspPinEvent, console group bspPinEventCommand), linked only with pin_events
    libs/bsp/bspPinEvent
    libs/bsw/autosarBase
    libs/bsw/sbcUja113x
    libs/bsw/highSideSwitch
    libs/bsw/canPhyPassive
    libs/bsw/canSystem
    libs/bsw/consoleContext
    libs/bsw/e2e
    libs/bsw/ecuState
    # NVM-03: fault memory (header only), used by ecu/application (RteSystem) and ecu/uds (snapshot data, 85)
    libs/bsw/dem
    # NVM-19: NvM core (header only, QM and ASIL blocks, NVM-18), used by ecu/application (RteSystem, NvmRteBlocks)
    libs/bsw/nvm
    platforms/${NX_FAMILY}/bsp/bspLpspi
    # IO-61: FTM PWM group (header only) - center alignment, trigger, Q15 duty hand-over; used by ComplementaryPwm
    platforms/${NX_FAMILY}/bsp/bspFtmPwmGroup
    # IO-07: familienneutrale Hall-Auswertung und Zugang der Familie, gelinkt nur bei einem hall-Baustein
    libs/bsw/hallDecoder
    platforms/${NX_FAMILY}/bsp/bspHall
    # IO-09: I2C master on FlexIO, linked only by an integration with a FlexIO i2c bus
    platforms/${NX_FAMILY}/bsp/bspFlexioI2c
    # IO-05: TMC6200 gate driver, linked only with a board device of module gdTmc6200
    libs/bsw/gdTmc6200
    # IO-06: MC33771C battery cell controller, linked only with a board device of module bccMc33771
    libs/bsw/bccMc33771)
    if (EXISTS "${NX_ROOT}/${_NX_MODULE_DIR}")
        add_subdirectory("${NX_ROOT}/${_NX_MODULE_DIR}" "${_NX_MODULE_DIR}" EXCLUDE_FROM_ALL)
    endif ()
endforeach ()

add_subdirectory("${NX_ROOT}/ecu" ecu)
if (PLATFORM_SUPPORT_UDS)
    add_subdirectory("${NX_ROOT}/ecu/uds" ecu/uds)
endif ()
# TOOL-01: XCP on CAN (measure RTE signals, calibrate RTE parameters) and the A2L next to app.ecu.elf
option(NX_XCP "XCP slave on CAN and A2L of the application (ecu/xcp, TOOL-01)" ON)
if (PLATFORM_SUPPORT_CAN AND NX_XCP)
    add_subdirectory("${NX_ROOT}/ecu/xcp" ecu/xcp)
endif ()
add_subdirectory("${NX_ROOT}/bootloader" bootloader)
