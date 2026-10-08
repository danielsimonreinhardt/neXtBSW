# S32K148 build-test board (S32K148 LQFP176)

**Build-only profile: no flashing, no debugging, no console, no CAN access.** There is no board behind this profile
that the project uses; it exists only to be configured, generated and linked.

Second board profile, created with ARCH-09 (user decision 2026-09-15) as a permanent test of board neutrality. It
describes the public NXP evaluation board S32K148EVB-Q176 as far as the OpenBSW reference port proves it, with an
S32K148 in LQFP176. Hardware description: [`board.yaml`](board.yaml). Project:
[`buildtest_s32k148_min`](../../projects/buildtest_s32k148_min).

Purpose: every change to reusable code must keep building for this profile. Where that fails, or where reusable code
would have to change for it, that is a **finding** with a follow-up task — never a board-specific workaround (guiding
principle in [`CLAUDE.md`](../../CLAUDE.md), review questions in
[`doc/architecture/README.md`](../../doc/architecture/README.md), section 11).

## Status

| Item | State | Confirmed by | Date |
|---|---|---|---|
| Configuration (`nxconfig check`) | ok, 0 warnings | host run | 2026-09-18 |
| Target build (`buildtest-s32k148-min`) | builds without warnings up to `app.ecu.elf` | host run of the coordinator at the merge of SYS-13 | 2026-09-18 |
| Bring-up on hardware | none — build-only profile | — | — |

## What differs from the S32K144 evaluation board

| Property | S32K144 evaluation board | this profile |
|---|---|---|
| derivative, package | S32K144, LQFP100 | **S32K148, LQFP176** (1.5 MB program flash, 252 KB RAM, 8 FTM, 3 FlexCAN) |
| crystal | 40 MHz | **8 MHz** (same 80 MHz system clock, other PLL factors, other CAN bit timing) |
| supply, transceiver, watchdog | system basis chip UJA1131 (CAN0, V2, external watchdog) | **no system basis chip**: passive transceivers (`canPhyPassive`), the MCU's own watchdog, role `power_supply` unassigned |
| CAN | CAN0 (FlexCAN0) used, CAN1 (FlexCAN2) described | **two buses in use**: CAN0 (FlexCAN0, 500 kbit/s) and CAN1 (FlexCAN2, 250 kbit/s) |
| console | LPUART2 (PTD6/PTD7) | LPUART1 (PTC6/PTC7), the OpenSDA virtual COM port of the EVB |
| pins | all present in LQFP100 | LEDs and CAN1 on **PTE21…PTE25**, which exist only in LQFP176 |
| parts | SBC, gate driver, cell controller, KL30B switch | none; LEDs, a button, an analogue input |

## Fitted parts

| Reference | Part | Function | `board.yaml` entry | Source |
|---|---|---|---|---|
| — | TJA1051T/3 | transceiver of CAN0, mode fixed to normal | `devices: CAN0_PHY` | assumed for the build test |
| — | TJA1051T/3 | transceiver of CAN1 (second bus) | `devices: CAN1_PHY` | assumed for the build test |

Whether the S32K148EVB really carries these transceivers is not checked (*unconfirmed*); for the build test only
"passive transceiver" matters.

## Where the values come from

The template is the OpenBSW reference port of the S32K148 EVB
(`external/openbsw/executables/referenceApp/platforms/s32k148evb`). It proves the crystal (8 MHz), CAN0 (PTE4/PTE5),
the console (LPUART1, PTC6/PTC7), the button (PTC12), the RGB LED (PTE21/PTE22/PTE23), the safety test pin (PTD31)
and the analogue input (ADC0 SE28); every pin function is also checked against
[`mcudb/s32k1xx/S32K148_LQFP176.pinmux.yaml`](../../mcudb/s32k1xx/S32K148_LQFP176.pinmux.yaml). The port also gives the
levels of the button (active high, no internal pull) and of the red LED (active high) and the scaling of the
analogue input (5 V full scale, factor 1). What the port does not prove - the levels of the green and blue LED, which
it drives as PWM, and the `MOT1` group below - is marked `[SYN]` in `board.yaml`: a synthetic value of this build-only
profile, not a statement about hardware (TOOL-243; until then `verify: true`). Pad numbers are left out.

Two entries are there for the build test, not because of the EVB:

- CAN1 on FlexCAN2 (PTE24/PTE25), so that a second bus on a second FlexCAN instance is built.
- A complementary PWM group `MOT1` on FTM0 with a **synthetic** `power_stage` example (IO-24), although the EVB has
  no power stage. PWM groups are optional since ARCH-17; the group stays so that the complementary PWM path is built
  on a second derivative. None of its values is a measurement.

Until ARCH-15 the console had to be LPUART2 (PTD6/PTD7), because the board port fixed that instance (finding
ARCH-09-B2); since then the profile names the real EVB console.

## Safety rules

State of these rules: 2026-09-18.

- **Build only.** No flashing, no debugging, no console and no CAN access with this profile, because no board exists
  for it in this project and its values are partly chosen for the build test, not measured. `board.debug` names a
  J-Link only so that CMake generates the flash targets; they are not released.
- Whoever uses the profile for a real S32K148EVB writes a new README for that board first, with its own safety rules
  and the `[SYN]` values replaced by values from the EVB documentation.

## Check and build

```sh
.venv/Scripts/python tools/nxconfig check projects/buildtest_s32k148_min/project.yaml
cmake --preset buildtest-s32k148-min
cmake --build --preset buildtest-s32k148-min
```

In an agent worktree use a short build folder instead (Windows path limit, PLAT-06), for example:

```sh
cmake -S . -B %TEMP%/s32k/bt148 -G "Ninja Multi-Config" -DNX_PROJECT=projects/buildtest_s32k148_min -DPython3_EXECUTABLE=<.venv python>
cmake --build %TEMP%/s32k/bt148 --config RelWithDebInfo
```

## Sources

- OpenBSW reference port `external/openbsw/executables/referenceApp/platforms/s32k148evb`: `boardClock.h`
  (`CRYSTAL_SPEED`), `ioConfiguration.h` (pins), `analogInputConfiguration.h` (potentiometer)
- Pin functions: [`mcudb/s32k1xx/S32K148_LQFP176.pinmux.yaml`](../../mcudb/s32k1xx/S32K148_LQFP176.pinmux.yaml)
- NXP S32K148EVB-Q176 user manual (public; not used for any value yet)
