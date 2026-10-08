<!-- SPDX-License-Identifier: Apache-2.0 -->

# neXtBSW and Eclipse OpenBSW

Status: 2026-09-17 (**DOC-07**), checked against the repository at this commit and against the submodule pointer
`external/openbsw` = `3b6e702`.

Eclipse OpenBSW is a project of the Eclipse Foundation, not of neXtBSW. This page describes how neXtBSW *uses* it.
Nothing here is a statement by, about or on behalf of the OpenBSW project, and neXtBSW makes no claim of being
endorsed by, certified against or compatible with any particular OpenBSW release. Names and marks belong to their
owners.

Binding target picture: [`README.md`](README.md), section 2 (what OpenBSW delivers and which gaps neXtBSW closes),
section 6 (the platform rule P1–P6) and decision D9. The stocktaking of every place where neXtBSW deviates today is
[`openbsw-naehe.md`](openbsw-naehe.md) — this page is the reading version of both.

## 1. Why OpenBSW at all

OpenBSW brings a working, hardware-near basic software in C++17 with FreeRTOS, reference ports for two MCU families,
and middleware that is genuinely hardware independent. That is the expensive half of a BSW and neXtBSW has no reason
to write it again. What OpenBSW does not bring is a configuration model: every value lives in a hand-written header,
and integrating a board means copying `main.cpp`, `StaticBsp`, the CAN system, the ISRs, the linker script and the
start-up file. That copying is exactly what neXtBSW replaces.

So the division of labour is: **OpenBSW supplies drivers, middleware and structure; neXtBSW supplies the contracts,
the configuration model, the generators and everything above them.**

## 2. What neXtBSW takes

Used unchanged from the submodule (`external/openbsw`, added in `CMakeLists.txt:397-408`):

| Area | What | Where |
|---|---|---|
| MCU platforms | `s32k1xx` (reference S32K148-EVB), `stm32` (F413ZH bxCAN, G474RE FDCAN), `posix` | `external/openbsw/platforms/` |
| RTOS integration | FreeRTOS ports, `async`, `lifecycle` | `external/openbsw/platforms/<family>/3rdparty`, `libs/bsw/async`, `libs/bsw/lifecycle` |
| CAN stack | `cpp2can` (`ICanTransceiver`, `CanPhy`), `docan` (ISO-TP), `transport` | `external/openbsw/libs/bsw/` |
| Diagnostics | `uds` — sessions, services, `DiagJob`, routing | `external/openbsw/libs/bsw/uds`, used by `ecu/uds` |
| Infrastructure | `logger`, console, `time`, `util`, `storage`, ETL profile, safety libraries | `external/openbsw/libs/` |
| BSP interfaces | `CanPhy`, `SystemTimer`, `IFlashDriver`, `IEepromDriver`, `IEcuPowerStateController`, `UartConcept`, `DigitalInput`/`Output`/`OutputPwm`, `charInputOutput` | `external/openbsw/libs/bsp/` |

The application `ecu/` itself started as a copy of `executables/referenceApp` — OpenBSW intends the reference app to
be copied — with Ethernet/lwIP, SOME/IP, DoIP, ThreadX, Rust and the middleware demo left out.

## 3. What neXtBSW adds, and why

The gaps in OpenBSW and what neXtBSW adds for each (no neutral SPI/ADC contract, no device drivers above the MCU, no
configuration description or generators, integration copied per board, no RTE, no bootloader, priorities written as
numbers) are the table in [`README.md`](README.md) section 2; the places in the repository are `libs/bsp/bspSpi`,
`libs/bsp/bspAdcAccess`, `libs/bsw/*`, `mcudb/`, `boards/`, `projects/`, `tools/{nxconfig,bswgen,rtegen,dbcgen}`,
`bootloader/` and `platforms/common/{interruptPriority,cortex-m,cortex-r}`.

The deliberate choice: the generated headers **carry the OpenBSW file names** (`bsp/io/io/ioConfiguration.h`,
`bsp/clock/boardClock.h`, `bsp/can/canBitTimingConfiguration.h` and around thirty more), so the OpenBSW drivers that
include them stay untouched. neXtBSW changes where the values come from, not who reads them.

## 4. How the submodule is embedded — and why it stays unchanged

`.gitmodules` points `external/openbsw` at `https://github.com/eclipse-openbsw/openbsw.git`; the checked-out pointer
is `3b6e70210034b243dfc26d8d8c253f3be5ee382d`.

The embedding is deliberately shallow:

1. The repository root is its own top-level CMake project. `CMakeLists.txt:118` sets `OPENBSW_PLATFORM` to the family
   that `nxconfig` derived from the project, which is the same hook OpenBSW itself uses.
2. `CMakeLists.txt:102-110` prefers `platforms/<family>/CMakeLists.txt` when it exists and falls back to
   `external/openbsw/platforms/<family>` otherwise. For `s32k1xx`, the own file does
   `add_subdirectory("${OPENBSW_DIR}/platforms/s32k1xx" openbsw/platforms/s32k1xx …)` — the OpenBSW platform is used
   as-is and into the same binary directory, so an existing build sees no difference.
3. OpenBSW's `libs` are added wholesale (`CMakeLists.txt:408`), with `EXCLUDE_FROM_ALL`: modules are offers, and only
   what a target links gets built.
4. Only a handful of OpenBSW targets are touched from outside, all of them listed in
   [`openbsw-naehe.md`](openbsw-naehe.md) section 2: `bspMcu` include directories (`CMakeLists.txt:419-439`, because
   OpenBSW resolves CMSIS through `CMAKE_SOURCE_DIR`, which is wrong as soon as OpenBSW is a subproject), the ETL
   profile ordering (`CMakeLists.txt:441`), the FreeRTOS port supplied under OpenBSW's expected target name
   (`add_library(freeRtosPort ALIAS nxFreeRtosPort)`, `platforms/common/3rdparty/freertosPort`), and the three
   derivative workarounds in section 6 below.

**The submodule is never edited.** This is a project rule in `CLAUDE.md` ("Das Submodul `external/openbsw` wird nie
geändert") and decision D9 in [`README.md`](README.md) section 10: submodule unchanged, neutral contracts offered
upstream later, no fork. The practical reasons:

- A fork would have to be maintained against every upstream change, and the changes neXtBSW wants are mostly bug
  fixes that belong upstream anyway.
- Everything neXtBSW needs can be reached without editing: a generated header under the OpenBSW name, a forced
  include, a replaced source file, an overridden include path — each visible in one CMake file rather than buried in
  a diff against a third-party tree.
- Licensing: `ecu/` contains files derived from OpenBSW under Apache-2.0 with the copyright headers preserved
  (`README.md`, "Lizenzen"). Keeping the origin clean matters for the planned open-source release
  (`lizenz-und-release.md` (not published, available on request)).

Because copies do drift, the copies are tracked rather than trusted: `tools/openbswsync` with
`tools/openbswsync/copies.yaml` records for every copied file whether it is `identical`, `modified` (with a task ID
and a reason, both mandatory) or `new`, plus a hash of the upstream original. Today the list covers two roots —
`ecu/` against `executables/referenceApp` (34 identical, 11 modified, 9 new) and `platforms/s32k1xx/port` against
`executables/referenceApp/platforms/s32k148evb` (21 identical, 39 modified, 7 new). A submodule update that changes an
original turns the check red until the change has been reviewed and accepted
(`python -m tools.openbswsync check` / `report` / `accept <path>`).

## 5. The platform rule: staying close on purpose

Until 2026-09-17 neXtBSW was drifting: family-independent things were collected under `platforms/common`, which
OpenBSW does not have at all. The user decided to stay close instead, and the rule is now binding
([`README.md`](README.md) section 6, P1–P6, decision D15):

- **P1/P2** — every family is an OpenBSW platform: `platforms/<family>` has the shape of
  `external/openbsw/platforms/{s32k1xx,stm32}` and, together with `libs/`, is self-contained. Duplication between
  families is accepted; OpenBSW duplicates too (`freertos_cm4_sysTick` and `hardFaultHandler.s` exist once per
  platform there).
- **P3** — `platforms/common` may only hold parts for which OpenBSW has no counterpart, tested per directory without
  judgement calls; every exception needs an entry in the P4 list.
- **P5** — the hand-written family port belongs on application level, `ecu/platforms/<family>`, mirroring
  `referenceApp/platforms/<board>`.

Today's state against that rule: `platforms/common` still contains `cmake`, `3rdparty`, `cortex-m`, `cortex-r`,
`interruptPriority` and `test`, each covered by a P4 entry; the family port is still at `platforms/s32k1xx/port`
(the move is PLAT-54, open); `platforms/rm4x` contains only `3rdparty/freertos_crx`, which is already at the right
place. The rule applies to every new file from now on even where the existing state does not yet match.

## 6. Own workarounds for OpenBSW defects — and when they go

Each of these is a **finding for OpenBSW, not a design decision**, and each is meant to disappear once the fix is
upstream. They are switched by a derivative fact, never by a board name, and are no-ops on the S32K14x the reference
port covers.

In `platforms/s32k1xx/CMakeLists.txt` (its own comment says exactly this):

| # | Problem | Workaround | Goes away when |
|---|---|---|---|
| 1 | `bspCore/src/cache/cache.cpp` writes `LMEM->PCCCR`; a derivative without that cache (S32K11x) has no LMEM block, so the file does not compile | the translation unit is replaced by `bsp/bspCore/src/cache/cacheUnavailable.cpp` when `lmem_cache` is not in `NX_FEATURES` | the driver is guarded upstream |
| 2 | `bspClock/src/clockConfig.cpp`: `pllOff()` calls `isSpllSysClk()`/`spllStop()` outside the `PLL_AVAILABLE` guard, so a derivative without SPLL does not compile | forced include `bsp/bspClock/include/clock/nxSpllUnavailable.h`, plus `-Wno-unused-function` for that one translation unit | the guard is fixed upstream |
| 3 | `bspInterruptsImpl`: `SYS_GetActive()` forwards to `NVIC_GetActive()`, which ARMv6-M does not have | forced include `bsp/bspInterruptsImpl/include/interrupts/nxNvicGetActiveUnavailable.h` on `armv6m` | ARMv6-M is handled upstream |

In the family port, each a corrected copy of an OpenBSW file:

| Where | Problem upstream | neXtBSW today |
|---|---|---|
| `platforms/s32k1xx/port/safety/safeMemory/include/safeMemory/mpu.h` | 16 regions hard-coded, `isGlobalEnabled()` compares the whole `CESR` against `0x201`, `slot <=` reaches one region too far (caused a BusFault on the S32K144, Milestone 1) | region count from the generated `NX_MPU_REGIONS`, `static_assert` that the generated NRGD code matches, `slot <` |
| `platforms/s32k1xx/port/main/src/systems/CanSystem.*` | `FlexCANDevice::getBusOffState()` applies the **setter** macro `FLEXCAN_ESR1_FLTCONF` to `ESR1`, so the bus reads as permanently bus-off (COM-12) | `BusStateTransceiver` derives the state itself; multi-bus support is neXtBSW's own and stays |
| `platforms/s32k1xx/port/main/src/os/isr/isr_sys.cpp` | `HardFault_Handler` calls `printf` before branching, which destroys `EXC_RETURN` in LR and moves MSP (PLAT-20) | `__attribute__((naked))` with a bare branch to `customHardFaultHandler` |
| `platforms/s32k1xx/hardFaultHandler` | dump address hard-coded as `.equ 0x2000EC00`, outside the S32K144's SRAM | dump location from linker symbols, stack-pointer check, FP frame, reader after reset |
| `platforms/s32k1xx/port/safety/safeSystem/src/safeSystem/SafeSystem.cpp` | ADC reference fixed at 4800–5200 mV; the eval board's LM4132 delivers 4096 mV | `VREFH_NOMINAL_MV` from the project's configuration |
| `platforms/s32k1xx/port/bspConfiguration/src/bsp/SystemTimer/SystemTimer.cpp` | `updateTicks` discards the division remainder of the DWT conversion (measured −166 ppm at the board, ARCH-14) | remainder kept, clock switch without losing time |
| `platforms/s32k1xx/port/main/src/bsp/startUp.S` | no `.FlashConfig` section at 0x400; in erased flash that means FSEC = secured | section filled with the NXP default values |

Two things follow. First, every one of these rows is a reason to contribute rather than to fork. Second, until the
fix lands upstream these files are copies that can drift — which is what `tools/openbswsync` watches.

## 7. How work flows back

Status: **planned (after R1)**; nothing has been submitted upstream yet. Decided by the user on 2026-09-17 (ARCH-30
F4): **agents prepare, the user submits**, because contributions need the Eclipse Contributor Agreement signed with the
same e-mail address that Git uses. The prepared packages (`issue.md` in the OpenBSW bug-report template, a minimal
`fix.patch` against upstream `main`, `pr.md`, `tests.md` with the counter-check, `einreichen.md`) live in
[`doc/openbsw-beitraege/`](../openbsw-beitraege/README.md), one folder per package. The first five (PLAT-53):

| Package | Subject |
|---|---|
| `01-mpu-region-count` | region count per derivative, `isGlobalEnabled()` on `CESR[VLD]` only, `slot <` |
| `02-flexcan-bus-state` | bus state and error counters by field extraction instead of the setter macro |
| `03-hard-fault-handler` | `printf` removed before the branch (naked), dump location from `__NOINIT_DATA` |
| `04-cmsis-include-path` | `bspMcu` CMSIS path no longer through `CMAKE_SOURCE_DIR` |
| `05-mismatched-tag` | `DiagnosisConfiguration` forward-declared as `struct` (`-Wmismatched-tags`) |

Further packages (PLAT-60: DWT remainder, SPLL / `NVIC_GetActive` / LMEM, configurable ADC reference, `.FlashConfig`,
`core_cm0plus.h`, monitor context, SPI and ADC contracts) and the S32K116 offer (PLAT-57, subject to the licence review)
follow the same pattern. Once a package is accepted upstream, the workaround or copy is deleted and the OpenBSW file
used unchanged, with a byte comparison of the eval-board image or a justified difference
([`openbsw-naehe.md`](openbsw-naehe.md) section 4).

## 8. Open points

- The submodule pointer is `3b6e702`, while [`openbsw-naehe.md`](openbsw-naehe.md) still describes the state at
  `00e3cab`. Its comparison counts and its "upstream unchanged" checks date from 2026-09-17 against `main` and should
  be re-read as historical rather than current.
- ThreadX and Ethernet/lwIP were left out of `ecu/`. The user decided (ARCH-30 F7) that the way back stays open with
  at least one build test per option in CI; that task (PLAT-56) is open, so today the path is untested.
- `platforms/rm4x` exists as a directory but is not yet a platform in the sense of P1: only the FreeRTOS CRx port is
  there. Its acceptance criterion is that OpenBSW's own `referenceApp`/`unitTest` builds for RM46.
