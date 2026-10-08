# SPDX-License-Identifier: Apache-2.0
"""IO-33: the SBC configuration of the port (bsp/sbc/sbcConfiguration.h) from the project and the board.

- Generat: integration/sbcUja113xConfiguration.h carries the project values of modules.<sbc> (with 1/0 macros per
  choice) and the wiring of the part's pins from board.yaml devices[].pins (NX_DEVICE_<sbc>_PIN_<pin>_IS_<use>).
- Compile probe: the real port header is compiled against the generat of the demo and of own variants (not only
  the eval board: another project on testboard_sbc_errors with other values, a UJA1131HW/5V0 with bank 1) and
  prints BATTERY_MONITOR, the HVIO banks and EN. The demo prints exactly the values that stood fixed in the header
  before IO-33 (9000/18000/500/500 mV, BAT, nothing switched off, EN low), plus HVIO2..HVIO4 as wake-up inputs.
- Mutants of the header (a choice read from the wrong macro, a pin read from its neighbour) are detected.
"""

import copy
import os
import re
import sys
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_bswgen as base  # noqa: E402

import bswgen  # noqa: E402

PORT_HEADER = base.PORT / "bspConfiguration" / "include" / "bsp" / "sbc" / "sbcConfiguration.h"
PROFILES = base.SBC_PROFILES
ERROR_BOARD = PROFILES.parent / "boards" / "testboard_sbc_errors"

SBC_CONFIG_PROBE = r"""
// IO-33: compile probe of bsp/sbc/sbcConfiguration.h - prints what the port hands the driver.
#include "bsp/sbc/sbcConfiguration.h"

#include <cstdio>

GPIO_Type fakeGpio[5]   = {};
PORT_Type fakePort[5]   = {};
LPSPI_Type fakeLpspi[3] = {};

int main()
{
    ::sbc::Uja113xConfig const& c = ::bsp::sbc::SBC_CONFIG;
    ::sbc::BatteryMonitorConfig const& b = c.battery;
    std::printf("BM %d %u %u %u %u %u %u %u %u\n", b.configure ? 1 : 0, static_cast<unsigned>(b.source),
                static_cast<unsigned>(b.undervoltageMv), static_cast<unsigned>(b.overvoltageMv),
                static_cast<unsigned>(b.undervoltageHysteresisMv), static_cast<unsigned>(b.overvoltageHysteresisMv),
                static_cast<unsigned>(b.canShutdown), static_cast<unsigned>(b.v2Shutdown),
                static_cast<unsigned>(b.shutdownDelayMs));
    for (unsigned bank = 0U; bank < UJA113X_HVIO_BANKS; ++bank)
    {
        ::sbc::HvioBankConfig const& h = c.hvio[bank];
        std::printf("HVIO%u %d %u", bank, h.configure ? 1 : 0, static_cast<unsigned>(h.wakeThreshold));
        for (unsigned i = 0U; i < UJA113X_HVIO_PER_BANK; ++i)
        {
            ::sbc::HvioConfig const& p = h.pin[i];
            std::printf(" %u/%u/%u/%d%d%d%d%d/%u/%u", static_cast<unsigned>(p.function),
                        static_cast<unsigned>(p.activation), static_cast<unsigned>(p.shutdown),
                        p.slopeControl ? 1 : 0, p.wakeOnRisingEdge ? 1 : 0, p.wakeOnFallingEdge ? 1 : 0,
                        p.shortCircuitInterrupt ? 1 : 0, p.openLoadInterrupt ? 1 : 0,
                        static_cast<unsigned>(p.shortCircuitThresholdMa), static_cast<unsigned>(p.openLoadThresholdMa));
        }
        std::printf("\n");
    }
    std::printf("EN %d %u %u %u LIMP %d\n", c.enPin.configure ? 1 : 0, static_cast<unsigned>(c.enPin.mode),
                static_cast<unsigned>(c.enPin.driver), static_cast<unsigned>(c.enPin.shutdown),
                c.limpHomeAtStartup ? 1 : 0);
    return 0;
}
"""

OFF = "0/0/0/00000/0/0"         # HvioConfig{} - not configured
WAKE = "3/1/0/00000/0/0"        # WAKE_INPUT, activation ON, no shutdown, no edge, no thresholds

# Before IO-33 (sbcConfiguration.h, IO-10): BAT, 9000/18000 mV, windows 500 mV, CSC/V2SC NONE; both HVIO banks not
# configured (ABSOLUTE = 1 as the unused threshold); EN configured LOW (0), push-pull, no shutdown; LIMP released.
BEFORE_IO33 = {"BM": "1 0 9000 18000 500 500 0 0 0",
               "HVIO0": f"0 1 {OFF} {OFF} {OFF} {OFF}",
               "HVIO1": f"0 1 {OFF} {OFF} {OFF} {OFF}",
               "EN": "1 0 0 0 LIMP 0"}


def parse(stdout: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"^(BM|HVIO\d|EN) (.*)$", stdout, re.M)}


class SbcConfigurationTest(unittest.TestCase):
    setUp, tearDown = base.BswGenAdcTest.setUp, base.BswGenAdcTest.tearDown
    compile_probe = base.BswGenSbcConfigurationProbeTest.compile

    def generate(self, project_file: Path, name: str) -> Path:
        gen = self.tmp / f"gen_{name}"
        bswgen.generate(project_file, gen)
        return gen

    def probe(self, gen: Path, first=()) -> dict[str, str]:
        main = self.tmp / "sbc_probe.cpp"
        original = base.SBC_PROBE_MAIN
        try:
            base.SBC_PROBE_MAIN = SBC_CONFIG_PROBE
            result = self.compile_probe(gen, first=first, run=True)
        finally:
            base.SBC_PROBE_MAIN = original
        self.assertEqual(result.returncode, 0, result.stderr[-3000:] + result.stdout[-2000:])
        self.assertTrue(main.exists())
        return parse(result.stdout)

    def variant(self, name: str, sbc_values: dict, pins: dict | None = None, part: str | None = None,
                spi: str = "SBCA_SPI", device: str = "SBC_A") -> Path:
        """A project of its own on testboard_sbc_errors (copy of the board with the given pins/part)."""
        board = yaml.safe_load((ERROR_BOARD / "board.yaml").read_text(encoding="utf-8"))
        dev = next(d for d in board["devices"] if d["name"] == device)
        if part is not None:
            dev["part"] = part
        if pins is not None:
            dev["pins"] = pins
        board_dir = self.tmp / "boards" / name
        board_dir.mkdir(parents=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project = yaml.safe_load((PROFILES / "testproject_sbc_two" / "project.yaml").read_text(encoding="utf-8"))
        project_dir = self.tmp / "projects" / name
        project_dir.mkdir(parents=True)
        project["project"] = {"name": f"sbc_{name}", "board": Path(os.path.relpath(board_dir, project_dir)).as_posix()}
        project["use"] = {"spi": [spi]}
        project["modules"] = {device: sbc_values}
        (project_dir / "project.yaml").write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return project_dir / "project.yaml"

    # ----------------------------------------------------------------------------------------------------------------

    def test_generat_of_the_demo(self):
        gen = self.generate(base.PROJECT_FILE, "demo")
        header = (gen / "integration" / "sbcUja113xConfiguration.h").read_text(encoding="utf-8")
        for line in ["#define NX_DEVICE_SBC_PARAM_UNDERVOLTAGE_MV 9000U // mV",
                     "#define NX_DEVICE_SBC_PARAM_OVERVOLTAGE_MV 18000U // mV",
                     "#define NX_DEVICE_SBC_PARAM_BATTERY_SOURCE_IS_BAT 1",
                     "#define NX_DEVICE_SBC_PARAM_CAN_SHUTDOWN_IS_NONE 1",
                     "#define NX_DEVICE_SBC_PARAM_CAN_SHUTDOWN_IS_UNDERVOLTAGE 0",
                     "#define NX_DEVICE_SBC_PARAM_SHUTDOWN_DELAY_MS 0U // ms",
                     "#define NX_DEVICE_SBC_PIN_HVIO1_IS_NONE 1", "#define NX_DEVICE_SBC_PIN_HVIO1_IS_WAKE_INPUT 0",
                     "#define NX_DEVICE_SBC_PIN_HVIO2_IS_WAKE_INPUT 1", "#define NX_DEVICE_SBC_PIN_HVIO4_IS_WAKE_INPUT 1",
                     "#define NX_DEVICE_SBC_PIN_HVIO5_IS_NONE 0", "#define NX_DEVICE_SBC_PIN_HVIO5_IS_WAKE_INPUT 0",
                     "#define NX_DEVICE_SBC_PIN_EN_IS_NONE 1"]:
            self.assertIn(line + "\n", header)
        # the SPI lines of ARCH-40 stay as they were
        self.assertIn("#define NX_DEVICE_SBC_SPI_INSTANCE LPSPI1\n", header)

    def test_demo_keeps_the_former_values(self):
        values = self.probe(self.generate(base.PROJECT_FILE, "demo"))
        print(f"\ndemo: {values}", file=sys.stderr)
        # battery monitor and EN exactly as fixed in the header before IO-33
        self.assertEqual({k: values[k] for k in ("BM", "EN", "HVIO1")},
                         {k: BEFORE_IO33[k] for k in ("BM", "EN", "HVIO1")})
        # the one deliberate difference: bank 0 with HVIO2..HVIO4 as wake-up inputs (board fact), HVIO1 not wired
        self.assertNotEqual(values["HVIO0"], BEFORE_IO33["HVIO0"])
        self.assertEqual(values["HVIO0"], f"1 1 {OFF} {WAKE} {WAKE} {WAKE}")

    def test_other_values_and_pins(self):
        project = self.variant("values", {"battery_source": "batsense", "undervoltage_mv": 10500,
                                          "overvoltage_mv": 16000, "undervoltage_hysteresis_mv": 700,
                                          "overvoltage_hysteresis_mv": 0, "can_shutdown": "undervoltage",
                                          "v2_shutdown": "both", "shutdown_delay_ms": 120},
                               pins={"HVIO1": {"use": "wake_input"}, "HVIO3": {"use": "none"}})
        values = self.probe(self.generate(project, "values"))
        self.assertEqual(values["BM"], "1 1 10500 16000 700 0 1 3 120")
        self.assertEqual(values["HVIO0"], f"1 1 {WAKE} {OFF} {OFF} {OFF}")
        self.assertEqual(values["HVIO1"], BEFORE_IO33["HVIO1"])
        self.assertEqual(values["EN"], BEFORE_IO33["EN"])
        # overvoltage only, and a variant with eight HVIO: bank 1 from the board as well
        project = self.variant("bank1", {"can_shutdown": "overvoltage"}, part="UJA1131HW/5V0",
                               pins={"HVIO5": {"use": "wake_input"}, "HVIO8": {"use": "wake_input"}})
        values = self.probe(self.generate(project, "bank1"))
        self.assertEqual(values["BM"], "1 0 9000 18000 500 500 2 0 0")
        self.assertEqual(values["HVIO0"], BEFORE_IO33["HVIO0"])       # no wake input in bank 0: untouched
        self.assertEqual(values["HVIO1"], f"1 1 {WAKE} {OFF} {OFF} {WAKE}")

    def test_mutants_are_detected(self):
        header = PORT_HEADER.read_text(encoding="utf-8").replace("\r\n", "\n")
        mutants = {
            "can choice": ("BSP_SBC_PARAM(CAN_SHUTDOWN_IS_UNDERVOLTAGE)", "BSP_SBC_PARAM(CAN_SHUTDOWN_IS_OVERVOLTAGE)"),
            "source": ("BSP_SBC_PARAM(BATTERY_SOURCE_IS_BATSENSE) != 0", "BSP_SBC_PARAM(BATTERY_SOURCE_IS_BAT) == 0 && 0"),
            "pin": ("hvioPinOf(BSP_SBC_WAKE(HVIO1))", "hvioPinOf(BSP_SBC_WAKE(HVIO2))"),
            "delay": ("static_cast<uint16_t>(BSP_SBC_PARAM(SHUTDOWN_DELAY_MS))", "0U"),
        }
        project = self.variant("mutants", {"battery_source": "batsense", "can_shutdown": "undervoltage",
                                           "shutdown_delay_ms": 120},
                               pins={"HVIO1": {"use": "wake_input"}})
        gen = self.generate(project, "mutants")
        good = self.probe(gen)
        for index, (name, (old, new)) in enumerate(mutants.items()):
            with self.subTest(mutant=name):
                self.assertEqual(header.count(old), 1, name)
                patched = self.tmp / f"mutant{index}"
                (patched / "bsp" / "sbc").mkdir(parents=True)
                (patched / "bsp" / "sbc" / "sbcConfiguration.h").write_text(header.replace(old, new), encoding="utf-8")
                self.assertNotEqual(self.probe(gen, first=(patched,)), good, f"mutant not detected: {name}")


class ModuleHeaderTest(unittest.TestCase):
    """The generic part of bswgen/modules.py: choice macros of device parameters and pin macros from the manifest."""

    def test_render(self):
        import modules
        from nxconfig.checks import DeviceUse, Integration
        from nxconfig.model import Manifest

        manifest = Manifest.model_validate({
            "module": "pinTest", "kind": "device_driver", "parts": ["P*"],
            "parameters": {"mode": {"type": "str", "pattern": "slow|fast", "default": "slow"},
                           "name": {"type": "str", "pattern": "[a-z]+", "default": "x"}},
            "pins": {"A": {"uses": ["none", "wake_input"]}, "B": {"uses": ["none"]}}})
        dev = DeviceUse("DEV", "P1", "pinTest", [], [], parameters={"mode": "fast", "name": "abc"},
                        pins={"A": "wake_input"})
        integration = Integration([dev], [], [], [], [], [])
        modset = copy.copy(modules.load_manifests(()))
        modset.manifests = {"pinTest": manifest}
        text = modules.render_module_header(integration, modset, "pinTest", "test")
        for line in ['#define NX_DEVICE_DEV_PARAM_MODE "fast"', "#define NX_DEVICE_DEV_PARAM_MODE_IS_SLOW 0",
                     "#define NX_DEVICE_DEV_PARAM_MODE_IS_FAST 1", "#define NX_DEVICE_DEV_PIN_A_IS_NONE 0",
                     "#define NX_DEVICE_DEV_PIN_A_IS_WAKE_INPUT 1", "#define NX_DEVICE_DEV_PIN_B_IS_NONE 0"]:
            self.assertIn(line + "\n", text)
        self.assertNotIn("NX_DEVICE_DEV_PARAM_NAME_IS", text)     # free text: no choice


if __name__ == "__main__":
    unittest.main()
