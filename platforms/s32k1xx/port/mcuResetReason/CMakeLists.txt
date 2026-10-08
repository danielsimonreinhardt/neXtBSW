# RTE-16: reset reason of the MCU (RCM_SRS) as second source of the BSW service bsw.reset (ecu/rte/services,
# bswService/McuResetReason.h). Family code of s32k1xx: one register read before main(), mapping in
# include/mcuResetReason/RcmResetReason.h (host test test/host/RcmResetReasonHostTest.cpp). Another family provides the
# same function from its own reset status and registers its library the same way.
add_library(bswMcuResetReason src/mcuResetReason/McuResetReason.cpp)
target_include_directories(bswMcuResetReason PUBLIC include PRIVATE "${NX_ROOT}/ecu/rte/services/include")
target_link_libraries(bswMcuResetReason PUBLIC ecuState PRIVATE bspMcu)

# ecu/rte/CMakeLists.txt links it to bswServices and sets NX_BSW_MCU_RESET_REASON=1
set_property(GLOBAL PROPERTY NX_BSW_MCU_RESET_REASON bswMcuResetReason)
