"""IO-28: I2C buses of a project - schema i2c.<bus>, checks and plan (tools/nxconfig/i2c.py), generated
bsp/i2c/i2cConfiguration.h and NX_USE_I2C (tools/bswgen).

Own test profile, not the eval board: the PCC profile testboard_s32k1_gates/testproject_s32k1_gates with the test
derivative TESTK1X (8 MHz crystal, FlexIO at SOSCDIV2 = 8 MHz), its PWM group and input capture removed, and a copy of
the test derivative database with invented FlexIO data pins D0..D5 on PTA2, PTA3, PTB2, PTB3, PTB4 and PTE8. On it
up to three I2C buses on one FlexIO; the numbers of the divider are checked against the constants of the S32K1xx
driver (FlexioI2cTiming.h) and against the values the driver's host test measured (IO-09).

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_i2c.py
"""

import copy
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "tools" / "bswgen"))

import nxconfig  # noqa: E402
from nxconfig import i2c as nxi2c  # noqa: E402

import bswgen  # noqa: E402
import modules as bswmodules  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
GATES_BOARD = yaml.safe_load(
    (HERE / "test/profiles/boards/testboard_s32k1_gates/board.yaml").read_text(encoding="utf-8"))
GATES_PROJECT = yaml.safe_load(
    (HERE / "test/profiles/projects/testproject_s32k1_gates/project.yaml").read_text(encoding="utf-8"))
FLEXIO_PINS = {"PTA2": 0, "PTA3": 1, "PTB2": 2, "PTB3": 3, "PTB4": 4, "PTE8": 5}
S32K1 = nxi2c.FLEXIO_FAMILIES["s32k1xx"]
TIMING_HEADER = REPO / "platforms/s32k1xx/bsp/bspFlexioI2c/include/i2c/FlexioI2cTiming.h"


def bus(name: str, sda: str, scl: str, instance: str = "FlexIO") -> dict:
    entry = {"name": name, "instance": instance, "sda": {"pin": sda}, "scl": {"pin": scl}}
    if instance == "FlexIO":
        entry["sda"]["flexio_pin"], entry["scl"]["flexio_pin"] = FLEXIO_PINS[sda], FLEXIO_PINS[scl]
    return entry


SENSOR = bus("SENSOR", "PTA2", "PTA3")
EEPROM = bus("EEPROM", "PTB2", "PTB3")
THIRD = bus("THIRD", "PTB4", "PTE8")


class I2cProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mcudb = self.tmp / "mcudb"
        shutil.copytree(TEST_MCUDB, self.mcudb)
        file = self.mcudb / "s32k1xx" / "TESTK1X.yaml"
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        doc["pinmux"]["LQFP64"]["signals"].update({f"FlexIO_D{n}": {pin: 4} for pin, n in FLEXIO_PINS.items()})
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, buses=(SENSOR, EEPROM), i2c=None, use="all"):
        board = copy.deepcopy(GATES_BOARD)
        board.pop("pwm")
        board.pop("input_capture")
        board["i2c"] = [copy.deepcopy(b) for b in buses]
        project = copy.deepcopy(GATES_PROJECT)
        project.pop("pwm")
        project["project"]["board"] = "../../boards/testboard"
        project["use"]["i2c"] = use
        if i2c is not None:
            project["i2c"] = i2c
        for rel, doc in (("boards/testboard/board.yaml", board), ("projects/testproject/project.yaml", project)):
            path = self.tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return nxconfig.check(self.tmp / "projects/testproject/project.yaml", self.mcudb)

    def findings(self, messages):
        return [(m.severity, m.file.name, m.yaml_path) for m in messages]


class FlexioDividerTest(unittest.TestCase):
    """flexio_baud() is the arithmetic of flexio::calculateBaud() - same constants, same results."""

    def test_constants_of_the_driver(self):
        text = TIMING_HEADER.read_text(encoding="utf-8")

        def constant(name):
            return re.search(rf"constexpr uint32_t {name}\s*=\s*(0x[0-9A-F]+|\d+)U;", text).group(1)

        self.assertEqual(int(constant("MIN_BAUD_COMPARE"), 0), S32K1.min_compare)
        self.assertEqual(int(constant("MAX_BAUD_COMPARE"), 0), S32K1.max_compare)
        edges = int(constant("EDGES_PER_WORD"), 0)
        self.assertEqual((0xFF - 1) // edges - 1, S32K1.max_bytes)       # MAX_BYTES_PER_TRANSFER

    def test_values_of_the_driver_host_test(self):
        """The rates the host test of bspFlexioI2c measured on its register model (CHANGELOG IO-09)."""
        cases = {(40_000_000, 100_000): (199, 100_000), (40_000_000, 400_000): (49, 400_000),
                 (40_000_000, 90_000): (222, 89_686), (40_000_000, 50_000): None,
                 (48_000_000, 400_000): (59, 400_000), (48_000_000, 93_750): (255, 93_750),
                 (8_000_000, 400_000): (9, 400_000), (8_000_000, 1_000_000): (3, 1_000_000),
                 (1_000_000, 400_000): (2, 166_666)}
        for (clock, rate), expected in cases.items():
            with self.subTest(clock=clock, rate=rate):
                self.assertEqual(nxi2c.flexio_baud(S32K1, clock, rate), expected)

    def test_never_faster_than_requested(self):
        for clock in (1_000_000, 8_000_000, 40_000_000, 48_000_000, 80_000_000):
            for rate in range(clock // 512, clock // 6 + 1, 997):
                result = nxi2c.flexio_baud(S32K1, clock, rate)
                if result is None:
                    continue
                compare, scl = result
                self.assertLessEqual(scl, rate)
                self.assertTrue(S32K1.min_compare <= compare <= S32K1.max_compare)
                self.assertEqual(scl, clock // (2 * (compare + 1)))
                if compare > S32K1.min_compare:             # the next faster setting would exceed the request
                    self.assertGreater(clock / (2 * compare), rate)

    def test_min_timeout(self):
        # 15 words of 9 SCL periods at 100 kHz: 1350 us; at 400 kHz 337.5 -> 338 us
        self.assertEqual(nxi2c.min_timeout_us(13, 100_000), 1350)
        self.assertEqual(nxi2c.min_timeout_us(13, 400_000), 338)


class I2cPlanTest(I2cProfile):
    def test_two_buses_share_one_flexio(self):
        config, messages = self.check(i2c={"SENSOR": {"baudrate_hz": 400000, "timeout_ms": 5},
                                           "EEPROM": {"baudrate_hz": 100000, "verify": True}})
        self.assertEqual(self.findings(messages), [])
        self.assertEqual(config.clock.peripheral_hz["FlexIO"], 8_000_000)
        plans = {p.name: p for p in config.i2c_buses}
        self.assertEqual(list(plans), ["SENSOR", "EEPROM"])                # board order
        s, e = plans["SENSOR"], plans["EEPROM"]
        self.assertEqual((s.compare, s.scl_hz, s.timeout_us, s.max_bytes), (9, 400_000, 5000, 13))
        self.assertEqual((s.sda_pin, s.scl_pin, s.tx_shifter, s.rx_shifter, s.scl_timer), (0, 1, 0, 1, 0))
        self.assertEqual((e.compare, e.scl_hz, e.timeout_ms, e.verify), (39, 100_000, 20, True))
        self.assertEqual((e.sda_pin, e.scl_pin, e.tx_shifter, e.rx_shifter, e.scl_timer), (2, 3, 2, 3, 2))
        # the shift timer (SCL timer + 1) never falls on the reserved enable chain (54.3.1.20.2)
        for p in plans.values():
            self.assertNotEqual((p.scl_timer + 1) % S32K1.timer_chain, 0)

    def test_a_third_bus_does_not_fit(self):
        _, messages = self.check(buses=(SENSOR, EEPROM, THIRD))
        self.assertEqual(self.findings(messages), [("error", "project.yaml", "use.i2c")])
        self.assertIn("I2C THIRD: FlexIO has no shifters and timers left", messages[0].text)
        self.assertIn("already used by SENSOR, EEPROM", messages[0].text)
        self.assertIsNotNone(messages[0].line)
        # resources go by board order, not by the order of use.i2c: THIRD is the third bus, at use.i2c[1]
        _, messages = self.check(buses=(SENSOR, EEPROM, THIRD), use=["SENSOR", "THIRD", "EEPROM"])
        self.assertEqual(self.findings(messages), [("error", "project.yaml", "use.i2c[1]")])
        # two of three used: fits
        config, messages = self.check(buses=(SENSOR, EEPROM, THIRD), use=["THIRD", "SENSOR"])
        self.assertEqual(messages, [])
        self.assertEqual([(p.name, p.tx_shifter, p.scl_timer) for p in config.i2c_buses],
                         [("SENSOR", 0, 0), ("THIRD", 2, 2)])

    def test_missing_entry_is_standard_mode(self):
        """No i2c.<bus>: 100 kHz and the default time limit, no finding - info() names the default."""
        config, messages = self.check(buses=(SENSOR,))
        self.assertEqual(messages, [])
        plan = config.i2c_buses[0]
        self.assertEqual((plan.defaulted, plan.baudrate_hz, plan.scl_hz, plan.timeout_ms), (True, 100_000, 100_000, 20))
        self.assertEqual(nxi2c.info(config), [
            "I2C SENSOR on FlexIO: at most 100000 Hz (no i2c.SENSOR in the project: standard mode) -> SCL 100000 Hz "
            "(8000000 Hz / ((39 + 1) * 2)), time limit 20 ms, at most 13 bytes per transfer, shifters 0/1, "
            "timers 0/1"])

    def test_rate_below_the_slowest_setting(self):
        """8 MHz / 512 = 15625 Hz is the slowest SCL: 15000 Hz is refused (never rounded up), 15625 Hz fits."""
        _, messages = self.check(buses=(SENSOR,), i2c={"SENSOR": {"baudrate_hz": 15000, "timeout_ms": 100}})
        self.assertEqual(self.findings(messages), [("error", "project.yaml", "i2c.SENSOR.baudrate_hz")])
        self.assertIn("15000 Hz is below the slowest SCL rate", messages[0].text)
        self.assertIn("(15625..1333333 Hz)", messages[0].text)
        config, messages = self.check(buses=(SENSOR,), i2c={"SENSOR": {"baudrate_hz": 15625, "timeout_ms": 100}})
        self.assertEqual(messages, [])
        self.assertEqual((config.i2c_buses[0].compare, config.i2c_buses[0].scl_hz), (255, 15625))

    def test_rate_limits(self):
        # above Fast-mode Plus: refused whatever the FlexIO could do
        _, messages = self.check(buses=(SENSOR,), i2c={"SENSOR": {"baudrate_hz": 1_000_001}})
        self.assertEqual(self.findings(messages), [("error", "project.yaml", "i2c.SENSOR.baudrate_hz")])
        self.assertIn("above 1000000 Hz, the fastest I2C bus mode", messages[0].text)
        # 1 MHz (Fast-mode Plus) itself fits: 8 MHz / ((3 + 1) * 2); f/6 limits only slower FlexIO clocks
        config, messages = self.check(buses=(SENSOR,), i2c={"SENSOR": {"baudrate_hz": 1_000_000}})
        self.assertEqual(messages, [])
        self.assertEqual((config.i2c_buses[0].compare, config.i2c_buses[0].scl_hz), (3, 1_000_000))
        # the schema: positive rate, time limit 1..10000 ms
        for value in ({"baudrate_hz": 0}, {"baudrate_hz": 100000, "timeout_ms": 0},
                      {"baudrate_hz": 100000, "timeout_ms": 10001}, {"timeout_ms": 5}):
            with self.subTest(value=value):
                config, messages = self.check(buses=(SENSOR,), i2c={"SENSOR": value})
                self.assertIsNone(config)
                self.assertTrue(any(m.yaml_path.startswith("i2c.SENSOR") for m in messages), messages)

    def test_time_limit_below_the_longest_transfer(self):
        """15 words of 9 SCL periods: 1350 us at 100 kHz - 1 ms is too short, 2 ms is enough."""
        _, messages = self.check(buses=(SENSOR,), i2c={"SENSOR": {"baudrate_hz": 100000, "timeout_ms": 1}})
        self.assertEqual(self.findings(messages), [("error", "project.yaml", "i2c.SENSOR.timeout_ms")])
        self.assertIn("time limit 1 ms is shorter than the longest transfer", messages[0].text)
        self.assertIn("(1350 us for 13 data bytes)", messages[0].text)
        _, messages = self.check(buses=(SENSOR,), i2c={"SENSOR": {"baudrate_hz": 100000, "timeout_ms": 2}})
        self.assertEqual(messages, [])

    def test_unknown_and_unused_entries(self):
        _, messages = self.check(buses=(SENSOR, EEPROM), use=["SENSOR"],
                                 i2c={"SENSOR": {"baudrate_hz": 100000}, "EEPROM": {"baudrate_hz": 400000},
                                      "NOBUS": {"baudrate_hz": 100000}})
        self.assertEqual(self.findings(messages), [("warning", "project.yaml", "i2c.EEPROM"),
                                                   ("error", "project.yaml", "i2c.NOBUS")])
        self.assertIn("i2c EEPROM is not used (use.i2c), setting ignored", messages[0].text)
        self.assertIn("board testboard_s32k1_gates has no i2c entry NOBUS (available: SENSOR, EEPROM)",
                      messages[1].text)

    def test_one_data_pin_for_two_buses(self):
        clash = copy.deepcopy(EEPROM)
        clash["scl"]["flexio_pin"] = 0                          # D0 is SDA of SENSOR (pinmux faked to match)
        (self.mcudb / "s32k1xx" / "TESTK1X.yaml").write_text(
            (self.mcudb / "s32k1xx" / "TESTK1X.yaml").read_text(encoding="utf-8").replace(
                "FlexIO_D0:\n        PTA2: 4", "FlexIO_D0:\n        PTA2: 4\n        PTB3: 4"), encoding="utf-8")
        _, messages = self.check(buses=(SENSOR, clash))
        self.assertEqual(self.findings(messages), [("error", "board.yaml", "i2c[1].scl.flexio_pin")])
        self.assertIn("I2C EEPROM: FlexIO data pin D0 is used by I2C SENSOR as well", messages[0].text)

    def test_an_i2c_instance_gets_no_flexio_plan(self):
        """LPI2C1 of TESTK1X: rate and time limit only; that s32k1xx has no driver for it is TOOL-120's finding."""
        config, messages = self.check(buses=(bus("SENSOR", "PTA2", "PTA3", instance="LPI2C1"),),
                                      i2c={"SENSOR": {"baudrate_hz": 400000}})
        self.assertEqual(messages, [])
        plan = config.i2c_buses[0]
        self.assertEqual((plan.baudrate_hz, plan.scl_timer, plan.compare), (400_000, None, None))
        with self.assertRaises(bswgen.ConfigError):
            bswgen.i2c_context(config)

    def test_family_without_flexio_driver(self):
        """A family missing in FLEXIO_FAMILIES gets no FlexIO plan (nothing invented for it)."""
        config, messages = self.check(buses=(SENSOR,))
        saved = nxi2c.FLEXIO_FAMILIES.pop("s32k1xx")
        try:
            rep = nxconfig.config.Reporter()
            plans = nxi2c.check(config, rep)
        finally:
            nxi2c.FLEXIO_FAMILIES["s32k1xx"] = saved
        self.assertEqual((rep.messages, [p.scl_timer for p in plans]), ([], [None]))


class I2cGeneratorTest(I2cProfile):
    def render(self, config) -> str:
        ctx = {"source": "test", "i2c": bswgen.i2c_context(config)}
        return bswgen.template_env().get_template("i2cConfiguration.h.j2").render(ctx)

    def test_header_of_two_buses(self):
        config, _ = self.check(i2c={"SENSOR": {"baudrate_hz": 400000, "timeout_ms": 5}})
        text = self.render(config)
        self.assertIn("#define NX_I2C_BUS_COUNT 2\n", text)
        self.assertIn("#define NX_I2C_SENSOR_BAUDRATE_HZ 400000U\n", text)
        self.assertIn("#define NX_I2C_SENSOR_SCL_HZ      400000U\n", text)
        self.assertIn("#define NX_I2C_SENSOR_CLOCK_HZ    8000000U\n", text)
        self.assertIn("#define NX_I2C_SENSOR_TIMEOUT_US  5000U\n", text)
        self.assertIn("#define NX_I2C_EEPROM_INDEX       1U\n", text)
        self.assertIn("(no i2c.EEPROM in the project: standard mode)", text)
        rows = re.findall(r"^    X\((.*)\)(?: \\)?$", text, re.M)
        self.assertEqual(rows, [
            'SENSOR, 0U, IP_FLEXIO, NX_CLOCK_FLEXIO_HZ, NX_I2C_SENSOR_BAUDRATE_HZ, NX_I2C_SENSOR_TIMEOUT_US, 0U, 1U, '
            '0U, 1U, 0U, "SENSOR on FlexIO"',
            'EEPROM, 1U, IP_FLEXIO, NX_CLOCK_FLEXIO_HZ, NX_I2C_EEPROM_BAUDRATE_HZ, NX_I2C_EEPROM_TIMEOUT_US, 2U, 3U, '
            '2U, 3U, 2U, "EEPROM on FlexIO"'])

    def test_header_without_buses(self):
        config, messages = self.check(use=[])
        self.assertEqual(messages, [])
        text = self.render(config)
        self.assertIn("#define NX_I2C_BUS_COUNT 0\n", text)
        self.assertIn("#define NX_I2C_BUSES(X)\n", text)
        self.assertNotIn("NX_I2C_SENSOR", text)

    def test_used_buses_in_the_module_integration(self):
        """NX_USE_I2C (CMake) and NX_USE_I2C_<bus> (C) like SPI/DIO/ADC - the family port links its driver by it."""
        config, _ = self.check(use=["EEPROM"])
        cmake = bswmodules.render_cmake(config.integration, config.modules, "test")
        header = bswmodules.render_header(config.integration, config.modules, "test")
        self.assertIn("set(NX_USE_I2C EEPROM)\n", cmake)
        self.assertIn("#define NX_USE_I2C_EEPROM 1\n", header)
        self.assertNotIn("NX_USE_I2C_SENSOR", header)
        config, _ = self.check(use=[])
        self.assertIn("set(NX_USE_I2C )\n", bswmodules.render_cmake(config.integration, config.modules, "test"))

    def test_header_compiles(self):
        """The X macro expands to the FlexioI2cConfig order of the driver (C++17, zig as host compiler)."""
        try:
            import ziglang  # noqa: F401
        except ImportError:
            self.skipTest("ziglang not installed")
        config, _ = self.check(i2c={"SENSOR": {"baudrate_hz": 400000, "timeout_ms": 5}})
        (self.tmp / "gen/bsp/i2c").mkdir(parents=True)
        (self.tmp / "gen/bsp/i2c/i2cConfiguration.h").write_text(self.render(config), encoding="utf-8")
        source = self.tmp / "check.cpp"
        source.write_text(r'''
#include <cstdint>
struct FLEXIO_Type { uint32_t dummy; };
static FLEXIO_Type flexio;
#define IP_FLEXIO (&flexio)
#define NX_CLOCK_FLEXIO_HZ 8000000U
struct Config { FLEXIO_Type* base; uint32_t clock, baud, timeoutUs; uint8_t sda, scl, tx, rx, timer;
                char const* label; };
#include "bsp/i2c/i2cConfiguration.h"
#define ROW(bus, index, regs, clk, baud, t, sda, scl, tx, rx, timer, label) \
    Config{regs, clk, baud, t, sda, scl, tx, rx, timer, label},
static Config const rows[] = {NX_I2C_BUSES(ROW)};
static_assert(sizeof(rows) / sizeof(rows[0]) == NX_I2C_BUS_COUNT, "count");
static_assert(NX_I2C_SENSOR_CLOCK_HZ == NX_CLOCK_FLEXIO_HZ, "clock");
int main() { return (rows[1].tx == 2U && rows[1].timer == 2U && rows[0].timeoutUs == 5000U) ? 0 : 1; }
''', encoding="utf-8")
        exe = self.tmp / "check.exe"
        build = subprocess.run([sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                "-I", str(self.tmp / "gen"), str(source), "-o", str(exe)],
                               capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertEqual(subprocess.run([str(exe)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
