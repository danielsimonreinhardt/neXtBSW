# elfcheck – linked images against the derivative map and the core

Proposal D of [`doc/safety/tool-classification.md`](../../doc/safety/tool-classification.md) (finding TF6,
requirement SSR-083), task PLAT-80.

One generated memory layout feeds the bootloader, the application linker script, the updater and `canflash`.
They agree by construction, so a layout that is wrong for the derivative is wrong everywhere at once and every
consumer test passes. `elfcheck` checks the **linked images** against an expectation that does not come from that
chain:

| Source | What it gives |
|---|---|
| `mcudb/<family>/<derivative>.yaml` | code flash, RAM regions, sector geometry (equal or unequal sectors), program size, core and FPU |
| `project.yaml` (read directly, not through `tools/nxconfig`) | which flash, bootloader size and RAM, application RAM regions, board name |
| the images | sections, program headers, symbols, build attributes and the ten layout values the bootloader was compiled with (`layoutValues`, read out of the bootloader image) |

It imports nothing from `tools/nxconfig`, `tools/bswgen`, `tools/rtegen` or `tools/canflash`; the ELF reader
(`elffile.py`), the DWARF reader (`dwarf.py`), the Intel HEX reader (`ihex.py`) and the table of expected build
attributes per core (`cores.py`) are its own. No Qt. The subcommand `timing` (PLAT-82, below) checks the RTE timing
and the interrupt priorities.

## Checks

| Code | Check |
|---|---|
| `core-arch`, `core-profile`, `fp-arch`, `fp-precision`, `fp-abi` | `Tag_CPU_arch`, profile, `Tag_FP_arch`, `Tag_ABI_HardFP_use`, `Tag_ABI_VFP_args` of every image against `derivative.core`/`fpu` (hard float ABI with FPU, soft without) |
| `section-outside-memory`, `section-region-kind` | run address of every allocated section inside a code flash or RAM region of the derivative |
| `section-outside-flash`, `section-outside-area` | load address of every section with content inside the code flash and inside the image's area (bootloader area, application area) |
| `section-outside-ram` | RAM sections inside the regions the project gives the image (`memory.bootloader.ram`/`ram_size`, `memory.application.ram`) |
| `overlap` | no two sections on the same load or RAM bytes, no byte shared by bootloader and application or updater, application area outside the bootloader area |
| `layout-flash`, `layout-sector-size`, `layout-phrase`, `layout-ram` | the bootloader's layout values against the derivative (flash start and size, sector size or 0 for unequal sectors, program size, one block of RAM) |
| `sector-alignment` | application start and end, validity record and end of the bootloader area on sector boundaries of the derivative |
| `record-position` | record sector inside the flash, outside the application and the bootloader area; record file at the address the bootloader reads |
| `vector-table`, `vector-alignment`, `initial-sp`, `reset-vector` | Cortex-M: vector table at the start of the area, aligned for VTOR, stack pointer in the image's RAM, reset vector Thumb and inside the image |
| `boot-request` | boot request word inside bootloader and application RAM, not covered by a section a start-up code initialises |
| `rom-list-*` | ROM check list of SM-14 present, directly behind the last flash content, inside the application area, areas disjoint and covering every flash byte except the list |
| `record-missing`, `record-format`, `record-image`, `record-layout`, `record-target` | `<stem>.record.hex` exists, its fields are consistent, size and CRC-32 match the image (the `.hex` next to the ELF, itself compared with the ELF: `hex-differs`), layout identity and target identity (`CRC-32("<board>/<derivative>")`) match |
| `blob-*` | the bootloader embedded in the updater is this bootloader image and fits the bootloader area |
| `fcf-missing`, `fcf-secured`, `fcf-mass-erase`, `fcf-backdoor`, `fcf-protection`, `fcf-value` | NVM-15: an image that writes the first code flash sector (the bootloader) carries the family's flash configuration field complete and with the reference value, in the ELF and in the `.hex` next to it. s32k1xx: `0x400..0x40F`, FSEC `0xFE` (unsecured, mass erase enabled, backdoor key disabled), FOPT `0x7F`, nothing protected - the only field the updater accepts. Missing or secured, the chip comes up secured after the next reset; once the CSEc took a key space, mass erase is locked too and the part is lost. Families without such a field (`CONFIG_FIELDS` in `check.py`) are not checked |
| `heap-symbol` | DOC-28 (NXCPP-05, finding DE5 of `doc/safety/dev-environment.md`): no image defines a symbol of a heap allocator - newlib/libnosys `malloc`, `_malloc_r`, `calloc`, `realloc`, `free`, `_free_r`, `memalign`, `_sbrk`, `_sbrk_r`, ... (`HEAP_SYMBOLS` in `check.py`) or any `operator new`/`new[]` (`_Znw*`, `_Zna*`; placement new `_ZnwjPv` allocates nothing and stays allowed). The source scan sees only our calls; the image also shows what a library pulls in behind them. Before DOC-28 the deleting destructors linked the allocator (488 bytes) through libstdc++'s `operator delete`; the family port now defines both forms itself (`platforms/s32k1xx/port/main/src/cxxRuntime/NoHeapOperatorDelete.cpp`, fault reaction, never returns) |
| `isr-unwrapped`, `isr-vector-table` | SYS-170 (SYS69-S1, finding SFP-F01): in an application with the safety shell (`SafePreIsrHook` defined) every vector from SysTick (15) on points at `DefaultISR`, at one of the safety paths without hook wrapper (`Safe_WDOG_EWM_IRQHandler`, the halt deadman `Safe_LPIT0_Ch1_IRQHandler`) or at a function whose first call (`bl`) is `SafePreIsrHook` and which calls or branches to `SafePostIsrHook` - recognised by its code, not by its name, so an interrupt that preempts the safety cycle never runs with the MPU gate of the protected RAM open. Reserved entries (0) and the S32K14x TRIM word are skipped; bootloader and updater have no safety shell and are not checked |
| `isr-fpu` | SYS-170, review point SYS170-S4 (a): no function reached through `bl`/`b.w` from a handler such a wrapper calls (and from the hooks) holds a VFP/Advanced SIMD instruction (coprocessor 10/11; data marked `$d` skipped). With lazy FP stacking the first FP instruction of an interrupt writes the interrupted task's FP context onto that task's stack - for the safety task the stack the pre hook has just made read-only, a bus fault. Not followed: calls through a register (function pointers, virtual calls) and `DefaultISR` (unexpected interrupt, never returns; OpenBSW's `vIllegalISR` prints through code that uses `d8`/`d9`). Eval board image: 63 functions followed, 0 findings |
| `segment-extra-bytes` (warning) | a `PT_LOAD` segment carries file bytes of no section (ELF header) at an address outside the image's area |

## Use

```sh
.venv/Scripts/python tools/elfcheck --project projects/evalboard_s32k144_demo/project.yaml --build build/evalboard-s32k144-demo
.venv/Scripts/python tools/elfcheck --mcudb mcudb/stm32/STM32F446RE.yaml --flash FLASH --bootloader bootloader.elf
```

Only the flash configuration field (NVM-15), the post-build step of the bootloader (`bootloader/CMakeLists.txt`,
which turns the build red); `--family` ends with exit code 0 for a family without such a field:

```sh
.venv/Scripts/python tools/elfcheck fcf --family s32k1xx --project projects/evalboard_s32k144_demo/project.yaml bootloader.elf bootloader.hex
```

The bootloader linker script (`bootloader/platforms/s32k1xx/linker.ld`) also stops the link if `.FlashConfig` is not
16 bytes, and the application linker template refuses an application at the start of the code flash.

Messages: `error: <file>: <section|symbol|record>: <text> [<code>]`. Exit code 1 with errors, 2 for unusable input;
`--strict` counts warnings as errors, `--no-records` and `--no-rom-list` switch those parts off.

## Tests

`tools/elfcheck/test/test_elfcheck.py`, entries `elfcheck`, `elfcheck-stm32`, `elfcheck-demo`, `elfcheck-bt148` and
`elfcheck-s32k116` in `tools/test/tests.yaml`. The demo images must pass and 29 image mutants (one SYS-170: a vector
around its SAFE_ISR wrapper; four of them
NVM-15: FSEC secured in ELF and hex or in the hex only, mass erase disabled, field missing; three DOC-28: `malloc`
and `operator new` in the application, `_sbrk_r` in the bootloader) and 7 derivative mutants must turn the check
red; `ReaderUnitTest.test_heap_symbols` checks the allocator list without a build. `elfcheck-isr`
(`IsrWrapperUnitTest`, SYS-170) checks the interrupt frame rule, the floating-point rule and the branch and VFP
decoders on synthetic images. `elfcheck-fcf` checks the
configuration field rule without a build (ten counter-checks). S32K116: bootloader and updater of the project plus
an own small application linked with the real linker template (the application of the project does not link yet, PLAT-12).

## Product image without test commands (`product`)

Task SYS-74 (`iso26262-gap.md` task 19, SG6). A build with `-DNX_PRODUCT_BUILD=ON` (`cmake/NxProductBuild.cmake`)
leaves out the console commands that only test a safety mechanism by provoking a fault or its reaction. `product.py`
holds their inventory (`MARKERS`: command, reason, symbols, console texts, test-build option) and reports each one it
still finds in a linked application: a defined symbol, or a console text in the bytes that go into the ECU (sections
with content; the debug information is ignored).

```sh
.venv/Scripts/python tools/elfcheck product --app <app.ecu.elf>                # product image: 0 findings
.venv/Scripts/python tools/elfcheck product --app <app.ecu.elf> --expect-all   # counter check: every command found
```

Tests: `elfcheck-product` (no build: text only in image bytes, mangled symbols, inventory against the test-build
options of the family ports and `NX_TEST_BUILD_OPTIONS`, guards in the sources, built-in faults) and
`target-product` (`test/product_build.py`: demo product image clean, build with every test option finds every
command, product plus test option refused at configure time, standard demo keeps its development commands).

## Bootloader image changed → version changed (`blversion`)

A bootloader in the field is known only by `BL_VERSION_NUMBER` (BL-32). `blversion.py` hashes the flash content of
`bootloader.hex` (first to last written address, gaps 0xFF - no debug information, no paths) and compares it with
the entry of the current number in `bootloader/test/image_version.sha256` (reference preset `evalboard-s32k144-demo`,
one line `<version> <sha256> <gcc version>` per number, append only):

```sh
.venv/Scripts/python tools/elfcheck blversion <bootloader.hex>          # 0 unchanged, 1 changed/entry missing
.venv/Scripts/python tools/elfcheck blversion --add <bootloader.hex>    # append the entry of a new number
```

Exit code 3: the entry was taken with another GCC version (other code, not comparable). `--add` never rewrites an
existing entry - a changed image needs a new number. Test: `bootloader/test/test_bl_image_version.py`.

## RTE timing and interrupt priorities (`timing`)

Proposal F of the tool classification (finding TF7, requirement SSR-081), task PLAT-82. rtegen turns `rte.yaml` and
the BSW task profile into the task list, the FreeRTOS priorities (context numbers of `async/Config.h`) and the periods
and offsets of the runnables, which SM-05 uses as deadlines; bswgen turns the project's interrupt ranks into NVIC
levels. `timing.py` compares the **linked application** with an expectation it derives itself:

| Source | What it gives |
|---|---|
| `project.yaml` | ECU description, board (derivative), `interrupts.max_syscall_level` and `interrupts.ranks` |
| `rte.yaml` | application tasks (period, priority key, runnables in call order with offsets), components |
| `<component>/generated/arxml/*.arxml` | Simulink component: runnables, their symbols, TIMING-EVENT periods, INIT-EVENTs, mode switch events |
| `<component>/swc.yaml` with `implementation: c` | hand-written C component (RTE-20, PLAT-102): name, runnables and `period_ms`; `<Swc>_Init` first, C function `<Swc>_<Runnable>` |
| `ecu/asyncCoreConfiguration/tasks.yaml` | BSW tasks and keys, OS tick, default application task |
| `mcudb` | core and `nvic_priority_bits` |

Rules applied (the documented ones): all tasks sorted by key and numbered 1..N (idle 0, timer N+1); deadline of a
runnable = its period, of a task cycle = the task period; NVIC level = `max_syscall_level` + rank. The expectation of
six profiles (two of them with C components: `test_c_swc`, `test_calib`) is compared with the reviewed reference
output of rtegen (PLAT-78) - two independent paths that agree.

The `swc.yaml` of a C component is read by an **own narrow reader** in `timing.py` (PyYAML, only name, runnables and
periods), not by `rtegen` or `tools/swc/swc_yaml.py`: SSR-081 (b) asks for a parser that uses no generator code, and a
difference between this reader and rtegen's is exactly what the check is there to find (PLAT-102, finding TOOL-93).

| Code | Check (image) |
|---|---|
| `task-count`, `task-name`, `task-priority`, `task-period`, `task-runnables`, `task-hyper` | rows of `rte::TASKS` (context = FreeRTOS priority, period = deadline of a cycle) |
| `runnable-count`, `runnable-name`, `runnable-function`, `runnable-period`, `runnable-offset`, `runnable-task`, `runnable-ticks` | rows of `rte::RUNNABLES` (called function resolved to its symbol, period = SM-05 deadline) |
| `mode-runnables` | rows of `rte::MODE_RUNNABLES` (task of every mode switch runnable) |
| `task-list`, `task-priority`, `task-count`, `task-context-units` | enumerators `TASK_<NAME>` and `ASYNC_CONFIG_TASK_COUNT` of every compilation unit (DWARF, `dwarf.py`): the priority of every task, BSW tasks included; units that disagree point to a stale object |
| `os-tick`, `irq-rank`, `irq-syscall`, `irq-bits` | `ASYNC_CONFIG_TICK_IN_US`, `NX_IRQ_RANK_*`, `NX_IRQ_MAX_SYSCALL_LEVEL`, `NX_NVIC_PRIORITY_BITS` as compiled (`.debug_macro`; the presets of `CMakePresets.json` build with `-g3`) |
| `irq-level`, `irq-level-missing`, `irq-level-extra`, `irq-probe-build` | probe: the generated `interruptPriorities.h` with the platform's `nxInterruptPriority.h`, compiled and linked by `arm-none-eabi-gcc` for the core; the NVIC level of every rank read back with the own ELF reader |

```sh
.venv/Scripts/python tools/elfcheck timing --project projects/evalboard_s32k144_demo/project.yaml --build build/demo
.venv/Scripts/python tools/elfcheck timing --project <project.yaml> --app app.elf --generated <build>/generated/board
.venv/Scripts/python tools/elfcheck timing --project <project.yaml> --rte <rte.yaml> --tasks <tasks.yaml> --app rte.elf
```

Tests: `tools/elfcheck/test/test_timing.py`, entries `elfcheck-timing` (expectation, no compiler),
`elfcheck-timing-host` (own profile `tools/rtegen/test/multitask` built with the target compiler for Cortex-M4 and
M0+; own profile `tools/rtegen/test/c_swc` with the C component `LevelMonSwc` next to the Simulink `DemoSwc`, class
`TimingCSwcHostBuildTest`; interrupt probe for own and open profiles), `elfcheck-timing-demo` and
`elfcheck-timing-bt148` (linked applications) and the firmware of `test/c_swc` (class `TimingCSwcImageTest`, target
build `target-c-swc`, `NX_CSWC_BUILD_DIR`). Image, generat, template and configuration mutants must turn it red;
for the C component also a period that drifts apart between `swc.yaml` and the image, from both sides.

Limits of `timing`: an image without debugging information gets warnings instead of the BSW task and macro checks
(the RTE tables are still checked); the NVIC registers of a running device are a board check (tool-classification.md
TB4); Cortex-R (VIM channels) has no level check yet.

## Limits

- The expectation of the partition itself (where the bootloader ends, where the record lies) is only checked for
  consistency with the derivative (sector boundaries, disjoint areas) and with the project's bootloader size; the
  project decides the partition.
- The key sector (`BL_KEY_ADDR`) is not among the bootloader's layout values and is not checked.
- The check works on the files of the build, not on the flash of a device.
