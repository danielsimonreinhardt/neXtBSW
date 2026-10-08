# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of safe-supervisor-degraded (SYS-117): broken variants of the degraded reaction SD must fail it, and
configurations that would degrade a fail-safe event must not even compile.

Mutants: each one is applied to a copy of src/safeSupervisor/SafeSupervisor.cpp or include/safeSupervisor/Degraded.h;
the host test (test/host/SafeSupervisorDegradedHostTest.cpp, copied along unchanged together with DegradedRte.cpp) is
compiled against the copies with zig c++ exactly as the tools/test entry safe-supervisor-degraded does, and run. A
mutated header lies in an include directory in front of the module's own one. A mutant counts as detected if the test
exits non-zero or hangs; a mutant that does not compile is a fault of this script.

SYS-138: the same test carries the supervisor's read-back of the safe state (degraded groups, hold of S1, S1 + S2 before
the reset) against a faked family port; its mutants are in the list as well.

Refused configurations: the unchanged sources with a generated mapping of an event that is not on the closed list (the
MCU-internal events ROM_CHECK_FAILURE, SAFE_WATCHDOG_SERVICE_DEVIATION, INTEGRITY_EVENT_DMA_ECC_ERROR and the
report-only DEVICE_REPORT), without a group, with a group or bus group the project does not have, or with more
mappings than the supervisor holds - each must stop the compiler with the SYS-117 message of its static_assert.
SYS-135: likewise a mapping of the report HW_SHUTOFF in an image without a hardware shut-off path.

Run: .venv/Scripts/python ecu/safety/safeSupervisor/test/test_degraded_mutations.py (about 1 min)
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
ROOT = HERE.parents[3]
SRC = "src/safeSupervisor/SafeSupervisor.cpp"
HDR = "include/safeSupervisor/Degraded.h"
TEST = "test/host/SafeSupervisorDegradedHostTest.cpp"
RTE = "src/safeSupervisor/DegradedRte.cpp"
RUN_LIMIT_S = 60

# name: (file, [(old, new, occurrences), ...]); `old` must occur exactly `occurrences` times in that file
MUTATIONS = {
    "SD for a device fault of class IDENTITY": (SRC, [
        ("    if (!device::isWellFormed(word)\n"
         "        || (device::faultOf(word) == static_cast<uint8_t>(device::FaultClass::IDENTITY)))\n"
         "    {\n        return false;\n    }\n    uint8_t const bus     = device::busOf(word);",
         "    if (!device::isWellFormed(word))\n    {\n        return false;\n    }\n"
         "    uint8_t const bus     = device::busOf(word);", 1),
    ]),
    "SD for a malformed word": (SRC, [
        ("    if (!device::isWellFormed(word)\n        || (device::faultOf(word)",
         "    if (false\n        || (device::faultOf(word)", 1),
    ]),
    "bus of the word not compared with the configured bus": (SRC, [
        ("        if (degraded::mappingBus(mapping) != bus)",
         "        if ((degraded::mappingBus(mapping) != bus) && false)", 1),
    ]),
    "lost bus ignored (only the device's own groups)": (SRC, [
        ("groups = busLost ? busGroups : degraded::mappingGroups(mapping);",
         "groups = (busLost && false) ? busGroups : degraded::mappingGroups(mapping);", 1),
    ]),
    "no escalation to S1 at the last group": (SRC, [
        ("    bool const last      = (now == DEGRADED_ALL_GROUPS);", "    bool const last      = false;", 1),
    ]),
    "latch set before the outputs": (SRC, [
        ("    bool const last      = (now == DEGRADED_ALL_GROUPS);\n    if (last)",
         "    bool const last      = (now == DEGRADED_ALL_GROUPS);\n    setDegradedGroups(now);\n    if (last)", 1),
    ]),
    "groups do not accumulate (an earlier group returns)": (SRC, [
        ("    uint32_t const now   = (before | groups) & DEGRADED_ALL_GROUPS;",
         "    uint32_t const now   = groups & DEGRADED_ALL_GROUPS;", 1),
    ]),
    "SD walks the whole table": (SRC, [
        ("        ::safety::SafeState::enterDegradedState(entriesOf(now));",
         "        ::safety::SafeState::enterDegradedState(0xFFFFFFFFU);", 1),
    ]),
    "entries of a group left out": (SRC, [
        ("    add(2U, NX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_GROUP_2_ENTRIES);\n", "", 1),
    ]),
    "SD resets anyway": (SRC, [
        ("            if (degradeDeviceFault(word))\n            {\n                return;\n            }",
         "            if (degradeDeviceFault(word))\n            {\n            }", 1),
    ]),
    "degraded groups not held in the safety cycle": (SRC, [
        ("    holdDegradedGroups();\n", "", 1),
    ]),
    "degraded groups not re-applied at the start": (SRC, [
        ("    startDegraded(start.powerOn);\n", "", 1),
    ]),
    "record not written by SD": (SRC, [
        ("    degraded::write(degradedRecord, now, event, context);\n", "", 1),
    ]),
    "record not kept in step with the latch": (SRC, [
        ("    if (!recordValid || (kept != groups))", "    if (false)", 1),
    ]),
    "latch plausibility ignored by the reader": (SRC, [
        ("    if (((value ^ inverted) != 0xFFFFFFFFU) || ((value & ~DEGRADED_ALL_GROUPS) != 0U))",
         "    (void)inverted;\n    if ((value & ~DEGRADED_ALL_GROUPS) != 0U)", 1),
    ]),
    "implausible latch ignored in the safety cycle": (SRC, [
        ("    if (!plausible || ((kept & ~groups) != 0U))",
         "    if ((!plausible && false) || ((kept & ~groups) != 0U))", 1),
    ]),
    # IO-45: the lock per entry of the table (the enable of a gate driver and, SYS-123, the PWM group of a group)
    "entry lock ignores the latch": (SRC, [
        ("    return (groups & groupsOfEntry(entry)) != 0U;", "    return (groupsOfEntry(entry) & 0U) != 0U;", 1),
    ]),
    "entry lock: only the first entry of a group": (SRC, [
        ("        if ((entriesOf(1U << group) & (1U << entry)) != 0U)",
         "        if ((entriesOf(1U << group) & (1U << entry)) == (entriesOf(1U << group) & (0U - entriesOf(1U << group))))",
         1),
    ]),
    "entry lock: implausible latch ignored": (SRC, [
        ("    (void)degradedGroups(groups); // implausible", "    groups = _degradedGroups; // implausible", 1),
    ]),
    # SYS-123: the walk right after the BSP start
    "apply after the BSP start does nothing": (SRC, [
        ("    (void)degradedGroups(groups);\n    if (groups == 0U)\n    {\n        return;\n    }\n    if (groups == "
         "DEGRADED_ALL_GROUPS)\n    {\n        ::safety::SafeState::enterSafeState();\n    }\n    else\n    {\n"
         "        ::safety::SafeState::enterDegradedState(entriesOf(groups));\n    }\n}",
         "    (void)degradedGroups(groups);\n    (void)groups;\n}", 1),
    ]),
    "apply after the BSP start walks a partial set as S1": (SRC, [
        ("    if (groups == DEGRADED_ALL_GROUPS)\n    {\n        ::safety::SafeState::enterSafeState();\n    }\n    else\n"
         "    {\n        ::safety::SafeState::enterDegradedState(entriesOf(groups));\n    }\n}\n\nvoid SafeSupervisor::"
         "setDegradedGroups",
         "    ::safety::SafeState::enterSafeState();\n}\n\nvoid SafeSupervisor::setDegradedGroups", 1),
    ]),
    # SYS-138 (review K6): read-back of the degraded groups, of the hold of S1 and before the reset of S1 + S2
    "SYS-138: degraded groups not read back": (SRC, [
        ("        uint32_t const unconfirmed = ::safety::degradedStateReadback(entriesOf(groups));",
         "        uint32_t const unconfirmed = 0U;", 1),
    ]),
    "SYS-138: degraded read-back asks for the group bits, not their entries": (SRC, [
        ("::safety::degradedStateReadback(entriesOf(groups))", "::safety::degradedStateReadback(groups)", 1),
    ]),
    "SYS-138: degraded groups not confirmed, but no reset": (SRC, [
        ("            lockRegisterMonitor.trigger(unconfirmed);\n            return;", "            return;", 1),
    ]),
    "SYS-138: degraded read-back after the walk of the cycle": (SRC, [
        ("        uint32_t const unconfirmed = ::safety::degradedStateReadback(entriesOf(groups));",
         "        ::safety::SafeState::enterDegradedState(entriesOf(groups));\n"
         "        uint32_t const unconfirmed = ::safety::degradedStateReadback(entriesOf(groups));", 1),
    ]),
    "SYS-138: hold without read-back": (SRC, [
        ("        uint32_t const unconfirmed = ::safety::safeStateReadback();",
         "        uint32_t const unconfirmed = 0U;", 1),
    ]),
    "SYS-138: hold read-back right after its own walk": (SRC, [
        ("        uint32_t const unconfirmed = ::safety::safeStateReadback();",
         "        ::safety::SafeState::enterSafeState();\n"
         "        uint32_t const unconfirmed = ::safety::safeStateReadback();", 1),
    ]),
    "SYS-138: hold goes on holding when S1 is not confirmed": (SRC, [
        ("            resetWithLine(\n                ResetLine{\"LOCK_REGISTER_ERROR\", \"register\", ResetDetail::DECIMAL, "
         "unconfirmed},\n                Event::LOCK_REGISTER_ERROR,\n                _resetLoopRecord);\n"
         "            return; // host test only",
         "            (void)unconfirmed; // host test only", 1),
    ]),
    "SYS-138: reset of an unconfirmed hold with the wrong cause": (SRC, [
        ("                Event::LOCK_REGISTER_ERROR,\n                _resetLoopRecord);",
         "                Event::ROM_CHECK_FAILURE,\n                _resetLoopRecord);", 1),
    ]),
    "SYS-138: console line before the reset although S1 is not confirmed": (SRC, [
        ("    bool const confirmed = (::safety::safeStateReadback() == 0U);", "    bool const confirmed = true;", 1),
    ]),
    "closed list extended by an MCU-internal event": (HDR, [
        ("#define NX_SAFE_DEGRADABLE_EVENTS(X) X(DEVICE_FAULT)",
         "#define NX_SAFE_DEGRADABLE_EVENTS(X) X(DEVICE_FAULT) X(ROM_CHECK_FAILURE)", 1),
    ]),
    "power-on keeps the record (a way back without power cycle is lost, stale groups stay)": (HDR, [
        ("    if (powerOn)\n    {", "    if (powerOn && false)\n    {", 1),
    ]),
    "implausible record read as nothing degraded": (HDR, [
        ("        write(record, allGroups, EVENT_IMPLAUSIBLE, 0U);\n        return StartResult{allGroups, false};",
         "        clear(record);\n        return StartResult{0U, false};", 1),
    ]),
    "check word does not cover the context": (HDR, [
        ("rotl(context, 21U)", "(context & 0U)", 1),
    ]),
    "record with a group outside the project accepted": (HDR, [
        ("    return ((groups & ~allGroups) == 0U) && (record.check",
         "    return (((groups & ~allGroups) == 0U) || true) && (record.check", 1),
    ]),
}

DEVICE_FAULT = 0x13
EVENTS = {"ROM_CHECK_FAILURE": 0x08, "SAFE_WATCHDOG_SERVICE_DEVIATION": 0x04, "INTEGRITY_EVENT_DMA_ECC_ERROR": 0x07,
          "DEVICE_REPORT": 0x12}


def mapping_defines(mappings: list[tuple[int, int]], count: int | None = None) -> list[str]:
    """-D flags of a generated mapping list [(word, bus groups)]."""
    flags = ["-DNX_TEST_MAPPINGS_OVERRIDE",
             f"-DNX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPINGS={count if count is not None else len(mappings)}U"]
    for n, (word, bus_groups) in enumerate(mappings):
        flags += [f"-DNX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_{n}=0x{word:08X}U",
                  f"-DNX_MODULE_SAFESUPERVISOR_DERIVED_DEGRADED_MAPPING_{n}_BUS_GROUPS=0x{bus_groups:X}U"]
    return flags


GOOD = (DEVICE_FAULT << 24) | (3 << 16) | (0x20 << 8) | 0x1
REFUSED = {
    **{f"mapping of {name}": (mapping_defines([((event << 24) | (3 << 16) | (0x20 << 8) | 0x1, 0)]),
                              "not degradable") for name, event in EVENTS.items()},
    "mapping without a group": (mapping_defines([((DEVICE_FAULT << 24) | (3 << 16) | (0x20 << 8), 0)]),
                                "without a group"),
    "mapping with a group the project does not have": (
        mapping_defines([((DEVICE_FAULT << 24) | (3 << 16) | (0x20 << 8) | 0x9, 0)]), "without a group"),
    "bus groups with a group the project does not have": (mapping_defines([(GOOD, 0x10)]), "bus groups"),
    "more mappings than the supervisor holds": (mapping_defines([(GOOD, 0)], count=17), "MAX_MAPPINGS"),
    # SYS-135: HW_SHUTOFF (report id 0xFE) mapped in an image without a hardware shut-off path (the host test is built
    # without NX_SAFE_SUPERVISOR_HW_SHUTOFF, like the image of a project without a threshold)
    "mapping of HW_SHUTOFF without the path in the image": (
        mapping_defines([((0xFE << 24) | (3 << 16) | (0xFF << 8) | 0x1, 0)]), "not degradable"),
}


def build(tree: Path, header_dir: Path | None, workdir: Path, name: str, extra: list[str]):
    """Returns (compiled, output, exe)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror", "-DPLATFORM_SUPPORT_IO", "-DPLATFORM_SUPPORT_WATCHDOG", *extra,
           *([f"-I{header_dir}"] if header_dir is not None else []),
           f"-I{MODULE / 'include'}",
           f"-I{MODULE / 'test/host/stubs'}",
           f"-isystem{ROOT / 'external/openbsw/libs/safety/safeMonitor/include'}",
           str(tree / TEST), "-o", str(exe)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    return result.returncode == 0, result.stdout + result.stderr, exe


def run(exe: Path):
    """Returns (passed, output)."""
    try:
        result = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return False, "hangs"
    return result.returncode == 0, result.stdout[-2000:]


def copy_tree(target: Path, mutated: tuple[str, str] | None = None):
    """The test, SafeSupervisor.cpp and DegradedRte.cpp at their relative places below `target`."""
    for rel in (SRC, TEST, RTE):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODULE / rel, target / rel)
    if mutated is not None:
        rel, text = mutated
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")


def mutate(rel: str, changes) -> str:
    text = (MODULE / rel).read_text(encoding="utf-8")
    for old, new, occurrences in changes:
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        text = text.replace(old, new)
    return text


class DegradedMutationTest(unittest.TestCase):
    maxDiff = None

    def test_mutants_are_detected_and_fail_safe_mappings_refused(self):
        work = Path(tempfile.mkdtemp(prefix="nx_sd_mut_"))
        try:
            original = work / "original"
            copy_tree(original)
            compiled, output, exe = build(original, None, work, "original", [])
            self.assertTrue(compiled, f"original does not compile:\n{output}")
            passed, output = run(exe)
            self.assertTrue(passed, f"original does not pass:\n{output}")

            survivors, invalid = [], []
            for index, (name, (rel, changes)) in enumerate(MUTATIONS.items()):
                tree = work / f"m{index}"
                text = mutate(rel, changes)
                if rel == SRC:
                    copy_tree(tree, (SRC, text))
                    header_dir = None
                else:
                    copy_tree(tree)
                    header_dir = tree / "mutated_include"
                    (header_dir / "safeSupervisor").mkdir(parents=True)
                    (header_dir / "safeSupervisor" / "Degraded.h").write_text(text, encoding="utf-8")
                compiled, output, exe = build(tree, header_dir, work, f"m{index}", [])
                if not compiled:
                    errors = [line for line in output.splitlines() if "error" in line]
                    invalid.append(f"{name}: {errors[0] if errors else output[-800:]}")
                    continue
                passed, _ = run(exe)
                if passed:
                    survivors.append(name)
            self.assertEqual(invalid, [], "mutants that do not compile (fault of this script)")
            self.assertEqual(survivors, [], "mutants the host test does not detect")

            accepted = []
            for index, (name, (flags, message)) in enumerate(REFUSED.items()):
                compiled, output, _ = build(original, None, work, f"r{index}", flags)
                if compiled or "SYS-117" not in output or message not in output:
                    accepted.append(f"{name}: compiled={compiled}\n{output[-800:]}")
            self.assertEqual(accepted, [], "configurations the compiler does not refuse")
            print(f"{len(MUTATIONS)} mutants detected, {len(REFUSED)} configurations refused")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
