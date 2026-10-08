# SPDX-License-Identifier: Apache-2.0
"""Python side of the MATLAB test SwcTypesTest (RTE-15): a model made by swc_create from swc.yaml types against the
RTE-04 model TypeSwc that was built by hand (tools/rtegen/test/types/TypeSwc/TypeSwc_create.m).

    types_flow.py compare <component folder> <out folder>
        1. tools/rtegen reads both ARXMLs (parse_component): every port of the component must have the direction,
           layout, element types, scaling, literals and init value of the port of the same name of TypeSwc;
        2. rtegen generates the RTE of each component alone (same ECU description: the loops of
           tools/rtegen/test/types/rte.yaml, the other required ports at rte.init, the provided ports at rte;
           build-test profile S32K148, BSW task profile tools/rtegen/test/multitask/tasks.yaml) and Rte_Type.h,
           Rte_<Swc>.h and Rte.c must be the same text.
        Names are compared after renaming the component and its types to those of TypeSwc (<Swc> -> TypeSwc, and
        the fixed-point type of Temp -> UFIX16_SP1_BN40, the name Embedded Coder gives the bare fixdt of TypeSwc).
        Exit 1 with every difference printed.

Nothing is written outside <out folder>; the ARXML of both components is read where it lies.
"""

import difflib
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
REPO = TOOLS.parent
for _path in (str(TOOLS), str(TOOLS / "swc"), str(TOOLS / "rtegen")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import yaml  # noqa: E402

REFERENCE = REPO / "tools" / "rtegen" / "test" / "types" / "TypeSwc"
REFERENCE_NAME = "TypeSwc"
PROJECT = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
TASKS = REPO / "tools" / "rtegen" / "test" / "multitask" / "tasks.yaml"
# loops inside the component as in tools/rtegen/test/types/rte.yaml
LOOPS = {"Samples": "Scaled", "ModeIn": "ModeOut"}


def _renamer(name: str, temp_type: str):
    def rename(text: str) -> str:
        # the type name as a whole word only: Rte_Buf_<Swc>_Temp_Temp is a buffer, not the type
        text = re.sub(rf"\b{name}_Temp\b", temp_type, text) if name != REFERENCE_NAME else text
        return text.replace(name, REFERENCE_NAME).replace(name.upper(), REFERENCE_NAME.upper())   # include guard
    return rename


def compare_ports(name: str, folder: Path) -> list[str]:
    import rtegen
    mine = rtegen.parse_component(name, folder / "generated" / "arxml")
    ref = rtegen.parse_component(REFERENCE_NAME, REFERENCE / "generated" / "arxml")
    temp = ref.ports["Temp.Temp"].type.name
    rename = _renamer(name, temp)
    problems = []
    if sorted(mine.ports) != sorted(ref.ports):
        problems.append(f"ports: {sorted(mine.ports)} vs {sorted(ref.ports)}")
    for key in sorted(set(mine.ports) & set(ref.ports)):
        a, b = mine.ports[key], ref.ports[key]
        for what, x, y in (("direction", a.direction, b.direction),
                           ("type", rename(repr(a.type.signature())), repr(b.type.signature())),
                           ("type name", rename(a.type.name), b.type.name),
                           ("init", a.init, b.init)):
            if x != y:
                problems.append(f"port {key}: {what} {x} vs {y} of {REFERENCE_NAME}")
        print(f"port {key}: {a.direction} {a.type.describe()} init {a.init}", flush=True)
    return problems


def generate(name: str, folder: Path, out: Path) -> Path:
    import rtegen
    ecu = {"schema_version": 2,
           "tasks": [{"name": "fast", "period_ms": 10, "priority": 35, "stack_bytes": 1024,
                      "runnables": [f"{name}.Run10ms"]}],
           "components": [{"name": name, "path": folder.resolve().as_posix()}],
           "connections": {}}
    comp = rtegen.parse_component(name, folder / "generated" / "arxml")
    for key, port in comp.ports.items():
        if port.direction == "in":
            source = LOOPS.get(port.name)
            ecu["connections"][f"{name}.{key}"] = f"{name}.{source}.{source}" if source else "rte.init"
        else:
            ecu["connections"][f"{name}.{key}"] = "rte"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "rte.yaml"
    path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
    config = rtegen.nxconfig.load(PROJECT)
    rtegen.generate(path, config, out / "rte", REPO, TASKS)
    return out / "rte"


def compare_rte(name: str, folder: Path, out: Path) -> list[str]:
    import rtegen
    ref_temp = rtegen.parse_component(REFERENCE_NAME, REFERENCE / "generated" / "arxml").ports["Temp.Temp"].type.name
    rename = _renamer(name, ref_temp)
    mine = generate(name, folder, out / "mine")
    ref = generate(REFERENCE_NAME, REFERENCE, out / "reference")
    problems = []
    for mine_file, ref_file in (("Rte_Type.h", "Rte_Type.h"), (f"Rte_{name}.h", f"Rte_{REFERENCE_NAME}.h"),
                                ("Rte.c", "Rte.c")):
        a = rename((mine / mine_file).read_text(encoding="utf-8")).splitlines()
        b = (ref / ref_file).read_text(encoding="utf-8").splitlines()
        if a != b:
            diff = list(difflib.unified_diff(b, a, f"{REFERENCE_NAME}/{ref_file}", f"{name}/{mine_file}",
                                             lineterm="", n=1))
            problems.append(f"{mine_file} differs from the RTE of {REFERENCE_NAME}:\n" + "\n".join(diff[:80]))
        else:
            print(f"{mine_file}: same as for {REFERENCE_NAME} ({len(a)} lines)", flush=True)
    return problems


def main(argv: list) -> int:
    if len(argv) != 4 or argv[1] != "compare":
        print(__doc__)
        return 2
    folder, out = Path(argv[2]).resolve(), Path(argv[3]).resolve()
    name = folder.name
    import rtegen
    try:
        problems = compare_ports(name, folder) + compare_rte(name, folder, out)
    except rtegen.RteError as e:
        print(f"rtegen: error: {e}", flush=True)
        return 1
    for p in problems:
        print(f"difference: {p}", flush=True)
    print("types_flow: " + ("same as TypeSwc" if not problems else f"{len(problems)} differences"), flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
