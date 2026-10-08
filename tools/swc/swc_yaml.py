# SPDX-License-Identifier: Apache-2.0
"""Description of a software component: swc/<Swc>/swc.yaml (RTE-07).

The file names the runnables with their periods and the sender-receiver ports with type and the runnables using
them. tools/swc/swc_create.m builds the Simulink model (export-function model, AUTOSAR mapping) from it, neXtBSW
Studio (TOOL-08) writes it from a form. The checks live here, without MATLAB and without Qt, so the Studio and the
host tests use the same rules; every finding carries file, YAML path and line.

Implementation (RTE-20): `implementation: simulink` (default) - the .slx is the master after swc_create, rtegen reads
the ARXML Embedded Coder writes; `implementation: c` - hand-written C code in src/*.c, this file stays the master:
rtegen reads runnables, ports and parameters from here (no MATLAB, no ARXML), tools/swc/swc_skeleton.py creates the
code skeleton once and compares the runnable functions of the sources with this file.

    python tools/swc/swc_yaml.py check swc/NewSwc/swc.yaml          messages, exit code 1 on errors
    python tools/swc/swc_yaml.py json  swc/NewSwc/swc.yaml -o x.json normalized description for swc_create.m

Schema (version 1):

    version: 1                      # optional, only 1 is known
    name: NewSwc                    # C identifier, equals the folder name
    description: text               # optional
    implementation: simulink        # optional: simulink (default, model + Embedded Coder) or c (hand-written, RTE-20)
    runnables:                      # at least one; <name>_Init is created by the AUTOSAR mapping itself
      - name: Run10ms
        period_ms: 10               # integer ms > 0
    ports:                          # optional
      - name: SpeedRaw
        direction: in               # in: required port (root inport), out: provided port (root outport)
        type: uint16                # boolean, uint8/16/32, sint8/16/32, float32/64, or a name under types:
        runnables: [Run10ms]        # in: one or more readers, out: exactly one writer
        dimension: 1                # optional (RTE-25): elements of the port, 1 = scalar (default), > 1: array
                                    # a number, or a name of the init data (hvscmc.number_cell_voltages) that
                                    # load() resolves from <Swc>_init.m / init_data/ without MATLAB (initdata.py)
        dimension_source: expr      # optional, with a number as dimension: the expression it was resolved from
                                    # (the import writes it); load() checks it again where it can
        unit: V                     # optional (TOOL-150), PHYS_UNIT in the A2L; proposed from the name's prefix
                                    # (studio.core.portnaming, doc/naming/praefix-tabelle.yaml), never decided by it
        min: 0                      # optional (TOOL-150), engineering limits (A2L LOWER_LIMIT/UPPER_LIMIT),
        max: 100                    # physical, inside the type range; min <= max, like a parameter's
    parameters:                    # optional calibration parameters (RTE-03): one ParameterReceiver port CalPrm,
      - name: FilterAlpha           # one element per parameter; the model calls Rte_Prm_CalPrm_<name>()
        type: uint16                # platform type of the stored value
        dim: 1                      # optional, > 1: row vector with dim elements
        lsb: 0.001                  # optional fixed point (integer types): physical = stored * lsb + offset
        offset: 0                   # optional, with lsb
        runnables: [Run100ms]       # readers
        # calibration objects (RTE-26, stage K1 of doc/architecture/kalibrierung.md), all keys optional:
        kind: value                 # value | array | curve | map | axis; without kind: value (dim 1) or array
        unit: rpm                   # physical unit (A2L PHYS_UNIT)
        min: 0                      # engineering limits, physical, inside the type range; min <= max
        max: 8000
        safety: qm                  # qm | asil_a | asil_b; an ASIL class needs min and max
        description: text           # one line (A2L long identifier)
        value_table: {0: Off, 1: On}   # integer types without lsb: stored value -> text (A2L COMPU_VTAB)
        axis: SpeedAxis_rpm         # curve: a shared axis (name of a parameter with kind axis) or own
                                    # breakpoints {dim: 8, type: uint16, lsb, offset, unit, min, max, description}
        axes: {x: ..., y: ...}      # map: the X axis (rows) and the Y axis (columns), each as for axis
    measurements:                  # optional (TOOL-143): internal values an XCP tool may read (never write)
      - name: CellVoltageMax        # C identifier of the global variable; label <Swc>.<name> in rte::SIGNALS/A2L
        type: float32               # platform type
        dimension: 1                # optional, like a port: number, expression of the init data, dimension_source
        unit: V                     # optional, PHYS_UNIT in the A2L
        min: 0                      # optional (TOOL-150), engineering limits (A2L LOWER_LIMIT/UPPER_LIMIT),
        max: 100                    # physical, inside the type range; min <= max
        description: text           # optional, long identifier in the A2L

    types:                         # optional (RTE-15): named data types of the ports, Simulink components only
      - name: MySwc_Mode            # enumeration: Simulink enumeration, literals MySwc_Mode_Off ... in Rte_Type.h
        kind: enum
        base: uint8                 # optional: uint8 (default), uint16, sint8, sint16, sint32
        literals:                   # name, or {name: value}; without a value the previous one + 1 (first 0)
          - Off
          - Run
          - {Fault: 3}
        default: Off                # optional, the first literal otherwise (init value of the port)
      - name: MySwc_Speed           # fixed point: a named Simulink.NumericType (alias) - a bare fixdt(...) would
        kind: fixed                 # lose its scaling inside a record in the ARXML of R2024a (RTE-04)
        base: sint16                # integer platform type of the stored value
        lsb: 0.0625                 # physical = stored * lsb + offset
        offset: 0                   # optional
        unit: rpm                   # optional
      - name: MySwc_CmdBus          # record: Simulink.Bus, a struct of Rte_Type.h
        kind: record
        elements:
          - { name: Mode, type: MySwc_Mode }
          - { name: Speed, type: MySwc_Speed }
          - { name: Limits, type: uint8, dimension: 2 }   # array inside the record (a whole number)
          - { name: Enable, type: boolean }
All named types are imported from Rte_Type.h (DataScope Imported): the RTE defines them from the ARXML, the model
only uses them. swc_create writes their MATLAB definitions from this list into <Swc>_types.m next to the model
(the model's PreLoadFcn runs it, swc_load_init too); `swc_yaml.py types` writes it again after a change here.
A port with a named type has dimension 1 (arrays: a record with an array element).

A measurement is a global variable of the component code. Simulink: a Simulink.Signal object with an exporting
storage class (EXPORTING_STORAGE_CLASSES; the Studio import writes the list from the model), Embedded Coder defines
it. C (implementation: c): the RTE defines it (Rte.c) and declares it in Rte_<Swc>.h; the code writes it. rtegen
lists it in rte::SIGNALS, so the XCP slave may read it and tools/a2lgen writes a MEASUREMENT (MATRIX_DIM for arrays).

The values of the parameters are no part of this file: they stand only in <Swc>_data.m next to the model
(read by tools/swc/mdata.py). load() checks that every parameter has a value there with the right length.
RTE-26: a map is a matrix there with one row per X breakpoint ([1 2 3; 4 5 6]); own breakpoints of a curve or map
stand under <Name>_X / <Name>_Y (also parameters of kind axis in Swc.parameters, placed before their owner). The
values are the defaults of the component type until the dataset of the project (stage K2) takes them over.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # mdata.py (RTE-03)
from nxconfig.messages import Message, Path_  # noqa: E402

SCHEMA_VERSION = 1
FILE_NAME = "swc.yaml"

# AUTOSAR platform types of a port (swc/README.md, Modellregeln) and the Simulink type of the root port
TYPES = {
    "boolean": "boolean",
    "uint8": "uint8", "uint16": "uint16", "uint32": "uint32",
    "sint8": "int8", "sint16": "int16", "sint32": "int32",
    "float32": "single", "float64": "double",
}
DIRECTIONS = ("in", "out")
# RTE-20: how the component is implemented; simulink is the default, so existing descriptions keep their meaning
IMPLEMENTATIONS = ("simulink", "c")
DEFAULT_IMPLEMENTATION = "simulink"
# reserved words of C99 (runnable, port and component names become C identifiers of the RTE and of the component)
C_KEYWORDS = frozenset((
    "auto break case char const continue default do double else enum extern float for goto if inline int long "
    "register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while "
    "_Bool _Complex _Imaginary").split())
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# TOOL-134: the one rule for names that end up in generated C identifiers (swc.yaml, Studio and tools/rtegen use it).
# The length of a generated identifier is not limited: C99 (5.2.4.1) only guarantees a number of *significant*
# initial characters (63 internal, 31 external), GCC has no limit, and Embedded Coder keeps the AUTOSAR RTE calls
# Rte_Read_<Port>_<Port> whole even above MaxIdLength 63 (R2024a, measured 2026-09-25 with a 32-character port: a
# 74-character Rte_Read in the model code). What MISRA C:2012 asks (Rule 5.1 external, 5.2 same scope, 5.4 macros)
# is that identifiers are *distinct* within the significant characters. neXtBSW documents 63 for internal and
# external identifiers (the toolchains it builds with have no lower limit), so the check is: every generated
# identifier differs from every other one in its first SIGNIFICANT_CHARS characters (identifier_clashes).
# Names themselves are only limited where a tool limits them: an AUTOSAR SHORT-NAME has at most 128 characters
# (port = interface = data element name), a MATLAB name at most 63 (namelengthmax: model, parameter variable).
SIGNIFICANT_CHARS = 63
MAX_ID_LENGTH = SIGNIFICANT_CHARS                      # MaxIdLength of the models (swc_create.m), same number
MAX_SHORT_NAME = 128
MAX_PORT_NAME = MAX_SHORT_NAME
MAX_NAME = MAX_ID_LENGTH - len("_Init")

TOP_KEYS = {"version", "name", "description", "implementation", "runnables", "ports", "parameters", "measurements",
            "types"}
RUNNABLE_KEYS = {"name", "period_ms"}
PORT_KEYS = {"name", "direction", "type", "runnables", "dimension", "dimension_source",
            # TOOL-150: optional, like a measurement's - unit for A2L/display, min/max as engineering limits (A2L
            # LOWER_LIMIT/UPPER_LIMIT); the naming convention (studio.core.portnaming) proposes them, never decides
            "unit", "min", "max"}
# RTE-25: elements of an array port. The upper bound is the element count of one rte::SIGNALS row block and of the
# A2L MATRIX_DIM a calibration tool reads comfortably; a larger block is no sender-receiver signal any more.
MAX_DIMENSION = 4096
# a symbolic dimension: a name of the init data, or arithmetic of names and numbers (initdata.py evaluates it)
DIMENSION_EXPRESSION = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9_.+\-*/^() ]+$")
PARAMETER_KEYS = {"name", "type", "dim", "lsb", "offset", "runnables",
                  # RTE-26: calibration objects (kalibrierung.md, K1)
                  "kind", "unit", "min", "max", "safety", "description", "value_table", "axis", "axes"}
# RTE-03: all calibration parameters of a component are elements of one ParameterReceiver port
PARAMETER_PORT = "CalPrm"
# TOOL-134: a parameter is a variable of <Swc>_data.m, so namelengthmax limits it, not its Rte_Prm call
MAX_PARAMETER_NAME = MAX_ID_LENGTH
# TOOL-143: measurement signals (decision E6 a, doc/architecture/kalibrierung.md)
# TOOL-150: min/max are optional engineering limits (A2L LOWER_LIMIT/UPPER_LIMIT), like a parameter's - proposed by
# the naming convention (studio.core.portnaming), checked with _check_limits() (min <= max, inside the type range)
MEASUREMENT_KEYS = {"name", "type", "dimension", "dimension_source", "unit", "min", "max", "description"}
# Storage classes of a Simulink.Signal that mark an internal signal as a measurement. The rule: the model code
# *defines* a global variable with the name of the object at file scope, so the RTE can take its address (extern)
# and the linker gives the A2L its address. ExportedGlobal (built in) and ExportToFile (Simulink package, own .c/.h)
# do. Not: Auto / Model default (the coder may fold the signal away), ImportedExtern / ImportFromFile (defined
# outside the model), FileScope (static), Struct / BitField (a member, no symbol of its own), GetSet (functions),
# Volatile (a volatile object the RTE would have to declare volatile as well - not needed for polling).
EXPORTING_STORAGE_CLASSES = ("ExportedGlobal", "ExportToFile")
INTEGER_TYPES = {"uint8": (False, 8), "uint16": (False, 16), "uint32": (False, 32),
                 "sint8": (True, 8), "sint16": (True, 16), "sint32": (True, 32)}

# ---- RTE-26: calibration objects (stage K1 of doc/architecture/kalibrierung.md) ----------------------------------
CAL_KINDS = ("value", "array", "curve", "map", "axis")
# ASIL C and D come later with the same key (kalibrierung.md 4.4, ASIL B is the confirmed target)
SAFETY_CLASSES = ("qm", "asil_a", "asil_b")
LATER_SAFETY_CLASSES = ("asil_c", "asil_d")
OWN_AXIS_KEYS = {"dim", "type", "lsb", "offset", "unit", "min", "max", "description"}
AXIS_LETTERS = ("x", "y")
# own breakpoints of <Name> are the calibration object <Name>_X / <Name>_Y (data file, RTE element, A2L)
AXIS_SUFFIX = {"x": "_X", "y": "_Y"}
FLOAT_LIMITS = {"float32": 3.4028234663852886e38, "float64": 1.7976931348623157e308}


# ---- RTE-15: named data types of the ports (types:), the swc.yaml side of RTE-04 -----------------------------------
TYPE_KINDS = ("enum", "fixed", "record")
TYPE_KEYS = {"name", "kind", "description"}
KIND_KEYS = {"enum": {"base", "literals", "default"}, "fixed": {"base", "lsb", "offset", "unit"},
             "record": {"elements"}}
ELEMENT_KEYS = {"name", "type", "dimension", "unit", "description"}
# storage types of a Simulink enumeration (Simulink.defineIntEnumType StorageType; no uint32)
ENUM_BASES = ("uint8", "uint16", "sint8", "sint16", "sint32")
DEFAULT_ENUM_BASE = "uint8"
# every named type is defined by the RTE (Rte_Type.h, tools/rtegen) and imported by the model (Embedded Coder
# refuses anything else for AUTOSAR, RTE-04)
TYPES_HEADER = "Rte_Type.h"
TYPES_SUFFIX = "_types.m"


INIT_WHY = "with _Init at most MATLAB namelengthmax 63"
C_WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def identifier_clashes(identifiers, significant: int = SIGNIFICANT_CHARS) -> list[tuple[str, str]]:
    """Pairs of different identifiers that agree in their first `significant` characters (MISRA C:2012 5.1/5.2/5.4).

    Only identifiers of at least `significant` characters can clash: a shorter one is its own prefix. Every pair is
    returned once, (shorter or earlier, other), in a stable order.
    """
    groups: dict[str, list[str]] = {}
    for name in dict.fromkeys(identifiers):
        if len(name) >= significant:
            groups.setdefault(name[:significant], []).append(name)
    clashes = []
    for names in groups.values():
        names = sorted(names, key=lambda n: (len(n), n))
        clashes += [(names[0], other) for other in names[1:]]
    return clashes


def clash_text(first: str, second: str, significant: int = SIGNIFICANT_CHARS) -> str:
    """Message for a clash: both generated identifiers with their length."""
    return (f"generated identifier {second} ({len(second)} characters) is not distinct from {first} "
            f"({len(first)} characters) in the first {significant} characters (MISRA C:2012 Rule 5.1/5.2)")


def generated_identifiers(name: str, ports, parameters, runnables, implementation: str,
                          types=()) -> dict[str, Path_]:
    """C identifiers the component, its header Rte_<Swc>.h and the RTE get from one swc.yaml -> YAML path of the name.

    Port (element = port name): Rte_Read/Rte_Write_<Port>_<Port> in the component, Rte_Read/Rte_Write_<Swc>_<Port>_
    <Port> and the buffer Rte_Buf_<Swc>_<Port>_<Port> in the RTE; parameter: Rte_Prm_CalPrm_<Name>,
    Rte_Prm_<Swc>_CalPrm_<Name> and its _data; runnable of a C component: <Swc>_<Runnable>; named type (RTE-15): its
    typedef and one macro <Type>_<Literal> per enumeration literal. The per-task copies
    Rte_Cpy_<Task>_... depend on rte.yaml and are checked by tools/rtegen over everything it generates.
    """
    found: dict[str, Path_] = {}
    for path, port in ports:
        api = "Rte_Read" if port.direction == "in" else "Rte_Write"
        element = f"{port.name}_{port.name}"
        for ident in (f"{api}_{element}", f"{api}_{name}_{element}", f"Rte_Buf_{name}_{element}"):
            found.setdefault(ident, (*path, "name"))
    for path, prm in parameters:
        for ident in (f"Rte_Prm_{PARAMETER_PORT}_{prm.name}", f"Rte_Prm_{name}_{PARAMETER_PORT}_{prm.name}",
                      f"Rte_Prm_{name}_{PARAMETER_PORT}_{prm.name}_data"):
            found.setdefault(ident, (*path, "name"))
    if implementation == "c":
        for path, runnable in runnables:
            found.setdefault(f"{name}_{runnable.name}", (*path, "name"))
    for path, t in types:                                   # RTE-15: typedef and literal macros of Rte_Type.h
        found.setdefault(t.name, (*path, "name"))
        for j, (literal, _value) in enumerate(t.literals):
            found.setdefault(t.literal_macro(literal), (*path, "literals", j))
    return found


def data_file_of(folder: Path, name: str) -> Path:
    """Calibration values of a component: <folder>/<Swc>_data.m (RTE-03)."""
    return Path(folder) / f"{name}_data.m"


@dataclass
class Runnable:
    name: str
    period_ms: int


@dataclass
class Port:
    name: str
    direction: str
    type: str
    runnables: list[str]
    dimension: int | None = 1        # RTE-25: elements; None while a symbolic dimension is not resolved (load())
    dimension_source: str = ""       # the expression of the init data the dimension stands for, "" = a plain number
    # TOOL-150: optional, physical - unit for A2L/display, min/max as engineering limits (A2L LOWER_LIMIT/UPPER_LIMIT)
    unit: str = ""
    min: float | None = None
    max: float | None = None
    type_kind: str = "platform"      # RTE-15: platform, or enum / fixed / record for a name under types:

    @property
    def simulink_type(self) -> str:
        return simulink_type_of(self.type, self.type_kind)

    @property
    def array(self) -> bool:
        return self.dimension is not None and self.dimension > 1


def simulink_type_of(name: str, kind: str = "platform") -> str:
    """OutDataTypeStr of a port or bus element (RTE-15): the Simulink type of a platform type, 'Enum: <Name>',
    'Bus: <Name>', or the name of the Simulink.NumericType of a fixed-point type."""
    if kind == "enum":
        return f"Enum: {name}"
    if kind == "record":
        return f"Bus: {name}"
    if kind == "fixed":
        return name
    return TYPES[name]


@dataclass
class TypeElement:
    """RTE-15: an element of a record (Simulink.BusElement)."""
    name: str
    type: str
    kind: str = "platform"           # platform, enum, fixed, record
    dimension: int = 1
    unit: str = ""
    description: str = ""

    @property
    def simulink_type(self) -> str:
        return simulink_type_of(self.type, self.kind)


@dataclass
class SwcType:
    """RTE-15: a named data type of swc.yaml (`types:`), imported by the model from Rte_Type.h.

    enum: Simulink enumeration of `base` with `literals` ((name, value), in order), `default` literal; the C macro
    of a literal is <Type>_<Literal> (addClassNameToEnumNames, as RTE-04 found it necessary).
    fixed: Simulink.NumericType alias of the integer `base`, physical = stored * lsb + offset.
    record: Simulink.Bus with `elements`."""
    name: str
    kind: str
    base: str = ""
    literals: list = field(default_factory=list)
    default: str = ""
    lsb: float | None = None
    offset: float = 0
    unit: str = ""
    description: str = ""
    elements: list = field(default_factory=list)

    def literal_macro(self, literal: str) -> str:
        return f"{self.name}_{literal}"

    def to_json(self) -> dict:
        return {"name": self.name, "kind": self.kind, "base": self.base,
                "literals": [{"name": n, "value": v} for n, v in self.literals], "default": self.default,
                "lsb": self.lsb, "offset": self.offset, "unit": self.unit, "description": self.description,
                "elements": [{"name": e.name, "type": e.type, "kind": e.kind, "simulink_type": e.simulink_type,
                              "dimension": e.dimension, "unit": e.unit, "description": e.description}
                             for e in self.elements]}


@dataclass
class PortDimension:
    """What a Simulink PortDimensions text says about a port (RTE-25), for the import (tools/studio) and swc.yaml.

    `value`: whole number of elements (1 scalar), None if symbolic or inherited; `expression`: the symbolic
    expression ('' for a number); `inherited`: -1; `error`: why it cannot be a swc.yaml port (a matrix, ...)."""
    value: int | None
    expression: str = ""
    inherited: bool = False
    error: str = ""

    @property
    def scalar(self) -> bool:
        return self.value == 1

    def yaml_value(self):
        """Value for the key `dimension` of swc.yaml: the number, or the expression while there is none."""
        return self.value if self.value is not None else self.expression


def parse_port_dimensions(text) -> PortDimension:
    """Core rule for a PortDimensions text of Simulink ('1', '8', '[1 8]', '[8 1]', 'hvscmc.n', '[1 hvscmc.n]', '-1').

    A row or column vector is an array port of its length; a matrix (both sides > 1) is refused, because an RTE
    signal and swc.yaml have one dimension. Symbolic entries are kept as expression - swc_yaml.load() resolves them
    from the init data of the component (initdata.py), or the import takes the number MATLAB compiled.
    """
    raw = str(text if text is not None else "").strip()
    if raw in ("", "-1", "[-1]"):
        return PortDimension(None if raw else 1, inherited=bool(raw))
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        parts = [p for p in re.split(r"[\s,;]+", inner) if p] if inner else []
    else:
        parts = [raw]                 # without brackets one value, also an expression with blanks (a.n + 2)
    if not parts:
        return PortDimension(1)
    if len(parts) > 2:
        return PortDimension(None, raw, error=f"{len(parts)} dimensions ({raw}); swc.yaml ports have one")
    numbers = [int(p) if p.isdigit() else None for p in parts]
    if len(parts) == 2:
        ones = [i for i, n in enumerate(numbers) if n == 1]
        if not ones:
            if all(n is not None for n in numbers):
                return PortDimension(None, raw, error=f"matrix {raw}; swc.yaml ports are scalars or vectors")
            return PortDimension(None, raw, error=f"{raw} is a matrix unless one side is 1; give the vector as "
                                                  f"[1 n] or n")
        parts, numbers = [parts[1 - ones[0]]], [numbers[1 - ones[0]]]
    if numbers[0] is not None:
        return PortDimension(numbers[0]) if numbers[0] >= 1 else PortDimension(None, raw, error=f"dimension {raw}")
    if not DIMENSION_EXPRESSION.match(parts[0]):
        return PortDimension(None, parts[0], error=f"dimension '{parts[0]}' is neither a number nor an expression "
                                                   f"of names and numbers")
    return PortDimension(None, parts[0])


def resolve_dimension(expression: str, folder: Path | str, name: str):
    """RTE-25: initdata.Resolution of a symbolic dimension of component `name` in `folder` (no MATLAB)."""
    import initdata  # tools/swc/initdata.py, next to this file
    return initdata.resolve(expression, folder, name)


@dataclass
class Parameter:
    name: str
    type: str
    dim: int
    runnables: list[str]
    lsb: float | None = None
    offset: float = 0
    values: list = field(default_factory=list)   # from <Swc>_data.m (load()), not from swc.yaml
    # RTE-26 (K1): what makes it a calibration object; the defaults keep an RTE-03 parameter as it was
    kind: str = ""                  # value, array, curve, map, axis; "" = RTE-03 form (value if dim 1, else array)
    unit: str = ""
    min: float | None = None        # engineering limits, physical
    max: float | None = None
    safety: str = "qm"
    description: str = ""
    value_table: dict = field(default_factory=dict)   # stored value -> text
    axes: list = field(default_factory=list)          # [CalAxis]: X (curve, map), Y (map)
    shape: tuple = ()               # points per axis: (nx,) curve, (nx, ny) map; dim = their product
    own_of: str = ""                # own breakpoints: name of the curve/map they belong to
    yaml_path: tuple = ()           # where the definition stands in swc.yaml (messages)

    @property
    def simulink_type(self) -> str:
        if self.lsb is None:
            return TYPES[self.type]
        signed, bits = INTEGER_TYPES[self.type]
        return f"fixdt({int(signed)},{bits},{self.lsb!r},{self.offset!r})"

    @property
    def cal_kind(self) -> str:
        """Kind of the calibration object: the declared one, or value/array for an RTE-03 parameter."""
        return self.kind or ("value" if self.dim == 1 else "array")

    @property
    def calibration(self) -> bool:
        """True if the definition uses a key of RTE-26 beyond the RTE-03 parameter."""
        return bool(self.kind or self.unit or self.min is not None or self.max is not None or self.safety != "qm"
                    or self.description or self.value_table or self.axes or self.own_of)


@dataclass
class CalAxis:
    """Axis of a curve or map (RTE-26): a shared axis by name, or own breakpoints (a Parameter of kind axis)."""
    letter: str                      # x or y
    shared: str = ""                 # name of a parameter with kind axis
    own: Parameter | None = None     # own breakpoints <Owner>_X / <Owner>_Y

    @property
    def name(self) -> str:
        return self.shared or self.own.name


@dataclass
class Measurement:
    """TOOL-143: an internal value of the component an XCP tool may read (swc.yaml `measurements:`)."""
    name: str
    type: str
    dimension: int | None = 1        # like Port.dimension: None while a symbolic dimension is not resolved
    dimension_source: str = ""
    unit: str = ""
    description: str = ""
    # TOOL-150: optional engineering limits (A2L LOWER_LIMIT/UPPER_LIMIT), physical - like a Port's or a Parameter's
    min: float | None = None
    max: float | None = None

    @property
    def array(self) -> bool:
        return self.dimension is not None and self.dimension > 1


def is_measurement_mark(storage_class: str) -> bool:
    """TOOL-143: whether a Simulink.Signal with this storage class marks its signal as a measurement."""
    return str(storage_class or "").strip() in EXPORTING_STORAGE_CLASSES


@dataclass
class Swc:
    name: str
    description: str = ""
    runnables: list[Runnable] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    implementation: str = DEFAULT_IMPLEMENTATION
    measurements: list[Measurement] = field(default_factory=list)    # TOOL-143
    types: list[SwcType] = field(default_factory=list)                # RTE-15, in dependency order

    @property
    def types_file(self) -> str:
        """RTE-15: file name of the MATLAB definitions of the named types, next to the model."""
        return f"{self.name}{TYPES_SUFFIX}"

    @property
    def init_runnable(self) -> str:
        """Name (and C symbol) of the init runnable: <Swc>_Init, for Simulink and C alike."""
        return f"{self.name}_Init"

    def symbol_of(self, runnable: str) -> str:
        """C function of a periodic runnable. Simulink: the runnable name (Embedded Coder); C (RTE-20):
        <Swc>_<Runnable>, so that two hand-written components with a runnable Run10ms can share one ECU."""
        return f"{self.name}_{runnable}" if self.implementation == "c" else runnable

    def to_json(self) -> dict:
        """Normalized description for swc_create.m (jsondecode): lists stay lists, types resolved."""
        return {
            "version": SCHEMA_VERSION,
            "name": self.name,
            "description": self.description,
            "runnables": [{"name": r.name, "period_ms": r.period_ms, "sample_time": _sample_time(r.period_ms)}
                          for r in self.runnables],
            "ports": [{"name": p.name, "direction": p.direction, "type": p.type, "type_kind": p.type_kind,
                       "simulink_type": p.simulink_type, "runnables": list(p.runnables),
                       # RTE-25: PortDimensions of the root port, always the number (the model code has one)
                       "dimension": p.dimension, "dimension_source": p.dimension_source} for p in self.ports],
            "parameter_port": PARAMETER_PORT,
            "parameters": [{"name": p.name, "type": p.type, "dim": p.dim, "simulink_type": p.simulink_type,
                            "runnables": list(p.runnables), "values": list(p.values),
                            # RTE-26: kind and shape (a map is nx x ny values, row by row), unit, limits
                            "kind": p.cal_kind, "shape": list(p.shape) or [p.dim], "unit": p.unit,
                            "min": p.min, "max": p.max,
                            # TOOL-193: axes of a curve/map (names, x first) and the owner of own breakpoints -
                            # swc_params.m writes a map as nx x ny matrix and dissolves a lookup table object
                            "axes": [a.name for a in p.axes], "own_of": p.own_of} for p in self.parameters],
            # RTE-15: named types and the MATLAB file that defines them (swc_create writes it next to the model)
            "types": [t.to_json() for t in self.types],
            "types_file": {"name": self.types_file, "text": types_m(self)} if self.types else None,
        }


class SwcErrors(Exception):
    """Raised by load() if at least one error was found; carries all messages."""

    def __init__(self, messages: list[Message]):
        self.messages = messages
        super().__init__("\n".join(str(m) for m in messages if m.severity == "error"))


def _sample_time(period_ms: int) -> str:
    """Sample time of a function-call inport in seconds, as text without rounding noise (10 -> '0.01')."""
    whole, rest = divmod(period_ms, 1000)
    text = f"{whole}.{rest:03d}".rstrip("0").rstrip(".")
    return text


def _line_of(tree, path: Path_) -> int | None:
    line, node = None, tree
    for part in path:
        try:
            if isinstance(node, CommentedMap) and part in node:
                line, node = node.lc.key(part)[0] + 1, node[part]
            elif isinstance(node, CommentedSeq) and isinstance(part, int) and 0 <= part < len(node):
                line, node = node.lc.item(part)[0] + 1, node[part]
            else:
                break
        except (AttributeError, KeyError, TypeError):
            break
    return line


class _Checker:
    def __init__(self, file: Path | None, tree):
        self.file, self.tree = file, tree
        self.messages: list[Message] = []

    def error(self, path: Path_, text: str):
        self.messages.append(Message("error", text, self.file, tuple(path), _line_of(self.tree, path)))

    def warning(self, path: Path_, text: str):
        self.messages.append(Message("warning", text, self.file, tuple(path), _line_of(self.tree, path)))

    def unknown_keys(self, node: dict, allowed: set[str], path: Path_):
        for key in node:
            if key not in allowed:
                self.error((*path, key), f"unknown key '{key}' (allowed: {', '.join(sorted(allowed))})")

    def identifier(self, node: dict, path: Path_, what: str, max_len: int, why: str = "") -> str | None:
        value = node.get("name")
        if value is None:
            self.error((*path, "name"), f"{what} needs a name")
            return None
        if not isinstance(value, str) or not IDENTIFIER.match(value):
            self.error((*path, "name"), f"{what} name '{value}' is no identifier (letter, then letters, digits, _)")
            return None
        if len(value) > max_len:
            self.error((*path, "name"), f"{what} name '{value}' ({len(value)} characters) is longer than {max_len} "
                                        f"characters ({why or 'MATLAB namelengthmax'})")
            return None
        if value in C_KEYWORDS:
            self.error((*path, "name"), f"{what} name '{value}' is a reserved word of C")
            return None
        return value


def check(data, file: Path | None = None, tree=None, folder_name: str | None = None) -> tuple[Swc | None, list[Message]]:
    """Checks a parsed swc.yaml; returns the description (None on errors) and all messages."""
    c = _Checker(file, tree if tree is not None else data)
    if not isinstance(data, dict):
        c.error((), "swc.yaml must be a mapping with name, runnables and ports")
        return None, c.messages
    c.unknown_keys(data, TOP_KEYS, ())

    version = data.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION or isinstance(version, bool):
        c.error(("version",), f"version {version} is not supported (known: {SCHEMA_VERSION})")

    name = c.identifier(data, (), "component", MAX_NAME, INIT_WHY)
    if name is not None and folder_name is not None and name != folder_name:
        c.error(("name",), f"name '{name}' differs from the folder '{folder_name}' "
                           f"(swc_build takes the model name from the folder)")
    description = data.get("description", "")
    if not isinstance(description, str):
        c.error(("description",), "description must be text")
        description = ""
    implementation = data.get("implementation", DEFAULT_IMPLEMENTATION)
    if implementation not in IMPLEMENTATIONS:
        c.error(("implementation",), f"implementation '{implementation}' must be one of {', '.join(IMPLEMENTATIONS)} "
                                     f"(simulink: model and Embedded Coder, c: hand-written C code in src/)")
        implementation = DEFAULT_IMPLEMENTATION

    runnables: list[Runnable] = []
    raw_runnables = data.get("runnables")
    if not isinstance(raw_runnables, list) or not raw_runnables:
        c.error(("runnables",), "at least one runnable with name and period_ms is needed")
        raw_runnables = []
    seen: dict[str, Path_] = {}
    for i, raw in enumerate(raw_runnables):
        path = ("runnables", i)
        if not isinstance(raw, dict):
            c.error(path, "runnable must be a mapping with name and period_ms")
            continue
        c.unknown_keys(raw, RUNNABLE_KEYS, path)
        rname = c.identifier(raw, path, "runnable", MAX_NAME, INIT_WHY)
        period = raw.get("period_ms")
        if period is None:
            c.error((*path, "period_ms"), "runnable needs period_ms (event-triggered runnables are not supported)")
        elif isinstance(period, bool) or not isinstance(period, int) or period <= 0:
            c.error((*path, "period_ms"), f"period_ms {period} must be a whole number of milliseconds > 0")
            period = None
        if rname is None:
            continue
        if name is not None and rname == f"{name}_Init":
            c.error((*path, "name"), f"'{rname}' is the init runnable the AUTOSAR mapping creates itself")
            continue
        if rname in seen:
            c.error((*path, "name"), f"runnable '{rname}' is already defined at {_fmt(seen[rname])}")
            continue
        seen[rname] = (*path, "name")
        if period is not None:
            runnables.append(Runnable(rname, period))

    types, broken_types = _check_types(c, data, implementation)                # RTE-15
    named = {t.name: t for t in types}

    ports: list[Port] = []
    raw_ports = data.get("ports", [])
    if raw_ports is None:
        raw_ports = []
    if not isinstance(raw_ports, list):
        c.error(("ports",), "ports must be a list")
        raw_ports = []
    # references are checked against every given runnable name, so an invalid name is reported once, not per port
    runnable_names = {r.get("name") for r in raw_runnables if isinstance(r, dict) and isinstance(r.get("name"), str)}
    for i, raw in enumerate(raw_ports):
        path = ("ports", i)
        if not isinstance(raw, dict):
            c.error(path, "port must be a mapping with name, direction, type and runnables")
            continue
        c.unknown_keys(raw, PORT_KEYS, path)
        pname = c.identifier(raw, path, "port", MAX_PORT_NAME, "AUTOSAR SHORT-NAME of port, interface and data element")
        ok = pname is not None
        direction = raw.get("direction")
        if direction not in DIRECTIONS:
            c.error((*path, "direction"), f"direction '{direction}' must be one of {', '.join(DIRECTIONS)} "
                                          f"(in: required port, out: provided port)")
            ok = False
        ptype = raw.get("type")
        kind = "platform"
        if isinstance(ptype, str) and ptype in named:
            kind = named[ptype].kind                  # RTE-15 (a C component is refused at types:, _check_types)
        elif isinstance(ptype, str) and ptype in broken_types:
            ok = False                    # a named type with an error of its own, reported at its definition
        elif ptype not in TYPES:
            c.error((*path, "type"), f"type '{ptype}' is no supported platform type ({', '.join(TYPES)}) and no "
                                     f"name under types:")
            ok = False
        users = raw.get("runnables")
        if isinstance(users, str):
            users = [users]
        if not isinstance(users, list) or not users or not all(isinstance(u, str) for u in users):
            c.error((*path, "runnables"), "runnables must list the runnables that use the port")
            ok = False
            users = []
        for j, user in enumerate(users):
            if user not in runnable_names:
                c.error((*path, "runnables", j), f"runnable '{user}' is not defined under runnables")
                ok = False
        if len(set(users)) != len(users):
            c.error((*path, "runnables"), "a runnable is listed twice")
            ok = False
        if direction == "out" and len(users) > 1:
            c.error((*path, "runnables"), f"provided port '{pname}' is written by {len(users)} runnables; "
                                          f"exactly one runnable may write it")
            ok = False
        if pname is not None:
            if pname in seen:
                c.error((*path, "name"), f"name '{pname}' is already used at {_fmt(seen[pname])} "
                                         f"(runnables and ports are blocks of the same model level)")
                ok = False
            else:
                seen[pname] = (*path, "name")
        dimension, source = _check_dimension(c, raw, path)
        if dimension is False:
            ok = False
        elif kind != "platform" and (dimension != 1 or source):
            # RTE-15: an array of a named type was not tried with Embedded Coder; a record carries arrays
            c.error((*path, "dimension"), f"port '{pname}' of the named type {ptype} must have dimension 1; for "
                                          f"several values use a record with an array element (types:)")
            ok = False
        # TOOL-150: unit and engineering limits are optional, like a measurement's - a proposal of the naming
        # convention (studio.core.portnaming), never a decision (E6 a); limits stay physical, no lsb/offset on a
        # sender-receiver port
        unit = _check_text(c, raw, path, "unit")
        if unit is None:
            ok = False
            unit = ""
        if kind == "fixed":
            # RTE-15: physical limits of a fixed-point port, inside the range of its scaled base type
            limits = _check_limits(c, raw, path, named[ptype].base, named[ptype].lsb, named[ptype].offset)
        elif kind in ("enum", "record"):
            limits = (None, None)
            for key in ("min", "max"):
                if raw.get(key) is not None:
                    c.error((*path, key), f"{key}: port '{pname}' has the {kind} type {ptype}; engineering limits "
                                          f"need a number type")
                    limits = False
        else:
            limits = _check_limits(c, raw, path, ptype, None, 0) if ptype in TYPES else (None, None)
        if limits is False:
            ok = False
            limits = (None, None)
        if ok:
            ports.append(Port(pname, direction, ptype, list(users), dimension, source, unit, *limits, kind))

    parameters = _check_parameters(c, data, runnable_names, seen)
    measurements = _check_measurements(c, data)                                  # TOOL-143

    # RTE-15: a type no port uses (directly or inside a record) is only a warning - the model may use it inside
    used_types: set[str] = set()
    for p in ports:
        if p.type_kind != "platform":
            used_types |= type_closure(p.type, named)
    raw_types = data.get("types") if isinstance(data.get("types"), list) else []
    for t in types:
        if t.name not in used_types:
            index = next((i for i, r in enumerate(raw_types) if isinstance(r, dict) and r.get("name") == t.name), 0)
            c.warning(("types", index, "name"), f"type '{t.name}' is used by no port")

    used = {u for p in [*ports, *parameters] for u in p.runnables}
    for i, r in enumerate(runnables):
        if r.name not in used:
            c.warning(("runnables", i, "name"), f"runnable '{r.name}' uses no port")

    if implementation == "c" and name is not None:
        # RTE-20: the C function of a runnable is <Swc>_<Runnable>; it must not be the init runnable either
        for i, r in enumerate(runnables):
            if f"{name}_{r.name}" == f"{name}_Init":
                c.error(("runnables", i, "name"), f"runnable 'Init' would be the init runnable {name}_Init")

    if name is not None:
        # TOOL-134: generated identifiers must be distinct in their first SIGNIFICANT_CHARS characters
        def paths(key, items):
            raw = data.get(key) if isinstance(data.get(key), list) else []
            index = {r.get("name"): i for i, r in reversed(list(enumerate(raw))) if isinstance(r, dict)}
            return [((key, index.get(item.name, 0)), item) for item in items]
        generated = generated_identifiers(name, paths("ports", ports), paths("parameters", parameters),
                                          paths("runnables", runnables), implementation, paths("types", types))
        for first, second in identifier_clashes(generated):
            c.error(generated[second], clash_text(first, second))

    if any(m.severity == "error" for m in c.messages):
        return None, c.messages
    return Swc(name, description, runnables, ports, parameters, implementation, measurements, types), c.messages


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_dimension(c: _Checker, raw: dict, path: Path_):
    """RTE-25: (dimension, dimension_source) of a port; dimension None = symbolic (load() resolves it), False =
    error reported. The number stays the master of the generated code; an expression only says where it comes from."""
    value = raw.get("dimension", 1)
    source = raw.get("dimension_source")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        c.error((*path, "dimension"), f"dimension {value} must be a whole number >= 1 or an expression of the init "
                                      f"data (e.g. hvscmc.number_cell_voltages)")
        return False, ""
    if isinstance(value, int):
        if not 1 <= value <= MAX_DIMENSION:
            c.error((*path, "dimension"), f"dimension {value} must be between 1 (scalar) and {MAX_DIMENSION}")
            return False, ""
        if source is None:
            return value, ""
        if not isinstance(source, str) or not DIMENSION_EXPRESSION.match(source.strip()):
            c.error((*path, "dimension_source"), f"dimension_source {source!r} must be the expression of the init "
                                                 f"data the dimension was resolved from (e.g. hvscmc.n)")
            return False, ""
        return value, source.strip()
    parsed = parse_port_dimensions(value)
    if parsed.error or parsed.inherited:
        c.error((*path, "dimension"), f"dimension '{value}': {parsed.error or 'inherited (-1) is no dimension'}")
        return False, ""
    if source is not None:
        c.error((*path, "dimension_source"), "dimension_source goes with a number as dimension; an expression as "
                                             "dimension is its own source")
        return False, ""
    if parsed.value is not None:
        return _check_dimension(c, {**raw, "dimension": parsed.value}, path)
    return None, parsed.expression


# ---- RTE-15: named types -----------------------------------------------------------------------------------------

# names a named type must not take: platform and Simulink built-in types (a Simulink.Bus or NumericType of that
# name would shadow them in the base workspace)
RESERVED_TYPE_NAMES = frozenset((*TYPES, *TYPES.values(), "int64", "uint64", "sint64", "half", "fixdt", "string",
                                 "logical", "char"))


def _enum_literals(c: _Checker, raw, path: Path_, base: str) -> list | None:
    """[(name, value)] of an enumeration; None after an error. Entry: name, or {name: value}; without a value the
    previous value + 1, the first 0 (as MATLAB numbers an enumeration without values)."""
    if not isinstance(raw, list) or not raw:
        c.error(path, "literals must list the literals: a name, or {name: value}")
        return None
    first, last = type_range(base)
    literals, names, values, ok, previous = [], {}, {}, True, -1
    for j, entry in enumerate(raw):
        at = (*path, j)
        if isinstance(entry, str):
            lname, value = entry, previous + 1
        elif isinstance(entry, dict) and len(entry) == 1:
            lname, value = next(iter(entry.items()))
        else:
            c.error(at, f"literal {entry!r} must be a name or {{name: value}}")
            ok = False
            continue
        if not isinstance(lname, str) or not IDENTIFIER.match(lname) or lname in C_KEYWORDS:
            c.error(at, f"literal name {lname!r} is no identifier (letter, then letters, digits, _; no C keyword)")
            ok = False
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            c.error(at, f"literal {lname}: value {value!r} must be a whole number")
            ok = False
            continue
        if not first <= value <= last:
            c.error(at, f"literal {lname}: value {value} is outside the range of {base} ({first} .. {last})")
            ok = False
        if lname in names:
            c.error(at, f"literal '{lname}' is already defined at {_fmt(names[lname])}")
            ok = False
        elif value in values:
            c.error(at, f"literal {lname}: value {value} is already the value of {values[value]} (a TEXTTABLE "
                        f"compu method needs one literal per value)")
            ok = False
        names.setdefault(lname, at)
        values.setdefault(value, lname)
        literals.append((lname, value))
        previous = value
    return literals if ok else None


def _check_types(c: _Checker, data: dict, implementation: str) -> tuple[list[SwcType], set[str]]:
    """types: (RTE-15) -> (the valid types in dependency order, names of entries with an error).

    A record refers to other named types by name, in any order of the list; the result puts every type after the
    ones it uses (the order swc_create defines them and Rte_Type.h declares them). A cycle is an error."""
    raw_types = data.get("types")
    if raw_types is None:
        return [], set()
    if not isinstance(raw_types, list):
        c.error(("types",), "types must be a list of named types (name, kind)")
        return [], set()
    if raw_types and implementation == "c":
        c.error(("types",), "named types (types:) are available to Simulink components only: tools/rtegen reads the "
                            "ports of a hand-written C component (implementation: c) from swc.yaml with platform "
                            "types and knows records, enumerations and fixed point only from the ARXML of a model")
    declared = {r.get("name"): i for i, r in reversed(list(enumerate(raw_types)))
                if isinstance(r, dict) and isinstance(r.get("name"), str)}
    kinds = {r.get("name"): r.get("kind") for r in raw_types if isinstance(r, dict) and isinstance(r.get("name"), str)}
    found: dict[str, SwcType] = {}
    broken: set[str] = set()
    where: dict[str, Path_] = {}
    for i, raw in enumerate(raw_types):
        path = ("types", i)
        if not isinstance(raw, dict):
            c.error(path, "type must be a mapping with name and kind (enum, fixed, record)")
            continue
        before = len([m for m in c.messages if m.severity == "error"])
        tname = c.identifier(raw, path, "type", MAX_ID_LENGTH, "MATLAB namelengthmax, name of the Simulink object")
        if tname is not None and tname in RESERVED_TYPE_NAMES:
            c.error((*path, "name"), f"type name '{tname}' is a platform or Simulink type")
            tname = None
        if tname is not None and tname in where:
            c.error((*path, "name"), f"type '{tname}' is already defined at {_fmt(where[tname])}")
            continue
        if tname is not None:
            where[tname] = (*path, "name")
        kind = raw.get("kind")
        if kind not in TYPE_KINDS:
            c.error((*path, "kind"), f"kind '{kind}' must be one of {', '.join(TYPE_KINDS)}")
            if tname is not None:
                broken.add(tname)
            continue
        c.unknown_keys(raw, TYPE_KEYS | KIND_KEYS[kind], path)
        description = _check_text(c, raw, path, "description")
        t = SwcType(tname or "", kind, description=description or "")
        if kind == "enum":
            base = raw.get("base", DEFAULT_ENUM_BASE)
            if base not in ENUM_BASES:
                c.error((*path, "base"), f"base '{base}' of an enumeration must be one of {', '.join(ENUM_BASES)}")
            else:
                t.base = base
                t.literals = _enum_literals(c, raw.get("literals"), (*path, "literals"), base) or []
                default = raw.get("default")
                names = [n for n, _ in t.literals]
                if default is None:
                    t.default = names[0] if names else ""
                elif default not in names:
                    c.error((*path, "default"), f"default {default!r} is no literal of {tname} ({', '.join(names)})")
                else:
                    t.default = default
        elif kind == "fixed":
            base = raw.get("base")
            if base not in INTEGER_TYPES:
                c.error((*path, "base"), f"base '{base}' of a fixed-point type must be an integer platform type "
                                         f"({', '.join(INTEGER_TYPES)})")
            else:
                t.base = base
            lsb, offset = raw.get("lsb"), raw.get("offset", 0)
            if not _number(lsb) or lsb <= 0:
                c.error((*path, "lsb"), f"lsb {lsb!r} must be a number > 0 (physical = stored * lsb + offset)")
            else:
                t.lsb = lsb
            if not _number(offset):
                c.error((*path, "offset"), f"offset {offset!r} must be a number")
            else:
                t.offset = offset
            t.unit = _check_text(c, raw, path, "unit") or ""
        else:
            elements = raw.get("elements")
            if not isinstance(elements, list) or not elements:
                c.error((*path, "elements"), "a record needs elements (name, type)")
                elements = []
            seen_elements: dict[str, Path_] = {}
            for j, e in enumerate(elements):
                at = (*path, "elements", j)
                if not isinstance(e, dict):
                    c.error(at, "element must be a mapping with name and type")
                    continue
                c.unknown_keys(e, ELEMENT_KEYS, at)
                ename = c.identifier(e, at, "element", MAX_ID_LENGTH, "MATLAB namelengthmax, field of the struct")
                if ename is not None and ename in seen_elements:
                    c.error((*at, "name"), f"element '{ename}' is already defined at {_fmt(seen_elements[ename])}")
                    ename = None
                if ename is not None:
                    seen_elements[ename] = (*at, "name")
                etype = e.get("type")
                ekind = "platform"
                if isinstance(etype, str) and etype in declared:
                    ekind = kinds.get(etype) if kinds.get(etype) in TYPE_KINDS else ""
                elif etype not in TYPES:
                    c.error((*at, "type"), f"type '{etype}' is no platform type ({', '.join(TYPES)}) and no name "
                                           f"under types:")
                    continue
                dim = e.get("dimension", 1)
                if isinstance(dim, bool) or not isinstance(dim, int) or not 1 <= dim <= MAX_DIMENSION:
                    c.error((*at, "dimension"), f"dimension {dim!r} must be a whole number between 1 and "
                                                f"{MAX_DIMENSION} (an element of a record has no symbolic dimension)")
                    continue
                if ekind == "record" and dim != 1:
                    c.error((*at, "dimension"), f"element '{ename}': an array of records is not supported (arrays of "
                                                f"anonymous structures are refused by tools/rtegen, RTE-04)")
                    continue
                unit = _check_text(c, e, at, "unit")
                text = _check_text(c, e, at, "description")
                if ename is not None:
                    # an element of a named type with an error of its own: kind "invalid" breaks the record below
                    t.elements.append(TypeElement(ename, etype, ekind or "invalid", dim, unit or "", text or ""))
        errors = len([m for m in c.messages if m.severity == "error"]) - before
        if tname is None:
            continue
        if errors:
            broken.add(tname)
        else:
            found[tname] = t

    # records whose elements use a broken type are broken too; cycles are errors
    changed = True
    while changed:
        changed = False
        for t in list(found.values()):
            if any(e.type in broken or (e.kind != "platform" and e.type not in found) for e in t.elements):
                broken.add(t.name)
                del found[t.name]
                changed = True
    ordered: list[SwcType] = []
    state: dict[str, int] = {}

    def visit(t: SwcType, stack: list[str]) -> bool:
        if state.get(t.name) == 2:
            return True
        if state.get(t.name) == 1:
            cycle = stack[stack.index(t.name):] + [t.name]
            c.error(("types", declared[t.name], "elements"), f"record {t.name} contains itself "
                                                             f"({' -> '.join(cycle)})")
            return False
        state[t.name] = 1
        ok = all(visit(found[e.type], stack + [t.name]) for e in t.elements if e.kind != "platform")
        state[t.name] = 2
        if ok:
            ordered.append(t)
        else:
            broken.add(t.name)
        return ok

    for t in list(found.values()):
        visit(t, [])
    return ordered, broken


def type_closure(name: str, named: dict) -> set[str]:
    """RTE-15: the named type and every named type inside it (records, recursively)."""
    result, todo = set(), [name]
    while todo:
        current = todo.pop()
        if current in result or current not in named:
            continue
        result.add(current)
        todo += [e.type for e in named[current].elements if e.kind != "platform"]
    return result


def _m_text(text: str) -> str:
    """A MATLAB character vector literal."""
    return "'" + str(text).replace("'", "''") + "'"


def _m_number(value) -> str:
    """A number for MATLAB code, exact as Python writes it (shortest round trip)."""
    return repr(value) if isinstance(value, float) else str(int(value))


def _binary_point(lsb, offset) -> int | None:
    """Fraction length of a power-of-two lsb without offset (binary point scaling), else None (slope and bias)."""
    from fractions import Fraction
    step = Fraction(str(lsb))
    if offset != 0 or step <= 0:
        return None
    n = 0
    while step < 1:
        step *= 2
        n += 1
    while step > 1 and step.denominator == 1 and step.numerator % 2 == 0:
        step /= 2
        n -= 1
    return n if step == 1 else None


def types_m(swc: Swc) -> str:
    """RTE-15: MATLAB definitions of the named types of swc.yaml, the file <Swc>_types.m next to the model.

    The model's PreLoadFcn and swc_load_init run it; it puts the Simulink.NumericType and Simulink.Bus objects into
    the base workspace and defines the enumerations (Simulink.defineIntEnumType), all imported from Rte_Type.h. One
    generated text per swc.yaml, so load() can tell a stale file (check_types_file)."""
    fn = f"{swc.name}_types"
    lines = [
        f"function {fn}()",
        f"%{fn.upper()} Named data types of component {swc.name} (RTE-15), generated from its swc.yaml.",
        f"%   Do not edit: change types: in swc.yaml and run  python tools/swc/swc_yaml.py types <component folder>",
        f"%   (swc_create writes it). All types are imported from {TYPES_HEADER}: the RTE (tools/rtegen) defines them",
        f"%   from the ARXML, the model only uses them. Run by the PreLoadFcn of {swc.name}.slx and by swc_load_init.",
        "",
    ]
    for t in swc.types:
        if t.kind == "enum":
            literals = ", ".join(_m_text(n) for n, _ in t.literals)
            values = " ".join(str(v) for _, v in t.literals)
            lines += [f"% enumeration {t.name}: " + ", ".join(f"{n} = {v}" for n, v in t.literals),
                      f"defineEnum({_m_text(t.name)}, {{{literals}}}, [{values}], {_m_text(TYPES[t.base])}, "
                      f"{_m_text(t.default)}, {_m_text(t.description)});", ""]
        elif t.kind == "fixed":
            signed, bits = INTEGER_TYPES[t.base]
            fraction = _binary_point(t.lsb, t.offset)
            bias = "" if t.offset == 0 else (f" + {_m_number(t.offset)}" if t.offset > 0
                                                else f" - {_m_number(-t.offset)}")
            lines += [f"% fixed point {t.name}: physical = stored * {_m_number(t.lsb)}{bias}"
                      + (f" [{t.unit}]" if t.unit else "") + f", stored {t.base}",
                      "t = Simulink.NumericType;"]
            if fraction is not None:
                lines += ["t.DataTypeMode = 'Fixed-point: binary point scaling';"]
            else:
                lines += ["t.DataTypeMode = 'Fixed-point: slope and bias scaling';"]
            lines += [f"t.Signedness = '{'Signed' if signed else 'Unsigned'}';", f"t.WordLength = {bits};"]
            if fraction is not None:
                lines += [f"t.FractionLength = {fraction};"]
            else:
                lines += [f"t.Slope = {_m_number(t.lsb)};", f"t.Bias = {_m_number(t.offset)};"]
            lines += ["t.IsAlias = true;", "t.DataScope = 'Imported';", f"t.HeaderFile = '{TYPES_HEADER}';",
                      f"t.Description = {_m_text(t.description)};", f"assignin('base', {_m_text(t.name)}, t);", ""]
        else:
            lines += [f"% record {t.name}", "b = Simulink.Bus;", f"b.Description = {_m_text(t.description)};",
                      "b.DataScope = 'Imported';", f"b.HeaderFile = '{TYPES_HEADER}';", "b.Elements = [ ..."]
            for k, e in enumerate(t.elements):
                end = ", ..." if k < len(t.elements) - 1 else "];"
                lines.append(f"    element({_m_text(e.name)}, {_m_text(e.simulink_type)}, {e.dimension}, "
                             f"{_m_text(e.unit)}, {_m_text(e.description)}){end}")
            lines += [f"assignin('base', {_m_text(t.name)}, b);", ""]
    lines += ["end", ""]
    if any(t.kind == "record" for t in swc.types):
        lines += ["function e = element(name, type, dims, unit, description)",
                  "e = Simulink.BusElement;",
                  "e.Name = name;",
                  "e.DataType = type;",
                  "e.Dimensions = dims;",
                  "e.Unit = unit;",
                  "e.Description = description;",
                  "end", ""]
    if any(t.kind == "enum" for t in swc.types):
        lines += ["function defineEnum(name, literals, values, storage, default, description)",
                  "% An enumeration that exists with the same literals and values stays: MATLAB cannot redefine a",
                  "% class while values of it exist. A changed one is defined again (MATLAB reports if it is in use).",
                  "try",
                  "    [members, names] = enumeration(name);",
                  "    if isequal(reshape(names, 1, []), literals) && isequal(double(reshape(members, 1, [])), values)",
                  "        return;",
                  "    end",
                  "catch",
                  "end",
                  "Simulink.defineIntEnumType(name, literals, values, 'StorageType', storage, ...",
                  "    'DefaultValue', default, 'Description', description, 'DataScope', 'Imported', ...",
                  f"    'HeaderFile', '{TYPES_HEADER}', 'AddClassNameToEnumNames', true);",
                  "end", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def check_types_file(swc: Swc, file: Path | None, tree=None, folder: Path | None = None) -> list[Message]:
    """RTE-15: <Swc>_types.m of a Simulink component with named types against swc.yaml (warnings).

    The model uses the definitions of that file, not swc.yaml; a different or missing file (with a model next to it)
    means the model would build with other types than swc.yaml describes."""
    if not swc.types or swc.implementation != DEFAULT_IMPLEMENTATION or folder is None:
        return []
    target = Path(folder) / swc.types_file
    path = ("types",)
    hint = f"run python tools/swc/swc_yaml.py types {Path(folder).as_posix()}"
    if target.is_file():
        try:
            current = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            current = None
        if current != types_m(swc):
            return [Message("warning", f"{swc.types_file} does not match types: of swc.yaml (the model uses the "
                                       f"file); {hint}", file, path, _line_of(tree, path))]
    elif (Path(folder) / f"{swc.name}.slx").is_file():
        return [Message("warning", f"{swc.types_file} is missing next to the model, which needs the named types of "
                                   f"swc.yaml; {hint}", file, path, _line_of(tree, path))]
    return []


def _check_parameters(c: _Checker, data: dict, runnable_names: set, seen: dict) -> list[Parameter]:
    """parameters: calibration parameters (RTE-03); values are checked by load() against <Swc>_data.m."""
    raw_parameters = data.get("parameters", [])
    if raw_parameters is None:
        raw_parameters = []
    if not isinstance(raw_parameters, list):
        c.error(("parameters",), "parameters must be a list")
        return []
    if raw_parameters and PARAMETER_PORT in seen:
        c.error(("parameters",), f"'{PARAMETER_PORT}' is the name of the parameter port and must not be used "
                                 f"elsewhere (already at {_fmt(seen[PARAMETER_PORT])})")
    result = []
    for i, raw in enumerate(raw_parameters):
        path = ("parameters", i)
        if not isinstance(raw, dict):
            c.error(path, "parameter must be a mapping with name, type and runnables")
            continue
        c.unknown_keys(raw, PARAMETER_KEYS, path)
        pname = c.identifier(raw, path, "parameter", MAX_PARAMETER_NAME,
                             "variable of <Swc>_data.m, MATLAB namelengthmax")
        ok = pname is not None
        ptype = raw.get("type")
        if ptype not in TYPES:
            c.error((*path, "type"), f"type '{ptype}' is no supported platform type ({', '.join(TYPES)})")
            ok = False
        dim = raw.get("dim", 1)
        if isinstance(dim, bool) or not isinstance(dim, int) or dim < 1:
            c.error((*path, "dim"), f"dim {dim} must be a whole number >= 1 (1: scalar, > 1: row vector)")
            ok = False
        lsb, offset = raw.get("lsb"), raw.get("offset", 0)
        if lsb is not None:
            if not _number(lsb) or lsb <= 0:
                c.error((*path, "lsb"), f"lsb {lsb} must be a number > 0")
                ok = False
            elif ptype in TYPES and ptype not in INTEGER_TYPES:
                c.error((*path, "lsb"), f"fixed point (lsb) needs an integer type, not {ptype}")
                ok = False
        if not _number(offset):
            c.error((*path, "offset"), f"offset {offset} must be a number")
            ok = False
        elif "offset" in raw and lsb is None:
            c.error((*path, "offset"), "offset needs lsb (fixed point)")
            ok = False
        users = raw.get("runnables")
        if isinstance(users, str):
            users = [users]
        if not isinstance(users, list) or not users or not all(isinstance(u, str) for u in users):
            c.error((*path, "runnables"), "runnables must list the runnables that read the parameter")
            ok = False
            users = []
        for j, user in enumerate(users):
            if user not in runnable_names:
                c.error((*path, "runnables", j), f"runnable '{user}' is not defined under runnables")
                ok = False
        if pname is not None:
            if pname in seen:
                c.error((*path, "name"), f"name '{pname}' is already used at {_fmt(seen[pname])}")
                ok = False
            else:
                seen[pname] = (*path, "name")
        cal = _check_calibration(c, raw, path, pname, ptype, dim, lsb, offset, list(users), seen)   # RTE-26
        if cal is None:
            ok = False
        if ok:
            own = [a.own for a in cal.get("axes", []) if a.own is not None]
            result += own                     # own breakpoints before their curve/map (RTE record order x, y, v)
            result.append(Parameter(pname, ptype, dim, list(users), lsb, offset if lsb is not None else 0, **cal))
    _resolve_calibration_axes(c, result, raw_parameters)                                            # RTE-26
    return result


def _check_measurements(c: _Checker, data: dict) -> list[Measurement]:
    """TOOL-143: `measurements:` - name (a C identifier, the global variable), platform type, dimension like a port,
    optional unit and description. Names are unique among the measurements; they are signal names of the model,
    not blocks, so they may equal a port or runnable name.

    TOOL-150: min/max (engineering limits, A2L LOWER_LIMIT/UPPER_LIMIT) are optional as well, checked like a port's
    or a parameter's (_check_limits: min <= max, both inside the type range)."""
    raw_items = data.get("measurements", [])
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        c.error(("measurements",), "measurements must be a list")
        return []
    result, seen = [], {}
    for i, raw in enumerate(raw_items):
        path = ("measurements", i)
        if not isinstance(raw, dict):
            c.error(path, "measurement must be a mapping with name and type")
            continue
        c.unknown_keys(raw, MEASUREMENT_KEYS, path)
        mname = c.identifier(raw, path, "measurement", MAX_ID_LENGTH, "global C variable, MATLAB namelengthmax")
        ok = mname is not None
        mtype = raw.get("type")
        if mtype not in TYPES:
            c.error((*path, "type"), f"type '{mtype}' is no supported platform type ({', '.join(TYPES)})")
            ok = False
        texts = {}
        for key in ("unit", "description"):
            value = raw.get(key, "")
            if value is None:
                value = ""
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                c.error((*path, key), f"{key} must be text")
                ok = False
                value = ""
            texts[key] = str(value)
        dimension, source = _check_dimension(c, raw, path)
        if dimension is False:
            ok = False
        limits = _check_limits(c, raw, path, mtype, None, 0) if mtype in TYPES else (None, None)
        if limits is False:
            ok = False
            limits = (None, None)
        if mname is not None:
            if mname in seen:
                c.error((*path, "name"), f"measurement '{mname}' is already defined at {_fmt(seen[mname])}")
                ok = False
            else:
                seen[mname] = (*path, "name")
        if ok:
            result.append(Measurement(mname, mtype, dimension, source, texts["unit"], texts["description"], *limits))
    return result


# ---- TOOL-194: public check API for callers without a full swc.yaml ------------------------------------------------
# check() and declared_measurements() above use the two checkers directly, sharing one _Checker (and, for
# parameters, one `seen`) across every section of one file. A caller that only has a list of entries - not a whole
# swc.yaml, as the Studio's calibration and measurement editors do (tools/studio/core/swcdetails.py) - gets the same
# checks and the same messages (file, YAML path, line) through these two functions, instead of reaching into the
# private _Checker/_check_measurements/_check_parameters (TOOL-190 finding 2026-09-29).

def check_measurements(entries, file: Path | None = None, tree=None) -> tuple[list[Measurement], list[Message]]:
    """Checks `entries` as the `measurements:` list of a swc.yaml: name, type, dimension, optional unit,
    description and engineering limits (TOOL-150) - the same rule check() applies to a whole file. `tree` is the
    parsed swc.yaml, for line numbers in the messages; without it (entries built by the caller, not read from a
    file) the messages carry no line, only the YAML path relative to `entries` (measurements[0].type, ...).
    Returns the checked Measurement list (an entry with an error is left out) and all messages."""
    c = _Checker(file, tree)
    result = _check_measurements(c, {"measurements": entries})
    return result, c.messages


def check_parameters(entries, runnable_names, file: Path | None = None, tree=None,
                     seen: dict | None = None) -> tuple[list[Parameter], list[Message]]:
    """Checks `entries` as the `parameters:` list of a swc.yaml (RTE-03/RTE-26), like check_measurements() above.
    `runnable_names` are the names defined under `runnables:` a parameter may reference; `seen` is name -> YAML path
    of names already used elsewhere in the same file (ports, runnables) that must stay distinct from a parameter
    name and from the parameter port CalPrm - a fresh dict (no other section checked yet) if the caller has none.
    Returns the checked Parameter list (RTE-26: the own breakpoints of a curve/map placed before their owner) and
    all messages."""
    c = _Checker(file, tree)
    result = _check_parameters(c, {"parameters": entries}, runnable_names, dict(seen) if seen else {})
    return result, c.messages


def check_measurement_dimensions(swc: Swc, file: Path | None, tree=None, folder: Path | None = None) -> list[Message]:
    """TOOL-143: symbolic dimensions of measurements, with the rules of check_dimensions() for ports."""
    c = _Checker(file, tree)
    for i, m in enumerate(swc.measurements):
        if not m.dimension_source:
            continue
        found = resolve_dimension(m.dimension_source, folder, swc.name) if folder is not None else None
        if m.dimension is None:
            if found is None or found.value is None:
                c.error(("measurements", i, "dimension"),
                        f"dimension '{m.dimension_source}' of measurement {m.name} cannot be resolved without "
                        f"MATLAB{': ' + found.why if found else ''}. Write the number MATLAB gives as dimension and "
                        f"keep the expression: dimension: <n>, dimension_source: {m.dimension_source}")
            elif found.value > MAX_DIMENSION:
                c.error(("measurements", i, "dimension"), f"dimension of measurement {m.name}: {found.why}, more "
                                                          f"than {MAX_DIMENSION}")
            else:
                m.dimension = found.value
        elif found is None or found.value is None:
            c.warning(("measurements", i, "dimension_source"),
                      f"dimension_source '{m.dimension_source}' of measurement {m.name} not checked without MATLAB"
                      f"{' (' + found.why + ')' if found else ''}; dimension {m.dimension} is used")
        elif found.value != m.dimension:
            c.error(("measurements", i, "dimension"),
                    f"measurement {m.name} has dimension {m.dimension}, but its source {found.why} - the init data "
                    f"changed; set dimension: {found.value} (or import the model again)")
    return c.messages


def find_prefix(name: str, prefixes) -> str | None:
    """TOOL-150: the one place that recognizes a name prefix (ARCH-64 E6, doc/naming/praefix-tabelle.yaml) - the
    prefix stands at the start of `name` or after exactly one component prefix (`HvsCtrl_bm_HvsErrorState` -> `bm_`,
    `Dem_b_DiagCtrlPrecharge` -> `b_`); at one position the longest match of `prefixes` (any dict or set of prefix
    strings, only their keys/membership matter) wins. `None` if nothing matches.

    Shared by `measurement_prefix_findings()` below and `studio.core.portnaming.NamingRules.suggest()` - before
    TOOL-150 the two had separate copies of this rule and drifted apart: `suggest()` already looked after one
    component prefix, `measurement_prefix_findings()` only at the very start, so `HvsCellMod_pct_X` got no "not
    allowed" warning for a wrong type. A drift test (test_swc_yaml.py, test_portnaming.py) keeps both callers on
    this one function."""
    starts = [0]
    first = name.find("_")
    if first > 0:
        starts.append(first + 1)
    for start in starts:
        rest = name[start:]
        hits = [p for p in prefixes if rest.startswith(p) and len(rest) > len(p)]
        if hits:
            return max(hits, key=len)
    return None


def measurement_prefix_findings(swc: Swc, prefixes: dict | None = None) -> list[Message]:
    """TOOL-143: hook for the check of name prefixes against types (kalibrierung.md 8.2, decision E6 a). The prefix
    table of the user is not written down yet ({prefix: allowed platform types}); without one nothing is checked.
    A prefix never decides whether a signal is measurable - it only yields warnings.

    TOOL-150: the prefix is recognized with find_prefix() (start of the name, or after exactly one component
    prefix) - the same rule studio.core.portnaming.NamingRules.suggest() uses, so a measurement like
    `HvsCellMod_pct_X` is checked the same way a port of that name would be."""
    if not prefixes:
        return []
    found = []
    for i, m in enumerate(swc.measurements):
        prefix = find_prefix(m.name, prefixes)
        if prefix is not None:
            allowed = prefixes[prefix]
            if allowed and m.type not in allowed:
                found.append(Message("warning", f"measurement {m.name}: prefix {prefix} expects "
                                                f"{', '.join(allowed)}, the type is {m.type}", None,
                                     ("measurements", i, "type"), None))
    return found


def declared_measurements(folder: Path | str) -> tuple[Path, list[Measurement], list[Message]]:
    """TOOL-143: `measurements:` of the swc.yaml in `folder`, for rtegen - also for a Simulink component, whose
    swc.yaml is otherwise read leniently (declared_dimensions): the list has no other source than this file. The
    section itself is checked fully (messages with YAML path); symbolic dimensions are resolved from the init data.
    With an error the list is empty and the messages say why."""
    file = Path(folder) / FILE_NAME
    if not file.is_file():
        return file, [], []
    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as exc:
        return file, [], [Message("error", f"cannot read: {exc}", file)]
    if not isinstance(tree, dict) or not tree.get("measurements"):
        return file, [], []
    c = _Checker(file, tree)
    measurements = _check_measurements(c, tree)
    name = tree.get("name") if isinstance(tree.get("name"), str) else Path(folder).name
    swc = Swc(name, measurements=measurements)
    messages = c.messages + check_measurement_dimensions(swc, file, tree, Path(folder))
    if any(m.severity == "error" for m in messages):
        return file, [], messages
    return file, measurements, messages


def check_data(swc: Swc, file: Path | None, tree=None, data_file: Path | None = None) -> list[Message]:
    """Every parameter has a value of the right length in <Swc>_data.m; fills Parameter.values."""
    if not swc.parameters:
        return []
    import mdata  # tools/swc/mdata.py, next to this file

    c = _Checker(file, tree)
    if data_file is None or not data_file.is_file():
        c.error(("parameters",), f"{len(swc.parameters)} parameter(s) but no data file "
                                 f"{data_file.name if data_file else swc.name + '_data.m'} next to swc.yaml; "
                                 f"the values of calibration parameters stand only there")
        return c.messages
    try:
        values = mdata.load(data_file)
    except mdata.MDataError as exc:
        c.error(("parameters",), str(exc))
        return c.messages
    for i, p in enumerate(swc.parameters):
        try:
            p.values = values.get(p.name)
        except mdata.MDataError as exc:
            c.error((*(p.yaml_path or ("parameters", i)), "name"), str(exc))   # RTE-26: own axes shift i
            continue
        if len(p.values) != p.dim:
            c.error((*(p.yaml_path or ("parameters", i)), "dim"),
                    f"{data_file.name}:{values.line_of(p.name)}: {p.name} has {len(p.values)} value(s), dim is {p.dim}")
    return c.messages


def check_dimensions(swc: Swc, file: Path | None, tree=None, folder: Path | None = None) -> list[Message]:
    """RTE-25: resolves symbolic port dimensions from the init data of the component and checks dimension_source.

    `dimension: <expression>` must resolve (initdata.py, no MATLAB) - otherwise an error with the next step: write the
    number MATLAB gives and keep the expression as dimension_source. `dimension: <n>` with `dimension_source`: a
    different resolved value is an error (the init data changed since the import); one that cannot be resolved is a
    warning, the number is used.
    """
    c = _Checker(file, tree)
    for i, p in enumerate(swc.ports):
        if not p.dimension_source:
            continue
        if folder is None:
            if p.dimension is None:
                c.error(("ports", i, "dimension"), f"dimension '{p.dimension_source}' of port {p.name} needs the "
                                                   f"component folder to be resolved")
            continue
        found = resolve_dimension(p.dimension_source, folder, swc.name)
        if p.dimension is None:
            if found.value is None:
                c.error(("ports", i, "dimension"),
                        f"dimension '{p.dimension_source}' of port {p.name} cannot be resolved without MATLAB: "
                        f"{found.why}. Write the number MATLAB gives (e.g. `{p.dimension_source}` in the MATLAB "
                        f"command window after running {swc.name}_init.m) as dimension and keep the expression: "
                        f"dimension: <n>, dimension_source: {p.dimension_source}")
            elif found.value > MAX_DIMENSION:
                c.error(("ports", i, "dimension"), f"dimension of port {p.name}: {found.why}, more than "
                                                   f"{MAX_DIMENSION}")
            else:
                p.dimension = found.value
        elif found.value is None:
            c.warning(("ports", i, "dimension_source"),
                      f"dimension_source '{p.dimension_source}' of port {p.name} not checked without MATLAB "
                      f"({found.why}); dimension {p.dimension} is used")
        elif found.value != p.dimension:
            c.error(("ports", i, "dimension"),
                    f"port {p.name} has dimension {p.dimension}, but its source {found.why} - the init data changed; "
                    f"set dimension: {found.value} (or import the model again)")
    return c.messages


# ---- RTE-26: calibration objects (stage K1 of doc/architecture/kalibrierung.md) ----------------------------------
# One definition per calibration object in `parameters:` (decision E1 a). The RTE-03 keys keep their meaning; the
# keys below make a parameter a value, array, curve, map or shared axis with unit, engineering limits, safety class,
# description and value table. Checked here without MATLAB, so the Studio, rtegen and the host tests see the same
# findings with the YAML path.

def type_range(ptype: str, lsb=None, offset=0) -> tuple:
    """Physical range of a platform type with an optional fixed-point scaling, as Fractions (floats: +-max)."""
    from fractions import Fraction
    if ptype in FLOAT_LIMITS:
        top = Fraction(FLOAT_LIMITS[ptype])
        return -top, top
    if ptype == "boolean":
        lo, hi = 0, 1
    else:
        signed, bits = INTEGER_TYPES[ptype]
        lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    if lsb is None:
        return Fraction(lo), Fraction(hi)
    a = Fraction(str(lo)) * Fraction(str(lsb)) + Fraction(str(offset))
    b = Fraction(str(hi)) * Fraction(str(lsb)) + Fraction(str(offset))
    return min(a, b), max(a, b)


def _check_limits(c: _Checker, node: dict, path: Path_, ptype: str, lsb, offset):
    """(min, max) of a definition, None where not given; False after an error (min > max, outside the type)."""
    from fractions import Fraction
    lo, hi = node.get("min"), node.get("max")
    ok = True
    for key, value in (("min", lo), ("max", hi)):
        if value is not None and not _number(value):
            c.error((*path, key), f"{key} {value!r} must be a number (physical value)")
            ok = False
    if not ok:
        return False
    if lo is not None and hi is not None and lo > hi:
        c.error((*path, "min"), f"min {lo} is greater than max {hi}")
        return False
    if ptype in TYPES:
        first, last = type_range(ptype, lsb, offset)
        for key, value in (("min", lo), ("max", hi)):
            if value is not None and not first <= Fraction(str(value)) <= last:
                c.error((*path, key), f"{key} {value} is outside the range of {ptype}"
                                      f"{' with lsb ' + str(lsb) if lsb is not None else ''} "
                                      f"({float(first):g} .. {float(last):g})")
                ok = False
    return (lo, hi) if ok else False


def _check_text(c: _Checker, node: dict, path: Path_, key: str) -> str | None:
    value = node.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str) or "\n" in value:
        c.error((*path, key), f"{key} must be one line of text")
        return None
    return value.strip()


def _check_axis_spec(c: _Checker, spec, path: Path_, owner: str, letter: str, users: list, seen: dict):
    """One axis of a curve or map: a name (shared axis, resolved later) or own breakpoints; CalAxis or None."""
    if isinstance(spec, str):
        if not IDENTIFIER.match(spec):
            c.error(path, f"axis '{spec}' must be the name of a parameter with kind: axis")
            return None
        return CalAxis(letter, shared=spec)
    if not isinstance(spec, dict):
        c.error(path, "an axis is the name of a shared axis or own breakpoints {dim: n, type: ..., unit, min, max}")
        return None
    c.unknown_keys(spec, OWN_AXIS_KEYS, path)
    ok = True
    atype = spec.get("type")
    if atype not in TYPES or atype == "boolean":
        c.error((*path, "type"), f"type '{atype}' of the breakpoints must be a numeric platform type "
                                 f"({', '.join(t for t in TYPES if t != 'boolean')})")
        ok = False
    dim = spec.get("dim")
    if isinstance(dim, bool) or not isinstance(dim, int) or not 2 <= dim <= MAX_DIMENSION:
        c.error((*path, "dim"), f"dim {dim} must be the number of breakpoints, 2 .. {MAX_DIMENSION}")
        ok = False
    lsb, offset = spec.get("lsb"), spec.get("offset", 0)
    if lsb is not None and (not _number(lsb) or lsb <= 0 or atype not in INTEGER_TYPES):
        c.error((*path, "lsb"), f"lsb {lsb} must be a number > 0 on an integer type")
        ok = False
    if not _number(offset) or ("offset" in spec and lsb is None):
        c.error((*path, "offset"), "offset must be a number and needs lsb")
        ok = False
    limits = _check_limits(c, spec, path, atype, lsb, offset) if ok else False
    unit = _check_text(c, spec, path, "unit")
    description = _check_text(c, spec, path, "description")
    name = f"{owner}{AXIS_SUFFIX[letter]}"
    if len(name) > MAX_PARAMETER_NAME:
        c.error(path, f"own breakpoints {name} ({len(name)} characters) are longer than {MAX_PARAMETER_NAME} "
                      f"characters (variable of <Swc>_data.m, MATLAB namelengthmax)")
        ok = False
    if name in seen:
        c.error(path, f"own breakpoints of {owner} are the object {name}, but that name is already used at "
                      f"{_fmt(seen[name])}")
        ok = False
    if limits is False or unit is None or description is None or not ok:
        return None
    seen[name] = path
    own = Parameter(name, atype, dim, list(users), lsb, offset if lsb is not None else 0, kind="axis",
                    unit=unit, min=limits[0], max=limits[1], description=description, own_of=owner,
                    yaml_path=tuple(path))
    return CalAxis(letter, own=own)


def _check_calibration(c: _Checker, raw: dict, path: Path_, pname, ptype, dim, lsb, offset, users: list,
                       seen: dict) -> dict | None:
    """RTE-26 keys of one parameter: the extra fields of Parameter, or None after an error."""
    ok = True
    kind = raw.get("kind", "")
    if kind not in ("", *CAL_KINDS):
        c.error((*path, "kind"), f"kind '{kind}' must be one of {', '.join(CAL_KINDS)}")
        return None
    has_dim = "dim" in raw
    if kind == "value" and has_dim and dim != 1:
        c.error((*path, "dim"), f"a value has one element (dim {dim}); use kind: array for {dim} values")
        ok = False
    if kind in ("array", "axis") and (not has_dim or not isinstance(dim, int) or dim < 2):
        c.error((*path, "dim"), f"kind {kind} needs dim >= 2 ({'number of breakpoints' if kind == 'axis' else 'values'})")
        ok = False
    if kind in ("curve", "map") and has_dim:
        c.error((*path, "dim"), f"a {kind} takes its size from its axes; remove dim")
        ok = False
    if kind == "axis" and ptype == "boolean":
        c.error((*path, "type"), "breakpoints of an axis must be numeric, not boolean")
        ok = False
    for key, wanted in (("axis", "curve"), ("axes", "map")):
        if key in raw and kind != wanted:
            c.error((*path, key), f"{key} belongs to kind: {wanted} (this is kind: {kind or 'value/array'})")
            ok = False
    axes: list[CalAxis] = []
    if kind == "curve":
        if "axis" not in raw:
            c.error((*path, "kind"), "a curve needs axis: <shared axis> or axis: {dim: n, type: ...}")
            ok = False
        elif pname is not None:
            axis = _check_axis_spec(c, raw["axis"], (*path, "axis"), pname, "x", users, seen)
            ok = ok and axis is not None
            axes = [axis] if axis is not None else []
    if kind == "map":
        spec = raw.get("axes")
        if not isinstance(spec, dict) or set(spec) != set(AXIS_LETTERS):
            c.error((*path, "axes" if "axes" in raw else "kind"),
                    "a map needs axes: {x: <axis>, y: <axis>} (x: rows, y: columns; each shared or own)")
            ok = False
        elif pname is not None:
            for letter in AXIS_LETTERS:
                axis = _check_axis_spec(c, spec[letter], (*path, "axes", letter), pname, letter, users, seen)
                ok = ok and axis is not None
                if axis is not None:
                    axes.append(axis)
    limits = _check_limits(c, raw, path, ptype, lsb, offset) if ptype in TYPES else False
    if limits is False:
        ok = False
        limits = (None, None)
    unit = _check_text(c, raw, path, "unit")
    description = _check_text(c, raw, path, "description")
    if unit is None or description is None:
        ok = False
    safety = raw.get("safety", "qm")
    if safety in LATER_SAFETY_CLASSES:
        c.error((*path, "safety"), f"safety {safety}: ASIL C and D are not supported yet (target ASIL B; "
                                   f"kalibrierung.md 4.4)")
        ok = False
    elif safety not in SAFETY_CLASSES:
        c.error((*path, "safety"), f"safety '{safety}' must be one of {', '.join(SAFETY_CLASSES)}")
        ok = False
    elif safety != "qm":
        missing = [k for k in ("min", "max") if raw.get(k) is None]
        if missing:
            c.error((*path, "safety"), f"safety {safety} needs {' and '.join(missing)}: the limits are the "
                                       f"specification of a safety-relevant calibration value (SYS-76)")
            ok = False
        for axis in axes:
            if axis.own is not None and (axis.own.min is None or axis.own.max is None):
                c.error(axis.own.yaml_path, f"own breakpoints of {pname} (safety {safety}) need min and max")
                ok = False
    for axis in axes:
        if axis.own is not None:
            axis.own.safety = safety if safety in SAFETY_CLASSES else "qm"
    table = _check_value_table(c, raw, path, ptype, lsb, kind)
    if table is None:
        ok = False
    if not ok:
        return None
    return {"kind": kind, "unit": unit, "min": limits[0], "max": limits[1], "safety": safety,
            "description": description, "value_table": table, "axes": axes, "yaml_path": tuple(path)}


def _check_value_table(c: _Checker, raw: dict, path: Path_, ptype, lsb, kind) -> dict | None:
    """value_table: {stored value: text} of an integer or boolean parameter without scaling (COMPU_VTAB)."""
    table = raw.get("value_table")
    if table is None:
        return {}
    where = (*path, "value_table")
    if not isinstance(table, dict) or not table:
        c.error(where, "value_table must map stored values to texts, e.g. {0: Off, 1: On}")
        return None
    if ptype not in INTEGER_TYPES and ptype != "boolean":
        c.error(where, f"a value table needs an integer or boolean type, not {ptype}")
        return None
    if lsb is not None:
        c.error(where, "a value table and a fixed-point scaling (lsb) exclude each other")
        return None
    if kind not in ("", "value", "array"):
        c.error(where, f"a value table belongs to a value or an array, not to kind {kind}")
        return None
    first, last = type_range(ptype)
    texts: dict = {}
    for key, text_ in table.items():
        if isinstance(key, bool) or not isinstance(key, int) or not first <= key <= last:
            c.error((*where, key), f"value {key!r} must be a whole number of {ptype} ({first} .. {last})")
            return None
        if not isinstance(text_, str) or not text_.strip() or '"' in text_ or "\n" in text_:
            c.error((*where, key), f"text of value {key} must be one line without double quotes")
            return None
        if text_.strip() in texts:
            c.error((*where, key), f"text '{text_.strip()}' is already used for value {texts[text_.strip()]}")
            return None
        texts[text_.strip()] = key
    return {int(k): str(v).strip() for k, v in sorted(table.items())}


SAFETY_RANK = {name: i for i, name in enumerate(SAFETY_CLASSES)}


def _resolve_calibration_axes(c: _Checker, parameters: list, raw_parameters: list):
    """Shared axes of curves and maps by name; shape and dim of every curve and map; unused shared axes."""
    by_name = {p.name: p for p in parameters}
    raw_kinds = {r.get("name"): r.get("kind", "") for r in raw_parameters if isinstance(r, dict)}
    used = set()
    for p in parameters:
        if p.kind not in ("curve", "map"):
            continue
        shape, ok = [], True
        for axis in p.axes:
            path = (*p.yaml_path, "axis") if p.kind == "curve" else (*p.yaml_path, "axes", axis.letter)
            if axis.own is not None:
                shape.append(axis.own.dim)
                continue
            target = by_name.get(axis.shared)
            if target is None:
                ok = False
                if raw_kinds.get(axis.shared) != "axis":        # an axis with errors of its own is reported there
                    known = ", ".join(q.name for q in parameters if q.kind == "axis" and not q.own_of) or "none"
                    c.error(path, f"{p.name}: unknown axis '{axis.shared}' (shared axes of this component: {known}; "
                                  f"or own breakpoints {{dim: n, type: ...}})")
                continue
            if target.kind != "axis" or target.own_of:
                c.error(path, f"{p.name}: '{axis.shared}' is no shared axis (kind {target.cal_kind}); give it kind: axis")
                ok = False
                continue
            used.add(target.name)
            if SAFETY_RANK.get(target.safety, 0) < SAFETY_RANK.get(p.safety, 0):
                c.error(path, f"{p.name} (safety {p.safety}) uses axis {target.name} of safety {target.safety}; "
                              f"the breakpoints are part of the value, give the axis at least {p.safety}")
                ok = False
            shape.append(target.dim)
        if ok:
            p.shape = tuple(shape)
            p.dim = 1
            for n in shape:
                p.dim *= n
    for p in parameters:
        if p.kind == "axis" and not p.own_of and p.name not in used:
            c.warning((*p.yaml_path, "name"), f"shared axis {p.name} is used by no curve or map")


def check_calibration_data(parameters: list, file: Path | None, tree=None, data_file: Path | None = None) -> list:
    """RTE-26: default values of the calibration objects (from <Swc>_data.m, Parameter.values filled by check_data).

    A map is a matrix with one row per X breakpoint; breakpoints are strictly ascending; values stay inside min/max
    and, with a value table, are entries of it. These values are the defaults of the component type; the dataset of
    the project (stage K2) will be checked by the same rules.
    """
    import mdata  # tools/swc/mdata.py, next to this file

    cal = [p for p in parameters if p.calibration]
    if not cal or data_file is None or not Path(data_file).is_file():
        return []
    c = _Checker(file, tree)
    try:
        data = mdata.load(data_file)
    except mdata.MDataError as exc:
        c.error(("parameters",), str(exc))
        return c.messages
    name = Path(data_file).name
    for p in cal:
        entry = data.entries.get(p.name)
        if entry is None or entry.values is None:
            continue                          # reported by check_data
        where = f"{name}:{entry.line}"
        path = p.yaml_path or ("parameters",)
        values = list(entry.values)
        if p.kind == "map" and len(p.shape) == 2:
            if entry.shape != tuple(p.shape):
                said = "a scalar" if not entry.shape else f"a {entry.shape[0]} x {entry.shape[1]} matrix"
                c.error((*path, "axes"), f"{where}: {p.name} is {said}; the axes give {p.shape[0]} x {p.shape[1]} "
                                         f"(one row per X breakpoint, one column per Y breakpoint)")
                continue
        elif p.kind == "curve" and len(values) != p.dim:
            c.error((*path, "axis"), f"{where}: {p.name} has {len(values)} value(s), its axis has {p.dim} "
                                     f"breakpoints")
            continue
        if p.min is not None or p.max is not None:
            lo = p.min if p.min is not None else float("-inf")
            hi = p.max if p.max is not None else float("inf")
            outside = [(i, v) for i, v in enumerate(values) if not lo <= v <= hi]
            if outside:
                i, v = outside[0]
                at = f"[{i + 1}]" if len(values) > 1 else ""
                c.error((*path, "min" if v < lo else "max"),
                        f"{where}: {p.name}{at} = {v} is outside min {p.min} .. max {p.max}"
                        + (f" ({len(outside)} values)" if len(outside) > 1 else ""))
        if p.kind == "axis":
            for i in range(1, len(values)):
                if values[i] <= values[i - 1]:
                    c.error(path, f"{where}: breakpoints of axis {p.name} are not strictly ascending "
                                  f"({values[i - 1]} at {i}, {values[i]} at {i + 1})")
                    break
        if p.value_table:
            unknown = [v for v in values if v not in p.value_table]
            if unknown:
                c.error((*path, "value_table"), f"{where}: {p.name} = {unknown[0]} is no entry of its value table "
                                                f"({', '.join(str(k) for k in p.value_table)})")
    return c.messages


def declared_calibration(folder: Path | str) -> tuple[Path, object, list, list]:
    """RTE-26: (file, tree, parameters, messages) of the `parameters:` section of a component's swc.yaml.

    For a Simulink component rtegen compares these definitions with the ARXML Embedded Coder wrote (type, number
    of values, scaling) and takes kind, axes, unit, limits, safety class, description and value table from here
    (decision E1 a: swc.yaml is the master of the calibration definition). Only this section and its values are
    checked, not the ports (after swc_create the model is their master). No swc.yaml or no parameters: nothing.
    """
    file = Path(folder) / FILE_NAME
    if not file.is_file():
        return file, None, [], []
    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as exc:
        return file, None, [], [Message("error", f"cannot read: {exc}", file)]
    if not isinstance(tree, dict) or not tree.get("parameters"):
        return file, tree, [], []
    c = _Checker(file, tree)
    raw_runnables = tree.get("runnables") if isinstance(tree.get("runnables"), list) else []
    runnable_names = {r.get("name") for r in raw_runnables if isinstance(r, dict) and isinstance(r.get("name"), str)}
    parameters = _check_parameters(c, tree, runnable_names, {})
    messages = list(c.messages)
    if any(m.severity == "error" for m in messages):
        return file, tree, [], messages
    name = tree.get("name") if isinstance(tree.get("name"), str) else Path(folder).name
    swc = Swc(name, parameters=parameters)
    data_file = data_file_of(Path(folder), name)
    messages += check_data(swc, file, tree, data_file)
    if not any(m.severity == "error" for m in messages):
        messages += check_calibration_data(parameters, file, tree, data_file)
    return file, tree, parameters, messages


def _fmt(path: Path_) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def load(file: Path | str, check_folder: bool = True) -> tuple[Swc, list[Message]]:
    """Reads and checks swc.yaml; raises SwcErrors with all messages when there is an error."""
    file = Path(file)
    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as exc:
        raise SwcErrors([Message("error", f"cannot read: {exc}", file)]) from exc
    swc, messages = check(tree, file, tree, file.resolve().parent.name if check_folder else None)
    if swc is None:
        raise SwcErrors(messages)
    messages = messages + check_data(swc, file, tree, data_file_of(file.resolve().parent, swc.name))
    messages = messages + check_dimensions(swc, file, tree, file.resolve().parent)     # RTE-25
    if not any(m.severity == "error" for m in messages):
        messages = messages + check_calibration_data(swc.parameters, file, tree,
                                                     data_file_of(file.resolve().parent, swc.name))   # RTE-26
    messages = messages + check_measurement_dimensions(swc, file, tree, file.resolve().parent)   # TOOL-143
    messages = messages + check_types_file(swc, file, tree, file.resolve().parent)               # RTE-15
    if any(m.severity == "error" for m in messages):
        raise SwcErrors(messages)
    return swc, messages


def implementation_of(folder: Path | str) -> tuple[str, list[Message]]:
    """Implementation of the component in `folder` (RTE-20): (implementation, messages), without the full check.

    No swc.yaml or no key: simulink - a Simulink component may have no swc.yaml at all (DemoSwc), and after swc_create
    its swc.yaml is only the description the model was created from, so rtegen must not check more of it than this
    one key. An unreadable file or an unknown value is an error message (file, YAML path, line).
    """
    file = Path(folder) / FILE_NAME
    if not file.is_file():
        return DEFAULT_IMPLEMENTATION, []
    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as exc:
        return DEFAULT_IMPLEMENTATION, [Message("error", f"cannot read: {exc}", file)]
    value = tree.get("implementation", DEFAULT_IMPLEMENTATION) if isinstance(tree, dict) else DEFAULT_IMPLEMENTATION
    if value not in IMPLEMENTATIONS:
        path = ("implementation",)
        return DEFAULT_IMPLEMENTATION, [Message("error", f"implementation '{value}' must be one of "
                                                         f"{', '.join(IMPLEMENTATIONS)}", file, path,
                                                _line_of(tree, path))]
    return value, []


@dataclass
class DeclaredDimension:
    """Dimension swc.yaml gives a port of a Simulink component (declared_dimensions)."""
    port: str
    value: int | None               # None: symbolic and not resolved without MATLAB
    source: str                     # expression or ''
    path: Path_
    line: int | None


def declared_dimensions(folder: Path | str) -> tuple[Path, list[DeclaredDimension]]:
    """RTE-25: port dimensions of the swc.yaml of a Simulink component, read leniently (no full check).

    After swc_create the model is the master of a Simulink component and its swc.yaml only the description it came
    from (implementation_of); rtegen compares these numbers with the ARXML Embedded Coder wrote. Ports without a
    readable name or dimension are left out; symbolic dimensions are resolved from the init data where possible.
    """
    file = Path(folder) / FILE_NAME
    if not file.is_file():
        return file, []
    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return file, []
    raw_ports = tree.get("ports") if isinstance(tree, dict) else None
    name = tree.get("name") if isinstance(tree, dict) else None
    found = []
    for i, raw in enumerate(raw_ports if isinstance(raw_ports, list) else []):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or "dimension" not in raw:
            continue
        c = _Checker(file, tree)
        value, source = _check_dimension(c, raw, ("ports", i))
        if value is False:
            continue
        if value is None and isinstance(name, str):
            value = resolve_dimension(source, Path(folder), name).value
        found.append(DeclaredDimension(raw["name"], value, source, ("ports", i, "dimension"),
                                       _line_of(tree, ("ports", i, "dimension"))))
    return file, found


@dataclass
class DeclaredRunnable:
    """A runnable as the swc.yaml of a Simulink component names it (declared_description); None: not readable."""
    name: str
    period_ms: object
    index: int


@dataclass
class DeclaredPort:
    """A port as the swc.yaml of a Simulink component names it (declared_description), values as written."""
    name: str
    direction: object
    type: object                     # as written; RTE-15: the platform type of a named enumeration/fixed-point type
    runnables: object                # list of names, or what stands there
    index: int


@dataclass
class DeclaredDescription:
    """TOOL-166: what the swc.yaml of a Simulink component says about the model, read leniently.

    runnables / ports are None when the section is missing (or no list): a section that is not written is not
    compared. measurements: the names under `measurements:` (empty without the section)."""
    file: Path
    tree: object
    name: str | None
    runnables: list[DeclaredRunnable] | None
    ports: list[DeclaredPort] | None
    measurements: list[str]
    has_measurements: bool = False

    def line(self, path: Path_) -> int | None:
        return _line_of(self.tree, path)


def declared_description(folder: Path | str) -> DeclaredDescription | None:
    """TOOL-166: runnables, ports and measurement names of the swc.yaml of a Simulink component, without the full
    check (after swc_create the model is the master, implementation_of). rtegen compares them with the ARXML and the
    code Embedded Coder wrote (rtegen.description_findings). None without a swc.yaml or when it cannot be read or is
    a C component (implementation: c - there the swc.yaml is the master and fully checked). Entries without a
    readable name are left out; everything else is passed on as written."""
    file = Path(folder) / FILE_NAME
    if not file.is_file():
        return None
    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return None
    if not isinstance(tree, dict) or tree.get("implementation", DEFAULT_IMPLEMENTATION) != DEFAULT_IMPLEMENTATION:
        return None

    def entries(key: str):
        raw = tree.get(key)
        if not isinstance(raw, list):
            return None
        return [(i, e) for i, e in enumerate(raw) if isinstance(e, dict) and isinstance(e.get("name"), str)]

    runnables = entries("runnables")
    ports = entries("ports")
    measurements = entries("measurements")
    # RTE-15: the ARXML shows an enumeration or a fixed-point type by its platform type (what rtegen compares);
    # a port of such a named type is declared with that platform type, a record keeps its name
    stored = {e["name"]: (e.get("base") or (DEFAULT_ENUM_BASE if e.get("kind") == "enum" else None))
              for _, e in entries("types") or [] if e.get("kind") in ("enum", "fixed")}

    def platform(value):
        return stored.get(value) or value if isinstance(value, str) else value
    return DeclaredDescription(
        file, tree, tree.get("name") if isinstance(tree.get("name"), str) else None,
        None if runnables is None else [DeclaredRunnable(e["name"], e.get("period_ms"), i) for i, e in runnables],
        None if ports is None else [DeclaredPort(e["name"], e.get("direction"), platform(e.get("type")),
                                                 e.get("runnables"), i) for i, e in ports],
        [e["name"] for _, e in measurements or []], "measurements" in tree)


def _write_types(swc: Swc, file: Path, check_only: bool) -> int:
    """RTE-15: `types` command - writes <Swc>_types.m next to swc.yaml (or with --check only compares)."""
    if swc.implementation != DEFAULT_IMPLEMENTATION:
        print(f"{file}: error: implementation '{swc.implementation}' has no model and no named types",
              file=sys.stderr)
        return 1
    target = file.resolve().parent / swc.types_file
    if not swc.types:
        print(f"{file}: no types: - nothing to write" + (f" ({target.name} is left as it is)"
                                                         if target.is_file() else ""))
        return 0
    text = types_m(swc)
    current = target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.is_file() else None
    if check_only:
        print(f"{target}: " + ("current" if current == text else "missing" if current is None else "differs"))
        return 0 if current == text else 1
    if current != text:
        target.write_text(text, encoding="utf-8", newline="\n")
    print(f"{target}: {len(swc.types)} types" + ("" if current != text else " (unchanged)"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "json", "types"):
        p = sub.add_parser(command)
        p.add_argument("file", type=Path, help="swc.yaml or the component folder")
        p.add_argument("--no-folder-check", action="store_true", help="name may differ from the folder name")
        if command == "json":
            p.add_argument("-o", "--output", type=Path, help="write the JSON here instead of stdout")
        if command == "types":
            # RTE-15: <Swc>_types.m next to swc.yaml from its types:
            p.add_argument("--check", action="store_true", help="only compare; exit code 1 if the file differs")
    args = parser.parse_args(argv)
    file = args.file / FILE_NAME if args.file.is_dir() else args.file
    try:
        swc, messages = load(file, check_folder=not args.no_folder_check)
    except SwcErrors as exc:
        for m in exc.messages:
            print(m, file=sys.stderr)
        return 1
    if args.command == "types":
        return _write_types(swc, file, args.check)
    for m in messages:
        print(m, file=sys.stderr)
    if args.command == "json":
        if swc.implementation != DEFAULT_IMPLEMENTATION:
            # swc_create.m asks for the JSON: a hand-written component has no model (RTE-20)
            print(f"{file}: error: implementation '{swc.implementation}' has no Simulink model; create the C code "
                  f"skeleton with python tools/swc/swc_skeleton.py create {file.parent.as_posix()}", file=sys.stderr)
            return 1
        text = json.dumps(swc.to_json(), indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8", newline="\n")
        else:
            sys.stdout.write(text)
    else:
        print(f"{file}: {swc.name} ({swc.implementation}), {len(swc.runnables)} runnables, {len(swc.ports)} "
              f"ports, {len(swc.parameters)} parameters"
              + (f", {len(swc.measurements)} measurements" if swc.measurements else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
