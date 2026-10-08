"""ARCH-58 (customer-project.md N5): the feature switches of the build, derived instead of hand-written.

Until now every buildable board needed `ecu/platforms/<board>/Options.cmake`, a hand-written CMake file that set
`OPENBSW_PLATFORM` and the ten `PLATFORM_SUPPORT_*` switches; the root `CMakeLists.txt` refused to configure a board
without it. For a customer board that is one more file to write, and its content repeats what project and board
already say (architecture section 9, row 2: the target location of these switches is the project).

**What this module does.** It derives the switches from the configuration levels and from the port, and says for
every one of them where the value comes from. The root `CMakeLists.txt` takes them as `NX_FEATURE_<NAME>` out of
the generated `project.cmake` and sets `PLATFORM_SUPPORT_<NAME>` from them. A board port may still override a
switch afterwards - that is then a justified exception of that board and visible as such, not the normal way.

**Where each switch comes from (the evidence, ARCH-58).**

| Switch | Level | Derived from |
|---|---|---|
| `OPENBSW_PLATFORM` | derivative | the family of the derivative (`NX_FAMILY`); the root already set it and only checked that the board port repeated it |
| `IO` | project | the project uses at least one I/O entry of its board: `use.dio`, `use.adc`, `use.input_capture`, a `pwm` group, or an internal ADC channel (`adc_internal`) |
| `CAN` | project | the project uses at least one CAN bus of its board (`use.can`) |
| `TRANSPORT` | project | the project has a `diagnostics` section **and** uses a CAN bus (PLAT-96): the transport layer (ISO 15765 on CAN) is what carries the diagnostic stack. The root adds DoIP on top when Ethernet is switched on |
| `UDS` | project | the same condition as `TRANSPORT`: a `diagnostics` section (UDS addresses of application and bootloader) and a CAN bus of the application. Without one the section serves the bootloader only |
| `ETHERNET` | build option | not a board value: `NX_ETHERNET` of the build turns it on and checks the derivative for an Ethernet MAC (PLAT-56). Derived value here: off |
| `WATCHDOG` | family port | `ecu/platforms/<family>/features.yaml`: the port of the family says it drives the MCU watchdog |
| `MPU` | family port | `ecu/platforms/<family>/features.yaml`: the port of the family says it programs the memory protection unit |

**What is deliberately not derived (reasoned gaps, ARCH-58).**

- `STORAGE`, `ROM_CHECK`, `MIDDLEWARE` and the further switches of the OpenBSW reference application
  (`OBD_UDS_ADDRESSING`, `PROGRAMMING_SESSION`, `UDS_DEMO_SERVICES`) have **no reader in neXtBSW**: they are only
  read by `external/openbsw/executables/referenceApp`, which this tree does not build (it has its own executable
  under `ecu/`). Every board port had them off. They are not emitted here; a board port that needs one sets it, and
  the day a neXtBSW module reads one, the switch gets a level and a derivation like the others.
- `WATCHDOG` and `MPU` are stated by the **port**, not by the project. Today they describe whether a driver exists
  at all (the stm32 port has no MPU regions yet, PLAT-17/SYS-06) - that is a property of the port, not a decision of
  the ECU. A project switch on top ("this ECU runs without the MPU") is a value nobody has asked for yet; it would
  be a new project entry and is left out until someone does. The derivative cannot answer it either: `mcudb` knows
  the MPU region count of every derivative (the STM32F446 has eight) but says nothing about a driver, and it has no
  watchdog instance at all.
- The family port manifest `platforms/<family>/port/port.yaml` (PLAT-95) would be the natural home of the two port
  switches. It is one file further down the same road (the platform rule P5 moves the whole family port to
  `ecu/platforms/<family>`), and the file written here is already at that target location.

Run of the tests: `.venv/Scripts/python -m unittest tools/nxconfig/test_features.py`
"""

from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .config import REPO, line_of
from .messages import Message

# The family statement, at the location the platform rule P5 gives the family port: ecu/platforms/<family>.
FAMILY_FEATURES = "features.yaml"
FAMILY_SCHEMA_VERSION = 1

#: Feature switches this tree knows, in the order the root CMakeLists.txt sets them. `level` names who decides.
LEVELS = ("project", "option", "port")
FEATURES = (
    ("IO", "project"),
    ("CAN", "project"),
    ("ETHERNET", "option"),
    ("TRANSPORT", "project"),
    ("UDS", "project"),
    ("WATCHDOG", "port"),
    ("MPU", "port"),
)
#: Feature names a family may claim in its features.yaml (lower case of the `port` level features).
PORT_FEATURES = tuple(name.lower() for name, level in FEATURES if level == "port")


@dataclass(frozen=True)
class Switch:
    """One derived feature switch: its value and the sentence that says where the value comes from."""

    name: str                                       # PLATFORM_SUPPORT_<name>
    level: str                                      # project | option | port
    value: bool
    why: str                                        # human readable reason, quoted by CMake and the Studio

    @property
    def cmake(self) -> str:
        return "ON" if self.value else "OFF"


@dataclass(frozen=True)
class FamilyFeatures:
    """What the port of a family provides (ecu/platforms/<family>/features.yaml)."""

    file: Path
    where: str                                      # the file as the messages name it
    family: str
    provides: dict[str, str] = field(default_factory=dict)      # feature -> reason

    def has(self, feature: str) -> bool:
        return feature in self.provides


def family_features_file(family: str, repo: Path = REPO) -> Path:
    """The features file of a family, whether it exists or not."""
    return Path(repo) / "ecu" / "platforms" / family / FAMILY_FEATURES


def family_port_template(family: str, repo: Path = REPO) -> Path:
    """The family port template `platforms/<family>/port` - a board of a family without one is not built."""
    return Path(repo) / "platforms" / family / "port" / "CMakeLists.txt"


def _relative(file: Path, repo: Path) -> str:
    try:
        return Path(file).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return Path(file).as_posix()


def read_family_features(file: Path, family: str, repo: Path = REPO
                         ) -> tuple[FamilyFeatures | None, list[Message]]:
    """Reads and checks `ecu/platforms/<family>/features.yaml`. None with messages if it cannot be used.

    Hand-written instead of a pydantic model, for the same reason as the port manifest of PLAT-95: the file belongs
    to the port of a family, not to the three configuration levels of an ECU, and its vocabulary is exactly what
    the build can act on. A feature nxconfig could not switch would be a promise it does not keep, so it is
    rejected here with its YAML path.
    """
    messages: list[Message] = []
    tree = None

    def error(path, text):
        messages.append(Message("error", text, file, tuple(path), line_of(tree, tuple(path))))

    try:
        tree = YAML(typ="rt").load(file.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as e:
        messages.append(Message("error", f"cannot read the family features file: {e}", file))
        return None, messages
    if not isinstance(tree, dict):
        error((), "expected a mapping at the top level of the family features file")
        return None, messages
    version = tree.get("schema_version")
    if version != FAMILY_SCHEMA_VERSION:
        error(("schema_version",), f"family features schema_version {version!r}, "
                                   f"expected {FAMILY_SCHEMA_VERSION}")
        return None, messages
    if tree.get("family") != family:
        error(("family",), f"the file says family: {tree.get('family')!r}, but it lies in the port folder of "
                           f"family {family}: a features file belongs to the family whose folder it is in")
        return None, messages
    entries = tree.get("provides", [])
    if not isinstance(entries, list):
        error(("provides",), "`provides:` must be a list of entries with `name:` and `reason:`")
        return None, messages
    provides: dict[str, str] = {}
    ok = True
    for i, entry in enumerate(entries):
        where = ("provides", i)
        if not isinstance(entry, dict):
            error(where, f"entry {i} of `provides:` must be a mapping (name, reason, code)")
            ok = False
            continue
        name = entry.get("name")
        if not isinstance(name, str) or name.strip().lower() not in PORT_FEATURES:
            error(where + ("name",), f"`name:` must be one of the features a port provides "
                                     f"({', '.join(PORT_FEATURES)}), not {name!r}. The other switches are derived "
                                     f"from project and board (tools/nxconfig/features.py)")
            ok = False
            continue
        name = name.strip().lower()
        if name in provides:
            error(where + ("name",), f"feature {name} is listed twice")
            ok = False
            continue
        for key in entry:
            if key not in ("name", "reason", "code"):
                error(where + (key,), f"unknown key {key!r} of a `provides:` entry (known: name, reason, code)")
                ok = False
        reason = str(entry.get("reason") or "").strip()
        code = str(entry.get("code") or "").strip()
        provides[name] = f"{reason} ({code})" if reason and code else (reason or code)
    if not ok:
        return None, messages
    return FamilyFeatures(file, _relative(file, repo), family, provides), messages


def family_features(family: str, repo: Path = REPO) -> tuple[FamilyFeatures | None, list[Message]]:
    """The features file of the family, or None with a finding when the family is built but has none."""
    file = family_features_file(family, repo)
    if file.is_file():
        return read_family_features(file, family, repo)
    if not family_port_template(family, repo).is_file():
        return None, []                             # family without a port template: nothing is compiled from it
    return None, [Message("warning", f"family {family} has a port template (platforms/{family}/port) but no "
                                     f"{_relative(file, repo)}: the switches the port states (watchdog, MPU) stay "
                                     "off. Write the file, or set the switch in the board port as an exception "
                                     "(ARCH-58)", file)]


# ------------------------------------------------------------------------------------- derivation of the switches


def _uses_io(cfg) -> str:
    """Why IO is on: the first I/O entry of the board the project uses, as a sentence. Empty: none."""
    for kind, path in (("dio.outputs", "use.dio"), ("dio.inputs", "use.dio"), ("adc", "use.adc"),
                       ("input_capture", "use.input_capture")):
        entries = cfg.selected(kind)
        if entries:
            return f"the project uses {len(entries)} {kind} entr{'y' if len(entries) == 1 else 'ies'} " \
                   f"of board {cfg.board.board.name} ({path})"
    pwm = cfg.selected_pwm()
    if pwm:
        return f"the project uses {len(pwm)} PWM group(s) of board {cfg.board.board.name} (pwm)"
    if cfg.project.adc_internal:
        return f"the project reads {len(cfg.project.adc_internal)} internal ADC channel(s) (adc_internal)"
    return ""


def derive(cfg, rep=None, repo: Path = REPO) -> dict[str, Switch]:
    """The feature switches of `cfg`, by upper-case name. `rep`: findings of the family features file.

    Pure derivation - it reads project, board, derivative and the family features file, and nothing of the build.
    """
    family = cfg.derivative.derivative.family
    provided, messages = family_features(family, repo)
    if rep is not None:
        rep.messages.extend(messages)
    if provided is not None and provided.file not in cfg.code_inputs:
        cfg.code_inputs.append(provided.file)       # a changed statement has to reach the configure step

    def port_switch(name: str) -> Switch:
        key = name.lower()
        if provided is not None and provided.has(key):
            why = provided.provides[key] or f"stated by the port of family {family}"
            return Switch(name, "port", True, f"{why}; stated in {provided.where}")
        where = _relative(family_features_file(family, repo), repo)
        return Switch(name, "port", False, f"the port of family {family} does not state {key} in {where}")

    io_why = _uses_io(cfg)
    can = cfg.selected("can")
    diagnostics = cfg.project.diagnostics is not None
    # PLAT-96: the diagnostic stack of the application runs on ISO 15765, i.e. on a CAN bus of the application.
    # An application without one (ARCH-42) keeps its diagnostics section - it holds the addresses of the
    # bootloader, which talks on a board bus of its own (bootloader.can) - but has no transport and no UDS server:
    # with both switched on the OpenBSW transport router asked for a CAN bus id that does not exist.
    app_diagnostics = diagnostics and bool(can)
    no_can_why = ("the project has a diagnostics section, but the application uses no CAN bus (use.can) to carry "
                  "it (ISO 15765); the section only serves the bootloader")
    switches = [
        Switch("IO", "project", bool(io_why),
               io_why or f"the project uses no digital I/O, no ADC channel, no capture channel and no PWM group "
                         f"of board {cfg.board.board.name}"),
        Switch("CAN", "project", bool(can),
               f"the project uses the CAN bus(es) {', '.join(b.name for b in can)} of board "
               f"{cfg.board.board.name} (use.can)" if can else "the project uses no CAN bus (use.can)"),
        Switch("ETHERNET", "option", False,
               "no board value: the build option NX_ETHERNET switches Ethernet/lwIP on and checks the derivative "
               "for an Ethernet MAC (PLAT-56)"),
        Switch("TRANSPORT", "project", app_diagnostics,
               "the project has a diagnostics section, whose stack the transport layer (ISO 15765) carries"
               if app_diagnostics else no_can_why if diagnostics
               else "the project has no diagnostics section, so nothing uses the transport layer"),
        Switch("UDS", "project", app_diagnostics,
               "the project has a diagnostics section (UDS addresses of application and bootloader)"
               if app_diagnostics else no_can_why if diagnostics
               else "the project has no diagnostics section (no UDS server in the application)"),
        port_switch("WATCHDOG"),
        port_switch("MPU"),
    ]
    return {s.name: s for s in switches}


def settings_of(switches: dict[str, Switch]) -> dict[str, str]:
    """`NX_FEATURE_<NAME>` (ON/OFF) and `NX_FEATURE_<NAME>_WHY` for the generated project.cmake."""
    out: dict[str, str] = {}
    for name, switch in switches.items():
        out[f"NX_FEATURE_{name}"] = switch.cmake
        out[f"NX_FEATURE_{name}_WHY"] = f"{switch.level}: {switch.why}"
    return out


def settings(cfg, rep=None, repo: Path = REPO) -> dict[str, str]:
    """Derivation and formatting in one step (config.build_settings caches the switches on the Config)."""
    return settings_of(derive(cfg, rep, repo))
