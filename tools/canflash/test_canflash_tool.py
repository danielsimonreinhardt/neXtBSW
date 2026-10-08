"""Host tests of the canflash comfort functions (TOOL-05) without the bootloader DLL.

Progress output for terminal and log, version warnings, the quiet probe of DID 0xF1A0, bus timing from the
project, ECU selection and the TesterPresent scan. The end-to-end cases against the host bootloader (old
bootloader, several ECUs on one bus) are in test_bootloader_host.py.

    .venv/Scripts/python -m unittest tools/canflash/test_canflash_tool.py
"""

import contextlib
import datetime
import io
import json
import os
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import zlib
from pathlib import Path
from unittest import mock

import can
import isotp
import udsoncan
from can.interfaces.vector.exceptions import VectorOperationError
from can.interfaces.virtual import VirtualBus
from udsoncan.client import Client
from udsoncan.connections import QueueConnection

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blkeys  # noqa: E402
import canflash  # noqa: E402
import powercycle  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# PLAT-62: the own full test profile (diagnostic addresses and timing of the eval board demo) instead of the eval
# board project, which stays private (PLAT-46 F10) and is missing in the exported open tree
EVAL_PROJECT = ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
PRIVATE_PROJECTS = [p for p in (ROOT / "projects" / "evalboard_s32k144_demo" / "project.yaml",
                                ROOT / "projects" / "evalboard_s32k144_charge" / "project.yaml") if p.is_file()]


def collect():
    """A log function like print() that keeps what it was given, with end and flush."""
    lines: list[str] = []

    def log(*args, end="\n", flush=False):
        lines.append(" ".join(str(a) for a in args) + end)
    return lines, log


class ProgressTest(unittest.TestCase):
    """Finding of the board test: flash-can printed hundreds of '\\r  1024/143452 bytes' into one CMake log line."""

    SIZE, CHUNK = 143452, 1024

    def run_progress(self, mode):
        lines, log = collect()
        progress = canflash.Progress(self.SIZE, log, mode)
        for offset in range(0, self.SIZE, self.CHUNK):
            progress.update(min(offset + self.CHUNK, self.SIZE))
        progress.finish()
        return lines

    def test_steps_write_a_few_whole_lines(self):
        lines = self.run_progress("steps")
        counters = [line for line in lines if "bytes (" in line]
        self.assertEqual(len(counters), 10, lines)                  # 10 %, 20 %, ... 100 %
        self.assertTrue(all("\r" not in line and line.endswith("\n") for line in lines), lines)
        self.assertIn("(100 %)", counters[-1])
        self.assertIn(f"{self.SIZE}/{self.SIZE}", counters[-1])
        self.assertEqual([int(line.split("(")[1].split("%")[0]) // 10 for line in counters], list(range(1, 11)))
        self.assertTrue(lines[-1].strip().endswith("KB/s"))

    def test_a_single_chunk_still_reaches_100_percent_once(self):
        lines, log = collect()
        progress = canflash.Progress(500, log, "steps")
        progress.update(500)
        progress.finish()
        self.assertEqual(sum("bytes (" in line for line in lines), 1)

    def test_line_rewrites_one_line_on_a_terminal(self):
        lines = self.run_progress("line")
        counters = [line for line in lines if "bytes (" in line]
        self.assertEqual(len(counters), -(-self.SIZE // self.CHUNK))
        self.assertTrue(all(line.startswith("\r") and not line.endswith("\n") for line in counters))
        self.assertEqual(lines[-2], "\n")                        # the running line is closed before the rate
        self.assertTrue(lines[-1].strip().endswith("KB/s"))

    def test_off_writes_nothing(self):
        self.assertEqual(self.run_progress("off"), [])

    def test_auto_follows_the_stream(self):
        class Stream(io.StringIO):
            def __init__(self, tty):
                super().__init__()
                self.tty = tty

            def isatty(self):
                return self.tty

        self.assertEqual(canflash.progress_mode("auto", Stream(True)), "line")
        self.assertEqual(canflash.progress_mode("auto", Stream(False)), "steps")
        self.assertEqual(canflash.progress_mode("off", Stream(True)), "off")
        self.assertEqual(canflash.progress_mode("auto", object()), "steps")     # no isatty at all
        with self.assertRaises(ValueError):
            canflash.progress_mode("fancy")
        with self.assertRaises(ValueError):
            canflash.Progress(1, print, "auto")


class IdentificationFormTest(unittest.TestCase):
    """BL-22: DID 0xF180/0xF181 in both forms - bare text (before BL-22, every device in the field) and the
    Annex C.1 form with numberOfModules in front of it. The tester must read both and say which it got."""

    def test_the_form_with_number_of_modules(self):
        parsed = canflash.parse_identification(b"\x01nextbsw-bl 0.2.0")
        self.assertEqual(str(parsed), "nextbsw-bl 0.2.0")
        self.assertEqual(parsed.modules, 1)
        self.assertTrue(parsed.annex_c1)

    def test_the_form_before_bl_22(self):
        parsed = canflash.parse_identification(b"nextbsw-bl 0.2.0")
        self.assertEqual(str(parsed), "nextbsw-bl 0.2.0")
        self.assertIsNone(parsed.modules)
        self.assertFalse(parsed.annex_c1)

    def test_both_forms_are_the_same_identification(self):
        """Everything the tester decides from it follows the text, so the two forms must not differ."""
        for prefix in ("nextbsw-bl", canflash.BL_VERSION_PREFIX_FORMER, "S32K14x-APP"):
            with self.subTest(prefix):
                text = f"{prefix} 0.1.0"
                old = canflash.parse_identification(text.encode())
                new = canflash.parse_identification(b"\x01" + text.encode())
                self.assertEqual(old, new)
                self.assertEqual(canflash.split_boot_version(old), canflash.split_boot_version(new))
                self.assertEqual(canflash.boot_version_warnings(old, "nextbsw-bl", "0.1.0"),
                                 canflash.boot_version_warnings(new, "nextbsw-bl", "0.1.0"))
                self.assertEqual(canflash.security_method_of(old), canflash.security_method_of(new))

    def test_a_trailing_nul_is_dropped_in_both_forms(self):
        """A bootloader that sends sizeof() instead of strlen() (mutation of the host tests)."""
        self.assertEqual(canflash.parse_identification(b"nextbsw-bl 0.2.0\x00"), "nextbsw-bl 0.2.0")
        self.assertEqual(canflash.parse_identification(b"\x01nextbsw-bl 0.2.0\x00"), "nextbsw-bl 0.2.0")

    def test_where_the_two_forms_are_told_apart(self):
        """The first byte decides: an identification text starts with a printable character, a module count
        is small. The boundary is MAX_NUMBER_OF_MODULES and is a deliberate one, not an accident."""
        self.assertEqual(canflash.MAX_NUMBER_OF_MODULES, 0x1F)
        self.assertEqual(canflash.parse_identification(bytes([0x1F]) + b"x").modules, 0x1F)
        # 0x20 is the space character: from here on a text is possible, so it counts as the old form
        self.assertIsNone(canflash.parse_identification(bytes([0x20]) + b"x").modules)
        # numberOfModules = 0 would describe nothing; NUL is not a count either
        self.assertIsNone(canflash.parse_identification(b"\x00").modules)
        self.assertEqual(canflash.parse_identification(b""), "")
        self.assertIsNone(canflash.parse_identification(b"").modules)

    def test_the_note_names_the_old_form_only(self):
        self.assertEqual(canflash.identification_note(canflash.parse_identification(b"\x01nextbsw-bl 0.2.0")), "")
        self.assertIn("before BL-22",
                      canflash.identification_note(canflash.parse_identification(b"nextbsw-bl 0.2.0")))
        # a plain str (no answer, or a value from somewhere else) gets no note
        self.assertEqual(canflash.identification_note("nextbsw-bl 0.2.0"), "")
        self.assertEqual(canflash.identification_note(None), "")


class VersionWarningTest(unittest.TestCase):
    def test_matching_identification_is_silent(self):
        self.assertEqual(canflash.boot_version_warnings("nextbsw-bl 0.1.0", "nextbsw-bl", "0.1.0"), [])
        self.assertEqual(canflash.boot_version_warnings("nextbsw-bl 0.4.2", "nextbsw-bl", "0.1.0"), [])

    def test_former_identification_is_accepted(self):
        """PLAT-08 transition: the bootloader on the eval board still reports S32K14x-BL 0.1.0."""
        self.assertEqual(canflash.BL_VERSION_PREFIX_FORMER, "S32K14x-BL")      # read from bl_config.h
        self.assertEqual(canflash.accepted_prefixes("nextbsw-bl"), ["nextbsw-bl", "S32K14x-BL"])
        self.assertEqual(canflash.accepted_prefixes("S32K14x-BL"), ["S32K14x-BL"])
        self.assertEqual(canflash.boot_version_warnings("S32K14x-BL 0.1.0", "nextbsw-bl", "0.1.0"), [])
        self.assertEqual(canflash.boot_version_warnings("S32K14x-BL 0.1.0", "NX148-BL", "0.1.0"), [])
        # the former name does not bypass the major version check
        self.assertIn("major version", canflash.boot_version_warnings("S32K14x-BL 1.0.0", "nextbsw-bl", "0.1.0")[0])

    def test_other_prefix_warns(self):
        for foreign in ("ACME-BL 0.1.0", "nextbsw-b 0.1.0", "S32K14x-BLX 0.1.0"):
            with self.subTest(foreign):
                warnings = canflash.boot_version_warnings(foreign, "nextbsw-bl", "0.1.0")
                self.assertEqual(len(warnings), 1)
                self.assertIn(f"'{foreign.split(' ')[0]}'", warnings[0])
                self.assertIn("'nextbsw-bl'", warnings[0])

    def test_identification_is_compared_case_sensitively(self):
        """PLAT-51: the identifications are compared byte by byte. The mixed-case spelling of PLAT-08
        (never on a board) and case variants of the project's or the former prefix are other products."""
        for foreign in ("NeXtBSW-BL 0.1.0", "NEXTBSW-BL 0.1.0", "Nextbsw-bl 0.1.0", "s32k14x-bl 0.1.0",
                        "S32K14X-BL 0.1.0"):
            with self.subTest(foreign):
                self.assertEqual(len(canflash.boot_version_warnings(foreign, "nextbsw-bl", "0.1.0")), 1)
        self.assertNotIn("NeXtBSW-BL", canflash.accepted_prefixes("nextbsw-bl"))
        # a project may still choose an upper-case prefix; then exactly that spelling is accepted
        self.assertEqual(canflash.boot_version_warnings("ACME-BL 0.1.0", "ACME-BL", "0.1.0"), [])
        self.assertEqual(len(canflash.boot_version_warnings("acme-bl 0.1.0", "ACME-BL", "0.1.0")), 1)
        # the transition rule is unchanged: a bootloader flashed before PLAT-08 passes without a warning
        self.assertEqual(canflash.boot_version_warnings("S32K14x-BL 0.1.0", "nextbsw-bl", "0.1.0"), [])

    def test_other_major_version_warns(self):
        warnings = canflash.boot_version_warnings("nextbsw-bl 1.0.0", "nextbsw-bl", "0.1.0")
        self.assertEqual(len(warnings), 1)
        self.assertIn("major version", warnings[0])

    def test_prefix_with_spaces_and_unreadable_identification(self):
        self.assertEqual(canflash.split_boot_version("My ECU BL 2.10.3"), ("My ECU BL", "2.10.3"))
        self.assertIsNone(canflash.split_boot_version("nextbsw-bl"))
        self.assertIn("not '<prefix>", canflash.boot_version_warnings("nextbsw-bl x", "nextbsw-bl")[0])

    def test_prefix_of_the_selected_project_by_default(self):
        with mock.patch.dict(canflash.TEXT, {canflash.PREFIX_KEY: "Other-BL"}):
            self.assertEqual(len(canflash.boot_version_warnings(f"nextbsw-bl {canflash.BL_VERSION_NUMBER}")), 1)
            self.assertEqual(canflash.boot_version_warnings(f"Other-BL {canflash.BL_VERSION_NUMBER}"), [])


class FakeUdsServer:
    """Answers the requests of a udsoncan client over a QueueConnection: a bootloader from before BL-02."""

    def __init__(self, answers: dict[bytes, bytes]):
        self.conn = QueueConnection("fake")
        self.answers = answers
        self.requests: list[bytes] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                request = self.conn.touserqueue.get(timeout=0.01)
            except queue.Empty:
                continue
            self.requests.append(bytes(request))
            self.conn.fromuserqueue.put(self.answers.get(bytes(request), bytes([0x7F, request[0], 0x11])))

    def __enter__(self):
        self.thread.start()
        self.client = Client(self.conn, config={"data_identifiers": canflash.did_codecs(), "request_timeout": 2})
        self.client.open()
        return self.client

    def __exit__(self, *exc):
        self.client.close()
        self.stop.set()
        self.thread.join()


OLD_BOOTLOADER = {
    bytes([0x22, 0xF1, 0xA0]): bytes([0x7F, 0x22, 0x31]),                          # requestOutOfRange
    bytes([0x22, 0xF1, 0x80]): b"\x62\xF1\x80S32K14x-BL 0.1.0",
}


class QuietProbeTest(unittest.TestCase):
    """Board finding: a bootloader from before BL-02 made udsoncan print '[NegativeResponseException] ...
    RequestOutOfRange (0x31)' twice on every flash, before the one useful note."""

    def test_unknown_did_is_answered_without_log_output(self):
        with FakeUdsServer(OLD_BOOTLOADER) as client, self.assertNoLogs("UdsClient", "WARNING"):
            self.assertIsNone(canflash.read_target_compat(client))
        self.assertFalse(client.logger.disabled, "the client's log must be switched on again")

    def test_without_quiet_udsoncan_logs_the_answer_twice(self):
        """Evidence that the test above looks at the right output: without quiet() the noise is back."""
        with FakeUdsServer(OLD_BOOTLOADER) as client, \
                mock.patch.object(canflash, "quiet", contextlib.nullcontext), \
                self.assertLogs("UdsClient", "WARNING") as logs:
            self.assertIsNone(canflash.read_target_compat(client))
        self.assertEqual(len(logs.records), 2)
        self.assertTrue(all("RequestOutOfRange" in r.getMessage() for r in logs.records))

    def test_other_negative_answers_still_raise(self):
        answers = {bytes([0x22, 0xF1, 0xA0]): bytes([0x7F, 0x22, 0x22])}          # conditionsNotCorrect
        with FakeUdsServer(answers) as client:
            with self.assertRaises(canflash.NegativeResponseException):
                canflash.read_target_compat(client)
            self.assertFalse(client.logger.disabled)

    def test_old_bootloader_leaves_exactly_one_note(self):
        lines, log = collect()
        # a memory layout of some build: the note names the image's identity
        layout = {key: 0x1000 * (index + 1) for index, key in enumerate(canflash.LAYOUT_KEYS)}
        # project with the new identification, bootloader flashed before PLAT-08: no warning either
        with mock.patch.dict(canflash.TEXT, {canflash.PREFIX_KEY: "nextbsw-bl"}), \
                mock.patch.dict(canflash.CFG, layout), \
                FakeUdsServer(OLD_BOOTLOADER) as client, self.assertNoLogs("UdsClient", "WARNING"):
            self.assertEqual(canflash.check_boot_software(client, log), "S32K14x-BL 0.1.0")
            self.assertIsNone(canflash.check_compatibility(client, log))
        text = "".join(lines)
        self.assertEqual(text.count("older than BL-02"), 1, text)
        self.assertNotIn("Exception", text)
        self.assertNotIn("Warning", text)

    def test_missing_identification_warns(self):
        lines, log = collect()
        with FakeUdsServer({}) as client:
            self.assertIsNone(canflash.check_boot_software(client, log))
        self.assertIn("DID 0xF180", "".join(lines))


class CompatRoutineTest(unittest.TestCase):
    """BL-24: the compatibility check moved from 0xFF02 (reserved by ISO 14229-1 Annex F.1) to 0xF1A0 (system
    supplier range). The tester picks the identifier by the bootloader version in DID 0xF180."""

    RECORD = bytes(25)

    def test_identifiers(self):
        self.assertEqual((canflash.RID_CHECK_COMPAT, canflash.RID_CHECK_COMPAT_FORMER), (0xF1A0, 0xFF02))
        self.assertTrue(0xF000 <= canflash.RID_CHECK_COMPAT <= 0xFEFF, "system supplier range of Annex F.1")
        self.assertNotIn(canflash.RID_CHECK_COMPAT, (canflash.RID_ERASE_MEMORY, canflash.RID_CHECK_MEMORY))

    def test_routine_by_the_bootloader_version(self):
        new, former = canflash.RID_CHECK_COMPAT, canflash.RID_CHECK_COMPAT_FORMER
        cases = {
            "nextbsw-bl 0.3.4": new, "nextbsw-bl 0.3.10": new, "nextbsw-bl 0.4.0": new, "nextbsw-bl 1.0.0": new,
            "nextbsw-bl 0.3.3": former, "nextbsw-bl 0.3.1": former, "nextbsw-bl 0.2.9": former,
            # the number belongs to the software, not to the prefix of a project or the former name
            "NX148-BL 0.3.4": new, "NX148-BL 0.3.3": former, "S32K14x-BL 0.1.0": former,
            # nothing to decide by: try the new one, then the former (send_compat_routine)
            "nextbsw-bl": None, "garbage": None, "": None,
        }
        for text, rid in cases.items():
            with self.subTest(text):
                self.assertEqual(canflash.compat_routine_of(text), rid)
        self.assertIsNone(canflash.compat_routine_of(None))
        # both forms of the identification (BL-22) decide the same
        self.assertEqual(canflash.compat_routine_of(canflash.Identification("nextbsw-bl 0.3.1", None)), former)
        self.assertEqual(canflash.compat_routine_of(canflash.Identification("nextbsw-bl 0.3.4", 1)), new)

    def test_this_bootloader_gets_the_new_routine(self):
        """The version of bl_config.h is at least the first one with the new identifier."""
        self.assertEqual(canflash.compat_routine_of(f"nextbsw-bl {canflash.BL_VERSION_NUMBER}"),
                         canflash.RID_CHECK_COMPAT)

    @staticmethod
    def request(rid: int) -> bytes:
        return bytes([0x31, 0x01, rid >> 8, rid & 0xFF]) + CompatRoutineTest.RECORD

    @staticmethod
    def answer(rid: int, status: int = 0) -> bytes:
        return bytes([0x71, 0x01, rid >> 8, rid & 0xFF, status])

    def test_known_version_sends_one_request(self):
        for identification, rid in (("nextbsw-bl 0.3.1", 0xFF02), ("nextbsw-bl 0.3.4", 0xF1A0)):
            with self.subTest(identification):
                server = FakeUdsServer({self.request(rid): self.answer(rid)})
                with server as client:
                    sent, response = canflash.send_compat_routine(client, self.RECORD, identification)
                self.assertEqual(sent, rid)
                self.assertEqual(response.service_data.routine_status_record, b"\x00")
                self.assertEqual(server.requests, [self.request(rid)])

    def test_unknown_version_falls_back_after_request_out_of_range(self):
        server = FakeUdsServer({self.request(0xF1A0): bytes([0x7F, 0x31, 0x31]),
                                self.request(0xFF02): self.answer(0xFF02, 0x02)})
        with server as client, self.assertNoLogs("UdsClient", "WARNING"):
            sent, response = canflash.send_compat_routine(client, self.RECORD, None)
        self.assertEqual(sent, 0xFF02)
        self.assertEqual(response.service_data.routine_status_record, b"\x02")
        self.assertEqual(server.requests, [self.request(0xF1A0), self.request(0xFF02)])
        self.assertFalse(client.logger.disabled)

    def test_unknown_version_and_a_new_bootloader_sends_one_request(self):
        server = FakeUdsServer({self.request(0xF1A0): self.answer(0xF1A0)})
        with server as client:
            self.assertEqual(canflash.send_compat_routine(client, self.RECORD)[0], 0xF1A0)
        self.assertEqual(server.requests, [self.request(0xF1A0)])

    def test_other_refusals_are_not_a_reason_to_fall_back(self):
        """Only requestOutOfRange says "unknown routine"; conditionsNotCorrect or a missing security access are
        the bootloader's answer to the check itself and must reach the user."""
        for nrc in (0x22, 0x33, 0x13):
            with self.subTest(nrc=hex(nrc)):
                server = FakeUdsServer({self.request(0xF1A0): bytes([0x7F, 0x31, nrc])})
                with server as client, self.assertRaises(canflash.NegativeResponseException):
                    canflash.send_compat_routine(client, self.RECORD, None)
                self.assertEqual(server.requests, [self.request(0xF1A0)])

    def test_a_known_old_version_does_not_fall_back(self):
        """With a version the choice is final: a refusal of 0xFF02 by a bootloader that claims 0.3.1 is an error."""
        server = FakeUdsServer({self.request(0xFF02): bytes([0x7F, 0x31, 0x31])})
        with server as client, self.assertRaises(canflash.NegativeResponseException):
            canflash.send_compat_routine(client, self.RECORD, "nextbsw-bl 0.3.1")
        self.assertEqual(server.requests, [self.request(0xFF02)])


class FingerprintTest(unittest.TestCase):
    """BL-04: layout of DID 0xF184 and the transition to a bootloader that does not know it."""

    def test_date_is_bcd_and_the_tester_is_padded(self):
        data = canflash.fingerprint_bytes("tester-7", datetime.date(2026, 9, 17))
        self.assertEqual(len(data), canflash.FINGERPRINT_LEN)
        self.assertEqual(data[:canflash.FINGERPRINT_DATE_LEN], bytes([0x20, 0x26, 0x09, 0x17]))
        self.assertEqual(data[canflash.FINGERPRINT_DATE_LEN:], b"tester-7".ljust(canflash.FINGERPRINT_TESTER_LEN, b"\0"))
        self.assertEqual(canflash.parse_fingerprint(data),
                         {"date": datetime.date(2026, 9, 17), "tester": "tester-7", "raw": data})

    def test_lengths_come_from_bl_config(self):
        """The tester does not carry its own numbers: they are the macros of bootloader/include/bl_config.h."""
        text = (ROOT / "bootloader" / "include" / "bl_config.h").read_text(encoding="utf-8")
        for name, value in (("BL_FINGERPRINT_DATE_LEN", canflash.FINGERPRINT_DATE_LEN),
                            ("BL_FINGERPRINT_TESTER_LEN", canflash.FINGERPRINT_TESTER_LEN),
                            ("BL_FINGERPRINT_LEN", canflash.FINGERPRINT_LEN)):
            self.assertIn(f"#define {name}", text)
            self.assertEqual(canflash.CFG[name], value)
        self.assertEqual(canflash.FINGERPRINT_DATE_LEN + canflash.FINGERPRINT_TESTER_LEN, canflash.FINGERPRINT_LEN)

    def test_a_too_long_tester_identification_is_an_error(self):
        with self.assertRaises(ValueError):
            canflash.fingerprint_bytes("x" * (canflash.FINGERPRINT_TESTER_LEN + 1))

    def test_identification_of_the_workplace(self):
        """Who flashes is a value of the workplace, not of the project (architecture section 4)."""
        with mock.patch.dict("os.environ", {canflash.TESTER_ID_ENV: "bench-1"}):
            self.assertEqual(canflash.tester_identification().rstrip(b"\0"), b"bench-1")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(canflash.tester_identification().rstrip(b"\0"),
                             canflash.TESTER_ID_DEFAULT.encode())
        with mock.patch.dict("os.environ", {canflash.TESTER_ID_ENV: "bench-1"}):
            self.assertEqual(canflash.tester_identification("given").rstrip(b"\0"), b"given")

    def test_a_fingerprint_without_a_date_is_readable(self):
        value = canflash.parse_fingerprint(bytes([0xFF, 0xFF, 0xFF, 0xFF]) + b"hand".ljust(12, b"\0"))
        self.assertIsNone(value["date"])
        self.assertEqual(value["tester"], "hand")
        self.assertIn("no valid date", canflash.format_fingerprint(value))

    def test_a_bootloader_before_bl_04_only_leaves_a_note(self):
        """The old bootloader on the board answers 0x11 (service unknown): flashing must go on (PLAT-08)."""
        lines, log = collect()
        with FakeUdsServer(OLD_BOOTLOADER) as client, self.assertNoLogs("UdsClient", "WARNING"):
            self.assertIsNone(canflash.write_fingerprint(client, "tester-7", log=log))
            self.assertIsNone(canflash.read_fingerprint(client))
            self.assertIsNone(canflash.read_software_version(client))
            self.assertIsNone(canflash.read_hardware_number(client))
        text = "".join(lines)
        self.assertEqual(text.count("older than BL-04"), 1, text)
        self.assertNotIn("Exception", text)

    def test_hardware_number_in_plain_text(self):
        """BL-10: DID 0xF191 is "<board>/<derivative>"; canflash info compares its CRC-32 with the project's
        BL_TARGET_ID and names both sides. A build between BL-04 and BL-10 sent the four identity bytes."""
        own = "evalboard_s32k144/S32K144"
        saved_cfg, saved_text = dict(canflash.CFG), dict(canflash.TEXT)

        def restore():
            for current, old in ((canflash.CFG, saved_cfg), (canflash.TEXT, saved_text)):
                current.clear()
                current.update(old)

        self.addCleanup(restore)
        canflash.CFG[canflash.TARGET_KEY] = zlib.crc32(own.encode())
        canflash.TEXT[canflash.HARDWARE_KEY] = own
        request = bytes([0x22, 0xF1, 0x91])
        for payload, expected in ((own.encode(), own), (struct.pack(">I", zlib.crc32(own.encode())),
                                                        f"0x{zlib.crc32(own.encode()):08X}")):
            with self.subTest(expected), FakeUdsServer({request: bytes([0x62, 0xF1, 0x91]) + payload}) as client:
                self.assertEqual(canflash.read_hardware_number(client), expected)
                lines: list[str] = []
                canflash.print_identification("0.2.0", expected, out=lines.append)
                self.assertEqual(len(lines), 1, lines)
                self.assertIn(f"hardware number {expected} (DID 0xF191)", lines[0])
        lines = []
        canflash.print_identification("0.2.0", "testboard_s32k142_bl/S32K142", out=lines.append)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn(f"belongs to hardware {own}, the target reports testboard_s32k142_bl/S32K142", lines[1])

    def test_another_negative_answer_still_raises(self):
        answers = {bytes([0x2E, 0xF1, 0x84]) + canflash.fingerprint_bytes("t", datetime.date(2026, 9, 17)):
                   bytes([0x7F, 0x2E, 0x33])}                                  # securityAccessDenied
        with FakeUdsServer(answers) as client:
            with self.assertRaises(canflash.NegativeResponseException):
                canflash.write_fingerprint(client, "t", datetime.date(2026, 9, 17), log=lambda *a, **k: None)
            self.assertFalse(client.logger.disabled)


def did_request(did: int) -> bytes:
    return bytes([0x22, did >> 8, did & 0xFF])


def did_answer(did: int, payload: bytes) -> bytes:
    return bytes([0x62, did >> 8, did & 0xFF]) + payload


def out_of_range(*dids: int) -> dict[bytes, bytes]:
    return {did_request(did): bytes([0x7F, 0x22, 0x31]) for did in dids}


BOOTLOADER_DIDS = (canflash.DID_ECU_SW_VERSION, canflash.DID_ECU_HW_NUMBER, canflash.DID_ECU_SERIAL,
                   canflash.DID_SECURITY_INFO, canflash.DID_TARGET_COMPAT)
BL_IDENTIFICATION = f"nextbsw-bl {canflash.BL_VERSION_NUMBER}"
HARD_RESET = bytes([0x11, 0x01])
# the running application (ecu/uds): F180 from the bootloader's flash, F181 its own; none of the bootloader's DIDs
RUNNING_APPLICATION = {
    did_request(canflash.DID_BOOT_SOFTWARE_ID): did_answer(canflash.DID_BOOT_SOFTWARE_ID,
                                                           b"\x01" + BL_IDENTIFICATION.encode()),
    did_request(canflash.DID_APPLICATION_SOFTWARE_ID): did_answer(canflash.DID_APPLICATION_SOFTWARE_ID,
                                                                  b"\x01nextbsw-app 1.4.0"),
    **out_of_range(*BOOTLOADER_DIDS),
    HARD_RESET: bytes([0x51, 0x01]),
}
# the bootloader of the same board (BL-03/BL-04 and later): F181 unknown, its own DIDs answered in every session
BOOTLOADER_AFTER_BL_04 = {
    did_request(canflash.DID_BOOT_SOFTWARE_ID): did_answer(canflash.DID_BOOT_SOFTWARE_ID,
                                                           b"\x01" + BL_IDENTIFICATION.encode()),
    **out_of_range(canflash.DID_APPLICATION_SOFTWARE_ID),
    did_request(canflash.DID_ECU_SW_VERSION): did_answer(canflash.DID_ECU_SW_VERSION,
                                                         canflash.BL_VERSION_NUMBER.encode()),
    did_request(canflash.DID_ECU_HW_NUMBER): did_answer(canflash.DID_ECU_HW_NUMBER, b"evalboard_s32k144/S32K144"),
    did_request(canflash.DID_SECURITY_INFO): did_answer(
        canflash.DID_SECURITY_INFO, bytes([1, canflash.blkeys.METHOD_HMAC_SHA256, 1, 16, 32])),
    did_request(canflash.DID_ECU_SERIAL): did_answer(canflash.DID_ECU_SERIAL, bytes(range(16))),
    did_request(canflash.DID_TARGET_COMPAT): did_answer(
        canflash.DID_TARGET_COMPAT,
        bytes([1]) + struct.pack(">IIIII", 0x11223344, 0x55667788, 0x8000, 0x80000, 0x7FFE0) + bytes([0])),
    HARD_RESET: bytes([0x51, 0x01]),
}


class InfoWhoAnswersTest(unittest.TestCase):
    """TOOL-45, board finding 2026-09-21: `canflash info` against a board with the application running showed
    nextbsw-bl 0.2.0 as "security access XOR/rotl13 (bootloader before BL-03)" and "no software version (older
    than BL-04)". info reads without a session change, so the application answered - it reports DID 0xF180 from
    the bootloader's flash, but knows none of the bootloader's DIDs; their absence was taken for an old bootloader."""

    AGE_NOTES = ("before BL-0", "older than BL-0", "XOR/rotl13")

    def report(self, answers) -> tuple[str | None, list[str]]:
        lines: list[str] = []
        with mock.patch.object(canflash, "master_key_status", return_value="master key 'test' from test"), \
                FakeUdsServer(answers) as client, self.assertNoLogs("UdsClient", "WARNING"):
            app = canflash.info_report(client, out=lines.append)
        return app, lines

    def test_running_application_gives_no_age_notes(self):
        app, lines = self.report(RUNNING_APPLICATION)
        text = "\n".join(lines)
        self.assertEqual(app, "nextbsw-app 1.4.0")
        self.assertTrue(lines[0].startswith("application active: nextbsw-app 1.4.0"), text)
        self.assertIn(f"bootloader in flash (reported by the application): {BL_IDENTIFICATION}", text)
        self.assertIn("readable only in the bootloader", text)
        self.assertIn("info --bootloader", text)
        for note in self.AGE_NOTES:
            self.assertNotIn(note, text)

    def test_the_old_reading_of_the_same_answers_was_the_board_finding(self):
        """Evidence that the fake application reproduces the finding: read like before TOOL-45 (bootloader DIDs
        without asking who answers), the same answers give exactly the two wrong age notes."""
        lines: list[str] = []
        with FakeUdsServer(RUNNING_APPLICATION) as client:
            canflash.print_identification(canflash.read_software_version(client),
                                          canflash.read_hardware_number(client), out=lines.append)
            lines.append(canflash.security_state_text(canflash.read_security_info(client)))
        text = "\n".join(lines)
        self.assertIn("older than BL-04", text)
        self.assertIn("XOR/rotl13 (bootloader before BL-03)", text)

    def test_bootloader_answers_with_its_identification(self):
        app, lines = self.report(BOOTLOADER_AFTER_BL_04)
        text = "\n".join(lines)
        self.assertIsNone(app)
        self.assertEqual(lines[0], f"bootloader active: {BL_IDENTIFICATION}")
        self.assertIn(f"software version {canflash.BL_VERSION_NUMBER} (DID 0xF189)", text)
        self.assertIn("hardware number evalboard_s32k144/S32K144 (DID 0xF191)", text)
        self.assertIn("security access hmac-sha256, device key provisioned", text)
        self.assertIn("record format version 1", text)
        for note in self.AGE_NOTES:
            self.assertNotIn(note, text)

    def test_a_bootloader_before_bl_03_keeps_its_age_notes(self):
        """What the age notes are for: the bootloader itself answers and does not know the DIDs."""
        old = {**out_of_range(canflash.DID_APPLICATION_SOFTWARE_ID, *BOOTLOADER_DIDS),
               did_request(canflash.DID_BOOT_SOFTWARE_ID): did_answer(canflash.DID_BOOT_SOFTWARE_ID,
                                                                      b"S32K14x-BL 0.1.0")}
        app, lines = self.report(old)
        text = "\n".join(lines)
        self.assertIsNone(app)
        self.assertTrue(lines[0].startswith("bootloader active: S32K14x-BL 0.1.0"), text)
        self.assertIn("older than BL-04", text)
        self.assertIn("XOR/rotl13 (bootloader before BL-03)", text)
        self.assertIn("older than BL-02", text)

    def run_info(self, argv, servers, reached=True):
        """cmd_info with the bus, the switch into the bootloader and the clients faked: one FakeUdsServer per
        make_client() in the order given."""
        args = canflash.build_parser().parse_args(["info", *argv])
        pending = list(servers)
        switches: list[float] = []
        out, err = io.StringIO(), io.StringIO()

        def reach(bus, wait, log=print):
            switches.append(wait)
            return reached

        with mock.patch.object(canflash, "open_bus", return_value=contextlib.nullcontext(object())), \
                mock.patch.object(canflash, "make_client", side_effect=lambda bus: pending.pop(0)), \
                mock.patch.object(canflash, "reach_bootloader", side_effect=reach), \
                mock.patch.object(canflash, "master_key_status", return_value="master key 'test' from test"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = canflash.cmd_info(args)
        self.assertEqual(pending, [], "every expected client was used")
        return code, out.getvalue(), err.getvalue(), switches

    def test_info_stays_read_only_by_default(self):
        app = FakeUdsServer(RUNNING_APPLICATION)
        code, out, _, switches = self.run_info([], [app])
        self.assertEqual(code, 0)
        self.assertEqual(switches, [], "no session change without --bootloader")
        self.assertIn("application active", out)
        self.assertTrue(all(r[0] == 0x22 for r in app.requests), app.requests)

    def test_bootloader_switch_reads_there_and_resets_back(self):
        app, bl = FakeUdsServer(RUNNING_APPLICATION), FakeUdsServer(BOOTLOADER_AFTER_BL_04)
        code, out, _, switches = self.run_info(["--bootloader"], [app, bl])
        self.assertEqual(code, 0, out)
        self.assertEqual(switches, [0.0])
        self.assertIn("application active", out)
        self.assertIn(f"bootloader active: {BL_IDENTIFICATION}", out)
        self.assertIn(f"software version {canflash.BL_VERSION_NUMBER} (DID 0xF189)", out)
        self.assertIn("ECU reset", out)
        self.assertEqual(bl.requests[-1], HARD_RESET, "hard reset back into the application at the end")
        self.assertTrue(all(r[0] == 0x22 for r in app.requests), app.requests)

    def test_bootloader_switch_is_not_needed_when_the_bootloader_answers(self):
        bl = FakeUdsServer(BOOTLOADER_AFTER_BL_04)
        code, _, _, switches = self.run_info(["--bootloader"], [bl])
        self.assertEqual(code, 0)
        self.assertEqual(switches, [])
        self.assertNotIn(HARD_RESET, bl.requests, "a bootloader that was active is left as it was")

    def test_bootloader_switch_without_answer_fails(self):
        code, _, err, _ = self.run_info(["--bootloader"], [FakeUdsServer(RUNNING_APPLICATION)], reached=False)
        self.assertEqual(code, 1)
        self.assertIn("No answer from the bootloader", err)


class BlupdateAppFailureTest(unittest.TestCase):
    """BL-25 (c): `blupdate --app` whose application flash fails after a successful bootloader update ended in a
    traceback (board 2026-09-21). It has to say that the bootloader is updated, the application is not, and how
    to go on - with an exit code of its own."""

    def update(self, app_error: Exception | None):
        lines, log = collect()
        version = "nextbsw-bl 0.3.0"
        flashed = []

        def flash_over_bus(bus, data, wait, log, progress):
            if app_error is not None:
                raise app_error
            flashed.append(data)

        client = mock.MagicMock()
        client.__enter__.return_value = client
        with mock.patch.object(canflash, "reach_bootloader", return_value=True), \
                mock.patch.object(canflash, "make_client", return_value=client), \
                mock.patch.object(canflash, "read_boot_version", side_effect=["nextbsw-bl 0.2.0", version]), \
                mock.patch.object(canflash, "flash_application"), \
                mock.patch.object(canflash, "watch_updater", return_value=(0x00, (7, 45, 7))), \
                mock.patch.object(canflash, "wait_for_bootloader", return_value=True), \
                mock.patch.object(canflash, "drain", return_value=0), \
                mock.patch.object(canflash, "read_security_info", return_value=None), \
                mock.patch.object(canflash, "flash_over_bus", side_effect=flash_over_bus):
            code = canflash.bootloader_update(object(), b"updater", version, app=b"app", log=log)
        return code, "".join(lines), flashed

    def test_application_timeout_is_reported_without_traceback(self):
        timeout = canflash.udsoncan.exceptions.TimeoutException("P2 timeout time has expired")
        code, text, flashed = self.update(timeout)
        self.assertEqual(code, canflash.APP_NOT_FLASHED)
        self.assertIn("Bootloader updated to nextbsw-bl 0.3.0, application NOT flashed: no response in time", text)
        self.assertIn("canflash flash", text)
        self.assertEqual(flashed, [])

    def test_negative_response_and_runtime_errors_are_reported_too(self):
        for error in (RuntimeError("CRC check failed on target (status 01)"), can.CanError("bus off")):
            with self.subTest(type(error).__name__):
                code, text, _ = self.update(error)
                self.assertEqual(code, canflash.APP_NOT_FLASHED)
                self.assertIn(str(error), text)

    def test_successful_application_flash(self):
        code, text, flashed = self.update(None)
        self.assertEqual(code, 0)
        self.assertEqual(flashed, [b"app"])
        self.assertIn("Application flashed", text)

    def test_timeout_of_any_command_ends_without_traceback(self):
        """main(): a target that stops answering (flash without blupdate) gives one line and exit code 1."""
        err = io.StringIO()
        timeout = canflash.udsoncan.exceptions.TimeoutException("P2 timeout time has expired (timeout=1.000 sec)")
        with mock.patch.object(canflash, "prepare"), mock.patch.object(canflash, "cmd_info", side_effect=timeout), \
                contextlib.redirect_stderr(err):
            code = canflash.main(["info"])
        self.assertEqual(code, 1)
        self.assertIn("canflash: no response in time (P2 timeout", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class BlupdateFollowBusTest(unittest.TestCase):
    """BL-39: an updater built for another CAN bus of the ECU (-DBOOTLOADER_CAN_BUS=CAN1) is flashed over the bus of
    the installed bootloader, but reports on its own bus, and the new bootloader answers there. On the flash bus alone
    blupdate said "No status from the updater" even when the update would have worked (board 2026-10-02, VER-04).
    --follow-channel watches the updater, waits for the new bootloader and flashes --app on the other channel."""

    def update(self, follow):
        lines, log = collect()
        version = "nextbsw-bl 0.3.8"
        seen = {}

        def record(name, result):
            def call(bus, *args, **kwargs):
                seen.setdefault(name, []).append(bus)
                return result
            return call

        client = mock.MagicMock()
        client.__enter__.return_value = client
        with mock.patch.object(canflash, "reach_bootloader", side_effect=record("reach", True)),                 mock.patch.object(canflash, "make_client", side_effect=record("client", client)),                 mock.patch.object(canflash, "read_boot_version", side_effect=["nextbsw-bl 0.3.7", version]),                 mock.patch.object(canflash, "flash_application"),                 mock.patch.object(canflash, "watch_updater", side_effect=record("watch", (0x00, (7, 45, 7)))),                 mock.patch.object(canflash, "wait_for_bootloader", side_effect=record("wait", True)),                 mock.patch.object(canflash, "drain", side_effect=record("drain", 0)),                 mock.patch.object(canflash, "read_security_info", return_value=None),                 mock.patch.object(canflash, "flash_over_bus", side_effect=record("app", None)):
            code = canflash.bootloader_update("CAN0", b"updater", version, app=b"app", log=log, follow=follow)
        return code, "".join(lines), seen

    def test_updater_and_new_bootloader_on_the_follow_bus(self):
        code, text, seen = self.update("CAN1")
        self.assertEqual(code, 0, text)
        self.assertEqual(seen["reach"], ["CAN0"])
        self.assertEqual(seen["client"], ["CAN0", "CAN1"], "updater flashed on CAN0, new bootloader read on CAN1")
        self.assertEqual(seen["watch"], ["CAN1"])
        self.assertEqual(seen["wait"], ["CAN1"])
        self.assertEqual(seen["app"], ["CAN1"])
        self.assertIn("on the follow bus", text)

    def test_without_follow_everything_stays_on_one_bus(self):
        code, text, seen = self.update(None)
        self.assertEqual(code, 0, text)
        for name in ("reach", "client", "watch", "wait", "app"):
            self.assertEqual(set(seen[name]), {"CAN0"}, name)

    def cmd(self, *argv):
        buses = []

        def bus_for(interface, channel, timing, app_name=None):
            bus = mock.MagicMock(name=f"bus{channel}")
            buses.append((channel, timing, bus))
            return bus

        main_bus = mock.MagicMock()
        main_bus.__enter__.return_value = "CAN0"
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(canflash, "prepare"),                 mock.patch.object(canflash, "load_app_image", return_value=b"updater"),                 mock.patch.object(canflash, "extract_bootloader_image", return_value=b"image"),                 mock.patch.object(canflash, "check_bootloader_image", return_value="nextbsw-bl 0.3.8"),                 mock.patch.object(canflash, "check_updater_stub_field"),                 mock.patch.object(canflash, "open_bus", return_value=main_bus),                 mock.patch.object(canflash, "bus_for", side_effect=bus_for),                 mock.patch.object(canflash, "bootloader_update", return_value=0) as update,                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = canflash.main(["blupdate", "blupdater.hex", "--yes", *argv])
        return code, update, buses

    def test_command_line_opens_the_follow_channel(self):
        code, update, buses = self.cmd("--channel", "0", "--follow-channel", "1", "--follow-bitrate", "250000")
        self.assertEqual(code, 0)
        self.assertEqual([(c, t.bitrate) for c, t, _ in buses], [(1, 250000)])
        self.assertIs(update.call_args.kwargs["follow"], buses[0][2])
        buses[0][2].shutdown.assert_called_once()

    def test_same_or_no_follow_channel_opens_nothing(self):
        for argv in ((), ("--channel", "1", "--follow-channel", "1")):
            with self.subTest(argv=argv):
                code, update, buses = self.cmd(*argv)
                self.assertEqual(code, 0)
                self.assertEqual(buses, [])
                self.assertIsNone(update.call_args.kwargs["follow"])


class BlupdateWithoutTerminalTest(unittest.TestCase):
    """TOOL-53 (1): `blupdate` without --yes from a pipe ended in an EOFError traceback (board 2026-09-22)."""

    class Stdin(io.StringIO):
        def __init__(self, tty: bool):
            super().__init__("")
            self.tty = tty

        def isatty(self):
            return self.tty

    def blupdate(self, *argv, tty: bool):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(canflash, "prepare"), \
                mock.patch.object(canflash, "load_app_image", return_value=b"updater"), \
                mock.patch.object(canflash, "extract_bootloader_image", return_value=b"image"), \
                mock.patch.object(canflash, "check_bootloader_image", return_value="nextbsw-bl 0.3.4"), \
                mock.patch.object(canflash, "check_updater_stub_field"), \
                mock.patch.object(canflash, "open_bus") as open_bus, \
                mock.patch.object(canflash, "bootloader_update", return_value=0) as update, \
                mock.patch.object(sys, "stdin", self.Stdin(tty)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = canflash.main(["blupdate", "blupdater.hex", *argv])
        return code, out.getvalue(), err.getvalue(), open_bus, update

    def test_without_terminal_and_without_yes_it_refuses_clearly(self):
        code, out, err, open_bus, update = self.blupdate(tty=False)
        self.assertEqual(code, 1)
        self.assertIn("needs --yes when not interactive", err)
        self.assertIn("nothing was written", err)
        self.assertNotIn("Traceback", out + err)
        self.assertNotIn("Type UPDATE", out)
        open_bus.assert_not_called()
        update.assert_not_called()

    def test_yes_works_without_terminal(self):
        code, _, err, open_bus, update = self.blupdate("--yes", tty=False)
        self.assertEqual(code, 0, err)
        open_bus.assert_called_once()
        update.assert_called_once()

    def test_terminal_still_asks(self):
        with mock.patch.object(canflash, "confirm_update", return_value=False) as ask:
            code, out, _, open_bus, _ = self.blupdate(tty=True)
        self.assertEqual(code, 1)
        self.assertIn("Aborted.", out)
        ask.assert_called_once()
        open_bus.assert_not_called()

    def test_input_closed_while_asking_is_no_confirmation(self):
        def closed(prompt):
            raise EOFError("EOF when reading a line")
        lines, log = collect()
        self.assertFalse(canflash.confirm_update("nextbsw-bl 0.3.4", ask=closed, out=log))

    def test_interactive(self):
        self.assertFalse(canflash.interactive(self.Stdin(False)))
        self.assertTrue(canflash.interactive(self.Stdin(True)))
        self.assertFalse(canflash.interactive(object()))          # no isatty()
        closed = io.StringIO()
        closed.close()
        self.assertFalse(canflash.interactive(closed))            # ValueError on a closed stream


class RejectedImageTest(unittest.TestCase):
    """TOOL-53 (2) and (3) without the bootloader DLL: reset after a refusal, and the test aid that hands a foreign
    image to the bootloader's own check. The end-to-end cases are in test_bootloader_host.py."""

    RESET = bytes([0x11, 0x01])
    TARGET = {"version": 1, "target": 0x11111111, "layout": 0x22222222, "app_start": 0x8000, "app_end": 0x7F000,
              "record_addr": 0x7F000}

    def compat_answer(self, target: int) -> bytes:
        t = self.TARGET
        return (bytes([0x62, 0xF1, 0xA0, t["version"]])
                + struct.pack(">IIIII", target, t["layout"], t["app_start"], t["app_end"], t["record_addr"])
                + b"\x00")

    def check(self, status: int, local: bool, image_target: int = 0x33333333):
        rid = canflash.RID_CHECK_COMPAT
        answers = {bytes([0x22, 0xF1, 0xA0]): self.compat_answer(0x11111111)}
        server = FakeUdsServer(answers)
        image = {**self.TARGET, "target": image_target}
        record = bytes([image["version"]]) + struct.pack(">IIIII", image["target"], image["layout"],
                                                         image["app_start"], image["app_end"], image["record_addr"])
        answers[bytes([0x31, 0x01, rid >> 8, rid & 0xFF]) + record] = bytes([0x71, 0x01, rid >> 8, rid & 0xFF,
                                                                             status])
        lines, log = collect()
        with mock.patch.object(canflash, "image_compat", return_value=image), server as client:
            try:
                result = canflash.check_compatibility(client, log, "nextbsw-bl 0.3.4", local=local)
            except canflash.ImageRejected as e:
                return str(e), "".join(lines), server.requests
        return result, "".join(lines), server.requests

    def test_local_check_refuses_without_asking_the_bootloader(self):
        message, _, requests = self.check(0x02, local=True)
        self.assertIn("do not match", message)
        self.assertFalse(any(r[:1] == b"\x31" for r in requests), requests)

    def test_without_local_check_the_bootloader_decides(self):
        message, text, requests = self.check(0x02, local=False)
        self.assertIn("Bootloader rejects the image", message)
        self.assertIn("routine 0xF1A0 status 0x02", message)
        self.assertIn("WARNING (--skip-local-compat-check)", text)
        self.assertIn("board/derivative 0x33333333 against 0x11111111", text)
        self.assertTrue(any(r[:4] == b"\x31\x01\xF1\xA0" for r in requests))

    def test_a_bootloader_that_accepts_a_foreign_image_still_stops_the_run(self):
        message, _, _ = self.check(0x00, local=False)
        self.assertIn("Bootloader ACCEPTED an image that does not match", message)
        self.assertIn("Nothing was erased", message)

    def test_a_matching_image_passes_either_way(self):
        for local in (True, False):
            with self.subTest(local=local):
                result, text, _ = self.check(0x00, local=local, image_target=0x11111111)
                self.assertEqual(result["target"], 0x11111111)
                self.assertNotIn("WARNING", text)

    def test_rejection_is_a_runtime_error(self):
        """Callers that catch RuntimeError (cmd_flash, blupdate --app, Studio) keep working."""
        self.assertTrue(issubclass(canflash.ImageRejected, RuntimeError))

    def test_leave_bootloader(self):
        server = FakeUdsServer({self.RESET: bytes([0x51, 0x01])})
        with server as client:
            self.assertIn("ECU reset (11 01)", canflash.leave_bootloader(client))
        self.assertEqual(server.requests, [self.RESET])

        server = FakeUdsServer({})                         # serviceNotSupported
        with server as client:
            text = canflash.leave_bootloader(client)
        self.assertIn("did not accept the reset", text)
        self.assertIn("stays in the bootloader", text)

        server = FakeUdsServer({self.RESET: bytes([0x51, 0x01])})
        with server as client:
            self.assertIn("--stay-in-bootloader", canflash.leave_bootloader(client, reset=False))
        self.assertEqual(server.requests, [])

    def flash_cmd(self, *argv):
        err = io.StringIO()
        with mock.patch.object(canflash, "prepare"), \
                mock.patch.object(canflash, "load_app_image", return_value=b"app"), \
                mock.patch.object(canflash, "open_bus", return_value=contextlib.nullcontext(object())), \
                mock.patch.object(canflash, "flash_over_bus") as flash, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = canflash.main(["flash", "app.hex", *argv])
        self.assertEqual(code, 0)
        return flash.call_args.kwargs, err.getvalue()

    def test_options_of_flash(self):
        kwargs, err = self.flash_cmd()
        self.assertEqual((kwargs["local_check"], kwargs["reset_on_reject"]), (True, True))
        self.assertNotIn("WARNING", err)
        kwargs, err = self.flash_cmd("--skip-local-compat-check")
        self.assertFalse(kwargs["local_check"])
        self.assertIn("WARNING: --skip-local-compat-check is a TEST AID", err)
        kwargs, _ = self.flash_cmd("--stay-in-bootloader")
        self.assertFalse(kwargs["reset_on_reject"])
        help_text = canflash.build_parser()._subparsers._group_actions[0].choices["flash"].format_help()
        self.assertIn("TEST AID", help_text)


class TimingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="canflash_timing_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def project(self, text: str) -> Path:
        path = self.tmp / "p" / "project.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_eval_board_project_gives_the_known_vector_segments(self):
        timing = canflash.timing_from_project(EVAL_PROJECT)
        self.assertEqual((timing.bitrate, timing.sample_point_percent, timing.sjw), (500000, 81.25, 2))
        self.assertEqual(timing.segments(), {"sjw_abr": 2, "tseg1_abr": 12, "tseg2_abr": 3})

    def test_other_bus_other_bitrate_and_model_defaults(self):
        path = self.project("bootloader: { can: CANB }\ncan:\n  CANA: { bitrate: 500000 }\n"
                            "  CANB: { bitrate: 250000 }\n")
        timing = canflash.timing_from_project(path)
        self.assertEqual((timing.bitrate, timing.sample_point_percent, timing.sjw), (250000, 87.5, None))
        self.assertEqual(canflash.timing_from_project(path, "CANA").bitrate, 500000)     # the application's bus
        with self.assertRaises(ValueError):
            canflash.timing_from_project(path, "CANC")
        self.assertEqual(timing.segments(), {"sjw_abr": 2, "tseg1_abr": 13, "tseg2_abr": 2})
        self.assertEqual(canflash.BusTiming(1000000, 75.0, 1, 20).segments(),
                         {"sjw_abr": 1, "tseg1_abr": 14, "tseg2_abr": 5})
        self.assertEqual(canflash.BusTiming(500000).segments(), {})

    def test_project_errors(self):
        for text, expected in (("can: {}\n", "bootloader bus"),
                               ("bootloader: { can: CAN0 }\ncan:\n  CAN0: { bitrate: -1 }\n", "can.CAN0")):
            with self.subTest(expected), self.assertRaises(ValueError) as ctx:
                canflash.timing_from_project(self.project(text))
            self.assertIn(expected, str(ctx.exception))
        with self.assertRaises(ValueError):
            canflash.BusTiming(500000, 99.0).segments()

    def test_command_line_before_project_before_default(self):
        args = canflash.build_parser().parse_args(["info", "--sjw", "1"])
        args.timing_project = str(EVAL_PROJECT)
        self.assertEqual(canflash.resolve_timing(args), canflash.BusTiming(500000, 81.25, 1, 16))
        args = canflash.build_parser().parse_args(["info", "--bitrate", "125000", "--sample-point", "75"])
        args.timing_project = None
        self.assertEqual(canflash.resolve_timing(args), canflash.BusTiming(125000, 75.0, None, 16))
        args = canflash.build_parser().parse_args(["info"])
        args.timing_project = None
        self.assertEqual(canflash.resolve_timing(args), canflash.BusTiming(500000))

    def test_project_of_a_build_from_the_cmake_cache(self):
        layout = self.tmp / "build" / canflash.LAYOUT_IN_BUILD
        layout.parent.mkdir(parents=True)
        layout.write_text("", encoding="utf-8")
        self.assertIsNone(canflash.project_of_build(layout))
        project = self.project("project: { name: p }\n")
        (self.tmp / "build" / "CMakeCache.txt").write_text(
            f"CMAKE_HOME_DIRECTORY:INTERNAL={self.tmp.as_posix()}\nNX_PROJECT:STRING=p\n", encoding="utf-8")
        self.assertEqual(canflash.project_of_build(layout), project)
        (self.tmp / "build" / "CMakeCache.txt").write_text("NX_PROJECT:STRING=missing/project.yaml\n",
                                                           encoding="utf-8")
        self.assertIsNone(canflash.project_of_build(layout))


def ecu(name, request, response, functional=0x7DF, padding=0xCC, prefix="nextbsw-bl"):
    values = {"BL_CAN_ID_REQUEST_PHYS": request, "BL_CAN_ID_RESPONSE": response, "BL_CAN_ID_REQUEST_FUNC": functional,
              "BL_ISOTP_PADDING": padding, "BL_ISOTP_BLOCK_SIZE": 0, "BL_ISOTP_STMIN_MS": 0, "BL_ISOTP_N_CR_MS": 1000,
              "BL_ISOTP_N_BS_MS": 1000}
    return canflash.Ecu(name, Path(f"projects/{name}/project.yaml"), prefix, tuple(sorted(values.items())))


class EcuSelectionTest(unittest.TestCase):
    def test_alternative_projects_of_one_ecu_form_one_group(self):
        ecus = [ecu("demo", 0x7E0, 0x7E8), ecu("charge", 0x7E0, 0x7E8), ecu("other", 0x6F0, 0x6F8, 0x6DF)]
        canflash.check_ecus(ecus)
        self.assertEqual([[e.name for e in g] for g in canflash.address_groups(ecus)], [["demo", "charge"], ["other"]])

    def test_conflicting_addresses_and_names(self):
        for pair, expected in (((ecu("a", 0x7E0, 0x7E8), ecu("b", 0x7E0, 0x7E9)), "same request id 0x7E0"),
                               ((ecu("a", 0x7E0, 0x7E8), ecu("b", 0x7E1, 0x7E8)), "same response id 0x7E8"),
                               ((ecu("a", 0x7E0, 0x7E8), ecu("a", 0x6E0, 0x6E8)), "name a")):
            with self.subTest(expected), self.assertRaises(ValueError) as ctx:
                canflash.check_ecus(list(pair))
            self.assertIn(expected, str(ctx.exception))

    def test_ecu_by_project_name(self):
        tmp = Path(tempfile.mkdtemp(prefix="canflash_ecu_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name in ("front", "rear"):
            (tmp / name).mkdir()
            (tmp / name / "project.yaml").write_text(f"project:\n  name: {name}_ecu\n", encoding="utf-8")
        (tmp / "broken").mkdir()
        (tmp / "broken" / "project.yaml").write_text("project: [\n", encoding="utf-8")
        self.assertEqual(canflash.project_by_name("rear_ecu", [tmp]), tmp / "rear" / "project.yaml")
        self.assertEqual(canflash.project_by_name("front_ecu", [tmp / "front" / "project.yaml"]),
                         tmp / "front" / "project.yaml")
        with self.assertRaises(ValueError) as ctx:
            canflash.project_by_name("middle", [tmp])
        self.assertIn("front_ecu, rear_ecu", str(ctx.exception))
        with self.assertRaises(ValueError):
            canflash.project_files([tmp / "nothing"])

    def test_ecu_excludes_layout_and_project(self):
        args = canflash.build_parser().parse_args(["info", "--ecu", "x", "--project", str(EVAL_PROJECT)])
        with self.assertRaises(ValueError) as ctx:
            canflash.prepare(args)
        self.assertIn("--ecu", str(ctx.exception))

    def test_ecus_of_the_repository_projects(self):
        """The real projects: demo and charge share the eval board's addresses, the S32K148 profile has its own.
        PLAT-62: the full test profile has the same addresses; the eval board projects only where they exist."""
        ecus = canflash.ecus_from_projects([EVAL_PROJECT.parent, *PRIVATE_PROJECTS,
                                            ROOT / "projects" / "buildtest_s32k148_min" / "project.yaml"])
        groups = {(g[0].request_id, g[0].response_id): sorted(e.name for e in g)
                  for g in canflash.address_groups(ecus)}
        self.assertEqual(groups[(0x7E0, 0x7E8)], sorted(["testproject_full", *(p.parent.name for p in PRIVATE_PROJECTS)]))
        self.assertEqual(groups[(0x6F0, 0x6F8)], ["buildtest_s32k148_min"])


class FakeEcu(threading.Thread):
    """An ECU on a python-can virtual bus: answers 3E 00 on its request and functional id, sends a cyclic
    message that must not count as an answer."""

    def __init__(self, channel, request, response, functional, cyclic_id=None):
        super().__init__(daemon=True)
        self.bus = can.Bus(interface="virtual", channel=channel)
        self.request, self.response, self.functional, self.cyclic_id = request, response, functional, cyclic_id
        self.stop = threading.Event()

    def run(self):
        next_cyclic = 0.0
        while not self.stop.is_set():
            if self.cyclic_id is not None and time.monotonic() >= next_cyclic:
                self.bus.send(can.Message(arbitration_id=self.cyclic_id, data=b"\x01\x02\x03\x04", is_extended_id=False))
                next_cyclic = time.monotonic() + 0.01
            msg = self.bus.recv(0.002)
            if msg and msg.arbitration_id in (self.request, self.functional) and bytes(msg.data[:3]) == b"\x02\x3E\x00":
                self.bus.send(can.Message(arbitration_id=self.response, data=b"\x02\x7E\x00\xAA\xAA\xAA\xAA\xAA",
                                          is_extended_id=False))

    def close(self):
        self.stop.set()
        self.join()
        self.bus.shutdown()


class ScanTest(unittest.TestCase):
    def test_scan_finds_configured_and_unknown_ecus(self):
        channel = f"scan{id(self)}"
        ecus_on_bus = [FakeEcu(channel, 0x7E0, 0x7E8, 0x7DF),
                       FakeEcu(channel, 0x6F0, 0x6F8, 0x6DF, cyclic_id=0x6F9),
                       FakeEcu(channel, 0x5F0, 0x5F8, 0x7DF)]            # not configured, same functional id
        for fake in ecus_on_bus:
            fake.start()
            self.addCleanup(fake.close)
        tester = can.Bus(interface="virtual", channel=channel)
        self.addCleanup(tester.shutdown)
        configured = [ecu("eval", 0x7E0, 0x7E8), ecu("k148", 0x6F0, 0x6F8, 0x6DF), ecu("absent", 0x4F0, 0x4F8)]
        found, unknown = canflash.scan_bus(tester, configured, seconds=0.3)
        self.assertEqual([e.name for e in found], ["eval", "k148"])
        self.assertEqual(unknown, [0x5F8])


# TOOL-48: diagnostic addresses and ISO-TP values of a tester that talks to IsoTpEcu (evalboard values)
SLOW_DIAG = {"BL_CAN_ID_REQUEST_PHYS": 0x7E0, "BL_CAN_ID_REQUEST_FUNC": 0x7DF, "BL_CAN_ID_RESPONSE": 0x7E8,
             "BL_ISOTP_PADDING": 0xCC, "BL_ISOTP_BLOCK_SIZE": 0, "BL_ISOTP_STMIN_MS": 0,
             "BL_ISOTP_N_CR_MS": 1000, "BL_ISOTP_N_BS_MS": 1000}


class IsoTpEcu:
    """An ECU on a python-can virtual bus with its own ISO-TP stack: announces `stmin_ms` in its flow control and
    answers 36 <n> with 76 <n> once the whole request is in, like the bootloader (which waited correctly on the
    board, 2026-09-21)."""

    def __init__(self, channel: str, stmin_ms: int):
        self.bus = can.Bus(interface="virtual", channel=channel, receive_own_messages=False)
        address = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E8, rxid=0x7E0)
        self.stack = isotp.CanStack(self.bus, address=address,
                                    params={"stmin": stmin_ms, "blocksize": 0, "tx_padding": 0xCC,
                                            "tx_data_min_length": 8})
        self.requests: list[bytes] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.stack.start()
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            request = self.stack.recv(block=True, timeout=0.05)
            if request is None:
                continue
            self.requests.append(bytes(request))
            if request[0] == 0x36:
                self.stack.send(bytes([0x76, request[1]]))
            elif request[0] == 0x3E:
                self.stack.send(bytes([0x7E, 0x00]))

    def close(self):
        self.stop.set()
        self.thread.join()
        self.stack.stop()
        self.bus.shutdown()


class GappyBus(VirtualBus):
    """The tester's bus with a pause before every consecutive frame, like the scheduling gaps of 1-80 ms Windows
    put between the frames on the board (2026-09-21, STmin 1 ms): delayed sending on the tester side."""

    def __init__(self, channel: str, gap_s: float):
        super().__init__(channel=channel, receive_own_messages=False)
        self.gap_s = gap_s
        self.frames = 0

    def send(self, msg, timeout=None):
        if msg.data and (msg.data[0] >> 4) == 2:
            time.sleep(self.gap_s)
            self.frames += 1
        super().send(msg, timeout)


class SlowSendTest(unittest.TestCase):
    """TOOL-48: the client's P2 starts only when the request is out completely - for every command, because every
    command gets its client from make_client()."""

    BLOCK = bytes(range(256)) * 2        # 36 01 + 512 bytes: 1 first frame and 73 consecutive frames

    def setUp(self):
        patcher = mock.patch.dict(canflash.CFG, SLOW_DIAG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.channel = f"tool48_{self._testMethodName}"

    def ecu(self, stmin_ms: int) -> IsoTpEcu:
        ecu = IsoTpEcu(self.channel, stmin_ms)
        ecu.start()
        self.addCleanup(ecu.close)
        return ecu

    def open_tester(self):
        bus = can.Bus(interface="virtual", channel=self.channel, receive_own_messages=False)
        self.addCleanup(bus.shutdown)
        return bus

    def test_block_slower_than_p2_arrives_and_is_answered(self):
        """STmin of the ECU: 73 consecutive frames at 20 ms take about 1.5 s, more than P2 = 1.0 s."""
        ecu = self.ecu(stmin_ms=20)
        with canflash.make_client(self.open_tester()) as client:
            started = time.monotonic()
            response = client.transfer_data(1, self.BLOCK)
            took = time.monotonic() - started
            self.assertGreater(took, client.config["p2_timeout"], "the request has to take longer than P2")
        self.assertEqual(response.service_data.sequence_number_echo, 1)
        self.assertEqual(ecu.requests, [bytes([0x36, 0x01]) + self.BLOCK])

    def test_tester_side_gaps_longer_than_p2_in_sum(self):
        """Gaps of the PC between the consecutive frames (STmin of the ECU 0) add up beyond P2."""
        ecu = self.ecu(stmin_ms=0)
        bus = GappyBus(self.channel, gap_s=0.02)
        self.addCleanup(bus.shutdown)
        with canflash.make_client(bus) as client:
            started = time.monotonic()
            client.transfer_data(1, self.BLOCK)
            self.assertGreater(time.monotonic() - started, client.config["p2_timeout"])
        self.assertEqual(bus.frames, 73)
        self.assertEqual(ecu.requests, [bytes([0x36, 0x01]) + self.BLOCK])

    def test_without_blocking_send_p2_expires_while_sending(self):
        """Counter-check: with the non-blocking send of before TOOL-48 the same case fails on P2 in the middle of
        the block - the board finding of 2026-09-21 (Mitschnitt 33/35)."""
        self.ecu(stmin_ms=20)
        params = canflash.isotp_params()
        params["blocking_send"] = False
        with mock.patch.object(canflash, "isotp_params", return_value=params), \
                canflash.make_client(self.open_tester()) as client:
            with self.assertRaises(udsoncan.exceptions.TimeoutException) as ctx:
                client.transfer_data(1, self.BLOCK)
            self.assertIn("P2 timeout", str(ctx.exception))
            client.conn.empty_txqueue()

    def test_missing_flow_control_is_a_timeout_of_the_request(self):
        """No ECU on the bus: the stack gives up after N_Bs, the tester reports it like a missing response
        (udsoncan TimeoutException, which main() and blupdate handle - no isotp exception) without waiting P2."""
        with mock.patch.dict(canflash.CFG, {"BL_ISOTP_N_BS_MS": 200}), \
                canflash.make_client(self.open_tester()) as client:
            started = time.monotonic()
            with self.assertRaises(udsoncan.exceptions.TimeoutException) as ctx:
                client.transfer_data(1, self.BLOCK)
            self.assertLess(time.monotonic() - started, client.config["p2_timeout"])
        self.assertIn("not sent completely", str(ctx.exception))

    def test_single_frame_request_unchanged(self):
        """A single frame (3E 00) goes the same way and is answered at once."""
        ecu = self.ecu(stmin_ms=20)
        with canflash.make_client(self.open_tester()) as client:
            client.tester_present()
        self.assertEqual(ecu.requests, [b"\x3E\x00"])

    def test_every_command_gets_its_client_from_make_client(self):
        """flash, info, fingerprint, compat, blupdate, unlock (COM-30), identification in scan and provision:
        one ISO-TP stack and one connection class, both in make_client() - nothing builds its own stack."""
        self.assertTrue(canflash.isotp_params()["blocking_send"])
        self.assertIsInstance(canflash.make_client(self.open_tester()).conn, canflash.NxIsoTpConnection)
        here = Path(canflash.__file__).parent
        source = (here / "canflash.py").read_text(encoding="utf-8")
        head, tail = source.split("\ndef make_client(", 1)
        outside = {"canflash.py": head + "\ndef " + tail.split("\ndef ", 1)[1],
                   "provision.py": (here / "provision.py").read_text(encoding="utf-8")}
        built = re.compile(r"\b(CanStack|TransportLayer\w*|\w*IsoTp\w*Connection|NxClient|Client)\(")
        for name, text in outside.items():
            lines = [line for line in text.splitlines()
                     if built.search(line) and not line.lstrip().startswith("class ")]
            self.assertEqual(lines, [], f"{name} builds a stack, connection or client outside make_client()")
        unlock = source.split("\ndef cmd_unlock(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("make_client(bus)", unlock)

    def test_send_timeout_grows_with_the_request(self):
        self.assertEqual(canflash.send_timeout(7), canflash.SEND_TIMEOUT_MIN_S)
        self.assertEqual(canflash.send_timeout(4095), 586 * canflash.SEND_TIMEOUT_PER_FRAME_S)
        self.assertGreater(canflash.send_timeout(4095), 585 * 0.127, "all frames at the longest STmin")


class NoAckBus(can.BusABC):
    """TOOL-51: the tester's adapter on a bus where nobody acknowledges, like the board of 2026-09-22.

    The ECU holds a safe state with its CAN off ("held") or has no power ("off"): frames stay in the adapter's
    transmit queue, and once it is full every send is refused with the Vector driver's XL_ERR_QUEUE_IS_FULL.
    flush_tx_buffer() empties the queue. After power-on the bootloader listens for WINDOW_S (its startup window,
    200 ms like BL_STARTUP_WINDOW_MS of the eval board), then the ECU is held again (the application finds the
    same fault and holds its safe state). The frames still queued are delivered at power-on, as the controller
    repeats them until someone acknowledges."""

    QUEUE = 4
    WINDOW_S = 0.2
    ANSWER = bytes([0x06, 0x50, 0x02, 0x00, 0x32, 0x01, 0xF4, 0xCC])

    def __init__(self):
        super().__init__(channel="noack")
        self.lock = threading.Lock()
        self.ecu = "held"
        self.on_at = 0.0
        self.queued: list[can.Message] = []
        self.answers: queue.Queue = queue.Queue()
        self.delivered: list[bytes] = []
        self.refused = self.flushes = self.sent = 0

    def _acknowledging(self) -> bool:
        if self.ecu == "window" and time.monotonic() > self.on_at + self.WINDOW_S:
            self.ecu = "held"
        return self.ecu == "window"

    def _deliver(self, msg: can.Message) -> None:
        data = bytes(msg.data)
        self.delivered.append(data)
        if msg.arbitration_id == canflash.CFG["BL_CAN_ID_REQUEST_PHYS"] and data[:3] == b"\x02\x10\x02":
            self.answers.put(can.Message(arbitration_id=canflash.CFG["BL_CAN_ID_RESPONSE"], data=self.ANSWER,
                                         is_extended_id=False))

    def power_off(self) -> None:
        with self.lock:
            self.ecu = "off"

    def power_on(self) -> None:
        with self.lock:
            self.ecu, self.on_at = "window", time.monotonic()
            for msg in self.queued:
                self._deliver(msg)
            self.queued.clear()

    def send(self, msg, timeout=None):
        with self.lock:
            self.sent += 1
            if self._acknowledging():
                self._deliver(msg)
                return
            if len(self.queued) >= self.QUEUE:
                self.refused += 1
                raise VectorOperationError(11, "XL_ERR_QUEUE_IS_FULL", "xlCanTransmit")
            self.queued.append(msg)

    def flush_tx_buffer(self):
        with self.lock:
            self.flushes += 1
            self.queued.clear()

    def _recv_internal(self, timeout):
        try:
            if timeout is not None and timeout <= 0:
                return self.answers.get_nowait(), False
            return self.answers.get(timeout=3600 if timeout is None else timeout), False
        except queue.Empty:
            return None, False


class BusSwitch(powercycle.PowerSwitch):
    """Power switch of the test: switches NoAckBus, remembers what it did, can refuse."""

    name = "test switch"

    def __init__(self, bus: NoAckBus, fail: str | None = None):
        self.bus, self.fail, self.calls = bus, fail, []

    def off(self):
        self.calls.append("off")
        if self.fail == "off":
            raise powercycle.PowerError("refused by the test")
        self.bus.power_off()

    def on(self):
        self.calls.append("on")
        if self.fail == "on":
            raise powercycle.PowerError("refused by the test")
        self.bus.power_on()


class NoAckWaitTest(unittest.TestCase):
    """TOOL-51, board 2026-09-22 (e2, captures 22-24): `canflash flash --wait` ended with VectorOperationError
    XL_ERR_QUEUE_IS_FULL as soon as nobody acknowledged, so the way back from ROM_CHECK_FAILURE (--wait and a
    power cycle) could not be used."""

    def setUp(self):
        patcher = mock.patch.dict(canflash.CFG, SLOW_DIAG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bus = NoAckBus()
        self.addCleanup(self.bus.shutdown)

    def test_the_finding_a_full_queue_raises(self):
        """Counter-check of the simulated bus: a plain send ends in the exception of the board log."""
        msg = can.Message(arbitration_id=0x7E0, data=b"\x02\x10\x02", is_extended_id=False)
        for _ in range(NoAckBus.QUEUE):
            self.bus.send(msg)
        with self.assertRaises(can.CanError) as ctx:
            self.bus.send(msg)
        self.assertIn("XL_ERR_QUEUE_IS_FULL", str(ctx.exception))

    def test_waiting_goes_on_without_acknowledgement(self):
        lines, log = collect()
        started = time.monotonic()
        self.assertFalse(canflash.wait_for_bootloader(self.bus, 0.4, log=log))
        self.assertGreaterEqual(time.monotonic() - started, 0.4, "the waiting must use its whole time")
        self.assertGreater(self.bus.refused, 0, "the queue must have been full")
        self.assertEqual(self.bus.flushes, self.bus.refused, "every refusal empties the queue")
        self.assertGreater(self.bus.sent, self.bus.refused, "after emptying the queue the requests go out again")
        self.assertIn("refused by the adapter", "".join(lines))

    def test_send_frame_reports_instead_of_raising(self):
        msg = can.Message(arbitration_id=0x7E0, data=b"\x02\x10\x02", is_extended_id=False)
        results = [canflash.send_frame(self.bus, msg) for _ in range(NoAckBus.QUEUE + 2)]
        self.assertEqual(results, [True] * NoAckBus.QUEUE + [False, True])

    def test_adapter_without_flush_keeps_waiting_too(self):
        """python-can's BusABC.flush_tx_buffer() raises NotImplementedError; the waiting goes on anyway."""
        with mock.patch.object(NoAckBus, "flush_tx_buffer", side_effect=NotImplementedError):
            self.assertFalse(canflash.wait_for_bootloader(self.bus, 0.2))

    def test_power_cycle_catches_the_bootloader_in_its_window(self):
        switch = BusSwitch(self.bus)
        lines, log = collect()
        started = time.monotonic()
        self.assertTrue(canflash.wait_for_bootloader(self.bus, 5.0, switch, off_s=0.3, log=log))
        self.assertGreaterEqual(time.monotonic() - started, 0.3, "not before the power came back")
        self.assertEqual(switch.calls, ["off", "on"])
        self.assertIn(b"\x02\x10\x02" + bytes([0xCC] * 5), self.bus.delivered)
        text = "".join(lines)
        self.assertIn("Power cycle over test switch", text)
        self.assertIn("Power on again.", text)

    def test_window_of_only_a_few_requests_is_enough(self):
        """10 ms between the requests: a startup window of 50 ms still gets several of them."""
        self.bus.WINDOW_S = 0.05
        self.assertTrue(canflash.wait_for_bootloader(self.bus, 3.0, BusSwitch(self.bus), off_s=0.1))

    def test_reach_bootloader_hands_over_an_empty_bus(self):
        """The requests queued while nobody acknowledged are delivered at power-on and answered each; none of
        these answers may reach the UDS client (drain())."""
        lines, log = collect()
        # PLAT-62: with the 4-frame queue a refused send empties it, so the number still queued at power-on depended
        # on the phase of the last flush (0..4, under load often 1). Here the queue holds every request of the
        # off time: all of them are delivered at power-on and answered - what this test is about.
        self.bus.QUEUE = 10_000
        self.assertTrue(canflash.reach_bootloader(self.bus, 5.0, log, BusSwitch(self.bus), off_s=0.2))
        self.assertGreater(sum(d[:3] == b"\x02\x10\x02" for d in self.bus.delivered), 1)
        self.assertIsNone(self.bus.recv(0.05))
        self.assertIn("power cycle over test switch", "".join(lines))

    def test_without_power_cycle_the_held_ecu_is_not_reached(self):
        self.assertFalse(canflash.reach_bootloader(self.bus, 0.3, log=lambda *a, **k: None))

    def test_failed_switch_keeps_waiting_and_says_so(self):
        for fail, state in (("off", "nothing was switched"), ("on", "may still be without power")):
            with self.subTest(fail=fail):
                bus = NoAckBus()
                self.addCleanup(bus.shutdown)
                lines, log = collect()
                started = time.monotonic()
                self.assertFalse(canflash.wait_for_bootloader(bus, 0.4, BusSwitch(bus, fail), off_s=0.05, log=log))
                self.assertGreaterEqual(time.monotonic() - started, 0.4)
                text = "".join(lines)
                self.assertIn("Power cycle failed (refused by the test)", text)
                self.assertIn(state, text)

    def test_flash_passes_the_switch_on(self):
        """cmd_flash -> flash_over_bus -> reach_bootloader: the switch of the command line arrives."""
        args = canflash.build_parser().parse_args(["flash", "a.hex", "--wait", "30", "--power-cycle", "psu:COM6"])
        args.power = powercycle.switch_from_args(args)
        with mock.patch.object(canflash, "load_app_image", return_value=b"\0" * 8), \
                mock.patch.object(canflash, "open_bus", return_value=contextlib.nullcontext(self.bus)), \
                mock.patch.object(canflash, "pre_programming_step", return_value=[]), \
                mock.patch.object(canflash, "wait_for_bootloader", return_value=False) as wait, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(canflash.cmd_flash(args), 1)
        self.assertIs(wait.call_args.args[2], args.power)
        self.assertEqual(wait.call_args.args[3], powercycle.DEFAULT_OFF_S)


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status, self.body = status, body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PowerSwitchTest(unittest.TestCase):
    """TOOL-51: the optional power switches - nothing real is switched here, LabControl and the shell are mocked."""

    def labcontrol(self, answers, **kwargs):
        requests = []

        def urlopen(request, timeout=None):
            requests.append(request)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return powercycle.LabControlSwitch("psu:COM6", url="http://lab:8420/", token="secret", urlopen=urlopen,
                                           **kwargs), requests

    def test_labcontrol_off_and_on(self):
        switch, requests = self.labcontrol([FakeResponse(200, b'{"ok": true}'), FakeResponse(202, b'{"status": "pending"}')])
        switch.off()
        switch.on()
        self.assertEqual([r.full_url for r in requests], ["http://lab:8420/api/v1/tiles/psu:COM6/actions"] * 2)
        self.assertEqual([r.get_method() for r in requests], ["POST", "POST"])
        self.assertEqual([json.loads(r.data) for r in requests], [{"action": "PSU_OUT_OFF"}, {"action": "PSU_OUT_ON"}])
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer secret")

    def test_labcontrol_on_with_a_current_limit(self):
        switch, requests = self.labcontrol([FakeResponse(200, b'{"ok": true}')], on_current=0.5)
        switch.on()
        self.assertEqual(json.loads(requests[0].data), {"action": "PSU_CURR", "value": 0.5})

    def test_labcontrol_refusal_and_unreachable(self):
        refusal = urllib.error.HTTPError("http://lab", 403, "Forbidden", {},
                                         io.BytesIO(b'{"ok": false, "error": "remote_control_inactive"}'))
        switch, _ = self.labcontrol([refusal, urllib.error.URLError("connection refused"),
                                     FakeResponse(200, b'{"ok": false, "error": "device_offline"}')])
        with self.assertRaisesRegex(powercycle.PowerError, "remote_control_inactive"):
            switch.off()
        with self.assertRaisesRegex(powercycle.PowerError, "not reachable"):
            switch.off()
        with self.assertRaisesRegex(powercycle.PowerError, "device_offline"):
            switch.on()

    def test_labcontrol_unauthorized_names_the_next_step(self):
        """TOOL-153: board 2026-09-28, `--power-cycle psu:COM6` was refused ("unauthorized") because no
        LABCONTROL_TOKEN was set. The message must name the next step (set LABCONTROL_TOKEN) and where the
        token comes from (LabControl), and must never contain the token value itself."""
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError("http://lab", 401, "Unauthorized", {},
                                         io.BytesIO(b'{"ok": false, "error": "unauthorized"}'))
        switch = powercycle.LabControlSwitch("psu:COM6", url="http://lab:8420/", token="secret", urlopen=urlopen)
        with self.assertRaises(powercycle.PowerError) as cm:
            switch.off()
        message = str(cm.exception)
        self.assertIn("unauthorized", message)
        self.assertIn(powercycle.LABCONTROL_TOKEN_ENV, message)
        self.assertIn("Settings -> Network", message)
        self.assertNotIn("secret", message)   # the token value is never part of the message

    def test_labcontrol_unauthorized_without_a_token_configured(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError("http://lab", 401, "Unauthorized", {},
                                         io.BytesIO(b'{"ok": false, "error": "unauthorized"}'))
        switch = powercycle.LabControlSwitch("psu:COM6", url="http://lab:8420/", token="", urlopen=urlopen)
        with self.assertRaisesRegex(powercycle.PowerError, "no LABCONTROL_TOKEN was set"):
            switch.off()

    def test_labcontrol_401_without_a_json_body_is_still_recognised(self):
        refusal = urllib.error.HTTPError("http://lab", 401, "Unauthorized", {}, io.BytesIO(b"Unauthorized"))
        switch, _ = self.labcontrol([refusal])
        with self.assertRaisesRegex(powercycle.PowerError, "LABCONTROL_TOKEN"):
            switch.off()

    def test_labcontrol_address_and_token_from_the_environment(self):
        with mock.patch.dict("os.environ", {"LABCONTROL_URL": "http://other:1/", "LABCONTROL_TOKEN": "t"}):
            switch = powercycle.LabControlSwitch("psu:COM7")
        self.assertEqual((switch.url, switch.token), ("http://other:1", "t"))
        with mock.patch.dict("os.environ", {}, clear=True):
            switch = powercycle.LabControlSwitch("psu:COM7")
        self.assertEqual((switch.url, switch.token), (powercycle.LABCONTROL_DEFAULT_URL, ""))

    def test_commands(self):
        calls = []

        def run(cmd, shell, timeout):
            calls.append(cmd)
            return mock.Mock(returncode=0 if cmd != "fails" else 3)
        powercycle.CommandSwitch("relay off", "relay on", run=run).off()
        powercycle.CommandSwitch("relay off", "relay on", run=run).on()
        self.assertEqual(calls, ["relay off", "relay on"])
        with self.assertRaisesRegex(powercycle.PowerError, "exit code 3"):
            powercycle.CommandSwitch("fails", "x", run=run).off()

    def args(self, *extra):
        return canflash.build_parser().parse_args(["flash", "a.hex", *extra])

    def test_nothing_is_switched_by_default(self):
        self.assertIsNone(powercycle.switch_from_args(self.args()))
        self.assertIsNone(powercycle.switch_from_args(self.args("--wait", "30")))

    def test_command_line(self):
        switch = powercycle.switch_from_args(self.args("--wait", "30", "--power-cycle", "psu:COM6",
                                                       "--power-on-current", "0.4"))
        self.assertIsInstance(switch, powercycle.LabControlSwitch)
        self.assertEqual((switch.device, switch.on_current), ("psu:COM6", 0.4))
        switch = powercycle.switch_from_args(self.args("--wait", "30", "--power-off-cmd", "a", "--power-on-cmd", "b"))
        self.assertIsInstance(switch, powercycle.CommandSwitch)
        for extra, message in (
                (("--power-cycle", "psu:COM6"), "needs --wait"),
                (("--wait", "30", "--power-cycle", "p", "--power-off-cmd", "a", "--power-on-cmd", "b"), "exclude"),
                (("--wait", "30", "--power-off-cmd", "a"), "only accepted together"),
                (("--wait", "30", "--power-on-current", "0.4"), "belongs to --power-cycle"),
                (("--wait", "30", "--power-cycle", "p", "--power-on-current", "0"), "greater than 0"),
                (("--wait", "30", "--power-cycle", "p", "--power-off-time", "-1"), "must not be negative")):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, message):
                powercycle.switch_from_args(self.args(*extra))

    def test_every_command_with_wait_takes_the_options(self):
        parser = canflash.build_parser()
        for command in (["flash", "a.hex"], ["info"], ["fingerprint"], ["compat"], ["blupdate", "u.hex"]):
            with self.subTest(command=command[0]):
                args = parser.parse_args([*command, "--wait", "5", "--power-cycle", "psu:COM6"])
                self.assertEqual(args.power_cycle, "psu:COM6")

    def test_prepare_sets_the_switch(self):
        with mock.patch.dict(canflash.CFG), mock.patch.dict(canflash.TEXT), mock.patch.dict(canflash.SOURCE):
            args = canflash.build_parser().parse_args(["--project", str(EVAL_PROJECT), "info", "--wait", "5",
                                                       "--power-off-cmd", "a", "--power-on-cmd", "b"])
            canflash.prepare(args)
        self.assertIsInstance(args.power, powercycle.CommandSwitch)

    def test_cycle_switches_on_again_after_the_pause(self):
        events = []

        class Recorder(powercycle.PowerSwitch):
            def off(self):
                events.append("off")

            def on(self):
                events.append("on")
        cycle = powercycle.PowerCycle(Recorder(), off_s=2.5, log=lambda *a, **k: None,
                                      sleep=lambda s: events.append(f"sleep {s}"))
        self.assertFalse(cycle.daemon, "Ctrl-C of the tester must still switch the ECU on again")
        cycle.start()
        cycle.join()
        self.assertEqual(events, ["off", "sleep 2.5", "on"])
        self.assertIsNone(cycle.error)


class LayoutSuffixTest(unittest.TestCase):
    """TOOL-54: a bl_memory.h passed with --layout is read with every C integer suffix, and a value that is
    defined but not readable is an error instead of 0.

    Board 2026-09-22 (capture 109_tool53b_lowercase_u_accepted.log): '#define BL_TARGET_ID 0x12345678u' did not
    match the old pattern (only U, L, UL), the identity counted as 0 - "Note: no board/derivative identity ..." -
    and the foreign image was flashed. Own test profile, not the eval board: flash at 0x00400000 with 8 KiB
    sectors, RAM at 0x20400000, CAN IDs 0x7A1/0x7FF/0x7A9."""

    TARGET = canflash.target_id("suffixboard", "S32K312")
    LAYOUT = {"BL_FLASH_START": 0x00400000, "BL_FLASH_SIZE": 0x00200000, "BL_SECTOR_SIZE": 0x2000,
              "BL_PHRASE_SIZE": 8, "BL_APP_START": 0x00420000, "BL_APP_END": 0x005FC000,
              "BL_APP_RECORD_ADDR": 0x005FE000, "BL_RAM_START": 0x20400000, "BL_RAM_END": 0x2041FFF0,
              "BL_BOOT_REQUEST_ADDR": 0x2041FFF0}
    DIAG = {"BL_CAN_ID_REQUEST_PHYS": 0x7A1, "BL_CAN_ID_REQUEST_FUNC": 0x7FF, "BL_CAN_ID_RESPONSE": 0x7A9,
            "BL_ISOTP_PADDING": 0xAA, "BL_ISOTP_BLOCK_SIZE": 0, "BL_ISOTP_STMIN_MS": 1, "BL_ISOTP_N_CR_MS": 150,
            "BL_ISOTP_N_BS_MS": 150}
    # every suffix C allows on an integer constant (C23 wb/uwb aside), in both cases where it has one
    SUFFIXES = ("", "u", "U", "l", "L", "ul", "uL", "Ul", "UL", "lu", "lU", "Lu", "LU", "ll", "LL", "ull", "uLL",
                "Ull", "ULL", "llu", "llU", "LLu", "LLU")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tool54_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for patched in (canflash.CFG, canflash.TEXT, canflash.SOURCE):
            patcher = mock.patch.dict(patched)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, target: str | None, suffix: str = "UL", diag_extra: str = "", layout_extra: str = "") -> Path:
        """bl_memory.h/bl_diag.h of the test profile in the form of the generated ones; BL_TARGET_ID as given
        (`target` is the literal text after the macro name, None leaves the macro out)."""
        layout = self.tmp / "bl_memory.h"
        lines = ["#pragma once", ""]
        lines += [f"#define {k:<22}{v:#010x}{suffix}" for k, v in self.LAYOUT.items()]
        if target is not None:
            lines.append(f"#define BL_TARGET_ID {target}  /* CRC-32 of suffixboard/S32K312 */")
        lines += ['#define BL_HARDWARE_ID "suffixboard/S32K312"', '#define BL_FAMILY "s32k3xx"', layout_extra, ""]
        layout.write_text("\n".join(lines), encoding="utf-8")
        diag = [f"#define {k} {v:#x}{suffix}" for k, v in self.DIAG.items()]
        diag += ['#define BL_VERSION_PREFIX "suffix-bl"', diag_extra, ""]
        (self.tmp / canflash.DIAG_HEADER_NAME).write_text("\n".join(diag), encoding="utf-8")
        return layout

    def test_every_suffix_is_read(self):
        for suffix in self.SUFFIXES:
            with self.subTest(suffix=suffix):
                values = canflash.load_build(self.write(f"{self.TARGET:#010x}{suffix}", suffix))
                self.assertEqual(values[canflash.TARGET_KEY], self.TARGET)
                self.assertEqual({k: values[k] for k in self.LAYOUT}, self.LAYOUT)
                self.assertEqual({k: values[k] for k in self.DIAG}, self.DIAG)
                canflash.use_layout(values)
                self.assertEqual(canflash.CFG[canflash.TARGET_KEY], self.TARGET)
                self.assertEqual(canflash.image_compat()["target"], self.TARGET)

    def test_decimal_and_upper_case_hex_prefix(self):
        for literal in (f"{self.TARGET}u", f"{self.TARGET}ULL", f"0X{self.TARGET:08X}lu"):
            with self.subTest(literal=literal):
                self.assertEqual(canflash.load_build(self.write(literal))[canflash.TARGET_KEY], self.TARGET)

    def test_board_finding_lower_case_u_is_rejected_by_the_target_check(self):
        """The finding itself: the foreign identity 0x12345678u now reaches the comparison and differs from
        the target's 0xEF272CA9, so canflash rejects before erasing instead of printing the note."""
        canflash.use_layout(canflash.load_build(self.write("0x12345678u")))
        self.assertEqual(canflash.CFG[canflash.TARGET_KEY], 0x12345678)
        image = canflash.image_compat()
        target = dict(image, target=0xEF272CA9)
        self.assertEqual(canflash.compat_differences(image, target),
                         ["board/derivative 0x12345678 against 0xEF272CA9 on the target"])

    def test_unreadable_identity_is_an_error_not_zero(self):
        for literal in ("0x12345678uu", "0x1234567G", "0x12345678UUL", "0x12345678lL", "0x12345678LUL",
                        "0x12345678.0", "12345678x", "0x", "BL_SOMETHING", "(0x12345678UL)", ""):
            with self.subTest(literal=literal):
                with self.assertRaises(ValueError) as caught:
                    canflash.load_build(self.write(literal))
                message = str(caught.exception)
                self.assertIn("BL_TARGET_ID", message)
                self.assertIn("bl_memory.h", message)
                self.assertIn("cannot read as an integer constant", message)

    def test_rejected_through_select_layout_leaves_the_previous_layout(self):
        canflash.CFG[canflash.TARGET_KEY] = 0xCAFEF00D
        with self.assertRaisesRegex(ValueError, r"BL_TARGET_ID '0x12345678uu'"):
            canflash.select_layout(str(self.write("0x12345678uu")), None)
        self.assertEqual(canflash.CFG[canflash.TARGET_KEY], 0xCAFEF00D)

    def test_typo_in_other_values_is_named_too(self):
        cases = ((dict(layout_extra="#define BL_KEY_ADDR 0x005FDOOOUL"), "BL_KEY_ADDR '0x005FDOOOUL'"),
                 (dict(diag_extra="#define BL_ISOTP_STMIN_MS 1uu"), "BL_ISOTP_STMIN_MS"))
        for extra, name in cases:
            with self.subTest(name=name):
                target = f"{self.TARGET:#x}u"
                path = self.write(target, **extra)
                if "diag_extra" in extra:          # replace the readable definition by the broken one
                    diag = self.tmp / canflash.DIAG_HEADER_NAME
                    diag.write_text(diag.read_text().replace("#define BL_ISOTP_STMIN_MS 0x1UL\n", ""))
                with self.assertRaises(ValueError) as caught:
                    canflash.load_build(path)
                self.assertIn(name, str(caught.exception))

    def test_missing_identity_stays_zero(self):
        """Not defined at all (a build before BL-02) still means "identity unknown": only the layout is
        compared, as before."""
        canflash.use_layout(canflash.load_build(self.write(None)))
        self.assertEqual(canflash.CFG[canflash.TARGET_KEY], 0)

    def test_function_like_and_expression_macros_are_not_values(self):
        """bl_config.h holds macros whose value is an expression or a string; they are no finding, and the
        numbers read from bl_config.h/upd_config.h are the same as with the pattern before TOOL-54."""
        path = self.tmp / "other.h"
        path.write_text('#define BL_X(a) ((a) + 1U)\n#define BL_Y (BL_APP_START + 4U)\n#define BL_Z "1.0"\n'
                        "#define BL_TARGET_ID_BITS 32u\n")
        self.assertEqual(canflash.unreadable_numbers(path, {"BL_X"}), [])
        self.assertEqual(canflash.unreadable_numbers(path, {"BL_Y", "BL_Z"}),
                         ["BL_Y '(BL_APP_START'", "BL_Z '\"1.0\"'"])
        self.assertEqual(canflash.load_config(path), {"BL_TARGET_ID_BITS": 32})
        old = {}
        for header in (canflash.BL_CONFIG, canflash.UPD_CONFIG):
            for name, value in re.findall(r"#define\s+((?:BL|UPD|NX)_\w+)\s+(0x[0-9A-Fa-f]+|\d+)U?L?\b",
                                          header.read_text()):
                old[name] = int(value, 0)
        self.assertEqual(canflash.load_config(), old)


class LayoutWithoutSecurityTest(unittest.TestCase):
    """TOOL-204: --layout without bl_security.h next to bl_memory.h (name of the master key optional)."""


    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = (dict(canflash.CFG), dict(canflash.TEXT), dict(canflash.SOURCE))
        self.addCleanup(self.restore)
        folder = self.tmp / "layout"
        folder.mkdir()
        self.layout = folder / "bl_memory.h"
        self.layout.write_text(self.memory_header(), encoding="utf-8")
        (folder / canflash.DIAG_HEADER_NAME).write_text(self.diag_header(), encoding="utf-8")
        canflash.use_master_key(None)
        self.addCleanup(canflash.use_master_key, None)

    def restore(self):
        for target, old in zip((canflash.CFG, canflash.TEXT, canflash.SOURCE), self.saved):
            target.clear()
            target.update(old)

    @staticmethod
    def memory_header() -> str:
        return "\n".join(f"#define {key} {0x1000 * (i + 1):#x}" for i, key in enumerate(canflash.LAYOUT_KEYS)) + "\n"

    @staticmethod
    def diag_header() -> str:
        lines = [f"#define {k} 0x{0x600 + i:x}" for i, k in enumerate(canflash.DIAG_KEYS)]
        lines.append(f'#define {canflash.PREFIX_KEY} "nx-bl"')
        return "\n".join(lines) + "\n"

    def args(self, *extra):
        return canflash.build_parser().parse_args(["--layout", str(self.layout), *extra, "info"])

    def test_layout_loads_without_security_header(self):
        self.assertFalse((self.layout.parent / canflash.SECURITY_HEADER_NAME).exists())
        canflash.prepare(self.args())
        self.assertNotIn(canflash.KEY_NAME_KEY, canflash.TEXT)

    def test_message_names_file_and_source(self):
        canflash.prepare(self.args())
        with self.assertRaises(ValueError) as cm:
            canflash.master_key()
        text = str(cm.exception)
        self.assertIn(str(self.layout.parent / canflash.SECURITY_HEADER_NAME), text)
        self.assertIn("blgen.py", text)
        self.assertIn("--key-name", text)
        self.assertIn("--project", text)

    def test_key_name_option(self):
        canflash.prepare(self.args("--key-name", blkeys.TEST_KEY_NAME))
        self.assertEqual(canflash.master_key().name, blkeys.TEST_KEY_NAME)

    def test_key_name_environment(self):
        with mock.patch.dict(os.environ, {canflash.KEY_NAME_ENV: blkeys.TEST_KEY_NAME}):
            canflash.prepare(self.args())
        self.assertEqual(canflash.master_key().name, blkeys.TEST_KEY_NAME)

    def test_header_wins_over_environment_option_wins_over_header(self):
        (self.layout.parent / canflash.SECURITY_HEADER_NAME).write_text(
            '#define BL_SECURITY_METHOD 1U\n#define BL_SECURITY_KEY_NAME "from-header"\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {canflash.KEY_NAME_ENV: "from-env"}):
            canflash.prepare(self.args())
            self.assertEqual(canflash.TEXT[canflash.KEY_NAME_KEY], "from-header")
            canflash.prepare(self.args("--key-name", "from-option"))
            self.assertEqual(canflash.TEXT[canflash.KEY_NAME_KEY], "from-option")

    def test_name_of_previous_layout_is_forgotten(self):
        header = self.layout.parent / canflash.SECURITY_HEADER_NAME
        header.write_text('#define BL_SECURITY_KEY_NAME "first"\n', encoding="utf-8")
        canflash.use_layout(canflash.load_build(self.layout))
        header.unlink()
        canflash.use_layout(canflash.load_build(self.layout))
        self.assertNotIn(canflash.KEY_NAME_KEY, canflash.TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
