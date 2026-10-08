# neXtBSW — coexistence of ASIL B and QM software in one image (SYS-61)

| | |
|---|---|
| Issue | 1, 2026-09-28 |
| Task | SYS-61 — gap analysis `iso26262-gap.md` (not published, available on request) section 6 no. 2; rows 6-13, 6-14, 9-02 |
| Decision | user 2026-09-22, decision board **iso-q4**: show coexistence (variant b) — QM parts keep QM with a freedom-from-interference argument per module; the separating element (RTOS, MPU code) is ASIL B |
| Status | draft for review; written by an agent on the user's behalf (author side, safety plan section 2) |
| Corrections | Korrektur nach Safety-Review 2026-10-02 S171-4, Freigabe unveraendert (the release of 2026-10-02 stands): section 4 and CX-6 say that since SYS-97 all 8 MPU descriptors of S32K144 are used, with the consequence for SYS-21; AoU-41 added (S171-1) |
| Built against | branch `dev` at `65f3dc9`, demo project `projects/evalboard_s32k144_demo`, preset `evalboard-s32k144-demo` (RelWithDebInfo), xPack GCC 11.3.1, linker map `application.map` of that build |
| Verification | mock/host: consistency test [`test/test_coexistence.py`](test/test_coexistence.py) against this document, the linker map of the demo build, `tools/openbswsync/copies.yaml`, `doc/todo.yaml` and the tree; **nothing on the board** |

Baseline: v0.2.0

**What this document is.** The coexistence decision of the safety plan (activity P9-02, P6-06) worked out for every
module in the demo image: which software is ASIL B, which stays QM, and for each QM module the argument per kind of
interference that it cannot violate an ASIL B requirement — or the finding that says why the argument does not hold
yet and which task closes it. It is the work product `doc/safety/coexistence.md` named by the safety plan (P9-02,
CM-09) and the safety case (EW-20).

**Licence rule.** ISO 26262:2018 is cited by **part and clause only** (for example "6-7.4.8"), requirements are
restated in our own words; no text, table or figure of the standard is reproduced.

**What it is not.** Not the partitioning design (SYS-21 builds it; section 7 is the hand-over), not the qualification
of pre-existing software (SYS-62), not a statement that the image is free from interference today.

---

## 0. Result in one page

| Question | Answer |
|---|---|
| Rule applied | a QM module in the same image as ASIL B software keeps QM only if it is shown that it cannot violate an ASIL B safety requirement — in memory, in timing and execution, and in the exchange of information (6-7.4.8, 9-6, 6-Annex D); the software that provides the separation is ASIL B (6-7.4.9); a module called by ASIL B code before the safe state is reached is part of that ASIL B path (section 1.3) |
| Modules classified | **36 modules** covering every archive and object linked into the demo image (81 parts, checked against the linker map) and every one of the **157** OpenBSW copy entries of `copies.yaml` |
| ASIL B | **21 modules**: 13 safety mechanisms (`B-SM`), 3 separating elements (`B-SEP`: FreeRTOS with its port and configuration, OS binding with task table and stacks, interrupt lock with start-up and vectors), 5 dependencies on an ASIL B path (`B-DEP`: ADC driver, pin configuration, system timer and reset, clock set-up, run-time libraries) |
| QM with coexistence argument | **15 modules** (`QM-C`): 9 with code in the demo image — logger, console, lifecycle and system administration, diagnostics (UDS, DoCAN, transport), XCP, CAN drivers, device drivers of stage 2, BSP I/O managers, application SWCs with BSW services — and 6 that only classify copies (build files, board configuration, NvM/DEM, fault injection, tests, docs) |
| Measured share (demo image) | flash **ASIL B 100 639 B (46.6 %)**, QM 115 357 B (53.4 %); RAM ASIL B 30 270 B (75.3 %), QM 9 945 B (24.7 %) — RAM is mostly task stacks, which belong to the OS binding (section 2.2) |
| Coexistence arguments | **54** rows (section 3): **28 argued** with an existing measure and evidence, **22 open** with a named task or finding, **4 AoU** handed to the integrator |
| QM modules fully argued today | **1 of 9** QM modules with code in the image (XCP); the other 8 count as **ASIL B for any release claim** until their open rows are closed (rule of section 1.4) — which is where the gap analysis stood (row 6-13), now with the list of what closes each row |
| New findings | **CX-1 … CX-9** (section 5): QM logger and `printf` inside the reaction path before the safe state (CX-1); the QM console command `lc` shuts the safety component down and disables the watchdog without a safe state (CX-2); ETL assert resets without the safe outputs table (CX-3); QM code can re-energise an output while S1 holds without a reset (CX-4, SYS-30); kernel data unprotected (CX-5); MPU descriptor budget of 8 on S32K144 (CX-6); CAN gate and QM CAN integration in one unit (CX-7); stacks of QM tasks without guard (CX-8); service console commands left in the product image after SYS-74 not assessed (CX-9, added by DOC-35) |
| OpenBSW copies | of 157 entries, **100** belong to ASIL B modules; **53 of the 82 modified copies** are in ASIL B modules and count as own development under part 6 (SYS-62; three more since SYS-98, three more since SYS-74/SYS-97 in `copies.yaml` of integ6, one more QM copy since PLAT-26/PLAT-27, three more QM copies since PLAT-106: `RuntimeSystem.h/.cpp`, `SysAdminSystem.h` behind `NX_CONSOLE`) — only 29 modified copies are QM |
| Changes to other documents | AoU-17 rewritten and concept 6.3 points here (`concept.md`); safety plan P9-02 `partial`; safety case EW-20 `partial` (carried by SYS-21); the rest as proposals (section 8) |
| Verification | **mock/host**: `doc/safety/test/test_coexistence.py` (9 checks, counter-checks with broken copies); target build of the baseline warning-free |

---

## 1. Reading of the rule and classes

### 1.1 What the standard asks (own words)

- **6-7.4.8, 9-6.** When software elements with different ASILs — or QM and ASIL elements — share one image, either
  everything is developed to the highest ASIL, or it is shown for each lower-rated element that it cannot interfere
  with the higher-rated ones. Without that evidence the lower-rated element inherits the highest ASIL.
- **6-7.4.9.** If freedom from interference rests on partitioning, the shared resources are handled so that the
  separation holds, the partitioning software itself is developed to the highest ASIL of the partitions, and its
  effectiveness is shown at integration. Hardware support for the partitioning is demanded only at ASIL D; an MPU on
  one core is admissible at B.
- **6-Annex D.** Interference is considered in three kinds: **timing and execution** (blocking, deadlock, livelock,
  wrong allocation of execution time, wrong synchronisation), **memory** (corruption of content, access to memory
  allocated to another element), **exchange of information** (repetition, loss, delay, insertion, masquerade,
  corruption, wrong sequence of data, blocking of a communication channel).
- **9-6** is the *coexistence* clause; **9-5** (decomposition) is not used at stage 1 (safety plan T-08).

Stage 1 has one ASIL (B) and QM. So the question per module is binary: B, or QM with evidence.

### 1.2 Classes

<!-- classes:start -->
| Class | Meaning | Development |
|---|---|---|
| `B-SM` | implements a software safety requirement (component of [`sw-architecture.md`](sw-architecture.md) section 1) | part 6 at ASIL B (allocation, 6-7.4.6) |
| `B-SEP` | provides the separation other arguments rest on: scheduler, task and stack layout, interrupt lock, start-up, vector table, linker script | part 6 at ASIL B or qualified per 8-12 (6-7.4.9, decision iso-q4) |
| `B-DEP` | called by ASIL B code **before** the safe state is reached, or set up so that an ASIL B mechanism relies on it | part 6 at ASIL B or qualified per 8-12 (SYS-62) |
| `QM-C` | QM; stays QM only with the coexistence rows of section 3 argued | QM process (tests, static analysis as for all code); counts as ASIL B while a row is open |
<!-- classes:end -->

### 1.3 Direction of calls

Interference from QM to B is what the standard names. In this code base it also enters the other way: when ASIL B
code **calls** QM code, a fault of the QM code runs in the ASIL B context (its stack, its priority, its interrupt
lock). The rule used here:

- a QM function may be called from ASIL B code **after** the safe state has been reached (the outputs are
  de-energised by then; a fault of the callee can only delay the reset, which the MCU watchdog bounds) —
  example: the hard-fault dump uses `printf` after the safe outputs table was walked;
- before the safe state, every callee is `B-DEP` or the call is removed. The linker cross-reference of the safety
  archives (section 2.3) is the list of such calls.

### 1.4 When a QM module counts as QM

A `QM-C` module counts as QM for a release claim only when **every** coexistence row of section 3 that names it has
status `argued` or `AoU`. A single `open` row makes it ASIL B for that release. This keeps the claim honest while
SYS-21 is not done: today 8 of the 9 `QM-C` modules with code in the image are ASIL B by this rule (section 0), which
is the state the gap analysis described (row 6-13).

---

## 2. Inventory of the demo image

### 2.1 Modules

Every archive and object file linked into `app.ecu.elf` of the demo build is in exactly one row (column "Image
parts", archive or `archive(object)` for archives split between modules). Sizes are summed from the input sections of
the linker map: flash = sections placed below `0x1FFF0000` (code, read-only data, initial values), RAM = sections
above (`.bss`, `.data`, stacks). Column "Paths" lists the source directories; every OpenBSW copy of `copies.yaml` falls
under one of them (longest prefix).

<!-- modules:start -->
| ID | Module | Class | Paths | Image parts | Flash B | RAM B |
|---|---|---|---|---|---|---|
| M-SUP | Safe supervisor, synchronous reset line | B-SM | `ecu/safety/safeSupervisor`, `platforms/s32k1xx/port/safety/safeSupervisor`, `platforms/s32k1xx/port/main/src/safeConsole` | `libsafeSupervisor.a`, `SafeConsole.cpp.obj` | 8 604 | 404 |
| M-WDG | Safe watchdog and MCU watchdog BSP | B-SM | `ecu/safety/safeWatchdog`, `external/openbsw/platforms/s32k1xx/safety/safeBspMcuWatchdog` | `libsafeWatchdog.a`, `libsafeBspMcuWatchdog.a` | 792 | 4 |
| M-LCY | Safety lifecycle (safety cycle, ISR hooks, safety component) | B-SM | `platforms/s32k1xx/port/safety/safeLifecycle`, `ecu/application/src/systems/SafetySystem.cpp`, `ecu/application/include/systems/SafetySystem.h`, `ecu/safety/CMakeLists.txt` | `libsafeLifecycle.a`, `SafetySystem.cpp.obj` | 1 242 | 63 |
| M-MEM | Safe memory (MPU, ROM check, ECC) | B-SM | `platforms/s32k1xx/port/safety/safeMemory` | `libsafeMemory.a`, `libromCheckFlashWrites.a` | 2 460 | 119 |
| M-SYS | Safe system (ADC reference, internal supplies) | B-SM | `platforms/s32k1xx/port/safety/safeSystem` | `libsafeSystem.a` | 572 | 0 |
| M-SIO | Safe I/O (pin lock, register check) | B-SM | `platforms/s32k1xx/port/safety/safeIo` | `libsafeIo.a`, `libnxSafeIoRegisterList.a` | 1 100 | 4 |
| M-SPC | Supervision-peripheral checks | B-SM | `platforms/s32k1xx/port/safety/safeSupervisionCheck` | `libsafeSupervisionCheck.a` | 360 | 0 |
| M-HFH | Fault handlers and hard-fault dump | B-SM | `platforms/s32k1xx/hardFaultHandler` | `libnxHardFaultHandler.a` | 1 125 | 8 |
| M-INT | Integration of the safety part (start order, power-stage adapters, deadman, clock check report) | B-SM | `platforms/s32k1xx/port/main/src/integration`, `platforms/s32k1xx/port/main/src/main.cpp`, `platforms/common/clockCheck` | `libmain.a(Integration.cpp.obj)`, `libmain.a(main.cpp.obj)`, `libmain.a(SafeOffHoldCommand.cpp.obj)`, `libnxClockCheckReport.a` | 6 416 | 3 277 |
| M-RTE | RTE run-time supervision and signal qualifier | B-SM | `ecu/application/src/systems/RteSystem.cpp`, `ecu/application/include/systems/RteSystem.h`, `ecu/rte`, `tools/rtegen/templates`, `ecu/application/src/systems/test` | `RteSystem.cpp.obj`, `librte.a(RteConfig.cpp.obj)`, `librte.a(Rte.c.obj)` | 24 987 | 2 033 |
| M-E2E | End-to-end protection | B-SM | `libs/bsw/e2e` | `libe2e.a` | 1 360 | 0 |
| M-CAN | CAN transmit gate and bus state, with the CAN system unit that compiles them (CX-7) | B-SM | `libs/bsw/canSystem`, `platforms/s32k1xx/port/main/src/systems/CanSystem.cpp`, `platforms/s32k1xx/port/main/src/integration/CanShutdownStep.cpp`, `platforms/s32k1xx/port/main/include/systems/CanSystem.h` | `libmain.a(CanSystem.cpp.obj)`, `libmain.a(CanShutdownStep.cpp.obj)` | 9 439 | 121 |
| M-ECU | ECU state, run-down, start reason | B-SM | `libs/bsw/ecuState`, `ecu/application/src/systems/EcuStateSystem.cpp`, `platforms/s32k1xx/port/mcuResetReason` | `libecuState.a`, `EcuStateSystem.cpp.obj`, `libbswMcuResetReason.a` | 7 904 | 60 |
| S-RTOS | FreeRTOS kernel, Cortex-M4F port with SysTick, kernel configuration | B-SEP | `external/openbsw/libs/3rdparty/freeRtos`, `external/openbsw/platforms/s32k1xx/3rdparty/freertos_cm4_sysTick`, `platforms/s32k1xx/port/freeRtosCoreConfiguration` | `libfreeRtos.a`, `libfreeRtosCm4SysTick.a` | 6 006 | 929 |
| S-OSB | OS binding: `async` on FreeRTOS, task table and priorities, task stacks, OS hooks (stack overflow, kernel assert) | B-SEP | `external/openbsw/libs/bsw/asyncFreeRtos`, `ecu/asyncBinding`, `ecu/asyncCoreConfiguration`, `ecu/application/src/app`, `ecu/application/include/app`, `platforms/s32k1xx/port/main/src/osHooks` | `libasyncFreeRtosImpl.a`, `app.cpp.obj`, `libosHooks.a` | 8 702 | 22 465 |
| S-INT | Interrupt lock, start-up code, vector table and ISR entries, linker script, application entry with the ETL assert handler | B-SEP | `external/openbsw/platforms/s32k1xx/bsp/bspInterruptsImpl`, `platforms/s32k1xx/port/main/src/bsp`, `platforms/s32k1xx/port/main/src/os`, `platforms/s32k1xx/port/main/linkerscript`, `ecu/application/src/main.cpp` | `libbspInterruptsImpl.a`, `libstartUp.a`, `main.cpp.obj` | 5 959 | 0 |
| D-ADC | Shared ADC driver (`AnalogInput`, IO-14 lock) | B-DEP | `external/openbsw/platforms/s32k1xx/bsp/bspAdc`, `platforms/s32k1xx/port/bspConfiguration/src/bsp/adc` | `libbspConfiguration.a(AnalogInput.cpp.obj)`, `libbspAdc.a` | 4 501 | 117 |
| D-IO | Pin configuration driver | B-DEP | `external/openbsw/platforms/s32k1xx/bsp/bspIo` | `libbspIo.a` | 1 048 | 0 |
| D-TIM | System timer, delay, software reset, core registers | B-DEP | `platforms/s32k1xx/port/bspConfiguration/src/bsp/SystemTimer`, `external/openbsw/platforms/s32k1xx/bsp/bspMcu`, `external/openbsw/platforms/s32k1xx/bsp/bspCore` | `libbspConfiguration.a(SystemTimer.cpp.obj)`, `libbspMcu.a`, `libbspCore.a` | 976 | 28 |
| D-CLK | Clock set-up and static BSP start | B-DEP | `platforms/s32k1xx/bsp/bspClock`, `external/openbsw/platforms/s32k1xx/bsp/bspClock`, `platforms/s32k1xx/port/main/src/lifecycle`, `platforms/s32k1xx/port/main/include/lifecycle` | `libbspClock.a`, `libmain.a(StaticBsp.cpp.obj)` | 3 226 | 55 |
| D-RTL | Run-time libraries: newlib nano, libgcc, libm, syscalls, ETL, the platform's own `operator delete` (DOC-28: libstdc++ and libnosys no longer linked) | B-DEP | `external/openbsw/platforms/s32k1xx/etlImpl`, `external/openbsw/libs/3rdparty/etl`, `platforms/common/newlib`, `ecu/etl_profile`, `platforms/s32k1xx/port/main/src/cxxRuntime` | `libg_nano.a`, `libgcc.a`, `libm.a`, `NoHeapOperatorDelete.cpp.obj`, `libetlImpl.a`, `crtbegin.o`, `crti.o`, `crtn.o` | 3 860 | 583 |
| Q-LOG | Logger (OpenBSW logger, component mapping, buffered output, `printf`, `util`) | QM-C | `external/openbsw/libs/bsw/logger`, `external/openbsw/libs/bsw/util`, `external/openbsw/libs/3rdparty/printf`, `ecu/application/src/logger`, `ecu/application/include/logger` | `liblogger.a`, `logger.cpp.obj`, `libprintf.a`, `libutil.a` | 16 900 | 8 523 |
| Q-CON | Console, console commands, UART and character I/O, safety test consoles | QM-C | `ecu/application/src/console`, `ecu/application/include/console`, `ecu/consoleCommands`, `external/openbsw/libs/bsw/asyncConsole`, `external/openbsw/libs/bsp/bspCharInputOutput`, `external/openbsw/platforms/s32k1xx/bsp/bspUart`, `platforms/s32k1xx/port/bspConfiguration/src/bsp/uart`, `platforms/s32k1xx/port/bspConfiguration/src/bsp/stdIo`, `platforms/s32k1xx/port/safety/adcSafetyConsole`, `platforms/s32k1xx/port/main/src/systems/FlexNvmCommand.cpp`, `platforms/s32k1xx/port/main/src/systems/SafeOffOutputTester.cpp`, `platforms/s32k1xx/port/main/include/systems/SafeOffOutputTester.h`, `platforms/s32k1xx/port/main/src/systems/CheckedOutputPwmTester.cpp`, `platforms/s32k1xx/port/main/include/systems/CheckedOutputPwmTester.h` | `console.cpp.obj`, `libconsoleCommands.a`, `libasyncConsole.a`, `libbspCharInputOutput.a`, `libbspUart.a`, `libbspConfiguration.a(UartConfig.cpp.obj)`, `libbspConfiguration.a(stdIo.cpp.obj)`, `libadcSafetyConsole.a`, `libmain.a(FlexNvmCommand.cpp.obj)`, `libmain.a(SafeOffOutputTester.cpp.obj)`, `libmain.a(CheckedOutputPwmTester.cpp.obj)` | 10 583 | 974 |
| Q-LCM | Lifecycle manager, system administration, run-time statistics, BSP system | QM-C | `external/openbsw/libs/bsw/lifecycle`, `external/openbsw/libs/bsw/runtime`, `external/openbsw/libs/bsw/time`, `ecu/application/src/systems/SysAdminSystem.cpp`, `ecu/application/src/systems/RuntimeSystem.cpp`, `ecu/application/include/systems`, `platforms/s32k1xx/port/main/src/systems/BspSystem.cpp`, `platforms/s32k1xx/port/main/include/systems/BspSystem.h`, `platforms/s32k1xx/port/main/src/systems/DataFlashSystem.cpp` | `liblifecycle.a`, `libruntime.a`, `libtime.a`, `SysAdminSystem.cpp.obj`, `RuntimeSystem.cpp.obj`, `libmain.a(BspSystem.cpp.obj)`, `libmain.a(DataFlashSystem.cpp.obj)` | 3 858 | 17 |
| Q-DIA | Diagnostics: UDS application and stack, DoCAN, transport, routing configuration, reset-cause DEM reporter (SYS-113; reads a copy of the reset report, writes nothing to the supervisor) | QM-C | `ecu/uds`, `external/openbsw/libs/bsw/uds`, `external/openbsw/libs/bsw/docan`, `external/openbsw/libs/bsw/transport`, `external/openbsw/libs/bsw/transportRouterSimple`, `ecu/configuration`, `platforms/s32k1xx/port/main/src/integration/SafetyResetReport.cpp` | `libappUds.a`, `libuds.a`, `libdocan.a`, `libtransport.a`, `libtransportRouterSimple.a`, `libtransportConfiguration.a`, `libudsConfigurationImpl.a`, `libconfiguration.a`, `libmain.a(SafetyResetReport.cpp.obj)` | 44 194 | 140 |
| Q-XCP | XCP measurement and calibration | QM-C | `ecu/xcp` | `libxcp.a`, `libappXcp.a` | 3 528 | 0 |
| Q-CDR | CAN controller and transceiver drivers, CAN abstraction, generated CAN database | QM-C | `external/openbsw/platforms/s32k1xx/bsp/bspFlexCan`, `platforms/s32k1xx/port/bspConfiguration/bspFlexCan`, `external/openbsw/platforms/s32k1xx/bsp/canflex2Transceiver`, `external/openbsw/libs/bsw/cpp2can`, `tools/dbcgen` | `libbspFlexCan.a`, `libcanflex2Transceiver.a`, `libcpp2can.a`, `librte.a(CanDb.c.obj)` | 5 802 | 33 |
| Q-DEV | Device drivers of stage 2 (SBC, gate driver, high-side switches), their SPI and the I2C buses with the console group `i2c` (IO-28) | QM-C | `libs/bsw/sbcUja113x`, `libs/bsw/gdTmc6200`, `libs/bsw/highSideSwitch`, `platforms/s32k1xx/bsp/bspLpspi`, `platforms/s32k1xx/port/main/src/sbc`, `platforms/s32k1xx/port/main/src/systems/SbcSystem.cpp`, `platforms/s32k1xx/port/main/src/systems/GateDriverSystem.cpp`, `libs/bsp/bspI2c`, `platforms/s32k1xx/bsp/bspFlexioI2c`, `platforms/s32k1xx/port/bspConfiguration/src/bsp/i2c` | `libsbcUja113x.a`, `libgdTmc6200.a`, `libhighSideSwitch.a`, `libhighSideSwitchSystem.a`, `libbspLpspi.a`, `libmain.a(SbcSystem.cpp.obj)`, `libmain.a(SbcEarlyStart.cpp.obj)`, `libmain.a(GateDriverSystem.cpp.obj)`, `libbspFlexioI2c.a`, `libbspConfiguration.a(FlexioI2cBuses.cpp.obj)`, `libbspConfiguration.a(I2cBuses.cpp.obj)`, `libbspConfiguration.a(I2cCommand.cpp.obj)` | 26 519 | 36 |
| Q-IOM | BSP input, output and PWM managers | QM-C | `external/openbsw/libs/bsp/bspInputManager`, `external/openbsw/libs/bsp/bspOutputManager`, `external/openbsw/libs/bsp/bspOutputPwm` | `libbspInputManager.a`, `libbspOutputManager.a`, `libbspOutputPwm.a` | 3 465 | 210 |
| Q-APP | Application SWCs and BSW services of the RTE | QM-C | `swc`, `ecu/rte/services` | `librte.a(DemoSwc.c.obj)`, `libbswServices.a` | 508 | 12 |
| Q-BLD | Build and configuration files of the copies (not in the image) | QM-C | `ecu/CMakeLists.txt`, `ecu/application/CMakeLists.txt`, `ecu/platforms`, `platforms/s32k1xx/port/CMakeLists.txt`, `platforms/s32k1xx/port/main/CMakeLists.txt`, `platforms/s32k1xx/port/safety/CMakeLists.txt` | — | 0 | 0 |
| Q-BCF | Board configuration of the port (generated headers, bus IDs) | QM-C | `platforms/s32k1xx/port/bspConfiguration`, `ecu/configuration/include/busid` | — | 0 | 0 |
| Q-NVM | NvM, DEM and cell monitor systems (not in the demo image) | QM-C | `ecu/application/src/systems/nvm`, `ecu/application/src/systems/dem`, `platforms/s32k1xx/port/main/src/systems/CellMonitorSystem.cpp` | — | 0 | 0 |
| Q-FIJ | Fault injection commands (`safety mpu/wdg`, deadman, hard-fault test) — to leave the product image (SYS-74) | QM-C | `platforms/s32k1xx/port/main/src/integration/SafetyFaultInjectionCommand.cpp` | — | 0 | 0 |
| Q-TST | Host tests and test doubles next to the copies | QM-C | `ecu/consoleCommands/test` | — | 0 | 0 |
| Q-DOC | Module documentation next to the copies | QM-C | `ecu/platforms/README.md` | — | 0 | 0 |
<!-- modules:end -->

Remarks:

- **Why S-OSB holds 22 KB RAM.** `app.cpp` defines the task stacks (`2048 B` each, section 2.1 of
  `sw-architecture.md`) and the task objects. The stacks are the memory the separation argument is about (section 3,
  CX-8); they belong to the module that lays them out, not to the tasks that use them.
- **M-CAN is larger than the gate.** The transmit gate and the bus state are header-only (`libs/bsw/canSystem`) and
  compiled into `CanSystem.cpp`, which also holds the QM integration of the CAN controllers. A translation unit
  takes the highest ASIL of what it contains (CX-7).
- **The bootloader** (C-BLD of `sw-architecture.md`) is a separate image and not part of this inventory; it contains
  no QM module that runs together with the application.
- Q-BLD, Q-BCF, Q-NVM, Q-FIJ, Q-TST and Q-DOC have no part in the demo image; they exist so that every copy of
  `copies.yaml` is classified. Q-FIJ is in the image of the development build only when fault injection is on.

### 2.2 Measured share per class

<!-- shares:start -->
| Class | Modules | Flash B | Flash % | RAM B | RAM % |
|---|---|---|---|---|---|
| B-SM | 13 | 66 361 | 30.7 | 6 093 | 15.2 |
| B-SEP | 3 | 20 667 | 9.6 | 23 394 | 58.2 |
| B-DEP | 5 | 13 611 | 6.3 | 783 | 1.9 |
| QM-C | 15 | 115 357 | 53.4 | 9 945 | 24.7 |
| total | 36 | 215 996 | 100.0 | 40 215 | 100.0 |
<!-- shares:end -->

Totals are the sums of the module rows; the linker map attributes 211 688 B flash and 40 187 B RAM to input sections
of named files. `arm-none-eabi-size` reports `text` 211 060, `data` 692, `bss` 41 680 for the same ELF; the RAM
difference is the heap (516 B) and MSP stack (1 024 B) the linker script reserves without an input file, plus
alignment fill. ASIL B in section 0 is the sum of the three B classes.

### 2.3 Calls from ASIL B code into other modules

Undefined symbols of the safety archives, resolved against the archives of the image (`arm-none-eabi-nm -u` against
`--defined-only`, demo build). Calls inside the safety archives are left out.

| Caller | Callee | Symbols | Before the safe state? | Consequence |
|---|---|---|---|---|
| `libsafeSupervisor.a` | Q-LOG (`libutil.a`, logger component table) | `Logger::doLog`, `_componentMapping`, `SAFETY` | **yes** — every case of `handle()` logs before `enterLimpHome()` | **CX-1** |
| `libsafeLifecycle.a`, `libsafeMemory.a` | Q-LOG | `Logger::doLog` | yes (safety task, not under the reaction lock) | CX-1 |
| `libsafeLifecycle.a` | Q-CON (`libadcSafetyConsole.a`) | `registerAdcSafetyConsole`, `takeAdcDiagnosticsResetRequest` | yes | argued: CA-12 |
| `libsafeLifecycle.a` | S-INT (`libstartUp.a`) | IRQ handlers of CAN, ERM, FTFC | — | the `SAFE_ISR` wrappers; S-INT is ASIL B |
| `libsafeSupervisor.a` | D-TIM (`libbspMcu.a`), M-INT | `softwareSystemReset`, `rteTimingMailbox`, `safeConsoleFlush` | after S1 | ASIL B modules |
| `libsafeMemory.a` | D-TIM (`SystemTimer`) | `getSystemTimeMs32Bit` | yes | D-TIM is `B-DEP` |
| `libsafeSystem.a` | D-ADC | `AnalogInput::get` | yes | D-ADC is `B-DEP` (DF-45, DF-46 of `dfa.md`) |
| `libsafeIo.a` | D-IO, S-INT | `Io::getConfiguration`, `Io::setConfiguration`, `nxSafeOutputs` | yes (pin lock at start) | `B-DEP`, `B-SEP` |
| `libsafeBspMcuWatchdog.a` | D-TIM | `sysDelayUs` | yes (watchdog self-test) | `B-DEP` |
| `libnxHardFaultHandler.a` | Q-CON (`libbspCharInputOutput.a`) | `printf`, `vsnprintf` | **no** — the dump runs after the safe outputs table (SM-17) | allowed by section 1.3 |

Two findings come from this table: the logger (CX-1) and — found while reading the callers of the safety component —
the lifecycle path (CX-2). The ETL assert handler (CX-3) is not in this table because it is registered, not called:
`ecu/application/src/main.cpp` installs `etl_assert_function`, which every `ETL_ASSERT` in B and QM code reaches.

---

## 3. Coexistence arguments per QM module

One row per QM module and kind of interference that is plausible for it. Kinds: **memory**, **timing** (timing and
execution), **information** (exchange of information), **resource** (shared peripherals and kernel objects — the
6-7.4.9 a point). Status: `argued` = an existing measure prevents or detects the interference in time, with evidence;
`open` = no measure yet, the task named carries it; `AoU` = the integrator's side, stated in the safety manual
(DOC-19).

<!-- arguments:start -->
| ID | Module | Kind | Interference (own words) | Measure | Status | Evidence / task |
|---|---|---|---|---|---|---|
| CA-01 | Q-LOG | memory | a wild write of the logger (or of any QM task or interrupt handler) into the supervisor state or the safety stack | MPU region, gate open only in the safety context (SM-10 … SM-13); SYS-170: every interrupt that preempts the safety cycle runs through a `SAFE_ISR` wrapper that closes the gate and the safety stack first (all external vectors of the derivative and SysTick, generated; exceptions with reason in `unit-design.md` 5.4), checked on the image by `tools/elfcheck` (`isr-unwrapped`) | argued | `safe-supervisor-mpu`, `safety-gate`, board 2026-09-21 (DF-41); SYS-170: `bswgen`, `elfcheck`, `elfcheck-demo` (host); board proof "gate closed during an ISR" open |
| CA-02 | Q-LOG | timing | the logger runs inside `SafeSupervisor::handle()` with all interrupts masked (PRIMASK) before the safe state; a hang there delays S1 until the MCU watchdog resets | none before the reset; the halt deadman (SM-06) is masked by the same lock | open | CX-1 → N-CX1 |
| CA-03 | Q-LOG | information | the log text of a safety event is formatted by QM `printf`; wrong text is not a safety matter, but the formatting runs before S1 | as CA-02 | open | CX-1 → N-CX1 |
| CA-04 | Q-LOG | timing | the log drain in the idle context starves nothing above idle; the ROM check shares the idle context (DFA-1) | idle is the lowest priority; SM-14 reports a pass that overruns its period | argued | `rom-check`, SYS-44 |
| CA-05 | Q-CON | memory | console commands write memory of other modules (for example `safety` test functions, `gd enable`) | the protected RAM is written only through the MPU gate; test and fault injection commands leave the product image | open | SYS-74 (done: the product build leaves out the console group safety and gd enable, `elfcheck-product`, `target-product`); the service commands that stay in the product image are not assessed: CX-9 → N-CX4 |
| CA-06 | Q-CON | timing | an input flood: the UART interrupt (level 9) preempts every task for a bounded time per byte, the console runs in the idle context below every supervised task | safety task above every application task; UART ISR bounded per byte; SM-04 period check against LPTMR | argued | `cycle-period-check`, DF-37 |
| CA-07 | Q-CON | information | the command `lc` drives the lifecycle to shut the safety component down: the safety cycle is cancelled and the MCU watchdog **disabled**, no safe state | none | open | CX-2 → N-CX2, SYS-74 |
| CA-08 | Q-CON | information | `safeadc reset` asks the safety task to clear ADC statistics | request word with a pattern in ordinary RAM, taken by the safety task; the reset clears statistics only, never the consecutive-error counters of SM-19/SM-20 | argued | SafeSystem::resetAdcDiagnostics (SYS-37), `safe-system` |
| CA-09 | Q-LCM | timing | the lifecycle manager never starts the safety component, or starts it late | the MCU watchdog is configured and tested in `main()` before the scheduler (SM-07); without the safety cycle it is not serviced and resets; the reset-loop limit ends in S1 held (SSR-065) | argued | `safe-supervisor` (reset-loop limit), board SYS-52 |
| CA-10 | Q-LCM | information | a wrong transition request (level 0, restart) reaches `SafetySystem::shutdown()`, which disables the watchdog without S1 | none | open | CX-2 → N-CX2 |
| CA-11 | Q-LCM | timing | run-time statistics (`background`, priority 10) and the `sysadmin` task (50) run below the safety task (60) | fixed priorities from `tasks.yaml`, `above_application` checked | argued | `nxconfig`, `schedana` |
| CA-12 | Q-CON | resource | the ADC console reads SM-18/19 counters from the console task | read-only access to a copy; no write to the protected RAM from the console task | argued | `safety-context` (SYS-37 static rule) |
| CA-13 | Q-DIA | memory | the UDS stack writes data by identifier and runs routines; a fault writes other modules' RAM | protected RAM through the MPU (SM-10); everything else in one address space | open | SYS-21 (DF-42) |
| CA-14 | Q-DIA | timing | a diagnostic flood loads the CAN task (40) and the CAN interrupt (level 8) | safety task above the CAN task; SM-04 period check; SM-06 above the RTOS boundary | argued | DF-37, `cycle-period-check` |
| CA-15 | Q-DIA | information | a programming-session request or an ECU reset ends the application | the transition goes through the bootloader with a reset; outputs return to their reset state; the bootloader starts only a valid image (SSR-051) | argued | `canflash-tool`, `bootloader-host`, SYS-38 |
| CA-16 | Q-DIA | information | an I/O control or routine that drives an output in a session | no neXtBSW routine drives a power output; an integrator's routine is application code | AoU | AoU-17 (new text), DOC-19 |
| CA-17 | Q-XCP | memory | XCP `DOWNLOAD` writes RAM | writes are refused outside the calibration regions of the RTE; each write passes the generated plausibility check before it takes effect (SYS-90) | argued | `xcp-host`, `calibration-plausibility` |
| CA-18 | Q-XCP | timing | XCP runs in the CAN task, below the safety task | as CA-14 | argued | DF-37 |
| CA-19 | Q-XCP | information | a calibration changes a safety-relevant parameter | plausibility limits generated from `swc.yaml`; the reference page is read-only in production | argued | SYS-90, SYS-91 |
| CA-20 | Q-CDR | information | the QM CAN stack repeats, loses, delays, inserts, corrupts or reorders frames of safety signals | E2E profile check (counter, CRC, data ID, time-out) in the receiving ASIL B module for every configured signal (SM-31) | argued | `e2e`, `e2e-mutations`, SSR-041 |
| CA-21 | Q-CDR | information | a safety-relevant signal without E2E configuration | the integrator configures E2E for every signal his safety concept needs | AoU | AoU-17, DOC-19 |
| CA-22 | Q-CDR | resource | the QM CAN driver transmits into an unhealthy bus | transmit gate and bus state in M-CAN (SM-32) | argued | `can-phy-gate`, SSR-042 |
| CA-23 | Q-CDR | timing | receive interrupt storm | as CA-14; interrupt level 8 below the deadman (4) | argued | DF-37 |
| CA-24 | Q-DEV | information | a QM device driver re-energises a power output while S1 holds without a reset (`CHECK_REGISTER_ERROR`) | S1 walks the table once; GPIO data registers are not re-checked | open | CX-4 → SYS-30 |
| CA-25 | Q-DEV | resource | the SPI master shared by three devices (F5) | no stage-1 mechanism uses SPI; one device per `spi[]` entry | argued | `dfa.md` section 6 (SPI-4) |
| CA-26 | Q-DEV | resource | the SPI master shared between a QM and a future ASIL B partition | ownership per partition | open | SYS-21 (SPI-5) |
| CA-27 | Q-DEV | memory | driver state and SPI buffers in general RAM | as CA-13 | open | SYS-21 (DF-42) |
| CA-28 | Q-DEV | timing | a driver blocks the `bsp` task (20) or the SPI interrupt | `bsp` task below the safety task; the halt deadman is retriggered from the `bsp` task and trips when it stalls ≥ 10 ms (SM-06) — the fail-safe direction | argued | `lpit-halt-deadman`, board SYS-48 |
| CA-29 | Q-IOM | information | the output manager drives a safe_off output during S1 | as CA-24 | open | CX-4 → SYS-30 |
| CA-30 | Q-IOM | resource | the PWM manager reconfigures an FTM of the power stage | SYS-24 register classes `PWM_TIMER`, `CLOCK_GATE` checked every safety cycle (SM-22) | argued | `safe-io`, board SYS-24 |
| CA-31 | Q-IOM | resource | a pin function of a safety output changed | PCR lock at start-up (SM-21) | argued | `safe-io-mutations`, board SYS-24 |
| CA-32 | Q-APP | memory | a QM SWC writes data of a safety-related SWC or RTE buffers | none besides the MPU region of the supervisor | open | SYS-21 (DF-42) |
| CA-33 | Q-APP | timing | a QM SWC delays or starves a safety-related runnable | SM-05 reports overruns (S3); activation not supervised (F10) | open | SYS-21 (DF-43, F10) |
| CA-34 | Q-APP | information | a QM SWC sends wrong values to a safety-related SWC | qualifier for analogue values (SM-30); nothing for SWC-to-SWC data | open | SYS-21 (protected RTE buffers or E2E inside the ECU) |
| CA-35 | Q-APP | information | the integrator's application needs a safety-related SWC at all | stage 1 names no safety-related SWC of its own; the integrator allocates them | AoU | AoU-17 |
| CA-36 | Q-LOG | resource | the UART of the logger is also the safety reset line's UART | the reset line writes by polling with its own bounded flush (SYS-59); the logger buffer is not drained | argued | `safe-console`, board SYS-59 |
| CA-37 | Q-LCM | memory | lifecycle and runtime objects in general RAM | as CA-13 | open | SYS-21 (DF-42) |
| CA-38 | Q-CON | memory | console line buffer overrun | bounded line editor; general RAM | open | SYS-21 (DF-42) |
| CA-39 | Q-XCP | information | XCP reads (`UPLOAD`, DAQ) of any address | reads do not change state; DAQ runs in the CAN task | argued | `xcp-host` |
| CA-40 | Q-DIA | resource | the UDS stack and the application share the CAN controller | transmit through the gate (SM-32); mailbox ownership per bus in the CAN system | argued | `can-phy-gate` |
| CA-41 | Q-CDR | memory | receive buffers of the CAN driver in general RAM | as CA-13 | open | SYS-21 (DF-42) |
| CA-42 | Q-IOM | timing | input manager polling | `bsp` task below the safety task | argued | `schedana` |
| CA-43 | Q-LCM | resource | the `sysadmin` task changes a run level while the safety cycle runs | the lifecycle manager serialises transitions; the safety component is at run level 1 and leaves only with a shutdown (CA-10) | open | CX-2 → N-CX2 |
| CA-44 | Q-DEV | information | an SBC driver fault commands SBC sleep or a fail-safe input | the SBC is outside the element (stage 2, AoU-21); its limp and reset outputs are hardware | AoU | AoU-21 |
| CA-45 | Q-DEV | information | a device report with a malformed context word | the supervisor checks the word and reacts S1 + S2 for a malformed report (SYS-29) | argued | `safe-supervisor` |
| CA-46 | Q-APP | resource | an application task holds the interrupt lock for long | only the MCU watchdog bounds it; the deadman is masked by PRIMASK too | open | SYS-21 (a rule that application code uses the RTOS lock, not PRIMASK) |
| CA-47 | Q-LOG | memory | the logger's buffer lies below the MSP stack; the ISR stack has no guard | none | open | CX-8 → SYS-21 (STK-2 of `sw-architecture.md`) |
| CA-48 | Q-CON | resource | FlexNVM console command writes flash while a ROM check pass runs | write bracket discards the pass (SYS-44) | argued | `nvm-flash-write-bracket` |
| CA-49 | Q-DIA | memory | kernel structures written through a QM task's kernel call with corrupted arguments | `configASSERT` → `RTOS_ASSERT` (SYS-80) for what the kernel checks; nothing else | open | CX-5 → SYS-21 |
| CA-50 | Q-APP | memory | stack overflow of a QM task into a neighbouring stack | detected at the next context switch (`STACK_OVERFLOW`, SYS-80), after the damage | open | CX-8 → SYS-21 |
| CA-51 | Q-CDR | timing | bus-off recovery loops in the CAN task | bounded by the bus-off counters of the bus state (COM-12) | argued | `can-bus-state` |
| CA-52 | Q-LCM | timing | the lifecycle's run-down stalls | time-out per step of the run-down (SM-53) | argued | `ecustate`, SSR-052 |
| CA-53 | Q-IOM | memory | output and PWM manager state in general RAM | as CA-13 | open | SYS-21 (DF-42) |
| CA-54 | Q-IOM | timing | pin event interrupt storm (bouncing contact, broken wire, fault on the line): the pin vector preempts every task (`libs/bsp/bspPinEvent`, IO-53) | rate limit per input disarms the pin and re-arms it after a hold-off, bounded ISR path — driver robustness, not credited; the credited detection stays SM-04 period check and SM-06 (halt deadman, level above every pin rank) | argued | `pinevent`, `cycle-period-check`, DF-37 |
<!-- arguments:end -->

**Fully argued QM modules (section 1.4):** Q-XCP only (CA-17, CA-18, CA-19, CA-39). Q-CDR follows once SYS-21 closes
CA-41; Q-LCM once CX-2 and CA-37 are closed; Q-LOG needs CX-1.

---

## 4. The separating element at ASIL B

Decision iso-q4 fixes that the software implementing the separation is ASIL B. Concretely, for neXtBSW on S32K1:

| Element | Module | Why it separates | Route to ASIL B |
|---|---|---|---|
| FreeRTOS scheduler, tick, context switch, `configASSERT` | S-RTOS | temporal separation: the safety task preempts every application task; the priorities are the argument of CA-06, CA-11, CA-14, CA-28 | pre-existing: qualification per 8-12 or replacement — the route is decided in SYS-62 (iso-q5, `rtos-comparison.md`) |
| Task table, priorities, stacks, OS hooks | S-OSB | the layout the temporal and memory arguments rest on; stack overflow reaction (SYS-80) | own development per part 6; `tasks.yaml` checked by `nxconfig` (`above_application`) |
| MPU set-up, gate, descriptor check | M-MEM (`MemoryProtection`) | spatial separation of the supervisor; basis of every future partition | own development per part 6 (already `B-SM`) |
| ISR hooks and `SAFE_ISR` wrapper | M-LCY (`IsrHooks`) | opens the gate for exactly the safety interrupts | own development (already `B-SM`) |
| Interrupt lock, vector table, start-up, linker script | S-INT | the lock is the critical section of every monitor; the linker script places `.mpu_bss` and the safety stack | copies of OpenBSW (lock, start-up) and own linker script: own development per part 6 where modified, 8-12 where identical |

**What the S32K1 MPU can and cannot do for partitions** (register facts from `mcudb`, `hsi-s32k1xx.md`):

- The NXP system MPU grants an access if **any** valid region descriptor grants it. A protected window therefore has
  to be cut out of the full-access area: since SYS-97 **all 8** descriptors of S32K144 are used (descriptor 0 for the
  debugger and, as written by SYS-171, the other bus masters, 1, 3 and 5 for three full-access tiles around the two protected
  windows 2 and 4: supervisor data and safety stack, 6 for the stack guard, 7 for the tail).
- One more protected window costs two descriptors (the window and one more tile) — so **no** further window fits on
  S32K144, S32K142, S32K146 and S32K116 (8 descriptors, all used) without freeing descriptors, and about four more fit on S32K148 (16). **Consequence for
  SYS-21:** the partition model has to be designed within the 8 descriptors (or free descriptors first), and the
  restriction of descriptor 0 for the masters M2/M3 (AoU-41) must not need a descriptor of its own. This bounds SYS-21 to a
  **two-partition model** on the small derivatives: one contiguous ASIL B data window (safety-related SWC data and
  stacks, switched at the context switch) against everything QM (CX-6).
- FreeRTOS on this family uses the non-MPU Cortex-M4F port: **every task runs privileged**, and the kernel data
  (task control blocks, ready lists, queues) lie in general RAM writable by every task (CX-5). The system MPU can
  discriminate supervisor and user accesses, but only if the kernel runs QM tasks unprivileged — which needs a port
  with a system-call layer. That is a question for the RTOS route (SYS-62): FreeRTOS has such ports only for the ARM
  core MPU, which S32K1 does not have (`rtos-comparison.md` section 2.5).

Consequence for the argument: on S32K1, kernel data corruption by a QM task is **detected, not prevented** — through
the timing mechanisms that the corruption would trip (MCU watchdog SM-01 … SM-03, period check SM-04, halt deadman
SM-06) and the kernel asserts (SYS-80). Whether that detection argument is sufficient at ASIL B is decision point
**Q-CX1** (section 9.3).

---

## 5. Findings

<!-- findings:start -->
| ID | Finding | Where | Effect | Proposed measure | Carrier |
|---|---|---|---|---|---|
| CX-1 | `SafeSupervisor::handle()` calls `Logger::error` (QM logger, `printf` formatting) in every case **before** `enterLimpHome()`, under the PRIMASK lock; `SafetyManager` and `SafeMemory` log from the safety task | `ecu/safety/safeSupervisor/src/safeSupervisor/SafeSupervisor.cpp` (`handle()`) | a fault in QM code delays S1; a hang is ended only by the MCU watchdog (demo 250 ms), the deadman is masked | enter S1 first, then log; or replace the log call in `handle()` by the synchronous ASIL B reset line (SYS-59) and a no-init record (NVM-03); the log in the safety task is allowed only through a bounded, non-blocking enqueue whose code is `B-DEP` | new task **N-CX1** (paths `ecu/safety/safeSupervisor`) |
| CX-2 | the lifecycle can shut the safety component down: `SafetySystem::shutdown()` cancels the cycle and **disables the MCU watchdog** without S1; the QM console command `lc` and any wrong transition request reach it | `ecu/application/src/systems/SafetySystem.cpp`, `ecu/application/src/systems/SysAdminSystem.cpp` (`lc`) | the safety function ends while outputs may stay energised | `SafetySystem::shutdown()` enters S1 (walk the safe outputs table) before it disables the watchdog; `lc` leaves the product image (SYS-74) | new task **N-CX2** (paths `ecu/application/src/systems`); SYS-74 |
| CX-3 | the ETL assert handler prints and calls `softwareSystemReset()` — no safe outputs table, no supervisor event; unlike `configASSERT` (SYS-80) | `ecu/application/src/main.cpp` (`etl_assert_function`) | a failed `ETL_ASSERT` anywhere resets with outputs energised until the reset takes effect (SSR-002 not met on this path) | route it to the supervisor as an S1 + S2 event (like `RTOS_ASSERT`); the copy becomes `modified`. **Done by SYS-98 (wave cloud-1001, host-verified):** `main.cpp` registers `safety::etlAssertFailed`, event `ETL_ASSERT` (cause `0x117`) with S1, reset mark, fixed line and reset; `main.cpp` is `modified` in `copies.yaml`. Open: board proof (`lc assert`) | SYS-98 |
| CX-4 | while S1 holds **without** a reset (`CHECK_REGISTER_ERROR` with I/O support), QM code can switch a safe_off output on again; S1 walks the table once and GPIO data registers are not re-checked | `SafeState`, H6 of `hsi-s32k1xx.md` | loss of the safe state for S1-only reactions | read-back and re-walk of the safe outputs (SSR-060) | SYS-30 (exists) |
| CX-5 | kernel data (TCBs, lists, queues) in general RAM, all tasks privileged | S-RTOS | QM wild writes to kernel data are only detected through timing effects | decision Q-CX1; on derivatives with a core MPU a port with privilege separation | SYS-21, SYS-62 |
| CX-6 | 8 MPU descriptors on S32K144, all 8 used since SYS-97, any-region-grants semantics | `MemoryProtection.cpp`, `mcudb/s32k1xx/*.yaml` `mpu.regions` | no further protected window without freeing descriptors: SYS-21 must fit the two-partition model into the 8 descriptors | SYS-21 designs for two partitions on 8-descriptor derivatives; `nxconfig` refuses a partition count the derivative cannot hold (concept 10.2) | SYS-21 |
| CX-7 | the CAN transmit gate (ASIL B) is compiled into the same translation unit as the QM CAN integration | `platforms/s32k1xx/port/main/src/systems/CanSystem.cpp` | about 9 KB of QM integration code inherit ASIL B | move the gate supervision cycle into its own unit | proposal to SYS-21 (no own task) |
| CX-8 | stacks of QM tasks and the ISR stack have no guard; overflow is detected only at the next context switch (tasks) or not at all (MSP, STK-2) | S-OSB, linker script | memory interference before detection | guard regions cost MPU descriptors (CX-6): a pattern check per safety cycle for the MSP, a guard for the ASIL B window only | SYS-21 |
| CX-9 | after SYS-74 the product build still contains the service console commands that write registers or the state of other modules (`sbc`/`gd` register writes, `out`/`pwm set`, `can send`, `kl30b on`, `nvm write`, `dem clear`/`dem cycle`; list from the open point of SYS-74) | `ecu/consoleCommands`, `cmake/NxProductBuild.cmake` | a QM console command in the product image can change the state of ASIL B modules or outputs outside the MPU-protected RAM (CA-05); whether each one is a specified function of the product is not assessed | assess every service command for the product image: leave it out of the product build like the test commands (SYS-74) or argue it as a specified function; check the result in the image with `tools/elfcheck product` | new task **N-CX4** (paths `ecu/consoleCommands`, `cmake`, `tools/elfcheck`; same work as safety plan N7) |
<!-- findings:end -->

---

## 6. OpenBSW copies and their class

Every entry of [`tools/openbswsync/copies.yaml`](../../tools/openbswsync/copies.yaml) belongs to the module whose path is
the longest prefix of the copy's path (root `path` + file key; for patterns the part before the first wildcard). The
test counts them; the numbers below are the result at the baseline.

<!-- copies:start -->
| Class | identical | modified | new | total |
|---|---|---|---|---|
| B-SM | 9 | 32 | 29 | 70 |
| B-SEP | 4 | 15 | 1 | 20 |
| B-DEP | 1 | 6 | 3 | 10 |
| QM-C | 12 | 29 | 16 | 57 |
| total | 26 | 82 | 49 | 157 |
<!-- copies:end -->

Reading for SYS-62: a **modified** copy in an ASIL B module is own development and falls under part 6 (tests,
coverage, static analysis as a gate); an **identical** copy in an ASIL B module is pre-existing software under 8-12; a
copy in a `QM-C` module needs neither, only the coexistence rows of its module. The classification is kept here and
not as a field in `copies.yaml`: the loader of `tools/openbswsync` ignores unknown keys, so a field there would be
unchecked, and the class belongs to the module, not to the file. `copies.yaml` is therefore **not changed** by this
task (it is in the allowed paths); the test reads it.

---

## 7. Hand-over to SYS-21 — the evidence it must produce

SYS-21 (freedom from interference) turns the `open` rows into `argued`. Grouped by what it builds:

| No. | Evidence | Closes | Kind |
|---|---|---|---|
| FFI-1 | partition model from the configuration: one ASIL B window (safety-related SWC data, their stacks, the RTE buffers they read) and QM for the rest, within the descriptor budget of the derivative (CX-6); `nxconfig` refuses what does not fit | CA-13, CA-27, CA-32, CA-37, CA-38, CA-41 | memory |
| FFI-2 | a write from QM into the ASIL B window is a supervisor event (not only a bus fault) | same | memory |
| FFI-3 | activation supervision and execution budget per partition, building on SM-05 (F10) | CA-33 | timing |
| FFI-4 | exchange between partitions through protected RTE buffers or in-ECU E2E | CA-34 | information |
| FFI-5 | shared-resource ownership (SPI master, ADC as model, CAN mailboxes) per partition | CA-26 | resource |
| FFI-6 | rule and check that application code uses the RTOS critical section, not PRIMASK | CA-46 | resource |
| FFI-7 | decision and measure for kernel data (Q-CX1) | CA-49, CX-5 | memory |
| FFI-8 | stack guards: MSP pattern check per safety cycle, guard for the ASIL B window | CA-47, CA-50, CX-8 | memory |
| FFI-9 | effectiveness at integration: a board test that writes from a QM task into the ASIL B window and one that starves a safety-related runnable (6-7.4.9 d) | FFI-1 … FFI-3 | all |

This list extends section 7.1 of [`dfa.md`](dfa.md) (DF-42, DF-43, DF-44, SPI-4/5, DF-18) by the module view; the two
lists are consistent (every DF of `dfa.md` 7.1 appears in a CA row above).

---

## 8. Changes made to other documents, and proposals

Made here (unavoidable for the task: the done-when names safety plan, concept and AoU-17):

| Document | Change |
|---|---|
| `concept.md` AoU-17 | rewritten: coexistence per module in this document; the integrator's application is QM unless he allocates safety requirements to it, and then it lives in the ASIL B window of SYS-21 or is developed with the whole image at ASIL B |
| `concept.md` 6.3 | one paragraph pointing here |
| `safety-plan.md` | P9-02 status `planned` → `partial` (this document exists, SYS-21 evidence open); result counts adjusted; the coexistence bullet of section 3 states the result |
| `safety-case.md` EW-20 | status `missing` → `partial`, carrier SYS-21; result counts adjusted; counter-check of `test_safety_case.py` follows the row |

Proposed, not applied (other documents or paths):

| No. | Where | Proposal |
|---|---|---|
| P1 | `iso26262-gap.md` rows 6-13, 9-02 | status `missing` → `partial` with reference to this document |
| P2 | `sw-architecture.md` section 1 | replace the paragraph "ASIL B by default" by a reference to section 2.1 here; add D-ADC, D-IO, D-TIM, D-CLK, D-RTL to the component list |
| P3 | `dfa.md` 7.1 | add CX-5 and CX-6 as rows for SYS-21 |
| P4 | `ssr.sdoc` | new SSR "the reaction path calls no QM code before the safe state" (CX-1), "shutting the safety component down enters the safe state first" (CX-2), "every assert handler enters the safe state" (CX-3), all ASIL B, derived from ASG-06/AoU-17 |
| P5 | `tools/trace` or a static rule | a check that the safety archives import no symbol of a `QM-C` module except the allowed post-S1 calls (section 2.3) — the linker cross-reference this document did by hand |
| P6 | `doc/todo.yaml` | tasks N-CX1, N-CX2, N-CX3 (section 5), N-CX4 (CX-9, DOC-35); SYS-21 done-when gains FFI-1 … FFI-9; SYS-62 takes the copy counts of section 6 |
| P7 | `tools/test/tests.yaml` | entry for `doc/safety/test/test_coexistence.py` (block in the SYS-61 report) |

---

## 9. Verification and open points

### 9.1 What was checked

| Check | How | Result |
|---|---|---|
| every linked part classified exactly once | test: parts of the linker map `application.map` against column "Image parts" | 81 parts, all classified (demo build of the baseline) |
| every copy classified | test: every entry of `copies.yaml` to a module by longest path prefix | 157 of 157 |
| paths exist | test: every path in column "Paths" exists in the tree (submodule paths only when the submodule is checked out) | pass |
| arguments complete | test: every `QM-C` module with a part in the image has rows for memory, timing and information; every `open` row names a task open in `doc/todo.yaml` or a finding CX-n; every CX-n names a carrier | pass |
| numbers | test: section 0, 2.2 and 6 against the tables and the copy list | pass |
| licence rule | test: no quotation marks around standard text, clause references only | pass |
| calls from ASIL B code | `arm-none-eabi-nm` over the safety archives (section 2.3), by hand | 2 QM callee modules: Q-LOG before the safe state (CX-1), Q-CON once argued (CA-08) and once after S1 (hard-fault dump) |
| target build | `cmake --build --preset evalboard-s32k144-demo` at the baseline | warning-free, `text` 211 060 B |

### 9.2 Counter-checks

The test breaks copies of this document on purpose (an unclassified part, a copy without module, an open row without
task, a wrong total, a row for an unknown module) and asserts each rule reports it.

### 9.3 Open points and decisions for the user

| ID | Question | Why it matters |
|---|---|---|
| Q-CX1 | Is the **detection** of kernel data corruption through the timing mechanisms (section 4) an acceptable argument at ASIL B on S32K1, or must the kernel data be protected (which needs an RTOS port with privilege separation, i.e. the SYS-62 route)? | decides whether SYS-21 needs a kernel change on the focus family |
| Q-CX2 | Is the **two-partition model** (one ASIL B window) enough for the first applications (motor control, battery management), given 8 descriptors on S32K144? | decides the partition schema of SYS-21 and the configuration check |
| O1 | board evidence for FFI-9 (effectiveness at integration) | SYS-21 |
| O2 | confirmation review of this document at I1 (CM-09) | SYS-63, reviewer not named (iso-q1) |

---

## 10. Review questions of the architecture (section 11)

| No. | Answer |
|---|---|
| 1 (no SBC) | the classification does not depend on the SBC; Q-DEV is QM either way, the MCU watchdog is the timing backstop |
| 2 (other crystal) | no clock value here; D-CLK is `B-DEP` regardless of the frequency |
| 3 (other derivative) | the MPU budget is a derivative value (`mcudb` `mpu.regions`): two-partition model at 8, more at 16 (S32K148); S32K3/STM32/RM4x have a core MPU and a FreeRTOS MPU port becomes possible (CX-5 changes there) |
| 4 (several CAN buses) | CA-20 … CA-23 hold per bus; E2E and the gate are per bus already |
| 5 (several tasks) | the temporal arguments rest on the priority order from `tasks.yaml`, not on a fixed task set |
| 6 (configuration level) | no new configuration value; the partition count check proposed for SYS-21 reads the derivative level |
| 7 (own test profile) | the test reads the demo build only for the inventory; the table checks run without any build |
| 8 (Qt) | not touched |
| 9, 10 | no driver, no file under `platforms/` |

## 11. Sources

`doc/safety/iso26262-gap.md` (rows 6-13, 6-14, 9-02; section 4 point 5), `doc/safety/safety-plan.md` (P6-06, P9-02,
CM-09), `doc/safety/concept.md` (2.1, 4.4, 6.3, 10.2), `doc/safety/sw-architecture.md` (sections 1–3, 5), `doc/safety/dfa.md`
(4, 5.7, 7.1), `doc/safety/rtos-comparison.md` (2.5), `doc/safety/hsi-s32k1xx.md` (H6, H10), `doc/safety/fmea.md` (F5, F10, N3),
`tools/openbswsync/copies.yaml`, `mcudb/s32k1xx/*.yaml`, the demo build of `dev` at `65f3dc9` (`application.map`,
`arm-none-eabi-nm`), the code named in sections 2.3 and 5. ISO 26262:2018 parts 6 and 9 as read for SYS-58 (clause
numbers only).
