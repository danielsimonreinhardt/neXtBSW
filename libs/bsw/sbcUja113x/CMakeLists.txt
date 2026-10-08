add_library(sbcUja113x src/sbc/Uja113x.cpp)

target_include_directories(sbcUja113x PUBLIC include)

target_link_libraries(sbcUja113x PUBLIC bspSpi)

# Role can_phy (sbc/Uja113xCanPhy.h) on top of the OpenBSW CanPhy interface
add_library(sbcUja113xCanPhy INTERFACE)

target_link_libraries(sbcUja113xCanPhy INTERFACE sbcUja113x bsp)

# Role power_supply (sbc/Uja113xPowerSupply.h) on top of the ECU state interface (libs/bsw/ecuState, SYS-01)
add_library(sbcUja113xPowerSupply INTERFACE)

target_link_libraries(sbcUja113xPowerSupply INTERFACE sbcUja113x ecuState)
