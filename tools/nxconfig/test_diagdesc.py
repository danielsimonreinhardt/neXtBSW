"""Host tests of the diagnostic description (ARCH-51): model.DiagDescription and diagdesc.py.

Own profile, not the eval board: the synthetic bootloader project on `testboard` (family testfam) gets a
generated `diag.yaml` and a generated `rte.yaml` in a temporary tree, so the checks are exercised with
identifiers, DTCs and component names of their own. Every check is run once with a deliberately built-in
fault, and the test asserts the message *and* its YAML path - a check that fires at the wrong key is as
useless as one that does not fire. The demo project is checked as it stands in the repository.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_diagdesc.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import bswdtc, diagdesc  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = HERE / "test/profiles/boards/testboard/board.yaml"
BL_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject_bootloader/project.yaml")
                            .read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"

# A description that passes every check; the tests change one thing at a time.
BASE = {
    "schema_version": 1,
    "sessions": [
        {"name": "Default", "id": 0x01},
        {"name": "Extended", "id": 0x03},
    ],
    "security": [
        {"name": "Unlock", "level": 0x01, "sessions": ["Extended"]},
    ],
    "operation_cycles": [
        {"name": "Power", "kind": "power"},
    ],
    "enable_conditions": [
        {"name": "Running", "bsw": "ecu.state"},
    ],
    "data_identifiers": [
        {"identifier": 0x4711, "name": "Speed", "read": True,
         "data": [{"name": "speed", "type": "uint16", "unit": "km_h", "source": {"port": "Swc.Out.Speed"}}]},
        {"identifier": 0xFD10, "name": "Tuning", "read": True, "write": True,
         "write_sessions": ["Extended"], "write_security": "Unlock",
         "data": [{"name": "gain", "type": "uint8", "source": {"bsw": "app.gain"}}]},
    ],
    "routines": [
        {"identifier": 0x0401, "name": "SelfTest", "sessions": ["Extended"],
         "start": {"call": {"runnable": "Swc.Run"}}},
    ],
    "fault_memory": {"max_entries": 8, "snapshot_records_max": 1, "extended_records_max": 1},
    "dtcs": [
        {"number": 0x123456, "name": "SpeedImplausible", "operation_cycle": "Power",
         "enable_conditions": ["Running"], "severity": "check_at_next_halt",
         "event": {"port": "Swc.Out.SpeedValid"},
         "debounce": {"counter": {"failed_threshold": 5, "passed_threshold": -5}},
         "aging": {"threshold_cycles": 40},
         "snapshots": [{"record_number": 0x01, "dids": ["Speed"]}],
         "extended_data": [{"record_number": 0x01,
                            "data": [{"name": "occ", "counter": "occurrence_counter"}]}]},
    ],
}


def described(**changes):
    """A copy of BASE with the top level keys of `changes` replaced."""
    out = copy.deepcopy(BASE)
    out.update(copy.deepcopy(changes))
    return out


class DiagProfile(unittest.TestCase):
    """Writes the bootloader profile with a diag.yaml (and an ECU description) into a temporary tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, description, components=("Swc",)):
        board_dir = self.tmp / "boards" / "testboard"
        project_dir = self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEST_BOARD, board_dir / "board.yaml")
        project = copy.deepcopy(BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        project["project"]["rte"] = "rte.yaml"
        if getattr(self, "project_change", None) is not None:
            self.project_change(project)            # COM-30: a project value the description depends on
        rte = {"schema_version": 2,          # RTE-22
               "components": [{"name": name, "path": f"swc/{name}"} for name in components]}
        (project_dir / "rte.yaml").write_text(yaml.safe_dump(rte, sort_keys=False), encoding="utf-8")
        if description is not None:
            project["project"]["diag"] = "diag.yaml"
            (project_dir / "diag.yaml").write_text(yaml.safe_dump(description, sort_keys=False),
                                                   encoding="utf-8")
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, TEST_MCUDB)

    def messages(self, description, severity="error", components=("Swc",)):
        _, messages = self.check(description, components)
        return [m for m in messages if m.severity == severity]

    def expect(self, description, fragment, path, severity="error", components=("Swc",)):
        """One finding with `fragment` in its text, at `path` of diag.yaml, with a line number."""
        found = self.messages(description, severity, components)
        match = [m for m in found if fragment in m.text]
        self.assertTrue(match, f"'{fragment}' not in {[str(m) for m in found]}")
        self.assertEqual(match[0].file.name, "diag.yaml", str(match[0]))
        self.assertEqual(match[0].yaml_path, path, str(match[0]))
        self.assertIsInstance(match[0].line, int, str(match[0]))
        return match[0]


class DescriptionLoads(DiagProfile):

    def test_a_clean_description_passes_and_is_loaded(self):
        config, messages = self.check(BASE)
        # the profile itself warns about its two buses (bootloader.can); the description adds nothing
        self.assertEqual([str(m) for m in messages if m.file.name == "diag.yaml"], [])
        self.assertEqual(len(config.diag.data_identifiers), 2)
        self.assertEqual(config.diag_file.name, "diag.yaml")

    def test_without_the_key_there_is_no_description(self):
        config, messages = self.check(None)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        self.assertIsNone(config.diag)
        self.assertIsNone(config.diag_description)

    def test_a_named_file_that_does_not_exist_is_reported_at_the_project(self):
        board_dir = self.tmp / "boards" / "testboard"
        project_dir = self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEST_BOARD, board_dir / "board.yaml")
        project = copy.deepcopy(BL_PROJECT)
        project["project"]["board"] = "../../boards/testboard"
        project["project"]["diag"] = "missing.yaml"
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        _, messages = nxconfig.check(path, TEST_MCUDB)
        errors = [m for m in messages if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["project.diag"])
        self.assertIn("diagnostic description", errors[0].text)


class DerivedValues(DiagProfile):
    """Nothing that follows from the description is configured a second time (architecture, question 6)."""

    def test_lengths_severity_and_masks_are_derived(self):
        config, _ = self.check(BASE)
        plan = config.diag_description
        self.assertEqual(plan.did_lengths, {"Speed": 2, "Tuning": 1})
        # Annex D.3: checkAtNextHalt is bit 6, and the unclassified class bit 0 is always set
        self.assertEqual([(d.name, hex(d.severity_byte)) for d in plan.dtcs],
                         [("SpeedImplausible", "0x41")])
        self.assertEqual(plan.dtc_format, diagdesc.DTC_FORMAT_ISO_14229_1)
        # no warning lamp (decision 2026-09-21), so bit 7 is missing from the availability mask
        self.assertEqual(plan.status_availability_mask, 0x7F)
        self.assertEqual(plan.sessions, {"Default": 1, "Extended": 3})
        self.assertEqual(plan.security_levels, {"Unlock": 1})

    def test_a_length_follows_from_the_elements(self):
        description = described(data_identifiers=[
            {"identifier": 0x4711, "name": "Mixed", "read": True, "data": [
                {"name": "count", "type": "uint8", "source": {"constant": 1}},
                {"name": "text", "type": "ascii", "length_bytes": 30, "source": {"bsw": "app.version"}},
                {"name": "value", "type": "int32", "source": {"bsw": "app.value"}},
            ]}], dtcs=[], fault_memory=None)
        config, messages = self.check(description)
        self.assertEqual([str(m) for m in messages if m.file.name == "diag.yaml"], [])
        self.assertEqual(config.diag_description.did_lengths, {"Mixed": 35})


class IdentifierRanges(DiagProfile):
    """ISO 14229-1 Annex C.1, F.1 and D.1: what a project may number and what it may not."""

    def did(self, identifier, **extra):
        entry = {"identifier": identifier, "name": "Entry", "read": True,
                 "data": [{"name": "value", "type": "uint8", "source": {"bsw": "app.value"}}]}
        entry.update(extra)
        return described(data_identifiers=[entry], dtcs=[], fault_memory=None)

    def test_reserved_data_identifiers(self):
        cases = [
            (0x0042, "reserved by ISO 14229-1"),
            (0xA700, "reserved for legislative requirements"),
            (0xF250, "reserved for periodic identifiers"),
            (0xF480, "reserved for regulated OBD"),
            (0xF950, "reserved for tachograph data"),
            (0xFB00, "reserved by ISO 14229-1"),
        ]
        for identifier, fragment in cases:
            with self.subTest(identifier=hex(identifier)):
                self.expect(self.did(identifier), fragment, "data_identifiers[0].identifier")

    def test_free_ranges_pass(self):
        for identifier in (0x0100, 0xA5FF, 0xCF00, 0xF1A5, 0xF1F0, 0xFD00, 0xFEFF):
            with self.subTest(identifier=hex(identifier)):
                self.assertEqual([str(m) for m in self.messages(self.did(identifier))], [])

    def test_the_identifiers_of_the_bootloader_are_refused(self):
        for identifier, fragment in ((0xF184, "fingerprint"), (0xF1A1, "security access")):
            with self.subTest(identifier=hex(identifier)):
                message = self.expect(self.did(identifier), fragment, "data_identifiers[0].identifier")
                self.assertIn("tools/canflash", message.text)

    def test_the_identifiers_of_the_protocol_itself_are_refused(self):
        self.expect(self.did(0xFF00), "answered by the UDS stack itself", "data_identifiers[0].identifier")

    def test_an_identification_identifier_starts_with_the_number_of_modules(self):
        # Annex C.1 / BL-22: the first byte of F180..F182 is the number of modules
        self.expect(self.did(0xF181, data=[{"name": "text", "type": "ascii", "length_bytes": 8,
                                            "source": {"bsw": "app.version"}}]),
                    "the first element has to be a uint8", "data_identifiers[0].data[0].type")

    def test_the_fixed_length_of_the_active_session_is_checked(self):
        self.expect(self.did(0xF186, data=[{"name": "a", "type": "uint16", "source": {"bsw": "uds.session"}}]),
                    "length of 1 byte fixed by ISO", "data_identifiers[0].data")

    def test_a_data_identifier_twice(self):
        description = self.did(0x4711)
        description["data_identifiers"].append(copy.deepcopy(description["data_identifiers"][0]))
        description["data_identifiers"][1]["name"] = "Other"
        self.expect(description, "data identifier 0x4711 is already data_identifiers[0]",
                    "data_identifiers[1].identifier")

    def test_a_name_twice(self):
        description = self.did(0x4711)
        second = copy.deepcopy(description["data_identifiers"][0])
        second["identifier"] = 0x4712
        description["data_identifiers"].append(second)
        self.expect(description, "name Entry is already data_identifiers[0]", "data_identifiers[1].name")

    def test_reserved_routine_identifiers(self):
        cases = [(0x0010, "reserved by ISO 14229-1"), (0x0150, "tachograph"), (0xE100, "OBD test results"),
                 (0xE250, "safety related systems"), (0xFFFF, "reserved by ISO 14229-1")]
        for identifier, fragment in cases:
            with self.subTest(identifier=hex(identifier)):
                description = described(routines=[{"identifier": identifier, "name": "R",
                                                   "start": {"call": {"bsw": "bsw.reset.request"}}}],
                                        dtcs=[], fault_memory=None)
                self.expect(description, fragment, "routines[0].identifier")

    def test_the_routines_of_the_bootloader_are_refused(self):
        description = described(routines=[{"identifier": 0xFF00, "name": "Erase",
                                           "start": {"call": {"bsw": "bsw.reset.request"}}}],
                                dtcs=[], fault_memory=None)
        # 0xFF00 is both a standard routine and one the bootloader answers; both findings are useful
        texts = " ".join(m.text for m in self.messages(description))
        self.assertIn("eraseMemory of the bootloader", texts)
        self.assertIn("Annex F.1", texts)

    def test_reserved_dtc_numbers(self):
        for number, fragment in ((0x0000FF, "legislative"), (0xFFFF33, "group identifier")):
            with self.subTest(number=hex(number)):
                description = copy.deepcopy(BASE)
                description["dtcs"][0]["number"] = number
                self.expect(description, fragment, "dtcs[0].number")

    def test_a_reserved_session_type(self):
        description = described(sessions=[{"name": "Default", "id": 0x01}, {"name": "Odd", "id": 0x20}])
        self.expect(description, "session type 0x20 is reserved by ISO 14229-1", "sessions[1].id")

    def test_an_even_security_level_is_a_send_key(self):
        description = described(security=[{"name": "Unlock", "level": 0x02}])
        self.expect(description, "is named by its requestSeed sub-function", "security[0].level")

    # COM-30: the method of a level
    def test_the_method_defaults_to_hmac_sha256(self):
        config, _ = self.check(BASE)
        self.assertEqual(config.diag.security[0].method, "hmac-sha256")

    def test_an_unknown_method_is_a_schema_error(self):
        description = described(security=[{"name": "Unlock", "level": 0x01, "method": "xor"}])
        _, messages = self.check(description)
        found = [m for m in messages if m.severity == "error" and m.file.name == "diag.yaml"]
        self.assertTrue(any(m.yaml_path == "security[0].method" and "hmac-sha256" in m.text for m in found),
                        [str(m) for m in found])

    def test_hmac_sha256_cuts_seed_and_key_from_32_bytes(self):
        ok = described(security=[{"name": "Unlock", "level": 0x01, "seed_bytes": 32, "key_bytes": 32}])
        self.assertEqual([str(m) for m in self.messages(ok) if m.file.name == "diag.yaml"], [])
        for field_name in ("seed_bytes", "key_bytes"):
            with self.subTest(field_name):
                level = {"name": "Unlock", "level": 0x01, "seed_bytes": 16, "key_bytes": 16, field_name: 33}
                self.expect(described(security=[level]), f"{field_name} 33: method hmac-sha256",
                            f"security[0].{field_name}")

    def test_hmac_sha256_needs_the_key_sector_of_the_bootloader(self):
        """The device key lives in the key sector of the memory partitioning (BL-03); without it nothing
        could unlock the level on the target."""
        self.project_change = lambda project: project.pop("memory")
        self.expect(BASE, "no memory partitioning", "security[0].method")
        # no level, no key needed
        self.assertEqual([str(m) for m in self.messages(described(security=[]))
                          if m.file.name == "diag.yaml" and "hmac" in m.text], [])


class Lengths(DiagProfile):
    """The length of a value has to fit what fills it."""

    def one(self, element):
        return described(data_identifiers=[{"identifier": 0x4711, "name": "Entry", "read": True,
                                            "data": [element]}], dtcs=[], fault_memory=None)

    def test_a_text_constant_longer_than_its_element(self):
        self.expect(self.one({"name": "v", "type": "ascii", "length_bytes": 4,
                              "source": {"constant": "much too long"}}),
                    "is 13 bytes long but v holds 4", "data_identifiers[0].data[0].source.constant")

    def test_a_number_that_does_not_fit_its_type(self):
        self.expect(self.one({"name": "v", "type": "uint8", "source": {"constant": 300}}),
                    "does not fit into v of type uint8 (0..255)",
                    "data_identifiers[0].data[0].source.constant")

    def test_a_text_constant_in_a_numeric_element(self):
        self.expect(self.one({"name": "v", "type": "uint16", "source": {"constant": "text"}}),
                    "the constant is a text but v has type uint16",
                    "data_identifiers[0].data[0].source.constant")

    def test_a_fixed_width_type_must_not_state_a_length(self):
        self.expect(self.one({"name": "v", "type": "uint16", "length_bytes": 2,
                              "source": {"bsw": "app.v"}}),
                    "length_bytes must not be set", "data_identifiers[0].data[0]")

    def test_a_text_needs_a_length(self):
        self.expect(self.one({"name": "v", "type": "ascii", "source": {"bsw": "app.v"}}),
                    "needs length_bytes", "data_identifiers[0].data[0]")

    def test_an_identifier_longer_than_one_iso_tp_message(self):
        self.expect(self.one({"name": "v", "type": "bytes", "length_bytes": 5000,
                              "source": {"bsw": "app.v"}}),
                    "no longer fits into one ISO 15765-2 message", "data_identifiers[0].data")

    def test_a_constant_cannot_be_written(self):
        description = self.one({"name": "v", "type": "uint8", "source": {"constant": 7}})
        description["data_identifiers"][0]["write"] = True
        self.expect(description, "a constant cannot be written", "data_identifiers[0]")

    def test_an_identifier_that_is_neither_read_nor_written(self):
        description = self.one({"name": "v", "type": "uint8", "source": {"bsw": "app.v"}})
        description["data_identifiers"][0]["read"] = False
        self.expect(description, "can neither be read nor written", "data_identifiers[0].read")


class References(DiagProfile):
    """Every name in the description points somewhere."""

    def test_an_unknown_session(self):
        description = copy.deepcopy(BASE)
        description["data_identifiers"][0]["read_sessions"] = ["Programming"]
        self.expect(description, "session Programming is not described in sessions",
                    "data_identifiers[0].read_sessions[0]")

    def test_an_unknown_security_level(self):
        description = copy.deepcopy(BASE)
        description["routines"][0]["security"] = "Supplier"
        self.expect(description, "security level Supplier is not described in security", "routines[0].security")

    def test_an_unknown_operation_cycle(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["operation_cycle"] = "Driving"
        self.expect(description, "operation cycle Driving is not described in operation_cycles",
                    "dtcs[0].operation_cycle")

    def test_an_unknown_enable_condition(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["enable_conditions"] = ["Warm"]
        self.expect(description, "enable condition Warm is not described", "dtcs[0].enable_conditions[0]")

    def test_a_snapshot_names_an_identifier_that_does_not_exist(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["snapshots"][0]["dids"] = ["Speed", "Missing"]
        self.expect(description, "data identifier Missing is not described in data_identifiers",
                    "dtcs[0].snapshots[0].dids[1]")

    def test_a_snapshot_names_an_identifier_twice(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["snapshots"][0]["dids"] = ["Speed", "Speed"]
        self.expect(description, "stands twice in the same snapshot record", "dtcs[0].snapshots[0].dids[1]")

    def test_an_extended_record_names_an_identifier_that_does_not_exist(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["extended_data"][0]["data"] = [{"name": "v", "did": "Missing"}]
        self.expect(description, "data identifier Missing is not described",
                    "dtcs[0].extended_data[0].data[0].did")

    def test_a_port_of_a_component_the_ecu_does_not_run(self):
        description = copy.deepcopy(BASE)
        description["data_identifiers"][0]["data"][0]["source"] = {"port": "Other.Out.Speed"}
        self.expect(description, "names component Other, which the ECU description does not run",
                    "data_identifiers[0].data[0].source.port")

    def test_a_runnable_of_a_component_the_ecu_does_not_run(self):
        description = copy.deepcopy(BASE)
        description["routines"][0]["start"]["call"] = {"runnable": "Other.Run"}
        self.expect(description, "names component Other", "routines[0].start.call.runnable")

    def test_an_event_of_a_component_the_ecu_does_not_run(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["event"] = {"port": "Other.Out.Valid"}
        self.expect(description, "names component Other", "dtcs[0].event.port")

    def test_a_second_component_is_accepted(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["event"] = {"port": "Monitor.Out.Valid"}
        self.assertEqual([str(m) for m in self.messages(description, components=("Swc", "Monitor"))], [])


class FaultMemory(DiagProfile):
    """The fault memory in the scope of the decision of 2026-09-21."""

    def test_every_dtc_needs_an_event(self):
        description = copy.deepcopy(BASE)
        del description["dtcs"][0]["event"]
        self.expect(description, "Field required", "dtcs[0].event")

    def test_an_event_needs_exactly_one_source(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["event"] = {"port": "Swc.Out.Valid", "bsw": "ecu.state"}
        self.expect(description, "name exactly one source of the test result", "dtcs[0].event")

    def test_the_result_of_an_event_and_its_limits(self):
        """NVM-03: how a value becomes a test result is part of the event."""
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["event"] = {"port": "Swc.Out.Valid", "result": "limit"}
        self.expect(description, "result limit needs failed_above, failed_below or both", "dtcs[0].event")
        description["dtcs"][0]["event"] = {"port": "Swc.Out.Valid", "result": "boolean", "failed_above": 3}
        self.expect(description, "failed_above belong to result limit, not boolean", "dtcs[0].event")
        description["dtcs"][0]["event"] = {"port": "Swc.Out.Valid", "failed_above": 10, "failed_below": 10}
        self.expect(description, "no value would pass", "dtcs[0].event")
        description["dtcs"][0]["event"] = {"port": "Swc.Out.Valid", "result": "average"}
        self.expect(description, "Input should be", "dtcs[0].event.result")
        description["dtcs"][0]["event"] = {"port": "Swc.Out.Valid", "failed_above": 4900, "failed_below": 100}
        self.assertEqual(self.messages(description), [])

    def test_cycle_and_write_interval_of_the_memory(self):
        """NVM-03: sample cycle and the shortest distance of the NvM writes are values of the description."""
        description = copy.deepcopy(BASE)
        description["fault_memory"]["cycle_ms"] = 0
        self.expect(description, "greater than or equal to 1", "fault_memory.cycle_ms")
        description["fault_memory"]["cycle_ms"] = 10
        description["fault_memory"]["write_interval_ms"] = -1
        self.expect(description, "greater than or equal to 0", "fault_memory.write_interval_ms")

    def test_debouncing_is_either_counted_or_timed(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["debounce"] = {"counter": {"failed_threshold": 3},
                                              "time": {"failed_ms": 10, "passed_ms": 10}}
        self.expect(description, "name exactly one way of debouncing", "dtcs[0].debounce")

    def test_time_debouncing_above_one_hour_is_a_warning(self):
        """ARCH-68: upper limit of the AUTOSAR Dem definition, warning only."""
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["debounce"] = {"time": {"failed_ms": 3_600_001, "passed_ms": 3_600_000}}
        self.expect(description, "is above 3600000 ms", "dtcs[0].debounce.time.failed_ms", severity="warning")
        self.assertEqual([m for m in self.messages(description, "warning") if "passed_ms" in str(m.yaml_path)], [])
        self.assertEqual([m for m in self.messages(description) if "debounce" in str(m.yaml_path)], [])

    def test_a_jump_past_the_threshold_would_defeat_the_debouncing(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["debounce"] = {"counter": {"failed_threshold": 5, "passed_threshold": -5,
                                                          "jump_up": 5}}
        self.expect(description, "the debouncing would have no effect", "dtcs[0].debounce.counter.jump_up")

    def test_more_snapshots_than_the_memory_stores(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["snapshots"].append({"record_number": 0x02, "dids": ["Speed"]})
        self.expect(description, "the fault memory stores 1 per DTC", "dtcs[0].snapshots")

    def test_a_snapshot_record_number_twice(self):
        description = copy.deepcopy(BASE)
        description["fault_memory"]["snapshot_records_max"] = 2
        description["dtcs"][0]["snapshots"].append({"record_number": 0x01, "dids": ["Speed"]})
        self.expect(description, "snapshot record 0x1 is already dtcs[0].snapshots[0]",
                    "dtcs[0].snapshots[1].record_number")

    def test_dtcs_without_a_fault_memory(self):
        description = described(fault_memory=None)
        del description["fault_memory"]
        self.expect(description, "there is no fault_memory section", "dtcs")

    def test_dtcs_without_an_operation_cycle(self):
        description = described(operation_cycles=[])
        self.expect(description, "operation_cycles is empty", "dtcs")

    def test_more_dtcs_than_the_memory_holds_is_a_warning(self):
        description = copy.deepcopy(BASE)
        description["fault_memory"]["max_entries"] = 1
        description["dtcs"].append({**copy.deepcopy(description["dtcs"][0]),
                                    "number": 0x123457, "name": "Second"})
        message = self.expect(description, "the memory holds 1 at the same time", "fault_memory.max_entries",
                              severity="warning")
        self.assertIn("replace_oldest", message.text)

    def test_an_extended_record_number_outside_the_manufacturer_range(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["extended_data"][0]["record_number"] = 0xFF
        self.expect(description, "less than or equal to 143", "dtcs[0].extended_data[0].record_number")

    def test_an_extended_record_item_is_a_counter_or_an_identifier(self):
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["extended_data"][0]["data"] = [{"name": "v", "counter": "aging_counter",
                                                               "did": "Speed"}]
        self.expect(description, "name exactly one content", "dtcs[0].extended_data[0].data[0]")


class AutosarUpperLimits(DiagProfile):
    """ARCH-68: P2, P2* and the number of rows above what the AUTOSAR Dcm definition accepts are warnings."""

    def test_p2_and_p2_star_at_the_limit_pass_above_it_they_warn(self):
        description = copy.deepcopy(BASE)
        description["sessions"][0].update({"p2_ms": 1000, "p2_star_ms": 100_000})
        self.assertEqual([m for m in self.messages(description, "warning") if m.file.name == "diag.yaml"], [])
        description["sessions"][0].update({"p2_ms": 1001, "p2_star_ms": 100_001})
        self.expect(description, "p2_ms 1001 is above 1000 ms", "sessions[0].p2_ms", severity="warning")
        self.expect(description, "p2_star_ms 100001 is above 100000 ms", "sessions[0].p2_star_ms",
                    severity="warning")
        self.assertEqual(self.messages(description), [])           # never an error

    def test_more_than_31_security_levels_warn(self):
        description = copy.deepcopy(BASE)
        description["security"] = [{"name": f"L{i}", "level": 2 * i + 1} for i in range(32)]
        self.expect(description, "32 security levels are described", "security", severity="warning")
        del description["security"][-1]
        found = [m for m in self.messages(description, "warning") if "security levels" in m.text]
        self.assertEqual(found, [])


class TextTables(DiagProfile):

    def one(self, **element):
        return described(data_identifiers=[{"identifier": 0x4711, "name": "Entry", "read": True,
                                            "data": [{"name": "v", "source": {"bsw": "app.v"}, **element}]}],
                         dtcs=[], fault_memory=None)

    def test_a_text_table_on_a_float_is_refused(self):
        self.expect(self.one(type="float32", values=[{"value": 0, "text": "off"}]),
                    "not of float32", "data_identifiers[0].data[0]")

    def test_a_text_table_replaces_the_linear_conversion(self):
        self.expect(self.one(type="uint8", factor=2.0, values=[{"value": 0, "text": "off"}]),
                    "replaces the linear conversion", "data_identifiers[0].data[0]")

    def test_a_coded_value_twice(self):
        self.expect(self.one(type="uint8", values=[{"value": 1, "text": "a"}, {"value": 1, "text": "b"}]),
                    "every coded value stands once in a text table", "data_identifiers[0].data[0]")

    def test_min_above_max(self):
        self.expect(self.one(type="uint8", min=10, max=1), "is larger than max", "data_identifiers[0].data[0]")


class DtcGroups(DiagProfile):
    """NVM-09: `dtc_groups`, the groups 14 clears besides all DTCs and single DTCs (Annex D.1)."""

    # SpeedImplausible of BASE is 0x123456; a second DTC outside every group below
    DTCS = copy.deepcopy(BASE["dtcs"]) + [dict(copy.deepcopy(BASE["dtcs"][0]), number=0xC10016,
                                                name="BusOff", snapshots=[], extended_data=[])]

    def grouped(self, *groups):
        return described(dtcs=self.DTCS, dtc_groups=list(groups))

    def test_groups_pass_and_their_members_are_derived(self):
        description = self.grouped({"name": "Safety", "group": 0xFFFFD0, "first": 0x123456, "last": 0x123456},
                                   {"name": "Low", "group": 0x100000, "first": 0x100000, "last": 0xC10016},
                                   {"name": "Network", "group": 0xC00000, "first": 0xC00000, "last": 0xFFFEFF})
        config, messages = self.check(description)
        self.assertEqual([str(m) for m in messages if m.file.name == "diag.yaml"], [])
        # overlapping groups are allowed (a DTC can belong to a manufacturer and a functional group)
        self.assertEqual(config.diag_description.dtc_groups,
                         {"Safety": ("SpeedImplausible",), "Low": ("SpeedImplausible", "BusOff"),
                          "Network": ("BusOff",)})

    def test_without_groups_nothing_is_derived(self):
        config, _ = self.check(BASE)
        self.assertEqual(config.diag_description.dtc_groups, {})

    def test_all_dtcs_need_no_entry(self):
        self.expect(self.grouped({"name": "All", "group": 0xFFFFFF, "first": 0x000100, "last": 0xFFFEFF}),
                    "stands for all DTCs", "dtc_groups[0].group")

    def test_a_group_with_the_number_of_a_dtc(self):
        self.expect(self.grouped({"name": "Odd", "group": 0x123456, "first": 0x120000, "last": 0x12FFFF}),
                    "is the number of DTC SpeedImplausible", "dtc_groups[0].group")

    def test_a_group_in_the_legislative_range(self):
        self.expect(self.grouped({"name": "Low", "group": 0x000010, "first": 0x100000, "last": 0x1FFFFF}),
                    "reserved for legislative requirements", "dtc_groups[0].group")

    def test_first_above_last(self):
        self.expect(self.grouped({"name": "Turned", "group": 0x100000, "first": 0x1FFFFF, "last": 0x100000}),
                    "is below first 0x1FFFFF", "dtc_groups[0].last")

    def test_a_range_into_the_group_identifiers(self):
        self.expect(self.grouped({"name": "Wide", "group": 0xC00000, "first": 0xC00000, "last": 0xFFFFFF}),
                    "reaches the group identifiers", "dtc_groups[0].last")

    def test_a_group_twice(self):
        description = self.grouped({"name": "A", "group": 0x100000, "first": 0x100000, "last": 0x1FFFFF},
                                   {"name": "B", "group": 0x100000, "first": 0x120000, "last": 0x12FFFF})
        self.expect(description, "group identifier 0x100000 is already dtc_groups[0] (A)", "dtc_groups[1].group")

    def test_a_name_twice(self):
        description = self.grouped({"name": "A", "group": 0x100000, "first": 0x100000, "last": 0x1FFFFF},
                                   {"name": "A", "group": 0x200000, "first": 0x200000, "last": 0x2FFFFF})
        self.expect(description, "name A is already dtc_groups[0]", "dtc_groups[1].name")

    def test_a_group_without_a_described_dtc_is_a_warning(self):
        description = self.grouped({"name": "Body", "group": 0x800000, "first": 0x800000, "last": 0xBFFFFF})
        self.expect(description, "holds none of the described DTCs", "dtc_groups[0]", severity="warning")
        self.assertEqual(self.messages(description), [])

    def test_a_value_beyond_three_bytes_is_a_schema_error(self):
        self.expect(self.grouped({"name": "Big", "group": 0x1000000, "first": 0x100000, "last": 0x1FFFFF}),
                    "less than or equal to 16777215", "dtc_groups[0].group")


class DemoProject(unittest.TestCase):
    """The description of the full test profile (testproject_full/diag.yaml, PLAT-62; the same jobs as the eval
    board demo)."""

    def test_the_demo_dtc_groups(self):
        """NVM-09: the manufacturer groups of the demo, each with one of its DTCs."""
        config, _ = nxconfig.check(DEMO_PROJECT)
        self.assertEqual([(g.name, hex(g.group), hex(g.first), hex(g.last)) for g in config.diag.dtc_groups],
                         [("Body", "0x800000", "0x800000", "0xbfffff"),
                          ("Network", "0xc00000", "0xc00000", "0xfffeff")])
        self.assertEqual(config.diag_description.dtc_groups,
                         {"Body": ("MotorNtcOpenCircuit",), "Network": ("PcCommandTimeout",)})

    def test_the_demo_description_is_checked_with_the_project(self):
        config, messages = nxconfig.check(DEMO_PROJECT)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        self.assertEqual(config.diag_file.name, "diag.yaml")
        self.assertEqual([d.identifier for d in config.diag.data_identifiers],
                         [0xF180, 0xF181, 0xF186, 0xF18C, 0xF1F1, 0xF1F2, 0xFD00, 0x0110, 0x0120, 0x0130])
        # F1F1: PLAT-79, F1F2: PLAT-93, 0x0130: NVM-05

    def test_the_lengths_of_the_demo_identifiers_are_derived(self):
        config, _ = nxconfig.check(DEMO_PROJECT)
        lengths = config.diag_description.did_lengths
        # F180/F181: one numberOfModules byte plus the 32 byte version text (Annex C.1, BL-22)
        self.assertEqual(lengths["BootSoftwareIdentification"], 33)
        self.assertEqual(lengths["ApplicationSoftwareIdentification"], 33)
        self.assertEqual(lengths["ActiveDiagnosticSession"], 1)
        self.assertEqual(lengths["RamTestValue"], 4)        # RAM_TEST_DID_LENGTH of AppDataIdentifiers.h

    def test_the_demo_dtcs_carry_their_derived_severity(self):
        config, _ = nxconfig.check(DEMO_PROJECT)
        self.assertEqual([(d.name, hex(d.number), hex(d.severity_byte)) for d in config.diag_description.dtcs],
                         [("MotorNtcOpenCircuit", "0x904001", "0x41"),
                          ("PcCommandTimeout", "0xc10016", "0x1"),
                          # SYS-113: the causes of a safety reset from the standard description, derived numbers
                          ("SafetyResetMcuWatchdog", "0x1605fd", "0x81"),
                          ("SafetyResetFaultException", "0x1605fc", "0x81"),
                          ("SafetyResetDeadmanConfiguration", "0x160510", "0x81"),
                          ("SafetyResetOther", "0x1605fb", "0x81"),
                          ("SafetyResetLoop", "0x1605ff", "0x81"),
                          # NVM-30: one defective place of the QM block EcuStatistics (+0x09nn, block index 2)
                          ("NvmEcuStatisticsPlaceDefective", "0x160902", "0x81")])

    def test_the_demo_project_carries_the_file(self):
        config, _ = nxconfig.check(DEMO_PROJECT)
        settings, _ = nxconfig.config.build_settings(config)
        self.assertTrue(settings["NX_DIAG_FILE"].endswith("testproject_full/diag.yaml"))   # PLAT-62
        self.assertIn(settings["NX_DIAG_FILE"], settings["NX_CONFIG_FILES"])


class ResponseSplitsByTheDescription(DiagProfile):
    """COM-33: several identifiers of one 22 response stand back to back without a length of their own; a tester
    splits them by the described length alone, so every text has its full length_bytes on the wire."""

    DESCRIPTION = described(data_identifiers=[
        {"identifier": 0x4711, "name": "Identification", "read": True, "data": [
            {"name": "count", "type": "uint8", "source": {"constant": 1}},
            {"name": "text", "type": "ascii", "length_bytes": 12, "source": {"bsw": "app.version"}}]},
        {"identifier": 0x4712, "name": "Labelled", "read": True, "data": [
            {"name": "label", "type": "ascii", "length_bytes": 6, "source": {"constant": "AB"}},
            {"name": "value", "type": "uint16", "source": {"bsw": "app.value"}}]},
        {"identifier": 0x4713, "name": "Level", "read": True,
         "data": [{"name": "level", "type": "uint8", "source": {"bsw": "app.level"}}]},
    ], dtcs=[], fault_memory=None)

    def setUp(self):
        super().setUp()
        config, messages = self.check(self.DESCRIPTION)
        self.assertEqual([str(m) for m in messages if m.file.name == "diag.yaml"], [])
        self.desc = config.diag

    def test_three_identifiers_with_texts_in_one_response(self):
        payload = (b"\x62" + b"\x47\x11" + b"\x01" + b"v1.2".ljust(12, b"\0")
                   + b"\x47\x12" + b"AB\0\0\0\0" + b"\x12\x34"
                   + b"\x47\x13" + b"\x07")
        records = diagdesc.split_read_response(self.desc, payload)
        self.assertEqual([(r.identifier, r.name, len(r.data)) for r in records],
                         [(0x4711, "Identification", 13), (0x4712, "Labelled", 8), (0x4713, "Level", 1)])
        self.assertEqual(records[0].elements["count"], b"\x01")
        self.assertEqual(records[0].text("text"), "v1.2")
        self.assertEqual(records[1].text("label"), "AB")
        self.assertEqual(records[1].elements["value"], b"\x12\x34")      # behind the text, at a fixed offset
        self.assertEqual(records[2].elements["level"], b"\x07")
        self.assertEqual(diagdesc.TEXT_FILL_BYTE, 0x00)

    def test_a_text_with_its_runtime_length_cannot_be_split(self):
        """What COM-33 fixes: the text of 0x4711 without its fill bytes shifts every identifier behind it."""
        payload = b"\x62" + b"\x47\x11" + b"\x01" + b"v1.2" + b"\x47\x13" + b"\x07"
        with self.assertRaises(ValueError) as ctx:
            diagdesc.split_read_response(self.desc, payload)
        self.assertIn("Identification needs 13 bytes", str(ctx.exception))

    def test_unknown_identifier_and_cut_off_response(self):
        with self.assertRaisesRegex(ValueError, "0x4714 is not in the description"):
            diagdesc.split_read_response(self.desc, b"\x62\x47\x14\x00")
        with self.assertRaisesRegex(ValueError, "identifier cut off"):
            diagdesc.split_read_response(self.desc, b"\x62\x47\x13\x07\x47")
        with self.assertRaisesRegex(ValueError, "not a positive ReadDataByIdentifier"):
            diagdesc.split_read_response(self.desc, b"\x7f\x22\x31")

    def test_the_demo_identifiers_split(self):
        """F180, F181 and F186 of the demo project, as the application answers them since COM-33."""
        config, _ = nxconfig.check(DEMO_PROJECT)
        payload = (b"\x62" + b"\xf1\x80\x01" + b"nextbsw-bl 0.2.0".ljust(32, b"\0")
                   + b"\xf1\x81\x01" + b"nextbsw-app 0.1.0".ljust(32, b"\0") + b"\xf1\x86\x03")
        records = diagdesc.split_read_response(config.diag, payload)
        self.assertEqual([r.identifier for r in records], [0xF180, 0xF181, 0xF186])
        self.assertEqual(records[0].text("bootSoftwareIdentification"), "nextbsw-bl 0.2.0")
        self.assertEqual(records[1].text("applicationSoftwareIdentification"), "nextbsw-app 0.1.0")
        self.assertEqual(records[2].elements["activeSession"], b"\x03")


def bsw_dtc(name: str, event: str, **extra) -> dict:
    """A DTC of the basic software without a number (TOOL-178: derived from bsw_dtc_range and the plan)."""
    return {"name": name, "operation_cycle": "Power", "event": {"bsw": event},
            "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}}, **extra}


class BswDtcRange(DiagProfile):
    """TOOL-178: the neXtBSW DTC range as project value (bsw_dtc_range), numbers derived from base + plan, and the
    checks around it - each with its YAML path."""

    def with_bsw(self, *dtcs, span=None, groups=None):
        changes = {"dtcs": copy.deepcopy(BASE["dtcs"]) + list(dtcs)}
        if span is not None:
            changes["bsw_dtc_range"] = span
        if groups is not None:
            changes["dtc_groups"] = groups
        return described(**changes)

    def test_the_plan_itself(self):
        self.assertEqual(bswdtc.offset_of("rte.deadline"), 0x0100)
        self.assertEqual(bswdtc.offset_of("calibration.plausibility"), 0x0201)
        self.assertEqual(bswdtc.offset_of("can.busoff.CAN1", 2), 0x0302)
        self.assertEqual(bswdtc.offset_of("safety.reset_loop"), 0x05FF)
        self.assertEqual(bswdtc.offset_of("safety.watchdog", 3), 0x0503)
        # NVM-19: the three NvM events per block
        self.assertEqual([bswdtc.offset_of(f"nvm.{e}.Params", 4) for e in
                          ("default_loaded", "copy_repair_failed", "copy_repaired")], [0x0604, 0x0704, 0x0804])
        # NVM-30: one defective place of a QM block
        self.assertEqual(bswdtc.offset_of("nvm.place_defective.Params", 4), 0x0904)
        self.assertEqual(bswdtc.PLAN_SIZE, 0x0A00)
        for event, instance, fragment in (("app.monitor", None, "is no source"), (None, None, "no BSW event"),
                                          ("can.busoff.CAN1", None, "bsw_instance gives nn"),
                                          ("rte.deadline", 1, "leave bsw_instance out"),
                                          ("safety.x", 0xFF, "fixed sub-type")):
            with self.subTest(event=event), self.assertRaises(bswdtc.DerivationError) as e:
                bswdtc.offset_of(event, instance)
            self.assertIn(fragment, str(e.exception))
        self.assertEqual(bswdtc.display_code(0x160100), "P160100")
        self.assertEqual(bswdtc.display_code(0x5A0201), "C1A0201")
        self.assertEqual(bswdtc.display_code(0xC10016), "U010016")
        self.assertIsNone(bswdtc.range_problem(bswdtc.BSW_DTC_BASE, bswdtc.BSW_DTC_SIZE))
        self.assertIn("multiples", bswdtc.range_problem(0x160010, 0x100))
        self.assertIn("legislation", bswdtc.range_problem(0x000000, 0x100))
        self.assertIn("group identifiers", bswdtc.range_problem(0xFFFE00, 0x200))

    def test_numbers_are_derived_from_the_default_range(self):
        # COM-46: bsw_instance of a bus-off DTC is the index of the bus among the used buses (VEH 0, DIAG 1)
        description = self.with_bsw(bsw_dtc("RteDeadline", "rte.deadline"),
                                    bsw_dtc("BusOffDiag", "can.busoff.DIAG", bsw_instance=1),
                                    groups=[{"name": "NeXtBSW", "bsw": True}])
        config, messages = self.check(description)
        self.assertEqual([str(m) for m in messages if m.file.name == "diag.yaml"], [])
        numbers = {d.name: (d.number, d.display_code, d.number_derived) for d in config.diag.dtcs}
        self.assertEqual(numbers["RteDeadline"], (0x160100, "P160100", True))
        self.assertEqual(numbers["BusOffDiag"], (0x160301, "P160301", True))
        self.assertEqual(numbers["SpeedImplausible"][2], False)
        group = config.diag.dtc_groups[0]
        self.assertEqual((group.group, group.first, group.last), (0x160000, 0x160000, 0x16FFFF))
        self.assertEqual(config.diag_description.dtc_groups, {"NeXtBSW": ("RteDeadline", "BusOffDiag")})
        self.assertEqual([p.number for p in config.diag_description.dtcs], [0x123456, 0x160100, 0x160301])

    def test_a_customer_range_moves_every_bsw_dtc(self):
        description = self.with_bsw(bsw_dtc("RteDeadline", "rte.deadline"),
                                    span={"base": 0xD20000, "size": 0x1000},
                                    groups=[{"name": "NeXtBSW", "bsw": True}])
        config, messages = self.check(description)
        self.assertEqual([str(m) for m in messages if m.file.name == "diag.yaml"], [])
        self.assertEqual(config.diag.dtcs[1].number, 0xD20100)
        self.assertEqual(config.diag.dtcs[1].display_code, "U120100")
        group = config.diag.dtc_groups[0]
        self.assertEqual((group.group, group.first, group.last), (0xD20000, 0xD20000, 0xD20FFF))

    def test_an_invalid_range(self):
        self.expect(self.with_bsw(span={"base": 0x160080}), "multiple of 256", "bsw_dtc_range.base")
        self.expect(self.with_bsw(span={"base": 0x000000}), "greater than or equal to 256", "bsw_dtc_range.base")
        self.expect(self.with_bsw(span={"size": 0x180}), "multiple of 256", "bsw_dtc_range.size")
        self.expect(self.with_bsw(span={"base": 0xFFF000, "size": 0x1000}), "reaches the group identifiers",
                    "bsw_dtc_range")

    def test_a_number_that_cannot_be_derived(self):
        self.expect(self.with_bsw(bsw_dtc("Heater", "app.heater")), "is no source of it", "dtcs[1].number")
        port = bsw_dtc("Port", "x")
        port["event"] = {"port": "Swc.Out.SpeedValid"}
        self.expect(self.with_bsw(port), "the event is no BSW event", "dtcs[1].number")
        self.expect(self.with_bsw(bsw_dtc("BusOff", "can.busoff.VEH")), "bsw_instance gives nn",
                    "dtcs[1].bsw_instance")
        self.expect(self.with_bsw(bsw_dtc("Rte", "rte.deadline", bsw_instance=2)), "leave bsw_instance out",
                    "dtcs[1].bsw_instance")
        self.expect(self.with_bsw(bsw_dtc("Nvm", "nvm.default_loaded.Params", bsw_instance=3),
                                  span={"size": 0x600}), "raise bsw_dtc_range.size to at least 0x700",
                    "dtcs[1].number")
        self.expect(self.with_bsw(bsw_dtc("Rte", "rte.deadline", number=0x160100, bsw_instance=0)),
                    "leave it out with an explicit number", "dtcs[1]")

    def test_a_project_dtc_inside_the_range_is_a_warning(self):
        """Proposal B of TOOL-131: a DTC of the project (no BSW source) inside the neXtBSW range."""
        description = copy.deepcopy(BASE)
        description["dtcs"][0]["number"] = 0x160500
        self.expect(description, "no BSW source reports it", "dtcs[0].number", severity="warning")
        # the same number in a customer range elsewhere: no finding
        description["bsw_dtc_range"] = {"base": 0x5A0000}
        self.assertEqual([str(m) for m in self.messages(description, "warning") if m.file.name == "diag.yaml"], [])

    def test_an_explicit_bsw_number_against_the_plan(self):
        self.expect(self.with_bsw(bsw_dtc("Rte", "rte.deadline", number=0x160105)), "the numbering plan gives 0x160100",
                    "dtcs[1].number", severity="warning")
        # the number of the plan written out (projects of TOOL-131) and a BSW source outside the plan: accepted
        clean = self.with_bsw(bsw_dtc("Rte", "rte.deadline", number=0x160100),
                              bsw_dtc("Legacy", "can.busoff.CAN1", number=0x160333))
        self.assertEqual([str(m) for m in self.messages(clean, "warning") if m.file.name == "diag.yaml"], [])
        self.expect(self.with_bsw(bsw_dtc("Rte", "rte.deadline", number=0x160000)), "is the group identifier",
                    "dtcs[1].number")

    def test_a_derived_number_collides_with_an_explicit_one(self):
        self.expect(self.with_bsw(bsw_dtc("Explicit", "app.x", number=0x160100), bsw_dtc("Rte", "rte.deadline")),
                    "DTC number 0x160100 is already dtcs[1] (Explicit)", "dtcs[2].number")

    def test_a_range_smaller_than_the_plan_is_a_warning(self):
        self.expect(self.with_bsw(bsw_dtc("Rte", "rte.deadline"), span={"size": 0x300}), "nvm_copy_repaired (+0x08xx)",
                    "bsw_dtc_range.size", severity="warning")

    def test_the_group_of_the_range(self):
        self.expect(self.with_bsw(groups=[{"name": "N", "bsw": True, "group": 0x160000}]),
                    "are derived from bsw_dtc_range", "dtc_groups[0]")
        self.expect(self.with_bsw(groups=[{"name": "N", "group": 0x160000}]), "first, last missing",
                    "dtc_groups[0]")


if __name__ == "__main__":
    unittest.main()
