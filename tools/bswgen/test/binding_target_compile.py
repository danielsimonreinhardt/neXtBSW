# SPDX-License-Identifier: Apache-2.0
"""ARCH-52: target compile of the family binders of the generated module binding (ModuleBinders.h, s32k1xx).

No project of the repository binds a module through the generator yet, so no target build instantiates the SPI and
ADC binders of the family (the demo compiles ModuleBinders.h with NX_BINDING_SPI_DEVICE_COUNT 0). This script takes
the compiler command line of Integration.cpp from a real target build, puts a counts header in front that asks for
every binder, and compiles a probe that uses each binder with board entries of that build's own generat - an SPI
device on a board SPI instance with 40-bit frames, an ADC channel with its characteristic, a digital output and
input - with the same flags (-Werror included): no error, no warning. A second probe with a frame size the LPSPI
master does not support (33 bits) must stop at the binder's static_assert. A third compiles Integration.cpp itself
with a moduleConfiguration.h in which a hand-integrated module declares lifecycle level 2: the family integration's
static_assert must stop it (the manifest's lifecycle is no longer ignored).

    python tools/bswgen/test/binding_target_compile.py <build directory of a target build>
    python tools/bswgen/test/binding_target_compile.py <build directory> --configure <project.yaml>

With --configure the script first configures the project into the build directory (no build: the compile lines and
the generat are written at configure time, tools/test/build_dirs.py configure_project). Run by tools/test as
binding-target-compile on the synthetic full-featured test profile testproject_full (PLAT-62: gate driver on SPI,
high-side switch with sense input, digital in- and outputs) - an open profile, so the probe also runs in the exported
open tree, which lacks the private eval board project (TOOL-245; before: the demo build {build}/demo).
"""

import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools"))

from nxconfig.config import load_modules  # noqa: E402

TRANSLATION_UNIT = "platforms/s32k1xx/port/main/src/integration/Integration.cpp"
COUNTS = """\
// ARCH-52 target compile probe: every family binder is asked for
#pragma once
#define NX_BINDING_INSTANCE_COUNT 1
#define NX_BINDING_SAFE_OFF_COUNT 0
#define NX_BINDING_SPI_DEVICE_COUNT 1
#define NX_BINDING_ANALOG_INPUT_COUNT 1
"""
PROBE = """\
#include "ModuleBinders.h"
#include "ModuleBinding.h"

namespace
{{
struct Facts
{{
    static constexpr uint8_t SPI_FRAME_BITS  = {bits}U;
    static constexpr bool SPI_CPOL           = true;
    static constexpr bool SPI_CPHA           = true;
    static constexpr uint32_t SPI_MAX_HZ     = 1000000UL;
    static constexpr uint32_t SPI_TIMEOUT_US = 5000UL;
}};

::integration::FamilyBinders::SpiDevice<Facts> probeSpi{{NX_BINDER_SPI_BUS({spi}, 1U)}};
constexpr auto PROBE_ANALOG = NX_BINDER_ANALOG_INPUT({analog});
constexpr auto PROBE_OUTPUT = NX_BINDER_DIGITAL_OUTPUT({output});
constexpr auto PROBE_INPUT  = NX_BINDER_DIGITAL_INPUT({input});
}} // namespace

bool nxBindingProbe();

bool nxBindingProbe()
{{
    using B     = ::integration::FamilyBinders;
    uint64_t rx = 0U;
    uint16_t raw = 0U;
    uint8_t quality = 0U;
    bool active = false;
    bool ok = probeSpi.transferLong(0x123456789AULL, rx);
    ok = B::read(PROBE_ANALOG, raw) && ok;
    ok = (B::physical(PROBE_ANALOG, raw, quality) > 0) && ok;
    ok = B::set(PROBE_OUTPUT, true) && ok;
    ok = B::get(PROBE_INPUT, active) && ok;
    return ok && active && (rx != 0U);
}}
"""


def argv_of(command: str) -> list[str]:
    return shlex.split(command.replace('\\"', "\0").replace("\\", "/").replace("\0", '\\"'))


def entries_of_kind(generated: Path) -> dict[str, str]:
    """First board entry per connection kind among the module instances of the build, and an SPI instance."""
    manifests = load_modules().manifests
    found: dict[str, str] = {}
    for header in sorted((generated / "integration").glob("*Configuration.h")):
        module = header.name[: -len("Configuration.h")]
        manifest = manifests.get(module)
        text = header.read_text(encoding="utf-8")
        match = re.search(r"#define NX_DEVICE_\w+?_SPI_INSTANCE (\w+)", text)
        if match:
            found.setdefault("spi", match.group(1))
        if manifest is None:
            continue
        for conn, spec in manifest.connect.items():
            match = re.search(rf"#define NX_DEVICE_\w+_IO_{conn.upper()} (\w+)\n", text)
            if match:
                found.setdefault(spec.kind, match.group(1))
    return found


def compile_probe(base: list[str], entry: dict, work: Path, name: str, source: str) -> subprocess.CompletedProcess:
    c, o = base.index("-c"), base.index("-o")
    cpp = work / f"{name}.cpp"
    cpp.write_text(source, encoding="utf-8")
    argv = list(base)
    argv[c + 1] = cpp.as_posix()
    argv[o + 1] = (work / f"{name}.o").as_posix()
    argv = [a for a in argv if a != "-fdiagnostics-color=always"]
    # the counts first, the family integration directory for ModuleBinders.h and ModuleBinding.h
    argv[1:1] = [f"-I{(work / 'counts').as_posix()}", f"-I{(REPO / TRANSLATION_UNIT).parent.as_posix()}"]
    return subprocess.run(argv, cwd=entry["directory"], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def lifecycle_probe(base: list[str], entry: dict, build: Path, work: Path) -> int:
    """Integration.cpp itself with the build's moduleConfiguration.h, in which one hand-integrated module declares
    another lifecycle level: the static_assert of the family integration must stop the build (ARCH-52: the manifest's
    lifecycle is no longer ignored). Returns the number of failures (0 or 1)."""
    header = (build / "generated" / "board" / "integration" / "moduleConfiguration.h").read_text(encoding="utf-8")
    match = re.search(r"#define NX_MODULE_(\w+)_LIFECYCLE_LEVEL 1U\n", header)
    counts = re.findall(r"#define NX_MODULE_(\w+)_COUNT ([1-9])", header)
    used = [m for m, _ in counts if m in ("HIGHSIDESWITCH", "GDTMC6200")]
    if match is None or not used:
        print("FAIL the build uses no hand-integrated module with a lifecycle to probe with")
        return 1
    module = used[0]
    probe = work / "lifecycle" / "integration"
    probe.mkdir(parents=True)
    old = f"#define NX_MODULE_{module}_LIFECYCLE_LEVEL 1U\n"
    (probe / "moduleConfiguration.h").write_text(header.replace(old, old.replace("1U", "2U")), encoding="utf-8")
    argv = [a for a in base if a != "-fdiagnostics-color=always"]
    c, o = argv.index("-c"), argv.index("-o")
    argv[o + 1] = (work / "integration_probe.o").as_posix()
    argv[c + 1] = (REPO / TRANSLATION_UNIT).as_posix()
    argv[1:1] = [f"-I{(work / 'lifecycle').as_posix()}"]
    r = subprocess.run(argv, cwd=entry["directory"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0 or "module.yaml lifecycle differs from its hand-written integration" not in r.stderr:
        print(f"FAIL a manifest lifecycle level 2 of {module} did not stop Integration.cpp:\n{r.stderr[-2000:]}")
        return 1
    print(f"OK   lifecycle level 2 in the manifest of {module} stops Integration.cpp")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) not in (1, 3) or (len(args) == 3 and args[1] != "--configure"):
        print(__doc__)
        return 2
    build = Path(args[0]).resolve()
    if len(args) == 3:
        sys.path.insert(0, str(REPO / "tools" / "test"))
        from build_dirs import configure_project
        project = Path(args[2]) if Path(args[2]).is_absolute() else REPO / args[2]
        r = configure_project(REPO, project, build, sys.executable)
        if r.returncode != 0:
            print(f"FAIL configure of {args[2]}:\n{(r.stdout + r.stderr)[-3000:]}")
            return 1
    database = build / "compile_commands.json"
    if not database.is_file():
        print(f"FAIL no compile_commands.json in {build}: run the target build first (tools/test -k target-demo)")
        return 1
    entry = next((e for e in json.loads(database.read_text(encoding="utf-8"))
                  if Path(e["file"]).resolve().as_posix().endswith(TRANSLATION_UNIT)), None)
    if entry is None:
        print(f"FAIL {TRANSLATION_UNIT} is not in the build")
        return 1
    found = entries_of_kind(build / "generated" / "board")
    missing = [k for k in ("spi", "digital_output", "digital_input", "analog_input") if k not in found]
    if missing:
        print(f"FAIL the build's generat has no module connection of kind {', '.join(missing)} to probe with")
        return 1
    print("probe entries: " + ", ".join(f"{k} {v}" for k, v in found.items()))
    base = argv_of(entry["command"])
    work = Path(tempfile.mkdtemp(prefix="nx_binding_target_"))
    failures = 0
    try:
        (work / "counts" / "integration").mkdir(parents=True)
        (work / "counts" / "integration" / "moduleBindingConfiguration.h").write_text(COUNTS, encoding="utf-8")
        values = {"spi": found["spi"], "analog": found["analog_input"], "output": found["digital_output"],
                  "input": found["digital_input"]}
        r = compile_probe(base, entry, work, "probe", PROBE.format(bits=40, **values))
        text = r.stdout + r.stderr
        if r.returncode != 0 or re.search(r"\bwarning:", text):
            failures += 1
            print(f"FAIL family binders:\n{text[-3000:]}")
        else:
            print("OK   family binders (SPI device 40 bit, ADC channel, digital output and input)")
        r = compile_probe(base, entry, work, "probe33", PROBE.format(bits=33, **values))
        if r.returncode == 0 or "not supported by the LPSPI master of this family" not in r.stderr:
            failures += 1
            print(f"FAIL a 33-bit frame was not refused by the SPI binder:\n{(r.stdout + r.stderr)[-2000:]}")
        else:
            print("OK   33-bit frame refused by the SPI binder")
        failures += lifecycle_probe(base, entry, build, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"3 probes, {failures} failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
