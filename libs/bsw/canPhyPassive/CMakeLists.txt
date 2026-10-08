add_library(canPhyPassive INTERFACE)

target_include_directories(canPhyPassive INTERFACE include)

target_link_libraries(canPhyPassive INTERFACE bsp)
