"""Flash reserve of the bootloader in every build profile with a bootloader (BL-30).

The bootloader area is a project value (project memory.bootloader.size); the application starts right behind it, so
the area cannot grow without moving the application start (tools/canflash, updater, bootloader update). Before BL-30
the S32K116 profile had 92 bytes left of its 10 KB - the next change to the bootloader core would have ended in a
link error on a profile nobody was building. This test builds the bootloader of every profile and fails when less
than RESERVE_MIN bytes are left between the last loaded byte and the application start.

Profiles: every configure preset of CMakePresets.json that names a project with `memory:`/`bootloader` of its own
(not the derived worktree, ethernet or threadx presets). NX_BL_RESERVE_PRESETS=<preset>,<preset> limits the run.

Per profile it also checks what BL-30 changed in the s32k1xx linker script:
  * ARMv6-M: the vector table has 48 entries (32 external interrupts, the architectural maximum) and the gap up to
    the flash configuration field at 0x400 holds code (.text_gap); ARMv7-M: 256 entries, .text_gap is empty.
  * reset, NMI and HardFault vector point behind the FCF - the updater (upd_family_check_image.inc) and
    tools/canflash reject a bootloader image otherwise.

The measured values are printed as a table (memory report, see PLAT-29).

Needs cmake, ninja, arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN) and external/openbsw.
Run: .venv/Scripts/python -m unittest bootloader/test/test_bl_flash_reserve.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

RESERVE_MIN = 512  # bytes left in the bootloader area of every profile
FCF_ADDR = 0x400
FCF_SIZE = 16
VECTOR_ENTRIES = {"armv6m": 16 + 32, "armv7m": 16 + 240}

CMAKE = shutil.which("cmake")
NINJA = shutil.which("ninja")
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
OPENBSW = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "CMakeLists.txt"
# the eval board project stays private (PLAT-46 F10): checked where it exists, the open ARMv7-M build profile
# buildtest_s32k148_min always (TOOL-245: the exported open tree has no eval board)
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
BUILDTEST_PROJECT = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
K116_PROJECT = REPO / "projects" / "s32k116evb_minimal" / "project.yaml"


def arm_tool(name: str) -> str:
    return str(Path(ARM_GCC).with_name(Path(ARM_GCC).name.replace("gcc", name)))


def bootloader_presets() -> dict[str, Path]:
    """Configure presets that name a project with a bootloader area, first preset per project."""
    presets = json.loads((REPO / "CMakePresets.json").read_text(encoding="utf-8"))["configurePresets"]
    found: dict[str, Path] = {}
    for preset in presets:
        project = preset.get("cacheVariables", {}).get("NX_PROJECT")
        if preset.get("hidden") or not project:
            continue
        path = Path(project.replace("${sourceDir}", str(REPO)))
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if re.search(r"^memory:", text, re.M) and re.search(r"^\s+bootloader:", text, re.M):
            if path not in found.values():
                found[preset["name"]] = path
    wanted = [p for p in os.environ.get("NX_BL_RESERVE_PRESETS", "").split(",") if p]
    return {name: path for name, path in found.items() if not wanted or name in wanted}


def header_values(path: Path) -> dict[str, int | str]:
    values: dict[str, int | str] = {}
    for m in re.finditer(r"^#define (\w+)\s+(0x[0-9A-Fa-f]+|\d+)UL?\b|^#define (\w+)\s+\"([^\"]*)\"", path.read_text(
            encoding="utf-8"), re.M):
        if m.group(1):
            values[m.group(1)] = int(m.group(2), 0)
        else:
            values[m.group(3)] = m.group(4)
    return values


def architecture(settings: Path) -> str:
    m = re.search(r'set\(NX_CORE_ARCHITECTURE "?(\w+)"?', settings.read_text(encoding="utf-8"))
    return m.group(1) if m else "?"


def sections(elf: Path) -> dict[str, tuple[int, int]]:
    out = subprocess.run([arm_tool("readelf"), "-SW", str(elf)], capture_output=True, text=True, check=True).stdout
    result = {}
    for m in re.finditer(r"\]\s+(\S+)\s+\S+\s+([0-9a-f]{8})\s+[0-9a-f]+\s+([0-9a-f]+)", out):
        result[m.group(1)] = (int(m.group(2), 16), int(m.group(3), 16))
    return result


def load_end(elf: Path, flash_start: int, flash_end: int) -> int:
    """End of the last byte that goes into the flash (load addresses of the program headers)."""
    out = subprocess.run([arm_tool("readelf"), "-lW", str(elf)], capture_output=True, text=True, check=True).stdout
    end = flash_start
    for m in re.finditer(r"^\s*LOAD\s+0x[0-9a-f]+\s+0x[0-9a-f]+\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)", out, re.M):
        phys, size = int(m.group(1), 16), int(m.group(2), 16)
        if size and flash_start <= phys < flash_end:
            end = max(end, phys + size)
    return end


class SectionsGeneratorTest(unittest.TestCase):
    """bootloader/bl_sections.ld from tools/bswgen/blgen.py per core architecture - no toolchain needed."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import blgen
        cls.blgen = blgen

    def sections(self, project: Path) -> str:
        return self.blgen.board_files(self.blgen.nxconfig.load(project))[self.blgen.BOARD_SECTIONS]

    def test_armv6m_fills_the_gap(self):
        text = self.sections(K116_PROJECT)
        self.assertIn("(armv6m)", text)
        self.assertIn(".text_gap :", text)
        self.assertIn("> VECTORS", text)
        # the files with the vectors the updater checks stay behind the FCF
        self.assertIn("EXCLUDE_FILE(*startup.S.* *hal_s32k1xx.c.*)", text)

    def test_armv7m_has_no_statement(self):
        """Test profile S32K142 (Cortex-M4F), the build profile S32K148 and the eval board (where the tree has it):
        comment only, the ARMv7-M image stays as it was."""
        for project in (REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_s32k142_bl"
                        / "project.yaml", BUILDTEST_PROJECT,
                        *([EVAL_DEMO_PROJECT] if EVAL_DEMO_PROJECT.is_file() else [])):
            with self.subTest(project=project.parent.name):
                text = self.sections(project)
                self.assertIn("(armv7m)", text)
                self.assertNotIn(".text_gap", text)
                self.assertEqual(re.sub(r"/\*.*?\*/", "", text, flags=re.S).strip(), "")

    def test_linker_script_includes_it(self):
        text = (REPO / "bootloader" / "platforms" / "s32k1xx" / "linker.ld").read_text(encoding="utf-8")
        self.assertIn("INCLUDE bl_sections.ld", text)
        self.assertIn("Reset_Handler >= ORIGIN(FLASH) && Fault_Handler >= ORIGIN(FLASH)", text)


@unittest.skipIf(None in (CMAKE, NINJA, ARM_GCC) or not OPENBSW.is_file(),
                 "cmake, ninja, arm-none-eabi-gcc (NX_ARM_GCC_BIN) or external/openbsw missing")
class BootloaderFlashReserveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bl_reserve_"))
        cls.env = {**os.environ, "PATH": GCC_PATH, "PYTHONUTF8": "1"}
        cls.results: dict[str, dict] = {}
        for preset, project in bootloader_presets().items():
            build = cls.tmp / preset
            r = subprocess.run([CMAKE, "--preset", preset, "-B", str(build), f"-DPython3_EXECUTABLE={sys.executable}"],
                               cwd=REPO, capture_output=True, text=True, env=cls.env)
            if r.returncode == 0:
                r = subprocess.run([CMAKE, "--build", str(build), "--config", "RelWithDebInfo", "--target",
                                    "bootloader"], capture_output=True, text=True, env=cls.env)
            cls.results[preset] = {"project": project, "build": build, "run": r}
        cls.report()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def measure(cls, preset: str) -> dict | None:
        entry = cls.results[preset]
        if "values" in entry:
            return entry["values"]
        build = entry["build"]
        elfs = sorted(build.glob("bootloader/*/bootloader.elf"))
        if entry["run"].returncode != 0 or not elfs:
            return None
        memory = header_values(build / "generated" / "board" / "bootloader" / "bl_memory.h")
        settings = build / "generated" / "nxconfig" / "project.cmake"
        secs = sections(elfs[0])
        start, app = int(memory["BL_FLASH_START"]), int(memory["BL_APP_START"])
        end = load_end(elfs[0], start, app)
        entry["values"] = {
            "elf": elfs[0], "family": memory.get("BL_FAMILY", "?"), "arch": architecture(settings),
            "start": start, "app": app, "end": end, "reserve": app - end,
            "vectors": secs.get(".isr_vector", (0, 0))[1], "gap": secs.get(".text_gap", (0, 0))[1],
        }
        return entry["values"]

    @classmethod
    def report(cls):
        lines = [f"\nbootloader flash reserve (minimum {RESERVE_MIN} bytes):",
                 f"  {'preset':<28} {'arch':<7} {'area':>6} {'used':>6} {'free':>6} {'vectors':>7} {'gap code':>8}"]
        for preset in cls.results:
            v = cls.measure(preset)
            if v is None:
                lines.append(f"  {preset:<28} build failed")
                continue
            lines.append(f"  {preset:<28} {v['arch']:<7} {v['app'] - v['start']:>6} {v['end'] - v['start']:>6} "
                         f"{v['reserve']:>6} {v['vectors']:>7} {v['gap']:>8}")
        print("\n".join(lines), file=sys.stderr)

    def test_builds_without_warnings(self):
        self.assertTrue(self.results, "no preset with a bootloader found")
        for preset, entry in self.results.items():
            with self.subTest(preset=preset):
                run = entry["run"]
                self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-4000:])
                self.assertNotIn("warning", (run.stdout + run.stderr).lower())

    def built(self) -> list[tuple[str, dict]]:
        """Measured profiles; a profile whose build failed is reported by test_builds_without_warnings."""
        return [(preset, v) for preset in self.results if (v := self.measure(preset)) is not None]

    def test_profiles_found(self):
        if os.environ.get("NX_BL_RESERVE_PRESETS"):
            self.skipTest("NX_BL_RESERVE_PRESETS limits the presets")
        names = set(bootloader_presets())
        # the smallest bootloader area (S32K116, 10 KB), an ARMv7-M build profile (S32K148) and the eval board (where
        # the tree has it, TOOL-245) must be part of the check
        # TOOL-243c: the S32K116EVB profile is private until PLAT-13 and missing in the exported open tree
        if K116_PROJECT.is_file():
            self.assertIn("s32k116evb-minimal", names)
        self.assertIn("buildtest-s32k148-min", names)
        if EVAL_DEMO_PROJECT.is_file():
            self.assertIn("evalboard-s32k144-demo", names)

    def test_reserve(self):
        for preset, v in self.built():
            with self.subTest(preset=preset):
                self.assertGreaterEqual(
                    v["reserve"], RESERVE_MIN,
                    f"{preset}: only {v['reserve']} bytes left in the bootloader area "
                    f"(0x{v['start']:X}..0x{v['app']:X}), "
                    f"at least {RESERVE_MIN} needed (BL-30: size optimisation, deselectable parts, or a larger area - "
                    "the latter moves the application start)")

    def test_vector_table_and_gap(self):
        for preset, v in self.built():
            with self.subTest(preset=preset):
                if v["family"] != "s32k1xx":
                    continue  # the rules below are those of the s32k1xx linker script
                expected = VECTOR_ENTRIES.get(v["arch"])
                self.assertIsNotNone(expected, f"{preset}: architecture {v['arch']}")
                self.assertEqual(v["vectors"], 4 * expected)
                free_gap = FCF_ADDR - v["vectors"]
                if free_gap:
                    # filled up to the size of one more function at most
                    self.assertGreater(v["gap"], free_gap // 2, f"{preset}: gap in front of the FCF not used")
                self.assertLessEqual(v["vectors"] + v["gap"], FCF_ADDR)

    def test_checked_vectors_behind_fcf(self):
        """What upd_check_blob() and tools/canflash check: reset, NMI and HardFault behind the FCF."""
        for preset, v in self.built():
            with self.subTest(preset=preset):
                if v["family"] != "s32k1xx":
                    continue  # the rules below are those of the s32k1xx linker script
                image = subprocess.run([arm_tool("objcopy"), "-O", "binary", "-j", ".isr_vector", str(v["elf"]),
                                        str(v["elf"].with_suffix(".vec"))], capture_output=True, text=True)
                self.assertEqual(image.returncode, 0, image.stderr)
                data = v["elf"].with_suffix(".vec").read_bytes()
                for index, name in ((1, "reset"), (2, "NMI"), (3, "HardFault")):
                    address = int.from_bytes(data[4 * index:4 * index + 4], "little")
                    self.assertEqual(address & 1, 1, f"{preset}: {name} vector not Thumb")
                    self.assertGreaterEqual(address & ~1, FCF_ADDR + FCF_SIZE,
                                            f"{preset}: {name} vector in front of the FCF")
                    self.assertLess(address & ~1, v["end"])


if __name__ == "__main__":
    unittest.main()
