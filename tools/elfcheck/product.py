"""Product image without test, fault-injection and service write commands (SYS-74, SYS-119; iso26262-gap.md 19).

  python tools/elfcheck product --app <app.ecu.elf> [--expect-all]

A product build (cmake -DNX_PRODUCT_BUILD=ON, cmake/NxProductBuild.cmake) leaves out the console commands that only
test a safety mechanism by provoking a fault or its reaction, or pass a protection for a board test. This check
reads the linked image and reports every such command it still finds:

- a defined symbol of the command (symbol table, local symbols included), or
- one of its console texts in the bytes that go into the ECU (allocated sections with content; the debug
  information is not part of the image and would name enumerators of code that is compiled out).

SYS-119 adds the service commands that write registers, frames, outputs or the fault memory past the function that
owns them (coexistence.md CX-9, safety plan N7): the product image keeps only the reading commands of those groups.
Their markers name the console group they belong to (`group`, a text of the group's help): a project without the
module (no cell monitor, no I2C bus) has no such command in any build, so the counter check skips the marker there.

`--expect-all` is the counter check: on an image built with every test option (NX_HARDFAULT_TEST_COMMAND,
NX_GD_ENABLE_COMMAND, NX_SAFETY_FAULT_INJECTION_COMMAND, NX_FLEXNVM_PARTITION_COMMAND, NX_DFLASH_COMMAND,
NX_SHUTDOWN_HANG_COMMAND, NX_SHORT_LOCK_MEASURE_COMMAND) every command must be found, so a marker that no longer matches its command turns the check red instead of passing silently (a
marker whose console group is not in the image does not count, see above).

Exit code 0 when the expectation holds, 1 otherwise, 2 for unusable input.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import elffile  # noqa: E402


@dataclass(frozen=True)
class Marker:
    """One test or fault-injection command and how it shows in a linked image."""
    command: str                    # console command as typed
    why: str                        # why it is not in a product image
    symbols: tuple[str, ...] = ()   # regular expressions on (mangled) symbol names
    texts: tuple[bytes, ...] = ()   # console texts in the image
    option: str | None = None       # test-build option that brings it (None: in every development build)
    group: bytes | None = None      # SYS-119: help text of its console group (None: the group is in every image)


# The inventory of SYS-74 (doc/changelog.d/SYS-74.md). Texts are the help texts of the command tables and the
# console output of the commands, symbols the classes and functions only the command uses.
MARKERS: tuple[Marker, ...] = (
    Marker("safety mpu|wdg|prt|dst|est", "fault injection: MPU violation, watchdog reset, locked register write, "
           "safe state on and off through the SAFETY_TEST pin",
           symbols=(r"6safety13SafetyCommand",),   # not AdcSafetyCommand (safeadc stays)
           texts=(b"Commands to test safety features", b"enforce MPU violation", b"enforce watchdog reset",
                  b"Disable safety test pin", b"Re-enable safety test pin")),
    Marker("lc udef|pabt|dabt|assert", "fault injection: exceptions and a failed assertion on request",
           texts=(b"forces an undefined instruction exception", b"forces a prefetch abort exception",
                  b"forces a data abort exception", b"forces an assert")),
    Marker("lc reboot|poweroff|level", "lifecycle shut-down: cancels the safety cycle and disables the MCU watchdog "
           "without the safe state (coexistence.md CX-2)",
           texts=(b"reboot the system", b"poweroff the system", b"switch to level")),
    Marker("sbc failstart", "fault injection: resets before the SBC start (failed-start counter of the SBC)",
           symbols=(r"armFailStarts", r"failStartIfArmed"),
           texts=(b"JP1 test (board rule)", b"failed starts still armed", b"failed starts disarmed")),
    # NVM-19: in every development build of a project with NvM blocks (RteSystem.cpp, #ifndef NX_PRODUCT_BUILD)
    Marker("nvm inject", "fault injection: damages copy A or B of an NvM block (board proof of NVM-18/NVM-19)",
           symbols=(r"nvmInject",),
           texts=(b"TEST BUILD: damages copy A or B",)),
    # SYS-162 (SR-110-1 c): in every development build (BspSystem, #ifndef NX_PRODUCT_BUILD); the OpenBSW tester and
    # its help texts come only with the wrapper, so both count
    Marker("pwm all|set|setPeriod", "writes duties and periods of PWM channels from the console, past the function "
           "that drives the group (finding PLAT-110: pwm set on a pair of the power stage)",
           symbols=(r"OutputPwmTester",),
           texts=(b"channel[0-9,0xFF=all] duty[0=off|10000=100%]", b"refused: channel of a PWM group")),
    # SYS-119 (CX-9, safety plan N7): service commands that write past the function owning the register, frame,
    # output or fault memory - in every development build of a project with the module (#ifndef NX_PRODUCT_BUILD)
    Marker("sbc write", "writes any UJA113x register past the driver (fail-safe and limp-home, regulators, CAN)",
           texts=(b"write register (param: <address> <value>), not 0x00/0x01/MTPNV", b"0x%02x <- 0x%02x: %s"),
           group=b"UJA113x system basis chip"),
    Marker("sbc pn", "board test of COM-39: writes the CAN wake-up frame registers without Sleep (the sleep "
           "configures them itself)",
           texts=(b"arm: write CAN wake-up frame registers", b"SBC PN armed"),
           group=b"UJA113x system basis chip"),
    Marker("<gd> write", "writes GCONF/GSTAT/SHORT_CONF/DRV_CONF of the gate driver past the driver",
           texts=(b"write GCONF/GSTAT/SHORT_CONF/DRV_CONF",),
           group=b"state, faults, GSTAT/IOIN, counters"),
    Marker("<cmu> write", "writes fault status registers of the cell monitor past the driver",
           texts=(b"write a fault status register, 0 clears",),
           group=b"state, faults, balancing, counters"),
    Marker("can send", "sends any standard frame with any id on any bus (own and foreign messages)",
           texts=(b"send a standard frame if the transceiver is ready", b"%s tx 0x%x [%u]: %s"),
           group=b"CAN buses of the project"),
    Marker("i2c rd|wr", "writes any bytes to any device of an I2C bus past its driver (rd writes before it reads)",
           texts=(b"write the bytes with STOP", b"write the bytes, repeated START, read n"),
           group=b"I2C buses of the project"),
    # "out" keeps "all" and "get"; the OpenBSW base keeps its help table, so the marker is the wrapper's set path
    Marker("out set", "writes any digital output past the function that drives it (like pwm, SYS-162)",
           texts=(b"refused: safe_off output, switched by its driver only",),
           group=b"outputs console"),
    Marker("dem clear|cycle|setting", "changes the fault memory without session and security access (UDS 14 and "
           "85 are the specified way, 85 with security access in the product, COM-43); cycle is a test",
           texts=(b"[<dtc>] clears every DTC", b"test: ends and restarts the running operation cycles",
                  b"DTC status updates off/on (like 85)"),
           group=b"fault memory (NVM-03)"),
    # SYS-119 review points S1, S2, S4 (decision of the safety review pending; own commit, may be taken back)
    Marker("<hs> on|reset", "S1/S2: switches the load supply on without a specified function behind it, releases a "
           "latched overcurrent or diagnosis fault without a reset",
           texts=(b"switch on, load supply (board rule)", b"clear a latched overcurrent/diagnosis fault"),
           group=b"state, fault, load current, IS raw value"),
    Marker("<gd> clear", "S2: releases latched faults of the gate driver without a reset",
           texts=(b"clear latched faults and GSTAT flags",),
           group=b"state, faults, GSTAT/IOIN, counters"),
    Marker("<cmu> clear", "S2: releases latched faults of the cell monitor without a reset",
           texts=(b"clear latched faults and device fault registers",),
           group=b"state, faults, balancing, counters"),
    Marker("sbc sleep", "S4: SBC Sleep from the console past the run-down of the ECU state (ecu sleep stays)",
           texts=(b"enter SBC Sleep, MCU supply off", b"SBC Sleep in %u ms"),
           group=b"UJA113x system basis chip"),
    Marker("hardfault udf|bus|sp", "fault injection: hard faults on request",
           symbols=(r"HardFaultTestCommand",),
           texts=(b"Trigger a hard fault", b"hardfault: undefined instruction"),
           option="NX_HARDFAULT_TEST_COMMAND"),
    Marker("inject deadman|wdogint", "fault injection: falsified deadman configuration, WDOG_CS[INT] cleared",
           symbols=(r"SafetyFaultInjection", r"injectDeadmanFault", r"clearWatchdogInterrupt"),
           texts=(b"Safety fault injection", b"inject deadman", b"inject wdogint"),
           option="NX_SAFETY_FAULT_INJECTION_COMMAND"),
    Marker("<gd> enable", "switches the gate driver bridge on from the console",
           texts=(b"TEST BUILD: DRV_EN on",),
           option="NX_GD_ENABLE_COMMAND"),
    # NVM-17: same test-build option as "flexnvm partition" (the build that can write a key space can also undo it);
    # listed before it, so the last marker keeps an option of its own (counter check of test_product.py)
    Marker("csec m1|m2|m3|loadkey|rng|challenge|dbgauth", "irreversible CSEc key load and debug authorisation "
           "(erases all keys and the FlexNVM partition)",
           symbols=(r"CsecRecovery", r"CsecCommand", r"csecLaunchFromRam"),
           texts=(b"CSEc way back to the factory state", b"IRREVERSIBLE: erase all keys and the partition",
                  b"IRREVERSIBLE: load the MASTER_ECU_KEY"),
           option="NX_FLEXNVM_PARTITION_COMMAND"),
    Marker("flexnvm partition", "irreversible FlexNVM partitioning",
           symbols=(r"FlexNvmPartition",),
           texts=(b"IRREVERSIBLE: write the partition", b"to write it (user at the board only)"),
           option="NX_FLEXNVM_PARTITION_COMMAND"),
    # NVM-23: board proof of the data flash areas (systems/DataFlashSystem.cpp)
    Marker("dflash show|read|erase|write", "erases and programs the data flash areas (calibration data) from the "
           "console",
           symbols=(r"DataFlashSystem7Command",),
           texts=(b"data flash areas (NVM-23, test build)", b"dflash show lists them"),
           option="NX_DFLASH_COMMAND"),
    # SYS-173: board proof of the direct reset of SM-53 (integration/ShutdownHangCommand.cpp)
    Marker("shutdownhang arm|disarm|show", "fault injection: a lifecycle component hangs in its shutdown on request, "
           "replaces the direct reset hook of the ECU state management by a reporting wrapper",
           symbols=(r"ShutdownHangSystem", r"ShutdownHangCommand", r"reportingDirectReset"),
           texts=(b"SYS-173 test: level-2 component hangs", b"shutdownhang: DIRECT RESET"),
           option="NX_SHUTDOWN_HANG_COMMAND"),
    # SYS-172: board proof of the lock time of the safe state selection (systems/ShortLockMeasureCommand.cpp); only a
    # project with an active short has the selection; its "console group" is a log line of the start test of the short
    # (RteSystem.cpp SafeStateLog, NX_RTE_HAS_SAFE_STATE_TEST; the refusal line is dropped by the linker without an RTE
    # port that selects) - the demo has none, the counter check skips the marker there
    Marker("shortlock show|reset|select|block", "selects and blocks the short of a PWM group from the console, "
           "measured interrupt lock with probe pin",
           symbols=(r"ShortLockMeasureCommand", r"nxSafeStateLockStatRead"),
           texts=(b"SYS-172 lock time of the safe state selection", b"short blocked until power-on, allowed"),
           option="NX_SHORT_LOCK_MEASURE_COMMAND",
           group=b"short removed until power-on"),
)


@dataclass(frozen=True)
class Hit:
    marker: Marker
    kind: str                       # "symbol" or "text"
    what: str

    def __str__(self) -> str:
        return f"{self.marker.command}: {self.kind} {self.what}"


def image_parts(img: elffile.ElfImage) -> tuple[list[str], bytes]:
    """Names of the defined symbols and the bytes that go into the ECU (allocated sections with content)."""
    symbols = [s.name for s in img.symbols if s.shndx != 0]
    content = b"\0".join(img.section_bytes(s) for s in img.sections if s.has_content)
    return symbols, content


def find(symbols: list[str], content: bytes, markers: tuple[Marker, ...] = MARKERS) -> list[Hit]:
    """Every marker found in the symbol names or the image bytes."""
    hits: list[Hit] = []
    for marker in markers:
        for pattern in marker.symbols:
            rx = re.compile(pattern)
            hits.extend(Hit(marker, "symbol", name) for name in symbols if rx.search(name))
        hits.extend(Hit(marker, "text", repr(text.decode("ascii", "replace")))
                    for text in marker.texts if text in content)
    return hits


def applicable(content: bytes, markers: tuple[Marker, ...] = MARKERS) -> tuple[Marker, ...]:
    """SYS-119: the markers whose console group is in the image (a marker without group always applies)."""
    return tuple(m for m in markers if m.group is None or m.group in content)


def missing(hits: list[Hit], markers: tuple[Marker, ...] = MARKERS) -> list[Marker]:
    """Markers without any hit (counter check: each must be found in a test-build image)."""
    found = {id(h.marker) for h in hits}
    return [m for m in markers if id(m) not in found]


def check_image(path: Path) -> list[Hit]:
    return find(*image_parts(elffile.load(path)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="elfcheck product", description="product image without test, "
                                     "fault-injection and service write commands (SYS-74, SYS-119)")
    parser.add_argument("--app", type=Path, required=True, help="linked application image (app.ecu.elf)")
    parser.add_argument("--expect-all", action="store_true", help="counter check: every command must be found "
                        "(image of a development build with every test option)")
    args = parser.parse_args(argv)
    try:
        symbols, content = image_parts(elffile.load(args.app))
        hits = find(symbols, content)
    except elffile.ElfError as e:
        print(f"elfcheck product: {e}", file=sys.stderr)
        return 2
    print(f"elfcheck product: {args.app}")
    for hit in hits:
        print(f"  found {hit}")
    if args.expect_all:
        expected = applicable(content)
        absent = missing(hits, expected)
        for marker in absent:
            print(f"error: {marker.command}: not found - the marker no longer matches the command")
        for marker in MARKERS:
            if marker not in expected:
                print(f"  skipped {marker.command}: its console group is not in the image (module not in the project)")
        print(f"elfcheck product: {len(expected) - len(absent)} of {len(expected)} test commands found (counter "
              "check)")
        return 1 if absent else 0
    for hit in hits:
        print(f"error: {hit.marker.command}: in the product image ({hit.kind} {hit.what}) - {hit.marker.why}")
    print(f"elfcheck product: {len(hits)} findings, {len(MARKERS)} test commands checked")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
