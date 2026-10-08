"""New board (TOOL-72): `boards/<name>/board.yaml` for an own ECU printed circuit board.

Core function without Qt, used by the command line (`nxconfig board new`) and neXtBSW Studio ("Neues Board
anlegen"). Why it exists: a new ECU project is almost never one of the evaluation boards of the tree (feedback of
the user 2026-09-22), so the project wizard of TOOL-59 needs a third start besides "board of the tree" and "copy".

What the wizard asks and what it derives
----------------------------------------
Decision of the user 2026-09-22 (tool72-board = "gefuehrt"): the wizard asks **signal by signal** (CAN, UART, SPI,
I2C, digital inputs and outputs, ADC, PWM) and offers per signal only the pins that can carry that function on the
**chosen package**. Everything it offers comes from the two read-only levels of the configuration (architecture
section 4):

  - the derivative `mcudb/<family>/<derivative>.yaml`: which instances exist (`instances`), which packages
    (`derivative.packages`), which ADC channels (`channel_pattern`), how many timer channels;
  - the pin multiplexing of the package `<derivative>_<package>.pinmux.yaml`: signal -> pin -> selector.

Nothing here invents a hardware fact. What the wizard cannot know - the fitted crystal, the supply, the reference
voltage at VREFH, the active level of a LED - is asked and written with `verify: true`, exactly like a board that
was written by hand from a quick start guide (`boards/s32k116evb/board.yaml`).

Where a row comes from (tool72-schaltplan = "vorsehen")
------------------------------------------------------
Decision of the user 2026-09-22: the import from a schematic netlist is a task of its own (TOOL-76, KiCad only by
decision of 2026-09-28) and lives in `netlist.py`. What is built here is the interface it uses: every row of the pin
table carries the `source` it came from (`PinSource`: `MANUAL` or `KICAD`) plus a free `origin` that says where
exactly (the wizard step, or the file and the net(s) of the netlist). The importer fills the same `BoardDraft`
through the same constructors and checks, and the generated board file states per row where it comes from.

What the family port template needs (PLAT-95)
---------------------------------------------
Since PLAT-95 the family port template says in `platforms/<family>/port/port.yaml` what it needs of a project, its
board and its derivative. This module reads that manifest (`checks.read_port_manifest`) instead of writing the
values down a second time: `requirements()` turns the `board:` group into the board keys the draft has to carry
(s32k1xx: `adc_reference_mv`) and the `adc_internal:` group into the internal ADC channels the derivative has to
offer, so the wizard can say *before* anything is written that a project on this board will not compile.

What is written
---------------
`create()` writes, like the project wizard, into a staging folder and moves it into place in one step:

  <boards>/<name>/board.yaml          the board file with comments, a source block and `verify: true`
  <boards>/<name>/README.md           what the board is, what is unconfirmed, how to go on
  <workspace>/ecu/platforms/<name>/Options.cmake    the board port (feature switches, ARCH-04/05)

Since **ARCH-58** the board port no longer carries the feature switches - they are derived from project, board and
the statement of the family (`tools/nxconfig/features.py`), so a board **builds without this file**. What the
wizard writes is the documented hook for a justified exception of this board, next to the board itself (same
workspace root), so a customer board needs no change in the pinned tree (ARCH-57). `--no-port` leaves it out; the
board builds either way.

Afterwards `create()` proves the result: it plans an empty project on the new board in a scratch folder
(`newproject.plan_empty`) and returns the findings of `nxconfig check` on it. A board that cannot carry a project
is reported with file, line and YAML path instead of being handed over as "done".
"""

import fnmatch
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import config as nxcfg
from . import genlicense
from . import newproject
from .messages import Message

BOARD_FILE = "board.yaml"
README_FILE = "README.md"
BOARDS_DIR = "boards"
PORT_OPTIONS = Path("ecu") / "platforms"
PORT_FILE = "Options.cmake"

# Board name: the folder `boards/<name>`, `board.name` (model.Name) and the board port folder use it, and CMake
# passes it through as NX_BOARD - so it follows the strictest of the three.
NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")

# A pin that carries one of these functions is not free to use: switching it to a GPIO cuts off the debugger, the
# reset line, the crystal or the boot strapping. The wizard offers such a pin, but never silently.
SPECIAL_PIN_KINDS = (
    ("debug", re.compile(r"JTAG|SWD|SWCLK|SWDIO|SWO|SYS_J|TRACE|TCK|TMS|TDI|TDO", re.IGNORECASE),
     "the debugger reaches the MCU through it"),
    ("reset", re.compile(r"RESET|NMI", re.IGNORECASE), "reset and NMI of the MCU are wired to it"),
    # OSC at the start of a name or after an underscore: EXTAL/XTAL (s32k1xx), OSCIN (rm4x), RCC_OSC_IN and
    # RCC_OSC32_IN (stm32; TOOL-188 - `\bOSC` missed them, "_" is a word character), but not ...LPOSC... in a word
    ("oscillator", re.compile(r"EXTAL|XTAL|(?<![A-Z0-9])OSC", re.IGNORECASE), "the crystal of the board hangs on it"),
    ("boot", re.compile(r"BOOT", re.IGNORECASE), "it is sampled at reset and decides where the MCU starts"),
)

# Kinds of entry the wizard collects, in the order it asks for them. `path` is the YAML list of the board file the
# rows end up in; `pins` names the pin roles of one entry. The same table drives the board page of the Studio
# (add and remove an entry), so both offer exactly the same thing.
CAN, UART, SPI, I2C, DIO_IN, DIO_OUT, ADC, PWM, CAPTURE = (
    "can", "uart", "spi", "i2c", "dio_in", "dio_out", "adc", "pwm", "input_capture")
FLEXIO = "flexio"                               # instance class of a FlexIO in the derivative (mcudb)


@dataclass(frozen=True)
class EntryKind:
    """One kind of board entry: which instance class it needs, which pins, and where it is written."""

    id: str
    title: str                                  # German, the Studio shows it
    path: tuple                                 # YAML path of the list in board.yaml
    instance_class: str = ""                    # derivative instances.<x>.class the entry runs on ("": none)
    pins: tuple = ()                            # pin roles of one entry (role, function suffix)
    single: bool = False                        # one pin only (DIO, capture)
    # PLAT-103: further instance classes the entry may run on. An I2C bus runs on an I2C instance or on a FlexIO
    # (the same pair checks._board accepts); on a FlexIO every pin is a data pin D<n> (`flexio_pin`).
    also_classes: tuple = ()

    @property
    def needs_instance(self) -> bool:
        return bool(self.instance_class)

    @property
    def instance_classes(self) -> tuple:
        return ((self.instance_class,) if self.instance_class else ()) + tuple(self.also_classes)


ENTRY_KINDS = (
    EntryKind(CAN, "CAN-Bus", ("can",), "can", (("rx", "RX"), ("tx", "TX"))),
    EntryKind(UART, "UART (Konsole, LIN)", ("uart",), "uart", (("rx", "RX"), ("tx", "TX"))),
    EntryKind(SPI, "SPI-Bus", ("spi",), "spi",
              (("sck", "SCK"), ("sin", "SIN"), ("sout", "SOUT"), ("pcs", "PCS"))),
    EntryKind(I2C, "I2C-Bus", ("i2c",), "i2c", (("sda", "SDA"), ("scl", "SCL")), also_classes=(FLEXIO,)),
    EntryKind(DIO_IN, "Digitaler Eingang", ("dio", "inputs"), "", (("pin", "GPIO"),), single=True),
    EntryKind(DIO_OUT, "Digitaler Ausgang", ("dio", "outputs"), "", (("pin", "GPIO"),), single=True),
    EntryKind(ADC, "Analogeingang", ("adc",), "adc", (("pin", "SE"),), single=True),
    EntryKind(PWM, "PWM-Gruppe", ("pwm",), "timer", (("pin", "CH"),)),
    EntryKind(CAPTURE, "Input Capture", ("input_capture",), "timer", (("pin", "CH"),), single=True),
)
KIND_BY_ID = {k.id: k for k in ENTRY_KINDS}

# PLAT-103: whether the family port can drive an I2C bus on an instance. The rule itself is checks.I2C_DRIVERS
# (TOOL-120) and is read from there, never repeated here, so the wizard and `nxconfig check` cannot disagree.
DRIVER, NO_DRIVER, UNCHECKED = "driver", "no-driver", "unchecked"

DEBUG_INTERFACES = ("SWD", "JTAG", "cJTAG")
# Probes the flash targets of the root CMakeLists.txt know; anything else is allowed but gets no flash target.
DEBUG_PROBES = ("J-Link", "J-Link EDU Mini", "OpenSDA", "ST-LINK/V2-1", "CMSIS-DAP", "XDS110")


class NewBoardError(Exception):
    """Invalid request (name, folder, derivative, package): nothing was written."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# ----------------------------------------------------------------------------------------------- derivative db


@dataclass(frozen=True)
class DerivativeChoice:
    """One derivative of the database the board can be built on."""

    name: str
    file: Path
    family: str = ""
    core: str = ""
    packages: tuple = ()
    problem: str = ""                           # why it cannot be used (empty: fine)

    @property
    def usable(self) -> bool:
        return not self.problem and bool(self.packages)


def _load_yaml(file: Path):
    try:
        return YAML(typ="safe").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return None


def list_derivatives(mcudb: Path = nxcfg.MCUDB) -> list[DerivativeChoice]:
    """Derivatives of the database, sorted by name. Pin multiplexing files are not read here (they are big)."""
    result = []
    for file in sorted(Path(mcudb).glob("*/*.yaml")):
        if file.name.endswith(".pinmux.yaml"):
            continue
        data = _load_yaml(file)
        section = data.get("derivative") if isinstance(data, dict) else None
        if not isinstance(section, dict) or not section.get("name"):
            continue
        packages = section.get("packages") or []
        problem = "" if isinstance(packages, list) and packages else "the derivative names no package"
        result.append(DerivativeChoice(str(section["name"]), file, str(section.get("family") or ""),
                                       str(section.get("core") or ""),
                                       tuple(str(p) for p in packages) if isinstance(packages, list) else (),
                                       problem))
    return sorted(result, key=lambda d: d.name)


def find_derivative(name: str, mcudb: Path = nxcfg.MCUDB) -> DerivativeChoice:
    """The derivative of that name; raises NewBoardError with the list of known ones."""
    known = list_derivatives(mcudb)
    found = next((d for d in known if d.name == name), None)
    if found is None:
        raise NewBoardError([f"derivative {name!r} not found in {mcudb} "
                             f"(known: {', '.join(d.name for d in known) or '-'})"])
    if found.problem:
        raise NewBoardError([f"derivative {name}: {found.problem}"])
    return found


# ----------------------------------------------------------------------------------------------- package


@dataclass(frozen=True)
class PinOption:
    """One pin a function can sit on, with the reason why it is not free to use."""

    pin: str
    selector: object = None                     # PORT_PCR.MUX value (or named selectors), for the reader
    special: str = ""                           # "" or "debug: the debugger reaches the MCU through it"
    taken_by: str = ""                          # "" or the entry that already uses the pin
    function: str = ""                          # PLAT-103: set where one row offers several functions
    flexio_pin: int | None = None               # PLAT-103: FlexIO data pin D<n> of `function` (FlexIO_D<n>)

    @property
    def free(self) -> bool:
        return not self.taken_by


@dataclass
class Package:
    """Pin multiplexing of one derivative in one package, read without a project (the Studio needs it early)."""

    derivative: DerivativeChoice
    package: str
    signals: dict = field(default_factory=dict)
    complete: bool = True
    instances: dict = field(default_factory=dict)       # instance -> plain dict of the derivative entry

    @property
    def pins(self) -> set:
        return {pin for pins in self.signals.values() for pin in pins}

    def functions(self, pin: str) -> list[str]:
        return sorted(s for s, pins in self.signals.items() if pin in pins)

    def special(self, pin: str) -> str:
        """Why this pin is not free to use ("" if it is): debug, reset, oscillator or boot function on it."""
        for kind, pattern, why in SPECIAL_PIN_KINDS:
            hits = sorted(s for s in self.functions(pin) if pattern.search(s))
            if hits:
                return f"{kind} pin ({', '.join(hits)}): {why}"
        return ""

    def instances_of(self, instance_class) -> list[str]:
        """Instances of one class, or of any class of a tuple, sorted by name."""
        classes = (instance_class,) if isinstance(instance_class, str) else tuple(instance_class)
        return sorted(n for n, x in self.instances.items() if x.get("class") in classes)

    def class_of(self, instance: str) -> str:
        return str((self.instances.get(instance) or {}).get("class") or "")

    def flexio_functions(self, instance: str) -> list[tuple]:
        """(n, function) of every FlexIO data pin of `instance` this package has, e.g. (6, "FlexIO_D6").

        The name is the one of the pin multiplexing tables and of checks.bus_signal: `<instance>_D<n>`.
        """
        pattern = re.compile(rf"{re.escape(instance)}_D(\d+)")
        return sorted((int(m.group(1)), s) for s in self.signals for m in [pattern.fullmatch(s)] if m)

    def signal_name(self, instance: str, suffix: str) -> str:
        return f"{instance}_{suffix}" if instance else suffix

    def pin_options(self, function: str, taken: dict | None = None) -> list[PinOption]:
        """Every pin that can carry `function` in this package, with special and double-use marked."""
        taken = taken or {}
        return [PinOption(pin, selector, self.special(pin), taken.get(pin, ""))
                for pin, selector in sorted((self.signals.get(function) or {}).items(), key=_pin_key)]

    def channels(self, instance: str, pattern: str = "") -> list[tuple]:
        """Channel functions of an ADC or timer instance: (channel, function), e.g. ("SE3", "ADC0_SE3").

        The channel names are a fact of the derivative, not of this file: an ADC instance states them as
        `channel_pattern` (s32k1xx `SE<n>`, another family `IN<n>`), a timer channel is `CH<n>` by the naming of
        the pin multiplexing tables. Without a pattern only `CH<n>` counts, so a timer never offers ADC channels.
        """
        prefix = f"{instance}_"
        out = []
        for signal in self.signals:
            if not signal.startswith(prefix):
                continue
            rest = signal[len(prefix):]
            if re.fullmatch(pattern or r"CH\d+", rest):
                out.append((rest, signal))
        return sorted(out, key=lambda t: (re.sub(r"\d+$", "", t[0]), _trailing_number(t[0])))

    def channel_pattern(self, instance: str) -> str:
        """`channel_pattern` of the instance in the derivative ("" if it has none, e.g. a timer)."""
        return str((self.instances.get(instance) or {}).get("channel_pattern") or "")


def _trailing_number(text: str) -> int:
    match = re.search(r"(\d+)$", text)
    return int(match.group(1)) if match else -1


def _pin_key(item):
    """Pins in the order of the tables: port letter first, then the number (PTA2 before PTA10)."""
    pin = item[0] if isinstance(item, tuple) else item
    match = re.match(r"^(.*?)(\d+)$", pin)
    return (match.group(1), int(match.group(2))) if match else (pin, -1)


def load_package(derivative: DerivativeChoice, package: str) -> tuple[Package | None, list[Message]]:
    """The pin multiplexing table of `package`; None with messages if the derivative has no data for it."""
    messages: list[Message] = []
    data = _load_yaml(derivative.file)
    if not isinstance(data, dict):
        messages.append(Message("error", f"derivative file {derivative.file.name} cannot be read",
                                derivative.file))
        return None, messages
    if package not in derivative.packages:
        messages.append(Message("error", f"{derivative.name} has no package {package!r} "
                                         f"(it has {', '.join(derivative.packages) or '-'})",
                                derivative.file, ("derivative", "packages")))
        return None, messages
    instances = {str(n): dict(x) if isinstance(x, dict) else {}
                 for n, x in (data.get("instances") or {}).items()}
    ref = (data.get("pinmux") or {}).get(package)
    if not isinstance(ref, dict):
        messages.append(Message("warning", f"{derivative.name} has no pin multiplexing for {package}: the wizard "
                                           "can offer no pin and cannot check a pin function",
                                derivative.file, ("pinmux", package)))
        return Package(derivative, package, {}, False, instances), messages
    if ref.get("signals") is not None:
        signals = ref["signals"]
    else:
        file = derivative.file.parent / str(ref.get("file"))
        if not file.is_file():
            messages.append(Message("error", f"pin multiplexing file {ref.get('file')} not found next to "
                                             f"{derivative.file.name}", derivative.file,
                                    ("pinmux", package, "file")))
            return None, messages
        table = _load_yaml(file)
        signals = table.get("signals") if isinstance(table, dict) else None
        if not isinstance(signals, dict):
            messages.append(Message("error", f"pin multiplexing file {file.name} has no `signals:` table", file,
                                    ("signals",)))
            return None, messages
    return Package(derivative, package, {str(s): dict(p) for s, p in signals.items()},
                   bool(ref.get("complete", True)), instances), messages


# ----------------------------------------------------------------------------------------------- pin sources


class PinSource:
    """Where a row of the pin table comes from: by hand (TOOL-72) or from a KiCad netlist (TOOL-76).

    The board file states the source per row, so a later reader can tell a value that was typed in from one that a
    schematic netlist delivered - and a netlist import does not have to touch anything but this class.
    """

    id = ""
    title = ""
    verify_default = True                       # rows of this source are unconfirmed unless the source says so

    def describe(self, origin: str) -> str:
        return f"{self.title}{f' ({origin})' if origin else ''}"


class ManualSource(PinSource):
    id = "manual"
    title = "von Hand"
    verify_default = True

    def describe(self, origin: str) -> str:
        return f"entered by hand{f' ({origin})' if origin else ''}"


class KicadNetlistSource(PinSource):
    """TOOL-76: the row was read off a KiCad netlist (tools/nxconfig/netlist.py). `origin` is file and net(s).

    The netlist confirms the *wiring* (which pin sits on which net), not what the circuit behind it means: active
    levels, pulls and scales of such a row stay `verify: true` like a typed one."""

    id = "kicad-netlist"
    title = "aus KiCad-Netzliste"
    verify_default = True

    def describe(self, origin: str) -> str:
        return f"from KiCad netlist{f' ({origin})' if origin else ''}"


MANUAL = ManualSource()
KICAD = KicadNetlistSource()
SOURCES = {MANUAL.id: MANUAL, KICAD.id: KICAD}


def source(id_: str) -> PinSource:
    return SOURCES.get(id_, MANUAL)


# ----------------------------------------------------------------------------------------------- draft


@dataclass
class PinAssignment:
    """One pin of one board entry: which function it carries and where the row comes from."""

    role: str                                   # rx, tx, sck, pin, ...
    pin: str
    function: str                               # signal name of the pin multiplexing table (FlexCAN0_RX, GPIO, ...)
    channel: str = ""                           # ADC channel (SE3) or timer channel number, "" otherwise
    index: int | None = None                    # SPI PCS<n>
    note: str = ""
    flexio_pin: int | None = None               # PLAT-103: I2C on a FlexIO - data pin D<n> (board.yaml flexio_pin)


@dataclass
class BoardEntry:
    """One entry of the board file (a bus, an input, an output, an analog input, a PWM group)."""

    kind: str                                   # EntryKind.id
    name: str
    instance: str = ""
    pins: list = field(default_factory=list)    # list[PinAssignment]
    device: str = ""                            # CAN transceiver / SPI device (devices[].name)
    active: str = ""                            # DIO: high | low
    pull: str = ""                              # DIO input: up | down
    pairs: list = field(default_factory=list)   # PWM: list of (pair, high_side channel, low_side channel)
    source_id: str = MANUAL.id
    origin: str = ""
    note: str = ""
    verify: bool = True

    @property
    def source(self) -> PinSource:
        return source(self.source_id)

    def pin_names(self) -> list[str]:
        return [p.pin for p in self.pins]


@dataclass
class BoardDevice:
    """A fitted component, with the module that drives it (or none, with the reason)."""

    name: str
    part: str
    module: str = ""                            # "" : no module manifest lists the part
    roles: tuple = ()                           # roles the module provides (module.yaml provides)
    note: str = ""
    source_id: str = MANUAL.id
    verify: bool = True


@dataclass
class BoardDraft:
    """Everything the wizard collected. Nothing is written before `create()`."""

    name: str
    derivative: DerivativeChoice
    package: Package
    description: str = ""
    supply_v: float | None = None
    sosc_hz: int | None = None
    debug_interface: str = ""
    debug_probe: str = ""
    adc_reference_mv: int | None = None
    entries: list = field(default_factory=list)         # list[BoardEntry]
    devices: list = field(default_factory=list)         # list[BoardDevice]
    # TOOL-76: the netlist the imported rows come from ("" : none) - the [SCH] line of the board file names it
    schematic: str = ""
    # The neXtBSW **tree**, not the workspace: the family port template and its manifest live there (ARCH-57 -
    # in a customer repository the tree is the pinned submodule). Where the board is written is `plan(root=...)`.
    repo: Path = nxcfg.REPO

    # -------------------------------------------------------------- pin book-keeping

    def taken(self) -> dict:
        """pin -> the entry that uses it ("CAN0.rx"), for the double-use check and for the offers."""
        used = {}
        for entry in self.entries:
            for pin in entry.pins:
                used.setdefault(pin.pin, f"{entry.name}.{pin.role}" if not KIND_BY_ID[entry.kind].single
                                else entry.name)
        return used

    def function_of(self, kind: str, role: str, instance: str = "", index: int | None = None) -> str:
        """The pin multiplexing signal one pin role of one entry needs (`FlexCAN0_RX`, `LPSPI0_PCS1`, `GPIO`)."""
        spec = KIND_BY_ID.get(kind)
        if spec is None:
            return ""
        if kind in (DIO_IN, DIO_OUT):
            return "GPIO"
        if self.is_flexio(kind, instance):
            # PLAT-103: a FlexIO has no SDA/SCL function, every pin is a data pin; `index` is its number
            return f"{instance}_D{index if index is not None else '<n>'}"
        suffix = dict(spec.pins).get(role, role.upper())
        if role == "pcs" and index is not None:
            suffix = f"{suffix}{index}"
        return self.package.signal_name(instance, suffix)

    def offers(self, kind: str, role: str, instance: str = "", index: int | None = None) -> list[PinOption]:
        """The pins the wizard may offer for one pin role of one entry kind (decision tool72-board: only these).

        Every option carries why it is not free: `special` for a debug, reset, oscillator or boot pin, `taken_by`
        for a pin another entry already uses. The caller decides whether it shows them greyed out or warns.
        """
        if kind in (ADC, PWM, CAPTURE):
            return []                           # channel entries: use channel_offers(), the channel names the pin
        if self.is_flexio(kind, instance):
            return self.flexio_offers(instance)
        function = self.function_of(kind, role, instance, index)
        return self.package.pin_options(function, self.taken()) if function else []

    def pcs_indexes(self, instance: str) -> list[int]:
        """Chip select numbers the SPI instance offers in this package (`<instance>_PCS<n>`)."""
        prefix = f"{instance}_PCS"
        return sorted(int(s[len(prefix):]) for s in self.package.signals
                      if s.startswith(prefix) and s[len(prefix):].isdigit())

    def channel_offers(self, kind: str, instance: str) -> list[tuple]:
        """(channel, function, [PinOption]) of every channel of an ADC or timer instance of this package."""
        taken = self.taken()
        pattern = self.package.channel_pattern(instance) if kind == ADC else ""
        return [(channel, function, self.package.pin_options(function, taken))
                for channel, function in self.package.channels(instance, pattern)]

    def instances(self, kind: str) -> list[str]:
        """Instances of the derivative an entry of this kind can run on (an I2C bus: I2C and FlexIO, PLAT-103)."""
        spec = KIND_BY_ID.get(kind)
        return self.package.instances_of(spec.instance_classes) if spec and spec.needs_instance else []

    # -------------------------------------------------------------- PLAT-103: I2C on a FlexIO

    def is_flexio(self, kind: str, instance: str) -> bool:
        """True for an I2C bus on a FlexIO instance: its pins are FlexIO data pins D<n>, not SDA/SCL functions."""
        return kind == I2C and bool(instance) and self.package.class_of(instance) == FLEXIO

    def flexio_offers(self, instance: str) -> list[PinOption]:
        """Every pin that carries a data pin D<n> of the FlexIO `instance`, once per data pin it carries.

        A pin may carry two data pins (S32K144: PTD2 is FlexIO_D4 and FlexIO_D6), so an option is a pair pin/D<n>
        and names its function; `flexio_pin` of the board file is taken from the chosen option.
        """
        taken = self.taken()
        options = []
        for n, function in self.package.flexio_functions(instance):
            for pin, selector in (self.package.signals.get(function) or {}).items():
                options.append(PinOption(pin, selector, self.package.special(pin), taken.get(pin, ""),
                                         function, n))
        return sorted(options, key=lambda o: (_pin_key(o.pin), o.flexio_pin))

    def i2c_driver(self, instance: str) -> tuple:
        """(state, text): can the family port drive an I2C bus on `instance`? Read from checks.I2C_DRIVERS.

        DRIVER: the port has a driver for the class of the instance; NO_DRIVER: it has an I2C driver, but for other
        classes (s32k1xx: LPI2C, TOOL-120) - a project may not use such a bus; UNCHECKED: the family states no I2C
        driver at all (stm32, rm4x, invented families), exactly the families `nxconfig check` does not check.
        """
        from . import checks

        driver = checks.I2C_DRIVERS.get(self.derivative.family)
        if driver is None:
            return UNCHECKED, (f"the family {self.derivative.family} states no I2C driver (checks.I2C_DRIVERS): "
                               "not checked")
        classes, text = driver
        if self.package.class_of(instance) in classes:
            return DRIVER, text
        return NO_DRIVER, (f"the family port {self.derivative.family} has no driver for {instance} (class "
                           f"{self.package.class_of(instance) or '-'}) - its I2C driver is {text}")

    def has_pins(self, kind: str, instance: str) -> bool:
        """True if this package offers a pin for every pin role of a bus on `instance` (a FlexIO: two data pins
        on two different pins)."""
        if self.is_flexio(kind, instance):
            options = self.flexio_offers(instance)
            return any(a.pin != b.pin and a.flexio_pin != b.flexio_pin for a in options for b in options)
        roles = [role for role, _suffix in KIND_BY_ID[kind].pins if role != "pcs"]
        return all(self.offers(kind, role, instance) for role in roles)

    def instance_offers(self, kind: str) -> list[tuple]:
        """(instance, state, has pins) of every instance of a bus kind; state as i2c_driver() for I2C, else
        UNCHECKED.

        PLAT-103: first come the instances that can be built - a driver of the family port and pins in this
        package. The wizard pre-selects the first of them and nothing else.
        """
        found = [(name, self.i2c_driver(name)[0] if kind == I2C else UNCHECKED, self.has_pins(kind, name))
                 for name in self.instances(kind)]
        return sorted(found, key=lambda item: not (item[1] == DRIVER and item[2]))

    # -------------------------------------------------------------- adding and removing

    def check_entry(self, entry: BoardEntry) -> list[Message]:
        """Findings of one entry against package, derivative and the rows already in the draft.

        Errors mean "this cannot be written" (unknown pin, function impossible on the pin, name used twice),
        warnings mean "possible, but say it out loud" (double use, debug/reset/oscillator/boot pin).
        """
        messages: list[Message] = []
        spec = KIND_BY_ID.get(entry.kind)
        where = spec.path if spec else ()

        def error(path, text):
            messages.append(Message("error", text, None, tuple(path)))

        def warn(path, text):
            messages.append(Message("warning", text, None, tuple(path)))

        if spec is None:
            error((), f"unknown entry kind {entry.kind!r} (known: {', '.join(KIND_BY_ID)})")
            return messages
        if not NAME_RE.fullmatch(entry.name or ""):
            error(where + ("name",), f"name {entry.name!r}: a letter first, then letters, digits or '_'")
        if any(e is not entry and e.kind == entry.kind and e.name == entry.name for e in self.entries):
            error(where + ("name",), f"{spec.title} {entry.name} exists already in this board")
        if spec.needs_instance:
            known = self.instances(entry.kind)
            if entry.instance not in known:
                error(where + ("instance",),
                      f"{self.derivative.name} has no {'/'.join(spec.instance_classes)} instance "
                      f"{entry.instance!r} (it has {', '.join(known) or '-'})")
        if entry.kind == I2C and entry.instance in self.instances(I2C):
            messages += self._check_i2c(entry, where)
        if entry.kind == PWM:
            messages += self._check_pwm(entry, where)
        taken = self.taken()
        seen: dict = {}
        for pin in entry.pins:
            path = where + (pin.role,)
            if self.package.signals and pin.pin not in self.package.pins:
                error(path, f"{self.derivative.name} in package {self.package.package} has no pin {pin.pin!r}")
                continue
            if self.package.signals and pin.pin not in (self.package.signals.get(pin.function) or {}):
                options = ", ".join(o.pin for o in self.package.pin_options(pin.function)) or "-"
                error(path, f"pin {pin.pin} cannot carry {pin.function} in package {self.package.package} "
                            f"(possible: {options})")
                continue
            if pin.pin in seen:
                error(path, f"pin {pin.pin} is used twice in {entry.name} ({seen[pin.pin]} and {pin.role})")
            seen[pin.pin] = pin.role
            other = taken.get(pin.pin)
            if other and not any(p is pin for e in self.entries if e is entry for p in e.pins):
                warn(path, f"pin {pin.pin} is already used by {other}: a pin carries one function at a time")
            special = self.package.special(pin.pin)
            if special:
                warn(path, f"pin {pin.pin} is a {special} - using it here means giving that up")
        return messages

    def _check_i2c(self, entry: BoardEntry, where: tuple) -> list[Message]:
        """PLAT-103: data pins of an I2C bus on a FlexIO, and whether the family port can drive the bus at all.

        The same rules as checks._flexio_pins and checks.i2c_driver_problem (TOOL-120), said before anything is
        written: on a FlexIO every pin needs its data pin D<n> (and SDA and SCL two different ones), an I2C instance
        has none; a bus on an instance without a driver of the family port is allowed on the board (it is a fact of
        the hardware), but a project cannot use it - a warning, so it never happens by accident.
        """
        messages: list[Message] = []
        flexio = self.is_flexio(I2C, entry.instance)
        numbers: dict = {}
        for pin in entry.pins:
            path = where + (pin.role, "flexio_pin")
            if flexio and pin.flexio_pin is None:
                messages.append(Message("error", f"{entry.instance} is a FlexIO: the {pin.role.upper()} pin "
                                                 f"{pin.pin} needs the FlexIO data pin D<n> it is connected to "
                                                 f"({entry.instance}_D<n>)", None, path))
            elif not flexio and pin.flexio_pin is not None:
                # a warning, like checks._flexio_pins: a hand-written board read back may carry it
                messages.append(Message("warning", f"{entry.instance} is an I2C instance, not a FlexIO: "
                                                   f"flexio_pin D{pin.flexio_pin} is not read", None, path))
            elif flexio and pin.flexio_pin in numbers:
                messages.append(Message("error", f"SDA and SCL of {entry.name} are both on data pin "
                                                 f"D{pin.flexio_pin} of {entry.instance} ({numbers[pin.flexio_pin]} "
                                                 f"and {pin.role}): the FlexIO I2C master needs two", None, path))
            elif flexio:
                numbers[pin.flexio_pin] = pin.role
        state, text = self.i2c_driver(entry.instance)
        if state == NO_DRIVER:
            messages.append(Message("warning", f"I2C bus {entry.name}: {text}. The board may describe the bus, a "
                                               "project cannot use it (nxconfig check: use.i2c)", None,
                                    where + ("instance",)))
        return messages

    def _check_pwm(self, entry: BoardEntry, where: tuple) -> list[Message]:
        """TOOL-121: the high and low side of every half bridge must be one complementary pair of the timer
        instance (`pwm_pair_ok`, family-neutral) - the wizard used to let UH and UL choose a channel each on their
        own, with no check that the counter can even drive the two as one pair (bswgen._pwm_group refuses that
        combination only once a project uses the group, ARCH-33; here it is said while the board is still open)."""
        messages: list[Message] = []
        by_role = {p.role: p for p in entry.pins}
        for pair, high_role, low_role in entry.pairs:
            high, low = by_role.get(high_role), by_role.get(low_role)
            if high is None or low is None:
                continue
            if not pwm_pair_ok(self.derivative.family, high.channel, low.channel):
                messages.append(Message(
                    "error",
                    f"{pair}: channel CH{high.channel} (high side) and CH{low.channel} (low side) of "
                    f"{entry.instance} are not one complementary pair on {self.derivative.family} - choose the "
                    "low-side channel that belongs to the same half bridge as the high side",
                    None, where + (low_role.lower(),)))
        return messages

    def add(self, entry: BoardEntry) -> list[Message]:
        """Adds the entry if nothing speaks against it; returns every finding (warnings do not stop it)."""
        messages = self.check_entry(entry)
        if not any(m.severity == "error" for m in messages):
            self.entries.append(entry)
        return messages

    def remove(self, kind: str, name: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if not (e.kind == kind and e.name == name)]
        return len(self.entries) != before

    def add_device(self, device: BoardDevice) -> list[Message]:
        messages: list[Message] = []
        if not NAME_RE.fullmatch(device.name or ""):
            messages.append(Message("error", f"device name {device.name!r}: a letter first, then letters, digits "
                                             "or '_'", None, ("devices", "name")))
        if any(d.name == device.name for d in self.devices):
            messages.append(Message("error", f"device {device.name} exists already in this board", None,
                                    ("devices", "name")))
        if not any(m.severity == "error" for m in messages):
            self.devices.append(device)
        return messages

    def remove_device(self, name: str) -> bool:
        before = len(self.devices)
        self.devices = [d for d in self.devices if d.name != name]
        return len(self.devices) != before

    def entries_of(self, kind: str) -> list:
        return [e for e in self.entries if e.kind == kind]

    # -------------------------------------------------------------- what the family port needs

    def requirements(self):
        return requirements(self.derivative.family, self.repo)

    def check(self) -> list[Message]:
        """Every finding of the whole draft: entries, the port requirements and what a project will need."""
        messages: list[Message] = []
        for entry in self.entries:
            messages += [m for m in self.check_entry(entry) if m.severity == "error"]
            if entry.kind == I2C and self.i2c_driver(entry.instance)[0] == NO_DRIVER:
                # PLAT-103: said on the page and in the summary, not only in the dialog of the entry
                messages += [m for m in self._check_i2c(entry, KIND_BY_ID[I2C].path) if m.severity == "warning"
                             and m.path[-1:] == ("instance",)]
        messages += check_requirements(self, self.requirements())
        if not self.entries_of(CAN):
            messages.append(Message("warning", "no CAN bus: a project on this board gets no bootloader, no memory "
                                               "partitioning and no diagnostic addresses (nxconfig newproject)",
                                    None, ("can",)))
        if not self.entries_of(UART):
            messages.append(Message("warning", "no UART: a project on this board has no console", None, ("uart",)))
        if len(self.entries_of(DIO_OUT)) < 2:
            # TOOL-110: safe_state_output and locked_pin are two DIFFERENT outputs (bswgen refuses one for both)
            messages.append(Message("warning", f"{len(self.entries_of(DIO_OUT))} digital output(s): the safety "
                                               "section of a project needs two different ones (safe_state_output "
                                               "as indicator, locked_pin)", None, ("dio", "outputs")))
        free = newproject.free_test_pin(self.package.signals, set(self.taken()))
        if free is None and self.package.signals:
            messages.append(Message("warning", f"package {self.package.package} has no free GPIO pin left for the "
                                               "test pin of the safety section (safety.test_pin)", None,
                                    ("dio",)))
        for bus in self.entries_of(CAN):
            if bus.device and not any(d.name == bus.device for d in self.devices):
                messages.append(Message("error", f"CAN bus {bus.name} names the transceiver {bus.device}, which is "
                                                 "no device of this board", None, ("can", "phy")))
        return messages


# ----------------------------------------------------------------------------------------------- reading back


def _pin_of(value):
    """`{ pin: PTA0, index: 1 }` or `PTA0` -> (pin, index)."""
    if isinstance(value, dict):
        return str(value.get("pin", "")), value.get("index")
    return str(value or ""), None


def draft_from_file(board_file: Path, mcudb: Path = nxcfg.MCUDB, repo: Path = nxcfg.REPO
                    ) -> tuple[BoardDraft | None, list[Message]]:
    """An existing `board.yaml` as a draft, so the board page can offer and check the same things as the wizard.

    Only what the wizard itself writes is read back (buses, analog inputs, digital inputs and outputs, capture,
    devices). Sections it does not offer - sensor circuits, rails, power stages of a PWM group - stay in the file
    and are edited there; the draft is used for *offering pins and checking a new entry*, never for rewriting the
    file (the board page edits the document, not the draft).
    """
    return draft_from_data(_load_yaml(Path(board_file)), Path(board_file), mcudb, repo)


def draft_from_data(data, board_file: Path, mcudb: Path = nxcfg.MCUDB, repo: Path = nxcfg.REPO
                    ) -> tuple[BoardDraft | None, list[Message]]:
    """Same as draft_from_file() for a board that is already parsed - the Studio edits an unsaved document."""
    board_file = Path(board_file)
    messages: list[Message] = []
    section = data.get("board") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return None, [Message("error", f"{board_file.name} cannot be read as a board file", board_file)]
    try:
        derivative = find_derivative(str(section.get("mcu") or ""), mcudb)
    except NewBoardError as e:
        return None, [Message("error", p, board_file, ("board", "mcu")) for p in e.problems]
    package, found = load_package(derivative, str(section.get("package") or ""))
    messages += found
    if package is None:
        return None, messages
    draft = BoardDraft(str(section.get("name") or board_file.parent.name), derivative, package,
                       description=str(section.get("description") or ""), supply_v=section.get("supply_v"),
                       sosc_hz=(section.get("clock") or {}).get("sosc_hz")
                       if isinstance(section.get("clock"), dict) else None,
                       debug_interface=str((section.get("debug") or {}).get("interface") or "")
                       if isinstance(section.get("debug"), dict) else "",
                       debug_probe=str((section.get("debug") or {}).get("probe") or "")
                       if isinstance(section.get("debug"), dict) else "",
                       adc_reference_mv=section.get("adc_reference_mv"), repo=Path(repo))
    for device in data.get("devices") or []:
        if isinstance(device, dict) and device.get("name"):
            draft.devices.append(BoardDevice(str(device["name"]), str(device.get("part") or ""),
                                             str(device.get("module") or ""), (),
                                             str(device.get("note") or ""),
                                             verify=bool(device.get("verify"))))
    for kind in (SPI, CAN, UART, I2C):
        for entry in data.get(kind) or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            instance = str(entry.get("instance") or "")
            pins = []
            for role, _suffix in KIND_BY_ID[kind].pins:
                if role not in entry:
                    continue
                pin, index = _pin_of(entry[role])
                flexio_pin = entry[role].get("flexio_pin") if kind == I2C and isinstance(entry[role], dict) \
                    else None
                if draft.is_flexio(kind, instance):         # PLAT-103: the function is the data pin D<n>
                    pins.append(PinAssignment(role, pin, draft.function_of(kind, role, instance, flexio_pin),
                                              flexio_pin=flexio_pin))
                    continue
                pins.append(PinAssignment(role, pin, draft.function_of(kind, role, instance, index), index=index,
                                          flexio_pin=flexio_pin))
            draft.entries.append(BoardEntry(kind, str(entry["name"]), instance, pins,
                                            device=str(entry.get("phy") or entry.get("device") or "")))
    for entry in data.get(ADC) or []:
        if isinstance(entry, dict) and entry.get("name"):
            instance, channel = str(entry.get("instance") or ""), str(entry.get("channel") or "")
            draft.entries.append(analog(str(entry["name"]), instance, channel, str(entry.get("pin") or "")))
    for entry in data.get(CAPTURE) or []:
        if isinstance(entry, dict) and entry.get("name"):
            draft.entries.append(capture(str(entry["name"]), str(entry.get("instance") or ""),
                                         f"CH{entry.get('channel')}", str(entry.get("pin") or "")))
    for entry in data.get(PWM) or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        instance = str(entry.get("instance") or "")
        pins = [PinAssignment(str(c.get("name")), str(c.get("pin") or ""),
                              f"{instance}_CH{c.get('channel')}", channel=str(c.get("channel")))
                for c in entry.get("channels") or [] if isinstance(c, dict)]
        pairs = [(str(p.get("name")), str(p.get("high_side")), str(p.get("low_side")))
                 for p in entry.get("pairs") or [] if isinstance(p, dict)]
        draft.entries.append(BoardEntry(PWM, str(entry["name"]), instance, pins, pairs=pairs,
                                        active=str((entry.get("active") or {}).get("high_side") or "high")
                                        if isinstance(entry.get("active"), dict) else "high"))
    dio_section = data.get("dio") if isinstance(data.get("dio"), dict) else {}
    for kind, key in ((DIO_IN, "inputs"), (DIO_OUT, "outputs")):
        for entry in dio_section.get(key) or []:
            if isinstance(entry, dict) and entry.get("name"):
                draft.entries.append(dio(kind, str(entry["name"]), str(entry.get("pin") or ""),
                                         str(entry.get("active") or "high"), str(entry.get("pull") or "")))
    return draft, messages


def entry_value(entry: BoardEntry) -> object:
    """The entry as the plain YAML value the board file holds, for `yamldoc.add_item` on the board page."""
    if entry.kind in (DIO_IN, DIO_OUT):
        value = {"name": entry.name, "pin": entry.pins[0].pin, "active": entry.active or "high"}
        if entry.pull:
            value["pull"] = entry.pull
        value["verify"] = True
        return value
    if entry.kind == ADC:
        pin = entry.pins[0]
        return {"name": entry.name, "instance": entry.instance, "channel": pin.channel, "pin": pin.pin,
                "scale": {"factor": 1, "offset_mv": 0, "verify": True}}
    if entry.kind == CAPTURE:
        pin = entry.pins[0]
        return {"name": entry.name, "instance": entry.instance,
                "channel": int(pin.channel.removeprefix("CH") or 0), "pin": pin.pin}
    if entry.kind == PWM:
        return {"name": entry.name, "instance": entry.instance, "mode": "complementary",
                "active": {"high_side": entry.active or "high", "low_side": entry.active or "high"},
                "verify": True,
                "channels": [{"name": p.role, "channel": int(p.channel or 0), "pin": p.pin} for p in entry.pins],
                "pairs": [{"name": pair, "high_side": high, "low_side": low} for pair, high, low in entry.pairs]}
    value = {"name": entry.name, "instance": entry.instance}
    for pin in entry.pins:
        value[pin.role] = {"pin": pin.pin} if pin.index is None else {"pin": pin.pin, "index": pin.index}
        if pin.flexio_pin is not None:
            value[pin.role]["flexio_pin"] = pin.flexio_pin
    if entry.device:
        value["phy" if entry.kind == CAN else "device"] = entry.device
    return value


def device_value(device: BoardDevice) -> object:
    """A fitted component as the plain YAML value of `devices[]`."""
    value = {"name": device.name, "part": device.part}
    if device.module:
        value["module"] = device.module
    if device.note:
        value["note"] = device.note
    if device.verify:
        value["verify"] = True
    return value


# ----------------------------------------------------------------------------------------------- builders
# One constructor per kind of entry. The wizard, the command line and the tests all go through these, so an entry
# always carries the same fields - and a netlist importer (TOOL-76) only has to fill `source_id` and `origin`.


def bus(kind: str, name: str, instance: str, pins: dict, device: str = "", origin: str = "",
        source_id: str = MANUAL.id, note: str = "", flexio_pins: dict | None = None) -> BoardEntry:
    """A bus entry (CAN, UART, SPI, I2C): `pins` is role -> pin, or role -> (pin, index) for an SPI chip select.

    PLAT-103: an I2C bus on a FlexIO gives `flexio_pins` (role -> data pin number n); the pin then carries the
    function `<instance>_D<n>` instead of `<instance>_SDA`/`_SCL`, and the board file gets `flexio_pin: n`.
    """
    spec = KIND_BY_ID[kind]
    roles = dict(spec.pins)
    flexio_pins = flexio_pins or {}
    assigned = []
    for role, value in pins.items():
        pin, index = value if isinstance(value, tuple) else (value, None)
        if role in flexio_pins:
            assigned.append(PinAssignment(role, pin, f"{instance}_D{flexio_pins[role]}",
                                          flexio_pin=flexio_pins[role]))
            continue
        assigned.append(PinAssignment(role, pin, f"{instance}_{roles.get(role, role.upper())}"
                                      + (str(index) if role == "pcs" and index is not None else ""),
                                      index=index))
    return BoardEntry(kind, name, instance, assigned, device=device, origin=origin, source_id=source_id, note=note)


def i2c_bus(draft: BoardDraft, name: str, instance: str, sda: str = "", scl: str = "", origin: str = "",
            source_id: str = MANUAL.id) -> BoardEntry:
    """An I2C bus on an I2C instance or a FlexIO (PLAT-103), pins as `PTE4` or, on a FlexIO, `PTE4/D6`.

    On a FlexIO the data pin D<n> is **derived from the pin**: the pin multiplexing table says which data pins
    of the instance a pin can carry. A pin that carries exactly one needs no number; a pin that carries two
    (S32K144 PTD2: D4 and D6) is refused without one, with both named. A missing pin is the first free one that
    can carry the function (for SCL: on another data pin than SDA). Raises NewBoardError.
    """
    if not draft.is_flexio(I2C, instance):
        pins = {}
        for role, given in (("sda", sda), ("scl", scl)):
            pins[role] = given or suggest_pin(draft, draft.function_of(I2C, role, instance))
        return bus(I2C, name, instance, pins, origin=origin, source_id=source_id)
    options = draft.flexio_offers(instance)
    pins, numbers = {}, {}
    for role, given in (("sda", sda), ("scl", scl)):
        pin, _sep, wanted = str(given).partition("/")
        if not pin:
            usable = [o for o in options if o.free and not o.special and o.flexio_pin not in numbers.values()
                      and o.pin not in pins.values()]
            if not usable:
                raise NewBoardError([f"I2C bus {name}: no free pin carries a data pin of {instance} for "
                                     f"{role.upper()} in package {draft.package.package}"])
            pins[role], numbers[role] = usable[0].pin, usable[0].flexio_pin
            continue
        carried = [o.flexio_pin for o in options if o.pin == pin]
        if wanted:
            number = int(wanted.upper().removeprefix("D")) if wanted.upper().removeprefix("D").isdigit() else -1
            if number not in carried:
                raise NewBoardError([f"I2C bus {name}: pin {pin} does not carry {instance}_{wanted.upper()} "
                                     f"(it carries {', '.join(f'D{n}' for n in carried) or 'no data pin'})"])
        elif len(carried) != 1:
            raise NewBoardError([f"I2C bus {name}: pin {pin} carries "
                                 + (f"the data pins {', '.join(f'D{n}' for n in carried)} of {instance} - say "
                                    f"which ({pin}/D{carried[0]})" if carried else f"no data pin of {instance}")])
        else:
            number = carried[0]
        pins[role], numbers[role] = pin, number
    return bus(I2C, name, instance, pins, origin=origin, source_id=source_id, flexio_pins=numbers)


def dio(kind: str, name: str, pin: str, active: str = "high", pull: str = "", origin: str = "",
        source_id: str = MANUAL.id, note: str = "") -> BoardEntry:
    """A digital input or output on a GPIO pin."""
    return BoardEntry(kind, name, "", [PinAssignment("pin", pin, "GPIO")], active=active, pull=pull,
                      origin=origin, source_id=source_id, note=note)


def analog(name: str, instance: str, channel: str, pin: str, origin: str = "", source_id: str = MANUAL.id,
           note: str = "") -> BoardEntry:
    """An analog input: converter channel (SE<n>) and the pin that carries it."""
    return BoardEntry(ADC, name, instance, [PinAssignment("pin", pin, f"{instance}_{channel}", channel=channel)],
                      origin=origin, source_id=source_id, note=note)


def capture(name: str, instance: str, channel: str, pin: str, origin: str = "",
            source_id: str = MANUAL.id) -> BoardEntry:
    """An input capture channel: timer channel (CH<n>) and its pin."""
    return BoardEntry(CAPTURE, name, instance,
                      [PinAssignment("pin", pin, f"{instance}_{channel}", channel=channel)],
                      origin=origin, source_id=source_id)


def pwm_group(name: str, instance: str, pairs: list, active: str = "high", origin: str = "",
              source_id: str = MANUAL.id) -> BoardEntry:
    """A complementary PWM group. `pairs`: [(pair name, (high channel, high pin), (low channel, low pin)), ...]."""
    assigned, named = [], []
    for pair, high, low in pairs:
        for side, (channel, pin) in (("H", high), ("L", low)):
            role = f"{pair}{side}"
            assigned.append(PinAssignment(role, pin, f"{instance}_{channel}", channel=channel.removeprefix("CH")))
        named.append((pair, f"{pair}H", f"{pair}L"))
    return BoardEntry(PWM, name, instance, assigned, active=active, pairs=named, origin=origin,
                      source_id=source_id)


# TOOL-121: which two channels of a timer instance can be driven as one complementary pair (COMBINE/COMP of one
# half bridge), keyed by family like checks.PWM_COUNTERS - a fact of the timer IP the family port drives, not of
# one board. Mirrors the generator-level check of bswgen._pwm_group (ARCH-33: high side an even FlexTimer channel,
# low side its next one); kept here as well so the board wizard says it live, before a project ever runs bswgen.
# A family with no rule stated is not judged at board level (checks.py/bswgen still judge it once a project uses
# the group) - like checks.I2C_DRIVERS, an unknown family blocks nothing here.
PWM_PAIR_RULES = {
    "s32k1xx": lambda high, low: high % 2 == 0 and low == high + 1,
}


def pwm_pair_ok(family: str, high_channel: str, low_channel: str) -> bool:
    """True if `high_channel`/`low_channel` (`CH<n>` or plain `<n>`) can be one complementary PWM pair on `family`.

    Used by `BoardDraft._check_entry` (live in the wizard) and by the Studio dialog to suggest a matching low-side
    channel instead of two channels chosen independently (TOOL-121, doc/architecture/README.md section 11 Q3: a
    derivative of another family is judged by its own rule, not by the FlexTimer one)."""
    rule = PWM_PAIR_RULES.get(family)
    if rule is None:
        return True
    try:
        high = int(str(high_channel).removeprefix("CH"))
        low = int(str(low_channel).removeprefix("CH"))
    except ValueError:
        return True
    return rule(high, low)


def suggest_pin(draft: BoardDraft, function: str, avoid_special: bool = True) -> str:
    """First pin of the package that can carry `function`, is free and (by default) carries nothing special.

    The wizard uses it as the pre-selection of a step; the command line uses it when only the name of a bus is
    given. It never returns a pin the board already uses.
    """
    options = draft.package.pin_options(function, draft.taken())
    plain = [o for o in options if o.free and not o.special]
    free = [o for o in options if o.free]
    best = plain or (free if not avoid_special else plain or free)
    return best[0].pin if best else ""


# ----------------------------------------------------------------------------------------------- port manifest


@dataclass(frozen=True)
class PortNeeds:
    """What the family port template needs of a board, read from platforms/<family>/port/port.yaml (PLAT-95)."""

    family: str
    file: Path | None = None
    where: str = ""
    board_keys: tuple = ()                      # board.<key> entries the port's code reads
    adc_internal: tuple = ()                    # (name, channel) pairs the port reads as bios::AnalogInput
    can: tuple = ()                             # (bus name, controller instance) a port demands by name
    project_keys: tuple = ()                    # project sections the port needs (the project wizard writes them)
    notes: dict = field(default_factory=dict)   # key -> the manifest's own reason, quoted in the messages

    @property
    def known(self) -> bool:
        return self.file is not None


def requirements(family: str, repo: Path = nxcfg.REPO) -> PortNeeds:
    """The port manifest of `family` as the board wizard needs it. Never raises; an unreadable manifest is empty.

    Read through checks.read_port_manifest() on purpose (PLAT-95): what the port needs is written down once, in
    the port, and this module must not repeat it.
    """
    from . import checks

    file = checks.port_manifest_file(family, repo)
    if not file.is_file():
        return PortNeeds(family)
    found, _messages = checks.read_port_manifest(file, family, repo)
    if found is None:
        return PortNeeds(family)
    notes = {}
    board_keys = []
    for entry in found.entries("board"):
        board_keys.append(entry["key"])
        notes[f"board.{entry['key']}"] = found.note(entry)
    adc = []
    for entry in found.entries("adc_internal"):
        adc.append((entry["name"], entry["channel"]))
        notes[f"adc_internal.{entry['name']}"] = found.note(entry)
    can = []
    for entry in found.entries("can"):
        can.append((entry["name"], entry["instance"]))
        notes[f"can.{entry['name']}"] = found.note(entry)
    project_keys = []
    for entry in found.entries("project"):
        project_keys.append(entry["key"])
        notes[f"project.{entry['key']}"] = found.note(entry)
    return PortNeeds(family, found.file, found.where, tuple(board_keys), tuple(adc), tuple(can),
                     tuple(project_keys), notes)


def check_requirements(draft: BoardDraft, needs: PortNeeds) -> list[Message]:
    """Findings against the port manifest: a board that fails these does not compile with the family port."""
    messages: list[Message] = []
    if not needs.known:
        return messages
    for key in needs.board_keys:
        if getattr(draft, key, None) in (None, ""):
            messages.append(Message("error", f"the family port {needs.family} needs board.{key}"
                                             f"{needs.notes.get(f'board.{key}', '')}", None, ("board", key)))
    for name, channel in needs.adc_internal:
        offered = sorted(n for n, x in draft.package.instances.items()
                         if x.get("class") == "adc" and channel in (x.get("internal_channels") or []))
        if not offered:
            messages.append(Message("error", f"the family port {needs.family} reads the internal ADC channel "
                                             f"{name}, but {draft.derivative.name} has no ADC instance offering "
                                             f"{channel}{needs.notes.get(f'adc_internal.{name}', '')}", None,
                                    ("board", "mcu")))
    # A family port that demands a bus **by name** (PORT_GROUPS `can`): no port states such a demand today. The
    # s32k1xx port used to (its hand-written canConfiguration.h wired one bus as `Can0Config`), but ARCH-61 made
    # the CAN wiring generic over every used bus, whatever it is called, and removed that dead-by-then header. The
    # check stays here so that the day a port does state the requirement, the wizard refuses the board instead of
    # letting the user find out in the compiler.
    for name, instance in needs.can:
        found = next((e for e in draft.entries_of(CAN) if e.name == name), None)
        if found is None:
            messages.append(Message("error", f"the family port {needs.family} needs a CAN connection named {name} "
                                             f"on {instance}{needs.notes.get(f'can.{name}', '')}", None, ("can",)))
        elif instance and found.instance != instance:
            messages.append(Message("error", f"the family port {needs.family} needs the CAN connection {name} on "
                                             f"{instance}, this board puts it on {found.instance}"
                                             f"{needs.notes.get(f'can.{name}', '')}", None, ("can",)))
    return messages


# ----------------------------------------------------------------------------------------------- modules


@dataclass(frozen=True)
class ModuleOffer:
    """A driver module a fitted part can be assigned to (libs/bsw/<module>/module.yaml)."""

    module: str
    description: str
    roles: tuple
    parts: tuple

    def matches(self, part: str) -> bool:
        return any(fnmatch.fnmatch(part, pattern) for pattern in self.parts)


def module_offers(module_dirs=nxcfg.MODULE_DIRS) -> list[ModuleOffer]:
    """Every device driver of the module manifests, with the roles it provides and the parts it names."""
    modules = nxcfg.load_modules(module_dirs)
    offers = []
    for name, manifest in sorted(modules.manifests.items()):
        if manifest.kind != "device_driver" or not manifest.provides:
            continue
        offers.append(ModuleOffer(name, manifest.description or "", tuple(manifest.provides),
                                  tuple(manifest.parts)))
    return offers


def module_for_part(part: str, module_dirs=nxcfg.MODULE_DIRS) -> ModuleOffer | None:
    """The module whose manifest lists this part number; None if no manifest does (the wizard says why)."""
    return next((offer for offer in module_offers(module_dirs) if offer.matches(part)), None)


UNKNOWN_PART_NOTE = (
    "no module.yaml of the tree lists this part: the entry stays without `module:` on purpose. Adding the part to "
    "the `parts:` list of a module whose register set it does not have would be a lie about the component "
    "(boards/s32k116evb/board.yaml says the same about its UJA1169). A CAN transceiver without a module is "
    "treated by nxconfig as passive (always ready) and says so as a warning")


def describe_device(name: str, part: str, module_dirs=nxcfg.MODULE_DIRS, note: str = "") -> BoardDevice:
    """A fitted part with the module and the roles of its manifest, or without one and with the reason."""
    offer = module_for_part(part, module_dirs)
    if offer is None:
        return BoardDevice(name, part, "", (), note or UNKNOWN_PART_NOTE)
    return BoardDevice(name, part, offer.module, offer.roles,
                       note or f"{offer.description or offer.module} (module {offer.module}, roles "
                               f"{', '.join(offer.roles)})")


# ----------------------------------------------------------------------------------------------- rendering


def _quote(text: str) -> str:
    return newproject._quote(str(text))


def _fold(text: str, indent: int, width: int = 118) -> list[str]:
    """Comment text wrapped to `width` columns, each line with `indent` blanks and '# '."""
    words, lines, current = str(text).split(), [], ""
    pad = " " * indent + "# "
    for word in words:
        if current and len(pad) + len(current) + 1 + len(word) > width:
            lines.append(pad + current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(pad + current)
    return lines


def _pin_field(pin: PinAssignment) -> str:
    if pin.flexio_pin is not None:                  # PLAT-103: I2C on a FlexIO
        return f"{{ pin: {pin.pin}, flexio_pin: {pin.flexio_pin} }}"
    if pin.index is not None:
        return f"{{ pin: {pin.pin}, index: {pin.index} }}"
    return f"{{ pin: {pin.pin} }}"


def _source_comment(out: list, entry: BoardEntry, indent: int) -> None:
    """TOOL-76: a row of a flow-style list gets its source as a comment above it - only when it is not typed in,
    so a board of the wizard alone reads exactly as before."""
    if entry.source_id != MANUAL.id:
        out.extend(_fold(entry.source.describe(entry.origin), indent))


def render_board(draft: BoardDraft, port: bool = True) -> str:
    """The board file: header with the sources, then one section per kind of entry, with comments per row."""
    d, p = draft.derivative, draft.package
    needs = draft.requirements()
    mux_file = f"mcudb/{d.family}/{d.name}_{p.package}.pinmux.yaml"
    out: list[str] = []
    add = out.append

    add(f"# Board profile: {draft.description or draft.name} ({d.name}, {p.package}) - hardware only: what is "
        "fitted and how it is wired.")
    add(f"# Created by the new-board wizard of neXtBSW (TOOL-72) for an own ECU board. What a software project "
        "uses of it")
    add("# (selection, frequencies, safety pins) stands in projects/<project>/project.yaml, never here.")
    add("# Schema: tools/nxconfig/model.py (Board), responsibilities: doc/architecture/README.md section 4.")
    add("#")
    add("# Sources:")
    if draft.schematic:
        add(f"#   [SCH] KiCad netlist {draft.schematic} - the pins of every row marked `from KiCad netlist` were")
        add("#         read off it (TOOL-76): which MCU pin sits on which net. It does not say what the circuit")
        add("#         means - active levels, pulls, scales, the crystal and the fitted parts stay `verify: true`")
        add("#         until they have been checked against the schematic or measured on the board.")
    else:
        add("#   [SCH] the schematic of this board - **not stated yet**. Every row below was entered by hand in "
            "the")
        add("#         wizard and is marked `verify: true` until it has been read off the schematic or measured "
            "on")
        add("#         the board. Replace this line with the document (name, revision, date) and drop the "
            "`verify`")
        add("#         of every row it confirms.")
    add(f"#   [MUX] {mux_file} - every pin function below is taken")
    add("#         from there (signal -> pin -> selector); the wizard offered no pin that cannot carry it.")
    if needs.known:
        add(f"#   [PORT] {needs.where} - what the family port template of {needs.family} needs of this board")
        add(f"#         ({', '.join('board.' + k for k in needs.board_keys) or 'nothing'}); checked by nxconfig.")
    add("# Where a row comes from: the comment of the row says it - entered by hand in the wizard, or read off a "
        "KiCad")
    add("# netlist (TOOL-76, with file and net), so a typed row stays distinguishable from an imported one.")
    add("schema_version: 1")
    add("")
    add("board:")
    add(f"  name: {draft.name}")
    add(f"  mcu: {d.name}")
    add(f"  package: {p.package}")
    if draft.description:
        add(f"  description: {_quote(draft.description)}")
    if draft.supply_v is not None:
        add(f"  supply_v: {draft.supply_v:g}                        # verify: stated in the wizard, "
            "confirm against [SCH]")
    if draft.sosc_hz is not None:
        add("  clock:")
        out.extend(_fold("Fitted crystal or external oscillator. Bit timing of every CAN bus and the UART "
                         "dividers follow from it; a different crystal changes no code (review question 2). "
                         "verify: stated in the wizard, confirm against [SCH].", 4))
        add(f"    sosc_hz: {draft.sosc_hz}")
    else:
        add("  clock: {}                             # no crystal fitted: the clock model uses the internal "
            "oscillators")
    if draft.debug_interface:
        add("  debug:")
        out.extend(_fold("Debug connection of the board. The probe selects the flash targets of the build "
                         "(root CMakeLists.txt); a probe the targets do not know is allowed and simply gets no "
                         "flash target.", 4))
        add(f"    interface: {draft.debug_interface}")
        if draft.debug_probe:
            add(f"    probe: {_quote(draft.debug_probe)}")
    if draft.adc_reference_mv is not None:
        note = needs.notes.get("board.adc_reference_mv", "")
        out.extend(_fold("Voltage at the ADC reference input VREFH: the scaling base of every ADC channel."
                         + (f" Needed by the family port{note}" if note else ""), 2))
        add(f"  adc_reference_mv: {draft.adc_reference_mv}")

    if draft.devices:
        add("")
        out.extend(_fold("Fitted components the software talks to. `module:` is the driver whose manifest "
                         "(libs/bsw/<module>/module.yaml) lists the part; a part no manifest lists stays without "
                         "one, with the reason in the note.", 0))
        add("devices:")
        for device in draft.devices:
            add(f"  - name: {device.name}")
            add(f"    part: {_quote(device.part)}")
            if device.module:
                add(f"    module: {device.module}            # roles: {', '.join(device.roles) or '-'}")
            if device.note:
                out.extend(_fold(device.note, 4))
            if device.verify:
                add("    verify: true")

    for kind, header in ((SPI, "SPI buses."), (CAN, "CAN connections."), (UART, "UARTs (console, LIN, ...)."),
                         (I2C, "I2C buses.")):
        rows = draft.entries_of(kind)
        if not rows:
            continue
        add("")
        out.extend(_fold(header + " Instance and pins are checked against the derivative and [MUX].", 0))
        add(f"{kind}:")
        for entry in rows:
            add(f"  - name: {entry.name}")
            add(f"    instance: {entry.instance}")
            for pin in entry.pins:
                add(f"    {pin.role}: {_pin_field(pin)}".ljust(40)
                    + f"# [MUX] {pin.function} on {pin.pin}")
            if kind == CAN and entry.device:
                add(f"    phy: {entry.device}")
            if kind == SPI and entry.device:
                add(f"    device: {entry.device}")
            add(f"    # {entry.source.describe(entry.origin)}")
            if kind == I2C:
                state, text = draft.i2c_driver(entry.instance)
                if state == NO_DRIVER:                  # PLAT-103: said where the bus stands
                    out.extend(_fold(f"{text}: a project cannot use this bus (nxconfig check, use.i2c).", 4))
            if entry.note:
                out.extend(_fold(entry.note, 4))

    adc_rows = draft.entries_of(ADC)
    if adc_rows:
        add("")
        out.extend(_fold("Analog inputs. `scale` converts the pin voltage to the value in mV; factor 1 means the "
                         "signal reaches the pin unchanged. A divider or a sensor circuit in front of the pin is a "
                         "board fact and belongs here (architecture section 4, IO-18) - the wizard cannot know it, "
                         "so it writes the neutral scale with verify.", 0))
        add("adc:")
        for entry in adc_rows:
            pin = entry.pins[0]
            _source_comment(out, entry, 2)
            add(f"  - {{ name: {entry.name}, instance: {entry.instance}, channel: {pin.channel}, "
                f"pin: {pin.pin},")
            add("      scale: { factor: 1, offset_mv: 0, verify: true } }"
                + f"   # [MUX] {pin.function} on {pin.pin}")

    pwm_rows = draft.entries_of(PWM)
    if pwm_rows:
        add("")
        out.extend(_fold("PWM groups. `mode: complementary` drives every pair as a half bridge, the low side "
                         "inverted with dead time. Frequency and dead time are decided by the project; the facts "
                         "of the power stage (switching times, gate charge, R_DS(on), IO-24) belong here and are "
                         "unknown to the wizard - without them nxconfig does not check the dead time and says so.",
                         0))
        add("pwm:")
        for entry in pwm_rows:
            _source_comment(out, entry, 2)
            add(f"  - name: {entry.name}")
            add(f"    instance: {entry.instance}")
            add("    mode: complementary")
            add(f"    active: {{ high_side: {entry.active or 'high'}, low_side: {entry.active or 'high'} }}"
                "     # verify: confirm against [SCH]")
            add("    verify: true")
            add("    channels:")
            for pin in entry.pins:
                add(f"      - {{ name: {pin.role}, channel: {pin.channel}, pin: {pin.pin} }}".ljust(56)
                    + f"# [MUX] {pin.function}")
            add("    pairs:")
            for pair, high, low in entry.pairs:
                add(f"      - {{ name: {pair}, high_side: {high}, low_side: {low} }}")

    capture_rows = draft.entries_of(CAPTURE)
    if capture_rows:
        add("")
        out.extend(_fold("Input capture channels (edge measurement).", 0))
        add("input_capture:")
        for entry in capture_rows:
            pin = entry.pins[0]
            _source_comment(out, entry, 2)
            add(f"  - {{ name: {entry.name}, instance: {entry.instance}, "
                f"channel: {pin.channel.removeprefix('CH')}, pin: {pin.pin} }}"
                + f"   # [MUX] {pin.function}")

    inputs, outputs = draft.entries_of(DIO_IN), draft.entries_of(DIO_OUT)
    if inputs or outputs:
        add("")
        out.extend(_fold("Digital inputs and outputs. `active` is the pin level of the active state and `pull` the "
                         "resistor the MCU has to switch on - both are properties of the circuit, not of the "
                         "software; a wrong level only inverts the signal, the code stays the same.", 0))
        add("dio:")
        if inputs:
            add("  inputs:")
            for entry in inputs:
                _source_comment(out, entry, 4)
                pull = f", pull: {entry.pull}" if entry.pull else ""
                add(f"    - {{ name: {entry.name}, pin: {entry.pins[0].pin}, active: {entry.active or 'high'}"
                    f"{pull}, verify: true }}")
        if outputs:
            add("  outputs:")
            for entry in outputs:
                _source_comment(out, entry, 4)
                add(f"    - {{ name: {entry.name}, pin: {entry.pins[0].pin}, "
                    f"active: {entry.active or 'high'}, verify: true }}")

    if not port:
        add("")
        out.extend(_fold("No board port was written (ecu/platforms/<board>/Options.cmake). Since ARCH-58 that is "
                         "not needed to build: the feature switches are derived. Add the file only for a "
                         "justified exception of this board.", 0))
    return "\n".join(out).rstrip("\n") + "\n"


def render_readme(draft: BoardDraft, port: bool = True) -> str:
    """README next to the board file: what the board is, what is unconfirmed, what to do next."""
    d, p = draft.derivative, draft.package
    needs = draft.requirements()
    lines = [
        f"# Board `{draft.name}`",
        "",
        f"{draft.description or 'Own ECU board'} - {d.name} in package {p.package} (family `{d.family}`"
        f"{', core ' + d.core if d.core else ''}).",
        "",
        "Created with the new-board wizard of neXtBSW (`nxconfig board new`, neXtBSW Studio: *Neues Board "
        "anlegen*). The board file [`board.yaml`](board.yaml) carries **hardware facts only**: what is fitted and "
        "how it is wired. What a software project uses of it - clock, bit rates, tasks, safety pins - is decided "
        "in `projects/<project>/project.yaml` (architecture section 4).",
        "",
        "## What is confirmed and what is not",
        "",
        ("Every row of `board.yaml` was **entered by hand** in the wizard and is marked `verify: true`. The pin "
         "functions are the only part that is backed: the wizard offered only pins that can carry the function in "
         f"this package (`mcudb/{d.family}/{d.name}_{p.package}.pinmux.yaml`). Before the first bring-up:"
         if not draft.schematic else
         f"The pins of the rows marked *from KiCad netlist* were read off `{draft.schematic}` (TOOL-76) and "
         "checked against the pin multiplexing of this package "
         f"(`mcudb/{d.family}/{d.name}_{p.package}.pinmux.yaml`). The netlist backs the **wiring** only; what the "
         "circuit means - active levels, pull resistors, scales, the crystal, the fitted parts and their roles - "
         "stays `verify: true`, like every row entered by hand. Before the first bring-up:"),
        "",
        "1. read the schematic and confirm every pin, every active level, every pull resistor, the crystal, the "
        "supply and the ADC reference;",
        "2. name the schematic (document, revision, date) in the `[SCH]` line of the source block;",
        "3. drop `verify: true` from every row the document confirms.",
        "",
        "A row that is still marked `verify` is not wrong - it is unproven, and `nxconfig check` says so.",
        "",
        "## What the board offers",
        "",
        "| Section | Entries |",
        "|---|---|",
    ]
    for spec in ENTRY_KINDS:
        rows = draft.entries_of(spec.id)
        if rows:
            lines.append(f"| `{'.'.join(str(x) for x in spec.path)}` | "
                         + ", ".join(f"`{e.name}`" + (f" ({e.instance})" if e.instance else "") for e in rows)
                         + " |")
    if draft.devices:
        lines.append("| `devices` | " + ", ".join(
            f"`{x.name}` {x.part}" + (f" -> module `{x.module}`" if x.module else " (no module)")
            for x in draft.devices) + " |")
    lines += [
        "",
        "## Building a project on this board",
        "",
        "```sh",
        f".venv/Scripts/python tools/nxconfig new <project> --board {draft.name}",
        f".venv/Scripts/python tools/nxconfig check projects/<project>/project.yaml",
        "```",
        "",
    ]
    if port:
        lines += [
            f"The board port `ecu/platforms/{draft.name}/Options.cmake` was written next to this board. Since "
            "**ARCH-58** it carries no feature switch: `PLATFORM_SUPPORT_*` and `OPENBSW_PLATFORM` are derived "
            "from the project, the board and the statement of the family "
            f"(`ecu/platforms/{d.family}/features.yaml`), so the board builds without this file. It is the hook "
            "for a justified exception of this board. Everything else comes from the family port template "
            f"`platforms/{d.family}/port` and from the generated integration; **no board port ever points at "
            "another board port** (`ecu/platforms/README.md`).",
            "",
        ]
    else:
        lines += [
            "No board port was written. Since **ARCH-58** that is not needed to build: the feature switches are "
            f"derived from the project and from `ecu/platforms/{d.family}/features.yaml`. Add "
            f"`ecu/platforms/{draft.name}/Options.cmake` only for a justified exception of this board "
            "(`ecu/platforms/README.md`).",
            "",
        ]
    if needs.known:
        lines += [
            f"## What the family port of `{needs.family}` needs",
            "",
            f"Stated in [`{needs.where}`]({os.path.relpath(needs.file, Path('.')).replace(os.sep, '/')}) and "
            "checked by `nxconfig check`:",
            "",
        ]
        for key in needs.board_keys:
            lines.append(f"- `board.{key}` of this file{needs.notes.get(f'board.{key}', '')}")
        for name, channel in needs.adc_internal:
            lines.append(f"- an ADC instance of {d.name} that offers the internal channel `{channel}`; the "
                         f"project reads it as `adc_internal` entry `{name}`")
        for key in needs.project_keys:
            lines.append(f"- section `{key}` of the project (the project wizard writes it)")
        lines.append("")
    return "\n".join(lines)


def render_port_options(draft: BoardDraft) -> str:
    """The board port: the place for a justified exception of this board, nothing else (ARCH-58).

    Until ARCH-58 the wizard wrote the ten `PLATFORM_SUPPORT_*` switches and `OPENBSW_PLATFORM` here, and the
    board was buildable only because of them. They are derived now (tools/nxconfig/features.py), so the file is
    empty of switches - it stays as the documented hook for a board that really has to deviate.
    """
    family = draft.derivative.family
    return "\n".join([
        f"# Board port of {draft.name} (created by the new-board wizard, TOOL-72).",
        "#",
        "# **There is no feature switch here, and none is needed.** Since ARCH-58 (customer-project.md N5) the",
        "# switches of the build are derived instead of written by hand:",
        "#",
        "#   OPENBSW_PLATFORM         the family of the derivative (NX_FAMILY)",
        "#   IO, CAN, TRANSPORT, UDS  from the project (use.dio/adc/input_capture/pwm, use.can, diagnostics)",
        "#   ETHERNET                 from the build option NX_ETHERNET, not from the board",
        f"#   WATCHDOG, MPU            from the statement of the family, ecu/platforms/{family}/features.yaml",
        "#",
        "# Where every switch comes from, and what is deliberately not derived, stands at the top of",
        "# tools/nxconfig/features.py; the root CMakeLists.txt sets them (block \"Funktionsschalter aus Projekt",
        "# und Port\").",
        "#",
        "# **What this file is still for.** It is included *after* the derived switches, so it may override one -",
        "# that is then the justified exception of this board (ecu/platforms/README.md) and belongs into the",
        "# changelog with its reason. A board without this file builds.",
        "#",
        "# Everything hand-written lives in the family port template platforms/" + family + "/port (PLAT-23);",
        "# pins, clock, fitted parts and memory come from the configuration and the generators. No board port",
        "# points at another board port.",
        "",
    ])


# ----------------------------------------------------------------------------------------------- plan and write


@dataclass
class PlannedFile:
    path: str                                   # relative to the workspace root, posix
    text: str
    in_board_folder: bool = True                # False: written outside boards/<name> (the board port)


@dataclass
class Plan:
    """What create() will write. Nothing exists on disk yet."""

    draft: BoardDraft
    root: Path                                  # workspace root the paths below are relative to
    target: Path                                # <root>/boards/<name>
    files: list = field(default_factory=list)   # list[PlannedFile]
    notes: list = field(default_factory=list)
    port: bool = True

    @property
    def board_file(self) -> Path:
        return self.target / BOARD_FILE

    @property
    def port_file(self) -> Path | None:
        return self.root / PORT_OPTIONS / self.draft.name / PORT_FILE if self.port else None

    def tree(self) -> list[str]:
        return sorted(f.path for f in self.files)


@dataclass
class Result:
    board_file: Path
    messages: list                              # findings of nxconfig check on a scratch project (empty: clean)
    plan: Plan

    @property
    def errors(self) -> list:
        return [m for m in self.messages if m.severity == "error"]


def check_name(name: str, root: Path = nxcfg.REPO, boards_dir: Path | None = None) -> list[str]:
    """Problems of a board name (empty list: fine)."""
    problems = []
    if not NAME_RE.fullmatch(name or ""):
        problems.append(f"board name {name!r}: a letter first, then letters, digits or '_' (at most 48 "
                        "characters; folder name and NX_BOARD)")
        return problems
    if name.lower() in newproject.WINDOWS_DEVICE_NAMES:
        problems.append(f"board name {name!r} is a reserved device name on Windows")
    folder = Path(boards_dir) if boards_dir else Path(root) / BOARDS_DIR
    if (folder / name / BOARD_FILE).is_file():
        problems.append(f"board {name!r} exists already ({folder / name / BOARD_FILE})")
    # a board of the tree with the same name would be shadowed and the board port could not be told apart
    for other in newproject.list_boards(root):
        if other.name.lower() == name.lower() and other.file.parent != folder / name:
            problems.append(f"board name {name!r} is already used by {other.file} - a second board of that name "
                            "would share the board port ecu/platforms/<board>")
    return problems


def default_target(name: str, root: Path = nxcfg.REPO) -> Path:
    """Where a new board goes: boards/<name> of the workspace (in a customer repository: its own boards/)."""
    return Path(root) / BOARDS_DIR / name


def plan(draft: BoardDraft, target: Path | None = None, root: Path | None = None, port: bool = True) -> Plan:
    """Everything that will be written, with the findings of the draft as notes. Raises NewBoardError."""
    root = Path(root or draft.repo).resolve()
    target = Path(target).absolute() if target else default_target(draft.name, root)
    problems = check_name(draft.name, root, target.parent)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        problems.append(f"folder {target} exists and is not empty")
    if problems:
        raise NewBoardError(problems)
    result = Plan(draft, root, target, port=port)
    board_rel = _rel(target, root)
    result.files = [PlannedFile(f"{board_rel}/{BOARD_FILE}", render_board(draft, port)),
                    PlannedFile(f"{board_rel}/{README_FILE}", render_readme(draft, port))]
    if port:
        port_rel = (PORT_OPTIONS / draft.name / PORT_FILE).as_posix()
        if (root / PORT_OPTIONS / draft.name / PORT_FILE).exists():
            raise NewBoardError([f"board port {root / PORT_OPTIONS / draft.name / PORT_FILE} exists already"])
        result.files.append(PlannedFile(port_rel, render_port_options(draft), in_board_folder=False))
        result.notes.append(f"board port {port_rel}: the place for a justified exception of this board; the "
                            "feature switches themselves are derived (ARCH-58)")
    else:
        result.notes.append("no board port written: the board builds without one since ARCH-58; add "
                            "ecu/platforms/<board>/Options.cmake only for a justified exception")
    for message in draft.check():
        result.notes.append(f"{message.severity}: {message.text}")
    needs = draft.requirements()
    if needs.known:
        result.notes.append(f"the family port {needs.family} states its needs in {needs.where} "
                            f"({', '.join('board.' + k for k in needs.board_keys) or 'no board value'}); a project "
                            "on this board is checked against it")
    return result


def _rel(path: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(os.path.abspath(path), os.path.abspath(start))).as_posix()
    except ValueError:
        return Path(os.path.abspath(path)).as_posix()


def create(plan_: Plan, mcudb: Path = nxcfg.MCUDB, module_dirs=nxcfg.MODULE_DIRS) -> Result:
    """Writes the plan (staging folder, one move into place) and checks the result with a scratch project.

    All or nothing: a failure leaves nothing behind. Findings of the check are no failure - they come back as
    messages with file, line and YAML path, exactly like `nxconfig check`.
    """
    target = plan_.target
    if target.exists() and any(target.iterdir()):
        raise NewBoardError([f"folder {target} exists and is not empty"])
    written: list[Path] = []
    staging = target.parent / f".{target.name}.nxboard-{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir(parents=True)
        for f in plan_.files:
            if f.in_board_folder:
                out = staging / Path(f.path).name
            else:
                out = plan_.root / f.path              # the board port, outside the board folder
                if out.exists():
                    raise NewBoardError([f"{out} exists already"])
                out.parent.mkdir(parents=True, exist_ok=True)
                written.append(out)
            out.write_text(genlicense.stamp_if_known(f.text, f.path), encoding="utf-8", newline="\n")   # PLAT-120
        if target.exists():
            target.rmdir()                             # empty (checked above)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        for out in written:
            out.unlink(missing_ok=True)
            with_parent = out.parent
            if with_parent.is_dir() and not any(with_parent.iterdir()):
                with_parent.rmdir()
        raise
    return Result(plan_.board_file, check_board(plan_.board_file, plan_.root, mcudb, module_dirs), plan_)


def check_board(board_file: Path, root: Path = nxcfg.REPO, mcudb: Path = nxcfg.MCUDB,
                module_dirs=nxcfg.MODULE_DIRS) -> list[Message]:
    """Findings of `nxconfig check` on an empty project planned for this board, in a scratch folder.

    A board file alone cannot be checked: the checks live between the levels (which instance, which pin, which
    role, which clock), and only a project ties them together. So the board is proven the way it will be used -
    the same path `nxconfig new --board <board>` takes.
    """
    import tempfile

    board_file = Path(board_file).resolve()
    data = _load_yaml(board_file) or {}
    section = data.get("board") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return [Message("error", "board file cannot be read", board_file)]
    info = newproject.BoardInfo(str(section.get("name") or board_file.parent.name), board_file,
                                str(section.get("mcu") or ""), str(section.get("description") or ""),
                                (Path(root) / PORT_OPTIONS /
                                 str(section.get("name") or "") / PORT_FILE).is_file())
    scratch = Path(tempfile.mkdtemp(prefix="nxboard-check-"))
    try:
        try:
            # TOOL-106: with every signal of the board - the Studio creates the first project on a new board that
            # way, so the board is proven with all of its buses and channels, not only the first CAN and UART
            # TOOL-110: the safety section of this scratch project is derived (SAFETY_PROBE) only to prove that
            # the board can carry one - a project of the user never gets derived safety pins
            project = newproject.plan_empty("boardcheck", info, scratch / "boardcheck", Path(root),
                                            mcudb=mcudb, module_dirs=module_dirs, signals="all",
                                            safety=newproject.SAFETY_PROBE)
        except newproject.NewProjectError as e:
            return [Message("error", f"no project can be created on this board: {p}", board_file)
                    for p in e.problems]
        return list(newproject.create(project).messages)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ----------------------------------------------------------------------------------------------- command line


def _spec(text: str, what: str) -> tuple:
    """`NAME=REST` of a command line option; raises NewBoardError with the option named."""
    name, sep, rest = str(text).partition("=")
    if not sep or not name:
        raise NewBoardError([f"{what} {text!r}: expected NAME=..."])
    return name, rest


def _fill_bus(draft: BoardDraft, kind: str, text: str, what: str) -> BoardEntry:
    """`NAME=INSTANCE[:pin,pin,...]`; pins in the role order of the kind, missing ones are suggested."""
    name, rest = _spec(text, what)
    instance, _, pin_text = rest.partition(":")
    if not instance:
        known = draft.instances(kind)
        raise NewBoardError([f"{what} {name}: no instance given (known: {', '.join(known) or '-'})"])
    roles = [role for role, _ in KIND_BY_ID[kind].pins]
    given = [p.strip() for p in pin_text.split(",") if p.strip()] if pin_text else []
    if given and len(given) != len(roles):
        raise NewBoardError([f"{what} {name}: {len(given)} pin(s) for {len(roles)} role(s) ({', '.join(roles)})"])
    if kind == I2C:                                 # PLAT-103: on a FlexIO the data pin comes from the pin
        return i2c_bus(draft, name, instance, *(given or ("", "")), origin=f"nxconfig board new --{kind}")
    pins: dict = {}
    indexes = draft.pcs_indexes(instance)[:1]
    for i, role in enumerate(roles):
        pcs = indexes[0] if role == "pcs" and indexes else None
        function = draft.function_of(kind, role, instance, pcs)
        pin = given[i] if given else suggest_pin(draft, function)
        if not pin:
            raise NewBoardError([f"{what} {name}: no free pin for {function} in package {draft.package.package}"])
        pins[role] = (pin, pcs) if role == "pcs" else pin
    return bus(kind, name, instance, pins, origin=f"nxconfig board new --{kind}")


def _report(messages, prefix: str = "") -> int:
    errors = 0
    for m in messages:
        errors += m.severity == "error"
        print(f"{prefix}{m}")
    return errors


def _list(args) -> int:
    mcudb = args.mcudb or nxcfg.MCUDB
    if not args.mcu:
        print(f"derivatives of {mcudb}:")
        for d in list_derivatives(mcudb):
            print(f"  {d.name:<16} {d.family:<10} {d.core:<16} "
                  f"{d.problem or 'packages ' + ', '.join(d.packages)}")
        return 0
    derivative = find_derivative(args.mcu, mcudb)
    if not args.package:
        print(f"{derivative.name} ({derivative.family}, {derivative.core}): "
              f"packages {', '.join(derivative.packages)}")
        return 0
    package, messages = load_package(derivative, args.package)
    if _report(messages) or package is None:
        return 1
    print(f"{derivative.name} {package.package}: {len(package.pins)} pins, {len(package.signals)} functions")
    for spec in ENTRY_KINDS:
        if spec.needs_instance:
            print(f"  {spec.title:<24} {', '.join(package.instances_of(spec.instance_classes)) or '-'}")
    if args.function:
        draft = BoardDraft(args.name or "preview", derivative, package)
        for option in package.pin_options(args.function, draft.taken()):
            print(f"  {args.function} -> {option.pin} (selector {option.selector})"
                  + (f"  ({option.special})" if option.special else ""))
    return 0


def _new(args) -> int:
    mcudb = args.mcudb or nxcfg.MCUDB
    root = Path(args.root or nxcfg.REPO).resolve()
    module_dirs = tuple(args.module_dir) if args.module_dir else nxcfg.MODULE_DIRS
    derivative = find_derivative(args.mcu, mcudb)
    package, messages = load_package(derivative, args.package or (derivative.packages or [""])[0])
    if _report(messages) or package is None:
        return 1
    draft = BoardDraft(args.name, derivative, package, description=args.description, supply_v=args.supply_v,
                       sosc_hz=args.sosc_hz, debug_interface=args.debug_interface, debug_probe=args.debug_probe,
                       adc_reference_mv=args.adc_reference_mv)
    findings = []
    if args.netlist:
        draft.schematic = Path(args.netlist).name
    for text in args.device:
        name, part = _spec(text, "--device")
        findings += draft.add_device(describe_device(name, part, module_dirs))
    for kind, values, what in ((SPI, args.spi, "--spi"), (CAN, args.can, "--can"), (UART, args.uart, "--uart"),
                               (I2C, args.i2c, "--i2c")):
        for text in values:
            findings += draft.add(_fill_bus(draft, kind, text, what))
    for text in args.adc:
        name, rest = _spec(text, "--adc")
        parts = rest.split(":")
        instance, channel = parts[0], parts[1] if len(parts) > 1 else ""
        pin = parts[2] if len(parts) > 2 else suggest_pin(draft, f"{instance}_{channel}")
        findings += draft.add(analog(name, instance, channel, pin, origin="nxconfig board new --adc"))
    for kind, values, what in ((DIO_IN, args.input, "--in"), (DIO_OUT, args.output, "--out")):
        for text in values:
            name, rest = _spec(text, what)
            parts = rest.split(":")
            pin = parts[0] or suggest_pin(draft, "GPIO")
            active = parts[1] if len(parts) > 1 and parts[1] else "high"
            pull = parts[2] if len(parts) > 2 else ""
            findings += draft.add(dio(kind, name, pin, active, pull,
                                      origin=f"nxconfig board new --{kind}"))
    if args.netlist:
        findings += _import_netlist(draft, args)
    for text in args.phy:
        name, device = _spec(text, "--phy")
        found = next((e for e in draft.entries_of(CAN) if e.name == name), None)
        if found is None:
            raise NewBoardError([f"--phy {text}: no CAN bus {name} on this board"])
        found.device = device
    errors = _report(findings, "  ")
    target = Path(args.dir).absolute() if args.dir else default_target(args.name, root)
    board_plan = plan(draft, target, root, port=not args.no_port)
    print(f"board {draft.name} ({derivative.name}, {package.package}) in {target}:")
    for rel in board_plan.tree():
        print(f"  {rel}")
    for note in board_plan.notes:
        print(f"note: {note}")
    if errors:
        print("nothing written: the entries above have errors")
        return 1
    if args.dry_run:
        return 0
    result = create(board_plan, mcudb, module_dirs)
    for m in result.messages:
        print(f"  {m}")
    print(f"{draft.name}: created {result.board_file}, {len(result.errors)} error(s) in a project on it, "
          f"{len(result.messages) - len(result.errors)} warning(s)")
    return 1 if result.errors else 0


def _netlist_rules(args) -> list:
    """--rules FILE and --map NET=KIND[:NAME[:ROLE]] of `board netlist` and `board new --netlist` (TOOL-76)."""
    from . import netlist
    rules = [netlist.rule_from_text(text) for text in args.map]
    if args.rules:
        rules += netlist.read_rules(Path(args.rules))
    return rules


def _import_netlist(draft: BoardDraft, args) -> list[Message]:
    """`board new --netlist`: the netlist rows go into the new draft; the typed options come on top of them."""
    from . import netlist
    try:
        imp = netlist.prepare(Path(args.netlist), draft, args.ref or "", _netlist_rules(args))
    except netlist.NetlistError as e:
        raise NewBoardError([str(e)]) from None
    for line in netlist.report_lines(imp):
        print(line)
    netlist.apply(draft, imp.changes)
    return [m for m in imp.messages if m.severity == "error"] + [
        Message("error", c.describe(), None, KIND_BY_ID[c.entry.kind].path) for c in imp.changes
        if c.action == netlist.ERROR]


def _netlist(args) -> int:
    """`board netlist FILE`: proposals and preview against a derivative or an existing board; writes nothing."""
    from . import netlist
    mcudb = args.mcudb or nxcfg.MCUDB
    messages: list[Message] = []
    if args.board:
        draft, messages = draft_from_file(Path(args.board), mcudb)
        if draft is None:
            return _report(messages) or 1
    else:
        if not args.mcu:
            raise NewBoardError(["board netlist: --mcu (with --package) or --board is needed"])
        derivative = find_derivative(args.mcu, mcudb)
        package, messages = load_package(derivative, args.package or (derivative.packages or [""])[0])
        if package is None:
            return _report(messages) or 1
        draft = BoardDraft("preview", derivative, package)
    _report(messages)
    try:
        imp = netlist.prepare(Path(args.file), draft, args.ref or "", _netlist_rules(args))
    except netlist.NetlistError as e:
        raise NewBoardError([str(e)]) from None
    for line in netlist.report_lines(imp):
        print(line)
    if args.board:
        print("nothing written: the Studio board page applies the preview (conflicts only after confirmation)")
    return 1 if imp.errors or any(c.action == netlist.ERROR for c in imp.changes) else 0


def _netlist_options(parser) -> None:
    parser.add_argument("--ref", help="reference of the MCU in the netlist (default: the component whose pin names "
                                      "are pins of the package)")
    parser.add_argument("--rules", help="YAML file with `rules: [{ net: <glob>, kind: <kind>|ignore, name: ..., "
                                        "role: ..., instance: ..., active: ..., pull: ... }]`")
    parser.add_argument("--map", action="append", default=[], metavar="NET=KIND[:NAME[:ROLE]]",
                        help="one rule on the command line (glob on the net name; kind ignore leaves it out)")


def add_arguments(parser) -> None:
    """The `board` subcommand of `nxconfig` (tools/nxconfig/__main__.py) and of this file's own command line."""
    sub = parser.add_subparsers(dest="board_command", required=True)
    n = sub.add_parser("new", help="create boards/<name>/board.yaml, its README and the board port from a "
                                   "derivative and a package of the mcudb (TOOL-72)")
    n.add_argument("name", help="board name (folder name and NX_BOARD)")
    n.add_argument("--mcu", required=True, help="derivative of the mcudb the board is built on")
    n.add_argument("--package", help="package of the derivative (default: its first)")
    n.add_argument("--dir", help="target folder (default: <root>/boards/<name>)")
    n.add_argument("--root", help="workspace root the board and its port go into (default: the tree)")
    n.add_argument("--description", default="", help="board.description")
    n.add_argument("--supply-v", type=float, help="supply voltage of the MCU")
    n.add_argument("--sosc-hz", type=int, help="frequency of the fitted crystal (left out: none fitted)")
    n.add_argument("--debug-interface", default="", choices=("",) + DEBUG_INTERFACES, help="debug interface")
    n.add_argument("--debug-probe", default="", help=f"debug probe, e.g. one of {', '.join(DEBUG_PROBES)}")
    n.add_argument("--adc-reference-mv", type=int, help="voltage at VREFH (the family port may need it)")
    n.add_argument("--can", action="append", default=[], metavar="NAME=INSTANCE[:RX,TX]",
                   help="CAN connection; without pins the first free ones that can carry the function")
    n.add_argument("--uart", action="append", default=[], metavar="NAME=INSTANCE[:RX,TX]", help="UART")
    n.add_argument("--spi", action="append", default=[], metavar="NAME=INSTANCE[:SCK,SIN,SOUT,PCS]",
                   help="SPI bus")
    n.add_argument("--i2c", action="append", default=[], metavar="NAME=INSTANCE[:SDA,SCL]",
                   help="I2C bus on an I2C instance or a FlexIO; on a FlexIO the data pin D<n> is derived from "
                        "the pin, a pin carrying two is given as PIN/D<n>")
    n.add_argument("--adc", action="append", default=[], metavar="NAME=INSTANCE:CHANNEL[:PIN]",
                   help="analog input")
    n.add_argument("--in", action="append", default=[], dest="input", metavar="NAME=PIN[:active[:pull]]",
                   help="digital input")
    n.add_argument("--out", action="append", default=[], dest="output", metavar="NAME=PIN[:active]",
                   help="digital output")
    n.add_argument("--device", action="append", default=[], metavar="NAME=PART",
                   help="fitted component; the module comes from the manifest that lists the part")
    n.add_argument("--phy", action="append", default=[], metavar="BUS=DEVICE",
                   help="transceiver of a CAN connection (a device of --device)")
    n.add_argument("--no-port", action="store_true",
                   help="do not write ecu/platforms/<name>/Options.cmake (since ARCH-58 the board builds without "
                        "it; the file is only the hook for a justified exception)")
    n.add_argument("--netlist", help="KiCad netlist (.net or .xml) the pins are taken from (TOOL-76); the other "
                                     "options come on top of it")
    _netlist_options(n)
    n.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    n.add_argument("--mcudb", type=Path, help="derivative database (tests)")
    n.add_argument("--module-dir", action="append", default=[], type=Path, help="module directory (tests)")
    ls = sub.add_parser("list", help="derivatives, packages and what a package offers")
    ls.add_argument("--mcu", help="derivative; without it every derivative of the database")
    ls.add_argument("--package", help="package; with --mcu it lists the instances of the package")
    ls.add_argument("--function", help="pin multiplexing signal (e.g. FlexCAN0_RX): every pin that can carry it")
    ls.add_argument("--name", help="board name used in the preview")
    ls.add_argument("--mcudb", type=Path, help="derivative database (tests)")
    nl = sub.add_parser("netlist", help="pins of a KiCad netlist: proposals net -> pin -> signal, checked against "
                                        "the pin multiplexing, and what an import would change (TOOL-76)")
    nl.add_argument("file", help="KiCad netlist (.net S-expression or .xml)")
    nl.add_argument("--mcu", help="derivative of the mcudb (without --board)")
    nl.add_argument("--package", help="package of the derivative (default: its first)")
    nl.add_argument("--board", help="existing board.yaml: preview against its rows (every row counts as typed)")
    _netlist_options(nl)
    nl.add_argument("--mcudb", type=Path, help="derivative database (tests)")


def run(args) -> int:
    """Runs the parsed `board` subcommand; raises NewBoardError for an invalid request."""
    if args.board_command == "netlist":
        return _netlist(args)
    return _list(args) if args.board_command == "list" else _new(args)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nxconfig board", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except NewBoardError as e:
        for problem in e.problems:
            print(f"error: {problem}")
        return 1


if __name__ == "__main__":                      # `python -m nxconfig.newboard ...` from tools/; normally the
    raise SystemExit(main())                    # subcommand `nxconfig board new` calls run() directly
