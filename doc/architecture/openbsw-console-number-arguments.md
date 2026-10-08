# OpenBSW console commands with a number argument - audit (PLAT-110)

Safety review 2026-10-01, decision SR-150-1 b: after the finding of SYS-150 (`out set 259 1` wrote output 3,
because `bios::OutputTester` casts the number to the one-byte `Output::OutputId` before its range check), every
console command of OpenBSW that takes a number was checked for the same class of fault. Base: OpenBSW `3b6e702`
(submodule `external/openbsw`) and the neXtBSW copies of OpenBSW commands in `ecu/consoleCommands`.

What was looked for, per number argument:

- **A - alias by narrowing:** the number is checked after a cast to a narrower type (enum, `uint8_t`, `uint16_t`),
  or passed to a narrower parameter after a check on another value - so `n + 2^k` reaches `n` past the check.
- **B - unchecked scan result:** `CommandContext::scanIntToken()` returns 0 for a missing or invalid token and
  only marks the context; a command that acts without looking at the result writes 0 (or element 0).
- **C - scanner wrap:** `scanIntToken<T>()` itself has no overflow detection (wraps modulo `2^bits(T)`, accepts a
  sign for unsigned `T`). Harmless as long as the value that is checked is the value that is used - only a
  spelling alias, no bypass - but worth an error.

## Result per tester

| Command (class, file) | In a neXtBSW image | Number arguments | Finding | Action |
|---|---|---|---|---|
| `out set`, `out get` (`bios::OutputTester`, `libs/bsp/bspOutputManager`) | yes, every project | output (`uint32_t`) | **A**: check after the cast to the one-byte `OutputId` - `set 259 1` writes output 3, `get 259` reads it. `get` also prints and reads when the check failed | SYS-150: `systems::SafeOffOutputTester` refuses every number `>= TOTAL_NUMBER_OF_OUTPUTS` (and `set` of safe_off outputs). Upstream: package 13 |
| `pwm set`, `pwm setPeriod` (`bios::OutputPwmTester`, `libs/bsp/bspOutputPwm`) | yes, every project | channel, duty, period (`uint32_t`) | **A** in `setPeriod`: "wrong channel" is printed but `OutputPwm::setPeriod()` is still called with the channel narrowed to `uint16_t` (65536 + k is k). **B** in `set`: a missing/invalid duty writes duty 0 (`pwm set 1` unmasks the complementary pair MOT1_U at 0 % - low side on), an invalid channel writes channel 0 (the static dummy channel here) | PLAT-110: `systems::CheckedOutputPwmTester` runs `set`/`setPeriod` only for a complete valid line and a channel in range (host test `checked-pwm-tester`). Effect of A in neXtBSW today: none (`ComplementaryPwm::setPeriod()` is `BSP_NOT_SUPPORTED`). Upstream: package 13 |
| `in get` (`bios::DigitalInputTester`, `libs/bsp/bspInputManager`) | yes, every project | input (`uint16_t`) | **C** only: scanned as `uint16_t`, so 65536 + n reads input n; checked and used as the same value, read only | none (read only, no bypass) |
| `adc get <start> [<stop>]` (`bios::AnalogTester`, `platforms/s32k1xx/bsp/bspAdc`) | yes, every project | channels (`uint32_t`) | none: both numbers checked as `uint32_t` before the narrowing to `uint16_t`, `checkEol()` before acting | none |
| `lc level <n>` (`lifecycle::LifecycleControlCommand`, copy in `ecu/consoleCommands`) | development build only (`NX_PRODUCT_BUILD` removes `level`) | level (`uint8_t`) | **B**: `lc level`, `lc level x` scan as 0 and run `transitionToLevel(0)` - the full lifecycle shut-down, which cancels the safety cycle and disables the MCU watchdog without the safe state (`doc/safety/coexistence.md` CX-2) - while the console answers "error". **C**: `lc level 256` is level 0 | open: fix in `ecu/consoleCommands` (not in the paths of PLAT-110) - `context.checkEol()` and a check of the scan result before the transition. Mentioned in the upstream issue |
| `logger level …` (`DefaultLoggerCommand`, `libs/bsw/logger`) | yes | none (names) | - | none |
| `stats`, `safety …` (`StatisticsCommand`, `SafetyCommand`, copies in `ecu/consoleCommands`) | yes (`safety` not in the product build) | none | - | none |
| `can send <id> <bytes>` (`CanCommand`, `executables/referenceApp/consoleCommands`) | **no** (neXtBSW has its own `can` command in `CanSystem.cpp`, which parses its tokens itself) | id (`uint32_t`), bytes (`uint8_t`) | **B**/**C** upstream: id not checked, bytes wrap at 256, invalid tokens send 0 | none here; mentioned in the upstream issue |
| `phy get <reg>` (`Tja1101Tester`, `platforms/s32k1xx/bsp/bspTja1101`) | no | register (`uint8_t`) | **C** (read only) | none here; mentioned upstream |
| `storage write/fill/read` (`StorageTester`, `libs/bsw/storage`) | no | id, value (`uint8_t`), size | **B**: an invalid id scans as 0 and is written | none here; mentioned upstream |

Not part of the audit but checked on the way, because the same scanner is used: the number arguments of the
neXtBSW commands of the family port (`gd`, `sbc`, `bcc` read/write, `sbc failstart`, `can log`, `dflash`, `i2c`,
`hardfault`) check the `uint32_t` value before any narrowing and end with `checkEol()` - no finding of class A or
B. Two notes outside the family port: `dem setting <0|1>` (`ecu/application/src/systems/RteSystem.cpp`) scans
`uint8_t`, so `dem setting 256` switches the DTC setting off (class C); `dem clear <group>` passes the `uint32_t`
on unchecked to `DemMemory::clear()` (which answers "no DTC" for an unknown group).

## Contribution to OpenBSW

Prepared as package 13 in [`doc/openbsw-beitraege/13-console-tester-ranges`](../openbsw-beitraege/13-console-tester-ranges/)
(issue text, `fix.patch` against `3b6e702` for `OutputTester.cpp` and `OutputPwmTester.cpp`, PR text, tests);
submission by the user (decision 2026-09-17, ARCH-30 F4). Once accepted upstream and the submodule is updated,
`SafeOffOutputTester` keeps only its safe_off rule and `CheckedOutputPwmTester` can be removed.
