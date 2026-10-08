add_library(
    safeMemory
    src/safeMemory/MemoryProtection.cpp src/safeMemory/SafeMemory.cpp
    src/safeMemory/IntegrityEccHandler.cpp src/safeMemory/RomCheck.cpp
    # SYS-73: start-up latent-fault test of the RAM ECC through the EIM
    src/safeMemory/EccLatentTest.cpp src/safeMemory/EccLatentTestTarget.cpp)

target_include_directories(safeMemory PUBLIC include)

# SYS-44: the flash write indicator of the ROM check lives in ordinary RAM - its own library, whose archive name does
# not match the MPU protected sections of the linker script ("*libsafe*"), so every flash writer may update it
add_library(romCheckFlashWrites src/romCheckFlashWrites/RomCheckFlashWrites.cpp)
target_include_directories(romCheckFlashWrites PUBLIC include)
target_link_libraries(romCheckFlashWrites PUBLIC bspInterrupts)

target_link_libraries(
    safeMemory
    PUBLIC bspMcu
           bspInterrupts
           bsp # bsp/timer/SystemTimer.h: time base of the re-check period (SYS-44)
           romCheckFlashWrites
           safeUtils
           safeSupervisor
           async
           configuration)

# SYS-38: post-link step of the ROM check of this family (reference CRC into the image, produces the hex). An
# application image picks it up with nx_rom_check_image() (cmake/NxRomCheck.cmake); only then RomCheck runs.
set_property(GLOBAL PROPERTY NX_ROM_CHECK_TOOL "${CMAKE_CURRENT_SOURCE_DIR}/tools/romCheckImage.py")
