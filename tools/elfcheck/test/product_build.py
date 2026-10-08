"""SYS-74: the product image of the demo project has no test and fault-injection console commands.

  python tools/elfcheck/test/product_build.py <build root> [<demo build directory>]

Three configurations of the checked-in demo project (preset evalboard-s32k144-demo, the project is not changed):

product    -DNX_PRODUCT_BUILD=ON: build log without warnings, `elfcheck product` finds none of the commands of
           tools/elfcheck/product.py (symbols and console texts in the image), the commands that stay are there
           ("lc bootloader", the SBC and safeadc groups; SYS-119: the reading commands of the groups whose service
           write commands left, "nvm write", "out" with its own table without "set"), the layout check of elfcheck
           is clean.
testbuild  counter check: the development build with every test option (NX_HARDFAULT_TEST_COMMAND,
           NX_GD_ENABLE_COMMAND, NX_SAFETY_FAULT_INJECTION_COMMAND, NX_FLEXNVM_PARTITION_COMMAND, NX_DFLASH_COMMAND):
           every command of the inventory is found (SYS-119: a command whose console group the demo lacks is
           skipped), and `elfcheck product` on it fails.
refused    -DNX_PRODUCT_BUILD=ON together with test options: configuring stops and names the options.

With a demo build directory (default: the build of tools/test -k target-demo, if there): the standard image still
has the development commands (safety, lc, sbc failstart) - the demo keeps its behaviour.

Needs cmake, ninja and arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), like the target group of tools/test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(REPO / "tools" / "test"))

import check  # noqa: E402
import product  # noqa: E402
from build_dirs import demo_build_dir, fresh_args  # noqa: E402

PRESET = "evalboard-s32k144-demo"
DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
APP = "ecu/application/RelWithDebInfo/app.ecu.elf"
# one option can bring several commands (NVM-17: "flexnvm partition" and "csec")
TEST_OPTIONS = list(dict.fromkeys(m.option for m in product.MARKERS if m.option))
# Commands the product image keeps (a product check that removes everything would pass the absence checks)
KEPT_TEXTS = (b"reset into the CAN bootloader", b"UJA113x system basis chip", b"ADC diagnostics of the safety",
              b"lifecycle command",
              # SYS-119: the groups of the service commands keep their reading commands, "nvm write" stays
              b"read register (param: <address>)", b"CAN buses of the project", b"log received frames",
              b"fault memory (NVM-03)", b"snapshot and extended data records", b"writes every changed block now",
              b"outputs console", b"get output value (param: <output-id>)")
# SYS-119: "out" of the product image is the wrapper's own table without "set" (SafeOffOutputTester::getInfo)
PRODUCT_SYMBOLS = (r"7systems19SafeOffOutputTester7getInfoEv",)


def run(argv: list[str], env: dict, may_fail: bool = False) -> tuple[int, str]:
    print("$", subprocess.list2cmdline(argv), flush=True)
    result = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    output = result.stdout + result.stderr
    if result.returncode != 0 and not may_fail:
        print(output[-8000:])
        raise SystemExit(f"FAILED ({result.returncode}): {argv[0]} {argv[1]}")
    return result.returncode, output


def configure(build: Path, options: list[str], env: dict, may_fail: bool = False) -> tuple[int, str]:
    fresh, stale = fresh_args(build, env["PATH"])
    if stale:
        print(f"configured fresh: {stale}", flush=True)
    return run(["cmake", "--preset", PRESET, *fresh, "-B", str(build), f"-DPython3_EXECUTABLE={sys.executable}",
                *(f"-D{o}" for o in options)], env, may_fail)


def build_image(name: str, build: Path, options: list[str], env: dict, failures: list[str]) -> Path | None:
    _, config_log = configure(build, options, env)
    for old in build.glob(f"**/{APP}"):
        old.unlink()                    # every run links again (the check must see this configuration's image)
    _, log = run(["cmake", "--build", str(build), "--config", "RelWithDebInfo"], env)
    warnings = [line for line in log.splitlines() if re.search(r"\bwarning\b", line, re.I)]
    if warnings:
        failures.append(f"{name}: {len(warnings)} warning(s) in the build log:\n" + "\n".join(warnings[:30]))
    elf = build / APP
    if not elf.is_file():
        failures.append(f"{name}: {APP} missing")
        return None
    print(f"{name}: {elf}")
    if name == "product" and "NX_PRODUCT_BUILD: product image" not in config_log:
        failures.append("product: configure did not report the product build")
    return elf


def check_product(elf: Path, build: Path, failures: list[str]) -> None:
    hits = product.check_image(elf)
    for hit in hits:
        failures.append(f"product: still in the image: {hit}")
    symbols, content = product.image_parts(product.elffile.load(elf))
    for text in KEPT_TEXTS:
        if text not in content:
            failures.append(f"product: {text.decode()!r} missing - the product build removed more than the "
                            "test commands")
    for pattern in PRODUCT_SYMBOLS:
        if not any(re.search(pattern, name) for name in symbols):
            failures.append(f"product: no symbol {pattern} - 'out' of the product image lists the OpenBSW 'set'")
    exp = check.load_expectation(DEMO_PROJECT)
    images = check.find_build_images(build)
    findings = check.check_images(exp, images.get("bootloader"), images.get("app"), images.get("updater"))
    for f in findings.errors:
        failures.append(f"product: elfcheck: {f}")
    print(f"product: {len(hits)} test commands in the image, {len(findings.errors)} elfcheck errors")


def check_testbuild(elf: Path, failures: list[str]) -> None:
    symbols, content = product.image_parts(product.elffile.load(elf))
    hits = product.find(symbols, content)
    expected = product.applicable(content)      # SYS-119: markers of modules the demo has
    absent = product.missing(hits, expected)
    for marker in absent:
        failures.append(f"testbuild: counter check: {marker.command} not found - marker does not match the command")
    if product.main(["--app", str(elf)]) != 1:
        failures.append("testbuild: elfcheck product passes an image with test commands")
    skipped = [m.command for m in product.MARKERS if m not in expected]
    print(f"testbuild: {len(expected) - len(absent)} of {len(expected)} test commands found"
          + (f", skipped (module not in the demo): {', '.join(skipped)}" if skipped else ""))


def check_refused(build: Path, env: dict, failures: list[str]) -> None:
    options = ["NX_PRODUCT_BUILD=ON", "NX_GD_ENABLE_COMMAND=ON", "NX_SAFETY_FAULT_INJECTION_COMMAND=ON"]
    if build.exists():
        shutil.rmtree(build)
    code, log = configure(build, options, env, may_fail=True)
    flat = " ".join(log.split())
    if code == 0:
        failures.append("refused: configuring a product build with test options succeeded")
    elif "NX_GD_ENABLE_COMMAND, NX_SAFETY_FAULT_INJECTION_COMMAND switched on" not in flat:
        failures.append("refused: configure failed without naming the test options:\n" + log[-3000:])
    else:
        print("refused: configure stops and names NX_GD_ENABLE_COMMAND, NX_SAFETY_FAULT_INJECTION_COMMAND")


def check_demo(demo: Path, failures: list[str]) -> None:
    elf = demo / APP
    if not elf.is_file():
        print(f"demo: {elf} not built (tools/test -k target-demo), check skipped")
        return
    symbols, content = product.image_parts(product.elffile.load(elf))
    found = {h.marker.command for h in product.find(symbols, content)}
    expected = {m.command for m in product.applicable(content) if m.option is None}
    if found != expected:
        failures.append(f"demo: development commands {sorted(found)}, expected {sorted(expected)}")
    else:
        print(f"demo: development commands present ({', '.join(sorted(found))})")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    demo = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(demo_build_dir(REPO))
    env = {**os.environ, "PYTHONUTF8": "1"}
    env["PATH"] = os.pathsep.join(p for p in (env.get("NX_ARM_GCC_BIN"), env.get("PATH")) if p)
    env.setdefault("CC", "arm-none-eabi-gcc")
    env.setdefault("CXX", "arm-none-eabi-g++")
    if shutil.which("arm-none-eabi-gcc", path=env["PATH"]) is None:
        print("arm-none-eabi-gcc not found")
        return 2
    failures: list[str] = []
    elf = build_image("product", root / "product", ["NX_PRODUCT_BUILD=ON", *(f"{o}=OFF" for o in TEST_OPTIONS)],
                      env, failures)
    if elf is not None:
        check_product(elf, root / "product", failures)
    elf = build_image("testbuild", root / "testbuild", ["NX_PRODUCT_BUILD=OFF", *(f"{o}=ON" for o in TEST_OPTIONS)],
                      env, failures)
    if elf is not None:
        check_testbuild(elf, failures)
    check_refused(root / "refused", env, failures)
    check_demo(demo, failures)
    for f in failures:
        print("FAIL", f)
    print(f"product build: {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
