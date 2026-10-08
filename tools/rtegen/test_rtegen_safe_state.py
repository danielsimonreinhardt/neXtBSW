"""SYS-141 (safety review SYS-116/SYS-30 K8, follow-up C6): the RTE port of the safe state selection and the start test
of the active short.

A project in which a PWM group allows an active short (pwm.<group>.safe_states, SYS-116) gets
  safety.safe_state.<GROUP>         provided port: the requested state (0 freewheel, 1 short low, 2 short high)
  safety.safe_state_active.<GROUP>  required port: the state the switch-off paths would apply now
  safety.qlf.safe_state.<GROUP>     required port: QLF_OK while the request is in force and no state is removed
and, for pwm.<group>.safe_state_test, the table rte::SAFE_STATE_TESTS that RteSystem runs before the init runnables
(ecu/application/src/systems/safeState/SafeStateRte.h over the family's service hardFaultHandler/safeStateSelection.h).

Own test profile: the synthetic components of test/multitask (two tasks, BSW task profile test/multitask/tasks.yaml)
on the build test project buildtest_s32k148_min with an in-memory safe state plan of two PWM groups (BRIDGE: freewheel,
both shorts, start test 300 us; AUX: freewheel and short low, no test) - never the eval board. Every project of the
repository allows no short today (nxconfig refuses it until C2/C4), so none of them generates a line of it.

  SafeStateGenTest        generated hand-over, request in the writer's task, reads per task, table of the start
                          tests, refused connections; without a short or without targets no line
  SafeStateSilTest        test/safe_state/rte_safe_state_host_test.cpp: generated RTE + SafeStateRte.h + a model of
                          the service with fault injection (refused request, blocked short)
  SafeStateServiceTest    ecu/application/src/systems/test/host/SafeStateRteHostTest.cpp: SafeStateRte.h and
                          SafeStateService.h on the REAL selection service of the S32K1 port
                          (platforms/s32k1xx/hardFaultHandler/src/SafeOutputSelection.cpp) and a register model: start
                          test passes, stuck register, clock off, blocked group, selection refused/in force
  SafeStateMutation       both host tests against deliberately broken generated code and glue - every fault found by a
                          check
SYS-143 (safety review SYS-136/SYS-141, E2-E4): the table rows carry the reader of the bound gate driver (E3, enable
must read off), the header the budget of all holds (E2, a third of the MCU watchdog time-out, error above it); the
service test covers enable on, a stuck clock and the power-on-only start with its no-init record (E4); eleven more
mutants of the glue.

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_safe_state.py
"""

import contextlib
import copy
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools"))

import rtegen  # noqa: E402
from nxconfig import safestate  # noqa: E402
from test_rtegen_dem import BSP_INCLUDE, CXX_FLAGS, MULTI, MULTI_TASKS, PROJECT, ZIG  # noqa: E402

ECU_FILE = MULTI / "rte.yaml"
ECU = rtegen.with_resolved_paths(yaml.safe_load(ECU_FILE.read_text(encoding="utf-8")), ECU_FILE)
SIL = HERE / "test" / "safe_state" / "rte_safe_state_host_test.cpp"
SYSTEMS = REPO / "ecu" / "application" / "src" / "systems"
GLUE = SYSTEMS / "safeState" / "SafeStateRte.h"
SERVICE_TEST = SYSTEMS / "test" / "host" / "SafeStateRteHostTest.cpp"
HARD_FAULT = REPO / "platforms" / "s32k1xx" / "hardFaultHandler"

# AppSwc.SlowCount (uint32, app task) requests BRIDGE; AppSwc.Speed (uint32, app task) reads the state in force;
# MotorSwc.Setpoint (uint16, motor task) the qualifier of BRIDGE - a reader in another task than the writer
CONNECTIONS = {"AppSwc.SlowCount.SlowCount": "safety.safe_state.BRIDGE",
               "AppSwc.Speed.Speed": "safety.safe_state_active.BRIDGE",
               "MotorSwc.Setpoint.Setpoint": "safety.qlf.safe_state.BRIDGE"}


def plan(test=True):
    return safestate.SafeStatePlan([
        safestate.GroupPlan("BRIDGE", ("freewheel", "active_short_low", "active_short_high"), test,
                            300 if test else None),
        safestate.GroupPlan("AUX", ("freewheel", "active_short_low"), False, None),
        safestate.GroupPlan("FAN", ("freewheel",), False, None)])


def config(safe_states=None):
    cfg = copy.copy(PROJECT)
    cfg.safe_states = safe_states if safe_states is not None else plan()
    return cfg


def ecu(**connections):
    data = copy.deepcopy(ECU)
    data["connections"].update(connections if connections else CONNECTIONS)
    return data


def generate(tmp: Path, data: dict, cfg, out: str = "out") -> str:
    path = tmp / "rte.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with contextlib.redirect_stderr(io.StringIO()) as err:
        rtegen.generate(path, cfg, tmp / out, REPO, MULTI_TASKS)
    return err.getvalue()


class SafeStateGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def expect_error(self, fragment, cfg=None, **connections):
        with self.assertRaises(rtegen.RteError) as ctx:
            generate(self.tmp, ecu(**connections), cfg if cfg is not None else config())
        self.assertIn(fragment, str(ctx.exception))

    def test_ports_and_start_test(self):
        generate(self.tmp, ecu(), config())
        cpp, header = self.generated(), self.generated("RteConfig.h")
        self.assertEqual(cpp.count("void request(uint32_t group, uint32_t state);"), 1)
        write_app = cpp.split("void writeOutputs_app()")[1].split("\n}\n")[0]
        self.assertIn("::safestate::rte::request(0U, static_cast<uint32_t>(Rte_Buf_AppSwc_SlowCount_SlowCount));",
                      write_app)
        self.assertNotIn("request(", cpp.split("void writeOutputs_motor()")[1].split("\n}\n")[0])
        read_app = cpp.split("void readInputs_app()")[1].split("\n}\n")[0]
        read_motor = cpp.split("void readInputs_motor()")[1].split("\n}\n")[0]
        # one reading of the group per task that reads it; the mask is the project's (freewheel + both shorts)
        for text in (read_app, read_motor):
            self.assertEqual(text.count("::safestate::rte::read(0U, 0x7U, safeState_BRIDGE, "
                                        "safeStateInForce_BRIDGE);"), 1)
        self.assertIn("= static_cast<uint32>(safeState_BRIDGE);", read_app)
        ok, error = rtegen.QLF["OK"], rtegen.QLF["ERROR"]
        self.assertIn(f"= static_cast<uint16>(safeStateInForce_BRIDGE ? {ok}U : {error}U);", read_motor)
        self.assertIn("#define NX_RTE_HAS_SAFE_STATE 1", header)
        self.assertIn("constexpr size_t SAFE_STATE_GROUP_COUNT = 3U;", header)
        self.assertIn("#define NX_RTE_HAS_SAFE_STATE_TEST 1", header)
        # one test per short of the group with the test: low first, then high, 300 us each; no gate driver bound
        self.assertIn('{0U, 1U, 300U, "BRIDGE", nullptr},\n    {0U, 2U, 300U, "BRIDGE", nullptr},\n};', header)
        self.assertIn("constexpr size_t SAFE_STATE_TEST_COUNT = 2U;", header)
        # SYS-143 (E2): a third of the watchdog time-out of the profile (safeLifecycle default 250 ms)
        self.assertIn("constexpr uint32_t SAFE_STATE_TEST_BUDGET_US = 83333U;", header)
        self.assertIn("the sum of the rows is 600 us", header)
        self.assertNotIn("readGateDriver", header)

    def test_start_test_reads_the_bound_gate_driver(self):
        # SYS-143 (E3): the row carries the reader of the gate driver bound to the group, declared once
        with mock.patch.object(rtegen, "safe_state_gate_drivers", return_value={"BRIDGE": "BRIDGE_GD"}):
            generate(self.tmp, ecu(), config())
        header = self.generated("RteConfig.h")
        self.assertIn('{0U, 1U, 300U, "BRIDGE", &::integration::rte::readGateDriver_BRIDGE_GD},\n'
                      '    {0U, 2U, 300U, "BRIDGE", &::integration::rte::readGateDriver_BRIDGE_GD},\n};', header)
        self.assertEqual(header.count(
            "void readGateDriver_BRIDGE_GD(uint16_t& faults, bool& enabled, uint8_t& health);"), 1)
        # declared at global scope before the rte namespace, so the pointer names ::integration::rte
        self.assertIn("namespace integration\n{\nnamespace rte\n{\n// SYS-143", header)
        self.assertLess(header.index("void readGateDriver_BRIDGE_GD"), header.index("constexpr size_t TASK_COUNT")
                        if "constexpr size_t TASK_COUNT" in header else header.index("struct SafeStateTest"))

    def test_gate_driver_binding(self):
        # SYS-143 (E3): board pwm[].gate_driver, only a device whose module has RTE access gate_driver
        board = SimpleNamespace(pwm=[SimpleNamespace(name="BRIDGE", gate_driver="BRIDGE_GD"),
                                     SimpleNamespace(name="AUX", gate_driver="AUX_SENSE"),
                                     SimpleNamespace(name="FAN", gate_driver=None)])
        sources = {"gd": SimpleNamespace(access="gate_driver", names={"BRIDGE_GD"}),
                   "sense": SimpleNamespace(access="reading", names={"AUX_SENSE"})}
        with mock.patch.object(rtegen, "device_sources", return_value=sources):
            self.assertEqual(rtegen.safe_state_gate_drivers(SimpleNamespace(board=board)), {"BRIDGE": "BRIDGE_GD"})
        self.assertEqual(rtegen.safe_state_gate_drivers(SimpleNamespace()), {})

    def test_budget_of_the_holds(self):
        # SYS-143 (E2): the sum of all holds against a third of the watchdog time-out, error with the YAML path
        def with_us(us):
            return safestate.SafeStatePlan([
                safestate.GroupPlan("BRIDGE", ("freewheel", "active_short_low", "active_short_high"), True, us)])
        generate(self.tmp, copy.deepcopy(ECU), config(with_us(41666)))           # 83332 us in all: accepted
        self.assertIn("SAFE_STATE_TEST_BUDGET_US = 83333U;", self.generated("RteConfig.h"))
        with self.assertRaises(rtegen.RteError) as ctx:
            generate(self.tmp, copy.deepcopy(ECU), config(with_us(41667)))       # 83334 us
        text = str(ctx.exception)
        for fragment in ("project.yaml pwm.BRIDGE.safe_state_test_us", "hold 83334 us in all",
                         "BRIDGE 2 x 41667 us", "budget of 83333 us",
                         "modules.safeLifecycle.watchdog_timeout_ms 250 ms"):
            self.assertIn(fragment, text)
        tests = rtegen.safe_state_tests(rtegen.safe_state_groups(config(with_us(100))))
        self.assertEqual(rtegen.safe_state_test_budget(SimpleNamespace(), []), 0)
        for cfg in (SimpleNamespace(integration=None),
                    SimpleNamespace(integration=SimpleNamespace(parameters=lambda module: {}))):
            with self.assertRaises(rtegen.RteError) as ctx:
                rtegen.safe_state_test_budget(cfg, tests)
            self.assertIn("pwm.BRIDGE.safe_state_test: the start test of the short holds the first application task",
                          str(ctx.exception))
        timeout = SimpleNamespace(integration=SimpleNamespace(parameters=lambda module: {"watchdog_timeout_ms": 400}))
        self.assertEqual(rtegen.safe_state_test_budget(timeout, tests), 133333)

    def test_group_index_follows_the_plan(self):
        generate(self.tmp, ecu(**{"AppSwc.SlowCount.SlowCount": "safety.safe_state.AUX",
                                  "AppSwc.Speed.Speed": "safety.safe_state_active.AUX"}), config())
        cpp = self.generated()
        self.assertIn("::safestate::rte::request(1U,", cpp)
        self.assertIn("::safestate::rte::read(1U, 0x3U, safeState_AUX, safeStateInForce_AUX);", cpp)

    def test_start_test_without_ports(self):
        # the start test runs from the project value alone, the application need not connect a port
        generate(self.tmp, copy.deepcopy(ECU), config())
        header, cpp = self.generated("RteConfig.h"), self.generated()
        self.assertIn("#define NX_RTE_HAS_SAFE_STATE_TEST 1", header)
        self.assertNotIn("safestate::rte", cpp)

    def test_without_a_short_nothing_changes(self):
        for cfg in (config(safestate.SafeStatePlan()), config(safestate.SafeStatePlan(
                [safestate.GroupPlan("FAN", ("freewheel",), False, None)]))):
            generate(self.tmp, copy.deepcopy(ECU), cfg)
            self.assertNotIn("SAFE_STATE", self.generated("RteConfig.h"))
            self.assertNotIn("safestate", self.generated())
        # a short without test: ports only, no test table
        generate(self.tmp, ecu(), config(plan(test=False)))
        header = self.generated("RteConfig.h")
        self.assertIn("#define NX_RTE_HAS_SAFE_STATE 1", header)
        self.assertNotIn("SAFE_STATE_TEST", header)

    def test_refused_connections(self):
        cases = [
            ({"AppSwc.SlowCount.SlowCount": "safety.safe_state.NOPE"}, "unknown PWM group NOPE"),
            ({"AppSwc.SlowCount.SlowCount": "safety.safe_state.FAN"}, "pwm.FAN allows freewheel only"),
            ({"AppSwc.Speed.Speed": "safety.safe_state.BRIDGE"},
             "input port cannot use safety.safe_state.BRIDGE (the requested state is written by a provided port)"),
            ({"AppSwc.Led.Led": "safety.safe_state_active.BRIDGE"},
             "output port cannot use safety.safe_state_active.BRIDGE"),
            ({"AppSwc.Led.Led": "safety.safe_state.BRIDGE"}, "type boolean does not fit safety.safe_state.BRIDGE"),
            ({"MotorSwc.Enable.Enable": "safety.qlf.safe_state.BRIDGE"},
             "type boolean does not fit safety.qlf.safe_state.BRIDGE"),
            ({"MotorSwc.Speed.Speed": "safety.safe_state.BRIDGE"},
             "the safe state of BRIDGE is already requested by"),
        ]
        for connections, fragment in cases:
            with self.subTest(connections=connections):
                self.expect_error(fragment, **{**CONNECTIONS, **connections})
        self.expect_error("no PWM group of the project allows an active short", config(safestate.SafeStatePlan()))

    def test_numbers_are_the_services(self):
        header = (HARD_FAULT / "include" / "hardFaultHandler" / "safeStateSelection.h").read_text(encoding="utf-8")
        for state, number in rtegen.SAFE_STATE_NUMBERS.items():
            macro = {"freewheel": "FREEWHEEL", "active_short_low": "SHORT_LOW", "active_short_high": "SHORT_HIGH"}
            self.assertRegex(header, rf"#define NX_SAFE_STATE_{macro[state]}\s+{number}U")
            self.assertEqual(safestate.STATE_BIT[state], number)


def compile_run(cmd: list, exe: Path) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd + ["-o", str(exe)], capture_output=True, text=True)
    if r.returncode != 0:
        return r
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


def build_sil(tmp: Path, mutate=None) -> subprocess.CompletedProcess:
    out = tmp / "out"
    generate(tmp, ecu(), config())
    if mutate is not None:
        cpp = out / "RteConfig.cpp"
        text = cpp.read_text(encoding="utf-8")
        assert mutate[0] in text, f"mutation does not apply any more: {mutate[0]}"
        cpp.write_text(text.replace(*mutate), encoding="utf-8")
    swc = [MULTI / "MotorSwc/generated/src", MULTI / "AppSwc/generated/src"]
    includes = [out, out / "tasks", REPO / "libs/bsw/autosarBase/include", HERE / "test" / "can_host" / "stubs",
                *swc, SYSTEMS, SYSTEMS / "test" / "host"]
    objects = []
    for src in (out / "Rte.c", swc[0] / "MotorSwc.c", swc[1] / "AppSwc.c"):
        obj = tmp / (src.stem + ".o")
        r = subprocess.run(ZIG + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                                  *[f"-I{i}" for i in includes], "-c", str(src), "-o", str(obj)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return r
        objects.append(obj)
    return compile_run(ZIG + ["c++", *CXX_FLAGS, *[f"-I{i}" for i in includes], "-isystem", str(BSP_INCLUDE),
                              str(out / "RteConfig.cpp"), str(SIL), *map(str, objects)], tmp / "sil.exe")


def build_service(tmp: Path, mutate_glue=None) -> subprocess.CompletedProcess:
    glue_dir = SYSTEMS
    if mutate_glue is not None:
        glue_dir = tmp / "systems"
        (glue_dir / "safeState").mkdir(parents=True)
        text = GLUE.read_text(encoding="utf-8")
        assert mutate_glue[0] in text, f"mutation does not apply any more: {mutate_glue[0]}"
        (glue_dir / "safeState" / "SafeStateRte.h").write_text(text.replace(*mutate_glue), encoding="utf-8")
        shutil.copy(SYSTEMS / "safeState" / "SafeStateService.h", glue_dir / "safeState" / "SafeStateService.h")
    return compile_run(ZIG + ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-DNX_SAFE_OUTPUT_SELECT=1",
                              f"-I{glue_dir}", f"-I{HARD_FAULT / 'test' / 'host' / 'selectStubs'}",
                              f"-I{HARD_FAULT / 'include'}", str(SERVICE_TEST)], tmp / "service.exe")


def checks_of(result) -> int:
    return int(result.stdout.strip().splitlines()[-1].split()[0])


class SafeStateSilTest(unittest.TestCase):
    def test_ports_with_fault_injection(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_sil(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])
        self.assertIn(" 0 failures", result.stdout)
        self.assertGreaterEqual(checks_of(result), 20)


class SafeStateServiceTest(unittest.TestCase):
    def test_glue_on_the_real_service(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_service(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-3000:])
        self.assertIn(" 0 failures", result.stdout)
        self.assertGreaterEqual(checks_of(result), 100)


class SafeStateMutation(unittest.TestCase):
    GENERATED = [
        ("qualifier always OK", "safeStateInForce_BRIDGE ? 1U : 16U", "true ? 1U : 16U"),
        ("request of another group", "::safestate::rte::request(0U,", "::safestate::rte::request(1U,"),
        ("project mask of freewheel only", "read(0U, 0x7U,", "read(0U, 0x1U,"),
    ]
    GLUE = [
        ("a test that could not run passes", "        if (result != OK)\n        {\n            // a read-back",
         "        if ((result != OK) && (result != -4))\n        {\n            // a read-back"),
        ("failed test does not block", "            Service::blockShort(test.group);\n", ""),
        ("end of the test skipped", "            result = Service::testEnd(test.group);\n", ""),
        ("in force ignores a removed state",
         "                  && ((Service::allowed(group) & projectAllowed) == projectAllowed);",
         "                  && ((Service::allowed(group) | projectAllowed) != 0U);"),
        ("refusal reported every cycle",
         "if ((result != OK) && ((result != _result[group]) || (state != _request[group])))",
         "if (result != OK)"),
        # SYS-143
        ("E3: enable not read before the short is built",
         "Probe::enableOn(test) ? ENABLE_ON : Service::testBegin(test.group, test.state)",
         "Service::testBegin(test.group, test.state)"),
        ("E3: enable switched on during the hold passes", "result = ENABLE_ON; // switched on",
         "result = OK; // switched on"),
        ("E2: a stuck clock passes", "result = CLOCK_STUCK;", "result = OK;"),
        ("E2: a stuck clock is not detected", "                    held = false;\n", ""),
        ("passed mask ignores failures", "passedGroups = testedGroups & ~failedGroups;",
         "passedGroups = testedGroups;"),
        ("E4: the test runs after every reset", "    if (start == Start::POWER_ON)\n",
         "    if (start != Start::LOST)\n"),
        ("E4: record not cleared before the tests", "        write(record, 0U);\n        uint32_t passed",
         "        uint32_t passed"),
        ("E4: record not checked", "(start == Start::WARM) && isValid(record);", "(start == Start::WARM);"),
        ("E4: record read after a start that lost the RAM", "(start == Start::WARM) && isValid(record);",
         "(start != Start::POWER_ON) && isValid(record);"),
        ("E4: a warm start keeps an untested short", "        Service::blockShort(test.group);\n        ++removed;",
         "        ++removed;"),
        ("E4: the check word ignores the passed bits",
         "return ~(RECORD_KEY ^ ((passed << 7U) | (passed >> 25U)));", "return ~RECORD_KEY + (0U * passed);"),
    ]

    def run_all(self, mutations, build, key):
        for what, original, replacement in mutations:
            with self.subTest(fault=what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    result = build(tmp, **{key: (original, replacement)})
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"fault not found: {what}\n{result.stdout[-2000:]}")
                self.assertIn("FAIL ", result.stdout, f"{what}: {(result.stdout + result.stderr)[-2000:]}")

    def test_generated_code(self):
        self.run_all(self.GENERATED, build_sil, "mutate")

    def test_glue(self):
        self.run_all(self.GLUE, build_service, "mutate_glue")


if __name__ == "__main__":
    unittest.main()
