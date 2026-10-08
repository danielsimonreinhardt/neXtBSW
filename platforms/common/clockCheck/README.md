# Clock start-up check (PLAT-72)

Makes a clock that is not the planned one visible at start-up, in every family the same way.

## Finding

On the Nucleo F446RE (PLAT-15, 2026-09-19) the solder bridge SB50 was open, so the 8 MHz clock of the ST-LINK never
reached the MCU. `HSERDY` never came, the stm32 clock driver gave up after its bounded wait and the core ran on the
16 MHz reset clock instead of 96 MHz - **without any message**. Everything derived from the clock plan was wrong: OS
tick about 167 Hz instead of 1 kHz, UART baud rate divider and CAN bit timing for a bus clock the MCU did not have.

## Parts

| Part | Place | Role |
|---|---|---|
| Contract `clock/nxClockCheck.h` | once per family: `platforms/stm32/bsp/bspClock/include`, `platforms/s32k1xx/bsp/bspClock/include` | result structure and `nxClockCheck()`; the copies are identical (checked by `test/test_clock_check.py`), because a family module must not depend on `platforms/common` (platform rule P2, `doc/architecture/README.md` section 6) |
| Check of the family | `platforms/stm32/bsp/bspClock/src/clockCheck_stm32.cpp`, `platforms/s32k1xx/bsp/bspClock/src/ClockCheck.cpp` | reads the clock select and ready status of the hardware and compares it with the generated clock plan `bsp/clock/boardClock.h` |
| Reporter `nxClockCheckReport` | this directory | one console line at start-up, text of the fault codes |

The check reads the **registers**, not a value the clock driver stored: the driver runs in `boardInit()` before the
RAM initialization, and the start-up code overwrites `.data`/`.bss` afterwards. For the same reason `SystemCoreClock`
of the stm32 family always held the reset clock after start-up; `nxClockCheck()` now sets it to the clock the
hardware really runs at.

## Result

| `fault` | Meaning | stm32 | s32k1xx |
|---|---|---|---|
| `NX_CLOCK_CHECK_OK` | as planned | | |
| `NX_CLOCK_CHECK_OSC_NOT_READY` | planned crystal / external clock (s32k1xx: or SIRC/FIRC) not ready | `RCC_CR.HSERDY` = 0 | `SCG_SIRCCSR.SIRCVLD`, `SCG_FIRCCSR.FIRCVLD` or `SCG_SOSCCSR.SOSCVLD` = 0 |
| `NX_CLOCK_CHECK_PLL_NOT_LOCKED` | planned PLL not locked | `RCC_CR.PLLRDY` = 0 | `SCG_SPLLCSR.SPLLVLD` = 0 |
| `NX_CLOCK_CHECK_SOURCE_MISMATCH` | system clock source or dividers differ from the plan | `RCC_CFGR` HPRE/PPRE1/PPRE2/SW/SWS, `RCC_PLLCFGR` M/N/P/Q/source | `SCG_CSR` SCS/DIVCORE/DIVBUS/DIVSLOW against `_SCG_RCCR_` |

`monitor` = `NX_CLOCK_CHECK_MONITOR_EVENT` when a clock monitor saw a loss: s32k1xx reset reason `RCM_SRS[LOC]` or
`[LOL]` (clock monitors with reset, enabled by the driver) or a pending `SOSCERR`/`SPLLERR`; stm32 a pending
`RCC_CIR.CSSF` or a clock security event the NMI hook `nxClockSecurityNmi()` already cleared (PLAT-85). `actualCoreHz` is derived from the select
status registers and the frequencies of the clock plan, 0 when that is not possible.

`source` (`NX_CLOCK_SOURCE_*`, board finding 2026-10-02) names the clock the core really runs from, read from the same
select status (`SCG_CSR[SCS]`, `RCC_CFGR.SWS`): `PLL` (`pllInput` = reference of the PLL), `EXTERNAL`,
`INTERNAL_FAST` (FIRC / HSI), `INTERNAL_SLOW` (SIRC). It is in every console line, so a fallback from the crystal to an
internal oscillator is visible even when the frequency happens to look right, and a line with `ok` proves the
planned source (the fault check compares the select status with the plan).

Console line, e.g. the finding of PLAT-15 as the host test reproduces it:

```
clock: ok, core 80000000 Hz, source PLL on external oscillator
clock: FAULT oscillator not ready - core 16000000 Hz instead of 96000000 Hz, source internal fast RC (regs 0x00058683 0x00000000 0x24003010)
```

## Behaviour: continue with a report

The clock drivers do **not** stop when a clock does not come; the stm32 driver continues on the reset clock, and the
report makes that visible. Reasons: a stop before the console gives no diagnosis at all (PLAT-15 needed a debugger to
find the cause), and the reaction belongs to the safety layer of the application, which knows what depends on the
clock (CAN bus off or not started, safe state, event, reset). `nxClockCheckReport()` returns 1 for a fault or a
monitor event so that the integration can react.

Differences between the families:

- **stm32** (`clockConfig_stm32.cpp`): bounded wait (`NX_CLOCK_RESET_HZ / 10` polls) for `HSERDY`, `PLLRDY` and the
  switch status, then stays on HSI.
- **stm32**: since PLAT-85 `configurePll()` also enables the clock security system once HSE is ready; the NMI
  handler of the integration calls `nxClockSecurityNmi()` (`clock/nxClockSecurity.h`).
- **s32k1xx** (PLAT-85: own copy of the OpenBSW driver in `platforms/s32k1xx/bsp/bspClock`): bounded waits for SIRC,
  FIRC, SOSC, SPLL and every clock switch; a clock that does not come is neither selected nor monitored, and a
  normal start-up writes exactly what the OpenBSW driver writes. Before PLAT-85 `soscStart()`/`spllStart()` waited
  without a limit, so a missing crystal stopped the start-up in `boardInit()` with the MCU watchdog disabled.

## Use

```cpp
#include "clock/nxClockCheckReport.h"

nxClockCheckResult clock;
if (nxClockCheckReport(&clock) != 0)   // after the console is up; run mode, not while a low power clock is selected
{
    // reaction of the integration
}
```

CMake: `add_subdirectory(platforms/common/clockCheck nxClockCheckReport)` and link the family target
(`nxBspClock` or `bspClock`) to `nxClockCheckReport`. Integrated since PLAT-85: `platforms/s32k1xx/port/main`
(after `staticBsp.init()` and the `RCM::SRS` line) and the stm32 build test.

## Tests

`tools/test -k clock-check`: host tests of both families against register models with the real device headers, three
profiles each (stm32: Nucleo 8 MHz HSE bypass / PLL 96 MHz, 25 MHz crystal / PLL 100 MHz / AHB 2, HSI only; s32k1xx:
eval board SOSC 40 MHz / SPLL, S32K116 without SPLL, S32K14x without crystal), and `test/test_clock_check.py`
(contract copies identical, mutants of driver, checks and reporter detected).
