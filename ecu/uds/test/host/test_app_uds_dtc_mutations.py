"""Meaningfulness of the host test of 19 and 14 (NVM-04): deliberately broken copies of FaultMemoryServices.cpp.

Each mutation is compiled into its own DLL (build_app_uds_host.SOURCE_OVERRIDES, the source in the repository stays
untouched) and FaultMemoryServicesTest of test_app_uds_dtc.py runs against it; every mutation must make it fail.
Runtime about 10 minutes (one DLL build per mutation).

    .venv/Scripts/python ecu/uds/test/host/test_app_uds_dtc_mutations.py
"""

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
import build_app_uds_host  # noqa: E402
import test_app_uds_dtc  # noqa: E402

SERVICES = ROOT / "ecu" / "uds" / "src" / "uds" / "app" / "FaultMemoryServices.cpp"

# name: (old, new); every `old` must occur exactly once in the source
MUTATIONS = {
    "records of a DTC in the order of the table, not ascending": (
        "((found < 0) || (number < static_cast<int32_t>(records[found].number)))", "(found < 0)"),
    "status mask not applied (19 02)": (
        "(info.status & static_cast<uint8_t>(statusMask & ::dem::status::AVAILABILITY_MASK))", "(0xFFU)"),
    "19 14 lists failed DTCs too": ("(info.fdc < FDC_FAILED)", "(info.fdc <= FDC_FAILED)"),
    "extended record with an identifier reported without entry": ("if (hasIdentifiers && !stored)", "if (false)"),
    "snapshot data not placed behind its identifier": ("(void)::memmove(&out[pos], &out[src], bytes);", "(void)src;"),
    "a group clears every DTC": ("if ((number >= found->first) && (number <= found->last))", "if (number != 0U)"),
    "unknown snapshot record accepted": (
        "|| (findRecord(cfg.snapshots, cfg.snapshotCount, record) >= 0))", "|| true)"),
}


class FaultMemoryServicesMutationTest(unittest.TestCase):
    def test_mutations_are_found(self):
        source = SERVICES.read_text(encoding="utf-8")
        original = test_app_uds_dtc.dtc_dll
        try:
            for index, (name, (old, new)) in enumerate(MUTATIONS.items()):
                with self.subTest(mutation=name):
                    self.assertEqual(source.count(old), 1, f"mutation text not found once: {old}")
                    tmp = Path(tempfile.mkdtemp())
                    try:
                        mutant = tmp / SERVICES.name
                        mutant.write_text(source.replace(old, new), encoding="utf-8")
                        build_app_uds_host.SOURCE_OVERRIDES[SERVICES] = mutant
                        dll = build_app_uds_host.build(tmp / f"app_uds_host_mutant_{index}.dll",
                                                       test_app_uds_dtc.OTHER_PROJECT, diag=test_app_uds_dtc.PROFILE,
                                                       dem=True)
                        test_app_uds_dtc.dtc_dll = lambda: dll
                        suite = unittest.defaultTestLoader.loadTestsFromTestCase(
                            test_app_uds_dtc.FaultMemoryServicesTest)
                        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
                    finally:
                        build_app_uds_host.SOURCE_OVERRIDES.pop(SERVICES, None)
                        shutil.rmtree(tmp, ignore_errors=True)   # the loaded DLL stays until the process ends
                    self.assertFalse(result.wasSuccessful(), f"mutation '{name}' was not detected")
                    failed = sorted(t.id().rsplit(".", 1)[-1] for t, _ in result.failures + result.errors)
                    print(f"  detected: {name}: {', '.join(failed)}")
        finally:
            test_app_uds_dtc.dtc_dll = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
