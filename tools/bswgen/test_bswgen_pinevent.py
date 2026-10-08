"""Pin-interrupt table and pin events of bswgen (IO-54, doc/architecture/pin-interrupts.md variant A).

bswgen writes the whole table of the family dispatcher (bsp/pinIrq/pinIrqConfiguration.h: Hall pins and pin events,
vectors from the derivative fact pin_interrupts with the NVIC level of the rank), the family neutral table of the pin
events (bsp/pinEvent/pinEventConfiguration.h: PinEventConfig[], Pe_<NAME>, static_assert(isValidConfig)) and the
counts for CMake (bsp/pinIrq/pinIrqConfiguration.cmake). Profiles, none of them changes a checked-in project:

  - a copy of the demo project on the eval board (S32K144, one vector per port) with two pin events on its buttons,
    with and without the Hall sensor of the board on the same vector;
  - the demo project itself: no pin event, no Hall sensor -> empty lists, nothing linked;
  - the own S32K116 profile of the safe outputs (testproject_safe_outputs_k116, one vector PORT for all ports) with a
    Hall sensor and a pin event - the Hall sensor did not build on the S32K11x before (PORT<x>_IRQn per port).

The generated headers are compiled with zig against libs/bsp/bspPinEvent (static_asserts of the table, hand edits).

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_pinevent.py (tools/test -k bswgen-pinevent)
"""

import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import bswgen  # noqa: E402

# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
EVAL_BOARD = REPO / "tools" / "nxconfig" / "test" / "profiles" / "boards" / "testboard_full"   # PLAT-62
K116_BOARD = HERE / "test" / "profiles" / "boards" / "testboard_safe_outputs_k116" / "board.yaml"
K116_PROJECT = HERE / "test" / "profiles" / "projects" / "testproject_safe_outputs_k116" / "project.yaml"
PINEVENT_INCLUDE = REPO / "libs" / "bsp" / "bspPinEvent" / "include"

BUTTONS = {
    # active low on the eval board: activate = falling; debounce -> the detector needs both edges
    "BUTTON1": {"edge": "activate", "debounce_us": 2000, "max_events": 20, "window_ms": 100, "rearm_ms": 500},
    # no debounce: the physical edge of "deactivate" of an active-low input, rising
    "BUTTON2": {"edge": "deactivate", "debounce_us": 0, "max_events": 50, "window_ms": 100, "rearm_ms": 200},
}
EMPTY_RTE = ("schema_version: 2\ntasks: [{ name: swc, period_ms: 10, priority: 30, stack_bytes: 2048 }]\n"
             "components: []\n")
HALL1 = {"arrangement": "deg120", "pole_pairs": 4, "min_speed_rpm": 60, "max_speed_rpm": 6000}


def read(folder: Path, rel: str) -> str:
    return (folder / rel).read_text(encoding="utf-8")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class DemoCopyTest(Tmp):
    """Copy of the full test profile (PLAT-62, testboard_full) with pin events on BUTTON1/BUTTON2 (PTD5/PTD6, vector
    PORTD); its Hall signals lie on PORTD (HALL_UX/HALL_V) and PORTE (HALL_WY)."""

    def project(self, events=None, rank=2, hall=False) -> Path:
        doc = yaml.safe_load(DEMO.read_text(encoding="utf-8"))
        doc["project"]["board"] = EVAL_BOARD.as_posix()
        # TOOL-114: a project on a board of boards/ needs an ECU description; the smallest one
        (self.tmp / "p").mkdir(parents=True, exist_ok=True)
        (self.tmp / "p" / "rte.yaml").write_text(EMPTY_RTE, encoding="utf-8")
        doc["project"]["rte"] = "rte.yaml"
        doc["project"].pop("diag", None)
        (doc.get("safety") or {}).pop("safe_state_output_shared_with_application", None)
        doc["pin_events"] = copy.deepcopy(BUTTONS if events is None else events)
        doc["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": rank} for n in doc["pin_events"]]
        if hall:
            doc["modules"]["HALL1"] = dict(HALL1)
            doc["interrupts"]["ranks"] += [{"source": "dio", "name": n, "rank": rank}
                                           for n in ("HALL_UX", "HALL_V", "HALL_WY")]
        path = self.tmp / "p" / "project.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return path

    def test_table_of_two_pin_events(self):
        board = bswgen.generate(self.project(), self.tmp / "out")
        pins = board.pin_irq["pins"]
        self.assertEqual([(p["name"], p["port_index"], p["pin_number"], p["edge"], p["consumer"], p["channel"])
                          for p in pins],
                         [("BUTTON1", 3, 5, "BOTH", "PIN_EVENT", 0), ("BUTTON2", 3, 6, "RISING", "PIN_EVENT", 1)])
        (vector,) = board.pin_irq["vectors"]
        self.assertEqual((vector["name"], vector["irq"], vector["isr"], vector["ports"], vector["startup"]),
                         ("PORTD", "PORTD_IRQn", "PORTD_IRQHandler", 0x08, 0x08))
        self.assertEqual((vector["rank"], vector["level"]), (2, 7))           # max_syscall_level 5 + rank 2
        header = read(self.tmp / "out", "bsp/pinIrq/pinIrqConfiguration.h")
        self.assertIn("#define NX_PIN_IRQ_PIN_COUNT 2", header)
        self.assertIn("    X(3U, 5U, BOTH, PIN_EVENT, 0U) /* BUTTON1 (PTD5): pin event, vector PORTD */ \\\n"
                      "    X(3U, 6U, RISING, PIN_EVENT, 1U) /* BUTTON2 (PTD6): pin event, vector PORTD */\n", header)
        self.assertIn("#define NX_PIN_IRQ_VECTORS(X) \\\n    X(0, PORTD_IRQn, PORTD_IRQHandler, 0x08U, 7U, 0x08U)\n",
                      header)
        self.assertIn("    X(3U, 5U, true) /* BUTTON1 (PTD5, active low) */ \\\n"
                      "    X(3U, 6U, true) /* BUTTON2 (PTD6, active low) */\n", header)
        cmake = read(self.tmp / "out", "bsp/pinIrq/pinIrqConfiguration.cmake")
        self.assertIn("set(NX_PIN_IRQ_PIN_COUNT 2)", cmake)
        self.assertIn("set(NX_PIN_EVENT_COUNT 2)", cmake)
        events = read(self.tmp / "out", "bsp/pinEvent/pinEventConfiguration.h")
        self.assertIn("#define NX_PIN_EVENTS(X) X(BUTTON1) X(BUTTON2)", events)
        self.assertIn('{"BUTTON1", Edge::ACTIVATE, 2000U, 20U, 100000U, 500000U},', events)
        self.assertIn('{"BUTTON2", Edge::DEACTIVATE, 0U, 50U, 100000U, 200000U},', events)
        self.assertIn("    Pe_BUTTON1 = 0U,\n    Pe_BUTTON2 = 1U,", events)
        self.assertIn("static_assert(isValidConfig(CONFIG[Pe_BUTTON2])", events)
        self.assertIn("edge activate = falling, debounce 2000 us, detector both", events)

    def test_edges_of_the_detector(self):
        """Both while debouncing, else the physical edge of the logical one (board active low)."""
        cases = {("activate", 0): "FALLING", ("deactivate", 0): "RISING", ("both", 0): "BOTH",
                 ("activate", 500): "BOTH", ("deactivate", 500): "BOTH"}
        for (edge, debounce), expected in cases.items():
            with self.subTest(edge=edge, debounce=debounce):
                events = {"BUTTON1": dict(BUTTONS["BUTTON1"], edge=edge, debounce_us=debounce)}
                board = bswgen.generate(self.project(events), self.tmp / "out")
                self.assertEqual(board.pin_irq["pins"][0]["edge"], expected)

    def test_hall_and_pin_events_share_the_port_vector(self):
        board = bswgen.generate(self.project(hall=True), self.tmp / "out")
        self.assertEqual([(p["name"], p["consumer"], p["channel"], p["vector"]) for p in board.pin_irq["pins"]],
                         [("HALL_UX", "HALL", 0, "PORTD"), ("HALL_V", "HALL", 0, "PORTD"),
                          ("HALL_WY", "HALL", 0, "PORTE"), ("BUTTON1", "PIN_EVENT", 0, "PORTD"),
                          ("BUTTON2", "PIN_EVENT", 1, "PORTD")])
        self.assertEqual([(v["index"], v["name"], v["members"]) for v in board.pin_irq["vectors"]],
                         [(0, "PORTD", ["HALL_UX (PTD10)", "HALL_V (PTD11)", "BUTTON1 (PTD5)", "BUTTON2 (PTD6)"]),
                          (1, "PORTE", ["HALL_WY (PTE11)"])])
        header = read(self.tmp / "out", "bsp/pinIrq/pinIrqConfiguration.h")
        self.assertIn("X(3U, 10U, BOTH, HALL, 0U)", header)
        self.assertIn("X(1, PORTE_IRQn, PORTE_IRQHandler, 0x10U, 7U, 0x10U)", header)
        # the Hall header keeps its vectors (from the derivative fact): one per port, bit of the port
        hall = read(self.tmp / "out", "bsp/hall/hallSensorConfiguration.h")
        self.assertIn("X(0, PORTD, PORTD_IRQHandler, 3) X(1, PORTE, PORTE_IRQHandler, 4)", hall)
        self.assertIn("PORTD_IRQn, 7U", hall)

    def test_rank_of_a_vector_is_one(self):
        """nxconfig refuses a pin event with another rank than a Hall signal of its vector; bswgen stops as well."""
        path = self.project(hall=True)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for r in doc["interrupts"]["ranks"]:
            if r["name"] == "BUTTON1":
                r["rank"] = 3
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.generate(path, self.tmp / "out")
        self.assertIn("vector PORTD of S32K144 already serves HALL_UX, HALL_V", str(ctx.exception))

    def test_demo_itself_has_no_pin_interrupt(self):
        board = bswgen.generate(DEMO, self.tmp / "demo")
        self.assertEqual((board.pin_irq, board.pin_events), ({"pins": [], "vectors": []}, []))
        header = read(self.tmp / "demo", "bsp/pinIrq/pinIrqConfiguration.h")
        for line in ("#define NX_PIN_IRQ_PIN_COUNT 0\n#define NX_PIN_IRQ_PINS(X)\n",
                     "#define NX_PIN_IRQ_VECTOR_COUNT 0\n#define NX_PIN_IRQ_VECTORS(X)\n",
                     "#define NX_PIN_EVENT_PINS(X)\n"):
            self.assertIn(line, header)
        events = read(self.tmp / "demo", "bsp/pinEvent/pinEventConfiguration.h")
        self.assertIn("#define NX_PIN_EVENT_COUNT 0\n#define NX_PIN_EVENTS(X)\n", events)
        self.assertNotIn("#include", events)
        self.assertNotIn("CONFIG", events)
        cmake = read(self.tmp / "demo", "bsp/pinIrq/pinIrqConfiguration.cmake")
        self.assertIn("set(NX_PIN_IRQ_PIN_COUNT 0)\nset(NX_PIN_EVENT_COUNT 0)\n", cmake)

    def compile(self, generated: Path, override: Path | None = None):
        source = self.tmp / "tu.cpp"
        source.write_text('#include "bsp/pinEvent/pinEventConfiguration.h"\n'
                          "int main() { return static_cast<int>(::pinevent::project::CONFIG[0].maxEvents) - 20; }\n",
                          encoding="utf-8")
        includes = ([override] if override else []) + [generated, PINEVENT_INCLUDE]
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
               *[f"-I{i}" for i in includes], str(source), "-o", str(self.tmp / "tu.exe")]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_generated_table_compiles_and_refuses_hand_edits(self):
        bswgen.generate(self.project(), self.tmp / "out")
        r = self.compile(self.tmp / "out")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        text = read(self.tmp / "out", "bsp/pinEvent/pinEventConfiguration.h")
        cases = [
            # the debounce no longer shorter than the spacing of the rate limit (2000 us * 20 >= 40 ms)
            ("2000U, 20U, 100000U", "2000U, 20U, 40000U", "pin event BUTTON1: ranges or debounce"),
            # a count that does not match the table
            ("#define NX_PIN_EVENT_COUNT 2", "#define NX_PIN_EVENT_COUNT 3", "NX_PIN_EVENT_COUNT"),
            # no rate limit at all
            ("0U, 50U, 100000U", "0U, 0U, 100000U", "pin event BUTTON2: ranges or debounce"),
        ]
        for old, new, message in cases:
            with self.subTest(new=new):
                self.assertIn(old, text)
                patched = self.tmp / "patched" / "bsp" / "pinEvent"
                patched.mkdir(parents=True, exist_ok=True)
                (patched / "pinEventConfiguration.h").write_text(text.replace(old, new, 1), encoding="utf-8")
                r = self.compile(self.tmp / "out", self.tmp / "patched")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)


class S32K11xTest(Tmp):
    """Own S32K116 profile (Cortex-M0+, one pin interrupt vector PORT for all ports): Hall sensor and pin event."""

    INPUTS = [{"name": "HALL_U", "pin": "PTA2"}, {"name": "HALL_V", "pin": "PTA3"},
              {"name": "HALL_W", "pin": "PTD0"}, {"name": "KEY", "pin": "PTE4", "active": "high"}]

    def write(self, key_rank=0) -> Path:
        board = yaml.safe_load(K116_BOARD.read_text(encoding="utf-8"))
        board["dio"]["inputs"] += copy.deepcopy(self.INPUTS)
        board["devices"].append({"name": "MOT", "part": "hall3", "module": "hall",
                                 "connect": {"u": "HALL_U", "v": "HALL_V", "w": "HALL_W"}})
        project = yaml.safe_load(K116_PROJECT.read_text(encoding="utf-8"))
        project["modules"]["MOT"] = dict(HALL1)
        project["pin_events"] = {"KEY": {"edge": "both", "debounce_us": 0, "max_events": 10, "window_ms": 10,
                                         "rearm_ms": 100}}
        project["interrupts"] = {"ranks": [{"source": "dio", "name": n, "rank": 0}
                                           for n in ("HALL_U", "HALL_V", "HALL_W")]
                                 + [{"source": "dio", "name": "KEY", "rank": key_rank}]}
        board_dir, project_dir = self.tmp / "boards" / "b", self.tmp / "projects" / "p"
        board_dir.mkdir(parents=True)
        project_dir.mkdir(parents=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project["project"]["board"] = "../../boards/b"
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return path

    def test_one_vector_for_all_ports(self):
        board = bswgen.generate(self.write(), self.tmp / "out")
        (vector,) = board.pin_irq["vectors"]
        self.assertEqual((vector["name"], vector["irq"], vector["isr"], vector["ports"], vector["startup"]),
                         ("PORT", "PORT_IRQn", "PORT_IRQHandler", 0x1F, 0))
        self.assertEqual(vector["members"], ["HALL_U (PTA2)", "HALL_V (PTA3)", "HALL_W (PTD0)", "KEY (PTE4)"])
        self.assertEqual([(p["port_index"], p["pin_number"], p["edge"]) for p in board.pin_irq["pins"]],
                         [(0, 2, "BOTH"), (0, 3, "BOTH"), (3, 0, "BOTH"), (4, 4, "BOTH")])
        header = read(self.tmp / "out", "bsp/pinIrq/pinIrqConfiguration.h")
        self.assertIn("X(0, PORT_IRQn, PORT_IRQHandler, 0x1FU, ", header)
        # the Hall header names the vector of the derivative, not PORTA_IRQn/PORTD_IRQn (which the S32K116 lacks)
        hall = read(self.tmp / "out", "bsp/hall/hallSensorConfiguration.h")
        self.assertNotIn("PORTA_IRQn", hall)
        self.assertNotIn("PORTD_IRQn", hall)
        self.assertIn("PORT_IRQn", hall)
        self.assertIn('{"KEY", Edge::BOTH, 0U, 10U, 10000U, 100000U},',
                      read(self.tmp / "out", "bsp/pinEvent/pinEventConfiguration.h"))

    def test_one_vector_one_rank(self):
        """All ports share the vector PORT, so a pin event on PORTE needs the rank of the Hall signals on PORTA."""
        with self.assertRaises(bswgen.ConfigError) as ctx:
            bswgen.generate(self.write(key_rank=1), self.tmp / "out")
        self.assertIn("vector PORT of S32K116 already serves", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
