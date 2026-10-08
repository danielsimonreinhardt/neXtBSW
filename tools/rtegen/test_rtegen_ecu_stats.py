"""NVM-05: operating time and reset statistics in tools/rtegen - the reserved NvM block EcuStatistics (created by its
project.yaml entry like DemEvents/DemEntries by the fault memory) and the status values bsw.stats.*,
bsw.stats_mcu.<cause>, bsw.stats_supply.<cause> of the diagnosis (BSW_STATUS, read by ecu/application RteSystem.cpp).

Own profiles, not the eval board: the NvM plan with a stand-in project (nvm.blocks only, synthetic EEPROM window); the
diagnostic values with the ECU description test/diag (synthetic components of test/multitask), the project
buildtest_s32k148_min and identifier 0x0130 of tools/diaggen/standard_diag.yaml. The host test compiles the generated
RTE with stand-ins of the readers (test/ecu_stats/rte_ecu_stats_host_test.cpp), then built-in faults.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_ecu_stats.py
"""

import contextlib
import copy
import io
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import rtegen  # noqa: E402

DIAG_DIR = HERE / "test" / "diag"
STATUS_DIR = HERE / "test" / "diag_status"
STATS_DIR = HERE / "test" / "ecu_stats"
MULTI = HERE / "test" / "multitask"
MULTI_TASKS = MULTI / "tasks.yaml"
PROJECT_FILE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
PROJECT = rtegen.nxconfig.load(PROJECT_FILE)
SYSTEMS = REPO / "ecu" / "application" / "src" / "systems"
STANDARD = yaml.safe_load((REPO / "tools" / "diaggen" / "standard_diag.yaml").read_text(encoding="utf-8"))
STANDARD_DID = next(d for d in STANDARD["data_identifiers"] if d["identifier"] == 0x0130)
NARROW_DID = {"identifier": 0x0131, "name": "EcuStatisticsNarrow", "data": [
    {"name": "operating16", "type": "uint16", "source": {"bsw": "bsw.stats.operating_s"}},
    {"name": "watchdog8", "type": "uint8", "source": {"bsw": "bsw.stats_mcu.watchdog"}}]}


def stats_description() -> dict:
    """test/diag_status/diag.yaml (its first identifiers are connected by test/diag/rte.yaml) + 0x0130/0x0131."""
    data = yaml.safe_load((STATUS_DIR / "diag.yaml").read_text(encoding="utf-8"))
    data["data_identifiers"] = [d for d in data["data_identifiers"] if d["identifier"] in (0x0300, 0x0301)]
    data["data_identifiers"] += [copy.deepcopy(STANDARD_DID), copy.deepcopy(NARROW_DID)]
    return data


def description(data: dict, tmp: Path):
    path = tmp / "diag.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    desc, messages, _ = rtegen.nxconfig.config.read_diag(path)
    assert desc is not None, messages
    return desc


def stand_in(blocks: dict, window: int = 4096):
    """A project with nvm.blocks only and a derivative with an emulated EEPROM window (plan_nvm reads no more)."""
    entries = {name: SimpleNamespace(write=e.get("write"), min_interval_ms=e.get("min_interval_ms"),
                                     address=e.get("address"), integrity=e.get("integrity"), type=None, length=None)
               for name, e in blocks.items()}
    use = SimpleNamespace(blocks=entries, partition=None)
    region = SimpleNamespace(kind=rtegen.NVM_WINDOW_KIND, start=0x14000000, size=window, name="EERAM")
    return SimpleNamespace(project=SimpleNamespace(nvm=use), derivative=SimpleNamespace(memory=[region]))


def crc_id(text: str) -> int:
    ident = rtegen.crc16_ccitt(text.encode())
    return ident ^ 0x5A5A if ident in (0x0000, 0xFFFF) else ident


class EcuStatsBlockTest(unittest.TestCase):
    def plan(self, blocks, buffers=None, window=4096):
        return rtegen.plan_nvm({}, buffers or {}, {}, [], stand_in(blocks, window), None)

    def test_no_entry_no_block(self):
        self.assertIsNone(self.plan({}))

    def test_entry_creates_the_block(self):
        plan = self.plan({"EcuStatistics": {"write": "change", "min_interval_ms": 3600000}})
        self.assertEqual(len(plan.blocks), 1)
        b = plan.blocks[0]
        self.assertEqual((b.name, b.size, b.buffer, b.task, b.index), ("EcuStatistics", 52, None, None, 0))
        self.assertEqual((b.policy, b.min_interval_ms, b.integrity), ("CHANGE", 3600000, "qm"))
        self.assertEqual(b.slot_bytes, 60)
        self.assertEqual(plan.layout_bytes, 120)
        self.assertEqual(b.id, crc_id("EcuStatistics:ecustats:52:v1"))
        self.assertEqual(b.image_decl, "uint8_t nvmImage_EcuStatistics[52] = {};")
        # without write: run-down only (nxconfig warns), ASIL: other record id and size
        plain = self.plan({"EcuStatistics": {}}).blocks[0]
        self.assertEqual((plain.policy, plain.min_interval_ms), ("SHUTDOWN", 0))
        asil = self.plan({"EcuStatistics": {"integrity": "asil_b"}}).blocks[0]
        self.assertEqual(asil.id, crc_id("EcuStatistics:ecustats:52:v1:asil"))
        self.assertEqual(asil.slot_bytes, 64)
        # a fixed place of the project
        placed = self.plan({"EcuStatistics": {"address": 0x200}}).blocks[0]
        self.assertEqual(placed.address, 0x200)

    def test_other_entries_alone_create_nothing(self):
        self.assertIsNone(self.plan({"Trim": {"write": "change", "min_interval_ms": 10}}))

    def test_connection_to_the_reserved_name_refused(self):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.plan({}, buffers={"AppSwc.Stat.Value": SimpleNamespace(nvm="EcuStatistics")})
        self.assertIn("EcuStatistics is the block of the operating time and reset statistics", str(ctx.exception))

    def test_too_small_window(self):
        with self.assertRaises(rtegen.RteError):
            self.plan({"EcuStatistics": {"write": "change", "min_interval_ms": 3600000}}, window=100)


class EcuStatsDiagTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, diag):
        with contextlib.redirect_stderr(io.StringIO()):
            return rtegen.generate(DIAG_DIR / "rte.yaml", PROJECT, self.tmp / "out", REPO, MULTI_TASKS,
                                   diag=description(diag, self.tmp))

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def test_readers_declared_and_called(self):
        self.run_with(stats_description())
        config = self.generated()
        self.assertIn("void readEcuStatistics(uint32_t& stats_operating_s, uint32_t& stats_starts, "
                      "uint8_t& stats_persistent, uint32_t& stats_last_mcu, uint32_t& stats_last_supply);", config)
        for source in ("mcu", "supply"):
            params = ", ".join(f"uint16_t& stats_{source}_{c}" for c in rtegen.STATS_CAUSES)
            read = "readMcuResetCounts" if source == "mcu" else "readSupplyResetCounts"
            self.assertIn(f"void {read}({params});", config)
        header = self.generated("RteDiag.h")
        self.assertIn("bool readDid_EcuStatisticsNarrow(uint16_t& operating16, uint8_t& watchdog8);", header)

    def test_wrong_type_and_unknown_value(self):
        for element, fragment in (({"name": "x", "type": "uint16", "source": {"bsw": "bsw.stats.last_mcu"}},
                                   "bsw.stats.last_mcu"),
                                  ({"name": "x", "type": "uint16", "source": {"bsw": "bsw.stats.uptime"}},
                                   "unknown value uptime of the BSW status stats"),
                                  ({"name": "x", "type": "uint16", "source": {"bsw": "bsw.stats_mcu.brownout"}},
                                   "unknown value brownout of the BSW status stats_mcu")):
            with self.subTest(element=element):
                data = stats_description()
                data["data_identifiers"].append({"identifier": 0x0132, "name": "Bad", "data": [element]})
                with self.assertRaises(rtegen.RteError) as ctx:
                    self.run_with(data)
                self.assertIn(fragment, str(ctx.exception))

    def test_offered_to_every_project(self):
        values = rtegen.diag_bsw_values(PROJECT)
        for name in ["bsw.stats.operating_s", "bsw.stats.starts", "bsw.stats.persistent", "bsw.stats.last_mcu",
                     "bsw.stats.last_supply"] + [f"bsw.stats_{s}.{c}" for s in ("mcu", "supply")
                                                 for c in rtegen.STATS_CAUSES]:
            self.assertIn(name, values)
        self.assertEqual(values["bsw.stats.last_mcu"]["types"], ("uint32",))
        self.assertFalse({"stats", "stats_mcu", "stats_supply"} & set(rtegen.BSW_SERVICES))

    def test_contract_with_the_application(self):
        """Order of the causes = ecustats::Cause; the readers of RteSystem.cpp take them in the generator's order."""
        header = (SYSTEMS / "stats" / "EcuStatistics.h").read_text(encoding="utf-8")
        names = re.search(r'NAMES\[CAUSE_COUNT\]\s*=\s*\{([^}]*)\}', header).group(1)
        self.assertEqual(tuple(re.findall(r'"(\w+)"', names)), rtegen.STATS_CAUSES)
        source = (SYSTEMS / "RteSystem.cpp").read_text(encoding="utf-8")
        for read in ("readMcuResetCounts", "readSupplyResetCounts"):
            params = re.search(rf"void {read}\(([^)]*)\)", source).group(1)
            self.assertEqual(tuple(re.findall(r"uint16_t& (\w+)", params)), rtegen.STATS_CAUSES)
        params = re.search(r"void readEcuStatistics\(([^)]*)\)", source).group(1)
        self.assertEqual(re.findall(r"(\w+)& (\w+)", params),
                         [("uint32_t", "operatingS"), ("uint32_t", "starts"), ("uint8_t", "persistent"),
                          ("uint32_t", "lastMcu"), ("uint32_t", "lastSupply")])
        self.assertEqual([ctype for ctype, _ in rtegen.BSW_STATUS["stats"]["locals"]],
                         ["uint32_t", "uint32_t", "uint8_t", "uint32_t", "uint32_t"])


@unittest.skipUnless(shutil.which("zig") or __import__("importlib").util.find_spec("ziglang"), "ziglang missing")
class RteEcuStatsHostTest(unittest.TestCase):
    def test_runtime(self):
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import bswgen  # noqa: E402

        tmp = Path(tempfile.mkdtemp())
        try:
            out, board = tmp / "gen", tmp / "board"
            desc = description(stats_description(), tmp)
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(DIAG_DIR / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS, diag=desc)
                bswgen.generate(PROJECT_FILE, board)
            zig = [sys.executable, "-m", "ziglang"]
            includes = [f"-I{out}", f"-I{out / 'tasks'}", f"-I{REPO / 'libs/bsw/autosarBase/include'}",
                        f"-I{HERE / 'test' / 'can_host' / 'stubs'}", f"-I{MULTI / 'MotorSwc/generated/src'}",
                        f"-I{MULTI / 'AppSwc/generated/src'}", f"-I{board}"]
            objects = []
            for src in (out / "Rte.c", MULTI / "MotorSwc/generated/src/MotorSwc.c",
                        MULTI / "AppSwc/generated/src/AppSwc.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(zig + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror", *includes, "-c",
                                          str(src), "-o", str(obj)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(str(obj))

            def build_and_run(config_cpp: Path, name: str):
                exe = tmp / f"{name}.exe"
                r = subprocess.run(zig + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
                                          *includes, str(config_cpp), str(STATS_DIR / "rte_ecu_stats_host_test.cpp"),
                                          *objects, "-o", str(exe)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                return subprocess.run([str(exe)], capture_output=True, text=True)

            r = build_and_run(out / "RteConfig.cpp", "rte_ecu_stats_host_test")
            print("\n" + r.stdout.strip(), file=sys.stderr)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertIn(", 0 failures", r.stdout)

            config = (out / "RteConfig.cpp").read_text(encoding="utf-8")
            mutants = {
                "MCU counts swapped": ("mcuPower = static_cast<uint16_t>(bsw_stats_mcu_power);",
                                       "mcuPower = static_cast<uint16_t>(bsw_stats_mcu_reset);"),
                "supply share read as MCU": ("supplyCan = static_cast<uint16_t>(bsw_stats_supply_can);",
                                             "supplyCan = static_cast<uint16_t>(bsw_stats_mcu_can);"),
                "operating time not clamped": ("(bsw_stats_operating_s > 65535U) ? static_cast<uint16_t>(65535)",
                                               "(bsw_stats_operating_s > 65535U) ? static_cast<uint16_t>("
                                               "bsw_stats_operating_s)"),
            }
            for index, (what, (old, new)) in enumerate(mutants.items()):
                with self.subTest(mutant=what):
                    self.assertEqual(config.count(old), 1, what)
                    mutant = out / f"RteConfig_mutant{index}.cpp"
                    mutant.write_text(config.replace(old, new), encoding="utf-8")
                    r = build_and_run(mutant, f"mutant{index}")
                    self.assertNotEqual(r.returncode, 0, f"mutant not detected: {what}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
