# Console context (`consoleContext`)

Header-only library for the serial console of the ECU. It has two parts:

1. **Commands in the console context** (SYS-13, general form of COM-24): a console command parses its arguments,
   formats and prints in the lowest task (the console context), and only the short access to a system's state runs
   in the task that owns that state (`ContextCall`).
2. **An input line that shares the terminal with the log output** (SYS-15): typed characters no longer end up
   inside log lines (`InputLine`, `InterleavedConsoleInput`, `IOutputInterleave`).

It is a catalogue entry only (`kind: library`): `tools/nxconfig` and `tools/bswgen` assign no board device, take no
project values and generate nothing for it.

**State:** verification `hardware` for the commands in the console context; the input line only in host tests (see
[Tests](#tests)).

## Why

Console output goes to the UART synchronously, byte by byte (86.8 us per character at 115200 Bd, header comment
of `ContextCall.h`). A command registered with the context of its system lent that task to the UART: `can status`
held task `can` for 40-50 ms, `rte json` held the runnables of an application task for several cycles, `sbc dump`
held the task that triggers the SBC watchdog. Splitting the command removes that: the owner is busy only for the
snapshot or change itself, independent of the length of the output.

## Interface

| Header | Content |
|---|---|
| `console/ContextCall.h` | `console::ContextCall<RunnableBase, ContextT, Execute>`: `run(owner, work)`, type-erased `run(owner, work, thunk)`, `callerContext()`, `queuedCalls()`. Hardware and OS independent; the runnable type, context type and queue function are template parameters |
| `console/ConsoleContext.h` | binding to OpenBSW `::async`: `CONSOLE_CONTEXT` (= `TASK_IDLE`), `AsyncContextCall`, the one instance `consoleContextCall`, `runInOwnerContext(owner, work)` |
| `console/IOutputInterleave.h` | `beginOutput()` / `outputIdle()`, called by a writer of whole lines (the logger) around its output; `consoleOutputInterleave()` is defined by the application |
| `console/InputLine.h` | `console::InputLine`: line editor (`onByte`, `startCommand`, `prompt`, `line`, `isShown`), implements `IOutputInterleave`; `CAPACITY` = 128 |
| `console/InterleavedConsoleInput.h` | drop-in for OpenBSW `console::StdioConsoleInput` (same constructor, `init`, `shutdown`, `run`), line editing through `InputLine` |

CMake target `consoleContext` (INTERFACE). Users in this repository include the systems of the s32k1xx family port
(`CanSystem`, `SbcSystem`, `GateDriverSystem`, `CellMonitorSystem`), the application systems (`RteSystem`,
`EcuStateSystem`, `SysAdminSystem`), `ecu/application/src/console/console.cpp` and
`ecu/application/src/logger/logger.cpp`; [`canSystem`](../../canSystem/doc/canSystem.md) keeps the alias
`can::ContextCall`.

## Configuration

None - no `board.yaml` or `project.yaml` key, no module parameters
([modules reference](../../../../doc/reference/modules.md#m-consolecontext)). The console commands themselves are
listed in the [console reference](../../../../doc/reference/console.md).

## Behaviour

### ContextCall

- `run(owner, work)` runs `work` in the owner's context and returns when it is done. If the owner is the caller's
  own context, the work runs inline.
- Otherwise the work is queued in the owner's context (`Execute`) and the caller spins until it is done. Because the
  owner has the higher priority, it preempts the caller as soon as the work is queued.
- A second call while one is in progress returns `false` without running anything.
- Preconditions stated in the header: the caller runs below the owner (a caller above the owner would wait for
  ever); one call at a time; no timeout; the work does not print.

### ConsoleContext (target binding)

- Every console command of a neXtBSW system is registered with `CONSOLE_CONTEXT` (`TASK_IDLE`), where console input
  and logger output run as well, so command output, prompt and log lines come from one task.
- `runInOwnerContext` runs the work inline if the owner is `CONTEXT_INVALID`, if the caller is not the console
  context, or if a call is in progress; otherwise through `consoleContextCall`. It is not inlined (comment: inlining
  cost 4.3 KB of flash in the demo application).

### Input line

- Keys as in OpenBSW `StdioConsoleInput`: printable 0x20..0x7E, backspace 0x08/0x7F, ESC, CR/LF, prompt `"> "`.
  Characters beyond 128 are dropped without echo.
- `beginOutput()` before a log line: if the line is shown, VT100 erase line (`ESC [ 2 K`) and CR, 5 characters.
- `outputIdle()` when the logger has nothing more: prompt and typed text are drawn again once, if there is typed
  text. An empty prompt is not redrawn, so without typing the log output is unchanged byte for byte.
- A key while the line is not shown is stored and drawn only when the log is not busy. Enter always draws the line
  first, so the command stays readable above its answer.
- While a command runs the line is inactive and the console does not read; type-ahead stays in the UART receive
  buffer.
- Not thread safe: every call comes from the context of the console input.

## Limits and known gaps

- `ContextCall` has no timeout by design; a wrong priority order (caller above owner) would block the caller for
  ever. `runInOwnerContext` avoids this only for callers outside the console context.
- The input line needs a VT100-capable terminal (the log output already uses VT100 colours).
- The interleaved input line has not been checked on a board (verification note).

## Tests

Host-Tests, Testnamen und Verifikationsstand: [Modulkatalog](../../../../doc/reference/modules.md#m-consolecontext).
