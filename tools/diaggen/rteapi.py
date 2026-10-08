"""Interface between the generated diagnostic jobs (tools/diaggen) and the RTE (tools/rtegen), COM-28.

The diagnostic stack reaches component values only through functions of the RTE. The direction was decided with
COM-29: the diagnosis (the Dcm in AUTOSAR words) calls the RTE, the RTE never calls the diagnosis. Both generators
derive the list of these functions from the same diagnostic description with this module, so the calls diaggen
writes and the definitions rtegen writes cannot drift apart; a mismatch would at the latest be a link error,
because the C++ names carry the parameter types.

Functions (namespace rte::diag, header RteDiag.h):
  bool readDid_<Did>(T& element, ...) elements of the identifier whose value the RTE delivers: a provided port
                                      (diag.yaml `source.port`, the published value of that port) or a value of
                                      the basic software in the notation of the rte.yaml connections (COM-32,
                                      `source.bsw` with a prefix of RTE_BSW_PREFIXES: ecu.state, adc.mv.<CHANNEL>,
                                      bsw.power.supply_mv, ...). false: a value of the basic software cannot be
                                      read now (e.g. the ADC conversion failed), the job answers NRC 0x22
  writeDid_<Did>(T element, ...)      the port elements of a writable identifier (2E); the RTE hands them to the
                                      required ports connected to `did.<Did>[.<element>]` in rte.yaml (values of
                                      the basic software are never written)
  routine_<Routine>_<step>(T request..., T& response...)
                                      one step of RoutineControl (31: start, stop, results): hands the request
                                      parameters to the required ports connected to
                                      `routine.<Routine>.<step>.<param>`, counts the step on required ports
                                      connected to `routine.<Routine>.<step>` and reads the response parameters
                                      from the provided ports connected to `routine.<Routine>.<step>.<param>`

Every value on this interface has the type of the description (the coded value on the wire, before `factor` and
`offset`), never the type of the port: the RTE converts between the two (rtegen checks that they fit).

Qt free, no file access: the Studio can use it as it is.
"""

import math
import sys
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxconfig import genlicense  # noqa: E402  (PLAT-120: licence line of generated files)

STEPS = ("start", "stop", "results")
STEP_SUBFUNCTION = {"start": 0x01, "stop": 0x02, "results": 0x03}   # routineControlType (ISO 14229-1, 13.2)

C_TYPES = {"uint8": "uint8_t", "uint16": "uint16_t", "uint32": "uint32_t", "int8": "int8_t", "int16": "int16_t",
           "int32": "int32_t", "float32": "float"}
# value range of the integer types of the description; float32 has none
TYPE_RANGE = {"uint8": (0, 0xFF), "uint16": (0, 0xFFFF), "uint32": (0, 0xFFFFFFFF), "int8": (-0x80, 0x7F),
              "int16": (-0x8000, 0x7FFF), "int32": (-0x80000000, 0x7FFFFFFF)}
SCALAR_TYPES = tuple(C_TYPES)

# COM-32: `source: {bsw: <name>}` with one of these first parts is a value of the basic software the RTE reads
# (the notation of the rte.yaml connections: ecu.state, ecu.wakeup[.<source>], adc.mv|raw|phys|qlf.<CHANNEL>,
# bsw.<service>.<value>). Every other bsw name is a value of the diagnostic stack itself (tools/diaggen
# STACK_VALUES: bootloader.version, application.version, uds.session, uds.ram_test, chip.uid, hardware.number,
# nextbsw.version, compatibility.*; PLAT-79: configuration.identity). Which names exist
# in a project and which element types they fit is resolved by tools/rtegen (diag_bsw_values).
RTE_BSW_PREFIXES = ("ecu", "adc", "bsw")


def is_rte_bsw(name) -> bool:
    """True for a `source.bsw` name the RTE delivers (COM-32), False for a value of the diagnostic stack."""
    return isinstance(name, str) and name.split(".", 1)[0] in RTE_BSW_PREFIXES


@dataclass(frozen=True)
class Param:
    """One value on the interface."""
    name: str            # C parameter name (element or parameter name of the description)
    diag_type: str       # type of the description (uint8 .. float32)
    direction: str       # "in": from the diagnosis to the RTE, "out": from the RTE to the diagnosis
    target: str          # connection target in rte.yaml, e.g. did.RamTest.value or routine.Self.start.cycles
    element: object      # DiagParameter / DiagDataElement of the description

    @property
    def ctype(self) -> str:
        return C_TYPES[self.diag_type]

    def decl(self) -> str:
        return f"{self.ctype}& {self.name}" if self.direction == "out" else f"{self.ctype} {self.name}"

    @property
    def bsw(self) -> str | None:
        """COM-32: the value of the basic software behind this parameter (`source.bsw`), None for a port."""
        source = getattr(self.element, "source", None)
        name = getattr(source, "bsw", None)
        return name if is_rte_bsw(name) else None


@dataclass(frozen=True)
class Function:
    name: str            # rte::diag::<name>
    kind: str            # "read", "write" or "routine"
    owner: str           # identifier or routine name of the description
    step: str | None     # routine step (start, stop, results)
    params: tuple        # Param, in wire order (routine: request parameters first)
    counter: str | None  # routine: target of the step counter, routine.<Routine>.<step>

    @property
    def returns(self) -> str:
        """C return type: a read reports whether every value could be read (COM-32), the others return nothing."""
        return "bool" if self.kind == "read" else "void"

    def signature(self) -> str:
        return f"{self.returns} {self.name}({', '.join(p.decl() for p in self.params)})"


def did_target(did, element) -> str:
    return f"did.{did.name}.{element.name}"


def routine_target(routine, step: str, param=None) -> str:
    return f"routine.{routine.name}.{step}" + (f".{param.name}" if param is not None else "")


def port_elements(did) -> list:
    """Elements of an identifier whose value comes from a provided port."""
    return [e for e in did.data if e.source.port is not None]


def rte_elements(did) -> list:
    """Elements of an identifier whose value the RTE delivers: provided ports and values of the basic software."""
    return [e for e in did.data if e.source.port is not None or is_rte_bsw(e.source.bsw)]


def functions(desc) -> list[Function]:
    """RTE functions the diagnostic jobs of `desc` (DiagDescription) call, in the order of the description."""
    result = []
    for did in desc.data_identifiers:
        read, written = rte_elements(did), port_elements(did)
        if did.read and read:
            result.append(Function(f"readDid_{did.name}", "read", did.name, None,
                                   tuple(Param(e.name, e.type, "out", did_target(did, e), e) for e in read),
                                   None))
        if did.write and written:
            result.append(Function(f"writeDid_{did.name}", "write", did.name, None,
                                   tuple(Param(e.name, e.type, "in", did_target(did, e), e) for e in written),
                                   None))
    for routine in desc.routines:
        for step in STEPS:
            value = getattr(routine, step)
            if value is None:
                continue
            params = [Param(p.name, p.type, "in", routine_target(routine, step, p), p) for p in value.request]
            params += [Param(p.name, p.type, "out", routine_target(routine, step, p), p) for p in value.response]
            result.append(Function(f"routine_{routine.name}_{step}", "routine", routine.name, step, tuple(params),
                                   routine_target(routine, step)))
    return result


def coded_range(element) -> tuple | None:
    """(lowest, highest) coded value `min`/`max` allow, or None without both limits.

    Physical = factor * coded + offset, so coded = (physical - offset) / factor; a negative factor swaps the ends.
    A text table (`values`) is its own range: the coded values it lists.
    """
    values = getattr(element, "values", None)
    if values:
        coded = [v.value for v in values]
        return (min(coded), max(coded))
    if element.min is None or element.max is None or element.factor == 0:
        return None
    ends = sorted(((element.min - element.offset) / element.factor, (element.max - element.offset) / element.factor))
    if element.type in TYPE_RANGE:
        return (math.ceil(ends[0] - 1e-9), math.floor(ends[1] + 1e-9))
    return (ends[0], ends[1])


def render_header(desc, source: str, generator: str) -> str:
    """RteDiag.h: declarations of the RTE functions the diagnostic jobs call."""
    lines = [f"// Generated by {generator} from {source} - do not edit.",
             "//",
             "// Functions of the RTE for the diagnostic jobs of ecu/uds (COM-28, tools/diaggen/rteapi.py). The",
             "// diagnosis calls the RTE, never the other way round. Values are the coded values of the diagnostic",
             "// description; the RTE converts them to and from the port types. Callable from any task: every",
             "// function takes the RTE lock for the ports it touches; values of the basic software (COM-32) are read",
             "// before that, outside the lock. A read returns false if such a value cannot be read now.",
             "",
             "#pragma once",
             "",
             "#include <cstdint>",
             "",
             "namespace rte",
             "{",
             "namespace diag",
             "{"]
    for fn in functions(desc):
        lines.append(f"{fn.signature()};")
    lines += ["} // namespace diag", "} // namespace rte", ""]
    return genlicense.stamp("\n".join(lines), "RteDiag.h")      # PLAT-120
