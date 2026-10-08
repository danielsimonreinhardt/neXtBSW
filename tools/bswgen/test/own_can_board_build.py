"""Target build of an own board whose CAN buses are not the eval board's (ARCH-61, PLAT-96, PLAT-100).

    python tools/bswgen/test/own_can_board_build.py <variant> [<build directory>]

A customer's first own board broke the build although configuration, check and generators passed: the family port
wired the bus name CAN0 by hand (main.cpp: NX_IRQ_LEVEL_CAN_CAN0, NX_CAN_MB_IRQS_CAN0), a project without a CAN bus
did not compile, and a UART named `console` made the console macro refer to itself. Each variant here is one of
those boards, built like every target build of tools/test - warning-free with -Werror:

  names     two buses with other names (`body` on FlexCAN0, `chassis` on FlexCAN2), bootloader on `body`, XCP on
            the lower-case bus `chassis` (TOOL-112: a2lgen finds its bit rate under the C identifier), and the
            console UART named `console` (PLAT-100)
  instance  one bus `NBUS` on FlexCAN1 - an instance no other profile uses - with bootloader and XCP on it
  nocan     an application without any CAN bus (ARCH-42, PLAT-96): use.can empty, no rank, no XCP; the board keeps
            its buses because the bootloader still needs one (bootloader.can); CAN, TRANSPORT and UDS switched off
  noboardcan  a board without any CAN bus: the family port cannot build it (no bootloader, so no memory
            partitioning); the configure step must stop in nxconfig with file and YAML path (project memory),
            not in CMake or the compiler

Own profiles, never the eval board: board and project are derived from the build test board buildtest_s32k148 and
its minimal project (no SBC, passive transceivers) and written to <temp>/s32k/a61<variant>_src; only the parts
named above change. The build uses the preset of that project with NX_PROJECT pointed at the derived project, the
build directory is short (Windows 260 character limit, PLAT-06).

Checked per variant: nxconfig/bswgen accept the project (configure passes), the CAN switch of the build follows the
project (PLATFORM_SUPPORT_CAN), the generated device list names the buses on their instances, the build passes
without a warning, app.ecu.elf (and bootloader.elf where the project has a bootloader) exist, and where the project
has XCP, app.ecu.a2l carries the bit rate of the XCP bus.

Needs cmake, ninja and arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SOURCE_BOARD = REPO / "boards" / "buildtest_s32k148" / "board.yaml"      # never the eval board
SOURCE_PROJECT = REPO / "projects" / "buildtest_s32k148_min"
PRESET = "buildtest-s32k148-min"


def _rank(name: str, note: str) -> dict:
    return {"source": "can", "name": name, "rank": 3, "note": note}


def names(board: dict, project: dict) -> dict:
    """Two buses, neither called CAN0/CAN1; the console UART called `console`."""
    rename = {"CAN0": "body", "CAN1": "chassis"}
    for bus in board["can"]:
        old = bus["name"]
        bus["name"] = rename[old]
        bus["phy"] = f"{rename[old].upper()}_PHY"
    for dev in board["devices"]:
        if dev["name"].endswith("_PHY"):
            dev["name"] = f"{rename[dev['name'][:-4]].upper()}_PHY"
    board["uart"][0]["name"] = "console"
    project["can"] = {rename[k]: v for k, v in project["can"].items()}
    project["uart"] = {"console": project["uart"]["UART"]}
    project["bootloader"]["can"] = "body"
    # XCP on the lower-case bus (TOOL-112): until then tools/a2lgen looked the bit rate up as `chassis_BITRATE`
    # instead of the C identifier CHASSIS_BITRATE and stopped the build
    project["xcp"]["bus"] = "chassis"
    project["interrupts"]["ranks"] = [
        {**r, "name": rename[r["name"]]} if r["source"] == "can"
        else {**r, "name": "console"} if r["source"] == "uart" else r
        for r in project["interrupts"]["ranks"]]
    return {"can": True, "buses": [("BODY", "FlexCAN0"), ("CHASSIS", "FlexCAN2")], "bootloader": True,
            "a2l": ("chassis", project["can"]["chassis"]["bitrate"])}


def instance(board: dict, project: dict) -> dict:
    """One bus on FlexCAN1 (pins PTA12/PTA13, free on this board), named NBUS like the first Studio board."""
    board["can"] = [{"name": "NBUS", "instance": "FlexCAN1", "rx": {"pin": "PTA12"}, "tx": {"pin": "PTA13"},
                     "phy": "NBUS_PHY"}]
    board["devices"] = [d for d in board["devices"] if not d["name"].endswith("_PHY")]
    board["devices"].insert(0, {"name": "NBUS_PHY", "part": "TJA1051T/3", "module": "canPhyPassive",
                                "note": "transceiver of NBUS (test profile ARCH-61)"})
    project["can"] = {"NBUS": {"bitrate": 500000}}
    project["bootloader"]["can"] = "NBUS"
    project["xcp"]["bus"] = "NBUS"
    project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["source"] != "can"]
    project["interrupts"]["ranks"].insert(0, _rank("NBUS", "FlexCAN1 message buffers"))
    return {"can": True, "buses": [("NBUS", "FlexCAN1")], "bootloader": True, "a2l": ("NBUS", 500000)}


def nocan(board: dict, project: dict) -> dict:
    """An application without any CAN bus (ARCH-42): use.can is empty, no rank, no XCP.

    The board keeps its buses and the bootloader keeps CAN0 - it is the only way into the flash the family port
    has (memory partitioning, bootloader.can), and a bus the bootloader uses is not a bus of the application.
    """
    project["use"]["can"] = []
    project["can"] = {"CAN0": project["can"]["CAN0"]}          # bit timing of the bootloader bus
    project.pop("xcp", None)
    project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["source"] != "can"]
    return {"can": False, "buses": [], "bootloader": True}


def noboardcan(board: dict, project: dict) -> dict:
    """A board without any CAN bus. The family port cannot build it (no bootloader, so no memory partitioning, so no
    application linker script): nxconfig must refuse it at configure with file and YAML path, not the compiler."""
    board.pop("can")
    board["devices"] = [d for d in board["devices"] if not d["name"].endswith("_PHY")]
    for key in ("can", "bootloader", "diagnostics", "xcp", "memory"):
        project.pop(key, None)
    project["interrupts"]["ranks"] = [r for r in project["interrupts"]["ranks"] if r["source"] != "can"]
    return {"refused": r"project\.yaml(:\d+)? \(memory\): error: .*needs memory"}


VARIANTS = {"names": names, "instance": instance, "nocan": nocan, "noboardcan": noboardcan}


def relpath(target: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:
        return target.as_posix()


def write_profile(variant: str, src: Path) -> tuple[Path, dict]:
    board = yaml.safe_load(SOURCE_BOARD.read_text(encoding="utf-8"))
    project = yaml.safe_load((SOURCE_PROJECT / "project.yaml").read_text(encoding="utf-8"))
    board_name = f"a61_{variant}"
    board["board"]["name"] = board_name
    board["board"]["description"] = f"test profile ARCH-61 ({variant}), derived from buildtest_s32k148"
    project["project"]["name"] = f"a61_{variant}_main"
    project["project"]["board"] = f"../../boards/{board_name}"
    project["project"]["description"] = f"test profile ARCH-61 ({variant}) - not for hardware"
    expect = VARIANTS[variant](board, project)
    shutil.rmtree(src, ignore_errors=True)
    (src / "boards" / board_name).mkdir(parents=True)
    # a workspace pin, so that the board is a real board of a workspace (boards/) like a customer's and nxconfig
    # checks the requirements of the family port for it (checks.built_with_family_port)
    (src / "nextbsw.yaml").write_text(
        "# synthetic workspace of tools/bswgen/test/own_can_board_build.py (ARCH-61) - test data\n"
        "schema_version: 1\n"
        "nextbsw:\n"
        f"  version: {(REPO / 'VERSION').read_text(encoding='utf-8').strip()}\n"
        "  source: submodule\n"
        f"  path: {relpath(REPO, src)}\n"
        "  profile: open\n"
        "search:\n"
        "  boards: [boards]\n", encoding="utf-8")
    (src / "boards" / board_name / "board.yaml").write_text(
        f"# test profile of tools/bswgen/test/own_can_board_build.py ({variant}) - generated, do not edit\n"
        + yaml.safe_dump(board, sort_keys=False, allow_unicode=True), encoding="utf-8")
    proj_dir = src / "projects" / project["project"]["name"]
    proj_dir.mkdir(parents=True)
    project_file = proj_dir / "project.yaml"
    project_file.write_text(
        f"# test profile of tools/bswgen/test/own_can_board_build.py ({variant}) - generated, do not edit\n"
        + yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
    # component paths are relative to rte.yaml: point them at the components of the tree
    rte = (SOURCE_PROJECT / "rte.yaml").read_text(encoding="utf-8")
    rte = rte.replace("../../swc/", relpath(REPO / "swc", proj_dir) + "/")
    (proj_dir / "rte.yaml").write_text(rte, encoding="utf-8")
    return project_file, expect


def run(argv: list[str]) -> tuple[int, str]:
    print("$", " ".join(argv), flush=True)
    env = dict(os.environ)
    gcc_bin = env.get("NX_ARM_GCC_BIN")
    if gcc_bin:
        env["PATH"] = os.pathsep.join([gcc_bin, env.get("PATH", "")])
    env["PYTHONUTF8"] = "1"
    done = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env)
    return done.returncode, done.stdout + done.stderr


def main() -> int:
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in VARIANTS:
        print(__doc__)
        return 2
    variant = sys.argv[1]
    root = Path(tempfile.gettempdir()) / "s32k"
    build = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else root / f"a61{variant[:4]}"
    src = root / f"a61{variant}_src"
    shutil.rmtree(build, ignore_errors=True)
    project_file, expect = write_profile(variant, src)
    expect.setdefault("can", False)
    print(f"profile {variant}: {project_file}")
    failures: list[str] = []
    checks = 0

    code, out = run(["cmake", "--preset", PRESET, "-B", str(build), f"-DNX_PROJECT={project_file.as_posix()}",
                     f"-DPython3_EXECUTABLE={sys.executable}"])
    checks += 1
    if "refused" in expect:
        # the refusal must come from nxconfig with file and YAML path, before any compiler runs
        found = re.search(expect["refused"], out) is not None
        print("\n".join(line for line in out.splitlines() if "error" in line.lower())[-3000:])
        if code == 0 or not found:
            failures.append(f"configure {'passed' if code == 0 else 'failed without the nxconfig message'}: "
                            f"expected {expect['refused']!r}")
        checks += 1
        if (build / "build.ninja").is_file():
            failures.append("configure wrote build.ninja although the project was refused")
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"own CAN board {variant}: {checks} checks, {len(failures)} failures")
        if not failures:
            shutil.rmtree(src, ignore_errors=True)
        return 1 if failures else 0
    if code != 0:
        print(out[-6000:])
        print(f"FAIL: configure of the {variant} profile failed (nxconfig or a generator refused it)")
        print(f"own CAN board {variant}: {checks} checks, 1 failures")
        return 1
    checks += 1
    can_on = re.search(r"PLATFORM_SUPPORT_CAN ON", out) is not None
    if can_on != expect["can"]:
        failures.append(f"PLATFORM_SUPPORT_CAN is {'ON' if can_on else 'OFF'}, the project "
                        f"{'uses' if expect['can'] else 'uses no'} CAN bus")

    device = build / "generated" / "board" / "bsp" / "can" / "canDeviceConfiguration.h"
    text = device.read_text(encoding="utf-8") if device.is_file() else ""
    listed = re.findall(r"^    X\((\w+), \d+, \d+, \d+, \"(\w+)\"\)", text, re.M)
    checks += 1
    if listed != expect["buses"]:
        failures.append(f"generated NX_CAN_BUSES lists {listed}, expected {expect['buses']}")
    checks += 1
    if f"#define NX_CAN_DEVICE_COUNT {len(expect['buses'])}\n" not in text:
        failures.append(f"generated NX_CAN_DEVICE_COUNT is not {len(expect['buses'])}")

    code, out = run(["cmake", "--build", str(build)])
    # compiler and linker warnings ("warning:"), not the summary "0 warning(s)" of a re-configure by nxconfig
    warnings = [line for line in out.splitlines() if re.search(r"\bwarning:", line, re.I)]
    checks += 1
    if code != 0:
        errors = [line for line in out.splitlines() if re.search(r"\berror:|undefined reference", line, re.I)]
        print("\n".join(errors[:40]) or out[-6000:])
        failures.append(f"the build of the {variant} profile failed")
    checks += 1
    if warnings:
        failures.append(f"{len(warnings)} warning(s) in the build log, first: {warnings[0]}")
    images = ["app.ecu.elf"] + (["bootloader.elf"] if expect["bootloader"] else [])
    for image in images:
        checks += 1
        if not list(build.rglob(image)):
            failures.append(f"{image} was not built")
    if "a2l" in expect:
        # XCP on the bus of the profile: the A2L carries the bit rate of that bus (tools/a2lgen, TOOL-112)
        bus, bitrate = expect["a2l"]
        a2l = list(build.rglob("app.ecu.a2l"))
        checks += 1
        if not a2l:
            failures.append(f"app.ecu.a2l was not built (XCP on {bus})")
        elif not re.search(rf"^\s*BAUDRATE {bitrate}$", a2l[0].read_text(encoding="utf-8"), re.M):
            failures.append(f"{a2l[0]}: no BAUDRATE {bitrate} (XCP on {bus})")

    for failure in failures:
        print(f"FAIL: {failure}")
    print(f"own CAN board {variant}: {checks} checks, {len(failures)} failures")
    if not failures:
        shutil.rmtree(src, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
