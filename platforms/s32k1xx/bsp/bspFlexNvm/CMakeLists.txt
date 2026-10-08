# FlexNVM of the family s32k1xx (NVM-02, NVM-07): own family module next to OpenBSW's bspEepromDriver, which it uses.
#
#   bspFlexNvm           header only: flexnvm/FlexNvmEeprom.h, the emulated EEPROM as the virtual EEPROM of the NvM
#                        (eeprom::IEepromDriver). Uses an existing partition only and launches no flash command.
#   bspFlexNvmPartition  flexnvm/FlexNvmPartition.h: the one-time, IRREVERSIBLE partition (PGMPART through OpenBSW's
#                        EepromDriver::init(), launched from RAM). Linked only into test builds with the console command
#                        "flexnvm partition" (option NX_FLEXNVM_PARTITION_COMMAND of port/main), never at start-up.
#
# Added from ../../port/main/CMakeLists.txt after the OpenBSW platform (which defines bspEepromDriver, EXCLUDE_FROM_ALL).
# Host tests: test/test_flexnvm.py (register model, OpenBSW's EepromDriver.cpp compiled against it).

#   NVM-12: flexnvm/FlexNvmDataFlash.h (header only, in bspFlexNvm) - the data flash share of the FlexNVM as
#   dataflash::IDataFlash of libs/bsw/nvmDataFlash (read like memory, erase a sector, program a phrase).
#   NVM-25: flexnvm/FlexNvmBlockLock.h (header only) - the one lock of the FlexNVM block shared by both drivers.

add_library(bspFlexNvm INTERFACE)
target_include_directories(bspFlexNvm INTERFACE include)
if (NOT TARGET nvmDataFlash)
    add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../../../libs/bsw/nvmDataFlash" nxNvmDataFlash)
endif ()
# NVM-12: bspInterrupts - every EEPROM store and data flash launch is checked and made with interrupts suspended
target_link_libraries(bspFlexNvm INTERFACE bsp bspMcu bspInterrupts nvmDataFlash)

if (NOT TARGET bspEepromDriver)
    message(FATAL_ERROR "platforms/s32k1xx/bsp/bspFlexNvm: the OpenBSW target bspEepromDriver must exist before (add "
                        "this directory after the OpenBSW platform external/openbsw/platforms/s32k1xx)")
endif ()

add_library(bspFlexNvmPartition EXCLUDE_FROM_ALL src/FlexNvmPartition.cpp)
target_include_directories(bspFlexNvmPartition PUBLIC include)
target_link_libraries(bspFlexNvmPartition PUBLIC bsp PRIVATE bspEepromDriver bspMcu)
