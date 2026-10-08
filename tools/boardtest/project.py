"""What a board test needs to know about the ECU, taken from the project - never a constant of the scripts.

  console baud rate    project uart (the only UART, otherwise --baud)
  CAN bus and timing   rte.yaml can.bus -> project can.<bus> (bitrate, sample point, SJW), tools/canflash
  cyclic messages      DBC of the ECU description (rte.yaml can.dbc, node can.node, GenMsgCycleTime)
  qualifier and E2E    rte.yaml can.qualifiers (profile, data id, CRC/counter signals), DBC GenMsgTimeoutTime
  diagnostic addresses project diagnostics (COM-11), tools/canflash
  RTE tasks            rte.yaml tasks (names only, to recognise a complete "rte runnables" answer)

The port of the console and the CAN adapter channel belong to the test PC and come from the command line.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
ROOT = TOOLS.parent
for sub in ("canflash", "e2e", "rtegen", "dbcgen"):
    sys.path.insert(0, str(TOOLS / sub))
sys.path.insert(0, str(TOOLS))

import canflash  # noqa: E402
import e2e_ref  # noqa: E402


class SetupError(Exception):
    pass


@dataclass
class E2eSetup:
    """Everything needed to protect and check frames of one message with tools/e2e/e2e_ref.py."""

    profile: str
    data_id: int
    data_id_mode: str
    crc_offset: int
    counter_offset: int
    data_length: int
    nibble_offset: int = 0
    max_delta: int = 1

    def protect(self, counter: int, payload: bytes) -> bytes:
        data = bytearray(payload)
        mode = getattr(e2e_ref, f"DATAID_{self.data_id_mode}")
        if self.profile == "P01":
            cfg = e2e_ref.P01Config(counter_offset=self.counter_offset, crc_offset=self.crc_offset,
                                    data_id=self.data_id, data_id_nibble_offset=self.nibble_offset,
                                    data_id_mode=mode, data_length=self.data_length,
                                    max_delta_counter_init=self.max_delta)
            rc, _ = e2e_ref.p01_protect(cfg, counter, data)
        elif self.profile == "P11":
            cfg = e2e_ref.P11Config(counter_offset=self.counter_offset, crc_offset=self.crc_offset,
                                    data_id=self.data_id, data_id_nibble_offset=self.nibble_offset,
                                    data_id_mode=mode, data_length=self.data_length, max_delta_counter=self.max_delta)
            rc, _ = e2e_ref.p11_protect(cfg, counter, data)
        else:
            raise SetupError(f"E2E profile {self.profile}: the board test sends P01 and P11 only")
        if rc != e2e_ref.E_OK:
            raise SetupError(f"E2E {self.profile} protect failed ({rc}) for {len(payload)} byte")
        return bytes(data)

    @property
    def counter_modulo(self) -> int:
        return 15


@dataclass
class MessageSetup:
    name: str
    frame_id: int
    extended: bool
    length: int
    cycle_ms: int
    timeout_ms: int
    initial: bytes
    signals: dict = field(default_factory=dict)       # name -> dbcgen.Signal
    codec: object = None                              # cantools message
    debounce_ms: int = 0
    e2e: E2eSetup | None = None

    def raw(self, data: bytes) -> dict:
        """Raw signal values of a frame (cantools, the DBC loader of tools/dbcgen)."""
        return self.codec.decode(bytes(data), decode_choices=False, scaling=False)


@dataclass
class ProjectSetup:
    project: Path
    name: str
    baudrate: int | None
    bus: str | None
    timing: "canflash.BusTiming | None"
    node: str | None
    tx: dict                  # messages the ECU sends (DBC senders contain the node), name -> MessageSetup
    rx: dict                  # messages the ECU receives
    tasks: list
    diag: "canflash.Ecu | None"

    def message(self, name: str) -> MessageSetup:
        if name in self.rx:
            return self.rx[name]
        if name in self.tx:
            return self.tx[name]
        raise SetupError(f"{self.project}: no message {name} of node {self.node} in the DBC "
                         f"(sent: {', '.join(self.tx) or '-'}; received: {', '.join(self.rx) or '-'})")

    def qualified_rx(self) -> list[str]:
        return [name for name, msg in self.rx.items() if msg.e2e is not None]


def _initial_payload(msg, codec) -> bytes:
    """Frame with every signal at its GenSigStartValue (DBC), E2E fields still zero."""
    values = {s.name: s.initial_raw for s in msg.signals}
    return bytes(codec.encode(values, scaling=False, strict=False))


def load(project: Path, with_diag: bool = True) -> ProjectSetup:
    import rtegen

    project = Path(project)
    if project.is_dir():
        project = project / "project.yaml"
    try:
        values = yaml.safe_load(project.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise SetupError(f"{project}: {e}") from None
    info = values.get("project") or {}
    uarts = values.get("uart") or {}
    baudrate = next(iter(uarts.values())).get("baudrate") if len(uarts) == 1 else None

    ecu, ecu_file = {}, None
    if info.get("rte"):
        ecu_file = project.parent / info["rte"]
        ecu = yaml.safe_load(ecu_file.read_text(encoding="utf-8")) or {}
    tasks = [str(t["name"]) for t in ecu.get("tasks") or [] if isinstance(t, dict) and "name" in t]

    can_cfg = ecu.get("can")
    bus = node = timing = None
    tx, rx = {}, {}
    if can_cfg:
        used = (values.get("use") or {}).get("can")
        buses = list(values.get("can") or {}) if used in (None, "all") else [str(b) for b in used]
        try:
            loaded = rtegen.load_can(ecu, ROOT, buses, ecu_file)
        except rtegen.RteError as e:
            raise SetupError(f"{project}: {e}") from None
        bus, node = loaded.bus, loaded.node
        try:
            timing = canflash.timing_from_project(project, bus)
        except ValueError as e:
            raise SetupError(str(e)) from None
        import cantools

        raw_db = cantools.database.load_file(str(rtegen.path_of(ecu, ecu_file, ROOT, can_cfg["dbc"])),
                                            database_format="dbc", strict=True)
        for msg in loaded.dbc.messages:
            if msg.problem:
                continue
            codec = raw_db.get_message_by_name(msg.name)
            setup = MessageSetup(msg.name, msg.frame_id, msg.extended, msg.length, msg.cycle_ms, msg.timeout_ms,
                                 _initial_payload(msg, codec), {s.name: s for s in msg.signals}, codec)
            qualifier = loaded.qualifiers.get(msg.name)
            if qualifier is not None:
                setup.debounce_ms = qualifier.debounce_ms
                if qualifier.timeout_ms is not None:
                    setup.timeout_ms = qualifier.timeout_ms
                if qualifier.e2e is not None:
                    setup.e2e = _e2e(qualifier.e2e, msg, rtegen)
            if node in msg.senders:
                tx[msg.name] = setup
            elif node in msg.receivers:
                rx[msg.name] = setup

    diag = None
    if with_diag and values.get("diagnostics"):
        try:
            found = canflash.ecus_from_projects([project])
        except ValueError as e:
            raise SetupError(str(e)) from None
        diag = found[0] if found else None
    return ProjectSetup(project, str(info.get("name", project.parent.name)), baudrate, bus, timing, node, tx, rx,
                        tasks, diag)


def _e2e(e2e, msg, rtegen) -> E2eSetup:
    """Field offsets of the E2E signals, derived by the generator's own rule (rtegen.e2e_field)."""
    fields = {"crc": rtegen.e2e_field(msg.signal(e2e.crc_signal), "crc", 8, e2e.where),
              "counter": rtegen.e2e_field(msg.signal(e2e.counter_signal), "counter", 4, e2e.where)}
    nibble = rtegen.e2e_field(msg.signal(e2e.nibble_signal), "data id nibble", 4, e2e.where) \
        if e2e.nibble_signal else 0
    return E2eSetup(e2e.profile, e2e.data_id, e2e.data_id_mode, fields["crc"], fields["counter"], msg.length * 8,
                    nibble, e2e.max_delta)
