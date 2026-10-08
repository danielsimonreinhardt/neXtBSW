"""Generates a lean AUTOSAR RTE for OpenBSW from component ARXML files and an ECU description.

Input:
  project (project.yaml, tools/nxconfig): names the ECU description and decides which board signals
  (digital inputs/outputs, analog inputs, PWM pairs) are available as connection targets
  ecu description (YAML, schema EcuDescription in tools/nxconfig/model.py, ARCH-34): components, their ARXML
  folders and the port connections, application tasks, optionally a DBC
  component data file (swc/<Swc>/<Swc>_data.m): the calibration values of the ParameterReceiver ports (RTE-03)
Output (C for the component side, C++ for the OpenBSW side):
  Rte_Type.h, Rte_<Component>.h, Rte_Internal.h, Rte.c, RteConfig.h, RteConfig.cpp,
  CanDb.h/CanDb.c (tools/dbcgen) for the CAN messages used,
  Rte_Qlf.h and Rte_Qlf.m: signal qualifier constants for C and MATLAB (COM-03),
  EcuStateConfig.h: parameters of the ECU state management (project.yaml modules.ecuState, checked by tools/nxconfig,
  defaults from libs/bsw/ecuState/module.yaml)
  tasks/async/Config.h, RteTasks.h: task list (BSW tasks of ecu/asyncCoreConfiguration/tasks.yaml plus the
  application tasks of the ECU description, section tasks:)
  tasks/BswTasks.h: stacks of the BSW, idle and timer task objects and which BSW task runs on its own (PLAT-26:
  project.yaml bsw_tasks, checked by tools/nxconfig/bswtasks.py, defaults from ecu/configuration/taskStacks.yaml);
  a BSW task merged into another one (run_in) gets its context as an alias in tasks/async/Config.h

Scope: sender-receiver communication with explicit access (Rte_Read / Rte_Write) and implicit access (Rte_IRead /
Rte_IWrite / Rte_IWriteRef, RTE-09), timing events and init runnables.
Implicit access (RTE-09): a runnable whose ARXML lists a data element under DATA-READ-ACCESSS or DATA-WRITE-ACCESSS
works on a copy of its own (Rte_Imp_<Swc>_<Runnable>_<Port>_<Element>). The RTE calls the runnable through a wrapper
(RteConfig.cpp) that loads every copy before the runnable - from the task copy, the buffer or, for a port whose
runnables run in several tasks, the published value under one lock - and publishes the written copies after it (again
under at most one lock). The values a runnable reads stay the same for its whole run, what it writes becomes visible
when it ends. A write copy starts with the current value, so a runnable that does not write (or writes a record
partly through Rte_IWriteRef) publishes what it found. Without implicit access points the generated code is unchanged.
Data types (RTE-04): platform types, typedefs (TYPE_REFERENCE), fixed-size arrays, records (STRUCTURE, Simulink
buses, nested), enumerations (TEXTTABLE compu method, literals as macros in Rte_Type.h) and fixed point (LINEAR compu
method of the application data type). Arrays are passed by element pointer, records by pointer (write: const), as
Embedded Coder calls them; the RTE copies arrays with memcpy. Records, arrays and fixed point connect only component
to component (or rte/rte.init), sender and receiver must agree in layout, scaling and literals; enumerations may use
BSW targets of their platform type. Init values come from the ARXML value specifications, physical values of
application types are converted to stored ones. The signal table lists records and arrays element by element;
RTE-29: its fixed point rows get the rational conversion, unit and console decimals in rte::SIGNAL_SCALINGS (only
generated when there is one, so rte::Signal and an ECU without fixed point types stay as they were).
Array ports of C components (RTE-25): swc.yaml `dimension` (a number, or a name of the init data resolved by
tools/swc/initdata.py) gives rt_Array_<type>_<n>; a connection needs the same dimension on both sides, an array at a
CAN signal is refused. Runnables run in one or more application tasks
(RTE-02). Data consistency between tasks: a buffer accessed from one task only is used directly. A buffer accessed
from several tasks keeps its published value and one copy per task; a task reads its copies at its start and
publishes the copies it writes at its end, both under a lock (copy at the task boundary). A port whose runnables run
in several tasks accesses the published value directly under a lock (atomic hand-over, no snapshot). Received CAN
frames are handed over from the CAN task under a lock.

BSW service ports (RTE-05): required ports read BSW services through targets named by service, not by device:
bsw.power.state|ok|supply_mv|qlf (role power_supply), bsw.power.* needs a provider that publishes, bsw.reset.reason|
watchdog|power_on|qlf (start reasons of the role power_supply), bsw.time.ms|us (system timer). The RTE calls the
hand-over of ecu/rte/services (bswService/BswServiceRte.h) once per service and task cycle (BSW_SERVICES).

Mode management (RTE-08): required mode ports (MODE-SWITCH-INTERFACE) are connected to the ECU mode manager ecu.mode
(the states of libs/bsw/ecuState); the component reads the mode with Rte_Mode_<Port>_<Group>() in the values of its
mode declaration group. Runnables with SWC-MODE-SWITCH-EVENTs (on entry, exit, transition) are assigned to a task in
tasks[].runnables and run at the start of the first cycle of that task after a mode change, before the periodic ones
(exit, transition, entry). Every such task keeps its own view of the mode; a port read in several tasks reads the
mode manager directly. Not rows of rte::RUNNABLES. Without mode ports the generated code is unchanged.

Device values (RTE-18): <prefix>.<Instance>.<value> of every module whose manifest describes values for the
application (module.yaml rte: capture, hall, gd and any new driver) - names, types, units and the qualifier come
from the manifest, not from tables here; a new driver uses the generic reader contract (a reading struct and one
::integration::rte::<read>_<Instance>(reading) per instance). Inputs only.

Board targets (RTE-11): dio/adc/pwm and device value targets name the board entry exactly as board.yaml (or
project.yaml adc_internal) spells it, e.g. adc.mv.12VBAT_CURRENT. The C identifier of the generated board headers
(c_name, SIG_12VBAT_CURRENT for a leading digit) is derived internally and never written in the ECU description.
The internal converter inputs of project.yaml adc_internal (e.g. BANDGAP) are adc.mv/adc.raw targets (mV at the
converter, no sensor plan, so no adc.phys/adc.qlf).

Non-volatile blocks (NVM-02): target nvm.<Block>. Exactly one provided port writes a block (its init value is the
default), required ports read it (same type); records and arrays are allowed. The RTE keeps an image per block
(rte::NVM_BLOCKS, NX_RTE_HAS_NVM): nvmRestore() loads the stored values into buffer and task copies before the init
runnables, a changed value goes to the image at the end of the writer's task cycle and after the init runnables.
Layout in name order, two places per block (QM: record header, data, CRC-16; ASIL: two copies with CRC-32 and write
counter), record id from name, type and format; checked against the derivative's emulated EEPROM window (memory region
of kind eeprom_ram). NVM-18: a block is defined in project.yaml nvm.blocks.<Block> (type, length, integrity); the
connections are checked against the definition, a connected block without one gets a warning with the proposed
definition. A required port on nvm.qlf.<Block> reads where the value came from at the start (QLF_OK stored,
QLF_NOT_AVAILABLE never written or not read yet (RTE-28), QLF_ERROR_CRC lost and default, QLF_ERROR no memory). The NvM core is
libs/bsw/nvm (storage neutral, OpenBSW eeprom::IEepromDriver); the integration still uses its NVM-02 copy in
ecu/application/src/systems/nvm until it is switched over (libs/bsw/nvm/doc/nvm.md).

Calibration parameters (RTE-03): ParameterReceiver ports (PARAMETER-INTERFACE) of a component, scalar, fixed-size
array or fixed point (LINEAR COMPU-METHOD). Their values come only from <Swc>_data.m next to the model
(tools/swc/mdata.py), never from the ARXML or the ECU description; the RTE keeps them in RAM
(Rte_Prm_<Swc>_<Port>_<Element>_data, not const), the component reads them through Rte_Prm_<Port>_<Element>() and
rte::PARAMETERS[] describes them (address, type, element count, conversion, unit) for a calibration protocol (XCP,
TOOL-01). They are read only by components, so they need no task copies; a calibration write of more than one word
is not atomic towards a running reader (TOOL-01 decides how writes are placed).

Calibration objects (RTE-26, stage K1 of doc/architecture/kalibrierung.md): the swc.yaml `parameters:` of a
component (C and Simulink alike) define kind (value, array, curve, map, shared axis), axes, unit, engineering
limits, safety class, description and value table. For a Simulink component the definition is compared with the
ARXML (element type, number of values, scaling); the RTE follows the ARXML only where both agree. Own breakpoints
of a curve/map are the element <Name>_X/<Name>_Y and live with the values in one record (x, y, v), so a tool sees
them in front of the values (A2L STD_AXIS). rte::PARAMETERS[] carries what the run time needs (stored limits, axis
rows, kind, safety class); texts (description, value table) go to RteCalibration.json for tools/a2lgen.

Calibration dataset (TOOL-145, stage K2): a project with a folder calibration/ next to its project.yaml (format
nextbsw-cal 1, tools/calgen/caldata.py) takes the values of its calibration parameters from there instead of the
<Swc>_data.m defaults - base, or the variant dataset.yaml build_variant names (base plus delta). The dataset must be
complete, every value is converted and checked as a default value is (type, grid, stored limits, ascending axes), and
its layout identifier must be the one of this software; every finding is an error before anything is written.
RteCalibration.json then names the dataset, rte_components.cmake lists its files as configure inputs. Without the
folder the generated files stay byte for byte as before.

Hand-written C components (RTE-20): a component whose swc.yaml says `implementation: c` has no ARXML; rtegen reads
runnables, ports and calibration parameters from the swc.yaml (tools/swc/swc_yaml.py) into the same Component model
parse_component() builds from the ARXML (load_component), so connections, tasks, copies, parameters and
Rte_<Swc>.h follow the same rules and templates. The C function of a periodic runnable is <Swc>_<Runnable> (Embedded
Coder names it <Runnable>); two runnables with one C function are an error. Sources: <path>/src/*.c, listed apart in
rte_components.cmake (RTE_C_COMPONENT_SOURCE_DIRS, only when there is a C component) and built as application code
by ecu/application; tools/swc/swc_skeleton.py compares them with the swc.yaml (warnings). Without swc.yaml or with
`implementation: simulink` nothing changes.

Diagnostic jobs (COM-28, COM-32): the functions of RteDiag.h (rte::diag, list from tools/diaggen/rteapi.py) read
provided ports and, for an identifier element with `source: {bsw: ...}` in the notation of the BSW targets
(ecu.state, ecu.wakeup[.<source>], adc.mv|raw|phys|qlf.<CHANNEL>, bsw.<service>.<value>), the value of the basic
software itself - outside the RTE lock, callable from the UDS task and from the fault memory (snapshots). See
DIAG_BSW_KINDS; diag_bsw_values(config) lists the names a project offers (Studio). COM-45: bsw.rte.* and
bsw.calibration.* are status values of the application's integration for the diagnosis only (BSW_STATUS). NVM-05:
bsw.stats.*, bsw.stats_mcu.<cause>, bsw.stats_supply.<cause> - operating time and reset statistics (same way).
SYS-196: bsw.safeoff.* - take-backs of foreign safe_off levels (read from the integration of the family port).

Age supervision (SYS-32, gap G3 of doc/safety/concept.md): a required port on age.qlf.<Swc>.<Port>.<Element> reads
whether that provided port was written (Rte_Write) within the period of its one periodic writing runnable: QLF_OK,
QLF_ERROR_TIMEOUT when a write is missing (also when it never came), QLF_DEBOUNCE before the first write. Counted at
the end of every cycle of the writer's task (writeOutputs), handed to readers in other tasks like any value. A DTC
samples it with diag.yaml event.bsw age.qlf.<Swc>.<Port>.<Element>. See AGE_KIND.
"""

import argparse
import copy
import hashlib
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from fractions import Fraction
from functools import reduce
from math import gcd
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "dbcgen"))
sys.path.insert(0, str(HERE.parent))
import dbcgen  # noqa: E402
sys.path.insert(0, str(HERE.parent / "bswgen"))
import bswgen as nxbswgen  # noqa: E402  (COM-08: receive buffer split of the FlexCAN driver)
sys.path.insert(0, str(HERE.parent / "swc"))
import mdata  # noqa: E402  (RTE-03: calibration values of a component, swc/<Swc>/<Swc>_data.m)
import swc_yaml  # noqa: E402  (RTE-20: description of a hand-written C component, swc/<Swc>/swc.yaml)
import swc_skeleton  # noqa: E402  (RTE-20: runnable functions of a C component against its swc.yaml)
import modelhash  # noqa: E402  (TOOL-169: inputs of the last code generation, <Swc>/generated/model.sha256)
sys.path.insert(0, str(HERE.parent / "calgen"))
import caldata  # noqa: E402  (TOOL-145: calibration dataset nextbsw-cal 1 of the project, tools/calgen)
sys.path.insert(0, str(HERE.parent / "diaggen"))
import rteapi  # noqa: E402  (COM-28: functions of the RTE for the diagnostic jobs, shared with tools/diaggen)
import nxconfig  # noqa: E402
import nxconfig.config  # noqa: E402
from nxconfig import checks as nxchecks  # noqa: E402
from nxconfig import model as nxmodel  # noqa: E402  (ARCH-34: schema of the ECU description, EcuDescription)
from nxconfig import diagdesc as nxdiagdesc  # noqa: E402  (NVM-03: severity byte of a DTC)
from nxconfig import bswtasks as nxbswtasks  # noqa: E402  (PLAT-26: stacks and merges of the BSW tasks)
from nxconfig import identity as nxidentity  # noqa: E402  (PLAT-79: configuration identity, manifest)
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)
from pydantic import ValidationError  # noqa: E402

NS = {"ar": "http://autosar.org/schema/r4.0"}

PLATFORM_TYPES = {
    "boolean": "BOOLEAN",
    "uint8": "UINT8",
    "sint8": "SINT8",
    "uint16": "UINT16",
    "sint16": "SINT16",
    "uint32": "UINT32",
    "sint32": "SINT32",
    "float32": "FLOAT32",
    "float64": "FLOAT64",
}

# Size and alignment of the platform types (32 bit ARM EABI, D11), for the memory of the implicit copies (RTE-09)
PLATFORM_SIZES = {"boolean": 1, "uint8": 1, "sint8": 1, "uint16": 2, "sint16": 2, "uint32": 4, "sint32": 4,
                  "float32": 4, "uint64": 8, "sint64": 8, "float64": 8}

# Access points of a runnable (SWS RTE, RUNNABLE-ENTITY): explicit ones give Rte_Read/Rte_Write, implicit ones
# (DATA-READ-ACCESSS / DATA-WRITE-ACCESSS, RTE-09) Rte_IRead and Rte_IWrite/Rte_IWriteRef with a copy per runnable
EXPLICIT_ACCESS_GROUPS = {"DATA-RECEIVE-POINT-BY-ARGUMENTS": "r", "DATA-SEND-POINTS": "w"}
IMPLICIT_ACCESS_GROUPS = {"DATA-READ-ACCESSS": "r", "DATA-WRITE-ACCESSS": "w"}

# BSW signal kinds: direction seen from the RTE and the platform types they carry
BSW_KINDS = {
    "dio.in": {"direction": "in", "types": {"boolean"}},
    "dio.out": {"direction": "out", "types": {"boolean"}},
    "adc.mv": {"direction": "in", "types": {"uint32"}},
    "adc.raw": {"direction": "in", "types": {"uint16", "uint32"}},
    # IO-18: signed physical value of a board sensor (mV, mA, ohm, 0.1 degC; board adc[].sensor, project adc_loads)
    # and its quality (QLF_OK, QLF_ERROR_RANGE at the ends, QLF_ERROR when the conversion failed)
    "adc.phys": {"direction": "in", "types": {"sint32"}},
    "adc.qlf": {"direction": "in", "types": {"uint8", "uint16", "uint32"}},
    # duty of a complementary PWM pair in 0.01 % (bsp OutputPwm), values above 10000 saturate to 100 %
    "pwm": {"direction": "out", "types": {"uint16", "uint32"}},
}
PWM_MAX_DUTY = 10000  # OutputPwm::MAX_DUTY_CYCLE
ADC_SENSOR_KINDS = ("adc.phys", "adc.qlf")
# quality codes of the generated conversion (bsp/adc/analogSensorConfiguration.h, tools/nxconfig/sensor.py) ->
# qualifier values; test_rtegen.py compares them
ADC_SENSOR_QUALITY = {"OK": "QUALITY_OK", "ERROR_RANGE": "QUALITY_RANGE"}

# RTE-18: values of module instances (device values). Which values a driver offers the application - name, types,
# unit, meaning, which one is the qualifier - stands in its manifest (module.yaml rte, tools/nxconfig RteSpec), not
# in this generator: the targets are <prefix>.<Instance>.<value>, the instances the board devices of the module the
# project integrates (or, for the input capture, the board input_capture entries it uses). A new driver becomes
# connectable without a change here: its manifest names the generic reader contract (access reading, a reading
# struct and ::integration::rte::<read>_<Instance>(reading)). What stays here is the *access code* of the modules
# that existed before (access input_capture, hall, gate_driver): how the RTE reads them is specific (one update per
# cycle of the capture unit, the pin interrupt snapshot, the health code with the bus verdict of SYS-29), and each
# names the members its driver API has (DEVICE_ACCESS_FIELDS; a manifest value that maps to another member is
# refused). The names of the existing targets (capture.*, hall.*, gd.*) come from those manifests unchanged.
DEVICE_SOURCES = "device.sources"          # key of board_signals(): prefix -> DeviceSource of the project
DEVICE_ACCESS_FIELDS = {
    # bsp/capture/InputCapture.h Measurement (IO-03)
    "input_capture": ("frequency_mhz", "period_us", "duty_permille", "quality"),
    # hall/HallSensors.h Reading (IO-07)
    "hall": ("state", "sector", "period_us", "mech_period_us", "speed_rpm", "direction", "quality"),
    # Integration.cpp readGateDriver_<device>(faults, enabled, health) and the qualifier derived from it (IO-25)
    "gate_driver": ("faults", "enabled", "quality"),
}
# Quality values the capture driver reports (bsp/capture/InputCapture.h QUALITY_*); they have to be the qualifier
# values of COM-03, test_rtegen.py compares the two lists.
CAPTURE_QUALITY = {"DEBOUNCE": "QUALITY_DEBOUNCE", "OK": "QUALITY_OK", "ERROR_TIMEOUT": "QUALITY_TIMEOUT",
                   "ERROR": "QUALITY_ERROR"}
# Quality values the Hall driver reports (hall/HallSensors.h QUALITY_*); they have to be the qualifier values of
# COM-03, test_rtegen.py compares the two lists (same requirement as for the input capture of IO-03).
HALL_QUALITY = dict(CAPTURE_QUALITY)
# every <prefix>.<value> kind of a device value seen so far (IO_INPUT_KINDS, read by tools/schedana as well)
_DEVICE_KINDS: set[str] = set()


@dataclass(eq=False)
class DeviceSource:
    """RTE-18: the values one module offers the application, from its manifest, and the instances of the project."""

    module: str
    prefix: str
    access: str                  # reading (generic contract) or an access code of DEVICE_ACCESS_FIELDS
    instance: str                # word for the instance in messages (Device, Sensor, Channel)
    noun: str                    # what one instance is (gate driver)
    which: str                   # which instances the project has, for messages
    input_note: str              # why a provided port cannot use a value
    value_note: str | None       # appended to "unknown value"
    values: dict                 # value -> {"types": set, "field", "unit", "description", "qualifier", "without"}
    names: set                   # instances of the project, spelled as in the board profile
    include: str | None = None   # access reading: header of the reading struct
    reading: str | None = None   # access reading: C++ type of one reading
    read: str | None = None      # access reading: ::integration::rte::<read>_<Instance>(reading)

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(f"{self.prefix}.{value}" for value in self.values)

    @property
    def qualifier(self) -> str | None:
        return next((name for name, spec in self.values.items() if spec["qualifier"]), None)

    def field_of(self, kind: str) -> str:
        """Member of the reading behind a BSW kind <prefix>.<value>."""
        return self.values[kind.split(".", 1)[1]]["field"]


def device_source(manifest, names: set, file=None) -> DeviceSource:
    """DeviceSource of one module manifest with rte (tools/nxconfig RteSpec), for the instances `names`."""
    rte = manifest.rte
    values = {name: {"types": set(spec.types), "field": spec.field or name, "unit": spec.unit,
                     "description": spec.description, "qualifier": spec.qualifier,
                     "without": spec.without or "a current value from a held one"}
              for name, spec in rte.values.items()}
    fields = DEVICE_ACCESS_FIELDS.get(rte.access)
    if fields is not None:
        wrong = [f"{name} ({spec['field']})" for name, spec in values.items() if spec["field"] not in fields]
        if wrong:
            where = f"{file}: " if file else ""
            raise RteError(f"{where}rte.values: {', '.join(wrong)} is no member the access code {rte.access} reads "
                           f"({', '.join(fields)})")
    if rte.instances == "input_capture":
        which, noun = "use.input_capture", "input_capture channel"
    else:
        which, noun = f"board devices with module {manifest.module} the project uses", f"{manifest.module} instance"
    return DeviceSource(manifest.module, manifest.rte_prefix, rte.access, rte.instance, rte.noun or noun,
                        rte.which or which, rte.input_note or "a measured value is an input", rte.value_note,
                        values, set(names), rte.include, rte.reading, rte.read)


def device_sources(config: "nxconfig.Config") -> dict[str, DeviceSource]:
    """RTE-18: prefix -> DeviceSource of every module of the project with values for the RTE (module.yaml rte), in
    manifest order. The instances are the board devices of the module the project integrates (checks.Integration)
    or the board input_capture entries the project uses."""
    modules = getattr(config, "modules", None)
    integration = getattr(config, "integration", None)
    sources: dict[str, DeviceSource] = {}
    for name, manifest in (getattr(modules, "manifests", None) or {}).items():
        if getattr(manifest, "rte", None) is None:
            continue
        if manifest.rte.instances == "input_capture":
            names = {c.name for c in config.selected("input_capture")}
        else:
            names = {d.name for d in (integration.instances(name) if integration is not None else [])}
        source = device_source(manifest, names, (getattr(modules, "files", None) or {}).get(name))
        sources[source.prefix] = source
        _DEVICE_KINDS.update(source.kinds)
    return sources

# Pin events (IO-53/IO-54, doc/architecture/pin-interrupts.md variant A): counter of accepted edges, time stamp of
# the last one and quality of a digital input with project pin_events.<input>, counted in the pin interrupt by
# libs/bsp/bspPinEvent. Targets are pinevent.<Input>.<value>, the inputs the events nxconfig accepted
# (Config.pin_events). PinEvents::read() takes a consistent lock-free copy, so any task may read; when an interrupt
# wrote during every attempt it returns false and the RTE keeps the values of the cycle before. The project decides
# the events, not the connections: the RTE starts and cycles them (bsp/pinIrq/PinIrqConfig.h) whenever the project
# has pin_events, connected or not (console "pinevent").
PINEVENT_PREFIX = "pinevent"
PINEVENT_VALUES = {
    "count": {"types": {"uint32"}, "field": "count", "unit": "accepted edges, wraps from 0xFFFFFFFF to 0"},
    "time_us": {"types": {"uint32"}, "field": "timeUs",
                "unit": "microseconds, system time of the last accepted edge (valid with QLF_OK)"},
    "qlf": {"types": {"uint8", "uint16", "uint32"}, "field": "quality", "unit": "QLF_* of Rte_Qlf.h"},
}
PINEVENT_KINDS = tuple(f"{PINEVENT_PREFIX}.{value}" for value in PINEVENT_VALUES)
# Quality values of the module (pinEvent/PinEvent.h QUALITY_*); they have to be the qualifier values of COM-03,
# test_rtegen.py compares the two lists. QLF_DEBOUNCE until the first accepted edge (the module does not use
# QLF_NOT_AVAILABLE of RTE-28), QLF_ERROR while the rate limit keeps the input disarmed.
PINEVENT_QUALITY = {"DEBOUNCE": "QUALITY_DEBOUNCE", "OK": "QUALITY_OK", "ERROR": "QUALITY_ERROR"}

# Gate drivers (IO-25, access gate_driver of module.yaml rte): the family port integration defines one reader per
# device (Integration.cpp, readGateDriver_<device>) with the health code of GD_HEALTH, which the RTE turns into the
# qualifier values of COM-03. The driver's own pins are no dio.out targets (see board_signals).
# health code of the reader -> qualifier: not started yet, running (configured, DRV_EN off or on), failed
# (communication or device error, retried by the driver)
GD_HEALTH = {0: "QLF_DEBOUNCE", 1: "QLF_OK", 2: "QLF_ERROR"}

# SYS-29 (gap G5 of doc/safety/concept.md, finding F5 of doc/safety/fmea.md): qualifier path of device values. The
# qualifier of a device value (gd.<Device>.qlf today, the values of the device packages of SYS-28 later) is the
# driver's own health, and on top of it the safe supervisor's verdict on the bus the device hangs on: while the
# supervisor regards that bus as lost (communication faults of several devices on it within its window), the
# qualifier is QLF_ERROR, even if the driver still believes its last reading. The generated RTE reads the verdict
# through safety::device::rte::busLost(<bus id>) (ecu/safety/safeSupervisor/src/safeSupervisor/DeviceReportRte.cpp).
# The bus id is the one of the context word of the device reports (safeSupervisor/DeviceReport.h; test_rtegen.py
# compares the values): kind in bits 7..5, in bits 4..0 the position of the bus among the buses of that kind the
# project uses, in board order - derived from the configuration, never written by hand. A device on no bus or on
# more than one bus gets no bus verdict (DEVICE_NO_BUS), its qualifier is the driver's health alone.
DEVICE_BUS_KINDS = {"spi": 1, "i2c": 2, "uart": 3, "can": 4, "other": 5}
DEVICE_BUS_INDEX_MAX = 31
DEVICE_NO_BUS = 0xFF

# CAN signal kinds (can.rx.<Message>.<Signal>, can.tx.<Message>.<Signal>), checked against the DBC
CAN_KINDS = {"can.rx": "in", "can.tx": "out"}

# Signal qualifier of a received message (COM-03, doc/architecture/signal-qualifier.md): one value per message,
# target can.qlf.<Message>, computed by the RTE from bus state, timeout and the AUTOSAR E2E profiles.
QLF_KIND = "can.qlf"
QLF_TYPES = {"uint8", "uint16", "uint32"}  # the decided type is uint8, wider ports also carry the values
# values of the old MBDT project (SYSCONST.QLF_*), kept so that model logic like Qlf == QLF_OK still fits
QLF_VALUES = [
    ("DEBOUNCE", 0, "not valid yet: after the start until the first valid message, and while an error debounces"),
    ("OK", 1, "all checks passed"),
    ("ERROR_ALIVE", 2, "E2E counter repeated or out of sequence, longer than debounce_ms"),
    ("ERROR_CRC", 4, "E2E checksum or data id wrong, longer than debounce_ms"),
    ("ERROR_TIMEOUT", 8, "no message within timeout_ms"),
    ("ERROR", 16, "bus error (transceiver not ready, bus off): at once, for every received message of the bus"),
    ("ERROR_RANGE", 32, "value outside its valid range, longer than debounce_ms (SensorQualifier of a component, RTE-07)"),
    ("NOT_AVAILABLE", 64, "no source: required port not connected, NvM block not read or never written (RTE-28)"),
]
QLF = {name: value for name, value, _ in QLF_VALUES}
ON_ERROR = ("hold", "init")  # signal values while the qualifier reports an error

# RTE-27: a required port without any connection does not stop the configuration. It reads a defined init value
# from a constant buffer of its own (the init value of the port from the ARXML or swc.yaml, 0 without one), and the
# generator reports a warning with file and YAML path. A qualifier port (QLF_TYPES, name ending in Qlf/Qualifier)
# reads QLF_NOT_AVAILABLE (RTE-28, decision of 2026-09-29; RTE-27 used QLF_ERROR_TIMEOUT) instead of its init value:
# there is no source, so it must not look like QLF_OK, like QLF_DEBOUNCE (which a receiver waits out) or like a
# real fault of a source (ERROR_TIMEOUT of a message that stopped) - the component tells "not wired" apart from
# "wired and failing". Exceptions, still errors: a mode port (build_modes: without the ECU mode there is no
# defined mode and no switch event) and a required port of a safety-relevant component (component_safety != qm) -
# an init value in place of a safety input would silently replace a safety function; such a port is connected
# explicitly (a source, or rte.init as a documented decision). The same buffer serves RTE-23 later (ports fed by a
# disabled part): Buffer.init_only names why a port reads only its init value.
INIT_UNCONNECTED = "unconnected"
QLF_NO_SOURCE = "NOT_AVAILABLE"
QLF_PORT_SUFFIXES = ("qlf", "qualifier")

# RTE-23 (user test 2026-09-26): components (components[].enabled) and runnables (tasks[].runnables[].enabled) can be
# switched off without taking them out of the ECU description. A disabled part is not called: its runnables are in
# no task, the init runnable of a disabled component does not run. The component stays compiled and linked - the
# generated RTE keeps one shape (Rte_<Swc>.h, port buffers, calibration parameters and their A2L layout, measurement
# addresses), so switching a part on and off changes no calibration dataset and needs no other file; the target links
# with --gc-sections, so the runnable code nobody calls any more is dropped from the image.
# A provided port whose every writing runnable is disabled (disabled_source) delivers a constant value: its own init
# value (ARXML INIT-VALUE), else components[].disabled_values of its component, else the init value of the reading
# port - never a silent 0, a port without any of them is an error with its YAML path. Each required port of another
# component that reads it gets a const buffer of its own (Buffer.init_only = INIT_DISABLED), a qualifier port reads
# QLF_NOT_AVAILABLE like an unconnected one (RTE-28), so a receiver tells "source switched off" from real data.
# BSW targets of such a port (dio.out, pwm, can.tx, ecu.sleep_request, dem.*, did/routine) get the same value; an
# nvm.<Block> writer cannot be disabled (the default would overwrite the stored value).
# Safety: a component with component_safety != qm and its runnables cannot be disabled at all, and a part that feeds
# a required port of such a component neither - see disable_safety_error().
INIT_DISABLED = "disabled"

# SYS-32 (gap G3 of doc/safety/concept.md, ASG-03/ASG-05): age supervision of a value between components. SM-05
# supervises that the runnable runs in time, not that the value it should deliver is new - a device package whose
# device stops answering keeps running and simply stops writing. A required port on age.qlf.<Swc>.<Port>.<Element>
# switches on the supervision of that provided port and reads its qualifier (uint8/16/32, QLF_* of Rte_Qlf.h):
#   QLF_DEBOUNCE        from the start until the first write, as long as the deadline has not passed yet
#   QLF_OK              written within the deadline
#   QLF_ERROR_TIMEOUT   not written for one period of its writing runnable (also: never written since the start)
# The deadline is the period of the one periodic runnable that writes the port explicitly (Rte_Write), counted in
# cycles of its task at the end of each cycle - no new configuration value, like the deadlines of SM-05. The value
# itself is held (on_error hold of COM-03); recovery with the next write (Q7 of the signal qualifier). Several
# required ports may read the same qualifier; a DTC samples it with diag.yaml event.bsw age.qlf.<Swc>.<Port>.<Element>
# (or event.port of a reading port, result qualifier derived), which is the optional event.
AGE_PREFIX = "age"
AGE_KIND = "age.qlf"


@dataclass
class AgeSupervision:
    """SYS-32: age supervision of one provided port element (target age.qlf.<writer>)."""
    writer: str          # Component.Port.Element of the supervised provided port
    runnable: str        # Component.Runnable writing it periodically
    period_ms: int       # its period: the deadline
    task: "Task"         # its task: the age is counted at the end of every cycle of this task
    limit: int           # deadline in cycles of the task (period_ms / task.period_ms)
    index: int = 0       # row in the age states of RteConfig.cpp

    @property
    def flag(self) -> str:
        """C variable set by Rte_Write of the writer (Rte.c), taken back by the age check at the end of the cycle."""
        return "Rte_Age_" + self.writer.replace(".", "_")


# AUTOSAR E2E profiles of libs/bsw/e2e (COM-10). data_id_bits: width of the configured data id,
# header: fixed offsets the profile requires (P02: CRC in byte 0, counter in the low nibble of byte 1).
E2E_PROFILES = {
    "P01": {"modes": ("BOTH", "ALT", "LOW", "NIBBLE"), "data_id_bits": 16, "counter_modulo": 15,
            "max_bits": 256, "header": None},
    "P02": {"modes": (), "data_id_bits": 8, "counter_modulo": 16,
            "max_bits": 2048, "header": {"crc": 0, "counter": 8}},
    "P11": {"modes": ("BOTH", "NIBBLE"), "data_id_bits": 16, "counter_modulo": 15,
            "max_bits": 256, "header": None},
}
E2E_DATA_ID_LIST_SIZE = 16  # P02: one data id per counter value
# SyncCounterInit of P01/P02 (COM-14). With 0 a counter out of sequence leaves the reference (LastValidCounter)
# where it was while every Check call widens MaxDeltaCounter by 1: a stalled counter then fits the window after
# a few frames and comes back as OKSOMELOST (E2E_P_OK), which healed the qualifier for one cycle (board capture
# 2026-09-16). With 1 the out-of-sequence counter becomes the new reference, a stalled counter stays REPEATED,
# and one following frame in sequence (SYNC, E2E_P_OK with profileBehavior) resynchronises - as P11 does.
E2E_SYNC_COUNTER_INIT = 1

# ECU state management (libs/bsw/ecuState, SYS-01): targets and their direction/types
ECU_STATE_MODULE = "ecuState"
ECU_KINDS = {
    "ecu.state": {"direction": "in", "types": {"uint8", "uint16", "uint32"}},   # ecustate::State as number
    "ecu.wakeup": {"direction": "in", "types": {"uint32"}},                     # start reasons, bit mask
    "ecu.wakeup.": {"direction": "in", "types": {"boolean"}},                   # one start reason
    "ecu.sleep_request": {"direction": "out", "types": {"boolean"}},            # all connected ports: AND
}
# start reasons with a fixed bit (ecuState/EcuState.h, namespace wakeup); CAN buses and local inputs follow
WAKEUP_BITS = {"POWER": 0, "RESET": 1, "WATCHDOG": 2, "SUPPLY": 3, "TIMER": 4, "UNKNOWN": 7}
WAKEUP_CAN_SHIFT, WAKEUP_LOCAL_SHIFT, WAKEUP_GROUP_SIZE = 8, 16, 8
# SYS-02: parameters that name wake-up sources instead of carrying a value; they become one mask
# (NX_ECU_STATE_WAKEUP_SOURCES) and are generated as strings for the log, not as macro values
WAKEUP_LIST_PARAMETERS = ("wakeup_can", "wakeup_local")

# SYS-117: the degraded reaction SD of the safe supervisor (doc/safety/partner-and-degraded.md section 4.1, step 3).
# Required ports only; the values come from safety::degraded::rte::readGroups (ecu/safety/safeSupervisor,
# DegradedRte.cpp), one reading per task cycle, read only:
#   safety.degraded            bit mask, bit k = safety.output_groups[k] of project.yaml (degraded until power cycle)
#   safety.degraded.<GROUP>    1 while that group is degraded
#   safety.qlf.degraded        QLF_OK, or QLF_ERROR if the supervisor's latch is implausible (then every bit reads 1)
# A DTC puts a degradation into the error memory by sampling such a port (diag.yaml dtcs[].event.port, result boolean
# or a limit on the mask).
SAFETY_PREFIX = "safety"
SAFETY_DEGRADED = "safety.degraded"
SAFETY_DEGRADED_GROUP = "safety.degraded."
SAFETY_DEGRADED_QLF = "safety.qlf.degraded"
SAFETY_KINDS = {SAFETY_DEGRADED: {"uint8", "uint16", "uint32"}, SAFETY_DEGRADED_GROUP: {"boolean"},
                SAFETY_DEGRADED_QLF: QLF_TYPES}
SAFETY_GROUPS = "safety.groups"   # key of board_signals: safe-output group -> bit
# SYS-136: the hardware shut-off path of a PWM group (SYS-130/SYS-86, safety review C7): what the family port reported
# to the safe supervisor (S1 held until reset), read through safety::hwShutoff::read (ecu/safety/safeSupervisor,
# HwShutoff.h), one reading per task cycle, read only:
#   safety.hw_shutoff          uint32: the reported context word (bit 0 fault latched by the timer, bits 23..8 findings
#                              of the path, bits 31..24 timer index - the family port's layout), 0 = nothing reported;
#                              boolean: 1 while anything is reported
#   safety.qlf.hw_shutoff      QLF_OK, or QLF_ERROR if the supervisor's record is implausible (the word then reads
#                              0xFFFFFFFF, every bit reported - fail-safe)
# A project without a hardware shut-off path reads 0 (nothing reported). The DTC of it is the BSW event
# safety.hw_shutoff (DEM_BSW_REPORTERS), reported by RteSystem.
SAFETY_HW_SHUTOFF = "safety.hw_shutoff"
SAFETY_HW_SHUTOFF_QLF = "safety.qlf.hw_shutoff"
SAFETY_KINDS[SAFETY_HW_SHUTOFF] = {"boolean", "uint32"}
SAFETY_KINDS[SAFETY_HW_SHUTOFF_QLF] = QLF_TYPES
SAFETY_DEGRADED_TARGETS = (SAFETY_DEGRADED, SAFETY_DEGRADED_GROUP, SAFETY_DEGRADED_QLF)
SAFETY_HW_SHUTOFF_TARGETS = (SAFETY_HW_SHUTOFF, SAFETY_HW_SHUTOFF_QLF)
# SYS-141 (safety review SYS-116/SYS-30 K8, follow-up C6; doc/safety/partner-and-degraded.md 5.2 and 5.10): the
# run-time selection of the safe state of a PWM group whose project allows an active short (pwm.<group>.safe_states,
# tools/nxconfig/safestate.py) through the hand-over safestate::rte of ecu/application (systems/safeState/
# SafeStateRte.h), which hands it to the selection service of the family port (SYS-116, hardFaultHandler/
# safeStateSelection.h) - the only writer of the selection words the switch-off paths read:
#   safety.safe_state.<GROUP>         provided port (uint8/16/32): the requested state, 0 freewheel, 1 active_short_low,
#                                     2 active_short_high; handed over at the end of every cycle of the writer's task,
#                                     one writer per group; a refused request leaves the selection as it is (S3)
#   safety.safe_state_active.<GROUP>  required port (uint8/16/32): the state the switch-off paths would apply now
#   safety.qlf.safe_state.<GROUP>     required port: QLF_OK while the requested state is in force and every state the
#                                     project allows for the group is still available, QLF_ERROR otherwise (request
#                                     refused, shorts removed until power-on by a bridge fault or a failed start test)
# Only for a group that allows a short: the service exists only then (NX_SAFE_OUTPUT_SELECT). The start test of the
# short (pwm.<group>.safe_state_test) runs in RteSystem::init() from the derived table rte::SAFE_STATE_TESTS.
SAFETY_SAFE_STATE = "safety.safe_state."
SAFETY_SAFE_STATE_ACTIVE = "safety.safe_state_active."
SAFETY_SAFE_STATE_QLF = "safety.qlf.safe_state."
SAFETY_SAFE_STATE_TARGETS = (SAFETY_SAFE_STATE, SAFETY_SAFE_STATE_ACTIVE, SAFETY_SAFE_STATE_QLF)
SAFETY_SAFE_STATE_TYPES = {"uint8", "uint16", "uint32"}
SAFETY_KINDS[SAFETY_SAFE_STATE_ACTIVE] = SAFETY_SAFE_STATE_TYPES
SAFETY_KINDS[SAFETY_SAFE_STATE_QLF] = QLF_TYPES
SAFE_STATE_NUMBERS = {"freewheel": 0, "active_short_low": 1, "active_short_high": 2}   # NX_SAFE_STATE_* numbers
SAFETY_SAFE_STATES = "safety.safe_states"   # key of board_signals: PWM group -> SafeStateGroup
# SYS-143 (safety review SYS-136/SYS-141, decisions E2-E4 of 2026-09-30):
# - E2: the start tests hold the first application task in a busy wait before any application task runs. The task
#   that serves the MCU watchdog is above every application task (tasks.yaml above_application, RTE-02), so it is
#   not delayed; every task below the first application task is, by the sum of all holds. That sum is bounded by
#   SAFE_STATE_TEST_BUDGET_US = a third of the MCU watchdog time-out (project.yaml
#   modules.<SAFE_STATE_WATCHDOG_MODULE>.watchdog_timeout_ms; the three service opportunities of doc/safety/
#   concept.md 7.1): even a watchdog served from a delayed task would lose at most one of them. Derived, never set;
#   a sum above it is an error at pwm.<group>.safe_state_test_us, and RteSystem.cpp asserts it again at compile time.
# - E3: every row of rte::SAFE_STATE_TESTS carries the reader of the gate driver bound to its group (board
#   pwm[].gate_driver, a device whose module has RTE access gate_driver: readGateDriver_<device> of the family
#   integration) - the test requires its enable to read off; nullptr for a group without one.
SAFE_STATE_WATCHDOG_MODULE = "safeLifecycle"         # module of the family port that serves the MCU watchdog
SAFE_STATE_WATCHDOG_TIMEOUT = "watchdog_timeout_ms"
SAFE_STATE_WATCHDOG_SERVICES = 3                     # service opportunities before the time-out (concept 7.1)

# BSW service ports (RTE-05): targets bsw.<service>.<value>, named by service, not by the device that provides it,
# so a component sees the same targets on every board. The values come from the hand-over of ecu/rte/services
# (bswService/BswServiceRte.h, namespace bswservice::rte): one read call per service and task gives every value of
# that service, each call is a single atomic word per value, so any task with any priority may read any service.
#   power  role power_supply (provider publishes state and supply voltage; no provider: QLF_DEBOUNCE)
#   reset  start reasons of the role power_supply (ecuState, 0 until the run level is reached)
#   time   system timer of the platform, one reading per task cycle
# All values are inputs (required ports). "read": argument list of the hand-over call, "expr": C++ expression of a
# value from the locals of that call ({qlf}: health -> qualifier, see BSW_SERVICE_HEALTH).
BSW_SERVICE_PREFIX = "bsw"
BSW_SERVICE_TYPES_INT = {"uint8", "uint16", "uint32"}
BSW_SERVICES = {
    "power": {
        "read": "readPower",
        "locals": [("uint8_t", "power_state"), ("uint16_t", "power_mv"), ("uint8_t", "power_health")],
        "values": {
            "state": {"types": BSW_SERVICE_TYPES_INT, "expr": "power_state",
                      "unit": "bswservice::power::State: 0 UNKNOWN, 1 OK, 2 UNDERVOLTAGE, 3 OVERVOLTAGE, 4 FAULT"},
            "ok": {"types": {"boolean"}, "expr": "((power_health == 1U) && (power_state == 1U)) ? 1U : 0U",
                   "unit": "1 while the provider reports the supply OK"},
            "supply_mv": {"types": {"uint16", "uint32"}, "expr": "power_mv",
                          "unit": "supply voltage in mV, 0 if the provider does not measure it"},
            "qlf": {"types": BSW_SERVICE_TYPES_INT, "expr": "{qlf}", "unit": "QLF_* of Rte_Qlf.h"},
        },
    },
    "reset": {
        "read": "readReset",
        "locals": [("uint8_t", "reset_reason"), ("uint32_t", "reset_sources"), ("uint8_t", "reset_health")],
        "values": {
            "reason": {"types": BSW_SERVICE_TYPES_INT, "expr": "reset_reason",
                       "unit": "bswservice::reset::Reason: 0 UNKNOWN, 1 POWER_ON, 2 RESET, 3 WATCHDOG, 4 SUPPLY, "
                               "5 WAKEUP"},
            "watchdog": {"types": {"boolean"}, "expr": "((reset_sources & {WATCHDOG}) != 0U) ? 1U : 0U",
                         "unit": "1 if this start followed a watchdog reset"},
            "power_on": {"types": {"boolean"}, "expr": "((reset_sources & {POWER}) != 0U) ? 1U : 0U",
                         "unit": "1 if the supply was switched on for this start"},
            "qlf": {"types": BSW_SERVICE_TYPES_INT, "expr": "{qlf}", "unit": "QLF_* of Rte_Qlf.h"},
        },
    },
    "time": {
        "read": "readTime",
        "locals": [("uint32_t", "time_ms"), ("uint32_t", "time_us")],
        "values": {
            "ms": {"types": {"uint32"}, "expr": "time_ms", "unit": "ms since the start, wraps after 49.7 days"},
            "us": {"types": {"uint32"}, "expr": "time_us", "unit": "us since the start, wraps after 71.6 minutes"},
        },
    },
}
BSW_SERVICE_KINDS = tuple(f"{BSW_SERVICE_PREFIX}.{service}.{value}"
                          for service, spec in BSW_SERVICES.items() for value in spec["values"])
# bswservice::Health -> qualifier (COM-03): no reading yet, provider reports, provider cannot tell
BSW_SERVICE_HEALTH = {0: "QLF_DEBOUNCE", 1: "QLF_OK", 2: "QLF_ERROR"}
# numeric values of the hand-over enumerations (RTE interface; test_rtegen.py compares them with the header)
BSW_POWER_STATES = {"UNKNOWN": 0, "OK": 1, "UNDERVOLTAGE": 2, "OVERVOLTAGE": 3, "FAULT": 4}
BSW_RESET_REASONS = {"UNKNOWN": 0, "POWER_ON": 1, "RESET": 2, "WATCHDOG": 3, "SUPPLY": 4, "WAKEUP": 5}

# Mode management (RTE-08): the ECU states of libs/bsw/ecuState are the modes of the mode manager ecu.mode. A
# component reads them through a mode port (R-PORT with MODE-SWITCH-INTERFACE, Rte_Mode_<Port>_<Group>) and has
# runnables started by SWC-MODE-SWITCH-EVENTs (on entry, on exit, on transition). The mode declaration group of the
# port names exactly these modes (case does not matter); its own values are what Rte_Mode returns, the RTE maps the
# state number to them. ECU_MODES: ecustate::State numbers (EcuState.h, test_rtegen.py compares them).
ECU_MODE_TARGET = "ecu.mode"
ECU_MODES = {"STARTUP": 0, "RUN": 1, "STANDBY": 2, "SHUTDOWN": 3, "SLEEP": 4, "RUNDOWN": 5}
ECU_MODE_INITIAL = "STARTUP"     # initial mode of the mode manager (EcuStateRte.cpp starts with STARTUP)
# the application tasks run on the run level (RteSystem, lifecycle level 9) and stop when the lifecycle goes down:
# SHUTDOWN is seen for at most one cycle, SLEEP never - an event bound to them is reported as a warning
ECU_MODES_UNSEEN = ("SHUTDOWN", "SLEEP")
# call order of the mode switch runnables of one task: exit of the old mode, transition, entry of the new mode
MODE_ACTIVATIONS = ("ON-EXIT", "ON-TRANSITION", "ON-ENTRY")
MODE_TRANSITION_VIEW = 0xFF      # value of a task's mode view while it runs its mode switch runnables


# Non-volatile blocks (NVM-02): target nvm.<Block>. The one provided port connected to nvm.<Block> writes the block,
# its ARXML init value is the default; required ports connected to nvm.<Block> read it (same type as the writer).
# The value is an RTE buffer like any other; the RTE keeps an image of it for the NvM (ecu/application/src/systems/
# nvm/NvmManager.h), loads the stored value into the buffer before the init runnables (rte::nvmRestore) and hands a
# changed value over to the image at the end of the writer's task cycle (and after the init runnables). The NvM writes
# it on a virtual EEPROM (OpenBSW eeprom::IEepromDriver: FlexNVM EEPROM emulation, data-flash emulation, SPI EEPROM)
# in two slots with CRC. Records, arrays and fixed point are allowed: the block stores the bytes of the buffer.
NVM_PREFIX = "nvm"
NVM_BLOCK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
NVM_RECORD_OVERHEAD = 8          # id, size, sequence, CRC-16 (nvm/Nvm.h RECORD_OVERHEAD)
NVM_ASIL_OVERHEAD = 12           # NVM-18: id, size, write counter, CRC-32 (nvm/Nvm.h ASIL_OVERHEAD)
# NVM-18: quality of a block, target nvm.qlf.<Block> (required ports, QLF_TYPES). The NvM writes nvm::Quality
# (libs/bsw/nvm/include/nvm/Nvm.h, numbers are a contract) into rte::nvmQuality; nvmRestore() turns it into QLF_*.
# RTE-28 (decision of 2026-09-29): a block that is not read yet or was never written is always QLF_NOT_AVAILABLE -
# the component sees that its value is the default and not a stored one (before: DEBOUNCE resp. OK). The quality
# buffer starts with the NOT_READ value too. A lost value (UNSECURED) and a missing memory stay errors.
NVM_QLF_KIND = "nvm.qlf"
NVM_QUALITY = [                  # (nvm::Quality, number, QLF_* of Rte_Qlf.h)
    ("NOT_READ", 0, "NOT_AVAILABLE"),   # the NvM has not read the block (yet)
    ("STORED", 1, "OK"),                # the stored value (ASIL: a defective copy was repaired)
    ("DEFAULT", 2, "NOT_AVAILABLE"),    # never written: the default the component defines, no stored value
    ("UNSECURED", 3, "ERROR_CRC"),      # the stored value is lost (no valid place, or other layout): the default
    ("NO_MEMORY", 4, "ERROR"),          # no persistent memory (or the block lies beyond it): the default
]
NVM_MAX_BLOCK_BYTES = 0x7FFF     # size field of the record is 16 bit; far above any sensible EEPROM block
# NVM-05: the block of the operating time and reset statistics of the application (ecu/application/src/systems/stats/
# EcuStatistics.h, ecustats::Record of 52 bytes). Reserved name like DemEvents/DemEntries: created when project.yaml
# nvm.blocks names it (write policy, min_interval_ms, integrity, address from there), never connected in rte.yaml.
# The application finds it by name and uses it only with exactly this size. ECU_STATS_SIGNATURE goes into the record
# id: a new record format gets a new signature, so an old record starts from the default.
ECU_STATS_BLOCK = "EcuStatistics"
ECU_STATS_BYTES = 52
ECU_STATS_SIGNATURE = "ecustats:52:v1"
NVM_WINDOW_KIND = "eeprom_ram"   # derivative memory region of an emulated EEPROM (S32K1 FlexRAM)
NVM_WRITE_POLICIES = ("SHUTDOWN", "CHANGE")   # nvm::WritePolicy / rte::NvmWrite


def bsw_service_expr(kind: str, ctype: str) -> str:
    """C++ expression of one bsw.<service>.<value> from the locals of the service's read call."""
    service, value = kind.split(".")[1:]
    local_ = BSW_SERVICES[service]["locals"][-1][1]      # the health is the last local (power, reset)
    qlf = (f"(({local_} == 1U) ? {BSW_SERVICE_HEALTH[1]} : (({local_} == 2U) ? {BSW_SERVICE_HEALTH[2]} "
           f": {BSW_SERVICE_HEALTH[0]}))")
    expr = BSW_SERVICES[service]["values"][value]["expr"]
    expr = expr.replace("{qlf}", qlf)
    for name, bit in WAKEUP_BITS.items():
        expr = expr.replace("{" + name + "}", f"0x{1 << bit:08X}U")
    if ctype == "boolean":
        return expr
    return f"static_cast<{ctype}>({expr})"


# value range of the integer platform types, None: floating point
TYPE_RANGE = {
    "boolean": (0, 1), "uint8": (0, 2**8 - 1), "sint8": (-(2**7), 2**7 - 1), "uint16": (0, 2**16 - 1),
    "sint16": (-(2**15), 2**15 - 1), "uint32": (0, 2**32 - 1), "sint32": (-(2**31), 2**31 - 1),
    "float32": None, "float64": None,
}
CTYPE_CPP = {"boolean": "boolean", "uint8": "uint8_t", "sint8": "int8_t", "uint16": "uint16_t", "sint16": "int16_t",
             "uint32": "uint32_t", "sint32": "int32_t", "float32": "float", "float64": "double"}


# BSW tasks, ISR groups and the default application task (RTE-02)
TASKS_MANIFEST = Path("ecu") / "asyncCoreConfiguration" / "tasks.yaml"
# PLAT-26: stacks of the task objects of ecu/application/src/app/app.cpp and the merges a project may choose
TASK_STACKS_MANIFEST = Path(*nxbswtasks.TASK_STACKS_NAMES)
BSW_TASKS_FILE = "tasks/BswTasks.h"
# Task names of the BSW task manifest follow the rule of the application tasks (schema AppTask.name, ARCH-34)
TASK_NAME_RE = re.compile(nxmodel.AppTask.model_fields["name"].metadata[0].pattern)
RESERVED_TASK_NAMES = set(nxmodel.RESERVED_TASK_NAMES)


class RteError(Exception):
    pass


def _schema_error(where: tuple, err: dict) -> str:
    """One pydantic error of the ECU description as '<YAML path>: <text>' (path first, see rte_message)."""
    loc = where + tuple(p for p in err["loc"] if not (isinstance(p, str) and ("[" in p or p in _UNION_TAGS)))
    text = err["msg"].removeprefix("Value error, ")
    return f"{nxconfig.format_path(loc)}: {text}" if loc else text


# pydantic inserts union member tags into error locations (same list as tools/nxconfig/config.py)
_UNION_TAGS = {"int", "float", "str", "bool", "none", "constrained-str", "constrained-int", "constrained-float"}
# a placeholder that satisfies the required section `components` when only other sections are validated
_NO_COMPONENTS = [{"name": "_", "path": "_"}]
# RTE-22: key that carries the format of an ECU description (tools/nxconfig/model.py EcuDescription)
SCHEMA_KEY = "schema_version"
# the format whose paths are relative to the rte.yaml instead of the repository root (customer-project.md N3)
FILE_RELATIVE_PATHS = 2


def ecu_description(ecu, *sections: str) -> "nxmodel.EcuDescription":
    """The ECU description as schema model (tools/nxconfig/model.py EcuDescription, ARCH-34).

    `ecu` is the model (generate, validate) or the plain mapping of an rte.yaml (tools/schedana, tools/boardtest,
    neXtBSW Studio). A mapping is validated against the schema; with `sections` only those sections, so a caller
    that needs the CAN section is not stopped by an unfinished task list. Raises RteError with the YAML path.
    """
    if isinstance(ecu, nxmodel.EcuDescription):
        return ecu
    if not isinstance(ecu, dict):
        raise RteError("expected a mapping at the top level of the ECU description")
    data = {k: v for k, v in ecu.items() if not sections or k in sections}
    if sections and "components" not in sections:
        data["components"] = _NO_COMPONENTS
    # RTE-22: the format of the document is not a section, so a caller asking for `can` keeps it. A mapping read
    # past nxconfig (schedana, boardtest, Studio) may still be format 1; the placeholder only satisfies the schema
    # here - what the paths are based on is decided by paths_base() from the mapping itself, not from this.
    if sections:
        data[SCHEMA_KEY] = ecu.get(SCHEMA_KEY, FILE_RELATIVE_PATHS)
    try:
        return nxmodel.EcuDescription.model_validate(data)
    except ValidationError as e:
        raise RteError("\n".join(_schema_error((), err) for err in e.errors())) from None


def schema_version_of(ecu) -> int:
    """Format of an ECU description: the model is always the current one, a mapping without the key is format 1."""
    value = ecu.get(SCHEMA_KEY) if isinstance(ecu, dict) else getattr(ecu, SCHEMA_KEY, None)
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    return value


def paths_base(ecu, ecu_file: "Path | None", repo: Path) -> Path:
    """Folder the relative paths of an ECU description (`components[].path`, `can.dbc`) are based on (RTE-22).

    Format 2 (customer-project.md gap N3): the folder of the rte.yaml itself, the way `project.board` is relative
    to its project.yaml - a customer workspace has no neXtBSW root the paths could be based on. Format 1: the
    repository root, which is what `--repo` is; it stays only for that fallback.
    """
    if ecu_file is not None and schema_version_of(ecu) >= FILE_RELATIVE_PATHS:
        return Path(ecu_file).resolve().parent
    return Path(repo)


def path_of(ecu, ecu_file: "Path | None", repo: Path, value: str) -> Path:
    """One path of an ECU description as an absolute, normalised folder or file (RTE-22).

    Normalised on purpose: a path relative to the rte.yaml reaches a reusable component with `../..`, and those
    steps would otherwise stand in the middle of what the generator writes into `rte_components.cmake` - the same
    place spelled differently, so the generated file would change although nothing about the build did.
    """
    return Path(os.path.normpath(paths_base(ecu, ecu_file, repo) / value))


def with_resolved_paths(ecu, ecu_file: Path, repo: Path | None = None):
    """A copy of the mapping whose `components[].path` and `can.dbc` are absolute (RTE-22).

    The paths of an ECU description are relative to the file they were read from. A caller that hands the mapping
    to the generator from *somewhere else* - a test profile rendered into a scratch folder, a preview of an edited
    description - would otherwise have them looked up next to the new place. Resolving them once against the file
    they came from keeps them pointing at the same components and the same DBC, wherever the copy is written.
    """
    if not isinstance(ecu, dict):
        return ecu
    base = paths_base(ecu, ecu_file, repo if repo is not None else HERE.parents[1])
    result = copy.deepcopy(ecu)

    def absolute(value):
        return (base / value).resolve().as_posix() if not Path(value).is_absolute() else value

    for entry in result.get("components") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            entry["path"] = absolute(entry["path"])
    can = result.get("can")
    if isinstance(can, dict) and isinstance(can.get("dbc"), str):
        can["dbc"] = absolute(can["dbc"])
    return result


def load_ecu(ecu_file: Path) -> "nxmodel.EcuDescription":
    """Reads and validates an ECU description with the loader of tools/nxconfig; RteError with all findings."""
    ecu, messages, _tree = nxconfig.config.read_rte(ecu_file)
    if ecu is None:
        texts = [m.text.removeprefix("Value error, ") for m in messages]
        raise RteError("\n".join(f"{m.yaml_path}: {t}" if m.path else t for m, t in zip(messages, texts)))
    return ecu


def c_name(name: str) -> str:
    """C identifier of a configuration name, same rule as tools/bswgen/modules.py identifier()."""
    ident = re.sub(r"\W", "_", name.upper())
    return f"SIG_{ident}" if ident[0].isdigit() else ident


def can_buses(config: "nxconfig.Config | None") -> list[str]:
    """Board names of the CAN buses the project uses (ARCH-07), in the order of their bus ids."""
    integration = getattr(config, "integration", None) if config is not None else None
    return [b.name for b in (integration.can if integration is not None else [])]


@dataclass(eq=False)
class Task:
    name: str
    priority: int                  # ordering key (tasks.yaml), the FreeRTOS priority is os_priority
    where: str                     # YAML path for messages
    period_ms: int = 0             # application tasks only
    stack_bytes: int = 0
    bsw: bool = False
    above_application: bool = False
    os_priority: int = 0           # context number = FreeRTOS priority (OpenBSW asyncFreeRtos)
    index: int = 0                 # position in rte::TASKS (application tasks)
    runnables: list = field(default_factory=list)  # (component, runnable) in call order
    hyper_ticks: int = 1
    copy_in: list = field(default_factory=list)    # buffers this task reads from other tasks
    copy_out: list = field(default_factory=list)   # buffers this task publishes to other tasks
    mode_runnables: list = field(default_factory=list)  # (component, runnable) started by a mode switch (RTE-08)

    @property
    def context(self) -> str:
        return f"TASK_{self.name.upper()}"


@dataclass(eq=False)
class DataType:
    """Implementation data type of a port element or parameter (RTE-04).

    VALUE: a scalar of a platform type (native), possibly an enumeration (literals of a TEXTTABLE compu method) or
    fixed point (LINEAR compu method: physical = (numerator[0] + numerator[1] * stored) / denominator).
    ARRAY: `size` elements of `element`. STRUCTURE: named `fields` in declaration order (a record, a Simulink bus).
    The C name is the implementation data type; an anonymous array inside a record has the name "".
    Scaling and literals come from the application data type (DATA-TYPE-MAPPING-SET) or the implementation type.
    """
    path: str
    name: str
    native: str | None            # platform type of a scalar, None for ARRAY and STRUCTURE
    category: str = "VALUE"       # VALUE, ARRAY or STRUCTURE
    element: "DataType | None" = None             # ARRAY: type of one element
    size: int = 0                                 # ARRAY: fixed number of elements
    fields: list = field(default_factory=list)    # STRUCTURE: [(name, DataType)]
    target: "DataType | None" = None              # TYPE_REFERENCE: the type this one is a typedef of
    numerator: tuple = (0, 1)
    denominator: int = 1
    unit: str = ""
    literals: tuple = ()          # enumeration: ((symbol, value), ...)

    @property
    def composite(self) -> bool:
        return self.category != "VALUE"

    @property
    def scaled(self) -> bool:
        return tuple(self.numerator) != (0, 1) or self.denominator != 1

    @property
    def ctype(self) -> str:
        """C type of an RTE buffer: the platform type of a scalar, the type name of a record or array."""
        return self.native if not self.composite else self.name

    def signature(self) -> tuple:
        """What has to agree between a sender and a receiver: layout, element types, scaling, literals.

        Records compare by name as well, because the RTE copies them with a C assignment.
        """
        if self.category == "ARRAY":
            return ("ARRAY", self.size, self.element.signature())
        if self.category == "STRUCTURE":
            return ("STRUCTURE", self.name, tuple((n, t.signature()) for n, t in self.fields))
        return ("VALUE", self.native, tuple(self.numerator), self.denominator, tuple(self.literals))

    def describe(self) -> str:
        """Short text for messages, e.g. 'TypeSwc_CmdBus (record Mode, Speed, Limits, Enable)'."""
        if self.category == "ARRAY":
            return f"{self.name or 'array'} ({self.element.describe()}[{self.size}])"
        if self.category == "STRUCTURE":
            return f"{self.name} (record {', '.join(n for n, _ in self.fields)})"
        extra = []
        if self.scaled:
            extra.append(f"physical = ({self.numerator[0]} + {self.numerator[1]} * stored) / {self.denominator}")
        if self.literals:
            extra.append("enumeration " + ", ".join(f"{s}={v}" for s, v in self.literals))
        name = self.name if self.name == self.native else f"{self.name} ({self.native})"
        return name + (f" [{'; '.join(extra)}]" if extra else "")

    def leaves(self, prefix: str = "") -> list:
        """Scalar parts as (C access suffix, platform type), e.g. ('.Echo.Limits[1]', 'uint8')."""
        if self.category == "ARRAY":
            return [leaf for i in range(self.size) for leaf in self.element.leaves(f"{prefix}[{i}]")]
        if self.category == "STRUCTURE":
            return [leaf for n, t in self.fields for leaf in t.leaves(f"{prefix}.{n}")]
        return [(prefix, self.native)]

    def leaf_types(self) -> list:
        """RTE-29: the scalar DataType of every part, in the order of leaves() (scaling, unit of each row)."""
        if self.category == "ARRAY":
            return [t for _ in range(self.size) for t in self.element.leaf_types()]
        if self.category == "STRUCTURE":
            return [leaf for _, t in self.fields for leaf in t.leaf_types()]
        return [self]

    def decl(self, name: str) -> str:
        """C declaration of a record member of this type (an anonymous array keeps its size at the member)."""
        if self.category == "ARRAY" and not self.name:
            return self.element.decl(f"{name}[{self.size}]")
        return f"{self.name} {name}"

    @property
    def read_param(self) -> str:
        """Parameter of Rte_Read (AUTOSAR: arrays by pointer to the element, everything else by pointer)."""
        return f"{self.element.name}* data" if self.category == "ARRAY" else f"{self.name}* data"

    @property
    def write_param(self) -> str:
        """Parameter of Rte_Write (AUTOSAR: arrays and records by const pointer, scalars by value)."""
        if self.category == "ARRAY":
            return f"const {self.element.name}* data"
        if self.category == "STRUCTURE":
            return f"const {self.name}* data"
        return f"{self.name} data"

    @property
    def iread_type(self) -> str:
        """Return type of Rte_IRead (RTE-09): scalars by value, arrays as const element pointer, records as const
        pointer - the forms Embedded Coder calls."""
        if self.category == "ARRAY":
            return f"const {self.element.name}*"
        if self.category == "STRUCTURE":
            return f"const {self.name}*"
        return self.name

    @property
    def iwriteref_type(self) -> str:
        """Return type of Rte_IWriteRef (RTE-09): pointer to the copy, arrays as element pointer."""
        return f"{self.element.name}*" if self.category == "ARRAY" else f"{self.name}*"

    @property
    def alignment(self) -> int:
        if self.category == "ARRAY":
            return self.element.alignment
        if self.category == "STRUCTURE":
            return max((t.alignment for _, t in self.fields), default=1)
        return PLATFORM_SIZES[self.native]

    @property
    def byte_size(self) -> int:
        """sizeof() on the target (natural alignment of the ARM EABI), for the memory of the implicit copies."""
        if self.category == "ARRAY":
            return self.size * self.element.byte_size
        if self.category == "STRUCTURE":
            offset = 0
            for _, t in self.fields:
                offset = -(-offset // t.alignment) * t.alignment + t.byte_size
            return -(-offset // self.alignment) * self.alignment
        return PLATFORM_SIZES[self.native]


@dataclass
class Port:
    name: str
    direction: str  # "in" (R-port) or "out" (P-port)
    interface: str
    element: str
    type: DataType
    init: str      # C initializer of the buffer (stored values of the ARXML init value, RTE-04)
    init_declared: bool = False   # RTE-23: the ARXML names an INIT-VALUE (swc.yaml of a C component has none)


@dataclass
class Parameter:
    """Calibration parameter: one element of a ParameterReceiver port (PARAMETER-INTERFACE, RTE-03).

    The model describes it (type, dimension, scaling); the value comes from <Swc>_data.m. The RTE keeps it in RAM
    (not const), so a calibration tool may change it while the ECU runs (XCP, TOOL-01). Components only read it.
    """
    component: str
    port: str
    element: str
    type: DataType                # implementation type of one element (native: platform type)
    dim: int = 1                  # 1: scalar, > 1: fixed-size array
    numerator: tuple = (0, 1)     # COMPU-METHOD: physical = (numerator[0] + numerator[1] * raw) / denominator
    denominator: int = 1
    unit: str = ""
    access: str = "READ-WRITE"    # SW-CALIBRATION-ACCESS of the ARXML
    runnables: set = field(default_factory=set)   # runnables with a PARAMETER-ACCESS to it
    phys: list = field(default_factory=list)      # values as the data file writes them
    raw: list = field(default_factory=list)       # values stored in RAM
    # RTE-26 (calibration K1): definition from swc.yaml; the defaults describe an RTE-03 parameter
    kind: str = ""                # value, array, curve, map, axis; "" = no swc.yaml definition (RTE-03)
    safety: str = "qm"
    min: object = None            # engineering limits, physical
    max: object = None
    description: str = ""
    value_table: dict = field(default_factory=dict)   # stored value -> text
    axis_names: list = field(default_factory=list)    # elements of the X (and Y) axis, same port
    own_of: str = ""              # own breakpoints: element of their curve/map
    shape: tuple = ()             # (nx,) curve, (nx, ny) map
    axes: list = field(default_factory=list)          # resolved axis Parameters (finish_calibration)
    owner: "Parameter | None" = None                  # own breakpoints: their curve/map
    record: str = ""              # C variable of the record (own axes + values) this element lives in
    member: str = ""              # x, y or v in that record
    limits: tuple | None = None   # (min, max) as stored values of the element type, None: type range

    @property
    def key(self) -> str:
        return f"{self.port}.{self.element}"

    @property
    def name(self) -> str:
        return f"{self.component}.{self.port}.{self.element}"

    @property
    def symbol(self) -> str:
        """Access function Rte_Prm_<Component>_<Port>_<Element> (the model calls Rte_Prm_<Port>_<Element>)."""
        return f"Rte_Prm_{self.component}_{self.port}_{self.element}"

    @property
    def storage(self) -> str:
        """RAM variable holding the value (address and symbol for XCP/A2L)."""
        return f"{self.symbol}_data"

    @property
    def expr(self) -> str:
        """C expression of the storage: the variable, or its member in the record of a curve/map (RTE-26)."""
        return f"{self.record}.{self.member}" if self.record else self.storage

    @property
    def limits_symbol(self) -> str:
        """Constant {min, max} of the stored limits (RTE-26), referenced by rte::PARAMETERS[]."""
        return f"Rte_Lim_{self.component}_{self.port}_{self.element}"

    @property
    def scaled(self) -> bool:
        return tuple(self.numerator) != (0, 1) or self.denominator != 1


@dataclass
class Runnable:
    name: str
    symbol: str
    period_ms: int | None  # None: init runnable
    accesses: set = field(default_factory=set)  # Port.Element keys read or written (ARXML access points)
    task: "Task | None" = None
    offset_ms: int = 0
    mode_events: list = field(default_factory=list)   # ModeEvent: started by mode switches instead of a period
    mode_accesses: set = field(default_factory=set)   # Port.Group keys of its mode access points (Rte_Mode)
    implicit: dict = field(default_factory=dict)      # Port.Element -> "r"/"w": implicit access (RTE-09)
    explicit: set = field(default_factory=set)        # Port.Element keys of its explicit access points
    enabled: bool = True          # RTE-23: False - disabled (runnable or its component), never called
    disabled_where: str = ""      # RTE-23: YAML path of the switch that disabled it

    @property
    def init(self) -> bool:
        return self.period_ms is None and not self.mode_events


@dataclass
class ModeGroup:
    """MODE-DECLARATION-GROUP of a mode port (RTE-08): the modes and the values Rte_Mode returns for them.

    Values: EXPLICIT_ORDER takes the VALUE of each MODE-DECLARATION, otherwise (ALPHABETIC_ORDER, the AUTOSAR
    default) the modes are numbered 0.. in alphabetical order. The transition value (RTE_TRANSITION_<Group>) is
    ON-TRANSITION-VALUE, for ALPHABETIC_ORDER without it the number of modes.
    """
    name: str
    modes: dict[str, int]          # mode name as in the ARXML -> value, in declaration order
    transition: int
    initial: str | None = None     # INITIAL-MODE-REF, informative: the mode manager decides the initial mode

    @property
    def ctype(self) -> str:
        top = max([*self.modes.values(), self.transition])
        return "uint8" if top <= 0xFF else ("uint16" if top <= 0xFFFF else "uint32")

    def signature(self) -> tuple:
        return (tuple(sorted(self.modes.items())), self.transition, self.ctype)


@dataclass
class ModePort:
    """Required mode port (R-PORT-PROTOTYPE with MODE-SWITCH-INTERFACE): one mode group prototype (RTE-08)."""
    name: str
    group: str           # MODE-GROUP (mode declaration group prototype) of the interface
    interface: str
    decl: ModeGroup

    @property
    def key(self) -> str:
        return f"{self.name}.{self.group}"


@dataclass
class ModeEvent:
    """SWC-MODE-SWITCH-EVENT of a runnable: ON-ENTRY/ON-EXIT of one mode, ON-TRANSITION from one mode to another."""
    activation: str
    port: str            # Port.Group key of the mode port
    modes: tuple         # mode names of the port's group: (mode,) or (from, to)


@dataclass
class Component:
    name: str
    ports: dict[str, Port] = field(default_factory=dict)
    runnables: list[Runnable] = field(default_factory=list)
    access_points: bool = False  # False: ARXML without access points, every runnable may access every port
    parameters: dict[str, Parameter] = field(default_factory=dict)  # Port.Element -> calibration parameter (RTE-03)
    mode_ports: dict[str, ModePort] = field(default_factory=dict)   # Port.Group -> mode port (RTE-08)
    mode_access_points: bool = False  # False: no MODE-ACCESS-POINT, every runnable may read every mode port
    measurements: list = field(default_factory=list)  # TOOL-143: internal values (Measurement), swc.yaml
    safety: str = "qm"   # RTE-27: highest safety class of its swc.yaml (component_safety), "qm" without one
    enabled: bool = True             # RTE-23: components[].enabled of the ECU description
    disabled_where: str = ""         # RTE-23: YAML path of that switch
    index: int | None = None         # RTE-23: position in components[] (YAML path of its messages)
    disabled_values: dict = field(default_factory=dict)   # RTE-23: Port.Element -> C initializer while disabled


@dataclass
class Buffer:
    name: str      # C identifier of the RTE buffer
    ctype: str
    init: str
    label: str     # human readable, for the console
    bsw: str | None = None       # BSW kind, e.g. dio.in
    bsw_signal: str | None = None  # board targets: C identifier (c_name), else CAN signal, wake-up mask, service
    links: list[str] = field(default_factory=list)  # all BSW connections, for the console
    sleep_request: bool = False  # provided port connected to ecu.sleep_request
    # task access (plan_tasks): copies per task name ("r"/"w") if the buffer is shared between tasks
    shared: bool = False
    copies: dict = field(default_factory=dict)
    io_task: "Task | None" = None  # task doing the dio/adc/pwm/ecu transfer of this buffer
    dtype: "DataType | None" = None  # type of the owning port (None: scalar BSW/CAN buffer of type ctype)
    bsw_name: str | None = None    # board targets: name as in the board profile / project.yaml (RTE-11)
    diag_links: list[str] = field(default_factory=list)  # COM-28: did.* / routine.* targets of rte.yaml
    nvm: str | None = None         # NVM-02: name of the non-volatile block this provided port writes
    dem_links: list[str] = field(default_factory=list)  # NVM-03: dem.event.* / dem.cycle.* targets of rte.yaml
    age: "AgeSupervision | None" = None  # SYS-32: age qualifier buffer (age.qlf.<writer>) and its supervision
    init_only: str | None = None   # RTE-27: why the port reads only its init value (INIT_UNCONNECTED), const buffer
    disabled_by: str | None = None  # RTE-23: the disabled source (INIT_DISABLED): "<port> (<YAML path of the switch>)"
    device: "DeviceSource | None" = None  # RTE-18: module whose instance value this buffer reads (module.yaml rte)

    @property
    def constant(self) -> bool:
        """RTE-27: nobody writes this buffer, so it is const (flash) - Rte.c and Rte_Internal.h declare it so."""
        return self.init_only is not None

    @property
    def bsw_label(self) -> str | None:
        """Name of the connected BSW signal as the user wrote it (board name), for messages."""
        return self.bsw_name if self.bsw_name is not None else self.bsw_signal

    @property
    def connection(self) -> str | None:
        return ", ".join(self.links) if self.links else None

    @property
    def array(self) -> bool:
        return self.dtype is not None and self.dtype.category == "ARRAY"

    def leaves(self) -> list:
        """Scalar parts for the signal table (console, XCP): (label, C expression, platform type).

        A record or an array is listed element by element, e.g. TypeSwc.Stat.Stat.Echo.Limits[1] (RTE-04).
        """
        if self.dtype is None or not self.dtype.composite:
            return [(self.label, self.name, self.ctype)]
        return [(self.label + suffix, self.name + suffix, native) for suffix, native in self.dtype.leaves()]

    def leaf_types(self) -> list:
        """RTE-29: DataType of each row of leaves() (None for a BSW/CAN buffer without a port type)."""
        if self.dtype is None:
            return [None]
        return self.dtype.leaf_types()

    def assign(self, dst: str, src: str) -> str:
        """C++ statement copying one instance of this buffer (arrays cannot be assigned)."""
        if self.array:
            return f"(void)std::memcpy(&{dst}, &{src}, sizeof({dst}));"
        return f"{dst} = {src};"

    def ref(self, task: "Task | None") -> str:
        """C name of the buffer as seen from a task: its copy if it has one, else the published value."""
        if task is not None and self.shared and task.name in self.copies:
            return f"Rte_Cpy_{task.name}_{self.name[len('Rte_Buf_'):]}"
        return self.name

    @property
    def io_ref(self) -> str:
        return self.ref(self.io_task)


@dataclass
class CanSignalLink:
    signal: "dbcgen.Signal"
    buffer: Buffer
    kind: str  # "bool", "int" or "float": conversion between buffer and signal


@dataclass
class E2eConfig:
    """E2E protection of one message: profile, data id and the field offsets derived from DBC signals."""
    profile: str
    where: str                     # YAML path of the e2e section
    crc_signal: str
    counter_signal: str
    nibble_signal: str | None = None
    data_id: int = 0
    data_id_list: list = field(default_factory=list)   # P02
    data_id_mode: str = "BOTH"
    max_delta: int = 1
    crc_offset: int = 0            # bit offsets as in AUTOSAR: byte = offset / 8, offset % 8 == 0 low nibble
    counter_offset: int = 0
    nibble_offset: int = 0
    data_length: int = 0           # bits, 8 * DBC message length

    @property
    def config_type(self) -> str:
        return f"E2E_{self.profile}ConfigType"

    @property
    def initializer(self) -> str:
        """Positional initializer of the profile configuration (C++17 has no designated initializers)."""
        if self.profile == "P02":
            ids = ", ".join(f"0x{i:02X}U" for i in self.data_id_list)
            fields = [("DataLength", f"{self.data_length}U"), ("DataIDList", f"{{{ids}}}"),
                      ("MaxDeltaCounterInit", f"{self.max_delta}U"),
                      ("MaxNoNewOrRepeatedData", f"{E2E_PROFILES['P02']['counter_modulo'] - 1}U"),
                      ("SyncCounterInit", f"{E2E_SYNC_COUNTER_INIT}U")]
        else:
            fields = [("CounterOffset", f"{self.counter_offset}U"), ("CRCOffset", f"{self.crc_offset}U"),
                      ("DataID", f"0x{self.data_id:04X}U"), ("DataIDNibbleOffset", f"{self.nibble_offset}U"),
                      ("DataIDMode", f"E2E_{self.profile}_DATAID_{self.data_id_mode}"),
                      ("DataLength", f"{self.data_length}U")]
            if self.profile == "P01":
                fields += [("MaxDeltaCounterInit", f"{self.max_delta}U"),
                           ("MaxNoNewOrRepeatedData", f"{E2E_PROFILES['P01']['counter_modulo'] - 1}U"),
                           ("SyncCounterInit", f"{E2E_SYNC_COUNTER_INIT}U")]
            else:
                fields.append(("MaxDeltaCounter", f"{self.max_delta}U"))
        return "{" + ", ".join(f"/* {name} */ {value}" for name, value in fields) + "}"

    @property
    def map_call(self) -> str:
        """MapStatusToSM call for the state variable named 'state'."""
        if self.profile == "P11":
            return "E2E_P11MapStatusToSM(rc, state.Status)"
        # profile behaviour of R4.2 (recommended in libs/bsw/e2e/doc/e2e.md): INITIAL -> WRONGSEQUENCE, SYNC -> OK
        return f"E2E_{self.profile}MapStatusToSM(rc, state.Status, true)"


@dataclass
class Qualifier:
    """can.qualifiers.<Message>: supervision of one message (rx) or its E2E protection (tx)."""
    message: str
    where: str
    timeout_ms: int | None = None  # None: GenMsgTimeoutTime of the DBC
    debounce_ms: int = 0
    on_error: str = "hold"
    e2e: E2eConfig | None = None


@dataclass
class CanMessage:
    message: "dbcgen.Message"
    direction: str  # "rx" or "tx"
    links: list[CanSignalLink] = field(default_factory=list)
    ticks: int = 0  # tx: cycle, rx: timeout (0: no supervision) in base periods
    qlf: "Qualifier | None" = None       # rx: always set, tx: only with an e2e section
    qlf_buffer: "Buffer | None" = None   # rx: RTE buffer of can.qlf.<Message>
    index: int = 0                       # position in CAN_RX_STATUS / CAN_TX_STATUS
    where: str = ""                      # COM-38 tx: first connection to it ("connection <port> -> <target>")
    delay_ticks: int = 0                 # COM-38 tx: GenMsgDelayTime in base periods (event and mixed messages)
    event_index: int = 0                 # COM-38 tx: position in txEvents (event and mixed messages)

    @property
    def send_mode(self) -> str:
        """COM-38 (tx): dbcgen.SEND_CYCLIC, SEND_EVENT or SEND_MIXED - checked by tx_mode_problem."""
        return self.message.send_mode or dbcgen.SEND_CYCLIC

    @property
    def on_change(self) -> bool:
        """COM-38 (tx): sent when its packed signal values change (event or mixed message)."""
        return self.send_mode != dbcgen.SEND_CYCLIC

    @property
    def e2e(self) -> "E2eConfig | None":
        return self.qlf.e2e if self.qlf is not None else None

    @property
    def timeout_ms(self) -> int:
        given = self.qlf.timeout_ms if self.qlf is not None else None
        return self.message.timeout_ms if given is None else given


@dataclass
class CanConfig:
    dbc: "dbcgen.Database"
    node: str
    bus: str                       # board name of the CAN bus the signals run on (busid::<bus_ident>)
    monitor: list[str]
    task_name: str | None = None   # can.task, resolved in build_tasks
    bus_ident: str = ""            # C name of the bus (busid::<bus_ident>), filled by load_can
    rx: dict[str, CanMessage] = field(default_factory=dict)
    tx: dict[str, CanMessage] = field(default_factory=dict)
    task: "Task | None" = None
    qualifiers: dict = field(default_factory=dict)  # can.qualifiers: message -> Qualifier (before the DBC check)

    @property
    def messages(self):
        return [m.message for m in list(self.rx.values()) + list(self.tx.values())]

    @property
    def e2e_profiles(self) -> list[str]:
        """Profiles used by this ECU description, for the includes of the generated code."""
        used = {e.e2e.profile for e in list(self.rx.values()) + list(self.tx.values()) if e.e2e is not None}
        return sorted(used)


def text(node, tag):
    child = node.find(f"ar:{tag}", NS)
    return child.text.strip() if child is not None and child.text else None


def local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def index_elements(root):
    """Maps absolute AUTOSAR paths (/Pkg/Sub/Element) to XML nodes."""
    result = {}

    def walk(node, prefix):
        for pkg in node.findall("ar:AR-PACKAGES/ar:AR-PACKAGE", NS):
            path = f"{prefix}/{text(pkg, 'SHORT-NAME')}"
            elements = pkg.find("ar:ELEMENTS", NS)
            if elements is not None:
                for element in elements:
                    name = text(element, "SHORT-NAME")
                    if name:
                        result[f"{path}/{name}"] = element
            walk(pkg, path)

    walk(root, "")
    return result


def load_arxml(folder: Path):
    files = sorted(folder.glob("*.arxml"))
    if not files:
        raise RteError(f"no ARXML files in {folder}")
    elements = {}
    for f in files:
        elements.update(index_elements(ET.parse(f).getroot()))
    return elements


PROPS = "ar:SW-DATA-DEF-PROPS/ar:SW-DATA-DEF-PROPS-VARIANTS/ar:SW-DATA-DEF-PROPS-CONDITIONAL"
C_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ref(node, tag: str) -> str | None:
    ref = node.find(f"ar:{tag}", NS) if node is not None else None
    return ref.text.strip() if ref is not None and ref.text else None


def _base_native(elements, props, path: str) -> str:
    base = elements.get(_ref(props, "BASE-TYPE-REF") or "")
    native = text(base, "NATIVE-DECLARATION") if base is not None else None
    if native not in PLATFORM_TYPES:
        raise RteError(f"{path}: base type {native} is not an AUTOSAR platform type")
    return native


def _scalar_compu(elements, props, dtype: DataType, where: str):
    """Applies the COMPU-METHOD of a scalar (implementation or application type) to dtype.

    IDENTICAL: nothing; LINEAR: fixed-point scaling; TEXTTABLE: enumeration literals (not for boolean, whose
    FALSE/TRUE table is the platform type). Anything else (tables, SCALE_LINEAR with several scales) is refused.
    """
    if props is None or props.find("ar:COMPU-METHOD-REF", NS) is None:
        return
    method = elements.get(_ref(props, "COMPU-METHOD-REF"))
    if method is None:
        raise RteError(f"{where}: compu method {_ref(props, 'COMPU-METHOD-REF')} not found")
    category = text(method, "CATEGORY")
    if category == "TEXTTABLE":
        if dtype.native == "boolean":
            return
        if dtype.native not in TYPE_RANGE or TYPE_RANGE[dtype.native] is None:
            raise RteError(f"{where}: enumeration {text(method, 'SHORT-NAME')} on {dtype.native}, "
                           f"an enumeration needs an integer type")
        literals = []
        for scale in method.findall("ar:COMPU-INTERNAL-TO-PHYS/ar:COMPU-SCALES/ar:COMPU-SCALE", NS):
            lo, hi = text(scale, "LOWER-LIMIT"), text(scale, "UPPER-LIMIT")
            vt = scale.find("ar:COMPU-CONST/ar:VT", NS)
            symbol = text(scale, "SYMBOL") or (vt.text.strip() if vt is not None and vt.text else None)
            if symbol is None or lo is None or lo != hi:
                raise RteError(f"{where}: enumeration {text(method, 'SHORT-NAME')}: every COMPU-SCALE needs one "
                               f"value (LOWER-LIMIT = UPPER-LIMIT) and a literal (VT or SYMBOL)")
            if not C_IDENT_RE.match(symbol):
                raise RteError(f"{where}: enumeration literal {symbol!r} of {text(method, 'SHORT-NAME')} is no C "
                               f"identifier")
            value = int(Fraction(lo))
            rng = TYPE_RANGE[dtype.native]
            if not rng[0] <= value <= rng[1]:
                raise RteError(f"{where}: enumeration literal {symbol} = {value} does not fit {dtype.native}")
            literals.append((symbol, value))
        if len({s for s, _ in literals}) != len(literals):
            raise RteError(f"{where}: enumeration {text(method, 'SHORT-NAME')} names a literal twice")
        dtype.literals = tuple(literals)
        return
    numerator, denominator, unit = _compu_method(elements, props, where)
    if (tuple(numerator) != (0, 1) or denominator != 1) and TYPE_RANGE.get(dtype.native) is None:
        raise RteError(f"{where}: a floating point value has no fixed-point scaling")
    dtype.numerator, dtype.denominator, dtype.unit = tuple(numerator), denominator, unit


def _copy_type(dtype: DataType, path: str, name: str, target: "DataType | None") -> DataType:
    return DataType(path, name, dtype.native, dtype.category, dtype.element, dtype.size, list(dtype.fields),
                    target, tuple(dtype.numerator), dtype.denominator, dtype.unit, tuple(dtype.literals))


def _array_of(elements, node, path: str, name: str, seen) -> DataType:
    """ARRAY node (implementation type or record member): one sub-element with a fixed ARRAY-SIZE."""
    subs = node.findall("ar:SUB-ELEMENTS/ar:IMPLEMENTATION-DATA-TYPE-ELEMENT", NS)
    if len(subs) != 1:
        raise RteError(f"{path}: an array needs exactly one sub-element")
    size = text(subs[0], "ARRAY-SIZE")
    if size is None or not size.isdigit() or int(size) < 1:
        raise RteError(f"{path}: array without fixed ARRAY-SIZE")
    if (text(subs[0], "ARRAY-SIZE-SEMANTICS") or "FIXED-SIZE") != "FIXED-SIZE":
        raise RteError(f"{path}: only fixed-size arrays are supported (no variable-size arrays)")
    element = _type_of_element(elements, subs[0], f"{path}[]", seen)
    if element.category == "ARRAY" and not element.name:
        raise RteError(f"{path}: array of anonymous arrays, give the element (row) an implementation data type")
    return DataType(path, name, None, "ARRAY", element, int(size))


def _type_of_element(elements, sub, path: str, seen) -> DataType:
    """Type of an IMPLEMENTATION-DATA-TYPE-ELEMENT (array element or record member)."""
    category = text(sub, "CATEGORY")
    props = sub.find(PROPS, NS)
    if category == "TYPE_REFERENCE":
        ref = _ref(props, "IMPLEMENTATION-DATA-TYPE-REF")
        if ref is None:
            raise RteError(f"{path}: type reference without IMPLEMENTATION-DATA-TYPE-REF")
        return resolve_type(elements, ref, seen)
    if category == "VALUE":
        native = _base_native(elements, props, path)
        dtype = DataType(path, native, native)
        _scalar_compu(elements, props, dtype, path)
        return dtype
    if category == "ARRAY":
        return _array_of(elements, sub, path, "", seen)
    raise RteError(f"{path}: {category} as array element or record member is not supported (use a named "
                   f"implementation data type)")


def resolve_type(elements, path, seen=()):
    """Implementation data type: VALUE, TYPE_REFERENCE, ARRAY (fixed size) and STRUCTURE (RTE-04)."""
    node = elements.get(path)
    if node is None:
        raise RteError(f"data type {path} not found")
    kind = local(node.tag)
    name = text(node, "SHORT-NAME")
    if kind != "IMPLEMENTATION-DATA-TYPE":
        raise RteError(f"{path}: {kind} is not supported here, only implementation data types")
    if path in seen:
        raise RteError(f"{path}: cyclic type reference")
    seen = seen + (path,)
    category = text(node, "CATEGORY")
    props = node.find(PROPS, NS)
    if category == "TYPE_REFERENCE":
        ref = _ref(props, "IMPLEMENTATION-DATA-TYPE-REF")
        if ref is None:
            raise RteError(f"{path}: type reference without IMPLEMENTATION-DATA-TYPE-REF")
        inner = resolve_type(elements, ref, seen)
        dtype = _copy_type(inner, path, name, inner)
        if not dtype.composite:
            _scalar_compu(elements, props, dtype, path)
        return dtype
    if category == "VALUE":
        dtype = DataType(path, name, _base_native(elements, props, path))
        _scalar_compu(elements, props, dtype, path)
        return dtype
    if category == "ARRAY":
        return _array_of(elements, node, path, name, seen)
    if category == "STRUCTURE":
        subs = node.findall("ar:SUB-ELEMENTS/ar:IMPLEMENTATION-DATA-TYPE-ELEMENT", NS)
        if not subs:
            raise RteError(f"{path}: record without members")
        fields = [(text(s, "SHORT-NAME"), _type_of_element(elements, s, f"{path}.{text(s, 'SHORT-NAME')}", seen))
                  for s in subs]
        if len({n for n, _ in fields}) != len(fields):
            raise RteError(f"{path}: record names a member twice")
        for member, ftype in fields:
            if ftype.category == "STRUCTURE" and not ftype.name:
                raise RteError(f"{path}.{member}: anonymous nested record, give it an implementation data type")
        return DataType(path, name, None, "STRUCTURE", fields=fields)
    raise RteError(f"{path}: category {category} is not supported (VALUE, TYPE_REFERENCE, ARRAY, STRUCTURE)")


def _implementation_of(elements, path: str, where: str) -> str:
    """Implementation data type mapped to an application data type (DATA-TYPE-MAPPING-SET)."""
    found = []
    for mapping in (n for n in elements.values() if local(n.tag) == "DATA-TYPE-MAPPING-SET"):
        for m in mapping.findall("ar:DATA-TYPE-MAPS/ar:DATA-TYPE-MAP", NS):
            if _ref(m, "APPLICATION-DATA-TYPE-REF") == path:
                found.append(_ref(m, "IMPLEMENTATION-DATA-TYPE-REF"))
    if not found or found[0] is None:
        raise RteError(f"{where}: application data type {path} has no implementation type (DATA-TYPE-MAPPING-SET)")
    return found[0]


def _apply_application(elements, node, dtype: DataType, where: str) -> DataType:
    """Scaling, literals and unit of an application data type onto a (fresh) implementation type, recursively."""
    kind = local(node.tag)
    if kind == "APPLICATION-PRIMITIVE-DATA-TYPE":
        if dtype.composite:
            raise RteError(f"{where}: application type {text(node, 'SHORT-NAME')} is a primitive, its "
                           f"implementation {dtype.name} is not")
        _scalar_compu(elements, node.find(PROPS, NS), dtype, where)
    elif kind == "APPLICATION-ARRAY-DATA-TYPE":
        if dtype.category != "ARRAY":
            raise RteError(f"{where}: application array {text(node, 'SHORT-NAME')} is mapped to {dtype.name}, "
                           f"which is no array")
        element = node.find("ar:ELEMENT", NS)
        count = text(element, "MAX-NUMBER-OF-ELEMENTS") if element is not None else None
        if count is not None and int(count) != dtype.size:
            raise RteError(f"{where}: application array {text(node, 'SHORT-NAME')} has {count} elements, its "
                           f"implementation {dtype.name} {dtype.size}")
        ref = _ref(element, "TYPE-TREF")
        if ref is not None and elements.get(ref) is not None:
            dtype.element = _copy_type(dtype.element, dtype.element.path, dtype.element.name, dtype.element.target)
            dtype.element = _apply_application(elements, elements[ref], dtype.element, f"{where}[]")
    elif kind == "APPLICATION-RECORD-DATA-TYPE":
        if dtype.category != "STRUCTURE":
            raise RteError(f"{where}: application record {text(node, 'SHORT-NAME')} is mapped to {dtype.name}, "
                           f"which is no record")
        members = {text(e, "SHORT-NAME"): e for e in node.findall("ar:ELEMENTS/ar:APPLICATION-RECORD-ELEMENT", NS)}
        if set(members) != {n for n, _ in dtype.fields}:
            raise RteError(f"{where}: members of the application record {text(node, 'SHORT-NAME')} "
                           f"({', '.join(members)}) differ from {dtype.name} ({', '.join(n for n, _ in dtype.fields)})")
        fields = []
        for fname, ftype in dtype.fields:
            ref = _ref(members[fname], "TYPE-TREF")
            ftype = _copy_type(ftype, ftype.path, ftype.name, ftype.target)
            if ref is not None and elements.get(ref) is not None:
                ftype = _apply_application(elements, elements[ref], ftype, f"{where}.{fname}")
            fields.append((fname, ftype))
        dtype.fields = fields
    else:
        raise RteError(f"{where}: {kind} is not supported")
    return dtype


def resolve_prototype_type(elements, path: str, where: str) -> tuple[DataType, bool]:
    """Type of a data prototype (TYPE-TREF): implementation type, or application type mapped to its implementation.

    Returns (type, physical): physical is True for an application type, whose init values are physical values.
    """
    node = elements.get(path)
    if node is None:
        raise RteError(f"{where}: data type {path} not found")
    if local(node.tag).startswith("APPLICATION-"):
        impl = _implementation_of(elements, path, where)
        try:
            dtype = resolve_type(elements, impl)
        except RteError as e:
            raise RteError(f"{where}: {e}") from None
        return _apply_application(elements, node, dtype, where), True
    try:
        return resolve_type(elements, path), False
    except RteError as e:
        raise RteError(f"{where}: {e}") from None


def type_difference(a: DataType, b: DataType, path: str = "") -> str:
    """First difference of two types that do not match (for the message of a connection)."""
    at = path or "type"
    if a.category != b.category:
        return f"{at}: {a.category.lower()} {a.name} vs {b.category.lower()} {b.name}"
    if a.category == "ARRAY":
        if a.size != b.size:
            return f"{at}: {a.size} vs {b.size} elements"
        return type_difference(a.element, b.element, f"{path}[]")
    if a.category == "STRUCTURE":
        if a.name != b.name:
            return f"{at}: record {a.name} vs record {b.name} (records are copied as C types, the names must agree)"
        names_a, names_b = [n for n, _ in a.fields], [n for n, _ in b.fields]
        if names_a != names_b:
            return f"{at}: members {', '.join(names_a)} vs {', '.join(names_b)}"
        for (n, ta), (_, tb) in zip(a.fields, b.fields):
            if ta.signature() != tb.signature():
                return type_difference(ta, tb, f"{path}.{n}" if path else n)
    return f"{at}: {a.describe()} vs {b.describe()}"


def dimension_mismatch(required: DataType, provided: DataType) -> str:
    """RTE-25: text for a connection whose ports differ in their number of elements ('' if they do not).

    Only for scalars and arrays of scalars with the same element type - a different element type or a record is
    the general type message. Both ports need the same dimension: the RTE copies whole buffers, a shorter reader
    would lose elements and a longer one read past the writer's buffer."""
    a, b = port_dimension(required), port_dimension(provided)
    if a is None or b is None or a == b:
        return ""
    elem_a = required.element if required.category == "ARRAY" else required
    elem_b = provided.element if provided.category == "ARRAY" else provided
    if elem_a.signature() != elem_b.signature():
        return ""
    return (f"dimension {a} of the required port does not match dimension {b} of the provided port "
            f"({required.describe()} vs {provided.describe()}); a connection needs the same number of elements "
            f"on both sides - set the same dimension in swc.yaml (dimension) or in the model (PortDimensions)")


def _literal_value(dtype: DataType, literal: str, where: str) -> int:
    if dtype.native == "boolean" and literal.upper() in ("TRUE", "FALSE"):
        return 1 if literal.upper() == "TRUE" else 0
    for symbol, value in dtype.literals:
        if symbol == literal:
            return value
    known = ", ".join(s for s, _ in dtype.literals) or "none"
    raise RteError(f"{where}: init value {literal} is no literal of {dtype.name} (literals: {known})")


def _scalar_raw(dtype: DataType, value, physical: bool, where: str):
    """Stored value of one init value: physical values of fixed-point types are scaled and rounded."""
    if isinstance(value, str):
        return _literal_value(dtype, value, where)
    rng = TYPE_RANGE[dtype.native]
    if rng is None:
        return float(value)
    exact = Fraction(str(value))
    if physical and dtype.scaled:
        exact = (exact * dtype.denominator - dtype.numerator[0]) / dtype.numerator[1]
        stored = math.floor(exact + Fraction(1, 2))
    elif exact.denominator != 1:
        raise RteError(f"{where}: init value {value} is no whole number, but {dtype.name} is {dtype.native}")
    else:
        stored = int(exact)
    if not rng[0] <= stored <= rng[1]:
        raise RteError(f"{where}: init value {value} does not fit {dtype.describe()} (stored {stored})")
    return stored


def _spec_node(elements, spec, where: str):
    """Follows CONSTANT-REFERENCE to the value specification of the constant."""
    seen = 0
    while spec is not None and local(spec.tag) == "CONSTANT-REFERENCE":
        const = elements.get(_ref(spec, "CONSTANT-REF") or "")
        if const is None or seen > 8:
            raise RteError(f"{where}: init value: constant {_ref(spec, 'CONSTANT-REF')} not found")
        holder = const.find("ar:VALUE-SPEC", NS)
        spec = holder[0] if holder is not None and len(holder) else None
        seen += 1
    return spec


def parse_init(elements, spec, dtype: DataType, physical: bool, where: str):
    """Stored init value of a data element from its value specification: a number, or a list (array, record).

    NUMERICAL-VALUE-SPECIFICATION (physical for an application type), APPLICATION-VALUE-SPECIFICATION (physical,
    V or VT), TEXT-VALUE-SPECIFICATION (literal), ARRAY-/RECORD-VALUE-SPECIFICATION, CONSTANT-REFERENCE.
    No specification: zero (the implementation's default).
    """
    spec = _spec_node(elements, spec, where)
    if spec is None:
        return zero_value(dtype)
    kind = local(spec.tag)
    if dtype.category == "ARRAY":
        if kind == "ARRAY-VALUE-SPECIFICATION":
            items = list(spec.find("ar:ELEMENTS", NS) if spec.find("ar:ELEMENTS", NS) is not None else [])
            if len(items) != dtype.size:
                raise RteError(f"{where}: init value has {len(items)} element(s), {dtype.describe()} has "
                               f"{dtype.size}")
            return [parse_init(elements, item, dtype.element, physical, f"{where}[{i}]")
                    for i, item in enumerate(items)]
        if kind == "APPLICATION-VALUE-SPECIFICATION" and not dtype.element.composite:
            values = _app_values(spec)
            if len(values) != dtype.size:
                raise RteError(f"{where}: init value has {len(values)} element(s), {dtype.describe()} has "
                               f"{dtype.size}")
            return [_scalar_raw(dtype.element, v, True, f"{where}[{i}]") for i, v in enumerate(values)]
        raise RteError(f"{where}: {kind} cannot initialise the array {dtype.describe()}")
    if dtype.category == "STRUCTURE":
        if kind != "RECORD-VALUE-SPECIFICATION":
            raise RteError(f"{where}: {kind} cannot initialise the record {dtype.describe()}")
        items = list(spec.find("ar:FIELDS", NS) if spec.find("ar:FIELDS", NS) is not None else [])
        if len(items) != len(dtype.fields):
            raise RteError(f"{where}: init value has {len(items)} field(s), {dtype.describe()} has "
                           f"{len(dtype.fields)}")
        return [parse_init(elements, item, ftype, physical, f"{where}.{fname}")
                for item, (fname, ftype) in zip(items, dtype.fields)]
    if kind == "NUMERICAL-VALUE-SPECIFICATION":
        return _scalar_raw(dtype, _number(text(spec, "VALUE") or "0", where), physical, where)
    if kind == "TEXT-VALUE-SPECIFICATION":
        return _literal_value(dtype, text(spec, "VALUE") or "", where)
    if kind == "APPLICATION-VALUE-SPECIFICATION":
        values = _app_values(spec)
        if len(values) != 1:
            raise RteError(f"{where}: init value has {len(values)} values, {dtype.name} is a scalar")
        return _scalar_raw(dtype, values[0], True, where)
    raise RteError(f"{where}: {kind} is not supported as init value")


def _app_values(spec) -> list:
    """V (numbers) and VT (literals) of an APPLICATION-VALUE-SPECIFICATION in document order."""
    phys = spec.find("ar:SW-VALUE-CONT/ar:SW-VALUES-PHYS", NS)
    values = []
    for v in list(phys) if phys is not None else []:
        if local(v.tag) == "V":
            values.append(_number(v.text.strip(), "APPLICATION-VALUE-SPECIFICATION"))
        elif local(v.tag) == "VT":
            values.append(v.text.strip())
    return values


def _number(value: str, where: str) -> Fraction:
    try:
        return Fraction(value.strip())
    except ValueError:
        raise RteError(f"{where}: {value!r} is no number") from None


def zero_value(dtype: DataType):
    if dtype.category == "ARRAY":
        return [zero_value(dtype.element) for _ in range(dtype.size)]
    if dtype.category == "STRUCTURE":
        return [zero_value(t) for _, t in dtype.fields]
    return 0.0 if TYPE_RANGE[dtype.native] is None else 0


def init_literal(native: str, value) -> str:
    """C text of one stored scalar value of a buffer initializer (no suffix: templates may append one)."""
    if TYPE_RANGE[native] is None:
        return str(int(value)) if float(value).is_integer() else repr(float(value))
    if native == "sint32" and int(value) == -(2**31):
        return "(-2147483647 - 1)"
    return str(int(value))


def _c_definition(t: DataType) -> tuple:
    """What the C definition of a named type consists of (two components defining one name must agree)."""
    if t.target is not None:
        return ("typedef", t.target.name)
    if t.category == "ARRAY":
        return ("array", t.size, t.element.name)
    if t.category == "STRUCTURE":
        return ("record",) + tuple(ft.decl(n) for n, ft in t.fields)
    return ("value", t.native)


def collect_types(components: dict) -> list:
    """Named implementation data types of all ports and parameters, each after the types it uses (RTE-04).

    One C name has one definition: two components with the same type name must describe the same layout (and,
    if both carry them, the same enumeration literals); otherwise the ECU cannot be generated.
    """
    registry: dict[str, tuple] = {}
    order: list[str] = []

    def visit(t: DataType, owner: str):
        if t.category == "ARRAY":
            visit(t.element, owner)
        for _, ft in t.fields:
            visit(ft, owner)
        if t.target is not None:
            visit(t.target, owner)
        if not t.name or t.name in PLATFORM_TYPES:
            return
        known = registry.get(t.name)
        if known is None:
            registry[t.name] = (t, owner)
            order.append(t.name)
            return
        other, other_owner = known
        if _c_definition(other) != _c_definition(t) or (other.literals and t.literals
                                                       and other.literals != t.literals):
            raise RteError(f"components: type {t.name} is defined differently by {other_owner} "
                           f"({other.describe()}) and {owner} ({t.describe()})")
        if t.literals and not other.literals:
            registry[t.name] = (t, owner)

    for comp in components.values():
        for key, port in comp.ports.items():
            visit(port.type, f"{comp.name}.{key}")
        for key, prm in comp.parameters.items():
            visit(prm.type, f"{comp.name}.{key}")
    return [registry[name][0] for name in order]


def render_type_definitions(types: list) -> str:
    """C definitions of the named types for Rte_Type.h: typedefs, enumeration literals, arrays, records."""
    lines: list[str] = []
    symbols: dict[str, tuple] = {}
    for t in types:
        if t.target is not None:
            lines.append(f"typedef {t.target.name} {t.name};")
        elif t.category == "ARRAY":
            lines.append(f"typedef {t.element.decl(f'{t.name}[{t.size}]')};")
        elif t.category == "STRUCTURE":
            guard = f"DEFINED_TYPEDEF_FOR_{t.name}_"
            lines += [f"#ifndef {guard}", f"#define {guard}", "typedef struct", "{"]
            lines += [f"    {ft.decl(n)};" for n, ft in t.fields]
            lines += [f"}} {t.name};", "#endif"]
        else:
            lines.append(f"typedef {t.native} {t.name};")
        for symbol, value in t.literals:
            known = symbols.get(symbol)
            if known is not None and known[1] != value:
                raise RteError(f"components: enumeration literal {symbol} is {known[1]} in {known[0]} and {value} "
                               f"in {t.name}")
            symbols[symbol] = (t.name, value)
            number = f"{value}U" if value >= 0 else f"({value})"
            lines += [f"#ifndef {symbol}", f"#define {symbol} (({t.name}){number})", "#endif"]
    return "\n".join(lines)


def c_initializer(dtype: DataType, value) -> str:
    if dtype.category == "ARRAY":
        return "{" + ", ".join(c_initializer(dtype.element, v) for v in value) + "}"
    if dtype.category == "STRUCTURE":
        return "{" + ", ".join(c_initializer(t, v) for (_, t), v in zip(dtype.fields, value)) + "}"
    return init_literal(dtype.native, value)


def _compu_method(elements, props, where: str) -> tuple[tuple, int, str]:
    """Linear scaling of an application data type: (numerator, denominator, unit) of COMPU-INTERNAL-TO-PHYS."""
    if props is None:
        return (0, 1), 1, ""
    ref = props.find("ar:COMPU-METHOD-REF", NS)
    if ref is None:
        return (0, 1), 1, ""
    method = elements.get(ref.text.strip())
    if method is None:
        raise RteError(f"{where}: compu method {ref.text.strip()} not found")
    unit_ref = method.find("ar:UNIT-REF", NS)
    unit_node = elements.get(unit_ref.text.strip()) if unit_ref is not None else None
    unit = ""
    if unit_node is not None:
        unit = text(unit_node, "DISPLAY-NAME") or text(unit_node, "SHORT-NAME") or ""
        unit = "" if unit == "NoUnit" else unit
    category = text(method, "CATEGORY")
    if category == "IDENTICAL":
        return (0, 1), 1, unit
    if category != "LINEAR":
        raise RteError(f"{where}: compu method {text(method, 'SHORT-NAME')} of category {category} is not supported "
                       f"(IDENTICAL and LINEAR, i.e. fixed point with slope and bias)")
    scales = method.findall("ar:COMPU-INTERNAL-TO-PHYS/ar:COMPU-SCALES/ar:COMPU-SCALE", NS)
    if len(scales) != 1:
        raise RteError(f"{where}: compu method {text(method, 'SHORT-NAME')} needs exactly one COMPU-SCALE")
    coeffs = scales[0].find("ar:COMPU-RATIONAL-COEFFS", NS)
    num = [Fraction(v.text.strip()) for v in coeffs.findall("ar:COMPU-NUMERATOR/ar:V", NS)] if coeffs is not None else []
    den = [Fraction(v.text.strip()) for v in coeffs.findall("ar:COMPU-DENOMINATOR/ar:V", NS)] if coeffs is not None else []
    if len(num) != 2 or len(den) != 1 or den[0] == 0 or num[1] == 0:
        raise RteError(f"{where}: compu method {text(method, 'SHORT-NAME')} is not linear "
                       f"(expected numerator [offset, factor] and one denominator)")
    numerator, d = rational_coefficients(num[0], num[1], den[0], f"{where}: scaling of {text(method, 'SHORT-NAME')}")
    return numerator, d, unit


def rational_coefficients(offset: Fraction, factor: Fraction, denominator: Fraction, what: str) -> tuple[tuple, int]:
    """Integer coefficients of physical = (offset + factor * stored) / denominator for the target table (A2L
    RAT_FUNC): scaled by the common denominator, reduced, denominator positive, each fitting 32 bits."""
    common = reduce(lambda a, b: a * b // gcd(a, b), [f.denominator for f in (offset, factor, denominator)], 1)
    n0, n1, d = (int(f * common) for f in (offset, factor, denominator))
    g = reduce(gcd, (abs(n0), abs(n1), abs(d)))
    n0, n1, d = n0 // g, n1 // g, d // g
    if d < 0:
        n0, n1, d = -n0, -n1, -d
    if max(abs(n0), abs(n1), d) > 2**31 - 1:
        raise RteError(f"{what} does not fit 32-bit coefficients")
    return (n0, n1), d


def resolve_parameter_type(elements, path: str, where: str):
    """Type of a parameter data prototype: (element DataType, dimension, numerator, denominator, unit).

    Implementation data types (VALUE, TYPE_REFERENCE, ARRAY of those) and application primitive types with a
    linear COMPU-METHOD (fixed point: Embedded Coder maps fixdt(...) parameters this way, the implementation type
    comes from the DATA-TYPE-MAPPING-SET).
    """
    node = elements.get(path)
    if node is None:
        raise RteError(f"{where}: data type {path} not found")
    kind = local(node.tag)
    if kind == "APPLICATION-PRIMITIVE-DATA-TYPE":
        impl = None
        for mapping in (n for n in elements.values() if local(n.tag) == "DATA-TYPE-MAPPING-SET"):
            for m in mapping.findall("ar:DATA-TYPE-MAPS/ar:DATA-TYPE-MAP", NS):
                app = m.find("ar:APPLICATION-DATA-TYPE-REF", NS)
                if app is not None and app.text.strip() == path:
                    impl = m.find("ar:IMPLEMENTATION-DATA-TYPE-REF", NS).text.strip()
        if impl is None:
            raise RteError(f"{where}: application data type {path} has no implementation type "
                           f"(DATA-TYPE-MAPPING-SET)")
        props = node.find("ar:SW-DATA-DEF-PROPS/ar:SW-DATA-DEF-PROPS-VARIANTS/ar:SW-DATA-DEF-PROPS-CONDITIONAL", NS)
        dtype, dim, _, _, _ = resolve_parameter_type(elements, impl, where)
        numerator, denominator, unit = _compu_method(elements, props, where)
        return dtype, dim, numerator, denominator, unit
    if kind == "IMPLEMENTATION-DATA-TYPE" and text(node, "CATEGORY") == "ARRAY":
        subs = node.findall("ar:SUB-ELEMENTS/ar:IMPLEMENTATION-DATA-TYPE-ELEMENT", NS)
        if len(subs) != 1:
            raise RteError(f"{where}: array type {path} must have exactly one sub-element")
        sub = subs[0]
        size = text(sub, "ARRAY-SIZE")
        if size is None or not size.isdigit() or int(size) < 1:
            raise RteError(f"{where}: array type {path} has no fixed ARRAY-SIZE")
        if (text(sub, "ARRAY-SIZE-SEMANTICS") or "FIXED-SIZE") != "FIXED-SIZE":
            raise RteError(f"{where}: array type {path}: only fixed-size arrays are supported")
        ref = sub.find(".//ar:IMPLEMENTATION-DATA-TYPE-REF", NS)
        if ref is None:
            raise RteError(f"{where}: array type {path}: element type missing")
        dtype, inner, numerator, denominator, unit = resolve_parameter_type(elements, ref.text.strip(), where)
        if inner != 1:
            raise RteError(f"{where}: array type {path}: arrays of arrays (matrices) are not supported")
        return dtype, int(size), numerator, denominator, unit
    try:
        return resolve_type(elements, path), 1, (0, 1), 1, ""
    except RteError as e:
        raise RteError(f"{where}: {e}") from None


def parse_parameters(comp: Component, elements, port, pname: str, interface):
    """Parameter data prototypes of a ParameterReceiver port (R-PORT-PROTOTYPE with a PARAMETER-INTERFACE)."""
    iname = text(interface, "SHORT-NAME")
    for pd in interface.findall("ar:PARAMETERS/ar:PARAMETER-DATA-PROTOTYPE", NS):
        ename = text(pd, "SHORT-NAME")
        where = f"{comp.name}.{pname}.{ename}"
        tref = pd.find("ar:TYPE-TREF", NS)
        if tref is None:
            raise RteError(f"{where}: parameter without type (interface {iname}); Embedded Coder leaves the type "
                           f"out when the model does not use the parameter - use it or remove it from the port")
        dtype, dim, numerator, denominator, unit = resolve_parameter_type(elements, tref.text.strip(), where)
        if dtype.composite:
            raise RteError(f"{where}: {dtype.describe()} as calibration parameter is not supported (scalars and "
                           f"arrays of scalars only; records and matrices are open, RTE-04)")
        if dtype.native in ("float32", "float64") and (tuple(numerator) != (0, 1) or denominator != 1):
            raise RteError(f"{where}: a floating point parameter has no fixed-point scaling")
        access = pd.find(".//ar:SW-CALIBRATION-ACCESS", NS)
        comp.parameters[f"{pname}.{ename}"] = Parameter(
            comp.name, pname, ename, dtype, dim, tuple(numerator), denominator, unit,
            access.text.strip() if access is not None and access.text else "READ-WRITE")


def parse_component(name: str, arxml_dir: Path) -> Component:
    elements = load_arxml(arxml_dir)
    swc = next(
        (n for p, n in elements.items() if local(n.tag).endswith("SW-COMPONENT-TYPE") and text(n, "SHORT-NAME") == name),
        None,
    )
    if swc is None:
        raise RteError(f"component {name} not found in {arxml_dir}")
    comp = Component(name)

    for port in swc.find("ar:PORTS", NS):
        kind = local(port.tag)
        pname = text(port, "SHORT-NAME")
        if kind == "R-PORT-PROTOTYPE":
            direction, iref, specs = "in", "REQUIRED-INTERFACE-TREF", "REQUIRED-COM-SPECS"
        elif kind == "P-PORT-PROTOTYPE":
            direction, iref, specs = "out", "PROVIDED-INTERFACE-TREF", "PROVIDED-COM-SPECS"
        else:
            raise RteError(f"{name}.{pname}: {kind} is not supported")
        ipath = port.find(f"ar:{iref}", NS)
        interface = elements.get(ipath.text.strip())
        if interface is not None and local(interface.tag) == "PARAMETER-INTERFACE":
            if direction != "in":
                raise RteError(f"{name}.{pname}: a parameter interface on a provided port is not supported "
                               f"(parameter components are replaced by the data file <Swc>_data.m)")
            parse_parameters(comp, elements, port, pname, interface)
            continue
        if interface is not None and local(interface.tag) == "MODE-SWITCH-INTERFACE":
            if direction != "in":
                raise RteError(f"{name}.{pname}: a provided mode port (mode manager in a component, Rte_Switch) is "
                               f"not supported; the ECU modes come from the mode manager {ECU_MODE_TARGET}")
            mode_port = parse_mode_port(elements, interface, pname, f"{name}.{pname}")
            comp.mode_ports[mode_port.key] = mode_port
            continue
        if interface is None or local(interface.tag) != "SENDER-RECEIVER-INTERFACE":
            raise RteError(f"{name}.{pname}: only sender-receiver, parameter and mode switch interfaces are "
                           f"supported")
        data_elements = interface.findall("ar:DATA-ELEMENTS/ar:VARIABLE-DATA-PROTOTYPE", NS)
        inits = {}
        for spec in list(port.find(f"ar:{specs}", NS) if port.find(f"ar:{specs}", NS) is not None else []):
            ref = spec.find("ar:DATA-ELEMENT-REF", NS)
            value = spec.find("ar:INIT-VALUE", NS)
            if ref is not None and value is not None and len(value):
                inits[ref.text.strip().rsplit("/", 1)[1]] = value[0]
        for de in data_elements:
            ename = text(de, "SHORT-NAME")
            where = f"{name}.{pname}.{ename}"
            tref = _ref(de, "TYPE-TREF")
            if tref is None:
                raise RteError(f"{where}: data element without type")
            # RTE-04: scalars, arrays, records, enumerations and fixed point (application types with compu methods)
            dtype, physical = resolve_prototype_type(elements, tref, where)
            value = parse_init(elements, inits.get(ename), dtype, physical, f"{where}: init value")
            key = f"{pname}.{ename}"
            comp.ports[key] = Port(pname, direction, text(interface, "SHORT-NAME"), ename, dtype,
                                   c_initializer(dtype, value), init_declared=inits.get(ename) is not None)

    behavior = swc.find("ar:INTERNAL-BEHAVIORS/ar:SWC-INTERNAL-BEHAVIOR", NS)
    periods = {}
    init_events = set()
    mode_events: dict[str, list[ModeEvent]] = {}
    events = behavior.find("ar:EVENTS", NS)
    for ev in list(events) if events is not None else []:
        target = ev.find("ar:START-ON-EVENT-REF", NS).text.strip().rsplit("/", 1)[1]
        kind = local(ev.tag)
        if ev.find("ar:DISABLED-MODE-IREFS", NS) is not None:
            raise RteError(f"{name}.{target}: {kind} {text(ev, 'SHORT-NAME')} has DISABLED-MODE-IREFS; mode "
                           f"disabling is not supported (RTE-08), the runnable can read the mode with Rte_Mode")
        if kind == "SWC-MODE-SWITCH-EVENT":
            mode_events.setdefault(target, []).append(parse_mode_event(ev, comp, f"{name}.{target}"))
        elif kind == "TIMING-EVENT":
            seconds = float(text(ev, "PERIOD"))
            ms = round(seconds * 1000)
            if ms <= 0 or abs(ms - seconds * 1000) > 1e-6:
                raise RteError(f"{name}.{target}: period {seconds} s is not a whole number of milliseconds")
            periods[target] = ms
        elif kind == "INIT-EVENT":
            init_events.add(target)
        else:
            raise RteError(f"{name}.{target}: {kind} is not supported (timing, init and mode switch events only)")
    for r in behavior.find("ar:RUNNABLES", NS):
        rname = text(r, "SHORT-NAME")
        symbol = text(r, "SYMBOL") or rname
        if rname in mode_events:
            # RTE-08: a runnable started by mode switches has no period and is no init runnable
            if rname in periods or rname in init_events:
                raise RteError(f"{name}.{rname}: a runnable started by a mode switch event and by a "
                               f"{'timing' if rname in periods else 'init'} event is not supported; split it "
                               f"into two runnables")
            runnable = Runnable(rname, symbol, None, mode_events=mode_events[rname])
        elif rname in periods:
            runnable = Runnable(rname, symbol, periods[rname])
        elif rname in init_events or rname == f"{name}_Init":
            runnable = Runnable(rname, symbol, None)
        else:
            raise RteError(f"{name}.{rname}: runnable without timing, init or mode switch event")
        # RTE-08: mode access points (Rte_Mode)
        for point in r.findall("ar:MODE-ACCESS-POINTS/ar:MODE-ACCESS-POINT", NS):
            port_ref = point.find(".//ar:CONTEXT-R-PORT-REF", NS)
            group_ref = point.find(".//ar:TARGET-MODE-GROUP-REF", NS)
            if port_ref is None or group_ref is None:
                raise RteError(f"{name}.{rname}: mode access point without CONTEXT-R-PORT-REF and "
                               f"TARGET-MODE-GROUP-REF")
            key = f"{port_ref.text.strip().rsplit('/', 1)[1]}.{group_ref.text.strip().rsplit('/', 1)[1]}"
            if key not in comp.mode_ports:
                raise RteError(f"{name}.{rname}: mode access point to unknown mode port {key}")
            runnable.mode_accesses.add(key)
            comp.mode_access_points = True
        if r.find("ar:MODE-SWITCH-POINTS", NS) is not None and len(r.find("ar:MODE-SWITCH-POINTS", NS)):
            raise RteError(f"{name}.{rname}: mode switch points (Rte_Switch) are not supported; the ECU modes come "
                           f"from the mode manager {ECU_MODE_TARGET}")
        by_values = r.find("ar:DATA-RECEIVE-POINT-BY-VALUES", NS)
        if by_values is not None and len(by_values):
            raise RteError(f"{name}.{rname}: DATA-RECEIVE-POINT-BY-VALUES (Rte_DRead) is not supported; use explicit "
                           f"(DATA-RECEIVE-POINT-BY-ARGUMENTS, Rte_Read) or implicit access (DATA-READ-ACCESSS, "
                           f"Rte_IRead)")
        # explicit (DATA-RECEIVE-POINT-BY-ARGUMENTS, DATA-SEND-POINTS) and implicit access points (DATA-READ-ACCESSS,
        # DATA-WRITE-ACCESSS; RTE-09: Rte_IRead/Rte_IWrite/Rte_IWriteRef on a copy per runnable)
        for group, mode in {**EXPLICIT_ACCESS_GROUPS, **IMPLICIT_ACCESS_GROUPS}.items():
            for iref in r.findall(f"ar:{group}/ar:VARIABLE-ACCESS/ar:ACCESSED-VARIABLE/ar:AUTOSAR-VARIABLE-IREF", NS):
                port_ref = iref.find("ar:PORT-PROTOTYPE-REF", NS)
                data_ref = iref.find("ar:TARGET-DATA-PROTOTYPE-REF", NS)
                if port_ref is None or data_ref is None:
                    continue
                key = f"{port_ref.text.strip().rsplit('/', 1)[1]}.{data_ref.text.strip().rsplit('/', 1)[1]}"
                if key not in comp.ports:
                    raise RteError(f"{name}.{rname}: access point to unknown port element {key}")
                runnable.accesses.add(key)
                comp.access_points = True
                if group not in IMPLICIT_ACCESS_GROUPS:
                    runnable.explicit.add(key)
                    continue
                if (mode == "r") != (comp.ports[key].direction == "in"):
                    kind = "required" if comp.ports[key].direction == "in" else "provided"
                    raise RteError(f"{name}.{rname}: {group} of {key}: implicit "
                                   f"{'read' if mode == 'r' else 'write'} access needs a "
                                   f"{'required' if mode == 'r' else 'provided'} port, {key} is a {kind} port")
                runnable.implicit[key] = mode
        # RTE-03: parameter accesses (read only, no task copies needed; kept for the parameter table)
        for iref in r.findall("ar:PARAMETER-ACCESSS/ar:PARAMETER-ACCESS/ar:ACCESSED-PARAMETER/"
                              "ar:AUTOSAR-PARAMETER-IREF", NS):
            port_ref = iref.find("ar:PORT-PROTOTYPE-REF", NS)
            data_ref = iref.find("ar:TARGET-DATA-PROTOTYPE-REF", NS)
            if port_ref is None or data_ref is None:
                continue
            key = f"{port_ref.text.strip().rsplit('/', 1)[1]}.{data_ref.text.strip().rsplit('/', 1)[1]}"
            if key not in comp.parameters:
                raise RteError(f"{name}.{rname}: access to unknown parameter {key}")
            comp.parameters[key].runnables.add(rname)
        comp.runnables.append(runnable)
    if not comp.access_points:
        # no access points in the ARXML: assume every periodic runnable accesses every port
        for runnable in comp.runnables:
            if runnable.period_ms or runnable.mode_events:
                runnable.accesses = set(comp.ports)
    if not comp.mode_access_points:
        # RTE-08: no mode access points either: every task runnable may read every mode port
        for runnable in comp.runnables:
            if runnable.period_ms or runnable.mode_events:
                runnable.mode_accesses = set(comp.mode_ports)
    return comp


def c_component_file(swc_dir: Path) -> Path:
    """Description of a hand-written C component (RTE-20): <swc_dir>/swc.yaml."""
    return Path(swc_dir) / swc_yaml.FILE_NAME


SAFETY_ORDER = ("qm", "asil_a", "asil_b", "asil_c", "asil_d")


def component_safety(swc_dir: Path) -> str:
    """RTE-27: safety class of a component - the highest `safety` of the parameters in its swc.yaml ("qm" without one).

    The component description has no class of its own; a component with a safety-relevant calibration parameter
    (SYS-76) carries a safety function. Simulink and C components alike; a file that cannot be read counts as qm here
    (its own check reports it). An unknown class counts as safety relevant."""
    file = c_component_file(swc_dir)
    try:
        tree = yaml.safe_load(file.read_text(encoding="utf-8")) if file.is_file() else None
    except (OSError, yaml.YAMLError):
        return "qm"
    parameters = tree.get("parameters") if isinstance(tree, dict) else None
    classes = [str(entry.get("safety", "qm")) for entry in (parameters if isinstance(parameters, list) else [])
               if isinstance(entry, dict)]
    rank = {name: i for i, name in enumerate(SAFETY_ORDER)}
    return max(classes, key=lambda c: rank.get(c, len(SAFETY_ORDER)), default="qm")


class SwcDescriptionError(RteError):
    """RTE-20: swc.yaml of a C component has errors; carries the messages with file, YAML path and line."""

    def __init__(self, messages: list):
        self.messages = messages
        super().__init__("\n".join(str(m) for m in messages if m.severity == "error"))


def component_implementation(swc_dir: Path) -> str:
    """'simulink' (ARXML of Embedded Coder, also without swc.yaml) or 'c' (hand-written, swc.yaml is the master)."""
    implementation, messages = swc_yaml.implementation_of(swc_dir)
    if messages:
        raise SwcDescriptionError(messages)
    return implementation


def array_type(element: DataType, size: int) -> DataType:
    """Fixed-size array of a platform type as Embedded Coder names it for a vector port (rt_Array_<type>_<n>),
    so a C component (RTE-25) and a Simulink component with the same port share one type in Rte_Type.h."""
    name = f"rt_Array_{element.name}_{size}"
    return DataType(f"/DataTypes/{name}", name, None, "ARRAY", element, size)


def parse_c_component(name: str, swc_dir: Path) -> tuple[Component, list]:
    """Component model of a hand-written C component from its swc.yaml (RTE-20): (component, warnings).

    The same model parse_component() builds from the ARXML of a Simulink component, so connections, tasks, copies,
    parameters and Rte_<Swc>.h follow the same rules and the same templates:
      ports       sender-receiver, port = interface = data element name (as swc_create maps them), platform types,
                  init value 0 (the DefaultInitValue Embedded Coder writes); RTE-25: dimension > 1 is a fixed-size
                  array rt_Array_<type>_<n> (the number resolved by swc_yaml.load, also from the init data)
      parameters  elements of the ParameterReceiver port CalPrm; scalar, row vector (dim) or fixed point (lsb,
                  offset: a LINEAR compu method), values from <Swc>_data.m like every component (load_parameter_values)
      runnables   <Swc>_Init (init) and one per entry with its period; the C function of a periodic runnable is
                  <Swc>_<Runnable> (swc_yaml.Swc.symbol_of); explicit access (Rte_Read/Rte_Write) by exactly the
                  runnables listed at a port, so the task plan knows who reads and writes what
    swc.yaml errors raise SwcDescriptionError (messages with file, YAML path and line in the swc.yaml).
    """
    file = c_component_file(swc_dir)
    try:
        swc, messages = swc_yaml.load(file)
    except swc_yaml.SwcErrors as e:
        raise SwcDescriptionError(e.messages) from None
    if swc.name != name:
        raise RteError(f"components: {name}: {file.as_posix()} describes component {swc.name}; the name in the ECU "
                       f"description must be the component name")
    comp = Component(name)
    for p in swc.ports:
        dtype = DataType(f"/DataTypes/{p.type}", p.type, p.type)
        if p.array:
            dtype = array_type(dtype, p.dimension)       # RTE-25: the resolved number, never the expression
        comp.ports[f"{p.name}.{p.name}"] = Port(p.name, p.direction, p.name, p.name, dtype,
                                                 c_initializer(dtype, zero_value(dtype)))
    for prm in swc.parameters:
        dtype = DataType(f"/DataTypes/{prm.type}", prm.type, prm.type)
        numerator, denominator = (0, 1), 1
        if prm.lsb is not None:
            numerator, denominator = rational_coefficients(Fraction(str(prm.offset)), Fraction(str(prm.lsb)),
                                                           Fraction(1), f"{name}.{swc_yaml.PARAMETER_PORT}."
                                                                        f"{prm.name}: fixed-point scaling")
        comp.parameters[f"{swc_yaml.PARAMETER_PORT}.{prm.name}"] = Parameter(
            name, swc_yaml.PARAMETER_PORT, prm.name, dtype, prm.dim, tuple(numerator), denominator, "",
            "READ-WRITE", set(prm.runnables))
        define_calibration(comp.parameters[f"{swc_yaml.PARAMETER_PORT}.{prm.name}"], prm)          # RTE-26
    comp.runnables.append(Runnable(swc.init_runnable, swc.init_runnable, None))
    for r in swc.runnables:
        keys = {f"{p.name}.{p.name}" for p in swc.ports if r.name in p.runnables}
        comp.runnables.append(Runnable(r.name, swc.symbol_of(r.name), r.period_ms, accesses=set(keys),
                                       explicit=set(keys)))
    comp.access_points = True   # swc.yaml names the runnables of every port
    comp.measurements = [Measurement(name, m.name, m.type, m.dimension, m.unit, m.description, True)
                         for m in swc.measurements]                        # TOOL-143: defined by the RTE
    return comp, [m for m in messages if m.severity == "warning"]


@dataclass
class LoadedComponent:
    """A component of the ECU description with where its code comes from (RTE-20)."""
    component: Component
    swc_dir: Path
    implementation: str          # simulink or c
    warnings: list = field(default_factory=list)   # swc.yaml and source findings (nxconfig messages)

    @property
    def source_dir(self) -> Path:
        """C sources: generated/src (Embedded Coder) or src (hand-written)."""
        return self.swc_dir / ("src" if self.implementation == "c" else "generated/src")


def port_dimension(dtype: DataType) -> int | None:
    """Elements of a port (RTE-25): 1 for a scalar, the size of an array of scalars, None for anything else."""
    if dtype.category == "VALUE":
        return 1
    if dtype.category == "ARRAY" and not dtype.element.composite:
        return dtype.size
    return None


def arxml_dimension_findings(comp: Component, swc_dir: Path) -> list:
    """RTE-25: the dimensions the swc.yaml of a Simulink component names, against its ARXML (array types).

    The ARXML and the model code come from one Embedded Coder run, so the RTE follows the ARXML; a different number
    in swc.yaml means the model or its init data changed since the description was written - a warning with the
    YAML path, so the Studio shows it at the port."""
    file, declared = swc_yaml.declared_dimensions(swc_dir)
    findings = []
    for d in declared:
        port = comp.ports.get(f"{d.port}.{d.port}")
        if port is None:
            continue
        arxml = port_dimension(port.type)
        if d.value is None or arxml is None or arxml == d.value:
            continue
        said = f"{d.value}" + (f" ({d.source})" if d.source else "")
        findings.append(nxconfig.Message(
            "warning", f"port {d.port}: swc.yaml gives dimension {said}, the ARXML of the model "
                       f"{'a scalar' if arxml == 1 else f'{arxml} elements ({port.type.name})'}; the RTE follows the "
                       f"model - {TAKE_OVER}, or correct the PortDimensions in the model and run "
                       f"tools/swc/swc_build.m", file, d.path, d.line))
    return findings


# ---- TOOL-166: the swc.yaml of a Simulink component against its ARXML and code, field by field -------------------

#: what the user does about a swc.yaml that no longer describes the model (Studio, TOOL-165)
TAKE_OVER = ("take the description over from the model (Studio, page Komponenten: "
             "'Beschreibung aus Modell übernehmen')")
#: comment headers above the definitions Embedded Coder writes for an exporting storage class (ExportedGlobal,
#: ExportToFile) - signals and states, not parameters (calibration goes through the RTE port CalPrm)
_EXPORT_HEADER = re.compile(r"^/\*\s*(?:Exported\s+(?:block\s+signals|block\s+states|data\s+definition)|"
                            r"Definition\s+for\s+custom\s+storage\s+class:\s*Export)", re.I)


def exported_variables(source: Path) -> dict[str, tuple[str, int]]:
    """TOOL-166: {symbol: (type, elements)} of the global variables the generated code exports: the definitions in
    the blocks below an Embedded Coder header for exported signals/states/data (a block ends at an empty line).
    The model's own structures (DW, ARID_DEF, rtM) stand under other headers and are not listed."""
    found = {}
    for path in sorted(Path(source).glob("*.c")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for block in re.split(r"\n[ \t]*\n", text):
            if not _EXPORT_HEADER.match(block.lstrip()):
                continue
            for m in _DEFINITION.finditer(block):
                if m.group(1) in ("extern", "static", "return", "typedef", "else", "goto"):
                    continue
                found.setdefault(m.group(2), (m.group(1), int(m.group(3) or 1)))
    return found


def arxml_native(dtype: DataType) -> str | None:
    """Platform type of a port as swc.yaml names it: of the scalar or of the array element; None for a record."""
    if dtype.category == "VALUE":
        return dtype.native
    if dtype.category == "ARRAY" and dtype.element is not None and not dtype.element.composite:
        return dtype.element.native
    return None


def description_findings(comp: Component, swc_dir: Path) -> list:
    """TOOL-166: the swc.yaml of a Simulink component against the ARXML and the code Embedded Coder wrote.

    After swc_create the model is the master; its swc.yaml is only the description, and it goes stale when the model
    changes. Compared are the runnables (missing, extra, period), the ports (missing, extra, direction, platform
    type, the runnables that access them - only when the ARXML has access points), the dimensions (RTE-25,
    arxml_dimension_findings) and the exported variables of the code that `measurements:` does not name. Every
    difference is a warning (nxconfig.Message) with file, YAML path and line of the swc.yaml, so the Studio shows it at
    the entry; the text says to take the description over from the model (TAKE_OVER). The RTE follows the ARXML.
    Measurements named in swc.yaml but missing in the code are errors of model_measurements, not listed here.
    No swc.yaml, an unreadable one or a C component (implementation: c): no findings. A section that swc.yaml does
    not have (no `runnables:`, no `ports:`) is not compared. comp: the component as parse_component read it.
    """
    swc_dir = Path(swc_dir)
    desc = swc_yaml.declared_description(swc_dir)
    if desc is None:
        return []
    findings = []

    def warn(path: tuple, text_: str):
        findings.append(nxconfig.Message("warning", f"{text_}; the RTE follows the model - {TAKE_OVER}",
                                         desc.file, path, desc.line(path)))

    tasks = [r for r in comp.runnables if r.period_ms is not None]     # periodic runnables (swc.yaml has no others)
    if desc.runnables is not None:
        arxml = {r.name: r for r in tasks}
        named = set()
        for d in desc.runnables:
            named.add(d.name)
            r = arxml.get(d.name)
            if r is None:
                other = next((x for x in comp.runnables if x.name == d.name), None)
                if other is not None and other.mode_events:
                    continue                    # RTE-08: started by mode switches, swc.yaml cannot describe it
                warn(("runnables", d.index, "name"), f"runnable {d.name}: named in swc.yaml, the model (ARXML) has "
                                                     f"no periodic runnable {d.name}")
            elif d.period_ms != r.period_ms and not isinstance(d.period_ms, bool):
                warn(("runnables", d.index, "period_ms"), f"runnable {d.name}: swc.yaml gives period {d.period_ms} "
                                                          f"ms, the model (ARXML) {r.period_ms} ms")
        for r in tasks:
            if r.name not in named:
                warn(("runnables",), f"runnable {r.name} ({r.period_ms} ms) of the model (ARXML) is missing in "
                                     f"swc.yaml")
    if desc.ports is not None:
        named = set()
        for d in desc.ports:
            named.add(d.name)
            port = comp.ports.get(f"{d.name}.{d.name}") or next(
                (p for p in comp.ports.values() if p.name == d.name), None)
            if port is None:
                warn(("ports", d.index, "name"), f"port {d.name}: named in swc.yaml, the model (ARXML) has no "
                                                 f"sender-receiver port {d.name}")
                continue
            if d.direction in swc_yaml.DIRECTIONS and d.direction != port.direction:
                warn(("ports", d.index, "direction"), f"port {d.name}: swc.yaml gives direction {d.direction}, the "
                                                      f"model (ARXML) {port.direction} "
                                                      f"({'required' if port.direction == 'in' else 'provided'} port)")
            native = arxml_native(port.type)
            if native is not None and isinstance(d.type, str) and d.type != native:
                warn(("ports", d.index, "type"), f"port {d.name}: swc.yaml gives type {d.type}, the model (ARXML) "
                                                 f"{native}")
            if comp.access_points and isinstance(d.runnables, list):
                key = f"{port.name}.{port.element}"
                have = sorted(r.name for r in comp.runnables if key in r.accesses and not r.init)
                said = sorted(str(r) for r in d.runnables)
                if said != have:
                    warn(("ports", d.index, "runnables"),
                         f"port {d.name}: swc.yaml gives runnables [{', '.join(said)}], in the model (ARXML) "
                         f"{'[' + ', '.join(have) + '] access' if have else 'no runnable accesses'} it")
        for key, port in comp.ports.items():
            if port.name not in named:
                warn(("ports",), f"port {port.name} ({port.direction}) of the model (ARXML) is missing in swc.yaml")
    findings += arxml_dimension_findings(comp, swc_dir)
    source = swc_dir / "generated" / "src"
    if source.is_dir():
        named = set(desc.measurements)
        for symbol, (ctype, size) in sorted(exported_variables(source).items()):
            if symbol in named:
                continue
            native = CODER_TYPES.get(ctype, ctype)
            warn(("measurements",) if desc.has_measurements else (),
                 f"the generated code exports the variable {native} {symbol}" + (f"[{size}]" if size > 1 else "")
                 + " (exporting storage class), swc.yaml does not name it under measurements - an XCP tool cannot "
                   "read it until it is listed")
    return findings


def load_component(name: str, swc_dir: Path, repo: Path | None = None) -> LoadedComponent:
    """Reads one component of the ECU description: the ARXML of a Simulink component or the swc.yaml of a C component
    (RTE-20), then its calibration values. A C component is also compared with its sources (swc_skeleton.check):
    missing or extra runnable functions are warnings."""
    swc_dir = Path(swc_dir)
    implementation = component_implementation(swc_dir)
    if implementation == "c":
        comp, warnings = parse_c_component(name, swc_dir)
        warnings = warnings + swc_skeleton.check(swc_dir)
    else:
        comp = parse_component(name, swc_dir / "generated" / "arxml")
        warnings = description_findings(comp, swc_dir)             # TOOL-166 (with RTE-25 dimensions)
        warnings = warnings + apply_simulink_calibration(comp, swc_dir)                             # RTE-26
        comp.measurements, found = model_measurements(name, swc_dir)        # TOOL-143
        warnings = warnings + found
    load_parameter_values(comp, swc_dir, repo)
    finish_calibration(comp)                                                                          # RTE-26
    comp.safety = component_safety(swc_dir)                                                           # RTE-27
    return LoadedComponent(comp, swc_dir, implementation, warnings)


# ---- RTE-26: calibration objects (stage K1 of doc/architecture/kalibrierung.md) ----------------------------------

# rte::ParameterKind of a row of rte::PARAMETERS (RteConfig.h.j2), and the file with the texts for tools/a2lgen
PARAMETER_KINDS = {"": "PLAIN", "value": "PLAIN", "array": "PLAIN", "curve": "CURVE", "map": "MAP", "axis": "AXIS"}
CALIBRATION_FILE = "RteCalibration.json"
CALIBRATION_FORMAT = "nextbsw-rte-calibration 1"
FLOAT_MAX = {"float32": 3.4028234663852886e38, "float64": 1.7976931348623157e308}


def define_calibration(param: Parameter, d: "swc_yaml.Parameter"):
    """Takes the RTE-26 definition of swc.yaml over into an RTE parameter (C component or ARXML of a model)."""
    if not d.calibration:
        return
    param.kind = d.cal_kind
    if d.unit:
        param.unit = d.unit
    param.safety, param.min, param.max = d.safety, d.min, d.max
    param.description = d.description
    param.value_table = dict(d.value_table)
    param.axis_names = [a.name for a in d.axes]
    param.own_of = d.own_of
    param.shape = tuple(d.shape)


def apply_simulink_calibration(comp: Component, swc_dir: Path, check_values: bool = True,
                               declared: "list | None" = None) -> list:
    """RTE-26: the calibration definitions of a Simulink component's swc.yaml against its ARXML; returns warnings.

    swc.yaml is the master of the definition (E1 a), the ARXML of Embedded Coder the proof that the model has it:
    every defined parameter must be an element of a parameter port with the same element type, the same number of
    values (a map: nx * ny, row by row) and the same scaling. Differences are errors with the YAML path, so the
    Studio shows them at the parameter. Parameters of the model that swc.yaml does not define stay RTE-03 parameters
    (type range, QM) and are named in a warning. Matrices and records as parameters (a map as 2-D model argument,
    own axes inside a Simulink.LookupTable) are stage K5 (TOOL-146): until then a map is a vector parameter of
    nx * ny values and own breakpoints are the vector parameters <Name>_X / <Name>_Y of the same port.

    TOOL-173: `check_values=False` (the Studio's parameter view) leaves out the findings about the values in
    <Swc>_data.m - a value the data file still misses is what the Studio is there to fix, and it must not hide the
    comparison of type, dimension and scaling with the model. declared_calibration returns definitions only when
    the definition itself has no error, so with definitions every error left is about the values. `declared`
    receives the definitions (the view lists them). The build (build_rte) always checks the values.
    """
    file, tree, definitions, messages = swc_yaml.declared_calibration(swc_dir)
    if declared is not None:
        declared.extend(definitions)
    if not check_values and definitions:
        messages = [m for m in messages if m.severity != "error"]
    if any(m.severity == "error" for m in messages):
        raise SwcDescriptionError(messages)
    warnings = [m for m in messages if m.severity == "warning"]
    if not definitions:
        return warnings
    errors = []

    def finding(severity: str, path: tuple, text_: str):
        target = errors if severity == "error" else warnings
        target.append(nxconfig.Message(severity, text_, file, path, swc_yaml._line_of(tree, path)))

    declared = set()
    for d in definitions:
        path = d.yaml_path or ("parameters",)
        declared.add(d.name)
        keys = [k for k, p in comp.parameters.items() if p.element == d.name]
        key = f"{swc_yaml.PARAMETER_PORT}.{d.name}" if f"{swc_yaml.PARAMETER_PORT}.{d.name}" in keys else \
            (keys[0] if len(keys) == 1 else None)
        if key is None:
            finding("error", (*path, "name"), f"{d.name} is not a calibration parameter of the model {comp.name} "
                                              f"(ARXML): add it as element of the parameter port "
                                              f"{swc_yaml.PARAMETER_PORT} and run tools/swc/swc_build.m")
            continue
        rp = comp.parameters[key]
        if rp.type.native != d.type:
            finding("error", (*path, "type"), f"{d.name} is {d.type} in swc.yaml, the model has {rp.type.describe()}")
            continue
        if rp.dim != d.dim:
            shape = " x ".join(str(n) for n in d.shape) if d.shape else str(d.dim)
            finding("error", path if d.own_of else (*path, "dim" if not d.shape else ("axis" if len(d.shape) == 1
                                                                                          else "axes")),
                    f"dimension of {d.name} does not fit: swc.yaml gives {shape} value(s), the model (ARXML) "
                    f"{rp.dim}")
            continue
        numerator, denominator = (0, 1), 1
        if d.lsb is not None:
            numerator, denominator = rational_coefficients(Fraction(str(d.offset)), Fraction(str(d.lsb)),
                                                           Fraction(1), f"{rp.name}: fixed-point scaling")
        if (tuple(numerator), denominator) != (tuple(rp.numerator), rp.denominator):
            model = "no scaling" if not rp.scaled else \
                f"physical = ({rp.numerator[0]} + {rp.numerator[1]} * stored) / {rp.denominator}"
            finding("error", (*path, "lsb"), f"scaling of {d.name} differs: swc.yaml "
                                             f"{'lsb ' + str(d.lsb) + ', offset ' + str(d.offset) if d.lsb is not None else 'no lsb'}, "
                                             f"the model {model}")
            continue
        define_calibration(rp, d)
    for p in comp.parameters.values():
        if p.element not in declared:
            finding("warning", ("parameters",), f"parameter {p.element} of the model is not defined in swc.yaml: "
                                                f"type range as limits, safety qm, no description")
    if errors:
        raise SwcDescriptionError(errors + warnings)
    return warnings


def stored_limits(param: Parameter) -> tuple | None:
    """Engineering limits as stored values of the element type (inside, rounded onto the grid), None: none given."""
    if param.min is None and param.max is None:
        return None
    native = param.type.native
    if TYPE_RANGE[native] is None:
        top = FLOAT_MAX[native]
        return (float(param.min) if param.min is not None else -top, float(param.max) if param.max is not None else top)
    low, high = TYPE_RANGE[native]

    def stored(value):
        return (Fraction(str(value)) * param.denominator - param.numerator[0]) / param.numerator[1]

    rising = (param.numerator[1] > 0) == (param.denominator > 0)
    first, last = (param.min, param.max) if rising else (param.max, param.min)
    lo = math.ceil(stored(first)) if first is not None else low
    hi = math.floor(stored(last)) if last is not None else high
    lo, hi = max(lo, low), min(hi, high)
    if lo > hi:
        raise RteError(f"{param.name}: no stored value of {native} lies between min {param.min} and max {param.max} "
                       f"(grid of the scaling)")
    return lo, hi


def finish_calibration(comp: Component):
    """RTE-26: resolves axes, builds the records of own axes, converts the limits and checks the stored values.

    Runs after the values are loaded (load_parameter_values), for C and Simulink components alike: axes are
    elements of the same port, a curve/map has as many values as its axes give, breakpoints are strictly ascending
    as stored values too (the rounding onto the grid must not merge two of them), and every stored default lies
    inside the stored limits.
    """
    by_element = {(p.port, p.element): p for p in comp.parameters.values()}
    for p in comp.parameters.values():
        if not p.kind:
            continue
        p.axes = []
        for name in p.axis_names:
            axis = by_element.get((p.port, name))
            if axis is None:
                raise RteError(f"{p.name}: axis {name} is not a parameter of port {p.port} of the model")
            if axis.kind != "axis":
                raise RteError(f"{p.name}: {axis.name} is no axis (swc.yaml kind: axis)")
            p.axes.append(axis)
        if p.kind in ("curve", "map"):
            count = 1
            for axis in p.axes:
                count *= axis.dim
            if count != p.dim:
                raise RteError(f"{p.name}: dimension does not fit - {p.dim} values, the axes "
                               f"{' x '.join(f'{a.element} ({a.dim})' for a in p.axes)} give {count}")
        if p.value_table and (p.scaled or TYPE_RANGE[p.type.native] is None):
            raise RteError(f"{p.name}: a value table needs an unscaled integer type ({p.type.describe()})")
        if p.own_of:
            p.owner = by_element.get((p.port, p.own_of))
            if p.owner is None:
                raise RteError(f"{p.name}: own breakpoints of {p.own_of}, which is no parameter of port {p.port}")
        p.limits = stored_limits(p)
        check_stored_values(p)
    for p in comp.parameters.values():
        own = [a for a in p.axes if a.own_of == p.element]
        if not own:
            continue
        p.record, p.member = p.storage, "v"
        for a in own:
            a.record, a.member = p.storage, "xy"[p.axes.index(a)]


def check_stored_values(p: Parameter):
    """The stored values of one parameter inside its stored limits, the breakpoints of an axis strictly ascending.

    Shared by finish_calibration (default values of <Swc>_data.m) and the calibration dataset (TOOL-145, stage K2:
    apply_calibration_dataset, tools/calgen check), so a value is judged the same way wherever it comes from. The
    run-time plausibility check of SYS-90 (parameter_checks, ecu/calibration Plausibility.cpp) applies the same
    rules (value_rules) to the stored values in the ECU.
    """
    rules = value_rules(p)
    if "limits" in rules:
        lo, hi = p.limits
        for i, raw in enumerate(p.raw):
            if not lo <= raw <= hi:
                at = f"[{i + 1}]" if p.dim > 1 else ""
                raise RteError(f"{p.name}{at}: {p.phys[i]} is stored as {raw}, outside the stored limits "
                               f"{lo} .. {hi} of min {p.min} / max {p.max} on the grid of the scaling")
    if "ascending" in rules:
        for i in range(1, len(p.raw)):
            if p.raw[i] <= p.raw[i - 1]:
                raise RteError(f"{p.name}: breakpoints are not strictly ascending as stored values "
                               f"({p.raw[i - 1]} at {i}, {p.raw[i]} at {i + 1}; physical {p.phys[i - 1]}, "
                               f"{p.phys[i]})")


def value_rules(p: Parameter) -> list[str]:
    """Value rules of one parameter: "limits" (every stored value inside the stored limits) if it has limits,
    "ascending" (strictly ascending stored breakpoints) for a shared or own axis.

    One definition for the build and the dataset (check_stored_values) and for the plausibility check in the ECU
    (SYS-90: parameter_checks), so the generator, tools/calgen and the run time never judge a value differently.
    """
    rules = []
    if p.limits is not None:
        rules.append("limits")
    if p.kind == "axis":
        rules.append("ascending")
    return rules


# SYS-90 (calibration K6): rules of rte::PARAMETER_CHECKS[] (bit mask ParameterCheck::rules of RteConfig.h)
CHECK_RULES = {"form": 0x01, "limits": 0x02, "ascending": 0x04}
PLAUSIBILITY_FILE = "RteParameterChecks.md"


@dataclass
class ParameterCheck:
    """One generated plausibility check (SYS-90): a safety-relevant parameter, its row and what is checked."""
    row: int                      # row of rte::PARAMETERS
    parameter: Parameter
    shape: tuple                  # elements of the X/Y axis of a curve/map (form), 0: none
    rules: list

    @property
    def mask(self) -> int:
        return sum(CHECK_RULES[r] for r in self.rules)


def parameter_checks(components: dict) -> list[ParameterCheck]:
    """SYS-90: the plausibility checks of every calibration parameter with a safety class other than qm.

    swc.yaml guarantees what the checks rely on: a safety class needs min and max (so "limits" always applies), an
    axis has at least the class of every curve/map that uses it, and own breakpoints take the class of their owner -
    the breakpoints of a checked curve/map are checked too. "form" is the shape of swc.yaml (number of values, the
    element counts of the axes), checked in the ECU against its table rte::PARAMETERS.
    """
    params = [p for c in components.values() for p in c.parameters.values()]
    checks = []
    for row, p in enumerate(params):
        if p.safety == "qm":
            continue
        shape = tuple(a.dim for a in p.axes) if p.kind in ("curve", "map") else ()
        checks.append(ParameterCheck(row, p, shape + (0,) * (2 - len(shape)), ["form"] + value_rules(p)))
    return checks


def render_parameter_checks(checks: list, components: dict, source: str, dataset: "DatasetUse | None") -> str:
    """RteParameterChecks.md: the safety-relevant calibration parameters with their checks (SYS-90, K6).

    Evidence for the safety case (calibration data specified, verified and protected): every parameter the ECU
    checks, the rules it applies with the stored limits it uses, and the value this build carries. Written next to
    RteConfig.h only when there is a check, so the output of a project without one stays as it was.
    """
    values = f"dataset {dataset.name} (values {dataset.values_id})" if dataset is not None else \
        "defaults of <Swc>_data.m"
    lines = [
        f"<!-- Generated by tools/rtegen from {source} - do not edit. -->",
        "",
        "# Plausibility checks of the safety-relevant calibration parameters (SYS-90)",
        "",
        f"- Layout identifier: `{calibration_layout_id(components)}`",
        f"- Values of this build: {values}",
        f"- Checks: {len(checks)} (rte::PARAMETER_CHECKS, ecu/calibration/Plausibility.cpp)",
        "- Runs: at start-up on the values the ECU will use (the calibration segment before it becomes the working "
        "page, or the values in place), and on every XCP write to a checked parameter before the value takes effect.",
        "- Reaction: start-up - memory.calibration.on_invalid (refuse for a project with a checked parameter); "
        "XCP - the write is refused (ERR_OUT_OF_RANGE), the value stays. Both: log line and the BSW event "
        "`calibration.plausibility` of the fault memory.",
        "- Rules: `form` - number of values and axis element counts as in swc.yaml, stored limits present; `limits` - "
        "every stored value inside the stored limits (min/max rounded inside onto the grid of the scaling); "
        "`ascending` - breakpoints strictly ascending as stored values. The same rules as the build and "
        "tools/calgen check (rtegen.value_rules).",
        "",
        "| Row | Parameter | Safety | Kind | Type | Form | Unit | min .. max | Stored limits | Rules | Value |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in checks:
        p = c.parameter
        kind = "own axis" if p.own_of else (p.kind or ("value" if p.dim == 1 else "array"))
        form = " x ".join(str(n) for n in c.shape if n) if any(c.shape) else str(p.dim)
        lo, hi = p.limits
        limits = f"{p.min} .. {p.max}"
        stored = f"{lo} .. {hi}"
        value = " ".join(str(v) for v in p.phys)
        lines.append(f"| {c.row} | `{p.name}` | {p.safety} | {kind} | {p.type.native} | {form} | {p.unit or '-'} | "
                     f"{limits} | {stored} | {', '.join(c.rules)} | {value} |")
    return "\n".join(lines) + "\n"


def calibration_records(components: dict) -> list[dict]:
    """Records of curves/maps with own breakpoints: C type, variable and members x, y, v in memory order."""
    records = []
    for comp in components.values():
        for p in comp.parameters.values():
            if p.record and p.member == "v":
                members = sorted((a for a in p.axes if a.record == p.record), key=lambda a: a.member)
                records.append({"type": f"Rte_CalRec_{p.component}_{p.port}_{p.element}", "name": p.record,
                                "owner": p, "members": [(a.member, a) for a in members] + [("v", p)]})
    return records


def parameter_row_extras(components: dict) -> list[str]:
    """Text after the RTE-03 fields of each rte::PARAMETERS row: limits, axis rows, kind, safety class.

    An RTE-03 parameter (plain value or array, no limits, QM) gets nothing: its row keeps the defaults of the
    struct and stays as it was generated before RTE-26.
    """
    params = [p for c in components.values() for p in c.parameters.values()]
    index = {id(p): i for i, p in enumerate(params)}
    extras = []
    for p in params:
        kind = "OWN_AXIS" if p.own_of else PARAMETER_KINDS[p.kind]
        rows = [index[id(p.owner)]] if p.own_of else [index[id(a)] for a in p.axes]
        if kind == "PLAIN" and p.limits is None and p.safety == "qm":
            extras.append("")
            continue
        axis = ", ".join([f"{r}U" for r in rows] + ["NO_AXIS"] * (2 - len(rows)))
        limits = p.limits_symbol if p.limits is not None else "nullptr"
        extras.append(f", {limits}, {{ {axis} }}, ParameterKind::{kind}, SafetyClass::{p.safety.upper()}")
    return extras


def calibration_layout_id(components: dict) -> str:
    """Hash over name, kind, type, dimensions, axes and order of all calibration objects (kalibrierung.md 6.3).

    Prepared for the dataset (K2) and the calibration segment (K3): a dataset fits every build with the same
    layout identifier. Values, limits and texts are not part of it.
    """
    rows = [(p.name, "OWN_AXIS" if p.own_of else PARAMETER_KINDS[p.kind], p.type.native, p.dim, list(p.shape),
             [a.name for a in p.axes], p.scaled and [list(p.numerator), p.denominator])
            for c in components.values() for p in c.parameters.values()]
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()[:8]


def render_calibration_description(components: dict, source: str, dataset: "DatasetUse | None" = None) -> str:
    """RteCalibration.json: what tools/a2lgen needs beyond the image - description and value table per object.

    Addresses, types, counts, conversions, stored limits, axis rows and kind are in rte::PARAMETERS of the image
    itself; a2lgen checks that this file and the image name the same objects of the same kind.
    """
    import json
    objects = {}
    for comp in components.values():
        for p in comp.parameters.values():
            objects[p.name] = {
                "kind": "own_axis" if p.own_of else (p.kind or ("value" if p.dim == 1 else "array")),
                "description": p.description,
                "unit": p.unit,
                "safety": p.safety,
                "min": p.min,
                "max": p.max,
                "shape": list(p.shape) or [p.dim],
                "axes": [a.name for a in p.axes],
                "owner": p.owner.name if p.owner is not None else "",
                "value_table": [[k, v] for k, v in sorted(p.value_table.items())],
            }
    content = {"format": CALIBRATION_FORMAT, "source": source, "layout": calibration_layout_id(components),
               "parameters": objects}
    if dataset is not None:             # TOOL-145: only with a dataset, so the file stays as it was without
        content["dataset"] = {"name": dataset.name, "values": dataset.values_id}
    return json.dumps(content, indent=1, ensure_ascii=False) + "\n"


# ---- TOOL-145: calibration dataset (stage K2 of doc/architecture/kalibrierung.md) -----------------------------------

#: default of build_model()/generate(calibration=...): the dataset of the project, <project dir>/calibration
PROJECT_DATASET = object()


@dataclass
class CalibrationChoice:
    """Dataset folder and variant a build takes its calibration values from (TOOL-145)."""
    folder: Path
    variant: str = ""            # "": build_variant of dataset.yaml, without it the base dataset


@dataclass
class DatasetUse:
    """What build_model() took from a calibration dataset: name (base or variant), files, identifier of the values."""
    folder: Path
    name: str
    layout: str
    values_id: str               # hash over the stored values of all parameters (RteCalibration.json, K3 header)
    files: list

    @property
    def cmake(self) -> str:
        """Lines for rte_components.cmake: a change of the dataset configures again (and so runs rtegen)."""
        inputs = [*self.files, HERE.parent / "calgen" / "caldata.py"]
        return (f"# TOOL-145: calibration dataset {self.folder.as_posix()} ({self.name}); a change configures again\n"
                "set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS\n"
                + "".join(f'    "{Path(p).as_posix()}"\n' for p in inputs) + ")\n")


def dataset_of(config, ecu_file: Path, calibration=PROJECT_DATASET) -> "CalibrationChoice | None":
    """The dataset a build uses: `calibration` if given, else <project dir>/calibration when that folder exists.

    Like the diagnostic description (diag_of) only for the project's own ECU description: a test that renders
    another rte.yaml against a project gets none. A folder calibration/ that is no valid dataset is an error of
    use_calibration_dataset, never silently skipped.
    """
    if calibration is not PROJECT_DATASET:
        return calibration
    project_file = getattr(config, "project_file", None)
    rte_file = getattr(config, "rte_file", None) if config is not None else None
    if project_file is None or rte_file is None or Path(ecu_file).resolve() != Path(rte_file).resolve():
        return None
    folder = Path(project_file).parent / caldata.DATASET_DIR
    return CalibrationChoice(folder) if folder.is_dir() else None


def dataset_values(p: Parameter, entry: "caldata.Entry") -> list:
    """Flat physical values of a dataset entry in the order of <Swc>_data.m (a map row by row, one row per X).

    A scalar is a number, an array/curve/axis a list, a map nx rows of ny values; a value with a value table may
    be written as its text; true/false are 1/0.
    """
    where = entry.where

    def one(v):
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str) and p.value_table:
            for stored, text_ in p.value_table.items():
                if text_ == v:
                    return int(stored)
            raise RteError(f"{p.name}: {where}: '{v}' is not in the value table "
                           f"({', '.join(f'{k}: {x}' for k, x in sorted(p.value_table.items()))})")
        raise RteError(f"{p.name}: {where}: {v!r} is no number")

    value = entry.value
    if len(p.shape) == 2:
        nx, ny = p.shape
        if not (isinstance(value, list) and len(value) == nx
                and all(isinstance(row, list) and len(row) == ny for row in value)):
            raise RteError(f"{p.name}: {where}: a map of {nx} x {ny} values is written as {nx} rows of {ny} values "
                           f"(one row per X breakpoint)")
        return [one(v) for row in value for v in row]
    if p.dim == 1:
        if isinstance(value, list):
            raise RteError(f"{p.name}: {where}: a scalar is expected, not a list")
        return [one(value)]
    if not isinstance(value, list) or len(value) != p.dim or any(isinstance(v, list) for v in value):
        count = f"{len(value)} values" if isinstance(value, list) else "a scalar"
        raise RteError(f"{p.name}: {where}: {p.dim} values expected as one list, the dataset has {count}")
    return [one(v) for v in value]


def apply_calibration_dataset(components: dict, values: "caldata.Part") -> list[str]:
    """Sets phys/raw of every parameter from a resolved dataset; returns the errors (empty: all values taken).

    The same conversion (_to_raw) and value checks (check_stored_values) as for the defaults of <Swc>_data.m. A
    parameter without a value, a value for no parameter and a component the software does not have are errors:
    the dataset must be complete, a default is never taken silently (kalibrierung.md 5.3). tools/calgen check runs
    this function too, so the build and the check never judge a value differently.
    """
    errors = []
    for comp in components.values():
        entries = values.components.get(comp.name, {})
        for p in comp.parameters.values():
            entry = entries.get(p.element)
            if entry is None:
                errors.append(f"{p.name}: no value in the calibration dataset ({values.name})")
                continue
            try:
                phys = dataset_values(p, entry)
                raw = [_to_raw(p, v, p.name, entry.where) for v in phys]
            except RteError as e:
                errors.append(str(e))
                continue
            p.phys, p.raw = phys, raw
            try:
                check_stored_values(p)
            except RteError as e:
                errors.append(f"{e} ({entry.where})")
        elements = {p.element for p in comp.parameters.values()}
        for element, entry in entries.items():
            if element not in elements:
                errors.append(f"{entry.where}: {comp.name}.{element} is no calibration parameter of {comp.name}")
    for name, entries in values.components.items():
        if name not in components:
            where = next(iter(entries.values())).where if entries else values.name
            errors.append(f"{where}: component {name} is not part of this ECU software")
    return errors


def calibration_values_id(components: dict) -> str:
    """Hash over name and stored values of every parameter: identifies the values an image carries (TOOL-145)."""
    rows = [(p.name, [repr(v) for v in p.raw]) for c in components.values() for p in c.parameters.values()]
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()[:8]


def use_calibration_dataset(components: dict, choice: "CalibrationChoice | None") -> "DatasetUse | None":
    """TOOL-145: takes the values of the chosen dataset (variant resolved) into the parameters, or refuses.

    Runs in build_model() after the components are loaded, so validate() (the Studio check) sees the same errors,
    and before anything is rendered. Errors: format of the dataset, unknown variant, completeness, conversion,
    limits, ascending axes, and a layout identifier other than the one of this software.
    """
    if choice is None:
        return None
    try:
        dataset = caldata.load(choice.folder)
        name = choice.variant or dataset.build_variant or caldata.BASE
        values = dataset.resolve(name)
    except caldata.CalDataError as e:
        raise RteError(f"calibration dataset {choice.folder.as_posix()}: {e}") from None
    errors = apply_calibration_dataset(components, values)
    layout = calibration_layout_id(components)
    if dataset.layout != layout:
        errors.insert(0, f"{dataset.index_file.as_posix()}: the dataset is bound to layout {dataset.layout or '-'}, "
                         f"this software has layout {layout}; check the values (tools/calgen/calgen.py check) and "
                         f"bind them (calgen rebind)")
    if errors:
        raise RteError(f"calibration dataset {choice.folder.as_posix()} ({name}): " + "; ".join(errors))
    return DatasetUse(choice.folder, name, layout, calibration_values_id(components), dataset.files_of(name))


def check_runnable_symbols(components: dict):
    """Two runnables with one C function cannot be linked; say which components collide (RTE-20)."""
    seen: dict[str, str] = {}
    for comp in components.values():
        for r in comp.runnables:
            other = seen.get(r.symbol)
            if other is not None:
                raise RteError(f"components: runnable {comp.name}.{r.name} and {other} have the same C function "
                               f"{r.symbol}; rename one of the runnables")
            seen[r.symbol] = f"{comp.name}.{r.name}"


# ---- TOOL-143: measurements - internal values of a component in rte::SIGNALS (XCP read access, A2L MEASUREMENT)

#: text of the `bsw` column of rte::SIGNALS for a measurement: "measurement [<unit>] <description>". tools/a2lgen
#: reads it back (unit -> PHYS_UNIT, description -> long identifier); the console shows it as it is
MEASUREMENT_TEXT = "measurement"
MEASUREMENT_TEXT_MAX = 200          # bytes; a2lgen reads strings of the image up to 256 bytes
#: element types in the code Embedded Coder writes (ERT names and, for autosar.tlc, the AUTOSAR platform types)
CODER_TYPES = {"boolean_T": "boolean", "uint8_T": "uint8", "int8_T": "sint8", "uint16_T": "uint16",
               "int16_T": "sint16", "uint32_T": "uint32", "int32_T": "sint32", "real32_T": "float32",
               "real_T": "float64", "real64_T": "float64", **{t: t for t in PLATFORM_TYPES}}


@dataclass
class Measurement:
    """TOOL-143: a global variable of a component an XCP tool may read (swc.yaml `measurements:`)."""
    component: str
    name: str                       # C symbol of the global variable
    native: str                     # platform type of an element
    dim: int = 1
    unit: str = ""
    description: str = ""
    rte_defined: bool = False       # C component: the RTE defines it (Rte.c); Simulink: the model code does

    @property
    def label(self) -> str:
        return f"{self.component}.{self.name}"

    @property
    def text(self) -> str:
        """The `bsw` text of its rows in rte::SIGNALS, safe inside a C string literal."""
        text = MEASUREMENT_TEXT + (f" [{self.unit}]" if self.unit else "") + \
            (f" {self.description}" if self.description else "")
        text = "".join(ch if ch.isprintable() else " " for ch in text).replace("\\", "/").replace('"', "'")
        while len(text.encode("utf-8")) > MEASUREMENT_TEXT_MAX:
            text = text[:-1]
        return text

    def leaves(self) -> list:
        """Rows for rte::SIGNALS: (label, C expression, platform type); an array element by element."""
        if self.dim > 1:
            return [(f"{self.label}[{i}]", f"{self.name}[{i}]", self.native) for i in range(self.dim)]
        return [(self.label, self.name, self.native)]

    @property
    def declaration(self) -> str:
        return f"{self.native} {self.name}" + (f"[{self.dim}]" if self.dim > 1 else "")


# RTE-29: scaling of the fixed point rows of rte::SIGNALS (rte::SIGNAL_SCALINGS, RteConfig.h.j2). A row whose port
# type has a LINEAR compu method (RTE-04/RTE-15: swc.yaml `types:` kind fixed, Simulink fixdt) gets one entry with
# the rational conversion physical = (numerator[0] + numerator[1] * stored) / denominator, its unit and the number of
# decimals the console prints. A separate, sparse table instead of three more fields in rte::Signal: rte::Signal
# keeps the layout tools/a2lgen and ecu/xcp (static_assert) read, and an ECU without fixed point types builds the
# same image as before.
SCALING_DECIMALS_MAX = 6
SCALING_UNIT_MAX = 32               # bytes of the unit text in the image (a2lgen reads strings up to 256 bytes)
SCALING_INT64_LIMIT = 2**63         # the console computes (n0 + n1 * stored) * 10^decimals + d/2 in int64_t
SIGNAL_ROW_MAX = 0xFFFF             # rte::SignalScaling::signal is uint16_t


def scaling_decimals(numerator: tuple, denominator: int) -> int:
    """Decimals that show every value of the grid exactly (the step and the offset end in a finite decimal), else
    enough to resolve the step with one more digit; at most SCALING_DECIMALS_MAX."""
    n0, n1 = numerator
    for k in range(SCALING_DECIMALS_MAX + 1):
        if (n0 * 10**k) % denominator == 0 and (n1 * 10**k) % denominator == 0:
            return k
    k = 0
    while k < SCALING_DECIMALS_MAX and abs(n1) * 10**k < abs(denominator):
        k += 1
    return min(k + 1, SCALING_DECIMALS_MAX)


def scaling_text(text: str) -> str:
    """A unit as text of a C string literal: printable, no backslash or quote, at most SCALING_UNIT_MAX bytes."""
    text = "".join(ch if ch.isprintable() else " " for ch in text).replace("\\", "/").replace('"', "'")
    while len(text.encode("utf-8")) > SCALING_UNIT_MAX:
        text = text[:-1]
    return text


@dataclass
class SignalScaling:
    """RTE-29: one row of rte::SIGNAL_SCALINGS."""
    signal: int                     # row of rte::SIGNALS
    label: str                      # its name (messages, comment in RteConfig.cpp)
    numerator: tuple
    denominator: int
    unit: str
    decimals: int


def signal_scalings(buffers: list, measurements: list) -> list[SignalScaling]:
    """RTE-29: the scaled rows of rte::SIGNALS, in the row order of RteConfig.cpp.j2 (buffers, then measurements).

    The coefficients are those rtegen read from the compu method of the port type (DataType.numerator/denominator,
    reduced integers); the console limit (int64_t) is checked over the whole stored range of the platform type."""
    rows = []
    row = 0
    for b in buffers:
        for (label, _expr, native), t in zip(b.leaves(), b.leaf_types()):
            if t is not None and t.scaled:
                low, high = TYPE_RANGE[native]
                n0, n1 = t.numerator
                d = t.denominator
                worst = max(abs(n0 + n1 * low), abs(n0 + n1 * high))
                decimals = scaling_decimals(t.numerator, d)
                while decimals > 0 and worst * 10**decimals + abs(d) >= SCALING_INT64_LIMIT:
                    decimals -= 1
                if worst + abs(d) >= SCALING_INT64_LIMIT:
                    raise RteError(f"{label}: scaling physical = ({n0} + {n1} * stored) / {d} of type {t.name} "
                                   f"exceeds 64 bit over the range of {native} (console rte signals)")
                rows.append(SignalScaling(row, label, (n0, n1), d, scaling_text(t.unit), decimals))
            row += 1
    row += sum(len(m.leaves()) for m in measurements)       # TOOL-143: measurements carry no scaling
    if rows and row - 1 > SIGNAL_ROW_MAX:
        raise RteError(f"rte::SIGNALS has {row} rows; rte::SIGNAL_SCALINGS indexes at most {SIGNAL_ROW_MAX + 1}")
    return rows


def model_measurements(name: str, swc_dir: Path) -> tuple[list, list]:
    """Measurements of a Simulink component: `measurements:` of its swc.yaml (the import writes it from the
    Simulink.Signal objects with an exporting storage class), each one checked against the code Embedded Coder
    wrote - the RTE only declares the variable, the model defines it. (measurements, warnings)."""
    file, declared, messages = swc_yaml.declared_measurements(swc_dir)
    errors = [m for m in messages if m.severity == "error"]
    if errors:
        raise SwcDescriptionError(errors)
    result = [Measurement(name, m.name, m.type, m.dimension, m.unit, m.description) for m in declared]
    source = Path(swc_dir) / "generated" / "src"
    if result and source.is_dir():
        definitions = generated_definitions(source)
        for i, m in enumerate(result):
            where = f"{file.as_posix()}: measurements[{i}] ({m.name})"
            found = definitions.get(m.name)
            if found is None:
                raise RteError(f"{where}: the generated code of {name} (generated/src) defines no global variable "
                               f"{m.name}. Give the Simulink.Signal {m.name} the storage class "
                               f"{' or '.join(swc_yaml.EXPORTING_STORAGE_CLASSES)} in the model's data and run "
                               f"tools/swc/swc_build.m, or, if the model no longer has it, {TAKE_OVER}")
            ctype, size = found
            native = CODER_TYPES.get(ctype)
            if native != m.native or size != m.dim:
                have = f"{ctype}" + (f"[{size}]" if size > 1 else "")
                raise RteError(f"{where}: swc.yaml gives {m.declaration}, the generated code defines {have} {m.name};"
                               f" {TAKE_OVER}, or correct the signal object and run swc_build")
    return result, [m for m in messages if m.severity == "warning"]


_DEFINITION = re.compile(r"^[ \t]*(?:volatile[ \t]+)?([A-Za-z_]\w*)[ \t]+([A-Za-z_]\w*)[ \t]*(?:\[[ \t]*(\d+)[ \t]*\])?"
                         r"[ \t]*(?:=[^;]*)?;", re.M)


def generated_definitions(source: Path) -> dict[str, tuple[str, int]]:
    """{symbol: (type, elements)} of the file-scope variable definitions in the .c files of generated code."""
    found = {}
    for path in sorted(Path(source).glob("*.c")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _DEFINITION.finditer(text):
            if m.group(1) in ("extern", "static", "return", "typedef", "else", "goto"):
                continue
            found.setdefault(m.group(2), (m.group(1), int(m.group(3) or 1)))
    return found


def check_measurement_symbols(components: dict):
    """TOOL-143: a measurement is one global symbol of the ECU - two components with the same name cannot be linked."""
    seen: dict[str, str] = {}
    for comp in components.values():
        for m in comp.measurements:
            other = seen.get(m.name)
            if other is not None:
                raise RteError(f"components: measurement {m.label} and {other} are the same global variable "
                               f"{m.name}; rename one of the signals")
            seen[m.name] = m.label


def _mode_group(elements, path: str, where: str) -> ModeGroup:
    """Reads a MODE-DECLARATION-GROUP (RTE-08): modes, their values and the transition value."""
    node = elements.get(path)
    if node is None or local(node.tag) != "MODE-DECLARATION-GROUP":
        raise RteError(f"{where}: mode declaration group {path} not found")
    gname = text(node, "SHORT-NAME")
    category = text(node, "CATEGORY") or "ALPHABETIC_ORDER"
    if category not in ("ALPHABETIC_ORDER", "EXPLICIT_ORDER"):
        raise RteError(f"{where}: mode declaration group {gname}: category {category} is not supported "
                       f"(ALPHABETIC_ORDER, EXPLICIT_ORDER)")
    declared = node.findall("ar:MODE-DECLARATIONS/ar:MODE-DECLARATION", NS)
    names = [text(d, "SHORT-NAME") for d in declared]
    if not names:
        raise RteError(f"{where}: mode declaration group {gname} has no modes")
    for mode in names:
        if not mode or not C_IDENT_RE.match(mode):
            raise RteError(f"{where}: mode declaration group {gname}: mode name {mode!r} is no C identifier")
    if category == "EXPLICIT_ORDER":
        modes = {}
        for d, mode in zip(declared, names):
            value = text(d, "VALUE")
            if value is None or not value.isdigit():
                raise RteError(f"{where}: mode declaration group {gname} (EXPLICIT_ORDER): mode {mode} has no "
                               f"VALUE")
            modes[mode] = int(value)
    else:
        order = sorted(names)
        modes = {mode: order.index(mode) for mode in names}
    transition_text = text(node, "ON-TRANSITION-VALUE")
    if transition_text is not None:
        if not transition_text.isdigit():
            raise RteError(f"{where}: mode declaration group {gname}: ON-TRANSITION-VALUE {transition_text!r} is no "
                           f"number")
        transition = int(transition_text)
    elif category == "ALPHABETIC_ORDER":
        transition = len(modes)
    else:
        raise RteError(f"{where}: mode declaration group {gname} (EXPLICIT_ORDER) needs an ON-TRANSITION-VALUE "
                       f"(Rte_Mode returns it while a mode switch runs)")
    values = list(modes.values()) + [transition]
    if len(set(values)) != len(values):
        raise RteError(f"{where}: mode declaration group {gname}: mode values and ON-TRANSITION-VALUE must all "
                       f"differ ({', '.join(f'{m} {v}' for m, v in modes.items())}, transition {transition})")
    if max(values) > 0xFFFFFFFF:
        raise RteError(f"{where}: mode declaration group {gname}: values above 32 bit")
    initial = _ref(node, "INITIAL-MODE-REF")
    return ModeGroup(gname, modes, transition, initial.rsplit("/", 1)[1] if initial else None)


def parse_mode_port(elements, interface, pname: str, where: str) -> ModePort:
    """A required port with a MODE-SWITCH-INTERFACE (RTE-08): its mode group prototype and declaration group."""
    groups = interface.findall("ar:MODE-GROUP", NS)
    if len(groups) != 1:
        raise RteError(f"{where}: mode switch interface {text(interface, 'SHORT-NAME')} must have exactly one "
                       f"MODE-GROUP")
    tref = _ref(groups[0], "TYPE-TREF")
    if tref is None:
        raise RteError(f"{where}: MODE-GROUP without TYPE-TREF")
    return ModePort(pname, text(groups[0], "SHORT-NAME"), text(interface, "SHORT-NAME"),
                    _mode_group(elements, tref, where))


def parse_mode_event(ev, comp: "Component", where: str) -> ModeEvent:
    """A SWC-MODE-SWITCH-EVENT (RTE-08): activation and the modes of a mode port of the component."""
    activation = text(ev, "ACTIVATION")
    if activation not in MODE_ACTIVATIONS:
        raise RteError(f"{where}: mode switch event {text(ev, 'SHORT-NAME')}: activation {activation} is not "
                       f"supported ({', '.join(MODE_ACTIVATIONS)})")
    irefs = ev.findall("ar:MODE-IREFS/ar:MODE-IREF", NS) or ev.findall("ar:MODE-IREF", NS)
    expected = 2 if activation == "ON-TRANSITION" else 1
    if len(irefs) != expected:
        raise RteError(f"{where}: mode switch event {text(ev, 'SHORT-NAME')} ({activation}) needs {expected} "
                       f"MODE-IREF, has {len(irefs)}")
    port_key, modes = None, []
    for iref in irefs:
        port_ref = _ref(iref, "CONTEXT-PORT-REF")
        group_ref = _ref(iref, "CONTEXT-MODE-DECLARATION-GROUP-PROTOTYPE-REF")
        mode_ref = _ref(iref, "TARGET-MODE-DECLARATION-REF")
        if port_ref is None or group_ref is None or mode_ref is None:
            raise RteError(f"{where}: MODE-IREF needs CONTEXT-PORT-REF, CONTEXT-MODE-DECLARATION-GROUP-PROTOTYPE-REF "
                           f"and TARGET-MODE-DECLARATION-REF")
        key = f"{port_ref.rsplit('/', 1)[1]}.{group_ref.rsplit('/', 1)[1]}"
        if key not in comp.mode_ports:
            raise RteError(f"{where}: mode switch event on unknown mode port {key}")
        if port_key not in (None, key):
            raise RteError(f"{where}: an ON-TRANSITION event must refer to one mode port, not {port_key} and {key}")
        port_key = key
        group = comp.mode_ports[key].decl
        mode = mode_ref.rsplit("/", 1)[1]
        if mode not in group.modes:
            raise RteError(f"{where}: mode {mode} is not in the mode declaration group {group.name} "
                           f"({', '.join(group.modes)})")
        modes.append(mode)
    if activation == "ON-TRANSITION" and modes[0] == modes[1]:
        raise RteError(f"{where}: ON-TRANSITION from {modes[0]} to itself never happens")
    return ModeEvent(activation, port_key, tuple(modes))


def data_file(swc_dir: Path, name: str) -> Path:
    """Calibration values of a component (RTE-03): <swc_dir>/<Swc>_data.m, next to the model."""
    return swc_dir / f"{name}_data.m"


def load_parameter_values(comp: Component, swc_dir: Path, repo: Path | None = None):
    """Fills Parameter.phys/raw of every parameter of comp from its data file.

    The data file is the only place of the values; the model and the ARXML carry none that count (the init value
    of the port is what Embedded Coder saw at the last build and is ignored). Conversion to the stored value:
    raw = (physical * denominator - numerator[0]) / numerator[1]; scaled (fixed point) values round to the nearest
    step, unscaled integers must be whole numbers; every value must fit its platform type.
    """
    if not comp.parameters:
        return
    file = data_file(swc_dir, comp.name)
    shown = file
    if repo is not None:
        try:
            shown = file.resolve().relative_to(Path(repo).resolve())
        except ValueError:
            pass
    if not file.is_file():
        raise RteError(f"{comp.name}: {len(comp.parameters)} calibration parameter(s) "
                       f"({', '.join(p.element for p in comp.parameters.values())}) but no data file "
                       f"{shown.as_posix()}")
    try:
        data = mdata.load(file)
    except mdata.MDataError as e:
        raise RteError(f"{comp.name}: {e}") from None
    data.file = shown
    for param in comp.parameters.values():
        where = param.name
        try:
            values = data.get(param.element, where)
        except mdata.MDataError as e:
            raise RteError(str(e)) from None
        line = f"{shown.as_posix()}:{data.line_of(param.element)}"
        if len(values) != param.dim:
            shape = "a scalar" if param.dim == 1 else f"{param.dim} values"
            raise RteError(f"{where}: {line}: {param.element} has {len(values)} value(s), the model expects {shape}")
        raw = []
        for value in values:
            raw.append(_to_raw(param, value, where, line))
        param.phys, param.raw = list(values), raw


def _to_raw(param: Parameter, value, where: str, line: str):
    native = param.type.native
    if TYPE_RANGE[native] is None:
        return float(value)
    exact = (Fraction(str(value)) * param.denominator - param.numerator[0]) / param.numerator[1]
    if param.scaled:
        stored = math.floor(exact + Fraction(1, 2))
    else:
        if exact.denominator != 1:
            raise RteError(f"{where}: {line}: {value} is no whole number, but {param.element} is {native} "
                           f"without scaling")
        stored = int(exact)
    lo, hi = TYPE_RANGE[native]
    if not lo <= stored <= hi:
        phys = f" (stored value {stored})" if param.scaled else ""
        raise RteError(f"{where}: {line}: {value} does not fit {param.type.name} ({native} {lo} .. {hi}){phys}")
    return stored


def c_literal(native: str, value) -> str:
    """C initializer of one stored parameter value."""
    if native == "float32":
        text_ = repr(float(value))
        return (text_ if ("e" in text_ or "." in text_) else text_ + ".0") + "F"
    if native == "float64":
        text_ = repr(float(value))
        return text_ if ("e" in text_ or "." in text_) else text_ + ".0"
    if native == "boolean":
        return "1U" if value else "0U"
    if native.startswith("u"):
        return f"{int(value)}U"
    if native == "sint32" and int(value) == -(2**31):
        return "(-2147483647 - 1)"
    return str(int(value))


def board_signals(config: "nxconfig.Config") -> dict[str, set[str]]:
    """BSW signals the RTE may connect: the board functions the project uses (tools/nxconfig).

    RTE-11: the names are spelled as in the board profile (board.yaml) and project.yaml (adc_internal), e.g.
    12VBAT_CURRENT; a target is written with that name (adc.mv.12VBAT_CURRENT). The C identifier of the generated
    board headers (c_name, SIG_12VBAT_CURRENT) is an internal detail that build_rte derives (resolve_board_name).
    """
    adc = {c.name for c in config.selected("adc")}
    project = getattr(config, "project", None)
    # RTE-11: internal inputs of the converter (project.yaml adc_internal, e.g. BANDGAP): tools/bswgen puts them
    # into the same AnalogInput table as the board channels (Ai_<NAME>, scale factor 1 = mV at the converter), so
    # AnalogInput::get/AnalogInputScale::get read them like a pin. No circuit, no sensor plan: no adc.phys/adc.qlf.
    internal = {c.name for c in (getattr(project, "adc_internal", None) or [])}
    integration = getattr(config, "integration", None)
    # RTE-18: values of module instances (capture, hall, gd and every module with module.yaml rte)
    sources = device_sources(config)
    # IO-25: a digital output wired to a module instance (board devices[].connect) belongs to that driver; an RTE
    # write would bypass it (e.g. DRV_EN of a gate driver), so it is no dio.out target
    owned = {c_name(target): f"{d.name} ({d.module}, {key})" for d in (integration.devices if integration else [])
             for key, target in d.connect.items()}
    outputs = {c.name for c in config.selected("dio.outputs")}
    owned_outputs = {name: owned[c_name(name)] for name in outputs if c_name(name) in owned}
    sensors = set(getattr(config, "adc_sensors", {}))
    return {
        "dio.in": {c.name for c in config.selected("dio.inputs")},
        "dio.out": outputs - set(owned_outputs),
        "dio.out.owned": owned_outputs,
        "adc.mv": adc | internal,
        "adc.raw": adc | internal,
        "adc.internal": internal,
        # IO-18: channels with a sensor plan (fitted and used; without a sensor block the board scale in mV)
        "adc.phys": sensors,
        "adc.qlf": set(sensors),
        "adc.unfitted": {c.name for c in config.selected("adc") if not getattr(c, "fitted", True)},
        "pwm": {p.name for g, _ in config.selected_pwm() for p in g.pairs},
        **{prefix: set(source.names) for prefix, source in sources.items()},
        # IO-54: the pin events nxconfig accepted (tools/nxconfig/pinevent.py)
        PINEVENT_PREFIX: {ev.input for ev in (getattr(config, "pin_events", None) or [])},
        DEVICE_SOURCES: sources,
        # SYS-117: the safe-output groups of project.yaml safety.output_groups, by name -> bit of safety.degraded
        SAFETY_GROUPS: {g.name: k for k, g in enumerate(getattr(getattr(project, "safety", None), "output_groups",
                                                                None) or [])},
        # SYS-141: the PWM groups with a selection word (tools/nxconfig/safestate.py), only if one allows a short
        SAFETY_SAFE_STATES: safe_state_groups(config),
    }


@dataclass(frozen=True)
class SafeStateGroup:
    """SYS-141: a used PWM group with its selection word (the order of tools/nxconfig/safestate.py SafeStatePlan and
    of the generated NX_SAFE_STATE_GROUPS of tools/bswgen), the states the project allows and its start test."""
    name: str
    index: int
    allowed: tuple[str, ...]
    test: bool
    test_us: int | None
    gate_driver: str | None = None   # SYS-143: C identifier of the bound gate driver (readGateDriver_<ident>)

    @property
    def ident(self) -> str:
        return c_name(self.name)

    @property
    def enable_reader(self) -> str:
        """SYS-143 (E3): initializer of SafeStateTest::readEnable."""
        return f"&::integration::rte::readGateDriver_{self.gate_driver}" if self.gate_driver else "nullptr"

    @property
    def mask(self) -> str:
        return f"0x{sum(1 << SAFE_STATE_NUMBERS[s] for s in self.allowed):X}U"

    @property
    def shorts(self) -> tuple[str, ...]:
        return tuple(s for s in self.allowed if s != "freewheel")


def safe_state_groups(config) -> dict[str, SafeStateGroup]:
    """SYS-141: PWM group -> SafeStateGroup; empty unless a group allows an active short (no selection service)."""
    plan = getattr(config, "safe_states", None)
    if plan is None or not getattr(plan, "select", False):
        return {}
    drivers = safe_state_gate_drivers(config)
    return {g.name: SafeStateGroup(g.name, i, tuple(g.allowed), bool(g.test), g.test_us, drivers.get(g.name))
            for i, g in enumerate(plan.groups)}


def safe_state_gate_drivers(config) -> dict[str, str]:
    """SYS-143 (E3): PWM group -> C identifier of the gate driver bound to it (board pwm[].gate_driver), only for a
    device the project integrates whose module has RTE access gate_driver (the reader readGateDriver_<ident> of the
    family integration exists for exactly those)."""
    board = getattr(config, "board", None)
    if board is None:
        return {}
    readable = set()
    for source in device_sources(config).values():
        if source.access == "gate_driver":
            readable |= set(source.names)
    return {g.name: c_name(g.gate_driver) for g in (getattr(board, "pwm", None) or [])
            if getattr(g, "gate_driver", None) in readable}


def safe_state_tests(groups: dict[str, SafeStateGroup]) -> list[tuple[SafeStateGroup, str]]:
    """SYS-141: the start tests the project enables (pwm.<group>.safe_state_test), one per short of such a group."""
    return [(g, s) for g in groups.values() if g.test and g.test_us for s in g.shorts]


def safe_state_test_budget(config, tests: list[tuple[SafeStateGroup, str]]) -> int:
    """SYS-143 (E2): the budget of all holds of the start tests in microseconds - a third of the MCU watchdog
    time-out (see SAFE_STATE_WATCHDOG_MODULE). Raises RteError if the tests hold longer in sum, or if the project has
    no watchdog time-out to bound them with. 0 without a test."""
    if not tests:
        return 0
    integration = getattr(config, "integration", None)
    timeout = (integration.parameters(SAFE_STATE_WATCHDOG_MODULE).get(SAFE_STATE_WATCHDOG_TIMEOUT)
               if integration is not None else None)
    first = tests[0][0].name
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise RteError(f"project.yaml pwm.{first}.safe_state_test: the start test of the short holds the first "
                       f"application task in a busy wait, which needs a bound - the MCU watchdog time-out "
                       f"modules.{SAFE_STATE_WATCHDOG_MODULE}.{SAFE_STATE_WATCHDOG_TIMEOUT} (the family port has no "
                       f"{SAFE_STATE_WATCHDOG_MODULE} module)")
    budget = timeout * 1000 // SAFE_STATE_WATCHDOG_SERVICES
    total = sum(g.test_us for g, _ in tests)
    if total > budget:
        per_group = ", ".join(f"{g.name} {sum(1 for h, _ in tests if h is g)} x {g.test_us} us"
                              for g in dict.fromkeys(g for g, _ in tests))
        raise RteError(f"project.yaml pwm.{first}.safe_state_test_us: the start tests hold {total} us in all "
                       f"({per_group}, one test per short), more than the budget of {budget} us - a third of "
                       f"modules.{SAFE_STATE_WATCHDOG_MODULE}.{SAFE_STATE_WATCHDOG_TIMEOUT} {timeout} ms (the "
                       f"tasks below the first application task wait that long, doc/safety/concept.md section 4.3, SYS-143)")
    return budget


def safe_state_link(src: str, port: Port, dst: str, groups: dict[str, SafeStateGroup]) -> tuple[str, SafeStateGroup]:
    """SYS-141: checks a safety.safe_state*.<GROUP> connection; returns the kind and the group."""
    where = f"connection {src} -> {dst}"
    kind = next(k for k in SAFETY_SAFE_STATE_TARGETS if dst.startswith(k))
    name = dst[len(kind):]
    if not groups:
        raise RteError(f"{where}: no PWM group of the project allows an active short (project.yaml "
                       f"pwm.<group>.safe_states), so there is no safe state to select")
    group = groups.get(name)
    if group is None:
        raise RteError(f"{where}: unknown PWM group {name} (PWM groups of the project: {', '.join(groups)})")
    if not group.shorts:
        raise RteError(f"{where}: pwm.{name} allows freewheel only (project.yaml pwm.{name}.safe_states), there is "
                       f"no safe state to select")
    direction = "out" if kind == SAFETY_SAFE_STATE else "in"
    if port.direction != direction:
        what = ("the requested state is written by a provided port" if kind == SAFETY_SAFE_STATE
                else "the state in force is read by a required port")
        raise RteError(f"{where}: {port.direction}put port cannot use {dst} ({what})")
    types = SAFETY_SAFE_STATE_TYPES if kind != SAFETY_SAFE_STATE_QLF else QLF_TYPES
    if port.type.native not in types:
        raise RteError(f"{where}: type {port.type.native} does not fit {dst} ({', '.join(sorted(types))})")
    return kind, group


def name_hint(known, name: str, kind: str) -> str:
    """RTE-11: hint for a target written with the C identifier (SIG_12VBAT_CURRENT) or another case (vbat)."""
    matches = sorted(n for n in known if n != name and (c_name(n) == name or c_name(n) == c_name(name)))
    if not matches:
        return ""
    return (f"; write {kind}.{matches[0]} as in the board profile (the C identifier {c_name(matches[0])} "
            f"is internal)")


def resolve_board_name(known, name: str, where: str, kind: str) -> str:
    """C identifier of a board target name the project uses (RTE-11); name is spelled as in the board profile.

    Two board names with the same C identifier (e.g. 12V and SIG_12V) cannot both be generated; the connection
    that meets such a pair is refused instead of silently taking one of them.
    """
    ident = c_name(name)
    others = sorted(n for n in known if n != name and c_name(n) == ident)
    if others:
        raise RteError(f"{where}: board names {name} and {', '.join(others)} give the same C identifier {ident}, "
                       f"rename one of them in the board profile")
    return ident


def wakeup_sources(config: "nxconfig.Config | None") -> dict[str, int]:
    """Names of ecu.wakeup.<source> and their bit masks: fixed reasons, LOCAL0..7, CAN buses of the project."""
    sources = {name: 1 << bit for name, bit in WAKEUP_BITS.items()}
    sources.update({f"LOCAL{i}": 1 << (WAKEUP_LOCAL_SHIFT + i) for i in range(WAKEUP_GROUP_SIZE)})
    integration = getattr(config, "integration", None) if config is not None else None
    for bus in (integration.can if integration is not None else []):
        if bus.name in sources:
            raise RteError(f"ecu.wakeup.{bus.name}: CAN bus name collides with a fixed wake-up source")
        if bus.index >= WAKEUP_GROUP_SIZE:
            raise RteError(f"ecu.wakeup.{bus.name}: CAN bus index {bus.index} has no wake-up bit "
                           f"(at most {WAKEUP_GROUP_SIZE} buses)")
        sources[bus.name] = 1 << (WAKEUP_CAN_SHIFT + bus.index)
    return sources


def ecu_state_manifest(config: "nxconfig.Config | None"):
    modules = getattr(config, "modules", None)
    manifest = modules.manifests.get(ECU_STATE_MODULE) if modules is not None else None
    if manifest is None:
        manifest = nxconfig.config.load_modules().manifests.get(ECU_STATE_MODULE)
    if manifest is None:
        raise RteError(f"module manifest libs/bsw/{ECU_STATE_MODULE}/module.yaml not found")
    return manifest


def load_ecu_state(ecu, config: "nxconfig.Config | None") -> tuple[dict, bool]:
    """Parameters of the ECU state management: project values (project.yaml modules.ecuState), defaults filled in.

    The values are a usage decision of the project (doc/architecture/README.md, section 4) and are checked by
    tools/nxconfig with file, line and YAML path (SYS-08); this function only takes them over. A section
    ecu_state: in the ECU description is refused, so the values cannot stand in two places. Without a project
    (config None) the defaults of module.yaml apply.

    Returns the values and whether the project marks them with verify: true.
    """
    ecu_description(ecu, "ecu_state")     # the schema refuses the section (EcuDescription.ecu_state)
    manifest = ecu_state_manifest(config)
    integration = getattr(config, "integration", None) if config is not None else None
    use = integration.modules.get(ECU_STATE_MODULE) if integration is not None else None
    if use is not None:
        return dict(use.parameters), use.verify
    if integration is not None:
        # a loaded project always has the entry (nxconfig: modules with project_parameters are part of every build)
        raise RteError(f"project has no checked parameters of module {ECU_STATE_MODULE} "
                       f"(module.yaml project_parameters missing?)")
    missing = [name for name, spec in manifest.parameters.items() if spec.default is None]
    if missing:
        raise RteError(f"module {ECU_STATE_MODULE}: parameters without default need a project: {', '.join(missing)}")
    return {name: spec.default for name, spec in manifest.parameters.items()}, False


def ecu_state_wakeup(values: dict, config: "nxconfig.Config | None") -> tuple[int, dict[str, str]]:
    """Wake-up sources the ECU arms before the power down (SYS-02): mask and resolved names per parameter.

    wakeup_can names CAN buses of the project (one bit per bus, ARCH-07), wakeup_local the local wake-up inputs
    of the power supply, wakeup_cyclic_ms adds the TIMER bit. A name that is no bus of the project is an error, so
    a source that does not exist on this board cannot be selected silently; for project values tools/nxconfig
    reports it first, with line and YAML path (module.yaml names: can_bus).
    """
    integration = getattr(config, "integration", None) if config is not None else None
    known = {
        "wakeup_can": ("CAN bus",
                       {bus.name: 1 << (WAKEUP_CAN_SHIFT + bus.index)
                        for bus in (integration.can if integration is not None else [])}),
        "wakeup_local": ("local wake-up input",
                         {f"LOCAL{i}": 1 << (WAKEUP_LOCAL_SHIFT + i) for i in range(WAKEUP_GROUP_SIZE)}),
    }
    mask, resolved = 0, {}
    for name, (what, bits) in known.items():
        text = values[name]
        if text == "none":
            chosen = []
        elif text == "all":
            chosen = list(bits)
        else:
            chosen = text.split(",")
            for entry in chosen:
                if entry not in bits:
                    offer = f"known: {', '.join(bits)}" if bits else f"the project uses no {what}"
                    raise RteError(f"ecu_state.{name}: {entry} is no {what} of the project ({offer})")
            if len(set(chosen)) != len(chosen):
                raise RteError(f"ecu_state.{name}: a {what} is named twice ({text})")
        for entry in chosen:
            mask |= bits[entry]
        resolved[name] = ",".join(chosen) if chosen else "none"
    if values["wakeup_cyclic_ms"]:
        mask |= 1 << WAKEUP_BITS["TIMER"]
    return mask, resolved


def ecu_state_nvm_step(nvm: "NvmPlan | None", config: "nxconfig.Config | None") -> str | None:
    """What makes the run-down of this project have an NvM step (ShutdownStep::NVM), None if nothing does.

    The family port fills the step with the NvM of the RTE (NvM blocks of the components, the fault memory, the ECU
    statistics: plan_nvm()) and, around it, with the data flash gate as soon as the project has data flash areas
    (nvm.data_flash, NVM-23)."""
    if nvm is not None and nvm.blocks:
        return f"NvM blocks {', '.join(b.name for b in nvm.blocks)}"
    use = getattr(getattr(config, "project", None), "nvm", None) if config is not None else None
    areas = getattr(getattr(use, "data_flash", None), "areas", None) or {}
    if areas:
        return f"data flash areas {', '.join(areas)}"
    return None


def check_ecu_state_step_error(values: dict, nvm: "NvmPlan | None", config: "nxconfig.Config | None") -> None:
    """SYS-174 (safety review 2026-10-02, SYS69-S5, finding ECS-F04): step_error continue is refused for a project
    whose run-down has an NvM step. A failed or timed-out NvM step may still program or erase the data flash, and the
    power down after a continued run-down would cut it - a half written block. The state machine never continues
    past that step anyway (ecuState isContinuableStep()); the refusal keeps the project value from promising it."""
    if values.get("step_error") != "continue":
        return
    reason = ecu_state_nvm_step(nvm, config)
    if reason is not None:
        raise RteError(f"modules.{ECU_STATE_MODULE}.step_error: continue is not allowed in a project whose run-down "
                       f"has an NvM step ({reason}): a failed or timed-out NvM step may still program or erase the "
                       f"data flash, and the power down would cut it (SYS-174) - use restart")


def render_ecu_state_config(values: dict, verify: bool, manifest, source: str,
                            config: "nxconfig.Config | None" = None) -> str:
    mask, resolved = ecu_state_wakeup(values, config)
    lines = [f"// Generated by tools/rtegen from {source} - do not edit.",
             f"// Parameters of the ECU state management (libs/bsw/{ECU_STATE_MODULE}/module.yaml): project.yaml"
             f" modules.{ECU_STATE_MODULE}, defaults from the manifest. Used through ecuState/EcuStateIntegration.h.",
             "", "#pragma once", ""]
    if verify:
        lines += ["// parameter values marked verify: not confirmed", ""]
    for name, spec in manifest.parameters.items():
        value = values[name]
        if name in WAKEUP_LIST_PARAMETERS:
            text = f'"{value}"'  # names, not a value: the mask below is what the code uses
        elif spec.type == "int":
            text = f"{value}U" if spec.min is not None and spec.min >= 0 else str(value)
        elif spec.type == "bool":
            text = "1" if value else "0"
        else:
            text = str(value).upper()  # enumerator of the C++ enum class (pattern of the manifest)
        default = " (default)" if value == spec.default else ""
        lines.append(f"#define NX_ECU_STATE_PARAM_{name.upper()} {text} // {name}{default}")
    cyclic = f", cyclic {values['wakeup_cyclic_ms']} ms" if values["wakeup_cyclic_ms"] else ""
    lines.append(f"#define NX_ECU_STATE_WAKEUP_SOURCES 0x{mask:08X}U"
                 f" // wakeup_can {resolved['wakeup_can']}, wakeup_local {resolved['wakeup_local']}{cyclic}")
    lines.append(f"#define NX_ECU_STATE_VERIFY {1 if verify else 0}")
    return "\n".join(lines) + "\n"


def render_qlf_header() -> str:
    """Rte_Qlf.h: the qualifier constants for C and C++ (same values as the old MBDT SYSCONST.QLF_*)."""
    lines = ["/* Generated by tools/rtegen - do not edit.",
             " * Signal qualifier values (doc/architecture/signal-qualifier.md, COM-03). The RTE writes one",
             " * qualifier per received CAN message (target can.qlf.<Message>); a component reads it like any",
             " * other required port. Precedence of several errors: ERROR > ERROR_TIMEOUT > ERROR_CRC >",
             " * ERROR_ALIVE. The same values are generated for MATLAB in Rte_Qlf.m. */", "",
             "#ifndef RTE_QLF_H", "#define RTE_QLF_H", "", "#include <stdint.h>", "",
             "typedef uint8_t Rte_QlfType;", ""]
    width = max(len(name) for name, _, _ in QLF_VALUES) + len("QLF_")
    for name, value, comment in QLF_VALUES:
        lines.append(f"#define {('QLF_' + name).ljust(width)} ((Rte_QlfType){value}u) /* {comment} */")
    lines += ["", "#endif /* RTE_QLF_H */"]
    return "\n".join(lines) + "\n"


def render_qlf_matlab() -> str:
    """Rte_Qlf.m: run it in MATLAB to get the QLF_* constants into the workspace of a model."""
    lines = ["% Generated by tools/rtegen - do not edit.",
             "% Signal qualifier values of the RTE (doc/architecture/signal-qualifier.md, COM-03), same values as",
             "% Rte_Qlf.h and as SYSCONST.QLF_* of the old MBDT project. Run this script (or",
             "% run('<build>/generated/rte/Rte_Qlf.m')) before a model that compares a qualifier port, e.g.",
             "% Qlf == QLF_OK.", ""]
    width = max(len(name) for name, _, _ in QLF_VALUES) + len("QLF_")
    for name, value, comment in QLF_VALUES:
        lines.append(f"{('QLF_' + name).ljust(width)} = uint8({value}); % {comment}")
    return "\n".join(lines) + "\n"


def bus_of(where: str, name: str, buses: list[str]) -> str:
    """Checks a configured CAN bus against the buses of the project (use.can) and returns it."""
    if name not in buses:
        raise RteError(f"{where}: bus {name} is not a CAN bus of the project "
                       f"(use.can: {', '.join(buses) if buses else 'none'})")
    return name


def load_can(ecu, repo: Path, buses: list[str], ecu_file: "Path | None" = None) -> CanConfig | None:
    """Section can: of the ECU description (model or mapping, see ecu_description) with its DBC.

    RTE-22: `ecu_file` is the rte.yaml `can.dbc` is relative to (format 2). Without it - a caller that only has
    the mapping - the DBC stays relative to `repo`, the format 1 fallback.
    """
    cfg = ecu_description(ecu, "can").can
    if cfg is None:
        return None
    try:
        db = dbcgen.load(path_of(ecu, ecu_file, repo, cfg.dbc))
        db.for_node(cfg.node)
    except dbcgen.DbcError as e:
        raise RteError(f"can: {e}") from None
    if cfg.bus is None and not buses:
        raise RteError("can: the project uses no CAN bus (use.can), so no signals can be sent or received")
    # default: the first CAN bus of the project (ARCH-07); the signals run on exactly one bus
    bus = bus_of("can.bus", cfg.bus if cfg.bus is not None else buses[0], buses)
    return CanConfig(db, cfg.node, bus, list(cfg.monitor), cfg.task,
                     bus_ident=c_name(bus), qualifiers=load_qualifiers(cfg.qualifiers))


def load_qualifiers(given: "dict[str, nxmodel.QualifierUse | None]") -> dict:
    """Section can.qualifiers (keys and value ranges checked by the schema). DBC checks follow in finish_qualifiers."""
    result = {}
    for name, entry in given.items():
        where = f"can.qualifiers.{name}"
        entry = entry if entry is not None else nxmodel.QualifierUse()
        result[name] = Qualifier(name, where, entry.timeout_ms, entry.debounce_ms, entry.on_error,
                                 load_e2e(entry.e2e, f"{where}.e2e"))
    return result


def load_e2e(given: "nxmodel.E2eUse | None", where: str) -> E2eConfig | None:
    """Section can.qualifiers.<Message>.e2e: the rules of the profile on top of the schema (keys, value ranges)."""
    if given is None:
        return None
    profile = given.profile
    spec = E2E_PROFILES[profile]
    e2e = E2eConfig(profile, where, given.crc, given.counter, max_delta=given.max_delta_counter)
    if e2e.max_delta > spec["counter_modulo"] - 1:
        raise RteError(f"{where}.max_delta_counter: {e2e.max_delta} is above the counter range of {profile} "
                       f"(0..{spec['counter_modulo'] - 1})")
    if profile == "P02":
        for key in ("data_id", "data_id_mode", "data_id_nibble"):
            if getattr(given, key) is not None:
                raise RteError(f"{where}.{key}: profile P02 uses data_id_list (one data id per counter value)")
        ids = given.data_id_list
        if ids is None or len(ids) != E2E_DATA_ID_LIST_SIZE:
            raise RteError(f"{where}.data_id_list: profile P02 needs exactly {E2E_DATA_ID_LIST_SIZE} data ids "
                           f"(one per counter value 0..{E2E_DATA_ID_LIST_SIZE - 1})")
        e2e.data_id_list = list(ids)
        for i, value in enumerate(e2e.data_id_list):
            if value > 0xFF:
                raise RteError(f"{where}.data_id_list[{i}]: {value} does not fit the 8 bit data id of P02")
        return e2e
    if given.data_id_list is not None:
        raise RteError(f"{where}.data_id_list: only profile P02 uses a data id list, {profile} uses data_id")
    e2e.data_id = given.data_id if given.data_id is not None else 0
    limit = (1 << spec["data_id_bits"]) - 1
    if e2e.data_id > limit:
        raise RteError(f"{where}.data_id: 0x{e2e.data_id:X} does not fit the {spec['data_id_bits']} bit data id "
                       f"of {profile} (0..0x{limit:X})")
    mode = given.data_id_mode or "BOTH"
    if mode not in spec["modes"]:
        raise RteError(f"{where}.data_id_mode: {mode} is not a data id mode of {profile} "
                       f"({', '.join(spec['modes'])})")
    e2e.data_id_mode = mode
    if mode == "NIBBLE":
        # the high nibble of the data id travels in the frame: its position is a DBC signal as well
        if e2e.data_id > 0x0FFF:
            raise RteError(f"{where}.data_id: mode NIBBLE allows at most 12 bit (0x{e2e.data_id:X} given)")
        if not given.data_id_nibble:
            raise RteError(f"{where}.data_id_nibble: mode NIBBLE sends the high nibble of the data id, "
                           f"name the DBC signal that carries it")
        e2e.nibble_signal = given.data_id_nibble
    elif given.data_id_nibble is not None:
        raise RteError(f"{where}.data_id_nibble: only mode NIBBLE sends a data id nibble (mode {mode})")
    return e2e


def load_uds(ecu, can: CanConfig | None, buses: list[str]) -> str | None:
    """Diagnostic bus of the application UDS stack (section uds:); default: the bus of the CAN signals."""
    cfg = ecu_description(ecu, "uds").uds
    if cfg is None:
        if can is not None:
            return can.bus
        return buses[0] if buses else None
    return bus_of("uds.bus", cfg.bus, buses)


def render_uds_bus_config(bus: str | None, source: str) -> str:
    """uds/udsBusConfiguration.h: the bus ecu/uds runs DoCAN on (::busid::NX_UDS_BUS)."""
    lines = [
        f"// Generated by tools/rtegen from {source} - do not edit.",
        "// CAN bus of the application diagnostic stack (ecu/uds): section uds: of the ECU description,",
        "// by default the bus of the CAN signals. The bus ids come from busid/BusId.h (tools/bswgen).",
        "",
        "#pragma once",
        "",
    ]
    if bus is None:
        lines += ["// the project uses no CAN bus: no diagnostic bus", "#define NX_UDS_BUS_MISSING 1"]
    else:
        lines += [f"#define NX_UDS_BUS      {c_name(bus)} // ::busid::{c_name(bus)}",
                  f'#define NX_UDS_BUS_NAME "{bus}"']
    return "\n".join(lines) + "\n"


XCP_EXTENDED_FLAG = 0x80000000   # project.yaml xcp: bit 31 marks a 29-bit identifier (like the DBC and ::can::CanId)


@dataclass(frozen=True)
class XcpCan:
    """XCP on CAN of the project (TOOL-26, project.yaml xcp:)."""
    bus: str
    master_id: int      # master -> ECU (commands); bit 31: extended
    slave_id: int       # ECU -> master (responses)


def load_xcp(config, buses: list[str], uds_bus: str | None) -> XcpCan | None:
    """project.yaml xcp: bus (default: the diagnostic bus) and identifiers; None: the project has no XCP.

    Range and collisions are checked by tools/nxconfig (diag.py, with the YAML path) before rtegen runs.
    """
    use = getattr(getattr(config, "project", None), "xcp", None)
    if use is None:
        return None
    if use.bus is None and uds_bus is None:
        raise RteError("xcp: no bus (xcp.bus is not set and the project has no diagnostic bus)")
    bus = bus_of("xcp.bus", use.bus, buses) if use.bus is not None else uds_bus
    return XcpCan(bus, use.master_id, use.slave_id)


def render_xcp_config(xcp: XcpCan | None, source: str) -> str:
    """xcp/xcpCanConfiguration.h: bus and identifiers of XCP on CAN (ecu/xcp, tools/a2lgen), for every project."""
    lines = [
        f"// Generated by tools/rtegen from {source} and project.yaml xcp: - do not edit.",
        "// XCP on CAN (TOOL-26): read by the target (ecu/xcp) and by tools/a2lgen (IF_DATA XCP_ON_CAN).",
        "",
        "#pragma once",
        "",
    ]
    if xcp is None:
        lines += ["// the project has no xcp: section: no XCP slave in the application", "#define NX_XCP_MISSING 1"]
    else:
        lines += [f"#define NX_XCP_BUS           {c_name(xcp.bus)} // ::busid::{c_name(xcp.bus)}",
                  f'#define NX_XCP_BUS_NAME      "{xcp.bus}"',
                  f"#define NX_XCP_CAN_ID_MASTER 0x{xcp.master_id:X}UL // master -> ECU (CTO), bit 31: extended id",
                  f"#define NX_XCP_CAN_ID_SLAVE  0x{xcp.slave_id:X}UL // ECU -> master (RES/ERR)"]
    return "\n".join(lines) + "\n"


# COM-08: receive message buffers of the FlexCAN driver per bus (tools/bswgen, canDeviceConfiguration.h). The board
# port checks at compile time that the generated filter table and the device configuration agree. COM-26: the split
# depends on the message buffers of the bus' instance (can_rx_splits); these are the values of an instance with 32.
FLEXCAN_RX_BUFS_STD = nxbswgen.CAN_RX_BUFS_STD
FLEXCAN_RX_BUFS_EXT = nxbswgen.CAN_RX_BUFS_EXT
CAN_STD_ID_BITS = 0x7FF
CAN_EXT_ID_BITS = 0x1FFFFFFF


@dataclass(frozen=True)
class CanRxFilter:
    """Acceptance filter of one receive message buffer: mask bit 1 = identifier bit compared."""
    buffer: int
    id: int
    mask: int
    extended: bool
    covers: tuple[str, ...]      # names of the identifiers the filter was made for (comment only)
    repeats: int | None = None   # COM-36: buffer whose filter this one repeats (queue of equal ids), None = first


def can_rx_ids(can: CanConfig | None, uds_bus: str | None, config, buses: list[str],
               xcp: XcpCan | None = None) -> dict[str, list[tuple[int, bool, str]]]:
    """Identifiers each CAN bus of the project receives: (id, extended, name) per bus, every bus present.

    Received DBC messages of the ECU description (can.rx: connected or monitored) on can.bus, the diagnostic
    request ids (project diagnostics) on the UDS bus and the XCP command id (project xcp, TOOL-26) on the XCP
    bus. Nothing else in the application listens on CAN.
    """
    ids: dict[str, list[tuple[int, bool, str]]] = {bus: [] for bus in buses}
    if can is not None:
        for entry in can.rx.values():
            ids[can.bus].append((entry.message.frame_id, entry.message.extended, entry.message.name))
    diag = getattr(config, "diagnostics", None) if config is not None else None
    if diag is not None and uds_bus is not None:
        ids[uds_bus].append((diag.request_id, False, "diagnostics.request_id"))
        ids[uds_bus].append((diag.functional_id, False, "diagnostics.functional_id"))
    if xcp is not None:
        ids[xcp.bus].append((xcp.master_id & CAN_EXT_ID_BITS, bool(xcp.master_id & XCP_EXTENDED_FLAG),
                             "xcp.master_id"))
    check_rx_id_collisions(ids)
    return ids


def check_rx_id_collisions(ids: dict[str, list[tuple[int, bool, str]]]):
    """COM-36: two receivers of one identifier on one bus are refused, not merged into one filter.

    tools/nxconfig (diag.py) refuses such a project with the YAML path before rtegen runs; this is the same rule for
    a generate without that check. A frame of such an id would reach both stacks (UDS and XCP, or a DBC message).
    """
    for bus, entries in ids.items():
        seen: dict[tuple[int, bool], str] = {}
        for value, extended, name in entries:
            other = seen.setdefault((value, extended), name)
            if other != name:
                width = 8 if extended else 3
                raise RteError(f"receive filter of {bus}: {name} and {other} are both the "
                               f"{'29' if extended else '11'}-bit id 0x{value:0{width}X}")


def can_rx_splits(config, buses: list[str]) -> dict[str, tuple[int, int]]:
    """COM-26: (standard, extended) receive buffers of every bus, from the message buffers of its FlexCAN instance.

    Same split as the device configuration of tools/bswgen (can_buffer_split); a bus without instance data (no
    configuration, stub configurations of tests) gets the split of an instance with 32 buffers.
    """
    splits = {bus: (FLEXCAN_RX_BUFS_STD, FLEXCAN_RX_BUFS_EXT) for bus in buses}
    integration = getattr(config, "integration", None) if config is not None else None
    if integration is None or getattr(config, "derivative", None) is None:
        return splits
    for use in integration.can:
        if use.name in splits:
            try:
                split = nxbswgen.can_buffer_split(config, use)
            except nxbswgen.ConfigError as e:
                # TOOL-113: an RteError like every other refusal of the model phase - validate() reports it as a
                # message instead of taking the Studio check down, the command line prints it instead of a trace
                raise RteError(str(e)) from None
            splits[use.name] = (split.rx_std, split.rx_ext)
    return splits


def _group_ids(ids: list[tuple[int, str]], buffers: int, width: int) -> list[tuple[int, int, tuple[str, ...]]]:
    """Groups identifiers into at most `buffers` (id, mask) filters.

    Exact filters while the identifiers fit, otherwise the pair of filters is merged whose common filter lets the
    fewest identifiers through that nobody receives (greedy; the software filter of the transceiver drops them).
    """
    groups: list[tuple[int, int, dict[int, str]]] = []
    for value, name in ids:
        for g in groups:
            if g[0] == value:
                break
        else:
            groups.append((value, width, {value: name}))
    groups.sort(key=lambda g: g[0])
    bits = bin(width).count("1")
    while len(groups) > buffers:
        best = None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                ia, ma, _ = groups[a]
                ib, mb, _ = groups[b]
                mask = ma & mb & ~(ia ^ ib) & width
                covered = {k: v for g in groups if (g[0] & mask) == (ia & mask) and (g[1] & mask) == mask
                           for k, v in g[2].items()}
                extra = (1 << (bits - bin(mask).count("1"))) - len(covered)
                if best is None or extra < best[0]:
                    best = (extra, ia & mask, mask)
        _, value, mask = best
        merged: dict[int, str] = {}
        rest = []
        for g in groups:
            if (g[0] & mask) == value and (g[1] & mask) == mask:
                merged.update(g[2])
            else:
                rest.append(g)
        groups = sorted(rest + [(value, mask, merged)], key=lambda g: min(g[2]))
    return [(value, mask, tuple(names[k] for k in sorted(names))) for value, mask, names in groups]


def can_rx_filters(ids: list[tuple[int, bool, str]], std_buffers: int = FLEXCAN_RX_BUFS_STD,
                   ext_buffers: int = FLEXCAN_RX_BUFS_EXT) -> list[CanRxFilter]:
    """Filter of every receive message buffer of one bus (buffers 0.. standard ids, then extended ids).

    All buffers of a type get a filter as long as that type has identifiers: the filters repeat, so frames of the
    same identifier queue in several buffers instead of overwriting each other ([RM] S32K1xx Rev. 13 p. 1900).
    A repeated filter names the buffer it repeats (COM-36: three ids in six buffers are three filters, each twice,
    not six receivers). Buffers of a type without identifiers get no entry (the port deactivates them).
    """
    result = []
    for extended, first, count, width in ((False, 0, std_buffers, CAN_STD_ID_BITS),
                                          (True, std_buffers, ext_buffers, CAN_EXT_ID_BITS)):
        wanted = [(value, name) for value, ext, name in ids if ext == extended]
        groups = _group_ids(wanted, count, width) if count else []
        for i in range(count if groups else 0):
            value, mask, names = groups[i % len(groups)]
            result.append(CanRxFilter(first + i, value, mask, extended, names,
                                      first + i % len(groups) if i >= len(groups) else None))
    return result


def render_can_rx_filter_config(filters: dict[str, list[CanRxFilter]], source: str,
                                splits: dict[str, tuple[int, int]] | None = None) -> str:
    """can/canRxFilterConfiguration.h: hardware receive filter of every CAN bus (board port CanSystem).

    `splits` holds the receive buffers (standard, extended) of each bus (can_rx_splits); without it every bus of
    `filters` has the split of an instance with 32 buffers.
    """
    lines = [
        f"// Generated by tools/rtegen from {source} - do not edit.",
        "// Receive filter of every CAN bus of the project (COM-08): the identifiers the application receives",
        "// (can.rx of the ECU description, diagnostic request ids on the UDS bus, XCP command id on the XCP bus),",
        "// grouped into the receive message buffers of the FlexCAN driver. A buffer without entry receives nothing.",
        "// Fewer filters than buffers of a type: the filters repeat over all of them, so frames of one id queue in",
        "// several buffers instead of overwriting each other; a repeated entry names the buffer it repeats (COM-36).",
        "",
        "#pragma once",
        "",
        "// Receive buffers of every bus, as the message buffers of its FlexCAN instance are split (COM-26); the board",
        "// port compares them with the device configuration of the bus.",
    ]
    for bus in filters:
        std, ext = (splits or {}).get(bus, (FLEXCAN_RX_BUFS_STD, FLEXCAN_RX_BUFS_EXT))
        lines += [f"#define NX_CAN_RX_FILTER_BUFFERS_STD_{c_name(bus)} {std}U",
                  f"#define NX_CAN_RX_FILTER_BUFFERS_EXT_{c_name(bus)} {ext}U"]
    lines.append("")
    for bus, entries in filters.items():
        lines.append(f"// {bus}: {_filter_use(entries, False)}, {_filter_use(entries, True)}")
    lines += [
        "",
        "// X(<bus>, <message buffer>, <id>, <mask>, <extended>): id and mask right aligned, mask bit 1 = compared",
    ]
    rows = []
    for bus, entries in filters.items():
        for f in entries:
            width = 8 if f.extended else 3
            names = ", ".join(f.covers[:3]) + (f", +{len(f.covers) - 3}" if len(f.covers) > 3 else "")
            if f.repeats is not None:
                names = f"repeats buffer {f.repeats}: {names}"
            rows.append(f"    X({c_name(bus)}, {f.buffer}U, 0x{f.id:0{width}X}UL, 0x{f.mask:0{width}X}UL, "
                        f"{'true' if f.extended else 'false'}) /* {names} */")
    if rows:
        lines.append("#define NX_CAN_RX_FILTERS(X) \\")
        lines += [r + " \\" for r in rows[:-1]] + [rows[-1]]
    else:
        lines.append("#define NX_CAN_RX_FILTERS(X)")
    return "\n".join(lines) + "\n"


def _filter_use(entries: list[CanRxFilter], extended: bool) -> str:
    """Summary line part of one identifier type: filters, buffers and ids (COM-36, reads the repeats at a glance)."""
    kind = "extended" if extended else "standard"
    own = [f for f in entries if f.extended == extended]
    if not own:
        return f"no {kind} id"
    filters = [f for f in own if f.repeats is None]
    ids = sum(len(f.covers) for f in filters)
    return (f"{len(filters)} {kind} filter{'s' if len(filters) != 1 else ''} for {ids} id{'s' if ids != 1 else ''} "
            f"in {len(own)} buffer{'s' if len(own) != 1 else ''}")


def fmt(value: Fraction) -> str:
    return f"{float(value):g}"


# COM-38: the GenMsgSendType names offered for a sent message (dbcgen.SEND_MODES knows the synonyms)
TX_SEND_TYPES = dbcgen.SEND_TYPE_CHOICES


def tx_mode_problem(msg: "dbcgen.Message", dbc: str) -> str:
    """COM-38: why the RTE cannot send a message the way its DBC says (GenMsgSendType, GenMsgCycleTime,
    GenMsgDelayTime), "" if it can. The task period is checked later (tx_timing_problem)."""
    mode, name, cycle, delay = msg.send_mode, msg.name, msg.cycle_ms, msg.delay_ms
    if mode is None:
        return (f"message {name} has GenMsgSendType {msg.send_type} ({dbc}), which the RTE does not send; "
                f"supported: {', '.join(TX_SEND_TYPES)}")
    if mode == dbcgen.SEND_CYCLIC and cycle <= 0:
        return (f"message {name} has no GenMsgCycleTime ({dbc}): a cyclic message needs one "
                f"(or GenMsgSendType Spontaneous: sent on change)")
    if mode == dbcgen.SEND_MIXED and cycle <= 0:
        return (f"message {name} has GenMsgSendType {msg.send_type} (cyclic and on change) but no "
                f"GenMsgCycleTime ({dbc})")
    if mode == dbcgen.SEND_EVENT and cycle > 0:
        return (f"message {name} has GenMsgSendType {msg.send_type} (on change only) and GenMsgCycleTime "
                f"{cycle} ms ({dbc}): set the cycle time to 0 or use CyclicAndSpontaneous")
    if mode == dbcgen.SEND_MIXED and delay > cycle:
        return (f"message {name}: GenMsgDelayTime {delay} ms is longer than GenMsgCycleTime {cycle} ms ({dbc}), "
                f"the cyclic sends would be held back")
    return ""


def tx_delay_problem(msg: "dbcgen.Message", period_ms: int, task: str, dbc: str) -> str:
    """COM-38: the minimum gap of an event or mixed message against the period of the CAN task, "" if it fits.
    (The cycle time is checked in finish_can; an event message has none, tx_mode_problem.)"""
    if msg.send_mode in (dbcgen.SEND_EVENT, dbcgen.SEND_MIXED) and msg.delay_ms % period_ms:
        return (f"GenMsgDelayTime {msg.delay_ms} ms of {msg.name} ({dbc}) is no multiple of the period "
                f"{period_ms} ms of the CAN task {task} (can.task)")
    return ""


def can_link(can: CanConfig, src: str, port: Port, dst: str, direction: str) -> tuple[CanMessage, CanSignalLink]:
    """Checks a can.rx/can.tx connection against the DBC and the port type."""
    where = f"connection {src} -> {dst}"
    if can is None:
        raise RteError(f"{where}: no DBC configured (section can: in the ECU description)")
    parts = dst.split(".")
    if len(parts) != 4:
        raise RteError(f"{where}: expected can.rx.<Message>.<Signal> or can.tx.<Message>.<Signal>")
    msg_name, sig_name = parts[2], parts[3]
    try:
        msg = can.dbc.message(msg_name)
        if msg.problem:
            raise RteError(f"{where}: {msg.problem}")
        sig = msg.signal(sig_name)
    except dbcgen.DbcError as e:
        raise RteError(f"{where}: {e}") from None
    native = port.type.native
    rng = TYPE_RANGE[native]
    if direction == "rx":
        if can.node not in sig.receivers:
            raise RteError(f"{where}: node {can.node} is no receiver of {msg_name}.{sig_name} "
                           f"(receivers: {', '.join(sig.receivers) or '-'})")
        if rng is None:
            kind = "float"
        else:
            if not sig.integer:
                raise RteError(f"{where}: signal resolution (factor {fmt(sig.factor)}, offset {fmt(sig.offset)}) "
                               f"is not integer, use a float32/float64 port")
            if sig.minimum < rng[0] or sig.maximum > rng[1]:
                raise RteError(f"{where}: signal range [{fmt(sig.minimum)}|{fmt(sig.maximum)}] does not fit "
                               f"type {native} [{rng[0]}|{rng[1]}]")
            kind = "bool" if native == "boolean" else "int"
    else:
        if can.node not in msg.senders:
            raise RteError(f"{where}: node {can.node} is no sender of {msg_name} "
                           f"(senders: {', '.join(msg.senders) or '-'})")
        problem = tx_mode_problem(msg, can.dbc.path.name)
        if problem:
            raise RteError(f"{where}: {problem}")
        if rng is None:
            kind = "float"
        elif native == "boolean":
            if not (sig.to_raw(0) != sig.to_raw(1) and sig.to_physical(sig.to_raw(0)) == 0
                    and sig.to_physical(sig.to_raw(1)) == 1):
                raise RteError(f"{where}: signal range [{fmt(sig.minimum)}|{fmt(sig.maximum)}] "
                               f"cannot represent boolean 0 and 1")
            kind = "bool"
        else:
            # the port value saturates to the signal range; ranges without overlap are a configuration error
            if sig.maximum < rng[0] or sig.minimum > rng[1]:
                raise RteError(f"{where}: signal range [{fmt(sig.minimum)}|{fmt(sig.maximum)}] and type {native} "
                               f"[{rng[0]}|{rng[1]}] do not overlap")
            kind = "int"
    table = can.rx if direction == "rx" else can.tx
    entry = table.setdefault(msg_name, CanMessage(msg, direction, where=where))
    if direction == "tx" and any(link.signal.name == sig_name for link in entry.links):
        raise RteError(f"{where}: signal {msg_name}.{sig_name} is already sent from another port")
    return entry, CanSignalLink(sig, None, kind)


def can_qlf_link(can: CanConfig, buffers: dict[str, Buffer], src: str, port: Port, dst: str) -> str:
    """Checks a can.qlf.<Message> connection and returns the key of the qualifier buffer."""
    where = f"connection {src} -> {dst}"
    if can is None:
        raise RteError(f"{where}: no DBC configured (section can: in the ECU description)")
    parts = dst.split(".")
    if len(parts) != 3 or not parts[2]:
        raise RteError(f"{where}: expected can.qlf.<Message>")
    name = parts[2]
    if port.direction != "in":
        raise RteError(f"{where}: output port cannot use {QLF_KIND}")
    if port.type.native not in QLF_TYPES:
        raise RteError(f"{where}: type {port.type.native} does not fit {QLF_KIND} "
                       f"({', '.join(sorted(QLF_TYPES))})")
    try:
        msg = can.dbc.message(name)
    except dbcgen.DbcError as e:
        raise RteError(f"{where}: {e}") from None
    if msg.problem:
        raise RteError(f"{where}: {msg.problem}")
    if can.node not in msg.receivers:
        raise RteError(f"{where}: node {can.node} receives no signal of {name}, a qualifier exists only for "
                       f"received messages")
    can.rx.setdefault(name, CanMessage(msg, "rx"))
    return qlf_buffer(buffers, name)


def age_link(components: dict, buffers: dict, src: str, port: Port, dst: str) -> str:
    """Checks an age.qlf.<Swc>.<Port>.<Element> connection and returns the key of the qualifier buffer (SYS-32)."""
    where = f"connection {src} -> {dst}"
    parts = dst.split(".")
    if len(parts) != 5 or parts[1] != "qlf" or not all(parts):
        raise RteError(f"{where}: expected {AGE_KIND}.<Component>.<Port>.<Element> (the provided port whose age "
                       f"is supervised)")
    if port.direction != "in":
        raise RteError(f"{where}: output port cannot use {AGE_KIND}, the age qualifier is read by required ports")
    if port.type.native not in QLF_TYPES:
        raise RteError(f"{where}: type {port.type.native} does not fit {AGE_KIND} ({', '.join(sorted(QLF_TYPES))})")
    writer = ".".join(parts[2:])
    key = f"{AGE_KIND}.{writer}"
    if key in buffers:
        return key
    comp = components.get(parts[2])
    wkey = f"{parts[3]}.{parts[4]}"
    if comp is None or wkey not in comp.ports:
        raise RteError(f"{where}: {writer} is not a port element of the ECU description (expected "
                       f"{AGE_KIND}.<Component>.<Port>.<Element> of a provided port)")
    if comp.ports[wkey].direction != "out":
        raise RteError(f"{where}: {writer} is a required port; the age is supervised where the value is written, "
                       f"name the provided port it is connected to")
    reason = disabled_source(comp, wkey)
    if reason is not None:
        # RTE-23: its writer is disabled - no value is coming, which is not a timeout of a running writer: the
        # qualifier reads QLF_NOT_AVAILABLE from a constant buffer, without supervision
        buffers[key] = Buffer("Rte_Buf_Age_" + writer.replace(".", "_"), "uint8", f"{QLF[QLF_NO_SOURCE]}U",
                              f"{writer}.Age", AGE_KIND, writer, [key], init_only=INIT_DISABLED)
        buffers[key].disabled_by = f"{writer} ({reason})"
        return key
    if not comp.access_points:
        raise RteError(f"{where}: the description of {comp.name} names no access points, so the runnable that "
                       f"writes {writer} is unknown (generate the ARXML with access points)")
    periodic = [r for r in comp.runnables if r.period_ms and wkey in r.accesses]
    implicit = [r.name for r in comp.runnables if r.implicit.get(wkey) == "w"]
    if implicit:
        raise RteError(f"{where}: {comp.name}.{implicit[0]} writes {writer} with implicit access (RTE-09), which "
                       f"publishes after every run - its age is the timing of the runnable, supervised by SM-05. "
                       f"Supervise a port written with Rte_Write, only when there is a new value")
    if not periodic:
        raise RteError(f"{where}: no periodic runnable of {comp.name} writes {writer}; the deadline of the age is "
                       f"the period of its writer (an init or mode switch runnable has none)")
    if len(periodic) > 1:
        names = ", ".join(f"{r.name} {r.period_ms} ms" for r in periodic)
        raise RteError(f"{where}: {writer} is written by several periodic runnables ({names}); the age needs one "
                       f"writer whose period is the deadline")
    r = periodic[0]
    if r.task is None:
        raise RteError(f"{where}: runnable {comp.name}.{r.name} runs in no task")
    buffers[key] = Buffer("Rte_Buf_Age_" + writer.replace(".", "_"), "uint8", f"{QLF['DEBOUNCE']}U",
                          f"{writer}.Age", AGE_KIND, writer, [key])
    buffers[key].age = AgeSupervision(writer, f"{comp.name}.{r.name}", r.period_ms, r.task,
                                      r.period_ms // r.task.period_ms)
    return key


def ecu_link(src: str, port: Port, dst: str, wakeup: dict[str, int]) -> tuple[str, str | None]:
    """Checks an ecu.* connection; returns the kind and, for ecu.wakeup.<source>, the bit mask as C literal."""
    where = f"connection {src} -> {dst}"
    if dst.startswith("ecu.wakeup."):
        kind, source = "ecu.wakeup.", dst[len("ecu.wakeup."):]
        if source not in wakeup:
            raise RteError(f"{where}: unknown wake-up source {source} (known: {', '.join(wakeup)})")
    elif dst in ECU_KINDS:
        kind, source = dst, None
    elif dst == ECU_MODE_TARGET:
        raise RteError(f"{where}: {ECU_MODE_TARGET} connects a mode port (MODE-SWITCH-INTERFACE, Rte_Mode); a "
                       f"sender-receiver port reads the state number from ecu.state")
    else:
        raise RteError(f"{where}: unknown ECU state target (ecu.state, ecu.wakeup, ecu.wakeup.<source>, "
                       f"ecu.sleep_request)")
    spec = ECU_KINDS[kind]
    if spec["direction"] != port.direction:
        raise RteError(f"{where}: {port.direction}put port cannot use {dst}")
    if port.type.native not in spec["types"]:
        raise RteError(f"{where}: type {port.type.native} does not fit {dst} ({', '.join(sorted(spec['types']))})")
    return kind, (f"0x{wakeup[source]:08X}U" if source is not None else None)


def safety_link(src: str, port: Port, dst: str, groups: dict[str, int]) -> tuple[str, str | None]:
    """SYS-117: checks a safety.* connection; returns the kind and, for safety.degraded.<GROUP>, its bit as C literal."""
    where = f"connection {src} -> {dst}"
    if dst.startswith(SAFETY_DEGRADED_GROUP):
        kind, group = SAFETY_DEGRADED_GROUP, dst[len(SAFETY_DEGRADED_GROUP):]
        if group not in groups:
            known = ", ".join(groups) or "none - project.yaml safety.output_groups is empty"
            raise RteError(f"{where}: unknown safe-output group {group} (known: {known})")
    elif dst in (SAFETY_DEGRADED, SAFETY_DEGRADED_QLF) + SAFETY_HW_SHUTOFF_TARGETS:
        kind, group = dst, None
    else:
        raise RteError(f"{where}: unknown safety target ({SAFETY_DEGRADED}, {SAFETY_DEGRADED_GROUP}<GROUP>, "
                       f"{SAFETY_DEGRADED_QLF}, {SAFETY_HW_SHUTOFF}, {SAFETY_HW_SHUTOFF_QLF})")
    if port.direction != "in":
        what = "the hardware shut-off report is" if kind in SAFETY_HW_SHUTOFF_TARGETS else "the degraded groups are"
        raise RteError(f"{where}: {port.direction}put port cannot use {dst} ({what} read only)")
    if port.type.native not in SAFETY_KINDS[kind]:
        raise RteError(f"{where}: type {port.type.native} does not fit {dst} "
                       f"({', '.join(sorted(SAFETY_KINDS[kind]))})")
    if kind == SAFETY_DEGRADED and len(groups) > 8 and port.type.native == "uint8":
        raise RteError(f"{where}: {len(groups)} safe-output groups do not fit uint8")
    return kind, (f"0x{1 << groups[group]:02X}U" if group is not None else None)


def bsw_service_link(src: str, port: Port, dst: str) -> tuple[str, str]:
    """Checks a bsw.<service>.<value> connection (RTE-05); returns (BSW kind, service)."""
    where = f"connection {src} -> {dst}"
    parts = dst.split(".")
    services = ", ".join(BSW_SERVICES)
    if len(parts) != 3 or not all(parts):
        raise RteError(f"{where}: expected {BSW_SERVICE_PREFIX}.<service>.<value> with service one of {services}")
    service, value = parts[1], parts[2]
    if service not in BSW_SERVICES:
        raise RteError(f"{where}: unknown BSW service {service} (known: {services}; targets are named by service, "
                       f"not by the device that provides it)")
    spec = BSW_SERVICES[service]["values"].get(value)
    if spec is None:
        raise RteError(f"{where}: unknown value {value} of the BSW service {service} "
                       f"(known: {', '.join(BSW_SERVICES[service]['values'])})")
    if port.direction != "in":
        raise RteError(f"{where}: {port.direction}put port cannot use {dst} (BSW service ports are inputs)")
    if port.type.native not in spec["types"]:
        raise RteError(f"{where}: type {port.type.native} does not fit {dst} "
                       f"({', '.join(sorted(spec['types']))}, {spec['unit']})")
    return dst, service


def bsw_service_warnings(buffers: dict[str, "Buffer"]) -> list[str]:
    """Power and reset values connected without their qualifier: 0 means 'not known' there as well as a value."""
    values: dict[str, set[str]] = {}
    for b in buffers.values():
        if (b.bsw or "").startswith(f"{BSW_SERVICE_PREFIX}."):
            values.setdefault(b.bsw_signal, set()).add(b.bsw.split(".")[2])
    return [f"connections: {BSW_SERVICE_PREFIX}.{service}.qlf is not connected, so a component cannot tell a value "
            f"from 'not known yet' or 'provider cannot tell': connect it to a required port"
            for service, kinds in sorted(values.items()) if "qlf" in BSW_SERVICES[service]["values"]
            and "qlf" not in kinds]


def device_link(source: DeviceSource, src: str, port: Port, dst: str) -> tuple[str, str]:
    """RTE-18: checks a <prefix>.<Instance>.<value> connection of a module with module.yaml rte; returns (BSW kind,
    instance as in the board profile).

    The instances and values come from the manifest and the project (device_sources). One reading of the driver
    holds all values of an instance, so the RTE reads it once per task cycle and copies what a port asked for.
    Device values are inputs only.
    """
    where = f"connection {src} -> {dst}"
    prefix = source.prefix
    parts = dst.split(".")
    if len(parts) != 3 or not all(parts):
        raise RteError(f"{where}: expected {prefix}.<{source.instance}>.<value> with value one of "
                       f"{', '.join(source.values)}")
    name, value = parts[1], parts[2]
    spec = source.values.get(value)
    if spec is None:
        note = f"; {source.value_note}" if source.value_note else ""
        raise RteError(f"{where}: unknown {prefix} value {value} (known: {', '.join(source.values)}{note})")
    known = source.names
    if name not in known:
        raise RteError(f"{where}: {name} is no {source.noun} of the project ({source.which}: "
                       f"{', '.join(sorted(known)) if known else 'none'}){name_hint(known, name, prefix)}")
    if port.direction != "in":
        raise RteError(f"{where}: {port.direction}put port cannot use {dst} ({source.input_note})")
    if port.type.native not in spec["types"]:
        raise RteError(f"{where}: type {port.type.native} does not fit {dst} "
                       f"({', '.join(sorted(spec['types']))}, {spec['unit']})")
    return f"{prefix}.{value}", name


def measured_targets(signals: dict) -> dict:
    """RTE-18: target prefix -> (value table, check function(signals, src, port, dst)) of every measured value the
    project offers - the device values of the module manifests and the pin events (IO-54). The Studio lists and
    checks its targets with this, so a module with module.yaml rte appears there without a change of the Studio."""
    sources = signals.get(DEVICE_SOURCES) or {}

    def check(source):
        return lambda _signals, src, port, dst: device_link(source, src, port, dst)

    order = [s for s in sources.values() if s.access == "input_capture"] + \
        [s for s in sources.values() if s.access == "hall"]
    result = {s.prefix: (s.values, check(s)) for s in order}
    result[PINEVENT_PREFIX] = (PINEVENT_VALUES, pinevent_link)
    for s in sources.values():
        result.setdefault(s.prefix, (s.values, check(s)))
    return result


def device_warnings(buffers: dict[str, "Buffer"], accesses: tuple[str, ...] | None = None) -> list[str]:
    """RTE-18: instances whose values are connected without their qualifier (a held value would look like a current
    one); modules of the given access codes (all without), each module by instance name."""
    values: dict[str, tuple[DeviceSource, dict[str, set[str]]]] = {}
    for b in buffers.values():
        source = b.device
        if source is None or (accesses is not None and source.access not in accesses):
            continue
        values.setdefault(source.prefix, (source, {}))[1].setdefault(b.bsw_label, set()).add(b.bsw.split(".")[1])
    if accesses is not None:
        ordered = [entry for access in accesses for entry in values.values() if entry[0].access == access]
    else:
        ordered = list(values.values())
    return [f"connections: {source.prefix}.{name}.{source.qualifier} is not connected, so a component cannot tell "
            f"{source.values[source.qualifier]['without']}: connect it to a required port"
            for source, instances in ordered if source.qualifier
            for name, kinds in sorted(instances.items()) if source.qualifier not in kinds]


def capture_warnings(buffers: dict[str, Buffer]) -> list[str]:
    """Channels whose values are connected without their quality (a held value would look like a measured one)."""
    return device_warnings(buffers, ("input_capture",))


def adc_warnings(buffers: dict[str, Buffer], signals: dict[str, set[str]]) -> list[str]:
    """IO-18: physical values connected without their quality, and pins of circuits the board does not fit."""
    values: dict[str, set[str]] = {}
    unfitted: set[str] = set()
    for b in buffers.values():
        if b.bsw in ADC_SENSOR_KINDS:
            values.setdefault(b.bsw_label, set()).add(b.bsw)
        if b.bsw in ("adc.mv", "adc.raw") and b.bsw_label in signals.get("adc.unfitted", set()):
            unfitted.add(b.bsw_label)
    return ([f"connections: adc.qlf.{channel} is not connected, so a component cannot tell a physical value from one "
             f"at the end of the converter or outside the sensor range: connect it to a required port"
             for channel, kinds in sorted(values.items()) if "adc.qlf" not in kinds]
            + [f"connections: the circuit of ADC channel {channel} is not fitted on the board (board.yaml adc "
               f"fitted: false), adc.mv/adc.raw.{channel} read an open pin" for channel in sorted(unfitted)])


def hall_warnings(buffers: dict[str, "Buffer"]) -> list[str]:
    """Sensors whose values are connected without their quality (standstill and an invalid state would hide)."""
    return device_warnings(buffers, ("hall",))


def pinevent_link(signals: dict[str, set[str]], src: str, port: Port, dst: str) -> tuple[str, str]:
    """Checks a pinevent.<Input>.<value> connection (IO-54); returns (BSW kind, board input).

    The inputs are the pin events of the project (project.yaml pin_events, accepted by nxconfig), the values
    PINEVENT_VALUES. One read() of the module holds all values of an input, so the RTE reads it once per cycle.
    """
    where = f"connection {src} -> {dst}"
    parts = dst.split(".")
    if len(parts) != 3 or not all(parts):
        raise RteError(f"{where}: expected pinevent.<Input>.<value> with value one of {', '.join(PINEVENT_VALUES)}")
    name, value = parts[1], parts[2]
    spec = PINEVENT_VALUES.get(value)
    if spec is None:
        raise RteError(f"{where}: unknown pinevent value {value} (known: {', '.join(PINEVENT_VALUES)})")
    known = signals[PINEVENT_PREFIX]
    if name not in known:
        raise RteError(f"{where}: {name} is no pin event of the project (project.yaml pin_events: "
                       f"{', '.join(sorted(known)) if known else 'none'}){name_hint(known, name, PINEVENT_PREFIX)}")
    if port.direction != "in":
        raise RteError(f"{where}: {port.direction}put port cannot use {dst} (a pin event is an input)")
    if port.type.native not in spec["types"]:
        raise RteError(f"{where}: type {port.type.native} does not fit {dst} "
                       f"({', '.join(sorted(spec['types']))}, {spec['unit']})")
    return f"{PINEVENT_PREFIX}.{value}", name


def pinevent_warnings(buffers: dict[str, "Buffer"]) -> list[str]:
    """Pin events whose values are connected without their quality (no edge yet, rate limit tripped would hide)."""
    values: dict[str, set[str]] = {}
    for b in buffers.values():
        if (b.bsw or "").startswith(f"{PINEVENT_PREFIX}."):
            values.setdefault(b.bsw_label, set()).add(b.bsw.split(".")[1])
    return [f"connections: pinevent.{name}.qlf is not connected, so a component cannot tell a counter that stands "
            f"because the rate limit switched the input off from one without edges, nor a valid time stamp from "
            f"none: connect it to a required port"
            for name, kinds in sorted(values.items()) if "qlf" not in kinds]


def device_bus_id(kind: str, index: int) -> int:
    """SYS-29: bus id of the index-th used bus of `kind` (safety::device::busId); DEVICE_NO_BUS if it does not fit."""
    if kind not in DEVICE_BUS_KINDS or not 0 <= index <= DEVICE_BUS_INDEX_MAX:
        return DEVICE_NO_BUS
    return (DEVICE_BUS_KINDS[kind] << 5) | index


def device_bus_ids(config: "nxconfig.Config | None") -> dict[str, int]:
    """SYS-29: bus id of every board device the project integrates, by the C identifier of its name (as the RTE
    names the device, e.g. readGateDriver_<ident>). SPI is the only bus a device module uses today (requires
    spi_device); a device with no used SPI bus or with several gets DEVICE_NO_BUS."""
    integration = getattr(config, "integration", None)
    if integration is None:
        return {}
    spi = list(integration.spi)               # used SPI buses in board order (tools/nxconfig checks.Integration)
    ids = {}
    for device in integration.devices:
        used = list(getattr(device, "spi", None) or [])
        bus_id = DEVICE_NO_BUS
        if len(used) == 1 and used[0] in spi:
            bus_id = device_bus_id("spi", spi.index(used[0]))
        ids[c_name(device.name)] = bus_id
    return ids


def gd_warnings(buffers: dict[str, "Buffer"]) -> list[str]:
    """Gate drivers whose values are connected without their quality (a failed driver reports no faults)."""
    return device_warnings(buffers, ("gate_driver",))


# ------------------------------------------------------------------------ COM-28: diagnostic jobs through the RTE
# The diagnostic jobs of ecu/uds (generated by tools/diaggen from diag.yaml) reach component values only through
# functions of the RTE (rte::diag, RteDiag.h; the list comes from tools/diaggen/rteapi.py, the same module
# diaggen uses). Direction as decided in COM-29: the diagnosis calls the RTE, the RTE never calls the diagnosis.
#   diag.yaml `source: {port: C.P.E}`       the provided port delivers the value of a data identifier (22)
#   rte.yaml  C.P.E: did.<Did>[.<element>]  required port: receives what the tester writes (2E); provided port:
#                                           the same link as source.port, allowed only if diag.yaml names it
#   rte.yaml  C.P.E: routine.<R>.<step>     required integer port: counts the requests of that step (start,
#                                           stop, results); a change tells the runnable to act
#   rte.yaml  C.P.E: routine.<R>.<step>.<p> required port: request parameter p; provided port: response
#                                           parameter p of that step
# Values on the interface have the type of the description; the RTE converts: a wider port is clamped to the
# element type when read, a value written to a port must fit it (diag.yaml min/max or values narrow the range).
DIAG_PREFIXES = ("did", "routine")
DIAG_COUNTER_TYPES = {"uint8", "uint16", "uint32"}
DIAG_TARGET = object()   # generate(diag=...): take the description of the project

# COM-32: values of the basic software as the source of a data identifier (diag.yaml `source: {bsw: <name>}` with a
# prefix of rteapi.RTE_BSW_PREFIXES), named exactly like the BSW targets of the rte.yaml connections:
#   ecu.state                  ecustate::State as number (EcuStateRte.h readState)
#   ecu.wakeup                 start reasons, bit mask (readWakeupSources); ecu.wakeup.<source>: one reason, 0/1
#   adc.mv.<CHANNEL>           mV of a board channel or an internal input (AnalogInputScale::get)
#   adc.raw.<CHANNEL>          converter value (AnalogInput::get)
#   adc.phys.<CHANNEL>         signed physical value of a board sensor (nxAdcPhys_<CHANNEL>, IO-18)
#   adc.qlf.<CHANNEL>          its quality (QLF_* of Rte_Qlf.h)
#   bsw.<service>.<value>      BSW service values (bswService/BswServiceRte.h, RTE-05)
# The value stands on the wire as it is (the coded value is mV, the state number, ...); `factor`, `offset` and
# `unit` of the element describe it for the tester. A wider value is clamped to the element type like a port.
# The functions read these values outside the RTE lock and without a task cycle: every hand-over is a single
# atomic word, the ADC conversions are serialized by the BSP (IO-14), so the diagnosis (UDS task) and the fault
# memory (snapshots, NVM-03) may read them from any task. A failed conversion makes the read return false (NRC 0x22,
# a snapshot stores 0xFF) instead of answering a made up value.
DIAG_BSW_INTEGER = ("uint8", "uint16", "uint32", "int8", "int16", "int32")
DIAG_BSW_MEASURE = DIAG_BSW_INTEGER + ("float32",)   # measured values: any number type, clamped
DIAG_BSW_KINDS = {
    "ecu.state": ("uint8", DIAG_BSW_INTEGER, "state of the ECU state machine (ecustate::State: "
                  + ", ".join(f"{v} {k}" for k, v in ECU_MODES.items()) + ")"),
    "ecu.wakeup": ("uint32", ("uint32",), "reasons of this start, bit mask (0 until the run level is reached)"),
    "ecu.wakeup.": ("boolean", DIAG_BSW_INTEGER, "1 if this start has the reason {signal}"),
    "adc.mv": ("uint32", DIAG_BSW_MEASURE, "voltage of {signal} in mV (board scale)"),
    "adc.raw": ("uint16", DIAG_BSW_MEASURE, "converter value of {signal}"),
    "adc.phys": ("sint32", DIAG_BSW_MEASURE, "physical value of the sensor {signal} (IO-18)"),
    "adc.qlf": ("uint8", DIAG_BSW_INTEGER, "quality of the sensor {signal} (QLF_* of Rte_Qlf.h)"),
}

# COM-45: status values of the basic software for the diagnosis only (diag.yaml `source: {bsw: bsw.<status>.<value>}`,
# not rte.yaml): what the integration of the application knows about its own run. The reader lives with its owner
# (ecu/application systems/RteSystem.cpp, namespace bswstatus::rte); one call per identifier gives every value of that
# status, each a single word - callable from the UDS task and from the fault memory, like the BSW services.
#   rte          deadline violations of task cycles and runnables since the start (SYS-05; "rte reset" keeps them)
#   calibration  identity of the calibration segment (SYS-89): layout and dataset of the CALV header and the result of
#                its checks; a build without a segment answers 0, 0 and 0xFF
# "types": element types of diag.yaml that may carry the value (identities only whole, counters clamped).
BSW_STATUS_KIND = "bswstatus"           # DiagBswValue.kind of these values (not bsw.*: no BSW service hand-over)
BSW_STATUS = {
    "rte": {
        "read": "readRteStatus",
        "locals": [("uint32_t", "rte_deadline_violations")],
        "values": {
            "deadline_violations": {"native": "uint32", "types": DIAG_BSW_INTEGER, "expr": "rte_deadline_violations",
                                    "unit": "deadline violations of task cycles and runnables since the start "
                                            "(saturating)"},
        },
    },
    "calibration": {
        "read": "readCalibrationStatus",
        "locals": [("uint32_t", "cal_layout"), ("uint32_t", "cal_dataset"), ("uint8_t", "cal_status")],
        "values": {
            "layout": {"native": "uint32", "types": ("uint32",), "expr": "cal_layout",
                       "unit": "layout identifier of the CALV header of the calibration segment"},
            "dataset": {"native": "uint32", "types": ("uint32",), "expr": "cal_dataset",
                        "unit": "dataset identifier of the CALV header, 0: the defaults of the build"},
            "status": {"native": "uint8", "types": DIAG_BSW_INTEGER, "expr": "cal_status",
                       "unit": "calibration::Status: 0 VALID, 1 ERASED, 2 MAGIC, 3 FORMAT, 4 HEADER_CRC, 5 LENGTH, "
                               "6 LAYOUT, 7 VALUES_CRC, 8 CHANGED (run-time check); 0xFE not checked yet, 0xFF no "
                               "calibration segment"},
        },
    },
}
# NVM-05: operating time and reset statistics of the application (ecu/application/src/systems/stats, readers in
# RteSystem.cpp). A project without the NvM block EcuStatistics answers the values of this start only (persistent 0);
# a project without NvM at all answers zeros. The counters per cause: starts whose reasons of that source had the
# cause (ecustate::wakeup bits; can/local = any bus/input, unknown = the source could not tell), saturating.
STATS_CAUSES = ("power", "reset", "watchdog", "supply", "timer", "can", "local", "unknown")   # ecustats::Cause order
BSW_STATUS["stats"] = {
    "read": "readEcuStatistics",
    "locals": [("uint32_t", "stats_operating_s"), ("uint32_t", "stats_starts"), ("uint8_t", "stats_persistent"),
               ("uint32_t", "stats_last_mcu"), ("uint32_t", "stats_last_supply")],
    "values": {
        "operating_s": {"native": "uint32", "types": DIAG_BSW_INTEGER, "expr": "stats_operating_s",
                        "unit": "operating time of the ECU in s over its life (NvM block EcuStatistics, NVM-05)"},
        "starts": {"native": "uint32", "types": DIAG_BSW_INTEGER, "expr": "stats_starts",
                   "unit": "counted starts of the ECU over its life (saturating)"},
        "persistent": {"native": "uint8", "types": DIAG_BSW_INTEGER, "expr": "stats_persistent",
                       "unit": "1: the statistics are stored in the NvM block EcuStatistics, 0: this start only"},
        "last_mcu": {"native": "uint32", "types": ("uint32",), "expr": "stats_last_mcu",
                     "unit": "reasons of the latest start as the MCU saw them (ecu.wakeup bits, 0: cannot tell)"},
        "last_supply": {"native": "uint32", "types": ("uint32",), "expr": "stats_last_supply",
                        "unit": "reasons of the latest start as the power supply saw them (ecu.wakeup bits, 0: not "
                                "reported)"},
    },
}
for _source, _read, _what in (("mcu", "readMcuResetCounts", "the MCU's own reset status"),
                              ("supply", "readSupplyResetCounts", "the role power_supply (e.g. a SBC)")):
    BSW_STATUS[f"stats_{_source}"] = {
        "read": _read,
        "locals": [("uint16_t", f"stats_{_source}_{c}") for c in STATS_CAUSES],
        "values": {c: {"native": "uint16", "types": DIAG_BSW_INTEGER, "expr": f"stats_{_source}_{c}",
                       "unit": f"starts with the cause {c} seen by {_what} (saturating at 65535)"}
                   for c in STATS_CAUSES},
    }
# SYS-196: the take-backs of foreign safe_off levels (hold of the commanded level, SYS-151) - the snapshot of the DTC
# safety.safe_off_take_back and a measurement for 22. The reader lives with the hold in the integration of the family
# port (platforms/s32k1xx/port/main Integration.cpp, the same namespace bswstatus::rte); a port without safe_off output
# answers 0xFF, 0, 0 (nxconfig refuses the values in such a project anyway).
BSW_STATUS["safeoff"] = {
    "read": "readSafeOffStatus",
    "locals": [("uint8_t", "safeoff_output"), ("uint32_t", "safeoff_output_take_backs"),
               ("uint32_t", "safeoff_take_backs")],
    "values": {
        "output": {"native": "uint8", "types": DIAG_BSW_INTEGER, "expr": "safeoff_output",
                   "unit": "number of the safe_off output (console group out) of the last take-back recorded for the "
                           "fault memory, 0xFF: none since the start"},
        "output_take_backs": {"native": "uint32", "types": DIAG_BSW_INTEGER, "expr": "safeoff_output_take_backs",
                              "unit": "foreign active levels of that output taken back since the start (saturating "
                                      "when narrowed)"},
        "take_backs": {"native": "uint32", "types": DIAG_BSW_INTEGER, "expr": "safeoff_take_backs",
                       "unit": "foreign active levels of every safe_off output taken back since the start"},
    },
}
BSW_STATUS_KINDS = tuple(f"{BSW_SERVICE_PREFIX}.{status}.{value}"
                         for status, spec in BSW_STATUS.items() for value in spec["values"])


@dataclass(frozen=True)
class DiagBswValue:
    """One value of the basic software a data identifier reads (COM-32)."""
    name: str            # as written in diag.yaml, e.g. adc.mv.12VBAT_CURRENT
    kind: str            # ecu.state, ecu.wakeup, ecu.wakeup., adc.mv|raw|phys|qlf, bsw.<service>.<value>
    native: str          # platform type of the value
    types: tuple         # element types of the description that may carry it
    what: str            # for messages and the Studio
    signal: str | None   # C identifier of an ADC channel, wake-up mask literal or BSW service


def _diag_bsw_service_native(types) -> str:
    return "boolean" if "boolean" in types else min(types, key=lambda t: PLATFORM_SIZES[t])


def resolve_diag_bsw(name: str, signals: dict, wakeup: dict[str, int], where: str) -> DiagBswValue:
    """The value of the basic software `name` of a data identifier (COM-32), checked like a rte.yaml target."""
    parts = name.split(".")
    if parts[0] == "ecu":
        if name in ("ecu.state", "ecu.wakeup"):
            native, types, what = DIAG_BSW_KINDS[name]
            return DiagBswValue(name, name, native, types, what, None)
        if name.startswith("ecu.wakeup.") and len(parts) == 3:
            if parts[2] not in wakeup:
                raise RteError(f"{where}: unknown wake-up source {parts[2]} (known: {', '.join(wakeup)})")
            native, types, what = DIAG_BSW_KINDS["ecu.wakeup."]
            return DiagBswValue(name, "ecu.wakeup.", native, types, what.format(signal=parts[2]),
                                f"0x{wakeup[parts[2]]:08X}U")
        raise RteError(f"{where}: {name} is no ECU state value the diagnosis can read (ecu.state, ecu.wakeup, "
                       f"ecu.wakeup.<source>; ecu.sleep_request and ecu.mode are no values)")
    if parts[0] == "adc":
        kind = ".".join(parts[:2])
        if kind not in ("adc.mv", "adc.raw", "adc.phys", "adc.qlf") or len(parts) != 3:
            raise RteError(f"{where}: expected adc.mv|adc.raw|adc.phys|adc.qlf.<CHANNEL>, not {name}")
        channel = parts[2]
        if kind in ADC_SENSOR_KINDS and channel in signals.get("adc.unfitted", set()):
            raise RteError(f"{where}: the circuit of {channel} is not fitted on the board (board.yaml adc fitted: "
                           f"false), there is no physical value")
        if kind in ADC_SENSOR_KINDS and channel in signals.get("adc.internal", set()):
            raise RteError(f"{where}: {channel} is an internal ADC channel (project.yaml adc_internal) without "
                           f"circuit or sensor plan, there is no physical value; use adc.mv.{channel} or "
                           f"adc.raw.{channel}")
        if channel not in signals.get(kind, set()):
            known = ", ".join(sorted(signals.get(kind, set()))) or "none"
            raise RteError(f"{where}: {channel} is no {kind} channel of the project (known: {known})"
                           f"{name_hint(signals.get(kind, set()), channel, kind)}")
        ident = resolve_board_name(signals[kind], channel, where, kind)
        native, types, what = DIAG_BSW_KINDS[kind]
        return DiagBswValue(name, kind, native, types, what.format(signal=channel), ident)
    if parts[0] == BSW_SERVICE_PREFIX and len(parts) == 3 and parts[1] in BSW_STATUS:
        # COM-45: status of the basic software, for the diagnosis only
        spec = BSW_STATUS[parts[1]]["values"].get(parts[2])
        if spec is None:
            raise RteError(f"{where}: unknown value {parts[2]} of the BSW status {parts[1]} "
                           f"(known: {', '.join(BSW_STATUS[parts[1]]['values'])})")
        return DiagBswValue(name, BSW_STATUS_KIND, spec["native"], tuple(spec["types"]), spec["unit"], parts[1])
    if parts[0] == BSW_SERVICE_PREFIX:
        services = ", ".join(list(BSW_SERVICES) + list(BSW_STATUS))
        if len(parts) != 3 or parts[1] not in BSW_SERVICES:
            raise RteError(f"{where}: expected {BSW_SERVICE_PREFIX}.<service>.<value> with service one of "
                           f"{services}, not {name}")
        spec = BSW_SERVICES[parts[1]]["values"].get(parts[2])
        if spec is None:
            raise RteError(f"{where}: unknown value {parts[2]} of the BSW service {parts[1]} "
                           f"(known: {', '.join(BSW_SERVICES[parts[1]]['values'])})")
        native = _diag_bsw_service_native(spec["types"])
        types = DIAG_BSW_INTEGER if native == "boolean" or parts[2] in ("state", "reason", "qlf") \
            else DIAG_BSW_MEASURE
        return DiagBswValue(name, name, native, types, spec["unit"], parts[1])
    raise RteError(f"{where}: {name} is no value of the basic software the RTE reads "
                   f"({', '.join(p + '.*' for p in rteapi.RTE_BSW_PREFIXES)})")


def diag_bsw_values(config: "nxconfig.Config") -> dict[str, dict]:
    """Every value of the basic software a data identifier of this project can read (COM-32), for the Studio:
    name -> {"types": element types, "what": text}, the same shape as tools/diaggen STACK_VALUES."""
    signals = board_signals(config)
    wakeup = wakeup_sources(config)
    names = ["ecu.state", "ecu.wakeup"] + [f"ecu.wakeup.{s}" for s in wakeup]
    for kind in ("adc.mv", "adc.raw", "adc.phys", "adc.qlf"):
        channels = signals.get(kind, set())
        if kind in ADC_SENSOR_KINDS:
            channels = channels - signals.get("adc.unfitted", set()) - signals.get("adc.internal", set())
        names += [f"{kind}.{c}" for c in sorted(channels)]
    names += list(BSW_SERVICE_KINDS)
    names += list(BSW_STATUS_KINDS)                   # COM-45
    result = {}
    for name in names:
        try:
            value = resolve_diag_bsw(name, signals, wakeup, name)
        except RteError:
            continue                                  # e.g. two board names with the same C identifier
        result[name] = {"types": value.types, "what": value.what}
    return result


@dataclass
class DiagPlan:
    functions: list                                   # rteapi.Function
    providers: dict = field(default_factory=dict)     # target -> (Buffer, Port): values the RTE hands out
    receivers: dict = field(default_factory=dict)     # target -> [(Buffer, Port)]: ports the diagnosis writes
    warnings: list = field(default_factory=list)
    bsw: dict = field(default_factory=dict)           # target -> DiagBswValue: values of the basic software (COM-32)

    @property
    def sensors(self) -> bool:
        """The jobs read a physical value or quality of an ADC sensor (analogSensorConfiguration.h)."""
        return any(v.kind in ADC_SENSOR_KINDS for v in self.bsw.values())

    def declarations(self) -> str:
        """File scope declarations of the BSW hand-overs the rte::diag functions call (COM-32)."""
        kinds = {v.kind for v in self.bsw.values()}
        services = [s for s in BSW_SERVICES if any(v.signal == s for v in self.bsw.values()
                                                    if v.kind.startswith(f"{BSW_SERVICE_PREFIX}."))]
        lines = []
        ecu = []
        if "ecu.state" in kinds:
            ecu.append("void readState(uint8_t& state);")
        if kinds & {"ecu.wakeup", "ecu.wakeup."}:
            ecu.append("void readWakeupSources(uint32_t& sources);")
        if ecu:
            lines += ["", "// COM-32: ECU state for the diagnostic jobs (libs/bsw/ecuState, ecuState/EcuStateRte.h)",
                      "namespace ecustate", "{", "namespace rte", "{"] + ecu + [
                      "} // namespace rte", "} // namespace ecustate"]
        if services:
            lines += ["", "// COM-32: BSW services for the diagnostic jobs (ecu/rte/services, bswService/BswServiceRte.h)",
                      "namespace bswservice", "{", "namespace rte", "{"]
            for name in services:
                spec = BSW_SERVICES[name]
                lines.append(f"void {spec['read']}("
                             + ", ".join(f"{ctype}& {local}" for ctype, local in spec["locals"]) + ");")
            lines += ["} // namespace rte", "} // namespace bswservice"]
        statuses = list(dict.fromkeys(v.signal for v in self.bsw.values() if v.kind == BSW_STATUS_KIND))
        if statuses:
            lines += ["", "// COM-45: status of the basic software for the diagnostic jobs (ecu/application RteSystem.cpp)",
                      "namespace bswstatus", "{", "namespace rte", "{"]
            for name in statuses:
                spec = BSW_STATUS[name]
                lines.append(f"void {spec['read']}("
                             + ", ".join(f"{ctype}& {local}" for ctype, local in spec["locals"]) + ");")
            lines += ["} // namespace rte", "} // namespace bswstatus"]
        return "\n".join(lines)


def _diag_scalar(where: str, port: Port):
    if port.type.composite or port.type.scaled:
        raise RteError(f"{where}: {port.type.describe()} is not a plain scalar; the diagnosis exchanges unscaled "
                       f"scalars only")
    if port.type.native == "float64":
        raise RteError(f"{where}: float64 has no type in the diagnostic description (float32 at most)")


def _diag_check_read(where: str, port: Port, element):
    """A port value becomes the value of `element`: floating point only into float32."""
    _diag_scalar(where, port)
    if port.type.native == "float32" and element.type != "float32":
        raise RteError(f"{where}: port type float32 cannot deliver the {element.type} element {element.name}")


def _diag_check_write(where: str, port: Port, element):
    """The value of `element` is written to a port: every value the diagnosis lets through must fit."""
    _diag_scalar(where, port)
    native = port.type.native
    if native == "float32":
        return
    if element.type == "float32":
        raise RteError(f"{where}: the float32 element {element.name} does not fit port type {native}")
    lo, hi = rteapi.TYPE_RANGE[element.type]
    coded = rteapi.coded_range(element)
    if coded is not None:
        lo, hi = max(lo, coded[0]), min(hi, coded[1])
    plo, phi = TYPE_RANGE[native]
    if lo < plo or hi > phi:
        raise RteError(f"{where}: {element.name} takes {lo}..{hi}, port type {native} holds {plo}..{phi}; narrow "
                       f"the element in diag.yaml (min/max or values)")


def _diag_port(components: dict[str, Component], ref: str, where: str) -> Port:
    comp_name, _, key = ref.partition(".")
    comp = components.get(comp_name)
    if comp is None:
        raise RteError(f"{where}: unknown component {comp_name} (known: {', '.join(components)})")
    if key not in comp.ports:
        raise RteError(f"{where}: {comp_name} has no port element {key} (known: {', '.join(comp.ports)})")
    return comp.ports[key]


def plan_diag(diag, components: dict[str, Component], buffers: dict[str, Buffer], signals: dict | None = None,
              wakeup: dict[str, int] | None = None) -> DiagPlan | None:
    """Resolves the did.* / routine.* targets and the port and BSW sources of the diagnostic description `diag`.

    `signals` (board_signals) and `wakeup` (wakeup_sources): the BSW values of the project (COM-32)."""
    if diag is None:
        for b in buffers.values():
            if b.diag_links:
                raise RteError(f"connection {b.label} -> {b.diag_links[0]}: the project has no diagnostic "
                               f"description (project.diag)")
        return None
    fns = rteapi.functions(diag)
    params = {p.target: (fn, p) for fn in fns for p in fn.params}
    plan = DiagPlan(fns)
    dids = {d.name: d for d in diag.data_identifiers}
    routines = {r.name: r for r in diag.routines}

    for i, did in enumerate(diag.data_identifiers):
        for j, e in enumerate(did.data):
            if rteapi.is_rte_bsw(e.source.bsw):
                where = f"diag.yaml data_identifiers[{i}].data[{j}].source.bsw ({did.name}.{e.name})"
                value = resolve_diag_bsw(e.source.bsw, signals or {}, wakeup or {}, where)
                if e.type not in value.types:
                    raise RteError(f"diag.yaml data_identifiers[{i}].data[{j}].type ({did.name}.{e.name}): "
                                   f"{e.source.bsw} ({value.what}) cannot be {e.type} "
                                   f"(one of {', '.join(value.types)})")
                if did.write:
                    raise RteError(f"diag.yaml data_identifiers[{i}].write ({did.name}): {e.name} comes from "
                                   f"{e.source.bsw}, a value of the basic software the diagnosis only reads")
                if value.kind in ("adc.mv", "adc.raw") and value.name.split(".")[2] in (signals or {}).get(
                        "adc.unfitted", set()):
                    plan.warnings.append(f"{where}: the circuit of {value.name.split('.')[2]} is not fitted "
                                         f"(board.yaml fitted: false), {value.name} reads an open pin")
                clash = next((k for k, x in enumerate(did.data) if x.name.startswith("bsw_")), None)
                if clash is not None:
                    raise RteError(f"diag.yaml data_identifiers[{i}].data[{clash}].name ({did.name}): "
                                   f"{did.data[clash].name} - element names starting with bsw_ are reserved for the "
                                   f"generated reading of values of the basic software")
                plan.bsw[rteapi.did_target(did, e)] = value
                continue
            if e.source.port is None:
                continue
            where = f"diag.yaml data_identifiers[{i}].data[{j}].source.port ({did.name}.{e.name})"
            port = _diag_port(components, e.source.port, where)
            if port.direction != "out":
                raise RteError(f"{where}: {e.source.port} is a required port; the source of a data identifier is a "
                               f"provided port")
            _diag_check_read(where, port, e)
            plan.providers[rteapi.did_target(did, e)] = (buffers[e.source.port], port)
    for i, routine in enumerate(diag.routines):
        for step in rteapi.STEPS:
            value = getattr(routine, step)
            if value is None or value.call.runnable is None:
                continue
            comp_name, _, runnable = value.call.runnable.partition(".")
            comp = components.get(comp_name)
            if comp is None or runnable not in [r.name for r in comp.runnables]:
                known = ", ".join(r.name for r in comp.runnables) if comp else "no such component"
                raise RteError(f"diag.yaml routines[{i}].{step}.call.runnable ({routine.name}): "
                               f"{value.call.runnable} is not a runnable of the ECU description ({known})")

    for b in buffers.values():
        for dst in b.diag_links:
            src = b.label
            where = f"connection {src} -> {dst}"
            port = _diag_port(components, src, where)
            parts = dst.split(".")
            if parts[0] == "did":
                did = dids.get(parts[1]) if len(parts) in (2, 3) else None
                if did is None:
                    raise RteError(f"{where}: expected did.<Did>[.<element>] with an identifier of diag.yaml "
                                   f"({', '.join(dids) or 'none'})")
                if len(parts) == 2:
                    if len(did.data) != 1:
                        raise RteError(f"{where}: {did.name} has {len(did.data)} elements, name one: "
                                       f"did.{did.name}.<element> ({', '.join(e.name for e in did.data)})")
                    element = did.data[0]
                else:
                    element = next((e for e in did.data if e.name == parts[2]), None)
                    if element is None:
                        raise RteError(f"{where}: {did.name} has no element {parts[2]} "
                                       f"({', '.join(e.name for e in did.data)})")
                target = rteapi.did_target(did, element)
                if port.direction == "out":
                    if element.source.port != src:
                        raise RteError(f"{where}: diag.yaml names {element.source.model_dump(exclude_none=True)} as "
                                       f"the source of {did.name}.{element.name}; a provided port delivers an "
                                       f"identifier only as its source.port")
                    continue                          # the same link as source.port
                entry = params.get(target)
                if entry is None or entry[1].direction != "in":
                    raise RteError(f"{where}: {did.name}.{element.name} is not written through the RTE; the "
                                   f"identifier needs write: true and a port source in diag.yaml")
                _diag_check_write(where, port, element)
                plan.receivers.setdefault(target, []).append((b, port))
                continue
            routine = routines.get(parts[1]) if len(parts) in (3, 4) else None
            if routine is None or parts[2] not in rteapi.STEPS or getattr(routine, parts[2]) is None:
                steps = {r.name: [s for s in rteapi.STEPS if getattr(r, s) is not None] for r in diag.routines}
                raise RteError(f"{where}: expected routine.<Routine>.<step>[.<parameter>] with a routine and step of "
                               f"diag.yaml ({'; '.join(f'{n}: {chr(44).join(s)}' for n, s in steps.items()) or 'none'})")
            if len(parts) == 3:
                if port.direction != "in" or port.type.native not in DIAG_COUNTER_TYPES or port.type.scaled:
                    raise RteError(f"{where}: the request counter of a step is a required port of type "
                                   f"{', '.join(sorted(DIAG_COUNTER_TYPES))}")
                plan.receivers.setdefault(dst, []).append((b, port))
                continue
            entry = params.get(dst)
            if entry is None:
                value = getattr(routine, parts[2])
                names = [p.name for p in value.request] + [p.name for p in value.response]
                raise RteError(f"{where}: the {parts[2]} step of {routine.name} has no parameter {parts[3]} "
                               f"({', '.join(names) or 'none'})")
            param = entry[1]
            if param.direction == "in":
                if port.direction != "in":
                    raise RteError(f"{where}: {parts[3]} is a request parameter, a required port receives it")
                _diag_check_write(where, port, param.element)
                plan.receivers.setdefault(dst, []).append((b, port))
            else:
                if port.direction != "out":
                    raise RteError(f"{where}: {parts[3]} is a response parameter, a provided port delivers it")
                if dst in plan.providers:
                    raise RteError(f"{where}: {dst} is already delivered by {plan.providers[dst][0].label}")
                _diag_check_read(where, port, param.element)
                plan.providers[dst] = (b, port)

    for fn in fns:
        for p in fn.params:
            if p.direction == "out" and p.target not in plan.providers and p.target not in plan.bsw:
                plan.warnings.append(f"diag.yaml: {p.target} has no providing port in the ECU description, the "
                                     f"diagnosis answers 0 (connect a provided port to {p.target})")
            elif p.direction == "in" and p.target not in plan.receivers:
                plan.warnings.append(f"diag.yaml: nothing receives {p.target} (connect a required port to "
                                     f"{p.target})")
    return plan


def _diag_read_expr(buffer: Buffer, port: Port, diag_type: str) -> str:
    """C++ expression: the port value as the element type (clamped to it if the port is wider)."""
    return _diag_convert(buffer.name, port.type.native, diag_type)


def _diag_convert(src: str, native: str, diag_type: str) -> str:
    """C++ expression: `src` of platform type `native` as the element type (clamped to it if `native` is wider)."""
    ctype = rteapi.C_TYPES[diag_type]
    if diag_type == "float32" or native in ("float32",):
        return f"static_cast<{ctype}>({src})"
    if native == "boolean":
        return f"static_cast<{ctype}>(({src} != 0U) ? 1U : 0U)"
    plo, phi = TYPE_RANGE[native]
    tlo, thi = rteapi.TYPE_RANGE[diag_type]
    # comparison literals in the signedness of the port (no sign-compare warning); only the ends that can occur
    suffix = "U" if plo >= 0 else ""
    expr = f"static_cast<{ctype}>({src})"
    if plo < tlo:          # only a signed port can lie below an element's lower end
        expr = f"(({src} < {tlo}) ? static_cast<{ctype}>({tlo}) : {expr})"
    if phi > thi:
        expr = f"(({src} > {thi}{suffix}) ? static_cast<{ctype}>({thi}) : {expr})"
    return expr


def _diag_write_stmt(buffer: Buffer, port: Port, value: str) -> str:
    if port.type.native == "boolean":
        return f"{buffer.name} = static_cast<{buffer.ctype}>(({value} != 0) ? 1U : 0U);"
    return f"{buffer.name} = static_cast<{buffer.ctype}>({value});"


def render_diag_code(plan: DiagPlan | None) -> str:
    """Definitions of the rte::diag functions (RteConfig.cpp): ports under the RTE lock, values of the basic
    software (COM-32) before it, without the lock."""
    if plan is None or not plan.functions:
        return ""
    lines = ["", "// ---- COM-28: functions of the RTE for the diagnostic jobs (RteDiag.h, tools/diaggen/rteapi.py) ----",
             "namespace diag", "{"]
    for fn in plan.functions:
        lines += ["", f"{fn.signature()}", "{"]
        bsw = [p for p in fn.params if p.target in plan.bsw]
        fallible = False
        if bsw:
            bsw_lines, fallible = _diag_bsw_reads(plan, bsw)
            lines += bsw_lines
        if any(p.target not in plan.bsw for p in fn.params) or fn.counter or not bsw:
            lines.append("    ::async::LockType const lock;")
        for p in fn.params:
            if p.target in plan.bsw:
                continue
            if p.direction == "out":
                provider = plan.providers.get(p.target)
                if provider is None:
                    lines.append(f"    {p.name} = static_cast<{p.ctype}>(0); // {p.target}: not connected")
                else:
                    buffer, port = provider
                    lines.append(f"    {p.name} = {_diag_read_expr(buffer, port, p.diag_type)}; // {buffer.label}")
            else:
                receivers = plan.receivers.get(p.target, [])
                if not receivers:
                    lines.append(f"    (void){p.name}; // {p.target}: nothing receives it")
                for buffer, port in receivers:
                    lines.append(f"    {_diag_write_stmt(buffer, port, p.name)} // {buffer.label}")
        if fn.counter:
            for buffer, _ in plan.receivers.get(fn.counter, []):
                lines.append(f"    {buffer.name} = static_cast<{buffer.ctype}>({buffer.name} + 1U); // {buffer.label}: "
                             f"{fn.step} requested")
        if fn.returns == "bool":
            lines.append("    return bsw_available;" if fallible else "    return true;")
        lines.append("}")
    lines += ["} // namespace diag", ""]
    return "\n".join(lines)


def _diag_bsw_reads(plan: DiagPlan, params: list) -> tuple[list[str], bool]:
    """Statements of one read function for its values of the basic software (COM-32), outside the RTE lock.

    One reading per source and function, so values of the same service or sensor belong together (like the task
    cycle, RTE-05, IO-18). Every local starts with bsw_ (an element named like one is refused in plan_diag).
    Returns the statements and whether a reading can fail (a failed ADC conversion: the function returns false
    then, and the job answers NRC 0x22)."""
    values = [(p, plan.bsw[p.target]) for p in params]
    kinds = {v.kind for _, v in values}
    lines = ["    // values of the basic software (COM-32), read outside the RTE lock"]
    fallible = any(v.kind in ("adc.mv", "adc.raw", "adc.phys") for _, v in values)
    if fallible:
        lines.append("    bool bsw_available = true;")
    if "ecu.state" in kinds:
        lines += ["    uint8_t bsw_ecu_state = 0U;", "    ::ecustate::rte::readState(bsw_ecu_state);"]
    if kinds & {"ecu.wakeup", "ecu.wakeup."}:
        lines += ["    uint32_t bsw_ecu_wakeup = 0U;", "    ::ecustate::rte::readWakeupSources(bsw_ecu_wakeup);"]
    for service in dict.fromkeys(v.signal for _, v in values if v.kind.startswith(f"{BSW_SERVICE_PREFIX}.")):
        spec = BSW_SERVICES[service]
        lines += [f"    {ctype} bsw_{local} = 0U;" for ctype, local in spec["locals"]]
        lines.append(f"    ::bswservice::rte::{spec['read']}("
                     f"{', '.join('bsw_' + local for _, local in spec['locals'])});")
    for status in dict.fromkeys(v.signal for _, v in values if v.kind == BSW_STATUS_KIND):   # COM-45
        spec = BSW_STATUS[status]
        lines += [f"    {ctype} bsw_{local} = 0U;" for ctype, local in spec["locals"]]
        lines.append(f"    ::bswstatus::rte::{spec['read']}("
                     f"{', '.join('bsw_' + local for _, local in spec['locals'])});")
    for kind, call, ctype in (("adc.mv", "AnalogInputScale", "uint32_t"), ("adc.raw", "AnalogInput", "uint16_t")):
        for ident in dict.fromkeys(v.signal for _, v in values if v.kind == kind):
            local = f"bsw_{kind.replace('.', '_')}_{ident}"
            lines += [f"    {ctype} {local} = 0U;",
                      f"    if (::bios::{call}::get(::bios::AnalogInput::Ai_{ident}, {local}) != ::bsp::BSP_OK)",
                      "    {", "        bsw_available = false;", "    }"]
    for ident in dict.fromkeys(v.signal for _, v in values if v.kind in ADC_SENSOR_KINDS):
        with_value = any(v.kind == "adc.phys" and v.signal == ident for _, v in values)
        s = f"bsw_sensor_{ident}"
        lines += [f"    uint16_t {s}_raw    = 0U;", f"    uint8_t {s}_quality = QLF_ERROR;"]
        if with_value:
            lines.append(f"    int32_t {s}_value   = 0;")
        lines += [f"    bool const {s}_valid",
                  f"        = (::bios::AnalogInput::get(::bios::AnalogInput::Ai_{ident}, {s}_raw) == ::bsp::BSP_OK);",
                  f"    if ({s}_valid)", "    {",
                  f"        {f'{s}_value = ' if with_value else '(void)'}nxAdcPhys_{ident}({s}_raw, &{s}_quality);",
                  "    }"]
        if with_value:
            # the quality alone is an answer (QLF_ERROR), a physical value without conversion is not
            lines += ["    else", "    {", "        bsw_available = false;", "    }"]
    for p, v in values:
        if v.kind == "ecu.state":
            src, native = "bsw_ecu_state", "uint8"
        elif v.kind == "ecu.wakeup":
            src, native = "bsw_ecu_wakeup", "uint32"
        elif v.kind == "ecu.wakeup.":
            src, native = f"(bsw_ecu_wakeup & {v.signal})", "boolean"
        elif v.kind in ("adc.mv", "adc.raw"):
            src, native = f"bsw_{v.kind.replace('.', '_')}_{v.signal}", v.native
        elif v.kind == "adc.phys":
            src, native = f"bsw_sensor_{v.signal}_value", v.native
        elif v.kind == "adc.qlf":
            src, native = f"bsw_sensor_{v.signal}_quality", v.native
        elif v.kind == BSW_STATUS_KIND:                  # COM-45
            src, native = f"bsw_{BSW_STATUS[v.signal]['values'][v.name.split('.')[2]]['expr']}", v.native
        else:
            expr = bsw_service_expr(v.kind, "boolean")      # the bare expression, _diag_convert casts it
            for _, local in BSW_SERVICES[v.signal]["locals"]:
                expr = re.sub(rf"\b{local}\b", f"bsw_{local}", expr)
            src, native = f"({expr})", v.native
        lines.append(f"    {p.name} = {_diag_convert(src, native, p.diag_type)}; // {v.name}")
    return lines, fallible


# ------------------------------------------------------------------------ NVM-03: fault memory (libs/bsw/dem)
# The fault memory is configuration: the DTC table, events, debouncing, enable conditions, operation cycles, aging,
# snapshots and extended data come from the diagnostic description (diag.yaml, ARCH-51), the RTE generator resolves
# where the samples come from and derives the NvM blocks of the memory - nothing of it is written in rte.yaml by
# hand. Outputs: RteDem.h (tables of dem::Config, only the integration includes it), rte::demSample/demCondition/
# demCycleState in RteConfig.cpp (dem::ISource of the integration, ecu/application/src/systems/dem/DemRte.h) and the
# blocks DemEvents/DemEntries in rte::NVM_BLOCKS.
#   diag.yaml dtcs[].event.port C.P.E      provided port: its value, or required port: the value it reads
#   diag.yaml dtcs[].event.bsw can.qlf.<M> the signal qualifier of a received message
#   diag.yaml dtcs[].event.bsw age.qlf.C.P.E  the age qualifier of a supervised provided port (SYS-32)
#   diag.yaml dtcs[].event.bsw <other>     reported by the BSW through dem::IFaultMemory::report() (safe supervisor,
#                                          bus-off, supply): only reporting, the reaction stays with the monitor
#   rte.yaml  C.P.E: dem.event.<Dtc>       the same link as event.port (allowed only if diag.yaml names that port)
#   rte.yaml  C.P.E: dem.cycle.<Cycle>     boolean provided port: runs an operation cycle with autostart: false
#   enable condition port C.P.E / bsw ecu.state[.<MODE>]   (ecu.state alone: the ECU state machine is in RUN)
# Every event is sampled once per fault memory cycle (fault_memory.cycle_ms) under the lock of the RTE.
DEM_PREFIX = "dem"
DEM_BLOCKS = ("DemEvents", "DemEntries")          # NvM blocks of the fault memory (reserved block names)
DEM_MAX_CONDITIONS = 32                           # bits of dem::Config::initialConditions
DEM_MAX_CYCLES = 8                                # bits of dem::Config::autostartCycles
DEM_RESULT = {"PASSED": 0, "FAILED": 1, "PREPASSED": 2, "PREFAILED": 3, "NONE": 0xFF}   # dem::Result
DEM_EVENT_RECORD, DEM_ENTRY_HEADER, DEM_NVM_HEADER = 5, 6, 4   # dem/DemTypes.h NVM_EVENT_RECORD, ...
DEM_COUNTERS = {"occurrence_counter": "OCCURRENCE", "aging_counter": "AGING",
                "fault_detection_counter": "FAULT_DETECTION", "failed_cycles": "FAILED_CYCLES",
                "operation_cycles_since_first_failed": "CYCLES_SINCE_FIRST_FAILED"}   # dem::Counter
DEM_LIMIT_TYPES = {"uint8", "uint16", "uint32", "sint8", "sint16", "sint32", "float32"}
# BSW event names a module of neXtBSW reports through dem::IFaultMemory::report() (event.bsw of diag.yaml). Other names
# are allowed for modules of a project (reported by name, found with findReportedEvent) and give a warning. A key may
# be a fnmatch pattern ("nvm.default_loaded.*") for a source with instances (tools/nxconfig/bswdtc.py PLAN) that
# reports one event per project value (here: per NvM block) instead of one fixed name - a plain name matches only
# itself, so the literal entries above behave as before.
DEM_BSW_REPORTERS = {"rte.deadline": "RteSystem, deadline violations of SYS-05",
                     "calibration.segment": "RteSystem, start-up check of the calibration segment of SYS-89",
                     "calibration.plausibility": "ecu/calibration, plausibility of the safety-relevant calibration "
                                                 "parameters of SYS-90 (start-up and XCP writes)",
                     # NVM-16/NVM-19: nvm::reportToFaultMemory() (libs/bsw/nvm/include/nvm/Nvm.h "NVM-16 makes them
                     # DTCs") reports these once per start and block, from the events of ecu/application/src/systems/
                     # nvm/NvmEvents.h (nvm::EventLog, DEM_EVENTS) - one DTC per event and NvM block (TOOL-178).
                     "nvm.default_loaded.*": "nvm::reportToFaultMemory(), an NvM block lost its value (CRC/read "
                                             "error) and started from its default",
                     "nvm.copy_repair_failed.*": "nvm::reportToFaultMemory(), the redundant copy of an ASIL NvM "
                                                 "block was defective and the other one could not repair it",
                     "nvm.copy_repaired.*": "nvm::reportToFaultMemory(), the redundant copy of an ASIL NvM block "
                                            "was defective and repaired from the other one",
                     # NVM-30: QM block, one of its two places defective (possibly the previous value loaded)
                     "nvm.place_defective.*": "nvm::reportToFaultMemory(), one place of a QM NvM block was defective "
                                              "and the value of the other one (possibly the previous) was loaded",
                     # COM-46/NVM-08: the CAN system of the board port per used bus (can/CanBusOffReport.h), only
                     # for the buses named here (dem/demBswReporters.h NX_DEM_CAN_BUSOFF)
                     "can.busoff.*": "CanSystem of the board port, bus-off of the CAN bus <BUS> (COM-46)",
                     # NVM-08: RteSystem, logging only - the reaction stays with the safe supervisor
                     "safety.device_fault": "RteSystem, safe-output groups in the degraded reaction of the safe "
                                            "supervisor after a device fault (SYS-117, logging only)",
                     # SYS-136: RteSystem, logging only - the reaction (S1 held until reset) stays with the supervisor
                     "safety.hw_shutoff": "RteSystem, hardware shut-off path of a PWM group reported to the safe "
                                          "supervisor (SYS-130, logging only)",
                     # SYS-196: the BSP task of the family port (integration/SafeOffTakeBackReport.h), recording only -
                     # the hold of SYS-151 already took the level back; nxconfig allows it only with a safe_off output
                     "safety.safe_off_take_back": "BSP task of the family port, a foreign active level of a safe_off "
                                                  "output taken back by the hold of the commanded level (SYS-151, "
                                                  "SYS-196, logging only)"}
# SYS-113: the cause of the safety reset before this start, reported by the BSP task of the family port (libs/bsw/dem
# dem/SafetyResetReport.h EVENTS - the names of the causes that reset; the held safe states are no source)
DEM_BSW_REPORTERS.update({name: "BSP task of the family port, cause of the safety reset before this start (SYS-113, "
                                "logging only)"
                          for name in ("safety.reset_loop", "safety.reset_other", "safety.mcu_watchdog",
                                       "safety.fault_exception", "safety.safety_manager_sequence_deviation",
                                       "safety.safe_watchdog_sequence_deviation",
                                       "safety.safe_watchdog_configuration_error",
                                       "safety.safe_watchdog_service_deviation",
                                       "safety.mpu_unlocked_on_safety_manager_entry",
                                       "safety.mpu_locked_on_safety_manager_exit",
                                       "safety.integrity_event_dma_ecc_error", "safety.mpu_not_enabled",
                                       "safety.mpu_configuration_error", "safety.adc_reference_error",
                                       "safety.internal_supplies_error", "safety.lock_register_error",
                                       "safety.deadman_configuration_error", "safety.safety_cycle_period_violation",
                                       "safety.stack_overflow", "safety.rtos_assert", "safety.etl_assert")})
# IO-59/NVM-08: BSW events the RTE samples itself (not reported): the supply from the BSW service bsw.power (role
# power_supply) - without a limit the provider's state (its thresholds are project values of its module), with
# failed_below/failed_above (mV) the measured voltage; the limit direction per event. Condition source bsw.power.ok.
DEM_SUPPLY_EVENTS = {"bsw.power.undervoltage": ("failed_below", BSW_POWER_STATES["UNDERVOLTAGE"]),
                     "bsw.power.overvoltage": ("failed_above", BSW_POWER_STATES["OVERVOLTAGE"])}
DEM_SUPPLY_CONDITION = "bsw.power.ok"
DEM_CAN_BUSOFF_PREFIX = "can.busoff."
DEM_SAFETY_DEVICE_FAULT = "safety.device_fault"
DEM_RTE_DEADLINE = "rte.deadline"   # COM-47: RteSystem reports it as a level (dem/DeadlineReport.h)
DEM_SAFETY_HW_SHUTOFF = "safety.hw_shutoff"   # SYS-136
DEM_REPORTERS_FILE = "dem/demBswReporters.h"


def dem_bsw_reporter(name: str) -> str | None:
    """DEM_BSW_REPORTERS entry describing what reports the BSW event `name` (exact name or fnmatch pattern),
    None if diag.yaml dtcs[].event.bsw ({name}) names no BSW module of neXtBSW yet."""
    return next((what for pattern, what in DEM_BSW_REPORTERS.items() if fnmatchcase(name, pattern)), None)


def dem_events_bytes(dtcs: int) -> int:
    """dem::eventsBlockBytes()"""
    return DEM_NVM_HEADER + DEM_EVENT_RECORD * dtcs


def dem_entries_bytes(entries: int, data: int) -> int:
    """dem::entriesBlockBytes()"""
    return DEM_NVM_HEADER + entries * (DEM_ENTRY_HEADER + data)


@dataclass
class DemDtc:
    index: int
    name: str
    number: int
    severity: int
    priority: int
    confirmation: int
    aging: int                   # 0: no aging
    cycle: int
    aging_cycle: int
    conditions: int              # bit mask
    counter: tuple | None        # (failed, passed, increment, decrement, jump_up, jump_down)
    time: tuple | None           # (failed_ms, passed_ms)
    snapshots: list              # [(record number, [(did, bytes, name)])]
    extended: list               # [(record number, [(counter, did, bytes, name)])]
    source: str                  # description of the source for comments and the console
    sample: str | None           # C++ statements returning the dem::Result number; None: reported by the BSW
    reported: str | None = None  # BSW event name

    @property
    def stored_bytes(self) -> int:
        return (sum(b for _, items in self.snapshots for _, b, _ in items)
                + sum(b for _, items in self.extended for c, _, b, _ in items if c == "DID"))


@dataclass
class DemPlan:
    dtcs: list
    conditions: list             # [(name, initial, C++ statements returning bool, source)]
    cycles: list                 # [(name, autostart, C++ statements returning the state or None, source)]
    max_entries: int
    replace_oldest: bool
    cycle_ms: int
    write_interval_ms: int
    warnings: list = field(default_factory=list)
    # COM-46/NVM-08: what the platform and the integration report (dem/demBswReporters.h): (C identifier of the bus,
    # BSW event name) per bus-off DTC, and whether RteSystem reports the degraded reaction of the safe supervisor
    busoff: list = field(default_factory=list)
    safety_device_fault: bool = False
    rte_deadline: bool = False   # COM-47: a DTC of rte.deadline, RteSystem reports the level (NX_DEM_RTE_DEADLINE)
    safety_hw_shutoff: bool = False   # SYS-136: RteSystem reports the hardware shut-off path
    uses_power: bool = False     # IO-59: a sample or a condition reads the BSW service bsw.power

    @property
    def entry_data_bytes(self) -> int:
        return max([d.stored_bytes for d in self.dtcs] + [1])

    @property
    def events_bytes(self) -> int:
        return dem_events_bytes(len(self.dtcs))

    @property
    def entries_bytes(self) -> int:
        return dem_entries_bytes(self.max_entries, self.entry_data_bytes)

    @property
    def autostart_mask(self) -> int:
        return sum(1 << i for i, (_, autostart, _, _) in enumerate(self.cycles) if autostart)

    @property
    def initial_conditions(self) -> int:
        return sum(1 << i for i, (_, initial, _, _) in enumerate(self.conditions) if initial)

    def signature(self) -> str:
        """Layout of the persistent state: a changed table or entry size gives the blocks another record id."""
        return repr((len(self.dtcs), self.max_entries, self.entry_data_bytes,
                     [(d.number, d.stored_bytes) for d in self.dtcs]))


def _dem_value(buffer: Buffer) -> str:
    return buffer.name


def _dem_literal(native: str, value: int) -> str:
    if native == "float32":
        return f"{float(value)!r}F"
    return f"{value}U" if not native.startswith("sint") else f"{value}"


def _dem_result_code(where: str, kind: str, event, native: str, value: str) -> str:
    """C++ statement returning the dem::Result number of one sample of `value` (a scalar of type `native`)."""
    failed, passed, none = DEM_RESULT["PREFAILED"], DEM_RESULT["PREPASSED"], DEM_RESULT["NONE"]
    if kind == "boolean":
        if native not in ("boolean", "uint8", "uint16", "uint32"):
            raise RteError(f"{where}: result boolean needs a boolean or unsigned port, not {native}")
        return f"return ({value} != 0U) ? {failed}U : {passed}U;"
    if kind == "qualifier":
        if native not in QLF_TYPES:
            raise RteError(f"{where}: result qualifier needs a qualifier ({', '.join(sorted(QLF_TYPES))}), not {native}")
        # RTE-28: QLF_NOT_AVAILABLE (no source, NvM block never written) is no test result, like QLF_DEBOUNCE
        return (f"return ({value} == {QLF['OK']}U) ? {passed}U : ((({value} == {QLF['DEBOUNCE']}U) || "
                f"({value} == {QLF['NOT_AVAILABLE']}U)) ? {none}U : {failed}U);")
    if kind == "status":
        if native not in ("uint8", "uint16", "uint32"):
            raise RteError(f"{where}: result status needs an unsigned port (AUTOSAR event status 0..3), not {native}")
        return f"return ({value} <= 3U) ? static_cast<uint8_t>({value}) : {none}U;"
    # limit
    if native not in DEM_LIMIT_TYPES:
        raise RteError(f"{where}: result limit needs a numeric port, not {native}")
    rng = TYPE_RANGE[native]
    conds = []
    for bound, op in ((event.failed_above, ">"), (event.failed_below, "<")):
        if bound is None:
            continue
        if rng is not None and not (rng[0] <= bound <= rng[1]):
            raise RteError(f"{where}: {'failed_above' if op == '>' else 'failed_below'} {bound} is outside the "
                           f"range {rng[0]}..{rng[1]} of the port type {native}")
        if rng is not None and ((op == ">" and bound >= rng[1]) or (op == "<" and bound <= rng[0])):
            raise RteError(f"{where}: {'failed_above' if op == '>' else 'failed_below'} {bound} can never be "
                           f"crossed by a {native}")
        conds.append(f"({value} {op} {_dem_literal(native, bound)})")
    return f"return ({' || '.join(conds)}) ? {failed}U : {passed}U;"


def _dem_buffer_of(components: dict[str, Component], buffers: dict[str, Buffer], port_buffer: dict[str, str],
                   ref: str, where: str) -> tuple[Buffer, Port]:
    comp_name, _, key = ref.partition(".")
    comp = components.get(comp_name)
    if comp is None or key not in comp.ports:
        known = ", ".join(comp.ports) if comp else f"no component {comp_name}"
        raise RteError(f"{where}: {ref} is not a port element of the ECU description ({known})")
    port = comp.ports[key]
    if port.type.composite or port.type.scaled or port.type.native == "float64":
        raise RteError(f"{where}: {ref} is {port.type.describe()}; the fault memory samples unscaled scalars")
    buffer = buffers.get(ref) if port.direction == "out" else buffers.get(port_buffer.get(ref, ""))
    if buffer is None:
        raise RteError(f"{where}: {ref} has no value in the RTE (not connected)")
    return buffer, port


def _dem_power_read() -> str:
    """IO-59: C++ statements reading the BSW service bsw.power into power_state/power_mv/power_health."""
    return ("uint8_t power_state = 0U;\n            uint16_t power_mv = 0U;\n            uint8_t power_health = 0U;\n"
            "            ::bswservice::rte::readPower(power_state, power_mv, power_health);")


def _dem_supply_sample(where: str, event, name: str) -> str:
    """IO-59: one sample of a supply event - the provider's state, or the measured voltage against the limit."""
    direction, state = DEM_SUPPLY_EVENTS[name]
    wrong = "failed_above" if direction == "failed_below" else "failed_below"
    if getattr(event, wrong) is not None:
        raise RteError(f"{where}: {name} fails {'below' if direction == 'failed_below' else 'above'} its threshold; "
                       f"give {direction} (mV), not {wrong}")
    if event.result not in (None, "limit"):
        raise RteError(f"{where}: {name} is sampled from the supply; result {event.result} does not apply")
    failed, passed, none = DEM_RESULT["PREFAILED"], DEM_RESULT["PREPASSED"], DEM_RESULT["NONE"]
    limit = getattr(event, direction)
    head = (f"{_dem_power_read()}\n            if (power_health != 1U)\n            {{\n"
            f"                return {none}U;\n            }}")
    if limit is not None:
        if not (0 < limit <= 0xFFFF):
            raise RteError(f"{where}: {direction} {limit} mV is outside 1..65535 (bsw.power.supply_mv is uint16)")
        op = "<" if direction == "failed_below" else ">"
        return (f"{head}\n            if (power_mv == 0U)\n            {{\n                return {none}U; "
                f"// the provider does not measure the voltage\n            }}\n"
                f"            return (power_mv {op} {limit}U) ? {failed}U : {passed}U;")
    ok, under, over = (BSW_POWER_STATES[k] for k in ("OK", "UNDERVOLTAGE", "OVERVOLTAGE"))
    return (f"{head}\n            if (power_state == {state}U)\n            {{\n                return {failed}U;\n"
            f"            }}\n            return ((power_state == {ok}U) || (power_state == {under}U) || "
            f"(power_state == {over}U)) ? {passed}U : {none}U;")


def plan_dem(diag, components: dict[str, Component], buffers: dict[str, Buffer],
             port_buffer: dict[str, str], buses: list[str] | None = None) -> DemPlan | None:
    """Resolves the sources of the fault memory of the diagnostic description `diag` (NVM-03).

    `buses` (COM-46): board names of the CAN buses the project uses, in the order of their bus ids - a bus-off DTC
    (can.busoff.<BUS>) must name one of them; None: not known (no project), not checked."""
    links = [(b.label, dst) for b in buffers.values() for dst in b.dem_links]
    if diag is None or not diag.dtcs:
        if links:
            raise RteError(f"connection {links[0][0]} -> {links[0][1]}: the project describes no DTCs "
                           f"(diag.yaml dtcs)")
        return None
    memory = diag.fault_memory
    if memory is None:
        raise RteError("diag.yaml: DTCs are described but there is no fault_memory section")
    if len(diag.operation_cycles) > DEM_MAX_CYCLES:
        raise RteError(f"diag.yaml operation_cycles: {len(diag.operation_cycles)} cycles, the fault memory runs at "
                       f"most {DEM_MAX_CYCLES}")
    if len(diag.enable_conditions) > DEM_MAX_CONDITIONS:
        raise RteError(f"diag.yaml enable_conditions: {len(diag.enable_conditions)} conditions, the fault memory "
                       f"knows at most {DEM_MAX_CONDITIONS}")
    cycles_idx = {c.name: i for i, c in enumerate(diag.operation_cycles)}
    cond_idx = {c.name: i for i, c in enumerate(diag.enable_conditions)}
    did_by_name = {d.name: d for d in diag.data_identifiers}
    dtc_by_name = {d.name: d for d in diag.dtcs}
    plan = DemPlan([], [], [], memory.max_entries, memory.overflow == "replace_oldest", memory.cycle_ms,
                   memory.write_interval_ms)

    # rte.yaml dem.* links
    cycle_links: dict[str, tuple[Buffer, Port, str]] = {}
    for label, dst in links:
        where = f"connection {label} -> {dst}"
        parts = dst.split(".")
        if len(parts) != 3 or parts[1] not in ("event", "cycle"):
            raise RteError(f"{where}: expected {DEM_PREFIX}.event.<Dtc> or {DEM_PREFIX}.cycle.<Cycle>")
        if parts[1] == "event":
            dtc = dtc_by_name.get(parts[2])
            if dtc is None:
                raise RteError(f"{where}: no DTC {parts[2]} in diag.yaml ({', '.join(dtc_by_name)})")
            if dtc.event.port != label:
                raise RteError(f"{where}: diag.yaml names {dtc.event.model_dump(exclude_none=True, include={'port', 'bsw'})} "
                               f"as the event of {dtc.name}; {DEM_PREFIX}.event.{dtc.name} is only the same link as "
                               f"event.port")
            continue
        cycle = next((c for c in diag.operation_cycles if c.name == parts[2]), None)
        if cycle is None:
            raise RteError(f"{where}: no operation cycle {parts[2]} in diag.yaml "
                           f"({', '.join(cycles_idx) or 'none'})")
        if cycle.autostart:
            raise RteError(f"{where}: operation cycle {cycle.name} has autostart: true, it runs with the ECU; a port "
                           f"drives only a cycle with autostart: false")
        if cycle.name in cycle_links:
            raise RteError(f"{where}: {dst} is already driven by {cycle_links[cycle.name][2]}")
        buffer, port = _dem_buffer_of(components, buffers, port_buffer, label, where)
        if port.type.native != "boolean":
            raise RteError(f"{where}: an operation cycle is driven by a boolean port (true: running), not "
                           f"{port.type.native}")
        cycle_links[cycle.name] = (buffer, port, label)

    for i, cycle in enumerate(diag.operation_cycles):
        if cycle.autostart:
            plan.cycles.append((cycle.name, True, None, "starts with the ECU, ends at its run-down"))
            continue
        link = cycle_links.get(cycle.name)
        if link is None:
            raise RteError(f"diag.yaml operation_cycles[{i}] ({cycle.name}): autostart: false needs a boolean "
                           f"provided port connected to {DEM_PREFIX}.cycle.{cycle.name} in the ECU description")
        buffer, _, label = link
        plan.cycles.append((cycle.name, False, f"return ({_dem_value(buffer)} != 0U) ? 1U : 0U;", label))

    for i, cond in enumerate(diag.enable_conditions):
        where = f"diag.yaml enable_conditions[{i}] ({cond.name})"
        if cond.port is not None:
            buffer, port = _dem_buffer_of(components, buffers, port_buffer, cond.port, where)
            if port.type.native not in ("boolean", "uint8", "uint16", "uint32"):
                raise RteError(f"{where}: a condition is a boolean (or unsigned, 0: not fulfilled), not "
                               f"{port.type.native}")
            plan.conditions.append((cond.name, cond.initial, f"return {_dem_value(buffer)} != 0U;", cond.port))
            continue
        name = cond.bsw
        if name == "ecu.state" or name.startswith("ecu.state."):
            mode = name[len("ecu.state."):] if name != "ecu.state" else "RUN"
            if mode not in ECU_MODES:
                raise RteError(f"{where}: unknown ECU state {mode} (ecu.state.<{'|'.join(ECU_MODES)}>)")
            plan.conditions.append((cond.name, cond.initial,
                                    f"uint8_t state = 0U;\n            ::ecustate::rte::readState(state);\n"
                                    f"            return state == {ECU_MODES[mode]}U;", f"{name} ({mode})"))
            continue
        if name == DEM_SUPPLY_CONDITION:
            # IO-59/NVM-08: the provider of the role power_supply reports the supply OK (health OK, state OK)
            plan.uses_power = True
            plan.conditions.append((cond.name, cond.initial,
                                    f"{_dem_power_read()}\n            return (power_health == 1U) && "
                                    f"(power_state == {BSW_POWER_STATES['OK']}U);", f"{name} (bsw.power)"))
            continue
        raise RteError(f"{where}: bsw {name} is not a condition the RTE knows (ecu.state = RUN, "
                       f"ecu.state.<{'|'.join(ECU_MODES)}>, {DEM_SUPPLY_CONDITION}, or a boolean port)")

    for i, dtc in enumerate(diag.dtcs):
        where = f"diag.yaml dtcs[{i}].event ({dtc.name})"
        ev = dtc.event
        kind = ev.result
        if kind is None and (ev.failed_above is not None or ev.failed_below is not None):
            kind = "limit"
        sample, reported, source = None, None, ""
        if ev.port is not None:
            buffer, port = _dem_buffer_of(components, buffers, port_buffer, ev.port, where)
            if kind is None:
                kind = ("qualifier" if buffer.bsw in (QLF_KIND, AGE_KIND) else
                        "boolean" if port.type.native == "boolean" else None)
            if kind is None:
                raise RteError(f"{where}: say how the {port.type.native} value of {ev.port} becomes a test result "
                               f"(result: boolean, qualifier, status or limit with failed_above/failed_below)")
            sample = _dem_result_code(where, kind, ev, port.type.native, _dem_value(buffer))
            source = f"{ev.port} ({kind})"
        elif ev.bsw.startswith(f"{QLF_KIND}."):
            name = ev.bsw[len(QLF_KIND) + 1:]
            buffer = buffers.get(f"{QLF_KIND}.{name}")
            if buffer is None:
                raise RteError(f"{where}: {ev.bsw}: the RTE receives no message {name} (connect a signal of it or "
                               f"list it under can.monitor in the ECU description)")
            kind = kind or "qualifier"
            sample = _dem_result_code(where, kind, ev, "uint8", _dem_value(buffer))
            source = f"{ev.bsw} ({kind})"
        elif ev.bsw.startswith(f"{AGE_KIND}."):
            # SYS-32: the age qualifier of a provided port (the optional event of the age supervision)
            buffer = buffers.get(ev.bsw)
            if buffer is None:
                raise RteError(f"{where}: {ev.bsw}: the age of {ev.bsw[len(AGE_KIND) + 1:]} is not supervised; "
                               f"connect a required port (uint8) to {ev.bsw} in the ECU description")
            kind = kind or "qualifier"
            sample = _dem_result_code(where, kind, ev, "uint8", _dem_value(buffer))
            source = f"{ev.bsw} ({kind})"
        elif ev.bsw in DEM_SUPPLY_EVENTS:
            # IO-59/NVM-08: sampled by the RTE from the BSW service bsw.power, not reported
            plan.uses_power = True
            sample = _dem_supply_sample(where, ev, ev.bsw)
            limit = getattr(ev, DEM_SUPPLY_EVENTS[ev.bsw][0])
            source = (f"{ev.bsw} (supply voltage against {limit} mV)" if limit is not None
                      else f"{ev.bsw} (state of the supply provider)")
        elif ev.bsw.startswith(f"{BSW_SERVICE_PREFIX}.power."):
            raise RteError(f"{where}: {ev.bsw} is no event of the supply (events: {', '.join(DEM_SUPPLY_EVENTS)}; "
                           f"bsw.power.<value> is read through an identifier or a port)")
        else:
            if kind is not None:
                raise RteError(f"{where}: {ev.bsw} is reported by the BSW with AUTOSAR event states; result "
                               f"{kind} applies to sampled sources only")
            reported, source = ev.bsw, f"{ev.bsw} (reported by the BSW)"
            if ev.bsw.startswith(DEM_CAN_BUSOFF_PREFIX):
                # COM-46: the CAN system reports bus-off of a bus it runs - the bus must be one the project uses
                bus = ev.bsw[len(DEM_CAN_BUSOFF_PREFIX):]
                if buses is not None and bus not in buses:
                    raise RteError(f"{where}: {ev.bsw}: {bus} is no CAN bus the project uses "
                                   f"({', '.join(buses) or 'none'})")
                if any(event == ev.bsw for _ident, event in plan.busoff):
                    raise RteError(f"{where}: {ev.bsw} is already the event of another DTC")
                plan.busoff.append((c_name(bus), ev.bsw))
            elif ev.bsw == DEM_SAFETY_DEVICE_FAULT:
                plan.safety_device_fault = True
            elif ev.bsw == DEM_RTE_DEADLINE:
                plan.rte_deadline = True
            elif ev.bsw == DEM_SAFETY_HW_SHUTOFF:
                plan.safety_hw_shutoff = True
            if dem_bsw_reporter(ev.bsw) is None:
                plan.warnings.append(
                    f"diag.yaml dtcs[{i}].event.bsw ({dtc.name}): {ev.bsw} is not reported by any BSW module of "
                    f"neXtBSW yet (known: {', '.join(f'{k} ({v})' for k, v in DEM_BSW_REPORTERS.items())}); the DTC "
                    f"changes only when a module reports it through dem::IFaultMemory::report()")
        counter = time = None
        if dtc.debounce.counter is not None:
            c = dtc.debounce.counter
            counter = (c.failed_threshold, c.passed_threshold, c.increment, c.decrement,
                       0 if c.jump_up is None else c.jump_up, 0 if c.jump_down is None else c.jump_down)
        else:
            time = (dtc.debounce.time.failed_ms, dtc.debounce.time.passed_ms)
            for value, what in ((time[0], "failed_ms"), (time[1], "passed_ms")):
                if value % memory.cycle_ms:
                    plan.warnings.append(f"diag.yaml dtcs[{i}].debounce.time.{what} ({dtc.name}): {value} ms is not a "
                                         f"multiple of fault_memory.cycle_ms {memory.cycle_ms}; it matures after "
                                         f"{-(-value // memory.cycle_ms) * memory.cycle_ms} ms")
        aging_cycle = (dtc.aging.cycle if dtc.aging and dtc.aging.cycle else memory.aging_cycle) or dtc.operation_cycle
        snapshots = [(s.record_number, [(did_by_name[n].identifier, did_by_name[n].length_bytes, n) for n in s.dids])
                     for s in dtc.snapshots]
        extended = [(r.record_number,
                     [("DID", did_by_name[item.did].identifier, did_by_name[item.did].length_bytes, item.name)
                      if item.did is not None else (DEM_COUNTERS[item.counter], 0, 1, item.name)
                      for item in r.data])
                    for r in dtc.extended_data]
        mask = sum(1 << cond_idx[n] for n in dtc.enable_conditions)
        plan.dtcs.append(DemDtc(i, dtc.name, dtc.number, nxdiagdesc.SEVERITY_BITS[dtc.severity]
                                | nxdiagdesc.DTC_CLASS_UNCLASSIFIED, dtc.priority, dtc.confirmation_threshold,
                                dtc.aging.threshold_cycles if dtc.aging else 0, cycles_idx[dtc.operation_cycle],
                                cycles_idx[aging_cycle], mask, counter, time, snapshots, extended, source, sample,
                                reported))
    if plan.entry_data_bytes > 0xFFFF:
        raise RteError(f"diag.yaml dtcs: a DTC stores {plan.entry_data_bytes} bytes of snapshots and extended data, "
                       f"an entry holds at most 65535")
    return plan


def render_dem_code(plan: DemPlan | None) -> str:
    """rte::demSample/demCondition/demCycleState (RteConfig.cpp): the dem::ISource of the integration."""
    if plan is None:
        return ""
    lines = ["", "// ---- NVM-03: sources of the fault memory (dem::ISource of ecu/application/src/systems/dem) ----",
             "uint8_t demSample(size_t const dtc)", "{", "    ::async::LockType const lock;", "    switch (dtc)", "    {"]
    for d in plan.dtcs:
        if d.sample is None:
            continue
        lines += [f"        case {d.index}U: // {d.name}: {d.source}", "        {", f"            {d.sample}",
                  "        }"]
    lines += [f"        default: return {DEM_RESULT['NONE']}U; // reported by the BSW (dem::IFaultMemory::report)",
              "    }", "}", "", "bool demCondition(size_t const condition)", "{", "    switch (condition)", "    {"]
    for i, (name, _, code, source) in enumerate(plan.conditions):
        # BSW hand-overs are single words (readState, readPower): no lock of the RTE
        lock = "" if ("readState" in code or "readPower" in code) else "            ::async::LockType const lock;\n"
        lines += [f"        case {i}U: // {name}: {source}", "        {", lock + f"            {code}", "        }"]
    lines += ["        default: return false;", "    }", "}", "", "uint8_t demCycleState(size_t const cycle)", "{",
              "    switch (cycle)", "    {"]
    for i, (name, autostart, code, source) in enumerate(plan.cycles):
        if autostart:
            continue
        lines += [f"        case {i}U: // {name}: {source}", "        {", "            ::async::LockType const lock;",
                  f"            {code}", "        }"]
    lines += ["        default: return 0xFFU; // autostart: runs with the ECU", "    }", "}"]
    return "\n".join(lines)


def render_dem_reporters(plan: DemPlan | None, source: str) -> str:
    """dem/demBswReporters.h (COM-46/NVM-08): which BSW events of the diagnostic description a module of the platform
    or the integration reports - written for every project, empty without such DTCs, so a project without them
    compiles exactly the code it compiled before."""
    lines = [f"// Generated by tools/rtegen from {source} and the diagnostic description - do not edit.", "//",
             "// COM-46/NVM-08: BSW events of diag.yaml (dtcs[].event.bsw) that a module reports through",
             "// dem::IFaultMemory::report(); a module compiles its reporter only for the events named here.",
             "//   NX_DEM_CAN_BUSOFF(X)          X(<bus>, \"can.busoff.<BUS>\") per bus with a bus-off DTC",
             "//                                 (CanSystem of the board port)",
             "//   NX_DEM_SAFETY_DEVICE_FAULT    RteSystem reports the degraded reaction of the safe supervisor",
             "//   NX_DEM_SAFETY_HW_SHUTOFF      RteSystem reports the hardware shut-off path (SYS-136)",
             "", "#pragma once", ""]
    if plan is not None and plan.busoff:
        lines += ["#define NX_DEM_CAN_BUSOFF(X) \\"]
        rows = [f'    X({ident}, "{event}")' for ident, event in plan.busoff]
        lines += [row + " \\" for row in rows[:-1]] + rows[-1:]
        lines.append("")
    if plan is not None and plan.safety_device_fault:
        lines += ["#define NX_DEM_SAFETY_DEVICE_FAULT 1", ""]
    if plan is not None and plan.rte_deadline:
        # COM-47: only here (not in the head of the file), so a project without the DTC keeps its header byte for byte
        lines += ["// COM-47: RteSystem reports rte.deadline as a level that follows the deadlines (dem/DeadlineReport.h)",
                  "#define NX_DEM_RTE_DEADLINE 1", ""]
    if plan is not None and plan.safety_hw_shutoff:
        lines += ["#define NX_DEM_SAFETY_HW_SHUTOFF 1", ""]
    return "\n".join(lines)


def render_dem_header(plan: DemPlan, source: str) -> str:
    """RteDem.h: the tables of dem::Config (libs/bsw/dem), included only by the integration of the fault memory."""
    lines = [f"// Generated by tools/rtegen from {source} and the diagnostic description - do not edit.",
             "//",
             "// NVM-03: tables of the fault memory (dem::Config, libs/bsw/dem/include/dem/DemTypes.h). Only the",
             "// integration includes this header (it links libs/bsw/dem); the sources are rte::demSample/demCondition/",
             "// demCycleState of RteConfig.h, the persistent state the NvM blocks DemEvents/DemEntries.",
             "", "#pragma once", "", "#include \"RteConfig.h\"", "", "#include <dem/DemTypes.h>", "",
             "#include <cstddef>", "#include <cstdint>", "", "namespace rte", "{", "namespace demtables", "{"]
    rows = []
    for d in plan.dtcs:
        snap_ref, ext_ref = "nullptr", "nullptr"
        if d.snapshots:
            for r, (number, items) in enumerate(d.snapshots):
                lines.append(f"inline constexpr ::dem::DataItem SNAPSHOT_{d.index}_{r}[] = {{"
                             + ", ".join(f"{{0x{did:04X}U, {b}U}}" for did, b, _ in items) + "}; // "
                             + ", ".join(n for _, _, n in items))
            lines.append(f"inline constexpr ::dem::SnapshotRecord SNAPSHOTS_{d.index}[] = {{"
                         + ", ".join(f"{{0x{number:02X}U, SNAPSHOT_{d.index}_{r}, {len(items)}U}}"
                                     for r, (number, items) in enumerate(d.snapshots)) + "};")
            snap_ref = f"SNAPSHOTS_{d.index}"
        if d.extended:
            for r, (number, items) in enumerate(d.extended):
                lines.append(f"inline constexpr ::dem::ExtendedItem EXTENDED_{d.index}_{r}[] = {{"
                             + ", ".join(f"{{::dem::Counter::{c}, 0x{did:04X}U, {b}U}}" for c, did, b, _ in items)
                             + "}; // " + ", ".join(n for *_, n in items))
            lines.append(f"inline constexpr ::dem::ExtendedRecord EXTENDED_{d.index}[] = {{"
                         + ", ".join(f"{{0x{number:02X}U, EXTENDED_{d.index}_{r}, {len(items)}U}}"
                                     for r, (number, items) in enumerate(d.extended)) + "};")
            ext_ref = f"EXTENDED_{d.index}"
        if d.counter is not None:
            f_, p_, inc, dec, ju, jd = d.counter
            debounce = f"::dem::DebounceKind::COUNTER, {{{f_}, {p_}, {inc}U, {dec}U, {ju}, {jd}}}, {{0U, 0U}}"
        else:
            debounce = f"::dem::DebounceKind::TIME, {{0, 0, 0U, 0U, 0, 0}}, {{{d.time[0]}U, {d.time[1]}U}}"
        reported = f'"{d.reported}"' if d.reported else "nullptr"
        rows.append(f"    {{\"{d.name}\", 0x{d.number:06X}U, 0x{d.severity:02X}U, {d.priority}U, {d.confirmation}U, "
                    f"{d.aging}U, {d.cycle}U, {d.aging_cycle}U, 0x{d.conditions:X}U, {debounce}, {snap_ref}, "
                    f"{len(d.snapshots)}U, {ext_ref}, {len(d.extended)}U, {reported}}}, // {d.source}")
    cycles = ", ".join(f'"{n}"' for n, *_ in plan.cycles) or "nullptr"
    conditions = ", ".join(f'"{n}"' for n, *_ in plan.conditions) or "nullptr"
    lines += ["", "inline constexpr ::dem::DtcConfig DTCS[] = {"] + rows + ["};", "",
              f"inline constexpr char const* CYCLE_NAMES[] = {{{cycles}}};",
              f"inline constexpr char const* CONDITION_NAMES[] = {{{conditions}}};", "",
              "// dtcs, count, max entries, replace oldest, cycles, autostart mask, conditions, initial, cycle ms, data",
              f"inline constexpr ::dem::Config CONFIG = {{DTCS, {len(plan.dtcs)}U, {plan.max_entries}U, "
              f"{'true' if plan.replace_oldest else 'false'}, {len(plan.cycles)}U, 0x{plan.autostart_mask:02X}U, "
              f"{len(plan.conditions)}U, 0x{plan.initial_conditions:X}U, {plan.cycle_ms}U, {plan.entry_data_bytes}U}};",
              "",
              f"static_assert(::rte::DEM_DTC_COUNT == {len(plan.dtcs)}U, \"DTC table\");",
              f"static_assert(::dem::eventsBlockBytes({len(plan.dtcs)}U) == ::rte::DEM_EVENTS_BYTES, "
              "\"layout of the NvM block DemEvents\");",
              f"static_assert(::dem::entriesBlockBytes({plan.max_entries}U, {plan.entry_data_bytes}U) == "
              "::rte::DEM_ENTRIES_BYTES, \"layout of the NvM block DemEntries\");",
              "", "} // namespace demtables", "} // namespace rte", ""]
    return "\n".join(lines)


def build_rte(ecu, components: dict[str, Component], signals: dict[str, set[str]],
              can: CanConfig | None = None, wakeup: dict[str, int] | None = None):
    wakeup = wakeup if wakeup is not None else wakeup_sources(None)
    connections = ecu_description(ecu, "connections").connections
    buffers: dict[str, Buffer] = {}      # key: Component.Port.Element of the owning port or BSW input
    port_buffer: dict[str, str] = {}     # Component.Port.Element -> buffer key
    used = set()

    def cname(key):
        return "Rte_Buf_" + key.replace(".", "_")

    # 1st pass: every P-port element owns a buffer
    for comp in components.values():
        for key, port in comp.ports.items():
            full = f"{comp.name}.{key}"
            if port.direction == "out":
                buffers[full] = Buffer(cname(full), port.type.ctype, port.init, full, dtype=port.type)
                port_buffer[full] = full

    # 2nd pass: connections. A provided port may feed several targets (list), a required port has one source.
    items = []
    for src, dst in connections.items():
        comp_name, _, key = src.partition(".")
        comp = components.get(comp_name)
        if comp is not None and key in comp.mode_ports:
            continue    # RTE-08: mode ports are connected by build_modes()
        if comp is not None and key in comp.parameters:
            raise RteError(f"connection {src}: {key} is a calibration parameter; it is not connected, its value "
                           f"comes from {comp_name}_data.m (RTE-03)")
        if comp is None or key not in comp.ports:
            raise RteError(f"connection {src}: unknown component port (expected Component.Port.Element)")
        port = comp.ports[key]
        if src in used:
            raise RteError(f"connection {src}: port connected twice")
        used.add(src)
        targets = dst if isinstance(dst, list) else [dst]     # a list has at least one target (schema)
        if isinstance(dst, list) and port.direction == "in":
            raise RteError(f"connection {src}: a required port has exactly one source, not a list")
        items += [(src, comp, key, port, t) for t in targets]

    nvm: dict[str, dict] = {}            # NVM-02: block -> writer (Component.Port.Element, Port) and readers
    nvm_qlf: list = []                   # NVM-18: (port, nvm.qlf.<Block>) of the quality readers
    for src, comp, key, port, dst in items:
        kind, _, signal = dst.rpartition(".")
        # RTE-04: records, arrays and fixed point stay between components (and in the RTE); BSW and CAN targets
        # carry unscaled scalars. Enumerations are integers and may use them. NvM blocks (NVM-02) store the bytes of
        # the buffer, so they take every type.
        if dst not in ("rte", "rte.init") and dst.partition(".")[0] not in (*components, NVM_PREFIX) \
                and (port.type.composite or port.type.scaled):
            what = {"ARRAY": "an array", "STRUCTURE": "a record"}.get(port.type.category, "fixed point")
            if port.type.category == "ARRAY" and dst.startswith("can."):
                # RTE-25: CAN signals stay scalar - one DBC signal carries one value
                raise RteError(f"connection {src} -> {dst}: {port.type.describe()} is an array port with "
                               f"{port.type.size} elements, a CAN signal carries one scalar value. Next step: "
                               f"give each element that goes on the bus its own scalar port (e.g. a Selector or "
                               f"Demux in the model, one DBC signal per element) and connect those; keep the "
                               f"array between components (component port, rte)")
            raise RteError(f"connection {src} -> {dst}: {port.type.describe()} is {what}, BSW and CAN targets take "
                           f"unscaled scalars only; connect it to a component port, rte or rte.init")
        can_kind = dst.split(".")[0] + "." + dst.split(".")[1] if dst.count(".") >= 1 else ""
        if can_kind in CAN_KINDS:
            if CAN_KINDS[can_kind] != port.direction:
                raise RteError(f"connection {src} -> {dst}: {port.direction}put port cannot use {can_kind}")
            entry, link = can_link(can, src, port, dst, can_kind[4:])
            if port.direction == "in":
                buffers[src] = Buffer(cname(src), port.type.native, port.init, src, can_kind, signal, [dst])
                port_buffer[src] = src
                link.buffer = buffers[src]
            else:
                link.buffer = buffers[src]
                link.buffer.links.append(dst)
            entry.links.append(link)
        elif dst.startswith(f"{NVM_QLF_KIND}."):
            # NVM-18: quality of a block, set by the NvM at the start; several ports may read it
            port_buffer[src] = nvm_qlf_link(buffers, src, port, dst)
            nvm_qlf.append((src, dst))
        elif dst.startswith(f"{NVM_PREFIX}."):
            block = nvm_block_name(src, dst)
            entry = nvm.setdefault(block, {"writer": None, "readers": []})
            if port.direction == "out":
                if entry["writer"] is not None:
                    raise RteError(f"connection {src} -> {dst}: {dst} is already written by {entry['writer'][0]}; a "
                                   f"block has exactly one writing port (read it with required ports instead)")
                b = buffers[src]
                if b.nvm is not None:
                    raise RteError(f"connection {src} -> {dst}: already stored as {NVM_PREFIX}.{b.nvm}; a port "
                                   f"writes one block")
                entry["writer"] = (src, port)
                b.nvm = block
                b.links.append(dst)
            else:
                entry["readers"].append((src, port))
        elif can_kind == QLF_KIND:
            # one qualifier per received message, computed by the RTE (COM-03); several ports may read it
            port_buffer[src] = can_qlf_link(can, buffers, src, port, dst)
        elif dst.startswith(f"{AGE_PREFIX}.") and dst.partition(".")[0] not in components:
            # SYS-32: age qualifier of a provided port, computed by the RTE; several ports may read it
            port_buffer[src] = age_link(components, buffers, src, port, dst)
        elif dst.partition(".")[0] in (signals.get(DEVICE_SOURCES) or {}):
            # RTE-18: a value of a module instance (capture, hall, gd, every module with module.yaml rte)
            source = signals[DEVICE_SOURCES][dst.partition(".")[0]]
            device_kind, name = device_link(source, src, port, dst)
            ident = resolve_board_name(source.names, name, f"connection {src} -> {dst}", source.prefix)
            buffers[src] = Buffer(cname(src), port.type.native, port.init, src, device_kind, ident, [dst],
                                  bsw_name=name, device=source)
            port_buffer[src] = src
        elif dst.startswith(f"{PINEVENT_PREFIX}."):
            pinevent_kind, name = pinevent_link(signals, src, port, dst)
            ident = resolve_board_name(signals[PINEVENT_PREFIX], name, f"connection {src} -> {dst}",
                                       PINEVENT_PREFIX)
            buffers[src] = Buffer(cname(src), port.type.native, port.init, src, pinevent_kind, ident, [dst],
                                  bsw_name=name)
            port_buffer[src] = src
        elif dst.startswith(f"{BSW_SERVICE_PREFIX}."):
            service_kind, service = bsw_service_link(src, port, dst)
            buffers[src] = Buffer(cname(src), port.type.native, port.init, src, service_kind, service, [dst])
            port_buffer[src] = src
        elif dst.startswith(SAFETY_SAFE_STATE_TARGETS):
            # SYS-141: the selection of the safe state of a PWM group (before the other safety.* targets)
            safe_kind, group = safe_state_link(src, port, dst, signals.get(SAFETY_SAFE_STATES) or {})
            literal = f"{group.index}U"
            if port.direction == "in":
                buffers[src] = Buffer(cname(src), port.type.native, port.init, src, safe_kind, literal, [dst],
                                      bsw_name=group.name)
                port_buffer[src] = src
            else:
                b = buffers[src]
                if b.bsw is not None:
                    raise RteError(f"connection {src} -> {dst}: already connected to {b.bsw}{b.bsw_label}")
                other = next((o for o in buffers.values() if o.bsw == safe_kind and o.bsw_name == group.name), None)
                if other is not None:
                    raise RteError(f"connection {src} -> {dst}: the safe state of {group.name} is already requested "
                                   f"by {other.label} (one writer per PWM group)")
                b.bsw, b.bsw_signal, b.bsw_name = safe_kind, literal, group.name
                b.links.append(dst)
        elif dst.startswith(f"{SAFETY_PREFIX}."):
            # SYS-117: the degraded groups of the safe supervisor, read only
            safety_kind, mask = safety_link(src, port, dst, signals.get(SAFETY_GROUPS) or {})
            buffers[src] = Buffer(cname(src), port.type.native, port.init, src, safety_kind, mask, [dst])
            port_buffer[src] = src
        elif dst.startswith("ecu."):
            ecu_kind, mask = ecu_link(src, port, dst, wakeup)
            if port.direction == "in":
                buffers[src] = Buffer(cname(src), port.type.native, port.init, src, ecu_kind, mask, [dst])
                port_buffer[src] = src
            else:
                b = buffers[src]
                if b.sleep_request:
                    raise RteError(f"connection {src} -> {dst}: already connected to {dst}")
                b.sleep_request = True
                b.links.append(dst)
        elif kind in BSW_KINDS:
            spec = BSW_KINDS[kind]
            if spec["direction"] != port.direction:
                raise RteError(f"connection {src} -> {dst}: {port.direction}put port cannot use {kind}")
            owner = signals.get("dio.out.owned", {}).get(signal) if kind == "dio.out" else None
            if owner is not None:
                raise RteError(f"connection {src} -> {dst}: {signal} is driven by the module instance {owner}, "
                               f"the RTE must not write it")
            if kind in ADC_SENSOR_KINDS and signal in signals.get("adc.unfitted", set()):
                raise RteError(f"connection {src} -> {dst}: the circuit of {signal} is not fitted on the board "
                               f"(board.yaml adc fitted: false), there is no physical value")
            if kind in ADC_SENSOR_KINDS and signal in signals.get("adc.internal", set()):
                raise RteError(f"connection {src} -> {dst}: {signal} is an internal ADC channel (project.yaml "
                               f"adc_internal) without circuit or sensor plan, there is no physical value; "
                               f"use adc.mv.{signal} or adc.raw.{signal}")
            if signal not in signals[kind]:
                raise RteError(f"connection {src} -> {dst}: {signal} is not a {kind} signal used by the project"
                               f"{name_hint(signals[kind], signal, kind)}")
            ident = resolve_board_name(signals[kind], signal, f"connection {src} -> {dst}", kind)
            if port.type.native not in spec["types"]:
                raise RteError(
                    f"connection {src} -> {dst}: type {port.type.native} does not fit {kind} "
                    f"({', '.join(sorted(spec['types']))})"
                )
            if port.direction == "in":
                buffers[src] = Buffer(cname(src), port.type.native, port.init, src, kind, ident, [dst],
                                      bsw_name=signal)
                port_buffer[src] = src
            else:
                b = buffers[src]
                if b.bsw is not None:
                    raise RteError(f"connection {src} -> {dst}: already connected to {b.bsw}.{b.bsw_label}")
                if kind == "pwm":
                    other = next((o for o in buffers.values() if o.bsw == kind and o.bsw_signal == ident), None)
                    if other is not None:
                        raise RteError(f"connection {src} -> {dst}: {dst} is already driven by {other.label}")
                b.bsw, b.bsw_signal, b.bsw_name = kind, ident, signal
                b.links.append(dst)
        elif dst.split(".")[0] == DEM_PREFIX:
            # NVM-03: the fault memory samples this provided port (dem.event.<Dtc>: the same link as diag.yaml
            # event.port; dem.cycle.<Cycle>: runs an operation cycle). Checked against the description in plan_dem().
            if port.direction != "out":
                raise RteError(f"connection {src} -> {dst}: the fault memory reads provided ports only; a required "
                               f"port gets nothing from {DEM_PREFIX}.*")
            buffers[src].links.append(dst)
            buffers[src].dem_links.append(dst)
        elif dst.split(".")[0] in DIAG_PREFIXES:
            # COM-28: the diagnostic jobs write this required port (did.<Did>: what the tester writes with 2E,
            # routine.<Routine>.<step>[.<param>]: a routine request) or read this provided port (routine response,
            # or the same port diag.yaml names as source). Checked against the description in plan_diag().
            if port.direction == "in":
                buffers[src] = Buffer(cname(src), port.type.ctype, port.init, src, None, None, [dst],
                                      dtype=port.type)
                port_buffer[src] = src
            else:
                buffers[src].links.append(dst)
            buffers[src].diag_links.append(dst)
        elif dst == "rte.init":
            # Unconnected receiver port (AUTOSAR init value): the RTE keeps a buffer nobody writes, so the
            # component reads the init value of the port and "rte signals" shows it. For reusable components
            # whose port has no source in a project, e.g. the signal qualifier in a project without CAN.
            if port.direction != "in":
                raise RteError(f"connection {src} -> rte.init: only required ports can keep their init value")
            buffers[src] = Buffer(cname(src), port.type.ctype, port.init, src, None, None, [dst], dtype=port.type)
            port_buffer[src] = src
        elif dst == "rte":
            if port.direction != "out":
                raise RteError(f"connection {src} -> rte: only provided ports can stay inside the RTE "
                               f"(a required port without source: rte.init)")
        else:
            other_comp, _, other_key = dst.partition(".")
            other = components.get(other_comp)
            if other is None or other_key not in other.ports:
                raise RteError(f"connection {src} -> {dst}: unknown target")
            target = other.ports[other_key]
            if port.direction != "in" or target.direction != "out":
                raise RteError(f"connection {src} -> {dst}: must connect a required port to a provided port")
            if port.type.signature() != target.type.signature():
                mismatch = dimension_mismatch(port.type, target.type)
                if mismatch:
                    raise RteError(f"connection {src} -> {dst}: {mismatch}")
                if not port.type.composite and not target.type.composite                         and port.type.native != target.type.native:
                    raise RteError(f"connection {src} -> {dst}: type {port.type.native} != {target.type.native}")
                raise RteError(f"connection {src} -> {dst}: type {port.type.name} does not match "
                               f"{target.type.name} of the provided port "
                               f"({type_difference(port.type, target.type)})")
            port_buffer[src] = dst

    # NVM-02: a required port on nvm.<Block> reads the buffer of the block's writer
    for block, entry in nvm.items():
        readers = ", ".join(src for src, _ in entry["readers"])
        if entry["writer"] is None:
            raise RteError(f"connections: {NVM_PREFIX}.{block} is read by {readers}, but no provided port writes it; "
                           f"a block has exactly one writing port, whose init value is the default of the block")
        writer, wport = entry["writer"]
        for src, port in entry["readers"]:
            if port.type.signature() != wport.type.signature():
                raise RteError(f"connection {src} -> {NVM_PREFIX}.{block}: type {port.type.describe()} does not match "
                               f"{wport.type.describe()} of the writing port {writer} "
                               f"({type_difference(port.type, wport.type) or 'different type'})")
            port_buffer[src] = writer
    for src, dst in nvm_qlf:
        block = dst[len(NVM_QLF_KIND) + 1:]
        if nvm.get(block, {}).get("writer") is None:
            raise RteError(f"connection {src} -> {dst}: no provided port writes {NVM_PREFIX}.{block}, so the block "
                           f"has no quality; connect the writing port of the block to {NVM_PREFIX}.{block}")

    disable_ports(components, buffers, port_buffer, cname)             # RTE-23

    for comp in components.values():
        for key, port in comp.ports.items():
            full = f"{comp.name}.{key}"
            if port.direction == "in" and full not in port_buffer:
                # RTE-27: no stop - a constant buffer with the init value (init_value_warnings reports it)
                buffers[full] = init_value_buffer(comp, key, INIT_UNCONNECTED, cname(full))
                port_buffer[full] = full

    if can is not None:
        finish_can(can, buffers)
    return buffers, port_buffer


def is_qualifier_port(port: Port) -> bool:
    """RTE-27: a required port that carries a qualifier (QLF_* of Rte_Qlf.h): an unscaled QLF_TYPES scalar whose port
    or element name ends in Qlf or Qualifier (the naming of COM-03/RTE-07, e.g. PcCommandQlf, SwitchQlf)."""
    if port.type.composite or port.type.scaled or port.type.native not in QLF_TYPES:
        return False
    return any(name.lower().endswith(QLF_PORT_SUFFIXES) for name in (port.name, port.element))


def init_value_buffer(comp: Component, key: str, reason: str, name: str) -> Buffer:
    """RTE-27: the constant buffer of a required port that reads only its init value (reason: INIT_UNCONNECTED;
    RTE-23 adds the ports fed by a disabled part). Init value of the port (ARXML/swc.yaml, 0 without one), a
    qualifier port QLF_NOT_AVAILABLE (RTE-28). Refused for a safety-relevant component (see INIT_UNCONNECTED)."""
    port = comp.ports[key]
    full = f"{comp.name}.{key}"
    if comp.safety != "qm":
        raise RteError(f"required port {full} is not connected: {comp.name} is safety relevant (swc.yaml safety "
                       f"{comp.safety}), an init value in place of one of its inputs would silently replace a "
                       f"safety function; connect it to its source, or to rte.init to keep the init value as a "
                       f"documented decision")
    init = c_literal(port.type.native, QLF[QLF_NO_SOURCE]) if is_qualifier_port(port) else port.init
    return Buffer(name, port.type.ctype, init, full, None, None, [], dtype=port.type, init_only=reason)


def init_value_warnings(components: dict[str, Component], buffers: dict[str, Buffer]) -> list[str]:
    """RTE-27: one warning per required port that reads only its init value; the prefix 'required port <...>' gives
    file, YAML path (connections.<port>) and line (rte_message). Same text in generator, Studio check and schedana."""
    found = []
    for key, b in buffers.items():
        if b.init_only != INIT_UNCONNECTED:
            continue
        comp_name, _, port_key = key.partition(".")
        if not components[comp_name].enabled:
            continue    # RTE-23: a disabled component reads nothing, its unconnected inputs need no source
        port = components[comp_name].ports[port_key]
        if is_qualifier_port(port):
            found.append(f"required port {key} is not connected: it reads QLF_{QLF_NO_SOURCE} "
                         f"({QLF[QLF_NO_SOURCE]}, no source) from a constant buffer, so the component treats the "
                         f"value it qualifies as unavailable; connect it to its source (e.g. can.qlf.<Message>)")
        else:
            found.append(f"required port {key} is not connected: it reads its init value {b.init} from a constant "
                         f"buffer; connect it to its source, or to rte.init to keep the init value deliberately")
    return found


# ---- RTE-23: disabled components and runnables (see INIT_DISABLED) ---------------------------------------------------

SAFETY_DISABLE_REASON = ("a disabled part would silently switch off a safety function, and the ECU description "
                         "records no safety analysis for it")


def disable_safety_error(where: str, what: str, comp: Component) -> RteError:
    """RTE-23: why a safety-relevant component and its runnables cannot be disabled - not even with a confirmation.

    The switch is one line of rte.yaml: nothing records who confirmed it, on which analysis, and it is flipped back
    and forth in the Studio. A safety function belongs to the safety concept, not to a configuration switch. The
    class comes from the component's own swc.yaml (component_safety), so lowering it is a change of the component,
    reviewed with it; taking the component out of rte.yaml is a visible change of the ECU as well."""
    return RteError(f"{where}: {what} cannot be disabled: {comp.name} is safety relevant (swc.yaml safety "
                    f"{comp.safety}) - {SAFETY_DISABLE_REASON}. Keep it enabled; to take it out, lower the class in "
                    f"its swc.yaml after the safety analysis or remove it from the ECU description")


def yaml_value(dtype: DataType, value, where: str):
    """RTE-23: stored value of a components[].disabled_values entry for the port type - like an ARXML init value:
    physical for fixed point, a literal for an enumeration, a list for an array or a record (fields in order)."""
    if dtype.category == "ARRAY":
        if not isinstance(value, list) or len(value) != dtype.size:
            raise RteError(f"{where}: {dtype.describe()} needs a list of {dtype.size} values")
        return [yaml_value(dtype.element, v, f"{where}[{i}]") for i, v in enumerate(value)]
    if dtype.category == "STRUCTURE":
        if not isinstance(value, list) or len(value) != len(dtype.fields):
            names = ", ".join(n for n, _ in dtype.fields)
            raise RteError(f"{where}: {dtype.describe()} needs a list of {len(dtype.fields)} values ({names})")
        return [yaml_value(ftype, v, f"{where}.{fname}") for v, (fname, ftype) in zip(value, dtype.fields)]
    if isinstance(value, list):
        raise RteError(f"{where}: {dtype.describe()} is a scalar, not a list")
    if isinstance(value, bool):
        value = int(value)
    return _scalar_raw(dtype, value, True, where)


def apply_enabled(ecu, components: dict[str, Component]):
    """RTE-23: components[].enabled and disabled_values of the ECU description into the component model.

    Called by build_tasks(), so every user of the task plan (generator, Studio check, tools/schedana) sees the same
    parts switched off. The runnable switches (tasks[].runnables[].enabled) are applied there as well."""
    # a mapping without components (a caller checking only its tasks) switches nothing off
    entries = [] if isinstance(ecu, dict) and "components" not in ecu else ecu_description(ecu, "components").components
    for comp in components.values():
        comp.enabled, comp.disabled_where, comp.disabled_values = True, "", {}
        for r in comp.runnables:
            r.enabled, r.disabled_where = True, ""
            r.task = None                 # build_tasks assigns again; a disabled runnable keeps none
    for i, entry in enumerate(entries):
        comp = components.get(entry.name)
        if comp is None:
            continue
        comp.index = i
        if not entry.enabled:
            where = f"components[{i}].enabled"
            if comp.safety != "qm":
                raise disable_safety_error(where, f"component {comp.name}", comp)
            comp.enabled, comp.disabled_where = False, where
            for r in comp.runnables:
                r.enabled, r.disabled_where = False, where
        for key, value in entry.disabled_values.items():
            where = f"components[{i}].disabled_values"
            port = comp.ports.get(key)
            if port is None or port.direction != "out":
                known = ", ".join(k for k, p in comp.ports.items() if p.direction == "out") or "none"
                raise RteError(f"{where}: {key} is no provided port of {comp.name} (Port.Element; provided ports: "
                               f"{known})")
            if port.init_declared:
                raise RteError(f"{where}: {comp.name}.{key} has an init value of its own ({port.init}, ARXML "
                               f"INIT-VALUE) - that is the value it delivers while disabled; remove {key} here, a "
                               f"value stands in one place")
            comp.disabled_values[key] = c_initializer(port.type, yaml_value(port.type, value,
                                                                             f"{where}: {comp.name}.{key}"))


def disabled_source(comp: Component, key: str) -> str | None:
    """RTE-23: YAML path of the switch that keeps every writer of the provided port `key` from running, None while
    one of them runs. Writers: the runnables with an access point to it (every runnable without access points)."""
    if not comp.enabled:
        return comp.disabled_where
    writers = [r for r in comp.runnables if key in r.accesses or not comp.access_points]
    if writers and not any(r.enabled for r in writers):
        return ", ".join(dict.fromkeys(r.disabled_where for r in writers))
    return None


def disabled_value(comp: Component, key: str, reader: Port | None = None) -> str | None:
    """RTE-23: C initializer a disabled provided port delivers - its own init value, else disabled_values, else the
    init value of the reading port; None: nowhere declared (the caller refuses it, never a silent 0)."""
    port = comp.ports[key]
    if port.init_declared:
        return port.init
    if key in comp.disabled_values:
        return comp.disabled_values[key]
    if reader is not None and reader.init_declared:
        return reader.init
    return None


def disable_ports(components: dict[str, Component], buffers: dict[str, Buffer], port_buffer: dict[str, str], cname):
    """RTE-23: the provided ports whose writers are all disabled deliver a constant value, to BSW targets through
    their own buffer, to the required ports of other components through a const buffer each (INIT_DISABLED)."""
    for full, b in list(buffers.items()):
        comp_name, _, key = full.partition(".")
        comp = components.get(comp_name)
        if comp is None or key not in comp.ports or comp.ports[key].direction != "out":
            continue
        reason = disabled_source(comp, key)
        if reason is None:
            continue
        b.disabled_by = reason
        if b.nvm is not None:
            raise RteError(f"connection {full} -> {NVM_PREFIX}.{b.nvm}: {full} is disabled ({reason}), but it writes "
                           f"a non-volatile block - its default would replace the stored value at the next write. "
                           f"Keep its writer enabled, or give the block another writer")
        value = disabled_value(comp, key)
        if b.links and value is None:
            raise RteError(f"connection {full} -> {b.links[0]}: {full} is disabled ({reason}) and has no init value "
                           f"of its own; set the value it delivers in components[{comp.index}].disabled_values "
                           f"({key}) - never a silent 0")
        if value is not None:
            b.init = value
    for full in list(port_buffer):
        comp_name, _, key = full.partition(".")
        comp = components.get(comp_name)
        if comp is None or key not in comp.ports or comp.ports[key].direction != "in" or not comp.enabled:
            continue
        source = port_buffer[full]
        b = buffers[source]
        if source.startswith(f"{AGE_KIND}.") and b.init_only == INIT_DISABLED:
            if comp.safety != "qm":
                raise RteError(f"required port {full} reads {source}, whose writer {b.disabled_by} is disabled: "
                               f"{comp.name} is safety relevant (swc.yaml safety {comp.safety}) - "
                               f"{SAFETY_DISABLE_REASON}; enable the writer")
            continue
        s_name, _, s_key = source.partition(".")
        s_comp = components.get(s_name)
        if s_comp is None or source == full or s_key not in s_comp.ports or s_comp.ports[s_key].direction != "out":
            continue
        reason = disabled_source(s_comp, s_key)
        if reason is None:
            continue
        port = comp.ports[key]
        if comp.safety != "qm":
            raise RteError(f"required port {full} reads {source}, which is disabled ({reason}): {comp.name} is "
                           f"safety relevant (swc.yaml safety {comp.safety}) - a constant in place of one of its "
                           f"inputs means {SAFETY_DISABLE_REASON}; enable {source} again or connect the port to "
                           f"another source")
        if is_qualifier_port(port):
            init = c_literal(port.type.native, QLF[QLF_NO_SOURCE])
        else:
            init = disabled_value(s_comp, s_key, port)
            if init is None:
                raise RteError(f"required port {full} reads {source}, which is disabled ({reason}), and neither port "
                               f"has an init value of its own: set the value {source} delivers while disabled in "
                               f"components[{s_comp.index}].disabled_values ({s_key}) - never a silent 0")
        buffers[full] = Buffer(cname(full), port.type.ctype, init, full, None, None, [], dtype=port.type,
                               init_only=INIT_DISABLED)
        buffers[full].disabled_by = f"{source} ({reason})"
        port_buffer[full] = full


def disabled_warnings(components: dict[str, Component], buffers: dict[str, Buffer],
                      port_buffer: dict[str, str]) -> list[str]:
    """RTE-23: what is switched off and what reads a constant because of it - one warning each, with the YAML path of
    the switch or of the reading connection. Same text in generator, Studio check and schedana."""
    found = []
    for comp in components.values():
        if not comp.enabled:
            names = ", ".join(r.name for r in comp.runnables if not r.init) or "none"
            found.append(f"{comp.disabled_where}: component {comp.name} is disabled: its init and its runnables "
                         f"({names}) are not called")
            continue
        for r in comp.runnables:
            if not r.enabled:
                found.append(f"{r.disabled_where}: runnable {comp.name}.{r.name} is disabled: it is not called")
    for key, b in buffers.items():
        if b.init_only == INIT_DISABLED and key.startswith(f"{AGE_KIND}."):
            for reader in [k for k, v in port_buffer.items() if v == key]:
                if components[reader.partition(".")[0]].enabled:
                    found.append(f"required port {reader} reads QLF_{QLF_NO_SOURCE} ({QLF[QLF_NO_SOURCE]}) from "
                                 f"{key}: the writer {b.disabled_by} is disabled, its age is not supervised")
        elif b.init_only == INIT_DISABLED:
            comp_name, _, port_key = key.partition(".")
            port = components[comp_name].ports[port_key]
            what = (f"QLF_{QLF_NO_SOURCE} ({QLF[QLF_NO_SOURCE]}, no source)" if is_qualifier_port(port)
                    else f"the constant value {b.init}")
            found.append(f"required port {key} reads {what} from a constant buffer: its source {b.disabled_by} is "
                         f"disabled")
        elif b.disabled_by is not None and b.links:
            dem = any(link.startswith(f"{DEM_PREFIX}.") for link in b.links)
            found.append(f"connection {key} -> {', '.join(b.links)}: {key} is disabled ({b.disabled_by}), the "
                         f"target{'s get' if len(b.links) > 1 else ' gets'} the constant value {b.init}"
                         + ("; the fault memory samples a value that no longer changes, so the DTC is never "
                            "detected from it" if dem else ""))
    return found


def nvm_block_name(src: str, dst: str) -> str:
    """Checks the target nvm.<Block> and returns the block name."""
    block = dst[len(NVM_PREFIX) + 1:]
    if not NVM_BLOCK_RE.match(block):
        raise RteError(f"connection {src} -> {dst}: expected {NVM_PREFIX}.<Block> with a block name of letters, "
                       f"digits and underscores (at most 32, starting with a letter)")
    return block


def nvm_qlf_link(buffers: dict[str, Buffer], src: str, port: Port, dst: str) -> str:
    """NVM-18: checks a nvm.qlf.<Block> connection and returns the key of the quality buffer of the block."""
    where = f"connection {src} -> {dst}"
    block = dst[len(NVM_QLF_KIND) + 1:]
    if not NVM_BLOCK_RE.match(block):
        raise RteError(f"{where}: expected {NVM_QLF_KIND}.<Block> with a block name of letters, digits and "
                       f"underscores (at most 32, starting with a letter)")
    if port.direction != "in":
        raise RteError(f"{where}: output port cannot use {NVM_QLF_KIND}, the quality of a block is read by required "
                       f"ports")
    if port.type.composite or port.type.scaled or port.type.native not in QLF_TYPES:
        raise RteError(f"{where}: type {port.type.describe()} does not fit {NVM_QLF_KIND} "
                       f"({', '.join(sorted(QLF_TYPES))}, QLF_* of Rte_Qlf.h)")
    key = f"{NVM_QLF_KIND}.{block}"
    if key not in buffers:
        buffers[key] = Buffer(f"Rte_Buf_NvmQlf_{block}", "uint8", f"{QLF[NVM_QUALITY[0][2]]}U", f"nvm.{block}.Qlf",
                              links=[key])
    return key


def nvm_port_shape(dtype: "DataType") -> tuple[str, int] | None:
    """NVM-18: (platform type, length) a block definition gives for a port type - a scalar (length 1) or an array of
    scalars; None for a record or a nested array, which a definition cannot describe."""
    if dtype.category == "VALUE":
        return dtype.native, 1
    if dtype.category == "ARRAY" and dtype.element is not None and dtype.element.category == "VALUE":
        return dtype.element.native, dtype.size
    return None


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE, the CRC of the NvM records (nvm/NvmManager.h crc16; check "123456789" = 0x29B1)."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def nvm_slot_bytes(size: int, integrity: str = "qm") -> int:
    """Bytes of one of the two places of a block (QM: record header, data, CRC-16; ASIL: copy with write counter and
    CRC-32; 4-byte aligned) - nvm::copyBytes(), tools/nxconfig/nvm.py copy_bytes()."""
    from nxconfig import nvm as nxnvm    # noqa: PLC0415
    return nxnvm.copy_bytes(size, integrity)


@dataclass(eq=False)
class NvmBlock:
    """One non-volatile block (NVM-02): the buffer of its writing port, stored in two slots of the NvM layout."""
    name: str
    buffer: Buffer | None        # None: a block of the fault memory (NVM-03), handed over by its integration
    writer: str                  # Component.Port.Element of the provided port
    readers: list                # Component.Port.Element of the required ports
    size: int                    # sizeof() of the buffer on the target
    id: int                      # record id from name and type (never 0x0000 or 0xFFFF)
    task: "Task | None"          # task whose writeOutputs hands a changed value to the NvM image
    address: int = 0
    index: int = 0
    policy: str = "SHUTDOWN"     # NVM_WRITE_POLICIES; blocks of components are written at the run-down
    min_interval_ms: int = 0
    integrity: str = "qm"        # NVM-18: project.yaml nvm.blocks.<Block>.integrity (qm, asil_a..asil_d)
    quality: "Buffer | None" = None   # NVM-18: buffer of nvm.qlf.<Block>, None if nobody reads the quality

    @property
    def slot_bytes(self) -> int:
        return nvm_slot_bytes(self.size, self.integrity)

    @property
    def asil(self) -> bool:
        return self.integrity != "qm"

    @property
    def image_decl(self) -> str:
        if self.buffer is None:      # NVM-03: a block of the fault memory, bytes (zeros: never stored)
            return f"uint8_t {self.image}[{self.size}] = {{}};"
        return f"{self.buffer.ctype} {self.image} = {self.buffer.init};"

    @property
    def default_decl(self) -> str:
        if self.buffer is None:
            return f"uint8_t const {self.default}[{self.size}] = {{}};"
        return f"{self.buffer.ctype} const {self.default} = {self.buffer.init};"

    @property
    def image(self) -> str:
        return f"nvmImage_{self.name}"

    @property
    def default(self) -> str:
        return f"nvmDefault_{self.name}"


@dataclass
class NvmPlan:
    blocks: list
    layout_bytes: int
    slot_max: int
    window_start: int            # derivative memory region of kind eeprom_ram (0/0: none, e.g. S32K3, STM32)
    window_size: int
    window_name: str
    warnings: list = field(default_factory=list)   # NVM-18: blocks without definition (migration)

    @property
    def qualified(self) -> list:
        """NVM-18: blocks whose quality a component reads (nvm.qlf.<Block>)."""
        return [b for b in self.blocks if b.quality is not None]

    @property
    def quality_map(self) -> list:
        """NVM-18: (nvm::Quality name, number, QLF value, QLF name) for the conversion in nvmRestore()."""
        return [(name, number, QLF[qlf], qlf) for name, number, qlf in NVM_QUALITY]


def nvm_block_id(name: str, dtype: "DataType", asil: bool = False) -> int:
    """Record id of a block: CRC-16 over name and type signature, so a renamed or retyped block is not misread.
    NVM-18: an ASIL block (other record format) gets another id, so a block whose integrity changed starts from its
    default instead of reading the old format (the QM id stays the one of NVM-02)."""
    ident = crc16_ccitt((f"{name}:{dtype.signature()!r}" + (":asil" if asil else "")).encode("utf-8"))
    return ident ^ 0x5A5A if ident in (0x0000, 0xFFFF) else ident


def nvm_definition(config, name: str):
    """NVM-18: the entry nvm.blocks.<name> of the project (None: none)."""
    use = getattr(getattr(config, "project", None), "nvm", None) if config is not None else None
    return use.blocks.get(name) if use is not None else None


def check_nvm_definition(name: str, writer: str, dtype: "DataType", entry, warnings: list) -> str:
    """NVM-18: checks the writing port of block `name` against its definition in project.yaml nvm.blocks and returns
    the integrity. Without a definition (a project from before NVM-18) a warning proposes one (migration)."""
    from nxconfig import nvm as nxnvm    # noqa: PLC0415
    where = f"connection {writer} -> {NVM_PREFIX}.{name}"
    shape = nvm_port_shape(dtype)
    integrity = (entry.integrity if entry is not None else None) or nxnvm.INTEGRITY_DEFAULT
    if entry is None or entry.type is None:
        if shape is None and entry is not None:
            return integrity         # a record: the definition names the integrity only
        proposal = nxnvm.suggestion(name, shape[0] if shape else None, shape[1] if shape else 1, integrity)
        what = "has no definition in" if entry is None else "names no type in"
        warnings.append(f"{where}: {NVM_PREFIX}.{name} {what} project.yaml nvm.blocks (NVM-18); it is stored as "
                        f"{dtype.describe()} of the writing port with integrity {integrity}. Define the block: "
                        f"{proposal}")
        return integrity
    if shape is None:
        raise RteError(f"{where}: {dtype.describe()} cannot be described by type and length (project.yaml "
                       f"nvm.blocks.{name} names {entry.type}); for a record leave type and length out and keep "
                       f"only the integrity")
    expected = (entry.type, entry.length or 1)
    if shape != expected:
        text = f"{expected[0]}" + (f"[{expected[1]}]" if expected[1] > 1 else "")
        raise RteError(f"{where}: type {dtype.describe()} of the writing port does not match the definition "
                       f"project.yaml nvm.blocks.{name} ({text}); change the definition or connect a port of "
                       f"that type")
    return integrity


def nvm_window(config) -> tuple[int, int, str]:
    """RAM window of an emulated EEPROM of the derivative (memory region of kind eeprom_ram), (0, 0, "") if none."""
    derivative = getattr(config, "derivative", None) if config is not None else None
    for region in getattr(derivative, "memory", None) or []:
        if region.kind == NVM_WINDOW_KIND:
            return region.start, region.size, region.name
    return 0, 0, ""


def plan_nvm(components: dict[str, Component], buffers: dict[str, Buffer], port_buffer: dict[str, str],
             app: list[Task], config, dem: "DemPlan | None" = None) -> NvmPlan | None:
    """Lays the blocks out (in name order, two slots each) and decides the hand-over task of each block.

    NVM-03: the fault memory adds its blocks DemEvents and DemEntries behind the blocks of the components (derived from
    the diagnostic description, never written in rte.yaml), with the write policy CHANGE and the shortest distance
    fault_memory.write_interval_ms (0: only at the run-down)."""
    writers = sorted(((key, b) for key, b in buffers.items() if b.nvm is not None), key=lambda kb: kb[1].nvm)
    stats_entry = nvm_definition(config, ECU_STATS_BLOCK)                  # NVM-05
    if not writers and dem is None and stats_entry is None:
        return None
    for key, b in writers:
        if b.nvm in DEM_BLOCKS:
            raise RteError(f"connection {key} -> {NVM_PREFIX}.{b.nvm}: {b.nvm} is the name of a block of the fault "
                           f"memory (NVM-03); choose another block name")
        if b.nvm == ECU_STATS_BLOCK:
            raise RteError(f"connection {key} -> {NVM_PREFIX}.{b.nvm}: {b.nvm} is the block of the operating time and "
                           f"reset statistics of the application (NVM-05, project.yaml nvm.blocks.{b.nvm}); choose "
                           f"another block name")
    blocks, ids, address, warnings = [], {}, 0, []
    qualities = {k[len(NVM_QLF_KIND) + 1:]: v for k, v in buffers.items() if k.startswith(f"{NVM_QLF_KIND}.")}
    for key, b in writers:
        comp_name, _, port_key = key.partition(".")
        comp = components[comp_name]
        tasks = sorted({r.task for r in comp.runnables if (r.period_ms or r.mode_events) and r.task is not None
                        and port_key in r.accesses},
                       key=lambda t: t.index)
        # same rule as the io task of a BSW buffer: the writing task with the highest priority, else the first task
        task = max(tasks, key=lambda t: t.os_priority) if tasks else app[0]
        size = b.dtype.byte_size
        if size > NVM_MAX_BLOCK_BYTES:
            raise RteError(f"connection {key} -> {NVM_PREFIX}.{b.nvm}: {size} bytes, a block holds at most "
                           f"{NVM_MAX_BLOCK_BYTES}")
        integrity = check_nvm_definition(b.nvm, key, b.dtype, nvm_definition(config, b.nvm), warnings)
        ident = nvm_block_id(b.nvm, b.dtype, asil=True) if integrity != "qm" else nvm_block_id(b.nvm, b.dtype)
        if ident in ids:
            raise RteError(f"connections: {NVM_PREFIX}.{b.nvm} and {NVM_PREFIX}.{ids[ident]} get the same record id "
                           f"0x{ident:04X} (CRC of name and type); rename one of them")
        ids[ident] = b.nvm
        readers = [k for k, v in port_buffer.items() if v == key and k != key]
        blocks.append(NvmBlock(b.nvm, b, key, readers, size, ident, task, address, len(blocks),
                               integrity=integrity, quality=qualities.get(b.nvm)))
        address += 2 * blocks[-1].slot_bytes
    if dem is not None:
        policy = "CHANGE" if dem.write_interval_ms else "SHUTDOWN"
        for name, size in zip(DEM_BLOCKS, (dem.events_bytes, dem.entries_bytes)):
            if size > NVM_MAX_BLOCK_BYTES:
                raise RteError(f"diag.yaml fault_memory: the block {name} needs {size} bytes, a block holds at most "
                               f"{NVM_MAX_BLOCK_BYTES}; store fewer entries or smaller snapshots")
            entry = nvm_definition(config, name)
            integrity = (entry.integrity if entry is not None else None) or "qm"
            ident = crc16_ccitt((f"{name}:{dem.signature()}" + (":asil" if integrity != "qm" else "")).encode())
            ident = ident ^ 0x5A5A if ident in (0x0000, 0xFFFF) else ident
            if ident in ids:
                raise RteError(f"connections: {NVM_PREFIX}.{ids[ident]} and the fault memory block {name} get the "
                               f"same record id 0x{ident:04X}; rename the block")
            ids[ident] = name
            blocks.append(NvmBlock(name, None, "the fault memory (diag.yaml)", [], size, ident, None, address,
                                   len(blocks), policy, dem.write_interval_ms, integrity))
            address += 2 * blocks[-1].slot_bytes
    if stats_entry is not None:
        # NVM-05: write policy and min_interval_ms come from the entry (place_nvm_blocks), SHUTDOWN without them
        integrity = stats_entry.integrity or "qm"
        ident = crc16_ccitt((f"{ECU_STATS_BLOCK}:{ECU_STATS_SIGNATURE}" + (":asil" if integrity != "qm" else ""))
                            .encode())
        ident = ident ^ 0x5A5A if ident in (0x0000, 0xFFFF) else ident
        if ident in ids:
            raise RteError(f"connections: {NVM_PREFIX}.{ids[ident]} and the block {ECU_STATS_BLOCK} get the same "
                           f"record id 0x{ident:04X}; rename the block")
        ids[ident] = ECU_STATS_BLOCK
        blocks.append(NvmBlock(ECU_STATS_BLOCK, None, "the ECU statistics (NVM-05)", [], ECU_STATS_BYTES, ident, None,
                               address, len(blocks), "SHUTDOWN", 0, integrity))
        address += 2 * blocks[-1].slot_bytes
    start, window, name = nvm_window(config)
    address = place_nvm_blocks(blocks, config, window) or address    # NVM-07: project.yaml nvm.blocks
    if window and address > window:
        raise RteError(f"connections: the NvM blocks need {address} bytes (two places each, with {NVM_RECORD_OVERHEAD} "
                       f"bytes of record for QM and {NVM_ASIL_OVERHEAD} for ASIL), the emulated EEPROM of the "
                       f"derivative ({name}) has {window}; store fewer or smaller blocks")
    return NvmPlan(blocks, address, max(bl.slot_bytes for bl in blocks), start, window, name, warnings)


def place_nvm_blocks(blocks: list, config, window: int) -> int:
    """NVM-07: fixed addresses and write policies of project.yaml nvm.blocks (checked by tools/nxconfig/nvm.py).

    Without entries the layout stays the name order above (0 is returned). With them, the fixed blocks keep their
    address and the others fill the gaps in name order (nxconfig.nvm.layout_blocks), so adding a block no longer
    moves a block that has a place; returns the bytes of the layout."""
    use = getattr(getattr(config, "project", None), "nvm", None) if config is not None else None
    if use is None or not use.blocks:
        return 0
    from nxconfig import nvm as nxnvm    # noqa: PLC0415
    by_name = {bl.name: bl for bl in blocks}
    for block_name, entry in use.blocks.items():
        bl = by_name.get(block_name)
        if bl is not None and entry.write is not None:
            bl.policy = entry.write.upper()
            bl.min_interval_ms = entry.min_interval_ms or 0
    fixed = {n: e.address for n, e in use.blocks.items() if e.address is not None and n in by_name}
    usable = use.partition.eeprom_bytes if use.partition is not None else window
    try:
        addresses = nxnvm.layout_blocks([(bl.name, 2 * bl.slot_bytes) for bl in blocks], fixed, usable)
    except ValueError as e:
        raise RteError(f"project.yaml {e}") from None
    for bl in blocks:
        bl.address = addresses[bl.name]
    return max(bl.address + 2 * bl.slot_bytes for bl in blocks)


def nvm_warnings(plan: NvmPlan | None) -> list[str]:
    """A block nobody reads back: the stored value never reaches a component. NVM-18: a block without definition."""
    if plan is None:
        return []
    return [f"connections: {NVM_PREFIX}.{b.name} is written by {b.writer} but no required port reads it, so the "
            f"stored value never reaches a component after a restart" for b in plan.blocks
            if not b.readers and b.buffer is not None] + list(plan.warnings)


def monitor_type(sig: "dbcgen.Signal") -> str:
    """Buffer type for a received signal without port: integer if possible, else float32."""
    if sig.integer:
        for native in ("uint32", "sint32"):
            lo, hi = TYPE_RANGE[native]
            if lo <= sig.minimum and sig.maximum <= hi:
                return native
    return "float32"


def finish_can(can: CanConfig, buffers: dict[str, Buffer]):
    base, task = can.task.period_ms, can.task.name
    for name in can.monitor:
        try:
            msg = can.dbc.message(name)
        except dbcgen.DbcError as e:
            raise RteError(f"can.monitor: {e}") from None
        if msg.problem:
            raise RteError(f"can.monitor: {msg.problem}")
        if not any(can.node in s.receivers for s in msg.signals):
            raise RteError(f"can.monitor: node {can.node} receives no signal of {name}")
        can.rx.setdefault(name, CanMessage(msg, "rx"))
    # qualifiers before the monitor buffers: entry.links then holds the port connections only, so a CRC or
    # counter signal connected to a port can be rejected
    finish_qualifiers(can, buffers)
    # received signals without a port stay readable in RTE buffers (console "rte signals")
    for entry in can.rx.values():
        connected = {link.signal.name for link in entry.links}
        for sig in entry.message.signals:
            if sig.name in connected or can.node not in sig.receivers:
                continue
            key = f"can.rx.{entry.message.name}.{sig.name}"
            native = monitor_type(sig)
            init = sig.to_physical(sig.initial_raw)
            init_text = repr(float(init)) + "F" if native == "float32" else str(int(init)) + ("U" if init >= 0 else "")
            buffers[key] = Buffer(f"Rte_Buf_CanRx_{entry.message.name}_{sig.name}", native, init_text,
                                  f"{entry.message.name}.{sig.name}", "can.rx", f"{entry.message.name}.{sig.name}",
                                  [key])
            entry.links.append(CanSignalLink(sig, buffers[key], "float" if native == "float32" else "int"))
    for entry in can.tx.values():
        m = entry.message
        if m.cycle_ms % base:
            raise RteError(f"can.tx.{m.name}: cycle time {m.cycle_ms} ms is no multiple of the period {base} ms "
                           f"of the CAN task {task} (can.task)")
        entry.ticks = m.cycle_ms // base
        # COM-38: event (0 ticks) and mixed messages, minimum gap in ticks of the CAN task
        problem = tx_delay_problem(m, base, task, can.dbc.path.name)
        if problem:
            raise RteError(f"{entry.where or 'can.tx.' + m.name}: {problem}")
        entry.delay_ticks = m.delay_ms // base
    for entry in can.rx.values():
        entry.ticks = math.ceil(entry.timeout_ms / base)
    ids = {}
    for m in can.messages:
        if m.can_id in ids and ids[m.can_id] != m.name:
            raise RteError(f"can: messages {ids[m.can_id]} and {m.name} share the CAN ID")
        ids[m.can_id] = m.name
    for entry in list(can.rx.values()) + list(can.tx.values()):
        entry.links.sort(key=lambda link: [s.name for s in entry.message.signals].index(link.signal.name))
    for i, entry in enumerate(can.rx.values()):
        entry.index = i
    for i, entry in enumerate(can.tx.values()):
        entry.index = i
    for i, entry in enumerate(e for e in can.tx.values() if e.on_change):
        entry.event_index = i                                           # COM-38: position in txEvents


def qlf_buffer(buffers: dict[str, Buffer], name: str) -> str:
    """RTE buffer of can.qlf.<Message>; every received message has one, connected or not."""
    key = f"{QLF_KIND}.{name}"
    if key not in buffers:
        buffers[key] = Buffer(f"Rte_Buf_CanQlf_{name}", "uint8", f"{QLF['DEBOUNCE']}U", f"{name}.Qlf",
                              QLF_KIND, name, [key])
    return key


def e2e_field(sig: "dbcgen.Signal", kind: str, bits: int, where: str) -> int:
    """AUTOSAR bit offset of a DBC signal that must be exactly one byte (CRC) or one nibble (counter, data id).

    Motorola signals name their most significant bit in the DBC, Intel signals their least significant one;
    both must end up inside a single byte, byte or nibble aligned.
    """
    if sig.length != bits:
        raise RteError(f"{where}: signal {sig.name} is {sig.length} bit wide, the E2E {kind} needs {bits} bit")
    if sig.signed or sig.factor != 1 or sig.offset != 0:
        raise RteError(f"{where}: signal {sig.name} must be unsigned with factor 1 and offset 0 "
                       f"to carry the E2E {kind} unchanged")
    byte, bit = divmod(sig.start, 8)
    low = bit - (bits - 1) if sig.motorola else bit
    if low < 0 or (low + bits) > 8:
        raise RteError(f"{where}: signal {sig.name} crosses a byte boundary, the E2E {kind} must lie in one byte")
    if bits != 8 and low not in (0, 4):
        raise RteError(f"{where}: signal {sig.name} is not nibble aligned (bit {low} of byte {byte}), "
                       f"the E2E {kind} must be the low (bit 0) or high (bit 4) nibble of a byte")
    return byte * 8 + (0 if bits == 8 else low)


def finish_e2e(e2e: E2eConfig, entry: CanMessage, can: CanConfig):
    """Derives the E2E field offsets from the DBC signals and checks layout, length and data id."""
    msg = entry.message
    where = e2e.where
    spec = E2E_PROFILES[e2e.profile]
    e2e.data_length = msg.length * 8
    if e2e.data_length < 16 or e2e.data_length > spec["max_bits"]:
        raise RteError(f"{where}: message {msg.name} is {msg.length} byte long, profile {e2e.profile} needs "
                       f"2..{spec['max_bits'] // 8} byte")
    fields = {"crc": (e2e.crc_signal, 8), "counter": (e2e.counter_signal, 4)}
    if e2e.nibble_signal is not None:
        fields["data id nibble"] = (e2e.nibble_signal, 4)
    if len({name for name, _ in fields.values()}) != len(fields):
        raise RteError(f"{where}: crc, counter and data_id_nibble must name different DBC signals")
    offsets = {}
    for kind, (name, bits) in fields.items():
        try:
            sig = msg.signal(name)
        except dbcgen.DbcError as e:
            raise RteError(f"{where}: {e}") from None
        if entry.direction == "rx" and can.node not in sig.receivers:
            raise RteError(f"{where}: node {can.node} is no receiver of {msg.name}.{name}, "
                           f"it cannot check the E2E {kind}")
        if any(link.signal.name == name for link in entry.links):
            raise RteError(f"{where}: {msg.name}.{name} carries the E2E {kind} and must not be connected to a port")
        offsets[kind] = e2e_field(sig, kind, bits, where)
    e2e.crc_offset, e2e.counter_offset = offsets["crc"], offsets["counter"]
    e2e.nibble_offset = offsets.get("data id nibble", 0)
    # E2E needs the CRC in a byte of its own and counter and data id in different nibbles (e2e_layoutValid).
    # The DBC loader rejects overlapping signals first, so this only catches layouts it accepts.
    for a, b in (("counter", "crc"), ("data id nibble", "crc"), ("data id nibble", "counter")):
        if a in offsets and b in offsets and (
                (offsets[a] // 8 == offsets[b] // 8) if b == "crc" else (offsets[a] == offsets[b])):
            raise RteError(f"{where}: {fields[a][0]} and {fields[b][0]} of {msg.name} overlap, the E2E "
                           f"{a} and {b} need their own byte or nibble")
    header = spec["header"]
    if header is not None:
        expected = {"crc": header["crc"], "counter": header["counter"]}
        for kind, offset in expected.items():
            if offsets[kind] != offset:
                raise RteError(f"{where}: profile {e2e.profile} has a fixed header, the {kind} must be at bit "
                               f"offset {offset} (byte {offset // 8}"
                               f"{', low nibble' if kind == 'counter' else ''}), "
                               f"{fields[kind][0]} is at {offsets[kind]}")


def finish_qualifiers(can: CanConfig, buffers: dict[str, Buffer]):
    """Assigns can.qualifiers entries to their messages, checks them against the DBC, adds the default ones."""
    for name, qlf in can.qualifiers.items():
        entry = can.rx.get(name) or can.tx.get(name)
        if entry is None:
            try:
                msg = can.dbc.message(name)
            except dbcgen.DbcError as e:
                raise RteError(f"{qlf.where}: {e}") from None
            raise RteError(f"{qlf.where}: message {name} is neither sent nor received by the RTE "
                           f"(no connected signal, not in can.monitor, no can.qlf.{msg.name} connection)")
        if entry.direction == "tx":
            rx_only = [key for key, unset in (("timeout_ms", qlf.timeout_ms is None),
                                              ("debounce_ms", qlf.debounce_ms == 0),
                                              ("on_error", qlf.on_error == "hold")) if not unset]
            if rx_only:
                raise RteError(f"{qlf.where}.{rx_only[0]}: {name} is sent by this ECU, "
                               f"a sent message only takes e2e (E2E protection)")
            if qlf.e2e is None:
                raise RteError(f"{qlf.where}: {name} is sent by this ECU, only e2e (E2E protection) applies")
        entry.qlf = qlf
    for name, entry in can.rx.items():
        if entry.qlf is None:
            # no qualifiers entry: bus error and the DBC timeout only, values hold (Q5 default)
            entry.qlf = Qualifier(name, f"can.qualifiers.{name}")
        entry.qlf_buffer = buffers[qlf_buffer(buffers, name)]
    for entry in list(can.rx.values()) + list(can.tx.values()):
        if entry.e2e is not None:
            finish_e2e(entry.e2e, entry, can)


def _int(value, where: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RteError(f"{where}: expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise RteError(f"{where}: {value} is below the minimum {minimum}")
    return value


def _keys(entry: dict, allowed: set, where: str):
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise RteError(f"{where}.{unknown[0]}: unknown key (allowed: {', '.join(sorted(allowed))})")


def _task_name(value, where: str) -> str:
    if not isinstance(value, str) or not TASK_NAME_RE.match(value):
        raise RteError(f"{where}: task name {value!r} must be lower case letters and digits, starting with a letter")
    if value in RESERVED_TASK_NAMES:
        raise RteError(f"{where}: task name {value} is reserved (OpenBSW idle and timer task)")
    return value


def load_task_manifest(path: Path) -> dict:
    """BSW tasks, ISR groups and the default application task (ecu/asyncCoreConfiguration/tasks.yaml)."""
    source = path.as_posix()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RteError(f"{source}: expected a mapping")
    manifest = {
        "path": path,
        "tick_us": _int(data.get("tick_us"), f"{source}: tick_us", 1),
        "max_priorities": _int(data.get("max_priorities"), f"{source}: max_priorities", 3),
        "min_stack_bytes": _int(data.get("min_stack_bytes", 0), f"{source}: min_stack_bytes", 0),
        "tasks": [],
        "isr_groups": [],
    }
    names, priorities = set(), set()
    for i, entry in enumerate(data.get("tasks") or []):
        where = f"{source}: tasks[{i}]"
        if not isinstance(entry, dict):
            raise RteError(f"{where}: expected a mapping with name and priority")
        _keys(entry, {"name", "priority", "above_application"}, where)
        name = _task_name(entry.get("name"), f"{where}.name")
        priority = _int(entry.get("priority"), f"{where}.priority", 1)
        above = entry.get("above_application", False)
        if not isinstance(above, bool):
            raise RteError(f"{where}.above_application: expected true or false")
        if name in names or priority in priorities:
            raise RteError(f"{where}: task name or priority used twice")
        names.add(name)
        priorities.add(priority)
        manifest["tasks"].append({"name": name, "priority": priority, "above_application": above, "where": where})
    default = data.get("application_default")
    if not isinstance(default, dict):
        raise RteError(f"{source}: application_default: expected name, priority and stack_bytes")
    _keys(default, {"name", "priority", "stack_bytes"}, f"{source}: application_default")
    manifest["default"] = {
        "name": _task_name(default.get("name"), f"{source}: application_default.name"),
        "priority": _int(default.get("priority"), f"{source}: application_default.priority", 1),
        "stack_bytes": _int(default.get("stack_bytes"), f"{source}: application_default.stack_bytes",
                            manifest["min_stack_bytes"]),
    }
    for i, group in enumerate(data.get("isr_groups") or []):
        if not isinstance(group, str) or not re.match(r"^[a-z][a-z0-9_]*$", group):
            raise RteError(f"{source}: isr_groups[{i}]: {group!r} is no valid name")
        manifest["isr_groups"].append(group)
    return manifest


def load_bsw_task_plan(config: "nxconfig.Config | None", manifest: dict, repo: Path,
                       stacks_manifest: Path | None = None) -> "nxbswtasks.BswTaskPlan":
    """PLAT-26: stacks of the BSW, idle and timer task objects and the merged BSW tasks (run_in).

    The values are project values (project.yaml bsw_tasks), checked by tools/nxconfig with file, line and YAML path;
    this function resolves them against the BSW tasks of *this* task manifest (a test profile has other names) and
    the stack manifest (default: <repo>/ecu/configuration/taskStacks.yaml; tasks it does not list take its
    default). The first error raises; the warnings are tools/nxconfig's.
    """
    path = stacks_manifest or (repo / TASK_STACKS_MANIFEST)
    if stacks_manifest is None and not path.is_file():
        path = HERE.parents[1] / TASK_STACKS_MANIFEST     # a repo folder with components only: this tree's manifest
    try:
        stacks = nxbswtasks.load_stack_manifest(path)
    except nxbswtasks.StackManifestError as e:
        raise RteError(str(e)) from None
    values = config.project.bsw_tasks if config is not None else {}

    def error(where, text):
        raise RteError(f"project.yaml {'.'.join(str(p) for p in where)}: {text}")

    return nxbswtasks.resolve([(t["name"], t["above_application"]) for t in manifest["tasks"]], stacks, values,
                              error, lambda where, text: None)


def render_bsw_tasks(plan: "nxbswtasks.BswTaskPlan", source: str) -> str:
    """PLAT-26: tasks/BswTasks.h - stacks of the task objects of ecu/application/src/app/app.cpp.

    C (included through app/appConfig.h, also by C++ code of the family port): NX_BSW_TASK_STACK_<TASK> in bytes
    for every task with a task object of its own, NX_BSW_TASK_OWN_<TASK> 1/0 for every BSW task (0: merged, its
    context is an alias in async/Config.h), and the sum of all these stacks.
    """
    stacks = plan.manifest.name if plan.manifest is not None else "taskStacks.yaml"
    lines = [f"/* Generated by tools/rtegen from project.yaml bsw_tasks and {stacks} (PLAT-26) for {source} "
             f"- do not edit. */",
             "/* Stacks of the BSW task objects of ecu/application/src/app/app.cpp and of the OpenBSW idle and timer",
             " * task, in bytes. NX_BSW_TASK_OWN_<TASK> 0: the task has no FreeRTOS task of its own and runs in",
             " * another one (project.yaml bsw_tasks.<task>.run_in); its context is an alias in async/Config.h. */",
             "", "#pragma once", ""]
    for t in plan.tasks:
        macro = t.name.upper()
        if t.own:
            default = " (default)" if t.stack_bytes == t.default_bytes else f" (default {t.default_bytes})"
            lines.append(f"#define NX_BSW_TASK_STACK_{macro} {t.stack_bytes}U /* {t.name}{default} */")
        if not t.os_task:
            note = "" if t.own else f" /* runs in {t.run_in} */"
            lines.append(f"#define NX_BSW_TASK_OWN_{macro} {1 if t.own else 0}{note}")
    lines += ["", "/* sum of the stacks above (without the task control blocks) */",
              f"#define NX_BSW_TASK_STACK_BYTES {plan.stack_bytes}U"]
    return "\n".join(lines) + "\n"


def build_tasks(ecu, components: dict[str, Component], manifest: dict, can: CanConfig | None,
                merged: dict[str, str] | None = None):
    """Application tasks of the ECU description (section tasks:) between the BSW tasks, runnables assigned.

    `merged` (PLAT-26): BSW tasks without a task of their own -> the BSW task they run in; they get no FreeRTOS
    priority, their names stay taken.

    Returns (application tasks in configuration order, all tasks in FreeRTOS priority order).
    """
    apply_enabled(ecu, components)                            # RTE-23: components[].enabled, disabled_values
    merged = merged or {}
    bsw = [Task(e["name"], e["priority"], e["where"], bsw=True, above_application=e["above_application"])
           for e in manifest["tasks"] if e["name"] not in merged]
    by_name = {t.name: t for t in bsw}
    for e in manifest["tasks"]:
        if e["name"] in merged:
            by_name[e["name"]] = Task(e["name"], e["priority"], e["where"], bsw=True)
    runnables = {f"{c.name}.{r.name}": (c, r) for c in components.values() for r in c.runnables}
    # RTE-23: a runnable of a disabled component runs in no task (the period of the one task ignores it too)
    periodic = [k for k, (c, r) in runnables.items() if r.period_ms and c.enabled]
    moded = [k for k, (c, r) in runnables.items() if r.mode_events and c.enabled]   # RTE-08: mode switch runnables

    ecu = ecu_description(ecu, "tasks", "base_period_ms")     # schema: keys, names, value ranges (ARCH-34)
    given = ecu.tasks
    if given is None:
        # one application task as before RTE-02: period is base_period_ms or the gcd of the runnable periods
        d = manifest["default"]
        periods = [runnables[k][1].period_ms for k in periodic]
        period = ecu.base_period_ms if ecu.base_period_ms is not None else (reduce(gcd, periods) if periods else 10)
        given = [nxmodel.AppTask(name=d["name"], period_ms=period, priority=d["priority"],
                                 stack_bytes=d["stack_bytes"])]

    app = []
    for i, entry in enumerate(given):
        where = f"tasks[{i}]"
        name = entry.name
        if name in by_name:
            other = by_name[name]
            raise RteError(f"{where}.name: {name} is already used by {'BSW task' if other.bsw else 'task'} "
                           f"({other.where})")
        period = entry.period_ms
        if (period * 1000) % manifest["tick_us"]:
            raise RteError(f"{where}.period_ms: {period} ms is no multiple of the OS tick {manifest['tick_us']} us")
        priority = entry.priority
        stack = _int(entry.stack_bytes, f"{where}.stack_bytes", manifest["min_stack_bytes"])
        task = Task(name, priority, where, period, stack, index=i)
        by_name[name] = task
        app.append(task)

    used = {}
    for task in bsw + app:
        other = used.get(task.priority)
        if other is not None:
            raise RteError(f"{task.where}.priority: {task.priority} is already used by "
                           f"{'BSW task' if other.bsw else 'task'} {other.name} ({other.where}); "
                           f"OpenBSW runs every task on its own FreeRTOS priority")
        used[task.priority] = task
    for task in app:
        for b in bsw:
            if b.above_application and task.priority > b.priority:
                raise RteError(f"{task.where}.priority: {task.priority} is above BSW task {b.name} ({b.priority}), "
                               f"which must keep the highest priority ({b.where})")
    ordered = sorted(bsw + app, key=lambda t: t.priority)
    if len(ordered) + 2 > manifest["max_priorities"]:
        raise RteError(f"tasks: {len(ordered)} tasks plus idle and timer need more than "
                       f"{manifest['max_priorities']} FreeRTOS priorities")
    for number, task in enumerate(ordered, start=1):
        task.os_priority = number

    assigned = {}

    def assign(task, key, offset, where):
        comp, r = runnables[key]
        if r.period_ms % task.period_ms:
            raise RteError(f"{where}: period {r.period_ms} ms of {key} is no multiple of the period "
                           f"{task.period_ms} ms of task {task.name}")
        if offset % task.period_ms:
            raise RteError(f"{where}.offset_ms: {offset} ms is no multiple of the period {task.period_ms} ms "
                           f"of task {task.name}")
        if offset >= r.period_ms:
            raise RteError(f"{where}.offset_ms: {offset} ms must be below the period {r.period_ms} ms of {key}")
        r.task, r.offset_ms = task, offset
        task.runnables.append((comp, r))
        assigned[key] = task

    def assign_mode(task, key, offset, where):
        # RTE-08: started by mode switches of the ECU mode manager at the start of a cycle of its task
        comp, r = runnables[key]
        if offset:
            raise RteError(f"{where}.offset_ms: {key} is started by a mode switch, not by a period; it has no "
                           f"offset")
        r.task = task
        task.mode_runnables.append((comp, r))
        assigned[key] = task

    listed = any("runnables" in e.model_fields_set for e in given)
    for task, entry in zip(app, given):
        if entry.runnables is None:
            continue
        for j, item in enumerate(entry.runnables):
            where = f"{task.where}.runnables[{j}]"
            key, offset = item.runnable, item.offset_ms
            if key not in runnables:
                raise RteError(f"{where}: unknown runnable {key} (known: {', '.join(runnables)})")
            if runnables[key][1].init:
                raise RteError(f"{where}: {key} is an init runnable, it runs once at lifecycle init without task")
            if key in assigned:
                raise RteError(f"{where}: {key} is already assigned to task {assigned[key].name}")
            comp, r = runnables[key]
            if not item.enabled:
                # RTE-23: disabled runnable - keeps its place in the list, is not called
                if comp.safety != "qm":
                    raise disable_safety_error(f"{where}.enabled", f"runnable {key}", comp)
                if comp.enabled:
                    r.enabled, r.disabled_where = False, f"{where}.enabled"
            if not r.enabled:
                assigned[key] = task          # listed once: a second entry is still an error
                continue
            if runnables[key][1].mode_events:
                assign_mode(task, key, offset, where)
            else:
                assign(task, key, offset, where)
    if not listed and len(app) == 1:
        for key in periodic:
            assign(app[0], key, 0, "tasks[0]" if ecu.tasks is not None else "base_period_ms")
        for key in moded:
            assign_mode(app[0], key, 0, "tasks[0]")
    for key in periodic + moded:
        if key not in assigned and runnables[key][1].enabled:
            raise RteError(f"tasks: runnable {key} is not assigned to a task (tasks[].runnables)")
    for task in app:
        task.hyper_ticks = reduce(lambda a, b: a * b // gcd(a, b),
                                  [r.period_ms // task.period_ms for _, r in task.runnables], 1)

    if can is not None:
        if can.task_name is None:
            if len(app) != 1:
                raise RteError(f"can.task: required with several application tasks "
                               f"({', '.join(t.name for t in app)})")
            can.task = app[0]
        else:
            can.task = next((t for t in app if t.name == can.task_name), None)
            if can.task is None:
                raise RteError(f"can.task: {can.task_name} is no application task "
                               f"(tasks: {', '.join(t.name for t in app)})")
    return app, ordered


class _InputKinds:
    """BSW kinds whose buffer the RTE fills in the task of its port: the fixed kinds and the <prefix>.<value> kinds of
    the device values (RTE-18, from the module manifests - known once a project's board_signals were built)."""

    def __init__(self, fixed: tuple[str, ...]):
        self.fixed = fixed

    def __contains__(self, kind) -> bool:
        return kind in self.fixed or kind in _DEVICE_KINDS

    def __iter__(self):
        return iter(self.fixed + tuple(sorted(_DEVICE_KINDS - set(self.fixed))))


IO_INPUT_KINDS = _InputKinds(("dio.in", "adc.mv", "adc.raw", "adc.phys", "adc.qlf", "ecu.state", "ecu.wakeup",
                              "ecu.wakeup.") + BSW_SERVICE_KINDS + PINEVENT_KINDS + tuple(SAFETY_KINDS))
IO_OUTPUT_KINDS = ("dio.out", "pwm", SAFETY_SAFE_STATE)   # SYS-141: the requested safe state


def plan_tasks(components: dict[str, Component], buffers: dict[str, Buffer], port_buffer: dict[str, str],
               app: list[Task], can: CanConfig | None):
    """Decides per buffer whether tasks share it and how every accessor reaches it.

    Returns (port access: Component.Port.Element -> (C name, locked), task of the ecu.sleep_request transfer).
    """
    accessors = {b.name: [] for b in buffers.values()}  # (task or None, spans several tasks, "r"/"w")
    port_tasks = {}
    for comp in components.values():
        for key, port in comp.ports.items():
            full = f"{comp.name}.{key}"
            # RTE-08: mode switch runnables run in their task like the periodic ones
            tasks = sorted({r.task for r in comp.runnables if (r.period_ms or r.mode_events)
                            and r.task is not None and key in r.accesses},
                           key=lambda t: t.index)
            port_tasks[full] = tasks
            accessors[buffers[port_buffer[full]].name].append(
                (tasks[0] if len(tasks) == 1 else None, len(tasks) > 1, "w" if port.direction == "out" else "r"))

    def io_task(key):
        # task of the port; a port in several tasks: the one with the highest priority; unused port: first task
        tasks = port_tasks.get(key) or []
        return max(tasks, key=lambda t: t.os_priority) if tasks else app[0]

    for key, b in buffers.items():
        is_input = b.bsw in IO_INPUT_KINDS or b.device is not None
        if is_input or b.bsw in IO_OUTPUT_KINDS:
            b.io_task = io_task(key)
            accessors[b.name].append((b.io_task, False, "w" if is_input else "r"))
        elif b.age is not None:
            # SYS-32: the age qualifier is written at the end of each cycle of the writer's task
            b.io_task = b.age.task
            accessors[b.name].append((b.io_task, False, "w"))
    if can is not None:
        for entry in can.rx.values():
            for link in entry.links:
                accessors[link.buffer.name].append((can.task, False, "w"))
            if entry.qlf_buffer is not None:
                accessors[entry.qlf_buffer.name].append((can.task, False, "w"))
        for entry in can.tx.values():
            for link in entry.links:
                accessors[link.buffer.name].append((can.task, False, "r"))
    sleep_task = None
    for key, b in buffers.items():
        if b.sleep_request:
            sleep_task = sleep_task or io_task(key)
            accessors[b.name].append((sleep_task, False, "r"))

    for b in buffers.values():
        acc = accessors[b.name]
        named = sorted({t for t, _, _ in acc if t is not None}, key=lambda t: t.index)
        b.shared = any(span for _, span, _ in acc) or len(named) > 1
        b.copies = {}
        if b.shared:
            for t in named:
                b.copies[t.name] = "w" if any(tt is t and mode == "w" for tt, _, mode in acc) else "r"

    port_access = {}
    for full, tasks in port_tasks.items():
        b = buffers[port_buffer[full]]
        if not b.shared:
            port_access[full] = (b.name, False)
        elif len(tasks) == 1:
            port_access[full] = (b.ref(tasks[0]), False)
        else:
            port_access[full] = (b.name, True)  # several tasks or init only: published value under the lock
    for b in buffers.values():
        if b.age is not None and port_access.get(b.age.writer, ("", False))[1]:
            raise RteError(f"connections: {AGE_KIND}.{b.age.writer}: {b.age.writer} is written from several tasks "
                           f"(periodic and mode switch runnables); the age is counted in the task of its one writer")
    # One update of the capture driver serves all its channels (bsp/capture/InputCapture.h update()), so all
    # capture values have to be read in one task; two tasks would poll the same hardware against each other.
    capture = [b for b in buffers.values() if b.device is not None and b.device.access == "input_capture"]
    capture_tasks = {b.io_task for b in capture}
    if len(capture_tasks) > 1:
        names = ", ".join(sorted(t.name for t in capture_tasks))
        raise RteError(f"connections: the {capture[0].device.prefix} values are read in several tasks ({names}), "
                       f"but one update of "
                       f"the measurement serves every channel: read them in one task (the task of a value is "
                       f"the task of the runnables that use its port)")
    for t in app:
        t.copy_in = [b for b in buffers.values() if b.shared and b.copies.get(t.name) == "r"]
        t.copy_out = [b for b in buffers.values() if b.shared and b.copies.get(t.name) == "w"]
    return port_access, sleep_task


@dataclass(eq=False)
class ImplicitCopy:
    """RTE-09: the copy of one implicitly accessed data element for one runnable (Rte_Imp_<Swc>_<Runnable>_<P>_<E>).

    Loaded before the runnable from `source` - what an explicit access of the port in that task would use: the task
    copy, the buffer of a single-task port, or the published value of a port whose runnables run in several tasks
    (`locked`: under the lock). A write copy is published back to `source` after the runnable."""
    component: Component
    runnable: Runnable
    key: str                 # Port.Element
    port: Port
    mode: str                # "r": Rte_IRead, "w": Rte_IWrite / Rte_IWriteRef
    buffer: Buffer
    source: str              # C name of the value the copy is exchanged with
    locked: bool

    @property
    def api(self) -> str:
        """<Swc>_<Runnable>_<Port>_<Element>: suffix of the RTE functions (the component calls them without <Swc>)."""
        return f"{self.component.name}_{self.runnable.name}_{self.port.name}_{self.port.element}"

    @property
    def local_api(self) -> str:
        return f"{self.runnable.name}_{self.port.name}_{self.port.element}"

    @property
    def name(self) -> str:
        return f"Rte_Imp_{self.api}"

    @property
    def label(self) -> str:
        return f"{self.component.name}.{self.runnable.name}: {self.key}"

    @property
    def byte_size(self) -> int:
        return self.port.type.byte_size

    def load(self) -> str:
        """C++ statement: copy <- source (before the runnable; write copies too, see ImplicitRunnable)."""
        if self.port.type.category == "ARRAY":
            return f"(void)std::memcpy(&{self.name}, &{self.source}, sizeof({self.name}));"
        if self.port.type.category == "STRUCTURE":
            return f"{self.name} = {self.source};"
        return f"{self.name} = static_cast<{self.port.type.name}>({self.source});"

    def store(self) -> str:
        """C++ statement: source <- copy (after the runnable, write copies only)."""
        if self.port.type.category == "ARRAY":
            return f"(void)std::memcpy(&{self.source}, &{self.name}, sizeof({self.source}));"
        if self.port.type.category == "STRUCTURE":
            return f"{self.source} = {self.name};"
        return f"{self.source} = static_cast<{self.buffer.ctype}>({self.name});"


@dataclass(eq=False)
class ImplicitRunnable:
    """RTE-09: a runnable with implicit access and the wrapper the RTE calls instead of its entry point."""
    component: Component
    runnable: Runnable
    copies: list                   # ImplicitCopy, reads and writes in the order of the component's ports

    @property
    def name(self) -> str:
        return f"{self.component.name}.{self.runnable.name}"

    @property
    def wrapper(self) -> str:
        return f"Rte_Implicit_{self.component.name}_{self.runnable.name}"

    @property
    def reads(self) -> list:
        return [c for c in self.copies if c.mode == "r"]

    @property
    def writes(self) -> list:
        return [c for c in self.copies if c.mode == "w"]

    @property
    def lock_before(self) -> bool:
        """One lock section before the runnable if any copy comes from a published value (never one per copy)."""
        return any(c.locked for c in self.copies)

    @property
    def lock_after(self) -> bool:
        return any(c.locked for c in self.writes)

    @property
    def byte_size(self) -> int:
        return sum(c.byte_size for c in self.copies)


def plan_implicit(components: dict[str, Component], buffers: dict[str, Buffer], port_buffer: dict[str, str],
                  port_access: dict) -> list[ImplicitRunnable]:
    """RTE-09: one copy per runnable and implicitly accessed data element, exchanged with what an explicit access
    of the port uses (plan_tasks: task copy, buffer, or published value under the lock). The task copies and their
    locks at the task boundary stay as they are: a copy loaded from a task copy needs no lock of its own."""
    plan = []
    for comp in components.values():
        for runnable in comp.runnables:
            if not runnable.implicit:
                continue
            copies = []
            for key in comp.ports:                     # port order of the ARXML: stable generated code
                mode = runnable.implicit.get(key)
                if mode is None:
                    continue
                full = f"{comp.name}.{key}"
                source, locked = port_access[full]
                copies.append(ImplicitCopy(comp, runnable, key, comp.ports[key], mode, buffers[port_buffer[full]],
                                           source, locked))
            plan.append(ImplicitRunnable(comp, runnable, copies))
    return plan


def implicit_warnings(plan: list[ImplicitRunnable]) -> list[str]:
    """RTE-09: an implicit write copy starts with the value found before the runnable and is always published. If
    runnables of several tasks write the element, one that does not write can publish an old value over a newer one
    of the other task."""
    out = []
    for ir in plan:
        for c in ir.writes:
            tasks = {r.task.name for r in c.component.runnables if c.key in r.accesses and r.task is not None}
            if c.locked and len(tasks) > 1:
                out.append(f"{c.label}: implicit write of a port whose runnables run in several tasks - a runnable "
                           f"that does not write it still publishes the value it found at its start and may "
                           f"overwrite a newer one of the other task; write it from one task")
    return out


@dataclass(eq=False)
class ModeLink:
    """A mode port connected to the ECU mode manager ecu.mode (RTE-08)."""
    component: Component
    port: ModePort
    values: list          # Rte_Mode value per ecustate::State number (index = ECU_MODES value)
    names: list           # mode name of the port's group per ecustate::State number
    task: "Task | None" = None  # task whose view Rte_Mode returns; None: the mode manager is read directly

    @property
    def full(self) -> str:
        return f"{self.component.name}.{self.port.key}"

    @property
    def symbol(self) -> str:
        """Rte_Mode_<Component>_<Port>_<Group> (the component calls Rte_Mode_<Port>_<Group>)."""
        return f"Rte_Mode_{self.component.name}_{self.port.name}_{self.port.group}"

    @property
    def macros(self) -> list:
        """RTE_MODE_<Group>_<Mode> per ecustate::State number, for the mapping table."""
        return [f"RTE_MODE_{self.port.decl.name}_{name}" for name in self.names]


def build_modes(ecu, components: dict[str, Component]) -> dict[str, ModeLink]:
    """Connects the mode ports (RTE-08): each to the ECU mode manager ecu.mode, with a mode declaration group that
    names exactly the ECU modes. Returns Component.Port.Group -> ModeLink; empty without mode ports."""
    connections = ecu_description(ecu, "connections").connections
    links: dict[str, ModeLink] = {}
    known = ", ".join(ECU_MODES)
    for src, dst in connections.items():
        comp_name, _, key = src.partition(".")
        comp = components.get(comp_name)
        if comp is None or key not in comp.mode_ports:
            continue
        port = comp.mode_ports[key]
        if isinstance(dst, list) or dst != ECU_MODE_TARGET:
            raise RteError(f"connection {src}: a mode port is connected to a mode manager, the ECU has one: "
                           f"{ECU_MODE_TARGET} (got {dst})")
        decl = port.decl
        upper: dict[str, str] = {}
        for mode in decl.modes:
            if mode.upper() in upper:
                raise RteError(f"connection {src} -> {dst}: modes {upper[mode.upper()]} and {mode} of the mode "
                               f"declaration group {decl.name} differ only in case")
            upper[mode.upper()] = mode
        missing = [m for m in ECU_MODES if m not in upper]
        unknown = [m for m in decl.modes if m.upper() not in ECU_MODES]
        if missing or unknown:
            detail = "; ".join(part for part in (f"missing {', '.join(missing)}" if missing else "",
                                                 f"unknown {', '.join(unknown)}" if unknown else "") if part)
            raise RteError(f"connection {src} -> {dst}: the mode declaration group {decl.name} must have exactly "
                           f"the modes of {ECU_MODE_TARGET} ({known}, case does not matter): {detail}")
        order = sorted(ECU_MODES, key=ECU_MODES.get)
        names = [upper[m] for m in order]
        links[src] = ModeLink(comp, port, [decl.modes[n] for n in names], names)
    for comp in components.values():
        for key in comp.mode_ports:
            if f"{comp.name}.{key}" not in links:
                raise RteError(f"required port {comp.name}.{key} is not connected (a mode port: connect it to "
                               f"{ECU_MODE_TARGET})")
    seen: dict[str, ModeLink] = {}
    for link in links.values():
        other = seen.setdefault(link.port.decl.name, link)
        if other.port.decl.signature() != link.port.decl.signature():
            raise RteError(f"connection {link.full}: the mode declaration group {link.port.decl.name} differs from "
                           f"the one of {other.full} (modes, values or transition value); Rte_Type.h has one set "
                           f"of RTE_MODE_{link.port.decl.name}_* per name")
    return links


def plan_modes(components: dict[str, Component], links: dict[str, ModeLink], app: list[Task]) -> list[Task]:
    """Decides per mode port what Rte_Mode returns (RTE-08) and returns the tasks that keep a view of the ECU mode.

    Like the data ports of RTE-02: a mode port read by runnables of one task returns that task's view, which
    changes only at its cycle start (together with the mode switch runnables of the task). A port read in several
    tasks (or by no task runnable) reads the mode manager directly: every call is consistent for itself.
    """
    for link in links.values():
        tasks = sorted({r.task for r in link.component.runnables
                        if (r.period_ms or r.mode_events) and r.task is not None
                        and link.port.key in r.mode_accesses}, key=lambda t: t.index)
        link.task = tasks[0] if len(tasks) == 1 else None
    return [t for t in app if t.mode_runnables or any(link.task is t for link in links.values())]


def mode_warnings(components: dict[str, Component]) -> list[str]:
    """Mode switch events on modes the application tasks cannot observe (RTE-08)."""
    found = []
    for comp in components.values():
        for r in comp.runnables:
            for ev in r.mode_events:
                unseen = [m for m in ev.modes if m.upper() in ECU_MODES_UNSEEN]
                if unseen:
                    found.append(f"components: {comp.name}.{r.name} runs {ev.activation} "
                                 f"{' -> '.join(ev.modes)}, but the application tasks stop when the lifecycle goes "
                                 f"down: {' and '.join(ECU_MODES_UNSEEN)} are seen for at most one cycle or never. "
                                 f"Last actions belong to the entry of RUNDOWN")
    return found


@dataclass(eq=False)
class ModeRunnableRow:
    """One row of rte::MODE_RUNNABLES (RTE-17): a mode switch event of a mode switch runnable in its task.

    A runnable with two events (e.g. on exit RUN and on entry STANDBY) has two rows: it runs once per event, and
    the console and tools/schedana show each event with its own statistics."""
    index: int
    task: Task
    component: "Component"
    runnable: "Runnable"
    event: ModeEvent
    mode: str                  # ECU mode (upper case): the mode of ON-ENTRY/ON-EXIT, the target of ON-TRANSITION
    from_mode: str | None      # ON-TRANSITION: the mode it leaves

    @property
    def name(self) -> str:
        return f"{self.component.name}.{self.runnable.name}"

    @property
    def activation(self) -> str:
        """Enumerator of rte::ModeActivation."""
        return self.event.activation.replace("-", "_")


def mode_runnable_table(app: list[Task]) -> list[ModeRunnableRow]:
    """The rows of rte::MODE_RUNNABLES (RTE-17), grouped by task in the order of rte::TASKS, inside a task in the
    order of tasks[].runnables and of the events of each runnable. Empty without mode switch runnables.

    A table of its own, not rows of rte::RUNNABLES: RteSystem calls every row behind the periodic ones as an init
    runnable (RTE-08). The RTE calls a row through runModeRunnable(index), so the runner (RteSystem) can measure
    it; the console ("rte runnables", "rte json") and tools/schedana name the event of every row."""
    rows: list[ModeRunnableRow] = []
    for task in sorted(app, key=lambda t: t.index):
        for comp, r in task.mode_runnables:
            for ev in r.mode_events:
                modes = [m.upper() for m in ev.modes]
                rows.append(ModeRunnableRow(len(rows), task, comp, r, ev, modes[-1],
                                            modes[0] if len(modes) == 2 else None))
    return rows


def mode_switch_code(task: Task, links: dict[str, ModeLink], rows: list[ModeRunnableRow]) -> str:
    """C++ of the mode switch of one task in readInputs_<task> (RTE-08); empty if the task keeps no view.

    Every mode switch runnable is called through runModeRunnable(<row of rte::MODE_RUNNABLES>) (RTE-17)."""
    view = f"Rte_EcuMode_{task.name}"
    exits: dict[int, list] = {}
    entries: dict[int, list] = {}
    transitions: list = []
    row_of = {id(row.event): row.index for row in rows if row.task is task}
    for comp, r in task.mode_runnables:
        for ev in r.mode_events:
            numbers = [ECU_MODES[m.upper()] for m in ev.modes]
            call = (f"runModeRunnable({row_of[id(ev)]}U);", f"{comp.name}.{r.name}")
            if ev.activation == "ON-EXIT":
                exits.setdefault(numbers[0], []).append(call)
            elif ev.activation == "ON-ENTRY":
                entries.setdefault(numbers[0], []).append(call)
            else:
                transitions.append((numbers[0], numbers[1], call))
    name_of = {v: k for k, v in ECU_MODES.items()}
    initial = ECU_MODES[ECU_MODE_INITIAL]
    lines = [
        "    {",
        f"        // ECU mode as seen by this task (RTE-08, mode manager {ECU_MODE_TARGET}). A change since the last "
        "cycle start",
        "        // runs the mode switch runnables of this task before its periodic runnables: on exit of the old "
        "mode, on",
        "        // transition, on entry of the new mode. Meanwhile Rte_Mode of a port read in this task returns "
        "RTE_TRANSITION_*.",
    ]
    if initial in entries:
        lines += [
            f"        static bool modeStarted = false; // the first cycle enters the initial mode {ECU_MODE_INITIAL}",
        ]
    lines += [
        "        uint8_t mode = 0U;",
        "        ::ecustate::rte::readState(mode);",
    ]
    if initial in entries:
        lines += [
            "        if (!modeStarted)",
            "        {",
            "            modeStarted = true;",
            f"            {view} = RTE_ECU_MODE_TRANSITION;",
            *[f"            {c} // {label}" for c, label in entries[initial]],
            f"            {view} = {initial}U; // {ECU_MODE_INITIAL}",
            "        }",
        ]
    lines += [
        f"        if (mode != {view})",
        "        {",
    ]
    if exits or transitions:
        lines.append(f"            uint8_t const previous = {view};")
    lines.append(f"            {view} = RTE_ECU_MODE_TRANSITION;")

    def switch(variable, calls, what):
        out = [f"            switch ({variable}) // {what}", "            {"]
        for number in sorted(calls):
            out.append(f"                case {number}U: // {name_of[number]}")
            out += [f"                    {c} // {label}" for c, label in calls[number]]
            out.append("                    break;")
        out += ["                default: break;", "            }"]
        return out

    if exits:
        lines += switch("previous", exits, "on exit")
    for old, new, (c, label) in transitions:
        lines += [f"            if ((previous == {old}U) && (mode == {new}U)) // {name_of[old]} -> {name_of[new]}",
                  "            {",
                  f"                {c} // {label}",
                  "            }"]
    if entries:
        lines += switch("mode", entries, "on entry")
    lines += [
        f"            {view} = mode;",
        "        }",
        "    }",
    ]
    return "\n".join(lines) + "\n"


def model_changes(swc_dir: Path, name: str) -> list | None:
    """Inputs of the component (model, <Swc>_init.m, init_data/, libraries, referenced models) that differ from the
    record of swc_build.m (TOOL-169, tools/swc/modelhash.py). None if nothing to compare."""
    if not (swc_dir / f"{name}.slx").exists():
        return None
    return modelhash.model_changes(swc_dir, name)


def model_is_current(swc_dir: Path, name: str) -> bool | None:
    """Compares the inputs of the model with the hashes recorded by swc_build.m. None if nothing to compare."""
    changes = model_changes(swc_dir, name)
    return None if changes is None else not changes


def model_warning(name: str, changes: list) -> str:
    """Warning of a component whose generated code is older than its model inputs, naming the files (TOOL-169)."""
    def files(kind):
        return [c.path for c in changes if c.kind == kind]

    def verb(paths, one, many):
        return one if len(paths) == 1 else many

    clauses = []
    changed, new, missing = files(modelhash.CHANGED), files(modelhash.NEW), files(modelhash.MISSING)
    if changed:
        clauses.append(f"{', '.join(changed)} {verb(changed, 'differs', 'differ')} from the model the generated "
                       f"code was built from")
    if new:
        clauses.append(f"{', '.join(new)} {verb(new, 'is', 'are')} new since the generated code was built")
    if missing:
        clauses.append(f"{', '.join(missing)} {verb(missing, 'is', 'are')} missing (input of the generated code)")
    first = (changed + new + missing)[0]
    owner = "" if first.startswith(name) else f"{name}: "
    return f"components: {owner}{'; '.join(clauses)}, run tools/swc/swc_build.m"


# Top level sections of the ECU description; only they can start a YAML path of a message (see _yaml_path)
ECU_SECTIONS = tuple(nxmodel.EcuDescription.model_fields)


def _yaml_path(where: str, sections=ECU_SECTIONS) -> tuple:
    """YAML path of a `where` prefix of an RteError: 'tasks[0].name' -> ('tasks', 0, 'name').

    Returns () for a text that is no path of the ECU description (the message itself names the place then);
    `sections` None: any path (the diagnostic description, COM-32).
    """
    parts: list = []
    for token in (where or "").split("."):
        name = re.match(r"^\w+", token)
        if name is None:
            return ()
        parts.append(name.group(0))
        parts += [int(i) for i in re.findall(r"\[(\d+)\]", token)]
    return tuple(parts) if parts and (sections is None or parts[0] in sections) else ()


def rte_message(text: str, ecu_file: Path, tree=None, severity: str = "error",
                diag: tuple | None = None) -> "nxconfig.Message":
    """One finding of the RTE generator as a message with file, YAML path and line.

    The generator reports a place as a prefix before the first colon: 'connection <Component.Port.Element>',
    'required port <...>' or a path of the ECU description like 'can.qualifiers.<Message>.e2e'. That prefix
    becomes the YAML path, so the Studio can show the message at the line that causes it (TOOL-07).
    """
    where = text.split(":", 1)[0]
    path: tuple = ()
    if diag is not None and where.startswith("diag.yaml "):
        # COM-32: a finding in the diagnostic description ('diag.yaml data_identifiers[0].data[1].source.bsw (..)')
        # belongs to that file, with its YAML path and line
        diag_file, diag_tree = diag
        path = _yaml_path(where[len("diag.yaml "):].split(" ", 1)[0], sections=None)
        line = nxconfig.config.line_of(diag_tree, path) if diag_tree is not None and path else None
        return nxconfig.Message(severity, text, diag_file, path, line)
    match = re.match(r"^(?:connection|required port) (\S+)", where)
    if match:
        path = ("connections", match.group(1))
    else:
        path = _yaml_path(where)
    line = nxconfig.config.line_of(tree, path) if tree is not None else None
    if line is None and tree is not None and path:
        line = nxconfig.config.line_of(tree, path[:1])
    return nxconfig.Message(severity, text, ecu_file, path, line)


def validate(project_file: Path, repo: Path | None = None, mcudb: Path | None = None,
             tasks_manifest: Path | None = None,
             running_tree: Path | None = None) -> list["nxconfig.Message"]:
    """Everything an RTE run would complain about, as messages - without writing a single file (TOOL-07).

    Checks the project with tools/nxconfig, then builds the RTE model of the ECU description in memory with
    build_model() - the model phase of generate() itself (TOOL-113): components (ARXML), CAN (DBC, qualifiers,
    E2E), ECU state parameters, tasks, connections, the task plan, XCP, receive filters, NvM, modes and every
    warning. Only the template rendering and the writing are left out (RENDER_STEPS). Every message carries
    severity, text, file, line and YAML path. Never raises for a configuration problem.

    A generator error stops the model build, so at most one such message is reported per run (the configuration
    messages of tools/nxconfig are always complete). test_validate_matches_generate (test_rtegen.py) guards that
    generate() checks nothing this function does not: per faulty profile both report the same, and generate()
    may call nothing but build_model() and the render steps.

    TOOL-87 - `running_tree`: which neXtBSW tree this call runs as, handed down to `nxconfig.check()` (the rule
    is described there, TOOL-85). Default None keeps the behaviour of every call that exists today: the tree
    this package lies in, so a workspace pin that names another tree stays refused on the command line. The
    Studio switches the tree per opened project (TOOL-61) and says so here; without this parameter it had to
    move the module variable `nxconfig.config.REPO` under a lock (studio/core/tree.py `as_tool_of`, TOOL-60).

    `repo` (the tree the task manifest is read from) and `mcudb` follow the running tree when they are not
    given - which is the tree this package lies in whenever `running_tree` is not given, so no call of today
    reads anywhere else than before.
    """
    repo = Path(repo) if repo is not None else nxconfig.config.tree_of(running_tree)
    config, messages = nxconfig.check(Path(project_file).resolve(),
                                      mcudb or nxconfig.config.mcudb_dir(running_tree),
                                      running_tree=running_tree)
    messages = list(messages)
    if config is None:
        return messages
    if config.rte_file is None:
        tree = config.sources["project"].tree
        path = ("project", "rte")
        messages.append(nxconfig.Message("error", "project.rte (ECU description) is not set, no RTE is generated",
                                         config.project_file, path, nxconfig.config.line_of(tree, path)))
        return messages
    ecu_file = config.rte_file
    # ARCH-34: nxconfig.check() has read the ECU description and checked it against the schema (messages above)
    ecu = config.rte
    source = config.sources.get(nxconfig.config.RTE_LEVEL)
    tree = source.tree if source is not None else None
    if ecu is None:
        ecu, found, tree = nxconfig.config.read_rte(ecu_file)
        if ecu is None:
            return messages + found
    # COM-32: findings in the diagnostic description ('diag.yaml <path>: ...') carry its file, path and line
    diag_source = config.sources.get(nxconfig.config.DIAG_LEVEL) if config.diag_file is not None else None
    diag = (config.diag_file, diag_source.tree if diag_source is not None else None) \
        if config.diag_file is not None else None
    def warn(finding):
        # TOOL-113: the warnings of the model phase, as they come - a later error keeps the ones before it
        messages.append(finding if isinstance(finding, nxconfig.Message)
                        else rte_message(finding, ecu_file, tree, "warning", diag))

    try:
        # TOOL-113: the same model phase as generate() - one function, so a new check cannot miss this path
        build_model(ecu, ecu_file, config, repo, tasks_manifest, warn=warn)
    except SwcDescriptionError as e:
        # RTE-20: findings in the swc.yaml of a C component keep their own file, YAML path and line
        messages += e.messages
    except (RteError, dbcgen.DbcError, OSError, ET.ParseError, KeyError, AttributeError, TypeError, ValueError) as e:
        # TypeError/ValueError: a malformed ECU description (wrong node type) must not crash the Studio either
        messages.append(rte_message(str(e) if isinstance(e, RteError) else f"{type(e).__name__}: {e}",
                                    ecu_file, tree, diag=diag))
    return messages


def generate_project(project_file: Path, out_dir: Path, repo: Path, mcudb: Path | None = None):
    """Loads the project (tools/nxconfig) and generates the RTE of its ECU description (project.rte)."""
    try:
        config = nxconfig.load(project_file, mcudb or nxconfig.MCUDB)
    except nxconfig.ConfigErrors as e:
        raise RteError(str(e)) from None
    if config.rte_file is None:
        raise RteError(f"{project_file}: project.rte (ECU description) is not set")
    return generate(config.rte_file, config, out_dir, repo)


def diag_of(config, ecu_file: Path, diag=DIAG_TARGET):
    """Diagnostic description the RTE serves (COM-28).

    Default: the project's diag.yaml, but only for the project's own ECU description - a test that renders another
    rte.yaml against a project gets none, because the description names ports of the project's components.
    """
    if diag is not DIAG_TARGET:
        return diag
    if config is None or getattr(config, "diag", None) is None or config.rte_file is None:
        return None
    return config.diag if Path(ecu_file).resolve() == Path(config.rte_file).resolve() else None


@dataclass
class RteModel:
    """Result of the model phase (build_model, TOOL-113): everything generate() renders, nothing written yet."""
    components: dict
    source_dirs: list            # Embedded Coder output of the Simulink components
    c_source_dirs: list          # RTE-20: hand-written C components, built as application code
    c_inputs: list               # their descriptions: a change configures again
    type_definitions: str
    buses: list
    can: "CanConfig | None"      # None also when a DBC is configured but nothing is connected
    uds_bus: str | None
    ecu_state_values: dict
    ecu_state_verify: bool
    manifest: dict
    app_tasks: list
    all_tasks: list
    signals: dict
    buffers: dict
    port_buffer: dict
    diag_desc: object
    diag_plan: "DiagPlan | None"
    xcp: "XcpCan | None"
    rx_splits: dict
    rx_filters: dict
    port_access: dict
    sleep_task: object
    dem: "DemPlan | None"
    nvm: "NvmPlan | None"
    implicit: list
    mode_links: dict
    mode_views: list
    mode_rows: list
    rendered: dict = field(default_factory=dict)   # RTE-24: text of the generated files (render_sources)
    dataset: "DatasetUse | None" = None            # TOOL-145: calibration dataset the values come from
    bsw_plan: "nxbswtasks.BswTaskPlan | None" = None   # PLAT-26: stacks and merges of the BSW task objects


def _print_warning(finding):
    """Default of build_model(warn=...): the generator's own output on stderr, as before TOOL-113."""
    if isinstance(finding, nxconfig.Message):
        print(f"rtegen: {finding}", file=sys.stderr)
    else:
        print(f"rtegen: warning: {finding}", file=sys.stderr)


def build_model(ecu, ecu_file: Path, config: "nxconfig.Config", repo: Path, tasks_manifest: Path | None = None,
                diag=DIAG_TARGET, warn=_print_warning, calibration=PROJECT_DATASET) -> RteModel:
    """The model phase of the RTE generator: every check and every warning, no file (TOOL-113).

    generate() and validate() both call this one function, so a check added here reaches the Studio check
    ("Konfiguration prüfen", `studio validate --like-generate`) without a second place to keep in step. Before
    TOOL-113 validate() copied the steps and missed load_xcp, the receive filters, the mode runnable table and the
    NvM warnings. Configuration errors raise (RteError and the exceptions validate() turns into messages);
    warnings go to `warn` as they are found - a text of the generator (validate() adds file and YAML path) or an
    nxconfig.Message of a component description (RTE-20). RTE-24: the text of the generated files is rendered
    here as well (render_sources), because the identifier check (check_identifiers, TOOL-134) needs it. What
    generate() does afterwards is rte_components.cmake and writing only (RENDER_STEPS);
    test_validate_matches_generate keeps it that way.
    """
    components = {}
    source_dirs = []
    c_source_dirs = []
    c_inputs = []
    for entry in ecu.components:              # at least one, names unique (schema)
        swc_dir = path_of(ecu, ecu_file, repo, entry.path)    # RTE-22: relative to the rte.yaml
        loaded = load_component(entry.name, swc_dir, repo)     # ARXML or, RTE-20, swc.yaml of a C component
        comp = loaded.component
        components[comp.name] = comp
        for m in loaded.warnings:
            warn(m)
        if loaded.implementation == "c":
            c_source_dirs.append(loaded.source_dir.as_posix())
            c_inputs += [f.as_posix() for f in (c_component_file(swc_dir), data_file(swc_dir, comp.name))
                         if f.is_file()]
            continue
        source_dirs.append(loaded.source_dir.as_posix())
        changes = model_changes(swc_dir, comp.name)
        if changes:
            warn(model_warning(comp.name, changes))
    check_runnable_symbols(components)
    check_measurement_symbols(components)          # TOOL-143
    # TOOL-145: values of the project's calibration dataset instead of the <Swc>_data.m defaults, checked here
    dataset = use_calibration_dataset(components, dataset_of(config, ecu_file, calibration))
    type_definitions = render_type_definitions(collect_types(components))

    buses = can_buses(config)
    can = load_can(ecu, repo, buses, ecu_file)
    uds_bus = load_uds(ecu, can, buses)
    ecu_state_values, ecu_state_verify = load_ecu_state(ecu, config)
    ecu_state_wakeup(ecu_state_values, config)   # TOOL-113: the wake-up names, before render_ecu_state_config
    manifest = load_task_manifest(tasks_manifest or (repo / TASKS_MANIFEST))
    bsw_plan = load_bsw_task_plan(config, manifest, repo)                                          # PLAT-26
    app_tasks, all_tasks = build_tasks(ecu, components, manifest, can,
                                       {t.name: t.run_in for t in bsw_plan.tasks if not t.own})
    signals = board_signals(config)
    buffers, port_buffer = build_rte(ecu, components, signals, can, wakeup_sources(config))
    diag_desc = diag_of(config, ecu_file, diag)
    diag_plan = plan_diag(diag_desc, components, buffers, signals, wakeup_sources(config))   # COM-28, COM-32
    # COM-08: after build_rte, which decides the received messages (can.rx)
    xcp = load_xcp(config, buses, uds_bus)  # TOOL-26
    rx_splits = can_rx_splits(config, buses)  # COM-26: receive buffers of each bus from its instance
    rx_filters = {bus: can_rx_filters(ids, *rx_splits[bus])
                  for bus, ids in can_rx_ids(can, uds_bus, config, buses, xcp).items()}
    if can is not None and not (can.rx or can.tx):
        can = None  # DBC configured but nothing connected: no CAN code
    port_access, sleep_task = plan_tasks(components, buffers, port_buffer, app_tasks, can)
    dem = plan_dem(diag_desc, components, buffers, port_buffer, buses if config is not None else None)  # NVM-03
    nvm = plan_nvm(components, buffers, port_buffer, app_tasks, config, dem)   # NVM-02, NVM-03
    check_ecu_state_step_error(ecu_state_values, nvm, config)                  # SYS-174: no continue past NvM
    implicit = plan_implicit(components, buffers, port_buffer, port_access)   # RTE-09
    mode_links = build_modes(ecu, components)                     # RTE-08
    mode_views = plan_modes(components, mode_links, app_tasks)
    mode_rows = mode_runnable_table(app_tasks)                     # RTE-17
    for text in (init_value_warnings(components, buffers)                          # RTE-27
                 + disabled_warnings(components, buffers, port_buffer)                # RTE-23
                 + capture_warnings(buffers) + hall_warnings(buffers) + pinevent_warnings(buffers)
                 + gd_warnings(buffers) + device_warnings(buffers, ("reading",))            # RTE-18
                 + adc_warnings(buffers, signals) + bsw_service_warnings(buffers) + mode_warnings(components)
                 + implicit_warnings(implicit) + (diag_plan.warnings if diag_plan else []) + nvm_warnings(nvm)
                 + (dem.warnings if dem else [])):
        warn(text)
    model = RteModel(components, source_dirs, c_source_dirs, c_inputs, type_definitions, buses, can, uds_bus,
                     ecu_state_values, ecu_state_verify, manifest, app_tasks, all_tasks, signals, buffers,
                     port_buffer, diag_desc, diag_plan, xcp, rx_splits, rx_filters, port_access, sleep_task, dem,
                     nvm, implicit, mode_links, mode_views, mode_rows)
    # RTE-24: the generated identifiers belong to the model check - TOOL-134 ran check_identifiers() only in
    # generate(), so the Studio check passed what the build then refused
    model.dataset = dataset
    model.bsw_plan = bsw_plan                                                                      # PLAT-26
    model.rendered = render_sources(model, ecu_file, config)
    check_identifiers(model.rendered)
    return model


def render_sources(model: RteModel, ecu_file: Path, config: "nxconfig.Config") -> dict[str, str]:
    """Text of every generated file but rte_components.cmake, which names the output folder (RTE-24).

    Part of the model phase: build_model() renders them and runs check_identifiers() on them, so validate()
    (Studio "Konfiguration prüfen") refuses the same identifier clash generate() refuses (TOOL-134 checked it
    only while writing). Refuses nothing itself besides what the render steps refuse.
    """
    components = model.components
    type_definitions, can, uds_bus, manifest = model.type_definitions, model.can, model.uds_bus, model.manifest
    ecu_state_values, ecu_state_verify = model.ecu_state_values, model.ecu_state_verify
    app_tasks, all_tasks, buffers, port_buffer = model.app_tasks, model.all_tasks, model.buffers, model.port_buffer
    checks = parameter_checks(components)                                                 # SYS-90
    diag_desc, diag_plan, xcp, rx_splits, rx_filters = (model.diag_desc, model.diag_plan, model.xcp,
                                                        model.rx_splits, model.rx_filters)
    port_access, sleep_task, dem, nvm, implicit = (model.port_access, model.sleep_task, model.dem, model.nvm,
                                                   model.implicit)
    mode_links, mode_views, mode_rows = model.mode_links, model.mode_views, model.mode_rows
    # RTE-18: device values by the access code of their module (module.yaml rte)
    def device_inputs(access: str) -> list[Buffer]:
        return [b for b in buffers.values() if b.device is not None and b.device.access == access]

    def device_fields(inputs: list[Buffer]) -> dict[str, str]:
        return {b.bsw: b.device.field_of(b.bsw) for b in inputs}

    capture_inputs = device_inputs("input_capture")
    hall_inputs = device_inputs("hall")
    gd_inputs = device_inputs("gate_driver")
    reading_sources = []
    for source in dict.fromkeys(b.device for b in device_inputs("reading")):
        inputs = [b for b in buffers.values() if b.device is source]
        reading_sources.append({
            "module": source.module, "prefix": source.prefix, "include": source.include, "reading": source.reading,
            "read": source.read, "instance": source.instance, "qualifier": source.qualifier, "inputs": inputs,
            "fields": device_fields(inputs),
            "instances": list(dict.fromkeys(b.bsw_signal for b in inputs)),
            # one reading per instance and task: the reader takes its own consistent snapshot, like the gate
            # drivers, so every task reads the instances it needs itself
            "reads": {task.name: list(dict.fromkeys(b.bsw_signal for b in inputs if b.io_task is task))
                      for task in app_tasks},
        })
    # IO-54: pin events. The project decides them (pin_events), so the RTE starts and cycles them even without a
    # connection; the cycle runs in the first task that reads one, otherwise in the first application task.
    pinevent_inputs = [b for b in buffers.values() if (b.bsw or "").startswith(f"{PINEVENT_PREFIX}.")]
    project_pin_events = bool(getattr(config, "pin_events", None))
    reading = [t for t in app_tasks if any(b.io_task is t for b in pinevent_inputs)]
    pinevent_cycle_task = (reading[0] if reading else (app_tasks[0] if project_pin_events and app_tasks else None))
    # SYS-29: bus id per device for the supervisor's bus verdict on its qualifier (None: no bus verdict)
    gd_bus = {device: (None if bus == DEVICE_NO_BUS else f"0x{bus:02X}U")
              for device, bus in device_bus_ids(config).items()}
    sensor_inputs = [b for b in buffers.values() if b.bsw in ADC_SENSOR_KINDS]
    bsw_inputs = [b for b in buffers.values() if b.bsw in BSW_SERVICE_KINDS]
    age_buffers = [b for b in buffers.values() if b.age is not None]
    for i, b in enumerate(age_buffers):
        b.age.index = i

    env = Environment(loader=FileSystemLoader(HERE / "templates"), undefined=StrictUndefined,
                      keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)
    ctx = {
        "can": can,
        "can_rx": list(can.rx.values()) if can else [],
        "can_tx": list(can.tx.values()) if can else [],
        "cpp_types": CTYPE_CPP,
        "source": ecu_file.name,
        "components": list(components.values()),
        "buffers": list(buffers.values()),
        "port_buffer": {k: buffers[v] for k, v in port_buffer.items()},
        "port_access": port_access,
        "tasks": app_tasks,
        "all_tasks": all_tasks,
        "manifest": manifest,
        "manifest_name": manifest["path"].name,
        # PLAT-26: BSW tasks without a task of their own, (context, context of the task they run in)
        "task_aliases": [(f"TASK_{t.name.upper()}", f"TASK_{t.run_in.upper()}")
                         for t in (model.bsw_plan.tasks if model.bsw_plan else ()) if not t.own],
        "init_runnables": [(c, r) for c in components.values() for r in c.runnables if r.init and r.enabled],
        "can_task": can.task if can else None,
        "sleep_task": sleep_task,
        "shared_buffers": [b for b in buffers.values() if b.shared],
        "locked_ports": {k for k, (_, locked) in port_access.items() if locked},
        "type_ids": PLATFORM_TYPES,
        "pwm_max_duty": PWM_MAX_DUTY,
        "qlf": QLF,
        "e2e_profiles": can.e2e_profiles if can else [],
        "e2e_rx": [e for e in (can.rx.values() if can else []) if e.e2e is not None],
        "e2e_tx": [e for e in (can.tx.values() if can else []) if e.e2e is not None],
        "tx_events": [e for e in (can.tx.values() if can else []) if e.on_change],   # COM-38
        "ecu_inputs": [b for b in buffers.values() if (b.bsw or "").startswith("ecu.")],
        "safety_inputs": [b for b in buffers.values() if b.bsw in SAFETY_DEGRADED_TARGETS],     # SYS-117
        "hw_shutoff_inputs": [b for b in buffers.values() if b.bsw in SAFETY_HW_SHUTOFF_TARGETS],  # SYS-136
        # SYS-141: the selection of the safe state (ports) and the start test of the short (derived)
        "safe_state_inputs": [b for b in buffers.values()
                              if b.bsw in (SAFETY_SAFE_STATE_ACTIVE, SAFETY_SAFE_STATE_QLF)],
        "safe_state_outputs": [b for b in buffers.values() if b.bsw == SAFETY_SAFE_STATE],
        "safe_state_groups": safe_state_groups(config),
        "safe_state_tests": [(g, SAFE_STATE_NUMBERS[s]) for g, s in
                             safe_state_tests(safe_state_groups(config))],
        # SYS-143: the gate-driver readers of the start tests (E3) and the budget of their holds (E2)
        "safe_state_test_readers": list(dict.fromkeys(g.gate_driver for g, _ in
                                                      safe_state_tests(safe_state_groups(config)) if g.gate_driver)),
        "safe_state_test_budget_us": safe_state_test_budget(config, safe_state_tests(safe_state_groups(config))),
        "safe_state_test_total_us": sum(g.test_us for g, _ in safe_state_tests(safe_state_groups(config))),
        "safe_state_watchdog_module": SAFE_STATE_WATCHDOG_MODULE,
        "capture_inputs": capture_inputs,
        "capture_task": capture_inputs[0].io_task if capture_inputs else None,
        "capture_channels": list(dict.fromkeys(b.bsw_signal for b in capture_inputs)),
        "capture_fields": device_fields(capture_inputs),
        "hall_inputs": hall_inputs,
        # Per task: the sensors it reads. One reading holds every value, and get() needs no cycle and no lock
        # from the RTE, so two tasks may read the same sensor (IO-07).
        "hall_reads": {task.name: list(dict.fromkeys(b.bsw_signal for b in hall_inputs if b.io_task is task))
                       for task in app_tasks},
        "hall_fields": device_fields(hall_inputs),
        # IO-25: gate drivers; the reader of the integration is interrupt and task safe (single words), so every
        # task reads the devices it needs itself
        # IO-54: pin events - per task the inputs it reads (one read() holds all values of an input), the task that
        # cycles them, and whether the RTE starts the pin interrupts of the port (Hall sensors read or pin events)
        "pinevent_inputs": pinevent_inputs,
        "pinevent_reads": {task.name: list(dict.fromkeys(b.bsw_signal for b in pinevent_inputs
                                                         if b.io_task is task))
                           for task in app_tasks},
        "pinevent_fields": {kind: PINEVENT_VALUES[kind.split(".")[1]]["field"] for kind in PINEVENT_KINDS},
        "pinevent_cycle_task": pinevent_cycle_task,
        "pin_events_project": project_pin_events,
        "gd_inputs": gd_inputs,
        "gd_devices": list(dict.fromkeys(b.bsw_signal for b in gd_inputs)),
        "gd_reads": {task.name: list(dict.fromkeys(b.bsw_signal for b in gd_inputs if b.io_task is task))
                     for task in app_tasks},
        "gd_fields": device_fields(gd_inputs),
        # RTE-18: modules with the generic reader contract (access reading of module.yaml rte)
        "reading_sources": reading_sources,
        "gd_health": GD_HEALTH,
        # SYS-29: bus id per gate driver for the supervisor's bus verdict on its qualifier (None: no bus verdict)
        "gd_bus": gd_bus,
        "gd_bus_verdict": any(gd_bus.get(device) for device in dict.fromkeys(b.bsw_signal for b in gd_inputs)),
        # SYS-32: age supervision of provided ports (age.qlf.*): the qualifier buffers with their supervision, and
        # the flag Rte_Write sets per supervised writer
        "age_buffers": age_buffers,
        "age_flags": {b.age.writer: b.age.flag for b in age_buffers},
        # IO-18: per task the ADC channels whose physical value or quality it reads; one conversion per channel
        # serves both, so value and quality always belong together
        "sensor_inputs": sensor_inputs,
        "sensor_reads": {task.name: [(channel, any(b.bsw == "adc.phys" and b.bsw_signal == channel and b.io_task is task
                                                   for b in sensor_inputs))
                                     for channel in dict.fromkeys(b.bsw_signal for b in sensor_inputs
                                                                  if b.io_task is task)]
                         for task in app_tasks},
        "sleep_requests": [b for b in buffers.values() if b.sleep_request],
        # RTE-05: BSW service ports. Per task the services it reads (one call per service gives all its values);
        # the hand-over is a single word per value, so every task reads the services it needs itself.
        "bsw_inputs": bsw_inputs,
        "bsw_services": {name: BSW_SERVICES[name] for name in BSW_SERVICES
                         if any(b.bsw_signal == name for b in bsw_inputs)},
        "bsw_reads": {task.name: [name for name in BSW_SERVICES
                                  if any(b.bsw_signal == name and b.io_task is task for b in bsw_inputs)]
                      for task in app_tasks},
        "bsw_expr": {b.name: bsw_service_expr(b.bsw, b.ctype) for b in bsw_inputs},
        # RTE-04: named implementation types (typedefs, enumerations, arrays, records), dependencies first
        "type_definitions": type_definitions,
        # arrays are copied with memcpy (string.h in Rte.c, cstring in RteConfig.cpp)
        "array_ports": any(p.type.category == "ARRAY" for c in components.values() for p in c.ports.values()),
        # NVM-02: non-volatile blocks (rte::NVM_BLOCKS), images and hand-over to the NvM; None without nvm.* targets
        "nvm": nvm,
        # RTE-03: calibration parameters in RAM, values from <Swc>_data.m
        "parameters": [p for c in components.values() for p in c.parameters.values()],
        # RTE-26: records of curves/maps with own axes, stored limits, the extra fields of a rte::PARAMETERS row
        "cal_records": calibration_records(components),
        "parameter_extra": parameter_row_extras(components),
        # SYS-90: plausibility checks of the safety-relevant parameters (rte::PARAMETER_CHECKS), only if there is one
        "parameter_checks": checks,
        "check_rules": CHECK_RULES,
        # TOOL-143: measurements (rows of rte::SIGNALS); those of C components are defined by the RTE
        "measurements": [m for c in components.values() for m in c.measurements],
        # RTE-29: conversion and unit of the fixed point rows of rte::SIGNALS (empty: no table, layout as before)
        "signal_scalings": signal_scalings(list(buffers.values()),
                                           [m for c in components.values() for m in c.measurements]),
        "c_literal": c_literal,
        # RTE-08: mode ports on the ECU mode manager; per task the view of the ECU mode and its mode switch code
        "mode_links": list(mode_links.values()),
        "mode_links_of": {name: [link for link in mode_links.values() if link.component is comp]
                          for name, comp in components.items()},
        "mode_groups": list({link.port.decl.name: link.port.decl for link in mode_links.values()}.values()),
        "mode_views": mode_views,
        "mode_live": [link for link in mode_links.values() if link.task is None],
        "mode_switch": {task.name: mode_switch_code(task, mode_links, mode_rows) for task in mode_views},
        # RTE-17: rte::MODE_RUNNABLES, one row per mode switch event, called through runModeRunnable(index)
        "mode_rows": mode_rows,
        "ecu_mode_count": len(ECU_MODES),
        "ecu_mode_initial": ECU_MODES[ECU_MODE_INITIAL],
        "transition_view": MODE_TRANSITION_VIEW,
        # RTE-09: implicit access, a copy per runnable and element; the RTE calls such a runnable through a wrapper
        "implicit": implicit,
        "implicit_of": {name: [c for ir in implicit if ir.component is comp for c in ir.copies]
                        for name, comp in components.items()},
        "implicit_call": {ir.name: ir.wrapper for ir in implicit},
        # COM-28: rte::diag functions for the diagnostic jobs (RteDiag.h)
        "diag_code": render_diag_code(diag_plan),
        # COM-32: BSW hand-overs the rte::diag functions call, and whether they convert ADC sensor values
        "diag_decls": diag_plan.declarations() if diag_plan else "",
        "diag_sensors": bool(diag_plan) and diag_plan.sensors,
        # NVM-03: the fault memory - sources (rte::demSample ...) and its NvM blocks; None without DTCs
        "dem": dem,
        "dem_code": render_dem_code(dem),
        "dem_nvm": {blk.name: blk.index for blk in (nvm.blocks if nvm else []) if blk.buffer is None},
        "dem_uses_state": bool(dem) and any("readState" in code for _, _, code, _ in dem.conditions),
        "dem_uses_power": bool(dem) and dem.uses_power,                    # IO-59/NVM-08: bsw.power samples
    }
    files = {"Rte_Type.h": "Rte_Type.h.j2", "Rte_Internal.h": "Rte_Internal.h.j2", "Rte.c": "Rte.c.j2",
             "RteConfig.h": "RteConfig.h.j2", "RteConfig.cpp": "RteConfig.cpp.j2", "RteTasks.h": "RteTasks.h.j2",
             "tasks/async/Config.h": "Config.h.j2"}
    rendered = {name: env.get_template(t).render(ctx) for name, t in files.items()}
    for comp in components.values():
        rendered[f"Rte_{comp.name}.h"] = env.get_template("Rte_Component.h.j2").render(ctx, component=comp)
    rendered[CALIBRATION_FILE] = render_calibration_description(components, ecu_file.name,           # RTE-26
                                                                model.dataset)                     # TOOL-145
    if checks:                                                                                     # SYS-90
        rendered[PLAUSIBILITY_FILE] = render_parameter_checks(checks, components, ecu_file.name, model.dataset)
    if model.bsw_plan is not None:
        rendered[BSW_TASKS_FILE] = render_bsw_tasks(model.bsw_plan, ecu_file.name)                   # PLAT-26
    rendered["EcuStateConfig.h"] = render_ecu_state_config(ecu_state_values, ecu_state_verify,
                                                           ecu_state_manifest(config), ecu_file.name, config)
    rendered["uds/udsBusConfiguration.h"] = render_uds_bus_config(uds_bus, ecu_file.name)
    rendered["can/canRxFilterConfiguration.h"] = render_can_rx_filter_config(rx_filters, ecu_file.name, rx_splits)
    rendered["xcp/xcpCanConfiguration.h"] = render_xcp_config(xcp, ecu_file.name)
    # COM-28: declarations for ecu/uds, from the same module as the calls tools/diaggen writes
    rendered["RteDiag.h"] = rteapi.render_header(diag_desc or nxmodel.DiagDescription(schema_version=1),
                                                 ecu_file.name, "tools/rtegen")
    if dem is not None:
        rendered["RteDem.h"] = render_dem_header(dem, ecu_file.name)       # NVM-03
    rendered[DEM_REPORTERS_FILE] = render_dem_reporters(dem, ecu_file.name)   # COM-46/NVM-08
    rendered["Rte_Qlf.h"] = render_qlf_header()
    rendered["Rte_Qlf.m"] = render_qlf_matlab()
    if can is not None:
        rendered.update(dbcgen.render(can.messages, can.dbc.path.name))
    return rendered


# TOOL-113: what generate() may call besides load_ecu() and build_model() - rendering, no check. A function that
# can refuse a configuration belongs into build_model(), or validate() does not see it. test_validate_matches_generate
# reads generate() and fails for a call that is neither; for a function of this module listed here it also fails if
# the function (or what it calls) raises RteError without build_model() reaching the same function first.
RENDER_STEPS = frozenset({
    "Environment", "FileSystemLoader", "device_bus_id", "device_bus_ids", "bsw_service_expr", "mode_switch_code",
    "render_diag_code", "render_dem_code", "render_dem_header", "render_dem_reporters", "render_ecu_state_config",
    "ecu_state_manifest",
    "render_uds_bus_config", "render_can_rx_filter_config", "render_xcp_config", "render_qlf_header",
    "render_qlf_matlab", "rteapi.render_header", "nxmodel.DiagDescription", "dbcgen.render",
    # PLAT-79: identity manifest of the run - hashes the inputs and the written files, refuses nothing
    "nxidentity.of", "nxidentity.manifest_path", "Path",
    # PLAT-120: licence line of the generated files
    "genlicense.stamp_all", "genlicense.stamp",
})


def generate(ecu_file: Path, config: "nxconfig.Config", out_dir: Path, repo: Path, tasks_manifest: Path | None = None,
             diag=DIAG_TARGET, calibration=PROJECT_DATASET):
    """Generates the RTE; tasks_manifest defaults to <repo>/ecu/asyncCoreConfiguration/tasks.yaml.

    `diag`: diagnostic description (model.DiagDescription) whose jobs reach component values through the RTE
    (COM-28); default: the project's (diag_of). Returns (components, buffers, application tasks).

    TOOL-113: the model phase is build_model() - the same function validate() runs; what follows only renders.
    """
    ecu = load_ecu(ecu_file)                  # schema of tools/nxconfig (ARCH-34), all findings with YAML path
    model = build_model(ecu, ecu_file, config, repo, tasks_manifest, diag, calibration=calibration)
    rendered = dict(model.rendered)
    components, source_dirs, c_source_dirs, c_inputs = (model.components, model.source_dirs, model.c_source_dirs,
                                                        model.c_inputs)
    can = model.can
    can_sources = [(out_dir / "CanDb.c").as_posix()] if can is not None else []
    rendered["rte_components.cmake"] = (
        f"# Generated by tools/rtegen from {ecu_file.name} - do not edit.\n"
        "set(RTE_COMPONENT_SOURCE_DIRS\n" + "".join(f'    "{d}"\n' for d in source_dirs) + ")\n"
        "set(RTE_CAN_SOURCES\n" + "".join(f'    "{s}"\n' for s in can_sources) + ")\n"
        f"set(RTE_USES_E2E {'TRUE' if can is not None and can.e2e_profiles else 'FALSE'})\n"
        # COM-29: the RTE only has rte::setApplicationTxEnabled() if this ECU sends application messages.
        # A diagnostic stack (ecu/uds) calls it on CommunicationControl (UDS 0x28) and compiles that call
        # only when this is TRUE, so a project without sent messages needs no stub and no weak symbol.
        f"set(RTE_APPLICATION_TX_GATE {'TRUE' if can is not None and can.tx else 'FALSE'})\n"
    )
    if c_source_dirs:
        # RTE-20: hand-written C components (swc.yaml implementation: c). ecu/application builds <path>/src/*.c as
        # application code (warnings are errors, MISRA scope), not with the Embedded Coder output of ecu/rte.
        # Only written when there is one, so the file of a project without C components stays as it was.
        inputs = c_inputs + [(HERE.parent / "swc" / f).as_posix()
                             for f in ("swc_yaml.py", "swc_skeleton.py", "mdata.py")]
        rendered["rte_components.cmake"] += (
            "set(RTE_C_COMPONENT_SOURCE_DIRS\n" + "".join(f'    "{d}"\n' for d in c_source_dirs) + ")\n"
            "set(RTE_C_COMPONENT_INPUTS\n" + "".join(f'    "{f}"\n' for f in inputs) + ")\n")
    if model.dataset is not None:
        rendered["rte_components.cmake"] += model.dataset.cmake          # TOOL-145, only with a dataset
    rendered = genlicense.stamp_all(rendered)                          # PLAT-120, before the manifest hashes the files
    if isinstance(config, nxconfig.Config) and config.rte_file is not None and \
            Path(ecu_file).resolve() == Path(config.rte_file).resolve():
        # PLAT-79: manifest of this run - identity of configuration and generators, SHA-256 of every file written
        identity = nxidentity.of(config)
        rendered[nxidentity.manifest_path("rtegen")] = genlicense.stamp(identity.manifest("rtegen", rendered),
                                                                         nxidentity.manifest_path("rtegen"))
    for name, content in rendered.items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8", newline="\n")
    return components, model.buffers, model.app_tasks


C_SOURCE_SUFFIXES = (".c", ".h", ".cpp", ".hpp")


def check_identifiers(rendered: dict[str, str]) -> None:
    """TOOL-134: every identifier of the generated C/C++ files is distinct in its first 63 characters.

    The one rule of tools/swc/swc_yaml.py (identifier_clashes): the length of a name is free, MISRA C:2012 5.1/5.2
    asks for distinct significant characters. Checked here over everything generated, so names that depend on
    rte.yaml (Rte_Cpy_<Task>_..., CAN buffers) are covered as well.
    """
    names = [m.group(0) for file, content in rendered.items() if file.endswith(C_SOURCE_SUFFIXES)
             for m in swc_yaml.C_WORD.finditer(content)]
    clashes = swc_yaml.identifier_clashes(names)
    if clashes:
        raise RteError("; ".join(swc_yaml.clash_text(a, b) for a, b in clashes))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="project (projects/<project>/project.yaml), names the rte.yaml")
    parser.add_argument("out", type=Path, nargs="?", help="output directory")
    parser.add_argument("--repo", type=Path, default=HERE.parents[1],
                        help="repository root; since RTE-22 only the fallback for an ECU description of format 1 "
                             "(no schema_version), whose components[].path and can.dbc are relative to it. A "
                             "description of format 2 has them relative to itself and ignores this")
    parser.add_argument("--validate", action="store_true",
                        help="only check project and ECU description (no file is written), same messages as the "
                             "Studio")
    args = parser.parse_args()
    if args.out is None and not args.validate:
        parser.error("the output directory is required unless --validate is given")
    if args.validate:
        # same function as the Studio live check (TOOL-07): messages with file, line and YAML path
        messages = validate(args.project, args.repo)
        for m in messages:
            print(f"rtegen: {m}", file=sys.stderr)
        sys.exit(1 if any(m.severity == "error" for m in messages) else 0)
    try:
        generate_project(args.project, args.out, args.repo)
    except (RteError, dbcgen.DbcError, OSError, ET.ParseError, KeyError, AttributeError) as e:
        print(f"rtegen: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
