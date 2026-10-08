"""Board pins from a schematic netlist (TOOL-76): KiCad only, by decision of the user.

Core function without Qt, used by the command line (`nxconfig board netlist`, `nxconfig board new --netlist`) and
by neXtBSW Studio (board wizard and board page, "Aus KiCad-Netzliste …").

Decisions of the user
---------------------
  - 2026-09-22 (tool72-schaltplan): the new-board wizard of TOOL-72 left room for it - every row of the pin table
    carries its source (`newboard.PinSource`). This module is that second source (`newboard.KICAD`); it fills the
    same `BoardDraft` through the same constructors (`bus`, `dio`, `analog`, ...) and the same checks.
  - 2026-09-28 (q-tool76-netzliste): **only KiCad**. Both netlist exports of Eeschema are read - the
    S-expression file (`.net`, "KiCad" export, the default since KiCad 5) and the XML file (`.xml`, input of the
    BOM scripts). They carry the same tree, so one reader serves both.

What is taken from the netlist, and what is not
-----------------------------------------------
The netlist knows the **wiring**: which pin of which component sits on which net, and the name the designer gave
the net. From that this module

  1. finds the MCU among the components (the one whose pin names are pins of the chosen package; `ref=` decides
     when the netlist has more than one),
  2. takes per named net the MCU pin(s) on it, resolving the pin *name* of the symbol ("PTE4", "PTE4/CAN0_RX",
     "N2HET1_15") against the pins of the package - the pin multiplexing tables of the mcudb know pin names, not
     pin numbers, so a symbol that only numbers its pins cannot be matched and is reported,
  3. proposes per pin a signal (`Proposal`): kind of entry, entry name, role, instance and channel - from the
     user's own rules first (`Rule`, glob on the net name), then from patterns (net name tokens against the
     functions the pin can carry in the package), and leaves a pin it cannot classify unassigned,
  4. groups the proposals into board entries and checks every one against the pin multiplexing of the package
     (`BoardDraft.check_entry`: pin exists, function possible on the pin, double use, debug/reset/boot pins).

What the netlist does **not** know stays work in the wizard, and this module does not guess it: the crystal
frequency (a hint names the crystal on EXTAL and its value, nothing is set), assembly variants, the part types of
the fitted components and their roles (a hint names the component on a bus and the module whose manifest lists its
value, no device is created), active levels beyond the naming convention of the net, pulls and scales. Every row
the import writes keeps `verify: true` where the board file has it.

Nothing entered by hand is overwritten without a preview
--------------------------------------------------------
`preview()` compares the grouped entries with what the draft already holds and says per entry what would happen:
`add` (new), `same` (identical, nothing to do), `replace` (an earlier import of the same entry, updated), `conflict`
(an entry typed in by hand has that name, or one of the pins is used by a typed entry) or `error` (the pin
multiplexing forbids it). `apply()` carries out `add` and `replace`, and a `conflict` **only** when its key is
passed in `accept` - the caller shows the preview first (the Studio as a table with a check box per conflict, the
command line as a list, writing only with `--replace NAME`).

Review question 3: nothing here knows a family. Instance names, channel patterns and pin names come from the
derivative and its pin multiplexing table; the patterns only know words a designer writes into a net name (CAN, RX,
MOSI, LED, ...). The host tests run on invented derivatives in two naming styles.
"""

import fnmatch
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import config as nxcfg
from . import newboard as nb
from .messages import Message

TOOL = "KiCad"
SUFFIXES = (".net", ".xml")

# ----------------------------------------------------------------------------------------------- reading


class NetlistError(Exception):
    """The file is not a KiCad netlist (or not readable): nothing was proposed."""


@dataclass(frozen=True)
class Node:
    """One pin of one component on a net."""

    ref: str                                    # U1
    pin: str                                    # pin number of the symbol ("27", "A3")
    name: str = ""                              # pin name of the symbol ("PTE4", "PTE4/CAN0_RX", "~{RESET}")
    type: str = ""                              # KiCad pin type (input, bidirectional, power_in, ...)


@dataclass
class Component:
    ref: str
    value: str = ""
    footprint: str = ""
    lib: str = ""
    part: str = ""
    pin_names: dict = field(default_factory=dict)       # pin number -> pin name (from libparts)


@dataclass
class Net:
    code: str
    name: str
    nodes: tuple = ()

    @property
    def label(self) -> str:
        """The name without the sheet path KiCad puts in front of a local label (`/sheet1/CAN_RX` -> `CAN_RX`)."""
        return self.name.rsplit("/", 1)[-1] if "/" in self.name else self.name

    @property
    def named(self) -> bool:
        """False for the nets KiCad names itself (`Net-(U1-Pad3)`, `unconnected-(U1-PTA0-Pad12)`)."""
        label = self.label
        return bool(label) and not re.match(r"^(Net-\(|unconnected-\(|N\$\d)", label, re.IGNORECASE)


@dataclass
class Netlist:
    file: Path
    format: str                                 # "sexpr" | "xml"
    tool: str = ""                              # Eeschema version from the design block, if any
    source: str = ""                            # the schematic the netlist was exported from
    components: dict = field(default_factory=dict)      # ref -> Component
    nets: list = field(default_factory=list)            # list[Net]

    def nodes_of(self, ref: str) -> list:
        return [(net, node) for net in self.nets for node in net.nodes if node.ref == ref]

    def pin_name(self, node: Node) -> str:
        """The pin name of a node: from the node itself (KiCad 6+ `pinfunction`), else from the libparts."""
        if node.name:
            return node.name
        component = self.components.get(node.ref)
        return component.pin_names.get(node.pin, "") if component else ""


def _tokens(text: str):
    """S-expression tokens: '(', ')', quoted strings (with \\ escapes) and bare atoms."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            i += 1
            out = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                out.append(text[i])
                i += 1
            if i >= n:
                raise NetlistError("unterminated string")
            i += 1
            yield ("str", "".join(out))
        else:
            start = i
            while i < n and text[i] not in ' \t\r\n()"':
                i += 1
            yield ("str", text[start:i])


def parse_sexpr(text: str) -> list:
    """The S-expression as nested lists of strings."""
    stack: list = [[]]
    for token in _tokens(text):
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) < 2:
                raise NetlistError("unbalanced ')'")
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(token[1])
    if len(stack) != 1:
        raise NetlistError("unbalanced '(' - the file ends inside an expression")
    return stack[0]


def _children(node: list, key: str) -> list:
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == key]


def _value(node: list, key: str, default: str = "") -> str:
    for c in _children(node, key):
        if len(c) > 1 and isinstance(c[1], str):
            return c[1]
    return default


def _from_sexpr(tree: list, file: Path) -> Netlist:
    export = next((c for c in tree if isinstance(c, list) and c and c[0] == "export"), None)
    if export is None:
        raise NetlistError("no (export ...) expression - not a KiCad netlist")
    design = next(iter(_children(export, "design")), ["design"])
    netlist = Netlist(file, "sexpr", _value(design, "tool"), _value(design, "source"))
    parts: dict = {}
    for libparts in _children(export, "libparts"):
        for libpart in _children(libparts, "libpart"):
            pins = {}
            for pins_node in _children(libpart, "pins"):
                for pin in _children(pins_node, "pin"):
                    pins[_value(pin, "num")] = _value(pin, "name")
            parts[(_value(libpart, "lib"), _value(libpart, "part"))] = pins
    for components in _children(export, "components"):
        for comp in _children(components, "comp"):
            source = next(iter(_children(comp, "libsource")), ["libsource"])
            c = Component(_value(comp, "ref"), _value(comp, "value"), _value(comp, "footprint"),
                          _value(source, "lib"), _value(source, "part"))
            c.pin_names = dict(parts.get((c.lib, c.part), {}))
            netlist.components[c.ref] = c
    for nets in _children(export, "nets"):
        for net in _children(nets, "net"):
            nodes = tuple(Node(_value(n, "ref"), _value(n, "pin"), _value(n, "pinfunction"), _value(n, "pintype"))
                          for n in _children(net, "node"))
            netlist.nets.append(Net(_value(net, "code"), _value(net, "name"), nodes))
    return netlist


def _from_xml(root: ET.Element, file: Path) -> Netlist:
    if root.tag != "export":
        raise NetlistError(f"root element <{root.tag}> - not a KiCad netlist (<export>)")
    design = root.find("design")
    netlist = Netlist(file, "xml", (design.findtext("tool") or "") if design is not None else "",
                      (design.findtext("source") or "") if design is not None else "")
    parts: dict = {}
    for libpart in root.iterfind("libparts/libpart"):
        parts[(libpart.get("lib", ""), libpart.get("part", ""))] = {
            p.get("num", ""): p.get("name", "") for p in libpart.iterfind("pins/pin")}
    for comp in root.iterfind("components/comp"):
        source = comp.find("libsource")
        c = Component(comp.get("ref", ""), comp.findtext("value") or "", comp.findtext("footprint") or "",
                      source.get("lib", "") if source is not None else "",
                      source.get("part", "") if source is not None else "")
        c.pin_names = dict(parts.get((c.lib, c.part), {}))
        netlist.components[c.ref] = c
    for net in root.iterfind("nets/net"):
        nodes = tuple(Node(n.get("ref", ""), n.get("pin", ""), n.get("pinfunction", ""), n.get("pintype", ""))
                      for n in net.iterfind("node"))
        netlist.nets.append(Net(net.get("code", ""), net.get("name", ""), nodes))
    return netlist


def parse(text: str, file: Path = Path("netlist.net")) -> Netlist:
    """A KiCad netlist from its text: S-expression (`.net`) or XML (`.xml`), told apart by the first character."""
    head = text.lstrip("﻿ \t\r\n")[:1]
    if head == "<":
        try:
            return _from_xml(ET.fromstring(text), Path(file))
        except ET.ParseError as e:
            raise NetlistError(f"XML cannot be read: {e}") from None
    if head == "(":
        return _from_sexpr(parse_sexpr(text), Path(file))
    raise NetlistError("neither an S-expression nor an XML file - export the netlist from Eeschema "
                       "(File > Export > Netlist, format KiCad)")


def read(file: Path) -> Netlist:
    """Reads a KiCad netlist file; raises NetlistError with the reason."""
    file = Path(file)
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise NetlistError(f"{file}: {e}") from None
    try:
        return parse(text, file)
    except NetlistError as e:
        raise NetlistError(f"{file.name}: {e}") from None


# ----------------------------------------------------------------------------------------------- the MCU


def _clean(name: str) -> str:
    """KiCad overbar `~{RESET}` -> `RESET`; blanks trimmed."""
    return re.sub(r"~\{([^}]*)\}", r"\1", name or "").replace("~", "").strip()


def resolve_pin(name: str, pins) -> str:
    """The package pin a symbol pin name stands for ("" if none).

    Symbols name a pin in several ways: `PTE4`, `PTE4/CAN0_RX`, `PTE4 (CAN0_RX)`, `N2HET1_15`. The whole name is
    tried first, then every part between `/`, `,`, `|`, blanks and brackets - never parts of an underscore name,
    because pin names of other families carry underscores themselves."""
    by_upper = {str(p).upper(): str(p) for p in pins}
    name = _clean(name)
    for candidate in [name] + re.split(r"[/,|\s()\[\]]+", name):
        found = by_upper.get(candidate.upper())
        if found:
            return found
    return ""


@dataclass(frozen=True)
class McuChoice:
    ref: str
    matched: int                                # pins of the package the component has on nets
    value: str = ""


def mcu_candidates(netlist: Netlist, package: nb.Package) -> list[McuChoice]:
    """Components ordered by how many of their pin names are pins of the package (best first)."""
    pins = package.pins
    scores = []
    for ref, component in netlist.components.items():
        names = set(component.pin_names.values()) | {netlist.pin_name(n) for _net, n in netlist.nodes_of(ref)}
        matched = {resolve_pin(n, pins) for n in names} - {""}
        if matched:
            scores.append(McuChoice(ref, len(matched), component.value))
    derivative = package.derivative.name.upper()
    return sorted(scores, key=lambda c: (-c.matched, derivative not in c.value.upper(), c.ref))


def find_mcu(netlist: Netlist, package: nb.Package, ref: str = "") -> tuple[str, list[Message]]:
    """The reference of the MCU in the netlist, with the reason; ("", errors) if there is none."""
    candidates = mcu_candidates(netlist, package)
    where = netlist.file
    if ref:
        if ref not in netlist.components:
            return "", [Message("error", f"component {ref} is not in the netlist "
                                         f"(components: {', '.join(sorted(netlist.components)) or '-'})", where)]
        found = next((c for c in candidates if c.ref == ref), None)
        if found is None:
            return "", [Message("error", f"no pin name of {ref} ({netlist.components[ref].value}) is a pin of "
                                         f"{package.derivative.name} in {package.package}", where)]
        return ref, []
    if not candidates:
        return "", [Message("error", f"no component of the netlist has pin names of {package.derivative.name} in "
                                     f"{package.package} (e.g. {', '.join(sorted(package.pins)[:3])}): the pin "
                                     "multiplexing tables know pin names, not numbers - name the pins in the symbol "
                                     "or choose the MCU with ref", where)]
    best = candidates[0]
    messages = []
    if len(candidates) > 1 and candidates[1].matched * 2 > best.matched:
        messages.append(Message("warning", f"{best.ref} ({best.value}, {best.matched} pins) taken as the MCU; "
                                           f"{candidates[1].ref} ({candidates[1].value}, {candidates[1].matched} "
                                           "pins) is close - choose with ref if that is wrong", where))
    return best.ref, messages


# ----------------------------------------------------------------------------------------------- rules


# Words of a net name and what they say. Upper case, compared with the tokens of the net name.
ROLE_WORDS = {
    "rx": {"RX", "RXD", "CANRX", "RXCAN"},
    "tx": {"TX", "TXD", "CANTX", "TXCAN"},
    "sck": {"SCK", "SCLK", "CLK", "SPC"},
    "sin": {"SIN", "MISO", "SDI", "SOMI", "DIN"},
    "sout": {"SOUT", "MOSI", "SDO", "SIMO", "DOUT"},
    "pcs": {"PCS", "CS", "NCS", "SS", "NSS", "CSN", "CSB", "SCS"},
    "sda": {"SDA"},
    "scl": {"SCL"},
}
CLASS_WORDS = {
    "can": {"CAN", "FDCAN", "FLEXCAN", "DCAN", "MCAN"},
    "uart": {"UART", "USART", "LPUART", "SCI", "LIN", "SERIAL", "CONSOLE", "DEBUG", "LOG"},
    "spi": {"SPI", "LPSPI", "MIBSPI", "DSPI", "QSPI"},
    "i2c": {"I2C", "IIC", "TWI", "LPI2C"},
}
ADC_WORDS = {"ADC", "AIN", "AN", "ANA", "ANALOG", "SENSE", "SNS", "MEAS", "VBAT", "VSUP", "TEMP", "NTC", "POT",
             "POTI", "ISENSE", "VSENSE", "IS", "IMON", "VMON", "CUR", "CURRENT", "VOLT"}
PWM_WORDS = {"PWM", "GATE", "HS", "LS", "INH", "INL", "HIN", "LIN_GATE"}
HIGH_WORDS = {"H", "HS", "HI", "HIGH", "TOP", "INH", "HIN", "UP"}
LOW_WORDS = {"L", "LS", "LO", "LOW", "BOT", "BOTTOM", "INL", "LIN", "DN"}
CAPTURE_WORDS = {"CAP", "ICU", "IC", "HALL", "ENC", "ENCODER", "FREQ", "TACHO", "TACH", "SPEED", "CAPTURE"}
OUTPUT_WORDS = {"LED", "EN", "ENA", "ENABLE", "OUT", "DO", "DOUT", "DRV", "RELAY", "RLY", "BUZZER", "BEEP", "PWR",
                "ON", "OFF", "SEL", "CTRL", "INHIBIT", "RST_OUT", "WDI"}
INPUT_WORDS = {"BTN", "BUTTON", "SW", "KEY", "IN", "DI", "DIN", "INT", "IRQ", "FAULT", "FLT", "ERR", "ERROR", "DIAG",
               "STAT", "STATUS", "WAKE", "WAKEUP", "DET", "DETECT", "PG", "PGOOD", "RDY", "READY", "ALERT", "SENS"}
LOW_ACTIVE_SUFFIXES = {"N", "B", "L", "NEG"}


@dataclass(frozen=True)
class Rule:
    """A user rule: nets whose name matches `net` (glob, case-insensitive; `re:` for a regular expression) become
    this kind of entry. Empty fields are derived as without a rule. `kind: ignore` leaves the net out."""

    net: str
    kind: str = ""                              # EntryKind id or "ignore"
    name: str = ""                              # entry name; "{net}" is the cleaned net name
    role: str = ""
    instance: str = ""
    active: str = ""
    pull: str = ""

    def matches(self, net: str) -> bool:
        if self.net.startswith("re:"):
            return re.fullmatch(self.net[3:], net, re.IGNORECASE) is not None
        return fnmatch.fnmatchcase(net.upper(), self.net.upper())

    def describe(self) -> str:
        return f"rule {self.net} -> {self.kind or '(auto)'}{f' {self.name}' if self.name else ''}"


def rule_from_text(text: str) -> Rule:
    """`NET=KIND[:NAME[:ROLE]]` of the command line (`--map CAN_*=can:VEH`); raises NetlistError."""
    net, sep, rest = str(text).partition("=")
    if not sep or not net:
        raise NetlistError(f"--map {text!r}: expected NET=KIND[:NAME[:ROLE]]")
    parts = (rest.split(":") + ["", "", ""])[:3]
    return _checked_rule(Rule(net, parts[0], parts[1], parts[2]), f"--map {text}")


def _checked_rule(rule: Rule, where: str) -> Rule:
    if rule.kind and rule.kind != "ignore" and rule.kind not in nb.KIND_BY_ID:
        raise NetlistError(f"{where}: unknown kind {rule.kind!r} (known: ignore, {', '.join(nb.KIND_BY_ID)})")
    if rule.active and rule.active not in ("high", "low"):
        raise NetlistError(f"{where}: active is high or low, not {rule.active!r}")
    if rule.net.startswith("re:"):
        try:
            re.compile(rule.net[3:])
        except re.error as e:
            raise NetlistError(f"{where}: {e}") from None
    return rule


def read_rules(file: Path) -> list[Rule]:
    """Rules from a YAML file: `rules: [{ net: "CAN_*", kind: can, name: VEH }, ...]`; raises NetlistError."""
    try:
        data = YAML(typ="safe").load(Path(file).read_text(encoding="utf-8"))
    except (OSError, YAMLError) as e:
        raise NetlistError(f"{file}: {e}") from None
    items = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise NetlistError(f"{Path(file).name}: expected a list `rules:`")
    rules = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("net"):
            raise NetlistError(f"{Path(file).name}: rules[{i}] needs `net:`")
        unknown = set(item) - {"net", "kind", "name", "role", "instance", "active", "pull"}
        if unknown:
            raise NetlistError(f"{Path(file).name}: rules[{i}]: unknown key(s) {', '.join(sorted(unknown))}")
        rules.append(_checked_rule(Rule(**{k: str(v) for k, v in item.items()}), f"{Path(file).name}: rules[{i}]"))
    return rules


# ----------------------------------------------------------------------------------------------- proposals


@dataclass
class Proposal:
    """One MCU pin on one named net and the signal proposed for it. The caller may change every field before
    `entries()` groups the proposals (manual correction); `kind == ""` leaves the pin out."""

    net: str                                    # net name without the sheet path
    pin: str                                    # package pin (PTE4)
    number: str = ""                            # pin number of the symbol
    kind: str = ""                              # EntryKind id, "" = not assigned
    name: str = ""                              # entry name (a bus: shared by its pins)
    role: str = ""                              # bus role, PWM pair side ("UH"), "pin" otherwise
    instance: str = ""
    channel: str = ""                           # ADC SE3 / timer CH2
    index: int | None = None                    # SPI chip select number
    active: str = ""                            # high | low (DIO, PWM)
    pull: str = ""
    reason: str = ""                            # why this proposal (rule, pattern, nothing matched)
    peers: tuple = ()                           # other components on the net: ((ref, value, pin name), ...)
    full_net: str = ""                          # net name with the sheet path

    @property
    def assigned(self) -> bool:
        return bool(self.kind)


@dataclass
class Suggestion:
    netlist: Netlist
    mcu: str
    proposals: list = field(default_factory=list)       # list[Proposal]
    hints: list = field(default_factory=list)           # what the netlist hints at, but the wizard decides
    messages: list = field(default_factory=list)
    skipped: list = field(default_factory=list)         # (net, pin number, pin name, why) of MCU pins left out


def net_words(net: str) -> list[str]:
    """Upper-case tokens of a net name: `/CAN0_RX` -> [CAN0, RX], `LED_~{ERR}` -> [LED, ERR]."""
    return [t for t in re.split(r"[^A-Za-z0-9]+", _clean(net).upper()) if t]


def _active_low(net: str) -> bool:
    raw = net.strip()
    words = net_words(raw)
    if raw.startswith("~") or raw.endswith("#") or raw.startswith("!"):
        return True
    if words and words[-1] in LOW_ACTIVE_SUFFIXES and len(words) > 1:
        return True
    first = words[0] if words else ""
    return len(first) > 1 and first[0] == "N" and first[1:] in (OUTPUT_WORDS | INPUT_WORDS | {"RESET", "RST", "CS"})


def entry_name(net: str) -> str:
    """A board entry name from a net name: letters, digits and '_', starting with a letter."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", _clean(net.rsplit("/", 1)[-1])).strip("_")
    name = re.sub(r"_+", "_", name)
    if not name:
        return "NET"
    if not name[0].isalpha():
        name = f"N_{name}"
    return name[:48]


def _split_function(function: str, package: nb.Package) -> tuple[str, str]:
    """`FlexCAN0_RX` -> (FlexCAN0, RX) for an instance of the derivative; ("", "") otherwise."""
    for instance in sorted(package.instances, key=len, reverse=True):
        if function.startswith(f"{instance}_"):
            return instance, function[len(instance) + 1:]
    return "", ""


def _instance_named(words: list, instance: str) -> bool:
    """True if a word of the net names the instance: CAN0 names FlexCAN0, LPUART1 names LPUART1, SPI1 names it."""
    upper = instance.upper()
    return any(any(ch.isdigit() for ch in w) and (upper == w or upper.endswith(w) or w.endswith(upper))
               for w in words)


def _classify_bus(p: Proposal, words: list, package: nb.Package) -> tuple[int, str]:
    best, why = 0, ""
    role_by_suffix = {kind.id: {suffix: role for role, suffix in kind.pins} for kind in nb.ENTRY_KINDS}
    for function in package.functions(p.pin):
        instance, suffix = _split_function(function, package)
        klass = str(package.instances.get(instance, {}).get("class", ""))
        if klass not in CLASS_WORDS:
            continue
        index = None
        match = re.fullmatch(r"PCS(\d+)", suffix)
        if match:
            role, index = "pcs", int(match.group(1))
        else:
            role = role_by_suffix.get(klass, {}).get(suffix, "")
        if not role:
            continue
        score = 0
        if suffix in words or words and ROLE_WORDS.get(role, set()) & set(words):
            score += 2
        if not score:
            continue                                # the role must be in the name: FlexCAN0_RX on a LED net is no bus
        if _instance_named(words, instance):
            score += 2
        if CLASS_WORDS[klass] & set(words) or any(w.startswith(tuple(CLASS_WORDS[klass])) for w in words):
            score += 1
        if score > best:
            best = score
            p.kind, p.instance, p.role, p.index = klass, instance, role, index
            why = f"{function} on {p.pin}, the net name says {role}"
    return best, why


def _classify_channel(p: Proposal, words: list, package: nb.Package, kinds: tuple) -> str:
    """ADC, PWM or capture channel of the pin, if the net name says it."""
    for function in package.functions(p.pin):
        instance, rest = _split_function(function, package)
        spec = package.instances.get(instance, {})
        klass = spec.get("class")
        if klass == "adc" and nb.ADC in kinds and ADC_WORDS & set(words):
            pattern = str(spec.get("channel_pattern") or "")
            if pattern and re.fullmatch(pattern, rest):
                p.kind, p.instance, p.channel, p.role = nb.ADC, instance, rest, "pin"
                return f"{function} on {p.pin}, the net name says analog"
        if klass == "timer" and re.fullmatch(r"CH\d+", rest):
            if nb.CAPTURE in kinds and CAPTURE_WORDS & set(words):
                p.kind, p.instance, p.channel, p.role = nb.CAPTURE, instance, rest, "pin"
                return f"{function} on {p.pin}, the net name says capture"
            if nb.PWM in kinds and (PWM_WORDS & set(words) or {"PWM"} & set(words)):
                side = "H" if HIGH_WORDS & set(words) else "L" if LOW_WORDS & set(words) else ""
                if side:
                    p.kind, p.instance, p.channel = nb.PWM, instance, rest
                    pair = [w for w in words if w not in PWM_WORDS | HIGH_WORDS | LOW_WORDS | {"PWM"}
                            and not _instance_named([w], instance)]
                    p.role = f"{pair[-1] if pair else 'P'}{side}"
                    return f"{function} on {p.pin}, the net name says PWM {'high' if side == 'H' else 'low'} side"
    return ""


def classify(p: Proposal, package: nb.Package, rules=()) -> Proposal:
    """Fills kind, name, role, instance, channel and active of one proposal from the rules, else from patterns."""
    words = net_words(p.net)
    rule = next((r for r in rules if r.matches(p.net) or r.matches(p.full_net)), None)
    kinds = tuple(nb.KIND_BY_ID)
    if rule is not None:
        if rule.kind == "ignore":
            p.kind, p.reason = "", f"{rule.describe()}: left out"
            return p
        if rule.kind:
            kinds = (rule.kind,)
    special = special_net(p.net)
    if special and rule is None:
        p.kind, p.reason = "", f"{special} net - not a board entry (the wizard asks for debug and crystal itself)"
        return p
    why = ""
    if set(kinds) & {nb.CAN, nb.UART, nb.SPI, nb.I2C}:
        score, why = _classify_bus(p, words, package)
        if p.kind and p.kind not in kinds:
            p.kind, why = "", ""
        if p.kind and score < 3 and rule is None:
            why = f"{why} (weak: only the role is in the name)"
    if not p.kind:
        why = _classify_channel(p, words, package, kinds)
    if not p.kind and "GPIO" in package.functions(p.pin):
        if nb.DIO_OUT in kinds and (OUTPUT_WORDS & set(words) or rule is not None and rule.kind == nb.DIO_OUT):
            p.kind, p.role = nb.DIO_OUT, "pin"
            why = "GPIO, the net name says output" if OUTPUT_WORDS & set(words) else "GPIO"
        elif nb.DIO_IN in kinds and (INPUT_WORDS & set(words) or rule is not None and rule.kind == nb.DIO_IN):
            p.kind, p.role = nb.DIO_IN, "pin"
            why = "GPIO, the net name says input" if INPUT_WORDS & set(words) else "GPIO"
    if rule is not None and rule.kind and not p.kind:
        p.reason = f"{rule.describe()}: {p.pin} cannot carry a {rule.kind} function in {package.package}"
        return p
    if not p.kind:
        p.reason = "no pattern matched the net name: choose the kind by hand"
        return p
    if p.kind in (nb.DIO_IN, nb.DIO_OUT, nb.PWM):
        p.active = "low" if _active_low(p.net) else "high"
    if p.kind in (nb.DIO_IN, nb.DIO_OUT, nb.ADC, nb.CAPTURE):
        p.name = entry_name(p.net)
    if rule is not None:
        p.instance = rule.instance or p.instance
        p.role = rule.role or p.role
        p.active = rule.active or p.active
        p.pull = rule.pull or p.pull
        if rule.name:
            p.name = rule.name.replace("{net}", entry_name(p.net))
        why = f"{rule.describe()}: {why}"
    p.reason = why
    return p


def special_net(net: str) -> str:
    """"debug", "reset", "oscillator" or "boot" if the net name says so (SWDIO, ~{RESET}, EXTAL), else ""."""
    cleaned = _clean(net)
    for kind, pattern, _why in nb.SPECIAL_PIN_KINDS:
        if pattern.search(cleaned):
            return kind
    return ""


def _bus_name(proposals: list) -> str:
    """Name of a bus from its nets: the words they share, without role words (SBC_MOSI, SBC_SCK -> SBC)."""
    role_words = {w for words in ROLE_WORDS.values() for w in words}
    shared = None
    for p in proposals:
        words = [w for w in net_words(p.net) if w not in role_words]
        shared = words if shared is None else [w for w in shared if w in words]
    name = "_".join(shared or [])
    return entry_name(name) if name else proposals[0].instance


def suggest(netlist: Netlist, draft: nb.BoardDraft, ref: str = "", rules=()) -> Suggestion:
    """Every MCU pin on a named net with its proposal; hints for what the wizard decides itself."""
    package = draft.package
    mcu, messages = find_mcu(netlist, package, ref)
    suggestion = Suggestion(netlist, mcu, messages=list(messages))
    if not mcu:
        return suggestion
    for net in netlist.nets:
        for node in net.nodes:
            if node.ref != mcu:
                continue
            name = netlist.pin_name(node)
            pin = resolve_pin(name, package.pins)
            if not pin:
                suggestion.skipped.append((net.label, node.pin, name, "not a pin of the package (supply, ground, "
                                                                      "or a symbol without pin names)"))
                continue
            if not net.named:
                suggestion.skipped.append((net.label, node.pin, name, "net without a name"))
                continue
            peers = tuple((n.ref, netlist.components[n.ref].value if n.ref in netlist.components else "",
                           netlist.pin_name(n)) for n in net.nodes if n.ref != mcu)
            suggestion.proposals.append(classify(Proposal(net.label, pin, node.pin, peers=peers,
                                                          full_net=net.name), package, rules))
    name_buses(suggestion.proposals)
    suggestion.hints = hints(suggestion, draft)
    return suggestion


def name_buses(proposals: list) -> None:
    """Gives every bus pin without a name the name of its bus (pins of one instance share it)."""
    groups: dict = {}
    for p in proposals:
        if p.kind in (nb.CAN, nb.UART, nb.SPI, nb.I2C, nb.PWM) and not p.name:
            groups.setdefault((p.kind, p.instance), []).append(p)
    for (_kind, _instance), members in groups.items():
        name = _bus_name(members)
        for p in members:
            p.name = name


def reassign(p: Proposal, kind: str, package: nb.Package, name: str = "") -> Proposal:
    """Manual correction of one pin: the proposal again, as if a rule for exactly this net named `kind` ("" leaves
    the pin out). Instance, role and channel follow from what the pin can carry; the caller replaces `p` with it."""
    fresh = Proposal(p.net, p.pin, p.number, peers=p.peers, full_net=p.full_net)
    if not kind:
        fresh.reason = "left out by hand"
        return fresh
    classify(fresh, package, [Rule("re:" + re.escape(p.net), kind, name)])
    fresh.reason = "by hand: " + fresh.reason.split(": ", 1)[-1]
    return fresh


def rename(proposals: list, p: Proposal, name: str) -> None:
    """Manual correction of a name. The pins of a bus or a PWM group share their name, so all of them follow."""
    old = p.name
    grouped = p.kind in (nb.CAN, nb.UART, nb.SPI, nb.I2C, nb.PWM)
    for other in proposals:
        if other is p or grouped and other.kind == p.kind and other.name == old:
            other.name = name


# ----------------------------------------------------------------------------------------------- hints

FREQUENCY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([kKmM]?)(?:hz|Hz|HZ)?\b")


def frequency_of(value: str) -> int | None:
    """`8MHz`, `16M`, `32.768kHz` -> Hz; None if the value names no frequency."""
    match = FREQUENCY_RE.search(value or "")
    # without "Hz" only a mega suffix counts (crystals are labelled "16M"; "10k" is a resistor)
    if not match or not (re.search(r"hz", value, re.IGNORECASE) or match.group(2) in ("m", "M")):
        return None
    number = float(match.group(1).replace(",", "."))
    return int(round(number * {"": 1, "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6}[match.group(2)]))


def hints(suggestion: Suggestion, draft: nb.BoardDraft, module_dirs=None) -> list[str]:
    """What the netlist shows but the wizard decides: crystal, debug connector, components on the buses."""
    out = []
    package = draft.package
    netlist = suggestion.netlist
    seen_osc = set()
    for net in netlist.nets:
        mcu_nodes = [n for n in net.nodes if n.ref == suggestion.mcu]
        for node in mcu_nodes:
            pin = resolve_pin(netlist.pin_name(node), package.pins)
            special = package.special(pin) if pin else ""
            if not pin:
                continue
            if special.startswith("oscillator") or special_net(net.label) == "oscillator":
                for n in net.nodes:
                    component = netlist.components.get(n.ref)
                    if n.ref == suggestion.mcu or component is None or n.ref in seen_osc:
                        continue
                    hz = frequency_of(component.value)
                    if hz or n.ref[:1] in ("Y", "X", "Q"):
                        seen_osc.add(n.ref)
                        out.append(f"crystal: {n.ref} ({component.value}) on {pin}"
                                   + (f" - {hz} Hz? " if hz else " - ")
                                   + "the netlist does not say whether it is fitted: enter sosc_hz in the wizard")
            elif special_net(net.label) == "debug":
                others = sorted({n.ref for n in net.nodes if n.ref != suggestion.mcu})
                if others:
                    out.append(f"debug: {pin} ({net.label}) goes to {', '.join(others)} - interface and probe are "
                               "set in the wizard")
    buses: dict = {}
    for p in suggestion.proposals:
        if p.kind in (nb.CAN, nb.SPI, nb.I2C, nb.UART):
            for ref, value, _pin in p.peers:
                if ref.startswith(("U", "IC")):
                    buses.setdefault((p.name, ref, value), None)
    for (bus_name, ref, value) in buses:
        offer = None
        try:
            offer = nb.module_for_part(value, module_dirs or nxcfg.MODULE_DIRS) if value else None
        except Exception:                        # a broken manifest must not stop the import; nxconfig reports it
            offer = None
        out.append(f"component: {ref} ({value or 'no value'}) on {bus_name}"
                   + (f" - the manifest of module {offer.module} lists it" if offer else "")
                   + " - add it as a device with its role in the wizard")
    return out


# ----------------------------------------------------------------------------------------------- entries


def origin_of(netlist: Netlist, nets) -> str:
    names = sorted(dict.fromkeys(nets))
    return f"{netlist.file.name}, net{'s' if len(names) > 1 else ''} {', '.join(names)}"


def entries(suggestion: Suggestion, draft: nb.BoardDraft) -> tuple[list, list[Message]]:
    """Board entries of the assigned proposals, grouped per bus, PWM group or single pin, with the findings of
    grouping (a role twice, a bus on two instances). The pin multiplexing is checked by `preview()`."""
    netlist = suggestion.netlist
    out, messages = [], []
    groups: dict = {}
    for p in suggestion.proposals:
        if p.assigned:
            groups.setdefault((p.kind, p.name), []).append(p)
    for (kind, name), members in groups.items():
        spec = nb.KIND_BY_ID.get(kind)
        path = (spec.path if spec else ()) + (name,)
        if spec is None:
            messages.append(Message("error", f"net {members[0].net}: unknown kind {kind!r}", netlist.file, path))
            continue
        origin = origin_of(netlist, [p.net for p in members])
        if spec.single:
            if len(members) > 1:
                messages.append(Message("error", f"{spec.title} {name}: {len(members)} pins "
                                                 f"({', '.join(p.pin for p in members)}) - one entry has one pin; "
                                                 "rename the others", netlist.file, path))
                continue
            p = members[0]
            if kind in (nb.DIO_IN, nb.DIO_OUT):
                out.append(nb.dio(kind, name, p.pin, p.active or "high", p.pull, origin, nb.KICAD.id))
            elif kind == nb.ADC:
                out.append(nb.analog(name, p.instance, p.channel, p.pin, origin, nb.KICAD.id))
            else:
                out.append(nb.capture(name, p.instance, p.channel, p.pin, origin, nb.KICAD.id))
            continue
        instances = sorted({p.instance for p in members})
        if len(instances) != 1:
            messages.append(Message("error", f"{spec.title} {name}: pins on {', '.join(instances)} - one entry runs "
                                             "on one instance", netlist.file, path))
            continue
        instance = instances[0]
        if kind == nb.PWM:
            sides: dict = {}
            for p in members:
                sides.setdefault(p.role[:-1], {})[p.role[-1:]] = (p.channel, p.pin)
            pairs = [(pair, s["H"], s["L"]) for pair, s in sorted(sides.items()) if "H" in s and "L" in s]
            lonely = sorted(pair for pair, s in sides.items() if not ("H" in s and "L" in s))
            if lonely:
                messages.append(Message("warning", f"PWM group {name}: pair(s) {', '.join(lonely)} without a high "
                                                   "and a low side - left out", netlist.file, path))
            if pairs:
                active = "low" if all(p.active == "low" for p in members) else "high"
                out.append(nb.pwm_group(name, instance, pairs, active, origin, nb.KICAD.id))
            continue
        pins: dict = {}
        for p in members:
            if p.role in pins:
                messages.append(Message("warning", f"{spec.title} {name}: {p.role} twice ({pins[p.role]!r} and "
                                                   f"{p.pin}) - {p.pin} left out; one {p.role} per entry",
                                        netlist.file, path + (p.role,)))
                continue
            pins[p.role] = (p.pin, p.index) if p.role == "pcs" else p.pin
        missing = [role for role, _ in spec.pins if role not in pins]
        if missing and kind != nb.SPI or kind == nb.SPI and set(missing) - {"pcs", "sin"}:
            messages.append(Message("warning", f"{spec.title} {name}: no net for {', '.join(missing)} in the "
                                               "netlist - complete it in the wizard", netlist.file, path))
        entry = nb.bus(kind, name, instance, pins, origin=origin, source_id=nb.KICAD.id)
        # the function names of the draft (bus() builds them from the suffix table, SPI chip selects included)
        for pin in entry.pins:
            pin.function = draft.function_of(kind, pin.role, instance, pin.index)
        out.append(entry)
    return out, messages


# ----------------------------------------------------------------------------------------------- preview and apply

ADD, SAME, REPLACE, CONFLICT, ERROR = "add", "same", "replace", "conflict", "error"


@dataclass
class Change:
    """What importing one entry would do to the draft."""

    action: str
    entry: nb.BoardEntry
    existing: nb.BoardEntry | None = None
    displaced: list = field(default_factory=list)       # other entries whose pins the new one takes
    reason: str = ""
    messages: list = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.entry.kind, self.entry.name)

    def describe(self) -> str:
        spec = nb.KIND_BY_ID[self.entry.kind]
        pins = ", ".join(self.entry.pin_names())
        text = f"{self.action}: {spec.title} {self.entry.name}" + (f" ({self.entry.instance}: {pins})"
                                                                  if self.entry.instance else f" ({pins})")
        return f"{text} - {self.reason}" if self.reason else text


def _same(a: nb.BoardEntry, b: nb.BoardEntry) -> bool:
    key = lambda e: (e.kind, e.instance, sorted((p.role, p.pin, p.index, p.channel) for p in e.pins))  # noqa: E731
    return key(a) == key(b)


def preview(draft: nb.BoardDraft, new_entries: list) -> list[Change]:
    """Per imported entry: add, same, replace (an earlier import), conflict (typed by hand) or error."""
    changes = []
    for entry in new_entries:
        existing = next((e for e in draft.entries if e.kind == entry.kind and e.name == entry.name), None)
        pins = set(entry.pin_names())
        displaced = [e for e in draft.entries if e is not existing and pins & set(e.pin_names())]
        # the pin multiplexing check against the draft as it would be: without the entries the import replaces
        probe = replace(draft, entries=[e for e in draft.entries if e is not existing and e not in displaced])
        found = probe.check_entry(entry)
        errors = [m for m in found if m.severity == "error"]
        if errors:
            changes.append(Change(ERROR, entry, existing, displaced, "; ".join(m.text for m in errors), found))
            continue
        if existing is not None and not displaced and _same(existing, entry):
            changes.append(Change(SAME, entry, existing, [], "already in the board as it is", found))
            continue
        hand = [e for e in [existing, *displaced] if e is not None and e.source_id != nb.KICAD.id]
        if hand:
            what = "; ".join(f"{nb.KIND_BY_ID[e.kind].title} {e.name} ({', '.join(e.pin_names())}, "
                             f"{e.source.describe(e.origin)})" for e in hand)
            changes.append(Change(CONFLICT, entry, existing, displaced,
                                  f"would replace what was entered by hand: {what}", found))
        elif existing is not None or displaced:
            what = ", ".join(e.name for e in [existing, *displaced] if e is not None)
            changes.append(Change(REPLACE, entry, existing, displaced, f"updates the earlier import ({what})",
                                  found))
        else:
            changes.append(Change(ADD, entry, None, [], "new", found))
    return changes


def apply(draft: nb.BoardDraft, changes: list, accept=()) -> list[Change]:
    """Carries out add and replace, a conflict only if its key (kind, name) is in `accept`. Returns what was done."""
    accept = set(accept)
    done = []
    for change in changes:
        if change.action in (SAME, ERROR) or change.action == CONFLICT and change.key not in accept:
            continue
        gone = [e for e in [change.existing, *change.displaced] if e is not None]
        draft.entries = [e for e in draft.entries if not any(e is g for g in gone)]
        if any(m.severity == "error" for m in draft.add(change.entry)):
            draft.entries.extend(gone)          # cannot happen after preview(); keep the draft whole if it does
            continue
        done.append(change)
    return done


def summary(changes: list) -> dict:
    counts = {action: 0 for action in (ADD, SAME, REPLACE, CONFLICT, ERROR)}
    for change in changes:
        counts[change.action] += 1
    return counts


# ----------------------------------------------------------------------------------------------- one call


@dataclass
class Import:
    """The whole import of one netlist into one draft, before anything is applied."""

    suggestion: Suggestion
    entries: list
    changes: list
    messages: list

    @property
    def errors(self) -> list:
        return [m for m in self.messages if m.severity == "error"]


def prepare(file: Path, draft: nb.BoardDraft, ref: str = "", rules=(), netlist: Netlist | None = None) -> Import:
    """Reads, proposes, groups and previews in one step (the command line and the Studio start with this)."""
    netlist = netlist or read(file)
    suggestion = suggest(netlist, draft, ref, rules)
    return regroup(suggestion, draft)


def regroup(suggestion: Suggestion, draft: nb.BoardDraft) -> Import:
    """After a manual correction of the proposals: group and preview again."""
    name_buses(suggestion.proposals)
    found, messages = entries(suggestion, draft) if suggestion.mcu else ([], [])
    return Import(suggestion, found, preview(draft, found), list(suggestion.messages) + messages)


def report_lines(imp: Import) -> list[str]:
    """The preview as text (command line; the Studio shows the same as a table)."""
    s = imp.suggestion
    lines = [f"netlist {s.netlist.file.name} ({TOOL} {s.netlist.format}"
             + (f", {s.netlist.tool}" if s.netlist.tool else "") + f"): MCU {s.mcu or '-'}, "
             f"{len(s.proposals)} pin(s) on named nets, {len(s.skipped)} left out"]
    for m in imp.messages:
        lines.append(f"  {m}")
    lines.append("proposals (net -> pin -> signal):")
    for p in s.proposals:
        target = (f"{p.kind} {p.name}" + (f".{p.role}" if p.role and p.role != "pin" else "")
                  + (f" [{p.instance}{' ' + p.channel if p.channel else ''}]" if p.instance else "")
                  + (f" active {p.active}" if p.active else "")) if p.kind else "-"
        lines.append(f"  {p.net:<24} {p.pin:<10} {target:<40} {p.reason}")
    lines.append("preview:")
    for change in imp.changes:
        lines.append(f"  {change.describe()}")
        lines += [f"      {m.severity}: {m.text}" for m in change.messages if m.severity == "warning"]
    if s.hints:
        lines.append("left to the wizard (the netlist does not decide it):")
        lines += [f"  {h}" for h in s.hints]
    counts = summary(imp.changes)
    lines.append("summary: " + ", ".join(f"{n} {a}" for a, n in counts.items()))
    return lines
