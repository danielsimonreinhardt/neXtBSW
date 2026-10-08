"""Meaningfulness of the E2E host tests: deliberately broken variants of libs/bsw/e2e must fail them (COM-10).

Each mutation is applied to a copy of the library. Both test layers run against it and are reported separately:
  C    libs/bsw/e2e/test/host/E2eHostTest.c (specification examples and behaviour, no Python)
  ref  tools/e2e/test_e2e_host.py ScenarioCompareTest/ConfigCompareTest/CrcCompareTest (Python reference model)
A mutation counts as detected if at least one layer fails; the summary shows which one did.
Runtime a few minutes. Run: .venv/Scripts/python tools/e2e/test_e2e_mutations.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from test_e2e_host import build_host_test  # noqa: E402

SRC = "libs/bsw/e2e/src/e2e/"
E2E, P01, P02, P11 = SRC + "E2E.c", SRC + "E2E_P01.c", SRC + "E2E_P02.c", SRC + "E2E_P11.c"
NIBBLE_COMPARE = "(e2e_readNibble(data, config->DataIDNibbleOffset) == ((config->DataID >> 8U) & 0x0FU))"

# name: [(file, old, new), ...]; every `old` must occur exactly once in the file
MUTATIONS = {
    "CRC8 polynomial 0x1C": [(E2E, "0x1DU);", "0x1CU);")],
    "CRC8 initial value 0x00": [(E2E, "#define CRC8_INITIAL_VALUE ((uint8_t)0xFFU)", "#define CRC8_INITIAL_VALUE ((uint8_t)0x00U)")],
    "CRC call sequence without XOR compensation": [
        (E2E, "(uint8_t)(startValue ^ CRC8_XOR_VALUE)", "startValue")],
    "P01/P11 CRC without final XOR 0xFF": [(E2E, "    return (uint8_t)(crc ^ 0xFFU);\n}", "    return crc;\n}")],
    "P01/P11 CRC byte included in the CRC": [
        (E2E, "crc = E2E_Crc8(&data[crcByte + 1U], (uint32_t)length - crcByte - 1U, crc, false);",
         "crc = E2E_Crc8(&data[crcByte], (uint32_t)length - crcByte, crc, false);")],
    "P01/P11 data before the CRC byte left out": [(E2E, "    if (crcByte > 0U)\n", "    if (false)\n")],
    "nibble offset: low and high nibble swapped on read": [
        (E2E, "((bitOffset % 8U) == 0U) ? (uint8_t)(byte & 0x0FU)", "((bitOffset % 8U) != 0U) ? (uint8_t)(byte & 0x0FU)")],
    "layout check: counter inside the CRC byte allowed": [
        (E2E, "&& ((counterOffset / 8U) != (crcOffset / 8U));", ";")],
    "legacy: SYNC after too many repetitions reported as OK": [
        (E2E, "            status              = LEGACY_STATUS_SYNC;", "            status              = LEGACY_STATUS_OK;")],
    "legacy: no resynchronisation after wrong sequence": [
        (E2E, "            *state->lastValidCounter = receivedCounter;\n        }\n        status = LEGACY_STATUS_WRONGSEQUENCE;",
         "        }\n        status = LEGACY_STATUS_WRONGSEQUENCE;")],
    "legacy: SyncCounter not decremented": [(E2E, "                --(*state->syncCounter);\n", "")],
    "legacy: no-new-or-repeated counter not saturated": [
        (E2E, "if (*counter < (uint8_t)(counterModulo - 1U))", "if (*counter < 200U)")],
    "P01 Data ID bytes in wrong order (BOTH)": [(P01, "            id[0] = low;\n            id[1] = high;",
                                                 "            id[0] = high;\n            id[1] = low;")],
    "P01 ALT parity inverted": [(P01, "((counter % 2U) == 0U) ? low : high", "((counter % 2U) == 0U) ? high : low")],
    "P01 NIBBLE uses the high byte in the CRC": [(P01, "            id[1] = 0U;", "            id[1] = high;")],
    "P01 Data ID nibble not compared": [(P01, NIBBLE_COMPARE, "true")],
    "P01 received counter 15 accepted": [(P01, "if (receivedCounter >= P01_COUNTER_MODULO)", "if (false)")],
    "P01 tolerance not widened per check": [(P01, "        ++state->MaxDeltaCounter;\n", "")],
    "P01 counter wraps after 15": [(P01, "#define P01_COUNTER_MODULO     15U", "#define P01_COUNTER_MODULO     16U")],
    "P01 map: profile behaviour of INITIAL swapped": [
        (P01, "case E2E_P01STATUS_INITIAL: return profileBehavior ? E2E_P_WRONGSEQUENCE : E2E_P_OK;",
         "case E2E_P01STATUS_INITIAL: return profileBehavior ? E2E_P_OK : E2E_P_WRONGSEQUENCE;")],
    "P02 Data ID of the next counter": [(P02, "config->DataIDList[state->Counter]", "config->DataIDList[(state->Counter + 1U) & 0x0FU]")],
    "P02 Data ID before the data in the CRC": [
        (P02, "    uint8_t const crc = E2E_Crc8H2F(&data[1], (uint32_t)length - 1U, 0xFFU, true);\n    return E2E_Crc8H2F(&dataId, 1U, crc, false);",
         "    uint8_t const crc = E2E_Crc8H2F(&dataId, 1U, 0xFFU, true);\n    return E2E_Crc8H2F(&data[1], (uint32_t)length - 1U, crc, false);")],
    "P02 counter increment after use": [
        (P02, "    state->Counter = (state->Counter < 15U) ? (uint8_t)(state->Counter + 1U) : 0U;\n", ""),
        (P02, "    data[0]        = p02Crc(data, length, config->DataIDList[state->Counter]);\n",
         "    data[0]        = p02Crc(data, length, config->DataIDList[state->Counter]);\n"
         "    state->Counter = (state->Counter < 15U) ? (uint8_t)(state->Counter + 1U) : 0U;\n")],
    "P02 INITIAL without taking the counter": [
        (P02, "        state->LastValidCounter = receivedCounter;\n        state->Status           = E2E_P02STATUS_INITIAL;",
         "        state->Status           = E2E_P02STATUS_INITIAL;")],
    "P11 Data ID nibble not compared": [(P11, NIBBLE_COMPARE, "true")],
    "P11 NIBBLE uses the high byte in the CRC": [
        (P11, "(config->DataIDMode == E2E_P11_DATAID_NIBBLE) ? 0U : (uint8_t)(config->DataID >> 8U)",
         "(uint8_t)(config->DataID >> 8U)")],
    "P11 counter wraps after 15": [(P11, "#define P11_COUNTER_MODULO 15U", "#define P11_COUNTER_MODULO 16U")],
    "P11 repetition reported as OK": [(P11, "    else if (delta == 0U)", "    else if (false)")],
    "P11 receiver counter not updated": [(P11, "    state->Counter = receivedCounter;\n", "")],
    "P11 receiver starts with counter 0": [(P11, "    state->Counter = 0x0EU; /* SWS_E2E_00556 */", "    state->Counter = 0U;")],
    "P11 MaxDeltaCounter off by one": [(P11, "    if (delta > config->MaxDeltaCounter)", "    if (delta > (config->MaxDeltaCounter + 1U))")],
    "P11 shorter buffer accepted by Protect": [
        (P11, "(E2E_P11CheckConfig(config) != E2E_E_OK) || (length != (config->DataLength / 8U))\n        || (state->Counter",
         "(E2E_P11CheckConfig(config) != E2E_E_OK) || (length > (config->DataLength / 8U))\n        || (state->Counter")],
    "P11 12-bit Data ID check removed": [(P11, "&& (!nibble || ((config->DataID & 0xF000U) == 0U));", ";")],
}


def copy_sources(dst: Path) -> None:
    for sub in ("libs/bsw/e2e", "tools/e2e"):
        shutil.copytree(ROOT / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))


def apply(test: unittest.TestCase, root: Path, edits) -> None:
    for file, old, new in edits:
        path = root / file
        text = path.read_text(encoding="utf-8")
        test.assertEqual(text.count(old), 1, f"mutation does not apply to {file}: {old!r}")
        path.write_text(text.replace(old, new), encoding="utf-8")


def run_layers(source: Path, tmp: Path, werror: bool):
    """Returns (C host test failed, reference comparison failed, detail)."""
    exe = tmp / "e2e_test.exe"
    build = build_host_test(exe, source, werror)
    if build.returncode != 0:
        c_failed, c_detail = True, "build failed"
    else:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        c_failed, c_detail = run.returncode != 0, run.stdout.strip().splitlines()[-1]
    env = dict(os.environ, E2E_SOURCE=str(source), E2E_WERROR="1" if werror else "0", PYTHONUTF8="1", TEMP=str(tmp),
               TMP=str(tmp))
    ref = subprocess.run([sys.executable, "-m", "unittest", "test_e2e_host.ScenarioCompareTest",
                          "test_e2e_host.ConfigCompareTest", "test_e2e_host.CrcCompareTest"],
                         cwd=str(source / "tools" / "e2e"), env=env, capture_output=True, text=True)
    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", ref.stderr, re.M)))
    return c_failed, ref.returncode != 0, f"C: {c_detail}; ref: {', '.join(failed) or 'ok'}"


class MutationTest(unittest.TestCase):
    def test_unmodified_copy_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy_sources(Path(tmp))
            c_failed, ref_failed, detail = run_layers(Path(tmp), Path(tmp), werror=True)
            self.assertFalse(c_failed or ref_failed, detail)

    def test_mutations_are_detected(self):
        summary = {"C": 0, "ref": 0, "both": 0}
        for name, edits in MUTATIONS.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                copy_sources(Path(tmp))
                apply(self, Path(tmp), edits)
                c_failed, ref_failed, detail = run_layers(Path(tmp), Path(tmp), werror=False)
                summary["C"] += c_failed
                summary["ref"] += ref_failed
                summary["both"] += c_failed and ref_failed
                print(f"\n  {name}: {detail}", end="")
                self.assertTrue(c_failed or ref_failed, f"{name} not detected")
        print(f"\n  {len(MUTATIONS)} mutations, detected by C host test: {summary['C']}, by reference comparison: "
              f"{summary['ref']}, by both: {summary['both']}", end="")


if __name__ == "__main__":
    unittest.main(verbosity=2)
