# Family part of the bootloader target besides the HAL (included by bootloader/CMakeLists.txt if present):
# BL_FAMILY_SOURCES are added to the target.
#
# BL-34: the low-voltage detect reset uses the family port's header pmc/LowVoltageDetectReset.h (C++), so that
# bootloader and application write PMC_LVDSC1 from one definition. Only this source sees the port's include directory.
set(BL_FAMILY_SOURCES "${BL_PLATFORM_DIR}/bl_lvd_s32k1xx.cpp")
set_source_files_properties("${BL_PLATFORM_DIR}/bl_lvd_s32k1xx.cpp" PROPERTIES INCLUDE_DIRECTORIES
                            "${NX_ROOT}/platforms/s32k1xx/port/main/include")
