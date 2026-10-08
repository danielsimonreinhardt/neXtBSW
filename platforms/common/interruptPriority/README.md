# Interrupt priorities as ranks (PLAT-10, wired up in PLAT-21, per core profile since ARCH-24)

Header `include/interrupts/nxInterruptPriority.h`, C and C++, no hardware access. CMake target
`nxInterruptPriority` (include directory only). Host tests: `platforms/common/test/test_cortex_m0plus.py`
(`InterruptPriorityHostTest`, the macros themselves; `InterruptPriorityCrossTest` with arm-none-eabi-gcc for
Cortex-R4F, Cortex-M4F and Cortex-M0+) and `tools/bswgen/test_bswgen_interrupts.py` (the generated inputs, compiled
with this header, including the synthetic Cortex-R4F profile TESTR4F).

## Layout per core profile (ARCH-24, profile headers moved in ARCH-25)

| File | Profile | What a rank becomes |
|---|---|---|
| `include/interrupts/nxInterruptPriority.h` | all | entry header: selects the profile header from the generated macros |
| `platforms/common/cortex-m/interruptPriority/include/interrupts/nxNvicPriority.h` | Cortex-M (NVIC) | NVIC level `NX_IRQ_MAX_SYSCALL_LEVEL + rank` (sections below) |
| `platforms/common/cortex-r/interruptPriority/include/interrupts/nxVimChannel.h` | Cortex-R (VIM) | VIM channel: ranked interrupts take the free channels in rank order |

The profile comes from the generated `mcu/derivativeFacts.h` (`tools/bswgen`, both family generators):
`NX_CORE_ARCHITECTURE_<ARMV6M|ARMV7M|ARMV8M|ARMV7R>` and `NX_CORE_INTERRUPT_CONTROLLER_<NVIC|VIM>`, one table
`tools/nxconfig/config.py` `CORES`. **Not** `__ARM_ARCH`: GCC defines it as 7 for Cortex-M4F and Cortex-R4F alike, so
before ARCH-24 a Cortex-R4F build silently got the BASEPRI rules (finding ARCH-19). Without exactly one architecture
and one controller the build stops; the compiler profile (`__ARM_ARCH_PROFILE` `'M'`/`'R'`) is only cross-checked.

The entry header includes the profile headers by relative path, so every user keeps its include directory. The
profile headers live in the profile folders of the target picture (doc/architecture/README.md section 8),
`platforms/common/cortex-m` and `platforms/common/cortex-r`; ARCH-25 moved them there without changing their code.

### VIM (Cortex-R)

| Value | Level | Generated macro |
|---|---|---|
| `vim.channels`, `vim.fixed[].channel` | derivative (mcudb, checked in `tools/nxconfig/checks.py`) | `NX_VIM_CHANNEL_COUNT`, `NX_VIM_FIXED_CHANNEL_COUNT`, `NX_VIM_FIXED_MASK0..3` (32 bits each, at most 128 channels) |
| rank per interrupt | project | `NX_IRQ_RANK_<SOURCE>_<NAME>` |
| channel | derived: n-th entry in rank order (equal ranks in YAML order, `checks.vim_channel_order`) gets the n-th free channel | `NX_IRQ_CHANNEL_<SOURCE>_<NAME> = NX_IRQ_OS_CHANNEL(<channel>)` |

- The priority of a VIM channel is its number (0 highest). There is no priority mask and no syscall level: every
  ranked interrupt is an IRQ and may call the RTOS, a FIQ never and is no rank. `NX_NVIC_PRIORITY_BITS` or a syscall
  level other than 0 on a VIM core stops the build.
- `NX_IRQ_OS_CHANNEL(channel)` with a channel beyond the derivative or on a fixed channel is a compile error.
- One channel per ranked entry. How many requests an instance really has (e.g. two per DCAN) and the remapping with
  `CHANCTRL` belong to the Cortex-R family generator (PLAT-37); the channel count and fixed channels of a real
  derivative come from its documents (PLAT-36).

## NVIC (Cortex-M)

### Problem

The board integration writes NVIC values directly (`SYS_SetPriority(CAN0_..., 8)`, FreeRTOS
`configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY 5`, lowest priority `0x0F`). These numbers assume 4 priority bits.
Cortex-M0+ has 2 bits; CMSIS `NVIC_SetPriority` shifts the value by `8 - __NVIC_PRIO_BITS`, so 8 becomes 0 = highest
priority, without any error.

### Concept

| Value | Level | Owner |
|---|---|---|
| `nvic_priority_bits` | derivative (mcudb), checked against the core in `tools/nxconfig/checks.py` | fact |
| `NX_IRQ_MAX_SYSCALL_LEVEL` | family/RTOS port: ARMv7-M/ARMv8-M `1..2^bits-1` (BASEPRI), ARMv6-M `0` (no BASEPRI, the kernel masks all interrupts) | family default, project may raise it for zero-latency interrupts |
| rank per interrupt | project: 0 = most urgent interrupt that calls the RTOS, 1 = next, ... | project |
| NVIC level | derived: `NX_IRQ_MAX_SYSCALL_LEVEL + rank` | generator / macro |

- Ranks available: `NX_IRQ_OS_RANK_COUNT = 2^bits - NX_IRQ_MAX_SYSCALL_LEVEL` (S32K144 today: 11, Cortex-M0+: 4).
- `NX_IRQ_OS_LEVEL(rank)` with a rank that does not fit is a compile error; a generator (ARCH-05) should report the
  same with the YAML path of the interrupt before the build.
- FreeRTOS: `configKERNEL_INTERRUPT_PRIORITY = NX_FREERTOS_KERNEL_INTERRUPT_PRIORITY` (lowest level),
  `configMAX_SYSCALL_INTERRUPT_PRIORITY = NX_FREERTOS_MAX_SYSCALL_INTERRUPT_PRIORITY`.
- A device header whose `__NVIC_PRIO_BITS` differs from mcudb stops the build (S32K116.h: 4, core: 2).
- Mapping of today's eval board: bits 4, max syscall level 5, CAN level 8 = **rank 3**. Same rank on a Cortex-M0+
  (level 3, the lowest) still fits; rank 4 would not.

Interrupts that must never be masked by the kernel (zero latency, no RTOS calls) get levels below
`NX_IRQ_MAX_SYSCALL_LEVEL`; they do not exist on ARMv6-M and are not part of the rank macro.

### Where the values come from (PLAT-21)

```
mcudb derivative.nvic_priority_bits ─┐
project interrupts.max_syscall_level ├─ tools/nxconfig (checks.py: rank fits, YAML path)
project interrupts.ranks ────────────┘                 │
                                                       v
                        tools/bswgen -> <build>/generated/board/interrupts/interruptPriorities.h
                        (NX_IRQ_MAX_SYSCALL_LEVEL, #include this header; NX_NVIC_PRIORITY_BITS and
                         NX_CORE_ARCHITECTURE_* in mcu/derivativeFacts.h,
                         NX_IRQ_RANK_<SOURCE>_<NAME> and NX_IRQ_LEVEL_<SOURCE>_<NAME> per rank)
                                                       v
                        board port: SYS_SetPriority(CAN0_..., NX_IRQ_LEVEL_CAN_CAN0)
```

- `max_syscall_level` may be omitted: the default of the core architecture is used (ARMv6-M 0, otherwise 1).
- The project names a rank per board entry (`source` can/uart/spi/i2c/adc/input_capture/pwm/dio plus the board
  name), so the numbers survive a change of derivative; nxconfig reports a rank that does not fit with file, line
  and YAML path, the macro stops the build as the second line of defence.

## Not done yet

- `FreeRtosPlatformConfig.h` of the board port still carries its own `configLIBRARY_*` numbers; `main.cpp` checks
  them against the generated values with `static_assert`. It becomes a family template with ARCH-05.
- Which interrupt the integration really uses is still hand written (`SYS_SetPriority` in `main.cpp`, the UART
  receive interrupt in `bspConfiguration/.../UartConfig.cpp` still writes level 9 directly). With the generated
  integration (ARCH-05/ARCH-07) the ISR table and its priorities come from the configuration, and a missing rank is
  reported with a YAML path instead of a compile error.
