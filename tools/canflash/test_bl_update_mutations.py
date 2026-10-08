"""Meaningfulness of the bootloader host tests: deliberately broken variants must fail them.

Mutations of the updater core (bootloader/updater) are checked against test_bl_update_host.py, mutations of the
compatibility check (bootloader/src/core, tools/canflash, BL-02) against test_bootloader_host.py. Each mutation is
applied to a copy of bootloader/ and tools/canflash, built as host DLL and the host test run against it.
Runtime about four minutes. Run: .venv/Scripts/python tools/canflash/test_bl_update_mutations.py
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
from test_bl_update_host import build_dll  # noqa: E402

CORE = "bootloader/updater/src/core/bl_update.c"
# family part of the updater, s32k1xx (BL-08): image start checks and stub/early phrases
FAMILY_CHECK = "bootloader/platforms/s32k1xx/upd_family_check_image.inc"
FAMILY = "bootloader/platforms/s32k1xx/upd_family.h"

# name: [(file, old, new), ...]; every `old` must occur exactly once
MUTATIONS = {
    "body written without stub in sector 0": [
        (CORE, "if (!write_sector(first.start, stubBuf, first.size, ORDER_STUB, &stats->stubUs, stats))",
         "if (false)"),
    ],
    "stub reset vector programmed last": [
        (CORE, "        if (order == ORDER_STUB)\n", "        if (false)\n"),
        (CORE, "    if (order == ORDER_FINAL)\n", "    if (order != ORDER_BODY)\n"),
    ],
    "FCF not programmed before the reset vector": [
        (CORE, "if (!(true UPD_STUB_EARLY_PHRASES(PROGRAM_EARLY_PHRASE)))", "if (false)"),
        (CORE, "((off == 0U) UPD_STUB_EARLY_PHRASES(IS_EARLY_PHRASE))", "(off == 0U)"),
    ],
    # BL-08: the same through the family part - a family list without the FCF phrases
    "family names no early phrases": [
        (FAMILY, "#define UPD_STUB_EARLY_PHRASES(X) X(UPD_FCF_ADDR) X(UPD_FCF_ADDR + BL_PHRASE_SIZE)",
         "#define UPD_STUB_EARLY_PHRASES(X)"),
    ],
    "stub without the reference FCF": [
        (FAMILY, "    (void)memcpy(&stub[UPD_FCF_ADDR], fcfReference, UPD_FCF_SIZE);\n", ""),
    ],
    "FCF check removed": [
        (FAMILY_CHECK, "if (memcmp(&img[UPD_FCF_ADDR], fcfReference, UPD_FCF_SIZE) != 0)", "if (false)"),
    ],
    "FSEC only checked for the SEC bits": [
        (FAMILY_CHECK, "if (memcmp(&img[UPD_FCF_ADDR], fcfReference, UPD_FCF_SIZE) != 0)",
         "if ((img[UPD_FCF_ADDR + 12U] & 0x03U) != 0x02U)"),
    ],
    "vector check removed": [
        (FAMILY_CHECK, "if ((sp <= BL_RAM_START) || (sp > BL_RAM_END))", "if (false)"),
    ],
    "version check removed": [
        (CORE, "if (!version_compatible(img, size))", "if (false)"),
    ],
    "no read-back verify": [
        (CORE, "if (ok && (memcmp(upd_flash_ptr(base), buf, sectorSize) == 0))", "if (ok)"),
    ],
    "record invalidated before writing": [
        (CORE, "    upd_report(UPD_STEP_CHECKED, 0U, stats);\n",
         "    upd_report(UPD_STEP_CHECKED, 0U, stats);\n    (void)invalidate_record();\n"),
    ],
    "resume not detected": [
        (CORE, "bool const resumed = memcmp(upd_flash_ptr(BL_FLASH_START), stubBuf, BL_PHRASE_SIZE) == 0;",
         "bool const resumed = false;"),
    ],
    "interrupted stub always erased again": [
        (CORE, "if ((order == ORDER_STUB) && (attempt == 0U) && sector_completable(base, buf, sectorSize))",
         "if (false)"),
    ],
    "erase outside the record sector": [
        (CORE, "        (void)upd_flash_erase_sector(record.start);",
         "        (void)upd_flash_erase_sector(record.start - record.size);"),
    ],
    "already-installed check only on the first phrase": [
        (CORE, "    return (memcmp(upd_flash_ptr(BL_FLASH_START), img, size) == 0)\n"
               "           && range_erased(BL_FLASH_START + size, UPD_BL_AREA_SIZE - size);",
         "    return memcmp(upd_flash_ptr(BL_FLASH_START), img, BL_PHRASE_SIZE) == 0;"),
    ],
    # PLAT-16: the sector walk of the area comes from the table
    "area walked with a fixed sector count": [
        (CORE, "        for (uint32_t base = first.start + first.size; base < (BL_FLASH_START + UPD_BL_AREA_SIZE);"
               " ++index)",
         "        for (uint32_t base = first.start + first.size; base < (first.start + 2U * first.size); ++index)"),
    ],
    "record sector not erased at the end": [
        (CORE, "    if (!invalidate_record())", "    if (false)"),
    ],
}


CANFLASH = "tools/canflash/canflash.py"

# canflash.enter_bootloader (COM-09 finding), checked with test_bootloader_host.py EnterBootloaderTest
CANFLASH_MUTATIONS = {
    "enter_bootloader accepts the application's answer": [
        (CANFLASH, "            if silent_after_answer:\n", "            if True:\n"),
    ],
    "enter_bootloader waits for silence only": [
        (CANFLASH, "            if answered_in_row >= confirmations:\n", "            if False:\n"),
    ],
}

IMAGE = "bootloader/src/core/app_image.c"
# application start rule of the Cortex-M families (BL-08), compiled inside app_image.c
ENTRY = "bootloader/platforms/common/arch/cortex_m_app_entry.inc"
UDS = "bootloader/src/core/uds.c"

# Compatibility of application and bootloader (BL-02), checked with test_bootloader_host.py BootloaderTest
COMPAT_MUTATIONS = {
    "record compatibility not checked at boot": [
        (IMAGE, "    if (app_record_compat(record) != APP_COMPAT_OK)", "    if (false)"),
    ],
    "board/derivative not compared": [
        (IMAGE, "    if ((targetId != 0U) && (BL_TARGET_ID != 0UL) && (targetId != (uint32_t)BL_TARGET_ID))",
         "    if (false)"),
    ],
    "application area not compared": [
        (IMAGE, "    if ((appStart != BL_APP_START) || (appEnd != BL_APP_END) || (recordAddr != BL_APP_RECORD_ADDR))",
         "    if (false)"),
    ],
    "memory layout not compared": [
        (IMAGE, "    if (layoutId != app_layout_id())", "    if (false)"),
    ],
    "damaged compatibility block accepted": [
        (IMAGE, "    if ((record->compatSize != APP_COMPAT_SIZE)\n"
                "        || (record->compatCrc != crc32_update(0U, (uint8_t const*)&record->compatMagic, "
                "APP_COMPAT_SIZE - 4U)))",
         "    if (false)"),
    ],
    "application start not checked (BL-08 family hook)": [
        (ENTRY, "    return (sp > BL_RAM_START) && (sp <= BL_RAM_END) && ((reset & 1U) != 0U)",
         "    return (sp == sp) && ((reset & 1U) == (reset & 1U))"),
    ],
    "reset handler without Thumb bit accepted": [
        (ENTRY, "&& ((reset & 1U) != 0U)", ""),
    ],
    "missing compatibility block reported as checked": [
        (IMAGE, "        return APP_COMPAT_ABSENT;", "        return APP_COMPAT_OK;"),
    ],
    "layout identity over the wrong values": [
        (IMAGE, "BL_APP_END,     BL_APP_RECORD_ADDR, BL_RAM_START,   BL_RAM_END, BL_BOOT_REQUEST_ADDR};",
         "BL_APP_END,     BL_APP_RECORD_ADDR, BL_RAM_START,   BL_RAM_END, 0UL};"),
    ],
    "fingerprint not written into the record (BL-10)": [
        (UDS, "uds->fingerprintValid ? uds->fingerprint : NULL", "NULL"),
    ],
    "damaged fingerprint block accepted (BL-10)": [
        (IMAGE, "        || (record.fpCrc != crc32_update(0U, (uint8_t const*)&record.fpMagic, "
                "APP_FINGERPRINT_SIZE - 4U)))", "        )"),
    ],
    "declared mismatch does not block programming": [
        (UDS, "    if (uds->compatDeclared && (uds->compatResult != APP_COMPAT_OK))", "    if (false)"),
    ],
    "canflash does not check before erasing": [
        # the call sits in front of the erase behind the version check (TOOL-05, local= since BL-24)
        (CANFLASH, "            check_compatibility(client, log, identification, local=local_check)\n",
         "            pass\n"),
    ],
    # BL-24: the check moved to 0xF1A0, 0xFF02 stays for testers and bootloaders from before (transition)
    "former compatibility routine no longer answered (BL-24)": [
        (UDS, "(rid == RID_CHECK_COMPAT) || (rid == RID_CHECK_COMPAT_FORMER)", "(rid == RID_CHECK_COMPAT)"),
    ],
    "compatibility check still only under the former routine (BL-24)": [
        (UDS, "(rid == RID_CHECK_COMPAT) || (rid == RID_CHECK_COMPAT_FORMER)", "(rid == RID_CHECK_COMPAT_FORMER)"),
    ],
    "compatibility answer names a fixed routine (BL-24)": [
        (UDS, "{0x71U, 0x01U, req[2], req[3], (uint8_t)uds->compatResult}",
         "{0x71U, 0x01U, 0xF1U, 0xA0U, (uint8_t)uds->compatResult}"),
    ],
    "canflash sends the new routine to every bootloader (BL-24)": [
        (CANFLASH, "    return RID_CHECK_COMPAT if version >= COMPAT_ROUTINE_SINCE else RID_CHECK_COMPAT_FORMER",
         "    return RID_CHECK_COMPAT"),
    ],
    "canflash sends the former routine to every bootloader (BL-24)": [
        (CANFLASH, "    return RID_CHECK_COMPAT if version >= COMPAT_ROUTINE_SINCE else RID_CHECK_COMPAT_FORMER",
         "    return RID_CHECK_COMPAT_FORMER"),
    ],
    "canflash without fallback when the version is unknown (BL-24)": [
        (CANFLASH, "        if e.response.code != 0x31:\n            raise\n", "        raise\n"),
    ],
    "canflash ignores the board/derivative": [
        (CANFLASH, '    if image["target"] and target["target"] and image["target"] != target["target"]:',
         "    if False:"),
    ],
    "canflash writes no board/derivative into the record": [
        (CANFLASH, "COMPAT_MAGIC, COMPAT_VERSION, COMPAT_SIZE, CFG[TARGET_KEY], layout_id()",
         "COMPAT_MAGIC, COMPAT_VERSION, COMPAT_SIZE, 0, layout_id()"),
    ],
    "canflash cuts a hex above the application area": [
        (CANFLASH, "    above = [a for a in ih.addresses() if a >= end]", "    above = []"),
    ],
}

ISOTP = "bootloader/src/core/isotp.c"

# BL-25, checked with test_bootloader_host.py BootloaderTest like the compatibility mutations above
BL25_MUTATIONS = {
    # both halves of the fix at once: each alone keeps the receiver working (frames stamped with the poll's time,
    # and a stamp "after" now never expires a timer), which is intended
    "ISO-TP frames stamped with a newer reading than the timers (state before BL-25)": [
        (ISOTP, "on_frame(id, data, len, nowMs);", "on_frame(id, data, len, hal_millis());"),
        (ISOTP, "if ((elapsed <= 0x7FFFFFFFUL) && (elapsed > limitMs))", "if (elapsed > limitMs)"),
    ],
    "response pending before every sector again (state before BL-25)": [
        (UDS, "            if (eraseKb != 0U)\n            {\n",
         "            if (eraseKb != 0U)\n            {\n                uds->jobPendingDue = true;\n"),
    ],
}


SECTORS = "bootloader/src/core/bl_sectors.h"

# PLAT-16: the sector table of the core, checked with test_bootloader_host.py BootloaderStm32F446Test (the layout
# with sectors of different sizes - on a flash with one sector size a mask and the table behave the same, which is
# exactly why these mutations need that layout).
SECTOR_MUTATIONS = {
    "erase range rounded with a mask again (state before PLAT-16)": [
        (UDS, "        uint32_t from = 0U;\n        uint32_t to   = 0U;\n"
              "        if (!bl_sector_range(start, size, &from, &to) || (from < BL_APP_START) || (to > BL_APP_END))\n"
              "        {\n            send_nrc(uds, req[0], NRC_REQUEST_OUT_OF_RANGE, false, nowMs);\n"
              "            return;\n        }\n",
         "        uint32_t const from = start & ~(BL_SECTOR_MAX_SIZE - 1UL);\n"
         "        uint32_t const to   = (start + size + BL_SECTOR_MAX_SIZE - 1UL) & ~(BL_SECTOR_MAX_SIZE - 1UL);\n"),
    ],
    "erase steps on by a fixed sector size": [
        (UDS, "                uds->jobPos        = sector.start + sector.size;",
         "                uds->jobPos        = sector.start + BL_BOOT_SECTOR_MAX_SIZE;"),
    ],
    # BL-25 replaced "a pending response before every sector" by a prediction from the cost per KB and the size
    # of the next sector; predicted from the size of the measured sector instead, the 128-KB sectors behind the
    # small record sector are underestimated and the pause exceeds P2*
    "next erase not predicted from the size of its sector": [
        (UDS, "(((uds->eraseMs + 1UL) * eraseKb) < (budgetMs * uds->eraseKb))",
         "(((uds->eraseMs + 1UL) * uds->eraseKb) < (budgetMs * uds->eraseKb))"),
    ],
    "sector lookup off by one": [
        (SECTORS, "            uint32_t const n = (address - group->start) / group->size;",
         "            uint32_t const n = ((address - group->start) / group->size) + 1U;"),
    ],
    "sector range takes the byte behind the range": [
        (SECTORS, "!bl_sector_at(start + (size - 1U), &last)", "!bl_sector_at(start + size, &last)"),
    ],
}


# PLAT-62: the eval board demo where it exists, else the own full test profile (same memory layout) - the eval board
# is private (PLAT-46 F10) and missing in the exported open tree
EVAL_PROJECT = next(p for p in (ROOT / "projects" / "evalboard_s32k144_demo" / "project.yaml",
                                ROOT / "tools/nxconfig/test/profiles/projects/testproject_full/project.yaml")
                    if p.is_file())


def build_bl_host_dll(out: Path, source: Path) -> Path:
    """Bootloader host DLL with the eval board memory layout; returns the generated bl_memory.h (ARCH-08)."""
    import hostbuild
    layout = hostbuild.generate_layout(EVAL_PROJECT, out.parent / "gen")
    hostbuild.build_bl_host(out, layout, source, werror=False)
    return layout / "bootloader" / "bl_memory.h"


def run_host_test(source: Path, dll: Path, werror: bool = True) -> subprocess.CompletedProcess:
    build_dll(dll, source, werror)  # mutations may leave unused code behind
    env = dict(os.environ, UPD_HOST_DLL=str(dll), PYTHONUTF8="1")
    return subprocess.run([sys.executable, str(HERE / "test_bl_update_host.py")], env=env, capture_output=True, text=True)


def copy_sources(dst: Path) -> None:
    # tools/nxconfig and tools/bswgen come along unmutated: the host builds read the configuration model and
    # render the generated headers (bl_memory.h, bl_diag.h) with the generator's own templates (COM-11)
    # bootloader/platforms: family hooks the host builds compile the cores with (BL-08)
    # mcudb: chip UID of the synthetic layouts without `uid:` (hostbuild.test_layout_uid, BL-03)
    # libs/bsw, boards and the eval board project: the configuration of the copy resolves the same modules and
    # devices as the original, so the project test_bootloader_host.py picks inside the copy (EVAL_PROJECT) is the one
    # the host DLL is built for; without them it falls back to the full test profile and its modules are missing
    # (the project is private and left out where it does not exist - PLAT-46 F10, PLAT-62)
    for sub in ("bootloader/include", "bootloader/src/core", "bootloader/platforms", "bootloader/test",
                "bootloader/updater", "tools/canflash", "tools/nxconfig", "tools/bswgen", "mcudb", "libs", "platforms", "boards",
                "projects/evalboard_s32k144_demo"):
        if (ROOT / sub).is_dir():
            shutil.copytree(ROOT / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))


def apply(test: unittest.TestCase, root: Path, edits) -> None:
    for file, old, new in edits:
        path = root / file
        text = path.read_text()
        test.assertEqual(text.count(old), 1, f"mutation does not apply: {old!r}")
        path.write_text(text.replace(old, new))


class MutationTest(unittest.TestCase):
    def test_unmodified_copy_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy_sources(Path(tmp))
            result = run_host_test(Path(tmp), Path(tmp) / "upd_host.dll")
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_mutations_are_detected(self):
        for name, edits in MUTATIONS.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                copy_sources(Path(tmp))
                apply(self, Path(tmp), edits)
                result = run_host_test(Path(tmp), Path(tmp) / "upd_host.dll", werror=False)
                failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", result.stderr, re.M)))
                print(f"\n  {name}: {len(failed)} failing test(s): {', '.join(failed)}", end="")
                self.assertNotEqual(result.returncode, 0, f"{name} not detected")

    def test_compatibility_mutations_are_detected(self):
        """BL-02: every broken compatibility check must fail test_bootloader_host.py (eval board layout); the same
        for the receive path and the response pending spacing of BL-25, and the choice of the routine identifier
        of BL-24 (CanflashComfortTest flashes a bootloader from before BL-24)."""
        for name, edits in {"unmodified": [], **COMPAT_MUTATIONS, **BL25_MUTATIONS}.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as src:
                copy_sources(Path(src))
                apply(self, Path(src), edits)
                dll = Path(src) / "bl_host.dll"
                import hostbuild
                layout = hostbuild.generate_layout(EVAL_PROJECT, dll.parent / "gen")
                hostbuild.build_bl_host(dll, layout, Path(src), werror=False)
                env = dict(os.environ, BL_HOST_DLL=str(dll), NX_BL_LAYOUT=str(layout / "bootloader" / "bl_memory.h"),
                           PYTHONUTF8="1")
                env.pop("NX_BUILD_DIR", None)   # the real application hex belongs to the unmutated bootloader
                result = subprocess.run([sys.executable, str(Path(src) / "tools" / "canflash" / "test_bootloader_host.py"),
                                         # BL-24: the flash runs against a bootloader from before live there
                                         "BootloaderTest", "CanflashComfortTest"],
                                        env=env, capture_output=True, text=True)
                failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", result.stderr, re.M)))
                print(f"\n  {name}: {len(failed)} failing test(s): {', '.join(failed)}", end="")
                self.assertEqual(result.returncode == 0, not edits, result.stderr[-1500:])

    def test_sector_table_mutations_are_detected(self):
        """PLAT-16: every broken use of the sector table must fail test_bootloader_host.py for the layout with
        sectors of different sizes (BootloaderStm32F446Test builds its own DLL from the mutated copy)."""
        with tempfile.TemporaryDirectory() as shared:
            import hostbuild
            # the test module needs an eval board layout at import time (its project files are not copied)
            eval_layout = (hostbuild.generate_layout(EVAL_PROJECT, Path(shared) / "gen")
                           / "bootloader" / "bl_memory.h")
            for name, edits in {"unmodified": [], **SECTOR_MUTATIONS}.items():
                with self.subTest(name), tempfile.TemporaryDirectory() as src:
                    copy_sources(Path(src))
                    apply(self, Path(src), edits)
                    env = dict(os.environ, NX_BL_LAYOUT=str(eval_layout), PYTHONUTF8="1")
                    env.pop("NX_BUILD_DIR", None)
                    env.pop("BL_HOST_DLL", None)
                    result = subprocess.run([sys.executable,
                                             str(Path(src) / "tools" / "canflash" / "test_bootloader_host.py"),
                                             "BootloaderStm32F446Test"], env=env, capture_output=True, text=True)
                    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", result.stderr, re.M)))
                    print(f"\n  {name}: {len(failed)} failing test(s): {', '.join(failed)}", end="")
                    self.assertEqual(result.returncode == 0, not edits, result.stderr[-1500:])

    def test_canflash_mutations_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dll = Path(tmp) / "bl_host.dll"
            layout = build_bl_host_dll(dll, ROOT)
            for name, edits in {"unmodified": [], **CANFLASH_MUTATIONS}.items():
                with self.subTest(name), tempfile.TemporaryDirectory() as src:
                    copy_sources(Path(src))
                    apply(self, Path(src), edits)
                    env = dict(os.environ, BL_HOST_DLL=str(dll), NX_BL_LAYOUT=str(layout), PYTHONUTF8="1")
                    result = subprocess.run([sys.executable, str(Path(src) / "tools" / "canflash" / "test_bootloader_host.py"),
                                             "EnterBootloaderTest"], env=env, capture_output=True, text=True)
                    failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (\w+)", result.stderr, re.M)))
                    print(f"\n  {name}: {len(failed)} failing test(s): {', '.join(failed)}", end="")
                    self.assertEqual(result.returncode == 0, not edits, result.stderr[-1500:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
