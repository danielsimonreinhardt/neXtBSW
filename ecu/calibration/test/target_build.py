# SPDX-License-Identifier: Apache-2.0
"""Target builds of the calibration segment (SYS-89) in the development and the production profile.

The profiles have no project of their own: this script derives them from projects of the tree with
memory.calibration added (paths made absolute, written to <build>/<profile>/project/project.yaml):

    demo-dev     evalboard_s32k144_demo (S32K144, DemoSwc, QM), PFLASH 4 KB, on_invalid defaults, development
    demo-prod    the same, profile production, on_invalid warn, without the xcp section
    calib-dev    buildtest_s32k148_min (S32K148, no SBC) with the RTE-26 test profile tools/rtegen/test/calib
                 (C component CalMapSwc, three asil_b parameters) and its calibration dataset (TOOL-145, copied
                 next to the derived project, so the build takes it), PFLASH 4 KB, on_invalid refuse, development
    calib-prod   the same, profile production, without the xcp section
    qm-dev       buildtest_s32k148_min with its own rte.yaml (DemoSwc, QM), PFLASH 4 KB, on_invalid defaults,
                 development; qm-prod the same as production, on_invalid warn, without the xcp section. Built in
                 place of demo-dev/demo-prod where the private eval board project is missing (exported open tree,
                 TOOL-245); by name in any tree

Without profile names the script builds demo-dev, demo-prod (or qm-dev, qm-prod), calib-dev and calib-prod.

demo-dev/demo-prod also derive a copy of the demo's diag.yaml with a DTC for event.bsw calibration.segment added
(SYS-101, CALIBRATION_DTC below): without it the checked-in diag.yaml would leave RteSystem.reportCalibrationToDem()
silent once memory.calibration exists, but the real demo project stays QM-only and does not get that DTC itself.

Checked per profile: configure and build pass, the build log has no warning; the map file has the output section
.nx_cal at the address tools/nxconfig computed (behind the first application sector) and inside the configured size,
and the application code behind it; the sealed header passes the start-up check (tools/calseg: layout identifier of
RteCalibration.json, dataset identifier = identifier of the values of the dataset or 0, length, CRC) in the ELF, in
app.ecu.hex (the download) and in the segment hex app.ecu.cal.hex; with a dataset also the segment hex of its variant
(tools/calseg hex --dataset, without a new build);
SYS-90: the calib profiles carry the generated check table rte::PARAMETER_CHECKS, the check code and the report
RteParameterChecks.md (one row per asil_b parameter, values of the dataset), the development profile checks XCP
writes (calibration::plausibility::acceptWrite); the QM demo has none of it;
every parameter of rte::PARAMETERS lies in the RAM working page (development) or in the flash segment (production),
and no parameter storage is left in .data/.bss; SYS-91: the ROM check list has five areas clear of the segment,
whose region is the area with its own reference (romCheckImage.py and tools/elfcheck accept it), the download image
with the segment of the variant sport or with a flipped header bit (tools/calseg merge) keeps the build reference
valid while the start-up check says VALID resp. HEADER_CRC, and the image carries the run-time check
(calibration::recheckSegment, RomCheck::setOwnAreaCheck);
development: the A2L is formally valid (tools/a2lgen/a2lcheck.py) and its CHARACTERISTIC/AXIS_PTS addresses lie in
the working page; production: no XCP in the image, no A2L. Across the two profiles of a project the values of the
segment are byte for byte the same (only the addresses of the parameters differ).

    python ecu/calibration/test/target_build.py <build directory> [profile ...]

Needs cmake, ninja and arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools" / "calseg"))
sys.path.insert(0, str(REPO / "tools" / "a2lgen"))

sys.path.insert(0, str(REPO / "platforms" / "s32k1xx" / "port" / "safety" / "safeMemory" / "tools"))

import calseg  # noqa: E402
import nxconfig  # noqa: E402
import romCheckImage as rc  # noqa: E402
from elf32 import Elf32  # noqa: E402
from elfcheck import check as elfcheck, elffile  # noqa: E402

DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
S32K148 = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
CALIB_RTE = REPO / "tools" / "rtegen" / "test" / "calib" / "rte.yaml"

PROFILES = {
    "demo-dev": (DEMO, "evalboard-s32k144-demo", None,
                 {"region": "PFLASH", "size": 0x1000, "on_invalid": "defaults", "profile": "development"}),
    "demo-prod": (DEMO, "evalboard-s32k144-demo", None,
                  {"region": "PFLASH", "size": 0x1000, "on_invalid": "warn", "profile": "production"}),
    "calib-dev": (S32K148, "buildtest-s32k148-min", CALIB_RTE,
                  {"region": "PFLASH", "size": 0x1000, "on_invalid": "refuse", "profile": "development"}),
    "calib-prod": (S32K148, "buildtest-s32k148-min", CALIB_RTE,
                   {"region": "PFLASH", "size": 0x1000, "on_invalid": "refuse", "profile": "production"}),
    # TOOL-245: the QM pair of the demo on the open build profile (S32K148, its own rte.yaml with DemoSwc), for a
    # tree without the private eval board project (the exported open tree); same calibration values as demo-*
    "qm-dev": (S32K148, "buildtest-s32k148-min", None,
               {"region": "PFLASH", "size": 0x1000, "on_invalid": "defaults", "profile": "development"}),
    "qm-prod": (S32K148, "buildtest-s32k148-min", None,
                {"region": "PFLASH", "size": 0x1000, "on_invalid": "warn", "profile": "production"}),
}


def default_profiles() -> list[str]:
    """Without names: demo, calib - and the QM pair qm-* in place of demo-* where the eval board project is missing
    (it stays private, PLAT-46 F10), so the QM path (no check table, defaults/warn) is built in every tree."""
    qm = ["demo-dev", "demo-prod"] if DEMO.is_file() else ["qm-dev", "qm-prod"]
    return qm + ["calib-dev", "calib-prod"]


DATASETS = {"calib-dev": CALIB_RTE.parent / "calibration", "calib-prod": CALIB_RTE.parent / "calibration"}

# SYS-101 (nxconfig, tools/nxconfig/calibration.py): as soon as a derived project has memory.calibration, its
# diag.yaml needs a DTC with event.bsw calibration.segment, or RteSystem.reportCalibrationToDem() finds nothing
# and stays silent. The checked-in projects/evalboard_s32k144_demo/diag.yaml stays untouched (the real demo is
# QM-only and has no segment of its own, see doc/changelog.d/SYS-101.md); demo-dev/demo-prod derive a copy of it
# with this DTC added instead. Same operation cycle and enable condition as the demo's 0x904001 template
# (PowerCycle/EcuRunning); debounce matures on the single report reportCalibrationToDem() makes at start-up
# (SYS-102: failed_threshold 1, passed_threshold -1, the same template as tools/nxconfig/test_calibration.py
# calibration_dtc()).
CALIBRATION_DTC = {
    "number": 0xF00001,
    "name": "CalibrationSegment",
    "label": "Calibration segment invalid",
    "description": "SYS-89 header, layout identifier or CRC of the calibration segment do not fit the "
                   "application (RteSystem.reportCalibrationToDem, SYS-101).",
    "group": "measurement",
    "severity": "check_at_next_halt",
    "operation_cycle": "PowerCycle",
    "enable_conditions": ["EcuRunning"],
    "confirmation_threshold": 1,
    "priority": 2,
    "event": {"bsw": "calibration.segment"},
    "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}},
}


def project_file(build: Path, name: str) -> Path:
    base, _, rte, calibration = PROFILES[name]
    yaml = YAML(typ="rt")
    yaml.width = 4096
    data = yaml.load(base.read_text(encoding="utf-8"))
    data["project"]["name"] = f"sys89_{name.replace('-', '_')}"
    data["project"]["board"] = (base.parent / data["project"]["board"]).resolve().as_posix()
    for key in ("rte", "diag"):
        if key in data["project"]:
            data["project"][key] = (base.parent / data["project"][key]).resolve().as_posix()
    if rte is not None:
        data["project"]["rte"] = rte.resolve().as_posix()
    data["project"]["description"] = f"SYS-89 target build {name}: calibration segment (no hardware)"
    data["memory"]["calibration"] = dict(calibration)
    if calibration["profile"] == "production":
        data.pop("xcp", None)
    target = build / "project" / "project.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    if "diag" in data["project"]:
        diag_source = Path(data["project"]["diag"])
        diag_data = yaml.load(diag_source.read_text(encoding="utf-8"))
        diag_data.setdefault("dtcs", []).append(dict(CALIBRATION_DTC))
        diag_target = target.parent / "diag.yaml"
        with diag_target.open("w", encoding="utf-8", newline="\n") as out:
            yaml.dump(diag_data, out)
        data["project"]["diag"] = diag_target.as_posix()
    dataset = target.parent / "calibration"
    if dataset.exists():
        shutil.rmtree(dataset)
    if name in DATASETS:
        shutil.copytree(DATASETS[name], dataset)
    with target.open("w", encoding="utf-8", newline="\n") as out:
        yaml.dump(data, out)
    return target


def run(argv: list[str], env: dict) -> str:
    print("$", subprocess.list2cmdline(argv), flush=True)
    result = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print(output[-8000:])
        raise SystemExit(f"FAILED ({result.returncode}): {argv[0]} {argv[1]}")
    return output


def map_sections(map_file: Path) -> dict[str, tuple[int, int]]:
    """Output sections of the link map: name -> (address, size)."""
    sections = {}
    text = map_file.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"^(\.[\w.]+)\s*\n?\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)", text, re.M):
        sections.setdefault(m.group(1), (int(m.group(2), 16), int(m.group(3), 16)))
    return sections


def a2l_addresses(a2l: Path) -> list[tuple[str, int]]:
    """(name, ECU address) of every CHARACTERISTIC and AXIS_PTS."""
    found = []
    text = a2l.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"/begin CHARACTERISTIC\s+(\S+)\s+\"[^\"]*\"\s+\w+\s+(0x[0-9A-Fa-f]+)", text):
        found.append((m.group(1), int(m.group(2), 16)))
    for m in re.finditer(r"/begin AXIS_PTS\s+(\S+)\s+\"[^\"]*\"\s+(0x[0-9A-Fa-f]+)", text):
        found.append((m.group(1), int(m.group(2), 16)))
    return found


def check_rom_image(name: str, elf_path: Path, hex_path: Path, layout, seg, failures: list[str]) -> None:
    """SYS-91: romCheckImage.py and tools/elfcheck on an image with calibration segment."""
    try:
        check = rc.check_image(rc.read_hex(hex_path), rc.symbols_of(elf_path))
    except rc.RomCheckError as exc:
        failures.append(f"{name}: ROM check of {hex_path.name}: {exc}")
        return
    size = calseg.HEADER_SIZE + seg.length
    touching = [a for a in check.areas if a[0] < seg.segment + size and seg.segment < a[1]]
    if len(check.areas) != 5 or touching or check.own_area != (layout.start, layout.end):
        failures.append(f"{name}: ROM check list {check.areas}, own area {check.own_area}, expected five areas "
                        f"clear of the segment and the own area 0x{layout.start:08X}..0x{layout.end:08X}")
    if check.stored != check.crc:
        failures.append(f"{name}: ROM check of {hex_path.name}: reference 0x{check.stored or 0:08X}, CRC over the "
                        f"areas 0x{check.crc:08X} - SM-14 would hold the ECU")
    findings = elfcheck.Findings()
    elfcheck.check_rom_list(elffile.load(elf_path), None, findings)
    if findings.errors:
        failures.append(f"{name}: elfcheck: " + "; ".join(f.message for f in findings.errors))


def check_profile(name: str, build: Path, env: dict, failures: list[str]) -> bytes | None:
    base, preset, _, calibration = PROFILES[name]
    project = project_file(build, name)
    config, messages = nxconfig.check(project)
    errors = [str(m) for m in messages if m.severity == "error"]
    if errors:
        failures.append(f"{name}: nxconfig: " + "; ".join(errors))
        return None
    layout = config.memory.calibration
    sys.path.insert(0, str(REPO / "tools" / "test"))
    from build_dirs import fresh_args
    fresh, stale = fresh_args(build, env["PATH"])
    if stale:
        print(f"configured fresh: {stale}", flush=True)
    run(["cmake", "--preset", preset, *fresh, "-B", str(build), f"-DNX_PROJECT={project.as_posix()}",
         f"-DPython3_EXECUTABLE={sys.executable}"], env)
    # relink every time: a changed linker template alone does not relink app.ecu (LINK_DEPENDS sits on startUp),
    # and the seal step runs only after a link
    for old in build.glob("**/RelWithDebInfo/app.ecu.elf"):
        old.unlink()
    log = run(["cmake", "--build", str(build), "--config", "RelWithDebInfo"], env)
    warnings = [line for line in log.splitlines() if re.search(r"\bwarning\b", line, re.I)]
    if warnings:
        failures.append(f"{name}: {len(warnings)} warning(s) in the build log:\n" + "\n".join(warnings[:30]))
    if "Calibration segment: header into" not in log and "calseg:" not in log:
        failures.append(f"{name}: the seal step did not run")

    elf_path = next(iter(sorted(build.glob("**/RelWithDebInfo/app.ecu.elf"))), None)
    if elf_path is None:
        failures.append(f"{name}: app.ecu.elf missing")
        return None
    out = elf_path.parent
    elf = Elf32(elf_path)
    seg = calseg.layout_of(elf)
    production = calibration["profile"] == "production"
    if seg.segment != layout.start:
        failures.append(f"{name}: segment at 0x{seg.segment:08X}, nxconfig placed it at 0x{layout.start:08X}")
    if seg.production != production:
        failures.append(f"{name}: profile of the image {'production' if seg.production else 'development'}")

    # map file: .nx_cal at the start of the segment, inside its size, the application code behind it
    map_file = next(iter(sorted(build.glob("**/application.map"))), None)
    if map_file is None:
        failures.append(f"{name}: application.map missing")
    else:
        sections = map_sections(map_file)
        address, size = sections.get(".nx_cal", (None, None))
        if address != layout.start or size is None or size > layout.size or size != calseg.HEADER_SIZE + seg.length:
            failures.append(f"{name}: map: .nx_cal at {address} size {size}, expected 0x{layout.start:08X} with "
                            f"{calseg.HEADER_SIZE + seg.length} bytes inside 0x{layout.size:X}")
        first = sections.get(".first", (0, 0))[0]
        if first != layout.end:
            failures.append(f"{name}: map: application code (.first) at 0x{first:08X}, expected behind the segment "
                            f"at 0x{layout.end:08X}")
        text = map_file.read_text(encoding="utf-8", errors="replace")
        stray = re.findall(r"^ \.(?:data|bss)\.Rte_Prm_\w+\s*\n?\s+0x([0-9a-f]+)", text, re.M)
        low, high = (seg.values, seg.values + seg.length) if production else (seg.ram, seg.ram + seg.length)
        outside = [a for a in stray if not low <= int(a, 16) < high]
        if not stray or outside:
            failures.append(f"{name}: map: parameter storage outside the page: {outside or 'none found'}")

    # the sealed segment in the ELF, the download and the segment hex
    description = next(iter(sorted(build.glob("**/generated/rte/RteCalibration.json"))))
    import json
    content = json.loads(description.read_text(encoding="utf-8"))
    layout_id = int(content["layout"], 16)
    segment = elf.read(seg.segment, calseg.HEADER_SIZE + seg.length)
    status = calseg.check(segment, layout_id, seg.length)
    if status != "VALID":
        failures.append(f"{name}: sealed segment in the ELF: {status}")
    dataset_id = int(content["dataset"]["values"], 16) if name in DATASETS else 0
    if name in DATASETS and "dataset" not in content:
        failures.append(f"{name}: the build did not take the calibration dataset")
    elif calseg.read_header(segment).dataset != dataset_id:
        failures.append(f"{name}: header names dataset {calseg.read_header(segment).dataset:08x}, expected "
                        f"{dataset_id:08x}")
    for hex_name in ("app.ecu.hex", "app.ecu.cal.hex"):
        hex_path = out / hex_name
        if not hex_path.is_file():
            failures.append(f"{name}: {hex_name} missing")
            continue
        in_hex = calseg.read_hex(hex_path, seg.segment, len(segment))
        if in_hex != segment:
            failures.append(f"{name}: {hex_name} does not carry the sealed segment")
    rte_config = description.with_name("RteConfig.h")
    if name in DATASETS:
        # the variant of the dataset as segment hex for this image, without a new build (BL-31 will flash it)
        variant_hex = out / "app.ecu.cal.sport.hex"
        run([sys.executable, str(REPO / "tools" / "calseg" / "calseg.py"), "hex", "--elf", str(elf_path),
             "--rte-config", str(rte_config), "--dataset", str(project), "--variant", "sport", "-o",
             str(variant_hex)], env)
        variant = calseg.read_hex(variant_hex, seg.segment, len(segment))
        expected = int(calseg.dataset_values(project, "sport")["values_id"], 16)
        if calseg.check(variant, layout_id, seg.length) != "VALID" or calseg.read_header(variant).dataset != expected:
            failures.append(f"{name}: segment hex of the variant sport is not valid for the image")
        elif variant[calseg.HEADER_SIZE:] == segment[calseg.HEADER_SIZE:]:
            failures.append(f"{name}: the variant sport gives the same values as base")
    parameters = calseg.parameters_of(elf_path, rte_config)
    try:
        calseg.check_parameters(seg, parameters, name)
    except calseg.CalsegError as exc:
        failures.append(str(exc))
    if not parameters:
        failures.append(f"{name}: no parameters in rte::PARAMETERS")

    # SYS-91: the ROM check list has the five areas of an image without segment, none touches the segment; the whole
    # region is the area with its own reference; the download image with another dataset or a damaged header keeps
    # the build reference valid (the start-up check decides, not SM-14); the image registers the run-time check
    symbols = elf.symbols
    check_rom_image(name, elf_path, out / "app.ecu.hex", layout, seg, failures)
    if name in DATASETS:
        merged = out / "app.ecu.sport.hex"
        status, _ = calseg.merge(elf_path, out / "app.ecu.hex", merged, out / "app.ecu.cal.sport.hex")
        if status != "VALID":
            failures.append(f"{name}: download image with the segment of the variant sport: start-up check {status}")
        check_rom_image(name + " (dataset sport)", elf_path, merged, layout, seg, failures)
    damaged = out / "app.ecu.damaged.hex"
    status, _ = calseg.merge(elf_path, out / "app.ecu.hex", damaged, flips=[(9, 2)])
    if status != "HEADER_CRC":
        failures.append(f"{name}: download image with a flipped header bit: start-up check {status}")
    check_rom_image(name + " (damaged header)", elf_path, damaged, layout, seg, failures)
    wanted = ("_ZN11calibration14recheckSegment", "_ZN6safety8RomCheck15setOwnAreaCheck")
    missing = [w for w in wanted if not any(s.startswith(w) for s in symbols)]
    if missing:
        failures.append(f"{name}: run-time check of the segment not in the image: {', '.join(missing)}")

    # SYS-90: the plausibility checks of the safety-relevant parameters - generated table, report and the check in the
    # image exactly for a profile with such a parameter (calib), nothing of it in the QM demo
    report = description.with_name("RteParameterChecks.md")
    safety = any(p.get("safety", "qm") != "qm" for p in content["parameters"].values())
    has_table = "_ZN3rte16PARAMETER_CHECKSE" in symbols
    has_check = any(s.startswith("_ZN11calibration12plausibility") for s in symbols)
    present = (report.is_file(), has_table, has_check)
    if (safety and not all(present)) or (not safety and any(present)):
        failures.append(f"{name}: plausibility checks: safety-relevant parameters {safety}, report {report.is_file()}, "
                        f"rte::PARAMETER_CHECKS {has_table}, check code {has_check}")
    if safety and report.is_file():
        rows = [line for line in report.read_text(encoding="utf-8").splitlines() if line.startswith("| ")][1:]
        expected = sum(1 for p in content["parameters"].values() if p.get("safety", "qm") != "qm")
        if len(rows) != expected or "Values of this build: dataset" not in report.read_text(encoding="utf-8"):
            failures.append(f"{name}: RteParameterChecks.md lists {len(rows)} parameters (expected {expected}) or "
                            f"does not name the dataset")
    accept = any(s.startswith("_ZN11calibration12plausibility11acceptWrite") for s in symbols)
    if safety and not production and not accept:
        failures.append(f"{name}: XCP writes are not checked (no calibration::plausibility::acceptWrite)")

    a2l = out / "app.ecu.a2l"
    xcp = "_ZN7systems9XcpSystem4findEjRN3xcp6RegionE" in symbols or any("XcpSystem" in s for s in symbols)
    if production:
        if xcp or a2l.is_file():
            failures.append(f"{name}: production profile with XCP in the image or an A2L")
    else:
        if not a2l.is_file():
            failures.append(f"{name}: app.ecu.a2l missing")
        else:
            result = subprocess.run([sys.executable, str(REPO / "tools" / "a2lgen" / "a2lcheck.py"), str(a2l)],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                failures.append(f"{name}: a2lcheck: {result.stdout[-2000:]}{result.stderr[-2000:]}")
            addresses = a2l_addresses(a2l)
            if not addresses:
                failures.append(f"{name}: no CHARACTERISTIC in the A2L")
            for label, address in addresses:
                if not seg.ram <= address < seg.ram + seg.length:
                    failures.append(f"{name}: A2L {label} at 0x{address:08X} outside the working page")
    print(f"{name}: segment 0x{seg.segment:08X}, {seg.length} value bytes, {len(parameters)} parameters, "
          f"{'production' if production else f'working page 0x{seg.ram:08X}'}", flush=True)
    return segment[calseg.HEADER_SIZE:]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    names = sys.argv[2:] or default_profiles()
    unknown = [n for n in names if n not in PROFILES]
    if unknown:
        print(f"unknown profile(s): {', '.join(unknown)} (known: {', '.join(PROFILES)})")
        return 2
    env = {**os.environ, "PYTHONUTF8": "1"}
    env["PATH"] = os.pathsep.join(p for p in (env.get("NX_ARM_GCC_BIN"), env.get("PATH")) if p)
    env.setdefault("CC", "arm-none-eabi-gcc")
    env.setdefault("CXX", "arm-none-eabi-g++")
    if shutil.which("arm-none-eabi-gcc", path=env["PATH"]) is None:
        print("arm-none-eabi-gcc not found")
        return 2
    failures: list[str] = []
    values = {}
    for name in names:
        values[name] = check_profile(name, root / name, env, failures)
    for project in ("demo", "qm", "calib"):
        dev, prod = values.get(f"{project}-dev"), values.get(f"{project}-prod")
        if dev is not None and prod is not None and dev != prod:
            failures.append(f"{project}: the segment values differ between development and production profile")
    for f in failures:
        print("FAIL", f)
    print(f"target build calibration: {len(names)} profiles, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
