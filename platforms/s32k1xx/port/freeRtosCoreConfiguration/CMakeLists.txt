add_library(freeRtosCoreConfiguration INTERFACE)

# ARCH-13: generated bsp/clock/boardClock.h (configCPU_CLOCK_HZ from NX_CLOCK_CORE_HZ)
target_include_directories(freeRtosCoreConfiguration INTERFACE include "${BOARD_GENERATED_DIR}")

# PLAT-12: configKERNEL_INTERRUPT_PRIORITY and configMAX_SYSCALL_INTERRUPT_PRIORITY come from the generated
# interrupt priorities of the project (interrupts/interruptPriorities.h, which includes
# interrupts/nxInterruptPriority.h of platforms/common/interruptPriority) instead of being written for 4 NVIC
# priority bits. The target is added by ../main/CMakeLists.txt, which runs before this directory.
target_link_libraries(freeRtosCoreConfiguration INTERFACE bsp bspMcu nxInterruptPriority)
