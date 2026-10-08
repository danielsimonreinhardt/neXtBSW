# stackuse – worst-case stack per execution context

Task SYS-65 (gap analysis `doc/safety/iso26262-gap.md` (not published, available on request) section 6 no. 7, row
6-18): an upper estimate of the stack of every execution context of an image, from the `-fstack-usage` output the
toolchain already emits (`cmake/toolchains/NxArmNoneEabi-gcc.cmake`). The result for the demo image is part of the
software architectural design specification of the safety part
([`doc/safety/sw-architecture.md`](../../doc/safety/sw-architecture.md), section 7).

```sh
python tools/stackuse --build build/evalboard-s32k144-demo \
    --elf build/evalboard-s32k144-demo/ecu/application/RelWithDebInfo/app.ecu.elf \
    --profile tools/stackuse/profiles/evalboard_s32k144_demo.yaml --out report.md
python tools/stackuse ... --strict          # incomplete contexts fail as well
python tools/stackuse ... --json            # machine-readable
python -m unittest tools/stackuse/test/test_stackuse.py
```

`arm-none-eabi-objdump` comes from `NX_ARM_GCC_BIN` or `PATH`. Exit code 0: every context fits its stack and the
nested interrupt bound fits the interrupt stack; 1: something overflows (with `--strict` also: a context is
incomplete or its margin is below `--min-margin`); 2: unusable input. No Qt; standard library plus PyYAML.

## Inputs

| Input | What it gives |
|---|---|
| `.su` files below `--build` | the frame of every compiled function (`<file>:<line>:<col>:<name>\t<bytes>\t<qualifier>`) |
| disassembly of the image (`objdump -d -C --no-show-raw-insn`) | function starts, direct calls (`bl`), tail calls (`b`/`b.w`/`b.n` to the start of another function), indirect calls (`blx rN`, `bx rN` with N ≠ lr), and every stack pointer decrement (`push`, `vpush`, `stmdb sp!`, `sub sp`, pre-indexed stores on sp) |
| profile (YAML) | the execution contexts, their stacks, the resolution of indirect calls, frames of functions nothing else bounds |

## Method

- **Frames.** A function's frame is the larger of (a) the largest `.su` value under its key and (b) the sum of all
  stack pointer decrements in its disassembly. The key is the qualified name without return type, parameters,
  template arguments and clone suffix, because `.su` names (`static void ns::C::f(uint8_t)`, `[with T = ...]`) and
  demangled symbols (`ns::C::f(unsigned char)`, `ns::C<int>::f`) spell the same function differently; merging under
  the key can only raise a frame. Some `.su` names are unusable (GCC writes only `)` for some template instances);
  (b) covers those, and it also covers newlib, libgcc and the assembly handlers, which have no `.su` at all. The
  report states how many functions have both values and how often they agree (demo: 1308 of 1457 equal, 14 with a
  larger disassembly sum). A frame the profile gives (`frames:`) overrides both.
- **Call graph.** Nodes are the exact symbols of the image, so template instances and delegate stubs stay apart.
  A tail call is counted like a call (over-estimates by at most the caller's frame). `ldr pc, [rT, rI, lsl #2]` is a
  switch table, not a call.
- **Contexts.** `depth = context_bytes + dispatch chain + deepest entry`. The dispatch chain is the fixed way from
  the task function to the runnables (task function, event loop, delegate stubs); each step's own frame stays on the
  stack below the next, and each step's direct subtree is a candidate for the deepest point on the way. The entries
  are what the context calls through function pointers (runnables, lifecycle transitions, console commands).
- **Indirect calls.** A reached indirect call without a resolution in the profile makes the context
  **incomplete**; its number is then a lower bound, and the report lists the call sites. Virtual calls are
  recognised (`ldr rV, [rObj]` … `ldr rF, [rV, #off]` … `blx rF`) and labelled with their vtable offset to help the
  reviewer, but not resolved automatically: resolving a slot over all vtables of the image merges unrelated classes
  (tried on the demo: one cycle of 444 functions and a meaningless 14.9 KB). The profile names the targets per
  caller, globally or per context, each with its source.
- **Recursion.** Strongly connected components of the graph. A cycle is an open item unless every member is listed
  in `bounded_recursion` with the number of times it can be on the stack (default 1); the tool then counts each
  member that many times.
- **Interrupt stack.** Handlers of one NVIC level cannot preempt each other; the nested bound is, per level, the
  deepest handler plus one exception frame (`exception_frame_bytes`), summed over all levels. That is an upper bound:
  it assumes every level is active at its deepest point at the same moment.

- **Interrupt stack budget (SYS-97).** A task-kind context with `on_isr_stack: true` runs on the interrupt stack itself (reset
  handler, constructors, `main()` up to the scheduler); every interrupt level can nest on top, so the interrupt stack must hold
  its depth **plus** the nested bound (checked against `isr_stack_bytes`).
- **Vector table coverage (SYS-97).** With `vectors:` the tool reads the vector table and requires every handler to be an
  entry of an interrupt context (or of the start-up context), a named default handler, a weak stub that only branches to one,
  a generated wrapper that calls only the `wrapper_hooks` and a default handler (SYS-170, an unused vector), or a named
  non-interrupt. A handler linked later (pin events, sample group, fast control loop) is **UNCOVERED** until the profile gives
  it a context; `--strict` then fails.
- **Guard below the interrupt stack (SYS-97).** With `isr_stack_guard_bytes` the guard must hold the largest frame on the
  interrupt stack plus the larger of that frame and the exception frame (a function may reserve its frame and write its locals
  later); while a context on the interrupt stack is incomplete, the largest frame of the whole image counts.
- **Interrupt stack and guard as linked (SYS-132).** Sizes are a project value (`memory.application.isr_stack_size` /
  `isr_stack_guard_size`) or the family default; the tool reads them from the image (`__StackTop - __StackLimit`,
  `__StackGuardEnd - __StackGuardStart` via `arm-none-eabi-nm`, or `--image-symbols <nm output>`) and checks budget and guard
  against them. A guard that does not end at `__StackLimit` is unusable input (exit 2); the profile values only apply to an
  image without these symbols.
- **Tables of function pointers (SYS-97).** A target `table:<section>` names every function whose address a section holds
  (`.init_array`).
- **Accepted lower bound (SYS-97).** `lower_bound_accepted: <reason>` lets a task-kind context stay a lower bound under
  `--strict` (never for a safety context or an interrupt level).
- **Floors of the BSW task stacks (PLAT-107).** A context with `bsw_task: <task>` takes its stack from the build
  (`<build>/generated/rte/tasks/BswTasks.h`, or `--bsw-tasks-header`; else the profile's `stack_bytes`) and its worst case is
  compared with the floor (`min_stack_bytes` of `ecu/configuration/taskStacks.yaml`, or `--task-stacks`); a worst case above
  the floor fails the run (exit code 1, also without `--strict`). A task merged into another one has no stack of its own and is
  refused. The report gets a table "Floors of the BSW task stacks" (safety review 2026-10-01, PLAT-26/S1).

What the method does **not** see: stack use through `alloca`/VLA (a register-sized `sub sp` raises an error; the
`.su` qualifier `dynamic` is reported), code reached by a path the profile resolves wrongly (the resolutions are a
review item), and the stack of a context switch inside a critical section in more detail than `context_bytes`.

## Profile

```yaml
name: <image>
exception_frame_bytes: 108      # per nested exception, core-specific (Cortex-M4F with lazy FP stacking: 104 + 4)
isr_stack_bytes: 4096           # MSP
isr_stack_guard_bytes: 1024     # SYS-97: no-write guard directly below the MSP
vectors:                        # SYS-97: every handler must be covered
  section: .interrupts
  skip: {0: initial stack pointer}
  default_handlers: [DefaultISR]
  wrapper_hooks: [SafePreIsrHook, SafePostIsrHook]   # SYS-170: wrapper of an unused vector = hooks + default handler
  not_interrupts: {}            # handler -> reason
safety_contexts: [safety]       # marked in the report; DemoImageTest requires them complete and fitting
frames: {asm_function: {bytes: 12, source: "..."}}
indirect: {caller: [target, ...]}                       # valid in every context
bounded_recursion: [{function: f, times: 2}]
contexts:
  - name: safety
    kind: task                  # or isr (then level: <NVIC level>)
    bsw_task: safety            # PLAT-107, optional: stack from BswTasks.h, worst case against the task floor
    stack_bytes: 2048           # with bsw_task: only without BswTasks.h
    context_bytes: 208          # exception frame + saved context on the task stack
    dispatch: [task_function, [alternative_a, alternative_b], ...]
    entries: [runnable, ...]
    indirect: {caller: [target, ...]}                   # valid in this context only
  - name: main-before-scheduler                         # SYS-97
    kind: task
    on_isr_stack: true                                  # stack_bytes defaults to isr_stack_bytes
    entries: [Reset_Handler]
    indirect: {__libc_init_array: ["table:.init_array"]}
    lower_bound_accepted: <reason>                      # optional
```

`--sections-dump <file>` gives the `objdump -s` output for `--disassembly` runs without a toolchain.

A reference is an exact demangled symbol or a key (it then names every symbol with that key). The profile carries
image facts, not board facts in code: the tool knows no board, no derivative and no project. The demo profile is
[`profiles/evalboard_s32k144_demo.yaml`](profiles/evalboard_s32k144_demo.yaml), its report for the build of this
commit `reports/evalboard_s32k144_demo.md` (not published, available on request).

## Tests

`test/test_stackuse.py` with its own profile `test/profile_test.yaml` and a made-up image (tasks, handlers on three levels,
dispatch chain, resolved and open indirect calls, recursion, tail call, switch table); deliberate mutations of the tool make
tests fail. `DemoImageTest` runs the demo profile against a real build when one exists (safety contexts complete and fitting,
every profile reference names a symbol, every interrupt level complete and every vector covered, MSP and guard sizes equal
the linker symbols); `IsrStackAnalysisTest` (SYS-97) and `TaskFloorTest` (PLAT-107) cover the additions. The CI step
`stackuse --strict` on the demo image is the entry `stackuse-demo` (group target).
