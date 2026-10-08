/* Probe for the toolchain configure test (assembler flags). */
    .syntax unified
    .thumb
    .text
    .global probe_nop
    .type probe_nop, %function
probe_nop:
    bx lr
