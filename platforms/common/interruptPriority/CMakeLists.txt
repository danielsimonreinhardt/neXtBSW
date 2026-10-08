# Interrupt priorities as ranks (PLAT-10, README.md): header only, C and C++, no hardware access.
#
# The inputs NX_NVIC_PRIORITY_BITS and NX_IRQ_MAX_SYSCALL_LEVEL come from the generated
# interrupts/interruptPriorities.h of the project (tools/bswgen, PLAT-21), not from this target.
# ARCH-24: include/interrupts/nxInterruptPriority.h selects ../cortex-m/ (NVIC) or ../cortex-r/ (VIM, moved there in
# ARCH-25) by relative path from
# NX_CORE_ARCHITECTURE_* and NX_CORE_INTERRUPT_CONTROLLER_* (generated mcu/derivativeFacts.h), so one include
# directory serves every core profile.

add_library(nxInterruptPriority INTERFACE)

target_include_directories(nxInterruptPriority INTERFACE include)
