"""DTC range of the basic software and its numbering plan (TOOL-131, configurable since TOOL-178).

neXtBSW raises some DTCs itself (RTE deadline, calibration segment, CAN bus-off, supply voltage, safe state, NvM).
Their numbers lie in one block of the 3-byte DTC space, the neXtBSW range. ISO 14229-1 Annex D.1 leaves the
numbers outside the legislated and group ranges to the vehicle manufacturer and names no separate supplier
range, so the range is a project value: `bsw_dtc_range` of diag.yaml (model.BswDtcRange), default
0x160000..0x16FFFF (decision of the user 2026-09-29, shown as P16xx-yy in the SAE J2012 display). A customer who
reserves another block for the basic software moves the base; every BSW DTC follows.

Numbering plan: the number of a BSW DTC is `base + offset`. The high byte of the offset is the source (a block of
256 numbers), the low byte the sub-type or the instance:

  +0x00xx  group identifier of the whole range (dtc_groups entry with `bsw: true`), never a DTC
  +0x01xx  RTE runtime supervision        +0x0100 rte.deadline (SYS-05)
  +0x02xx  calibration segment            +0x0200 calibration.segment (SYS-89), +0x0201 calibration.plausibility
  +0x03nn  CAN bus-off                    nn = index of the used CAN bus (can.busoff.<BUS>, COM-46)
  +0x04xx  supply voltage                 +0x0400 bsw.power.undervoltage, +0x0401 bsw.power.overvoltage (IO-59)
  +0x05nn  safe state / safety reset      nn = cause (safety.<cause>); +0x05FF safety.reset_loop (SYS-113),
                                          +0x05FE safety.hw_shutoff (hardware shut-off path, SYS-136),
                                          +0x05FD safety.mcu_watchdog, +0x05FC safety.fault_exception,
                                          +0x05FB safety.reset_other (safety reset before this start, SYS-113),
                                          +0x05FA safety.safe_off_take_back (a foreign active level of a safe_off
                                          output taken back by the hold of SYS-151, SYS-196)
  +0x06nn  NvM block lost, defaults loaded      nn = NvM block index (nvm.default_loaded.<BLOCK>, NVM-16/NVM-19)
  +0x07nn  NvM redundant copy not repairable    nn = NvM block index (nvm.copy_repair_failed.<BLOCK>, NVM-19)
  +0x08nn  NvM redundant copy repaired          nn = NvM block index (nvm.copy_repaired.<BLOCK>, NVM-19)
  +0x09nn  NvM QM block, one place defective    nn = NvM block index (nvm.place_defective.<BLOCK>, NVM-30)
  +0x0Axx..+0xFFxx reserved for further neXtBSW sources

A DTC of diag.yaml whose event is a BSW source of this plan may leave `number` out: the schema derives it
(model.DiagDescription). A source with instances (nn above) takes its low byte from `bsw_instance` of the DTC -
the index is a project fact (order of the used CAN buses, NvM block table) that the new-project wizard and the
follow-up tasks write, never a guess from the event name. The event names of sources that no module reports yet
(bus-off per index, supply, safety, NvM) are the names the follow-up tasks implement; this table is the one place
to change them.

Qt free and without a board, a derivative or a family: plain numbers.
"""

from dataclasses import dataclass
from fnmatch import fnmatchcase

BSW_DTC_BASE = 0x160000          # default base of the neXtBSW range (decision 2026-09-29)
BSW_DTC_SIZE = 0x010000          # default size: 0x160000..0x16FFFF
BLOCK = 0x100                    # numbers per source; base and size are multiples of it
LOWEST_BASE = 0x000100           # 0x000000..0x0000FF is reserved for legislation (Annex D.1)
GROUP_IDENTIFIERS = 0xFFFF00     # 0xFFFF00..0xFFFFFF are group identifiers, no DTC numbers (Annex D.1)


@dataclass(frozen=True)
class Source:
    """One block of the plan: `events` are (event name pattern, low byte) - None takes `bsw_instance`."""

    key: str
    block: int
    what: str
    task: str
    events: tuple[tuple[str, int | None], ...] = ()

    @property
    def offset(self) -> int:
        return self.block * BLOCK


PLAN = (
    Source("group", 0x00, "group identifier of the whole range, never a DTC", "NVM-09"),
    Source("rte", 0x01, "RTE runtime supervision", "SYS-05", (("rte.deadline", 0x00),)),
    Source("calibration", 0x02, "calibration segment", "SYS-89/SYS-90",
           (("calibration.segment", 0x00), ("calibration.plausibility", 0x01))),
    Source("can_busoff", 0x03, "CAN bus-off, low byte = index of the used CAN bus", "COM-46",
           (("can.busoff.*", None),)),
    Source("supply", 0x04, "supply voltage", "IO-59",
           (("bsw.power.undervoltage", 0x00), ("bsw.power.overvoltage", 0x01))),
    Source("safety", 0x05,
           "safe state / safety reset, low byte = cause, 0xFF reset loop limited, 0xFE hardware shut-off, "
           "0xFD MCU watchdog reset, 0xFC fault exception reset, 0xFB safety reset of another cause, "
           "0xFA foreign safe_off level taken back",
           "SYS-113/SYS-136/SYS-196",
           (("safety.reset_loop", 0xFF),
            # SYS-136: the hardware shut-off path of a PWM group (safeSupervisor/HwShutoff.h, SYS-130) - no event of
            # SafeSupervisor::Event, so a fixed sub-type from the top of the block like the reset loop; the event
            # numbers (low bytes of safety.<cause>) count from 0x00 and stay far below (test_rtegen_safety_hw_shutoff)
            ("safety.hw_shutoff", 0xFE),
            # SYS-113: the causes of a safety reset that are no event of SafeSupervisor::Event, reported after the
            # next start (libs/bsw/dem dem/SafetyResetReport.h INSTANCE_*): fixed sub-types below the two above
            ("safety.mcu_watchdog", 0xFD),
            ("safety.fault_exception", 0xFC),
            ("safety.reset_other", 0xFB),
            # SYS-196 (safety review SYS-194, SYS194-4 b): the hold of the commanded level (SYS-151) took back a
            # foreign active level of a safe_off output - reported by the BSP task of the family port, recording only
            # (the take-back was the reaction); only a project with a safe_off output has the hold (nxconfig checks)
            ("safety.safe_off_take_back", 0xFA),
            ("safety.*", None))),
    Source("nvm", 0x06, "NvM block lost, defaults loaded (DEFAULT_LOADED), low byte = NvM block index",
           "NVM-16/NVM-19", (("nvm.default_loaded.*", None),)),
    Source("nvm_copy_repair_failed", 0x07,
           "NvM redundant copy could not be repaired (COPY_REPAIR_FAILED), low byte = NvM block index", "NVM-19",
           (("nvm.copy_repair_failed.*", None),)),
    Source("nvm_copy_repaired", 0x08,
           "NvM redundant copy repaired from the other one (COPY_REPAIRED), low byte = NvM block index", "NVM-19",
           (("nvm.copy_repaired.*", None),)),
    # NVM-30: a QM block (two places in turn) with one place failing its CRC or unreadable - the value of the other
    # place was loaded, possibly the previous one (libs/bsw/nvm BlockEvent::PLACE_DEFECTIVE)
    Source("nvm_place_defective", 0x09,
           "NvM QM block with one defective place, the other one's (possibly previous) value loaded "
           "(PLACE_DEFECTIVE), low byte = NvM block index", "NVM-30",
           (("nvm.place_defective.*", None),)),
)
SOURCES = {s.key: s for s in PLAN}
# NVM-08: causes of the safe state that a module of neXtBSW reports today, as the low byte of safety.<cause>
# (+0x05nn): the number of the event in SafeSupervisor::Event (ecu/safety/safeSupervisor/include/safeSupervisor/
# SafeSupervisor.h; tools/nxconfig/test_bswdtc_sources.py compares them). device_fault: the degraded reaction after a
# device fault, reported by RteSystem while it lasts (ecu/application/src/systems/dem/SafetyFaultReport.h).
# SYS-113: the events that reset (S1 + S2), reported after the next start by the BSP task of the family port
# (libs/bsw/dem dem/SafetyResetReport.h, same table; libs/bsw/dem/test/test_start_report.py compares them). The
# events that hold the safe state or do not reset (rom_check_failure, watchdog_startup_check_failure,
# check_register_error, rte_timing_violation, device_report, reset_loop_limit) are no source.
SAFETY_CAUSES = {"device_fault": 0x13,
                 "safety_manager_sequence_deviation": 0x00, "safe_watchdog_sequence_deviation": 0x02,
                 "safe_watchdog_configuration_error": 0x03, "safe_watchdog_service_deviation": 0x04,
                 "mpu_unlocked_on_safety_manager_entry": 0x05, "mpu_locked_on_safety_manager_exit": 0x06,
                 "integrity_event_dma_ecc_error": 0x07, "mpu_not_enabled": 0x09, "mpu_configuration_error": 0x0A,
                 "adc_reference_error": 0x0B, "internal_supplies_error": 0x0C, "lock_register_error": 0x0E,
                 "deadman_configuration_error": 0x10, "safety_cycle_period_violation": 0x11,
                 "stack_overflow": 0x15, "rtos_assert": 0x16, "etl_assert": 0x17}
CAN_BUSOFF_PREFIX = "can.busoff."
SAFETY_PREFIX = "safety."
#: SYS-196: the BSW event of a foreign safe_off level taken back (only a project with a safe_off output reports it)
SAFE_OFF_TAKE_BACK = "safety.safe_off_take_back"
#: numbers the plan occupies from the base on; a smaller range leaves later sources without a number
PLAN_SIZE = (max(s.block for s in PLAN) + 1) * BLOCK


class DerivationError(ValueError):
    """A DTC number that cannot be derived; the text says why."""


def source_of(event: str | None) -> tuple[Source, int | None] | None:
    """(source, low byte or None for an instance) of a BSW event name, None if the plan has none."""
    if not event:
        return None
    for source in PLAN:
        for pattern, low in source.events:
            if fnmatchcase(event, pattern):
                return source, low
    return None


def offset_of(event: str | None, instance: int | None = None) -> int:
    """Offset of the DTC of a BSW event inside the range; raises DerivationError."""
    found = source_of(event)
    if found is None:
        known = ", ".join(p for s in PLAN for p, _low in s.events)
        what = f"the BSW event {event} is no source of it" if event else "the event is no BSW event"
        raise DerivationError(f"no number: only the DTC of a source of the neXtBSW numbering plan derives one "
                              f"and {what} (known: {known}); give the number")
    source, low = found
    if low is None:
        if instance is None:
            raise DerivationError(f"{event} is the {source.what} (+0x{source.block:02X}nn): bsw_instance gives nn")
        fixed = {v for _p, v in source.events if v is not None}
        if instance in fixed:
            raise DerivationError(f"bsw_instance 0x{instance:02X} of {event} is the fixed sub-type "
                                  f"+0x{source.offset + instance:04X} of the same block")
        return source.offset + instance
    if instance is not None:
        raise DerivationError(f"{event} has the fixed number +0x{source.offset + low:04X} in the numbering plan: "
                              "leave bsw_instance out")
    return source.offset + low


def derive(event: str | None, base: int, size: int, instance: int | None = None) -> int:
    """Number of the DTC of a BSW event: base + offset of the plan; raises DerivationError."""
    offset = offset_of(event, instance)
    if offset >= size:
        raise DerivationError(f"{event} would get 0x{base + offset:06X} (base + 0x{offset:04X}), outside the "
                              f"neXtBSW range 0x{base:06X}..0x{base + size - 1:06X}: raise bsw_dtc_range.size to at "
                              f"least 0x{(offset // BLOCK + 1) * BLOCK:X}")
    return base + offset


def planned(event: str | None, base: int, size: int, instance: int | None = None) -> int | None:
    """The plan's number of a BSW event, None if it has none (no source, missing instance, outside the range)."""
    try:
        return derive(event, base, size, instance)
    except DerivationError:
        return None


def range_problem(base: int, size: int) -> str | None:
    """Why base/size are no valid range, None if they are."""
    if base % BLOCK or size % BLOCK:
        return f"base and size of the neXtBSW range are multiples of 0x{BLOCK:X} (one block per source)"
    if base < LOWEST_BASE:
        return f"base 0x{base:06X} lies in 0x000000..0x0000FF, reserved for legislation (ISO 14229-1, Annex D.1)"
    if size < BLOCK:
        return f"size 0x{size:X} is below one block (0x{BLOCK:X})"
    if base + size > GROUP_IDENTIFIERS:
        return (f"the range 0x{base:06X}..0x{base + size - 1:06X} reaches the group identifiers "
                f"0x{GROUP_IDENTIFIERS:06X}..0xFFFFFF (ISO 14229-1, Annex D.1), which are no DTC numbers")
    return None


def display_code(number: int) -> str:
    """SAE J2012 display of a 3-byte DTC: P/C/B/U from bits 23..22, then the digits and the failure type byte
    (0x160100 -> P160100). ISO 14229-1 format 0x01 fixes no display text; the plan uses this one for its DTCs."""
    letter = "PCBU"[(number >> 22) & 0x3]
    return f"{letter}{(number >> 20) & 0x3:X}{(number >> 16) & 0xF:X}{(number >> 8) & 0xFF:02X}{number & 0xFF:02X}"


# NVM-16: a defective NvM block (CRC/read error, defaults loaded) as a DTC. The core (libs/bsw/nvm) already raises
# the events (BlockEvent, NVM-18); the integration (ecu/application/src/systems/nvm/NvmEvents.h, NVM-19) already
# turns them into reports of nvm::reportToFaultMemory() for whatever DTC diag.yaml names "nvm.<event>.<Block>".
# What was still open (TOOL-178 follow-up): a ready-made `dtcs[]` entry for a project's NvM block, one call per
# block instead of copying the calibration-segment shape by hand and getting bsw_instance wrong.
NVM_DTC_SOURCES = (
    ("nvm", "DefaultLoaded", "lost its value (CRC or read error) and started from its default"),
    ("nvm_copy_repair_failed", "CopyRepairFailed", "had a defective copy the other one could not repair"),
    ("nvm_copy_repaired", "CopyRepaired", "had a defective copy, repaired from the other one"),
)
# NVM-30: the source only a QM block raises (two places in turn, no copies); offered with place_defective=True
NVM_QM_DTC_SOURCES = (
    ("nvm_place_defective", "PlaceDefective",
     "had one defective place, the value of the other one (possibly the previous value) was loaded"),
)


def block_index(block: str, names) -> int:
    """`bsw_instance` of `block`: its position among `names` (every NvM block name of the project, `block`
    included) in the order tools/rtegen lays the blocks out - component writers in name order; the fault memory's
    own blocks (DemEvents/DemEntries) always come after them and report no defect of their own (NVM-20: the same
    rule nvm_dtc_yaml() documents for `index`, used by the Studio dialog "Neuer NvM-Block" and the new-project
    wizard so neither has to track the number by hand)."""
    from .nvm import DEM_BLOCKS          # lazy: nvm -> config -> model -> bswdtc would else cycle at import time

    from .nvm import ECU_STATS_BLOCK     # NVM-05: laid out by tools/rtegen after the fault memory's blocks

    if block in DEM_BLOCKS:
        raise DerivationError(f"{block} is a block of the fault memory (NVM-03): it reports no defect of its own")
    if block == ECU_STATS_BLOCK:
        raise DerivationError(f"{block} is the block of the ECU statistics (NVM-05): it comes after the component "
                              f"blocks, its index is not derived here")
    ordered = sorted({block, *names} - set(DEM_BLOCKS) - {ECU_STATS_BLOCK})
    return ordered.index(block)


def nvm_dtc_entries(block: str, index: int, asil: bool, operation_cycle: str = "PowerCycle",
                    place_defective: bool = False) -> list[dict]:
    """`dtcs[]` entries (as dicts, in schema field order) reporting a defect of the NvM block `block`: always the
    "defaults loaded" source (+0x06nn); for an ASIL block (two-copy mechanism, NVM-18) also its own two sources
    (+0x07nn copy not repairable, +0x08nn copy repaired) - a QM block has one copy and is never repaired, so
    libs/bsw/nvm never raises them for it. NVM-30: `place_defective` adds, for a QM block only, the source +0x09nn
    (one of its two places defective, possibly the previous value loaded); an ASIL block never raises it.

    `index` is the position of `block` among the project's NvM blocks in the order tools/rtegen lays them out
    (component writers in name order, then the fault memory's own blocks DemEvents/DemEntries, see block_index()):
    it becomes `bsw_instance`, so the DTC number follows `bsw_dtc_range` (tools/nxconfig/model.py) instead of
    being given by hand. `operation_cycle` must name an operation cycle of the target diag.yaml (the standard
    description uses "PowerCycle"). Single-report confirmation like the calibration segment (SYS-89): the event
    matures on the first report of a start (nvm::reportToFaultMemory, libs/bsw/nvm/include/nvm/Nvm.h "NVM-16 makes
    them DTCs").

    The structured form of nvm_dtc_yaml() (which renders the same entries as YAML text for pasting into a fresh
    template); a writer that edits an existing diag.yaml (tools/studio/core/diagnostics.DiagModel.add_entry, TOOL-192
    dialog "Neuer NvM-Block", NVM-20) inserts these dicts instead, so yamldoc keeps the comments of the file."""
    if not (0 <= index <= 0xFF):
        raise DerivationError(f"NvM block index {index} does not fit bsw_instance of a DTC (0..0xFF)")
    sources = NVM_DTC_SOURCES if asil else NVM_DTC_SOURCES[:1] + (NVM_QM_DTC_SOURCES if place_defective else ())
    entries = []
    for key, suffix, text in sources:
        pattern = SOURCES[key].events[0][0]
        if not pattern.endswith(".*"):
            raise DerivationError(f"{key}: the plan names no per-block event pattern")  # pragma: no cover (defensive)
        event = f"{pattern[:-2]}.{block}"
        entries.append({
            "name": f"Nvm{block}{suffix}",
            "label": f"NvM block {block} {text}",
            "severity": "check_immediately",
            "operation_cycle": operation_cycle,
            "confirmation_threshold": 1,
            "priority": 1,
            "bsw_instance": index,
            "event": {
                "bsw": event,
                "description": "nvm::reportToFaultMemory() reports it once per start, FAILED or PASSED (like the "
                              "start-up check of the calibration segment).",
            },
            "debounce": {"counter": {"failed_threshold": 1, "passed_threshold": -1}},
        })
    return entries


def nvm_dtc_yaml(block: str, index: int, asil: bool, operation_cycle: str = "PowerCycle",
                 place_defective: bool = False) -> str:
    """`dtcs[]` entries (tools/diaggen/standard_diag.yaml style, insert before `dtc_groups`) reporting a defect of
    the NvM block `block` - the YAML-text rendering of nvm_dtc_entries() (see there for `index`/`operation_cycle`),
    for pasting into a fresh diag.yaml (the new-project wizard, tools/nxconfig/newproject.py standard_diag)."""
    lines = []
    for entry in nvm_dtc_entries(block, index, asil, operation_cycle, place_defective):
        counter = entry["debounce"]["counter"]
        lines.append(
            f"  - name: {entry['name']}\n"
            f"    label: {entry['label']}\n"
            f"    severity: {entry['severity']}\n"
            f"    operation_cycle: {entry['operation_cycle']}\n"
            f"    confirmation_threshold: {entry['confirmation_threshold']}\n"
            f"    priority: {entry['priority']}\n"
            f"    bsw_instance: {entry['bsw_instance']}\n"
            "    event:\n"
            f"      bsw: {entry['event']['bsw']}\n"
            f"      description: {entry['event']['description']}\n"
            "    debounce:\n"
            f"      counter: {{failed_threshold: {counter['failed_threshold']}, "
            f"passed_threshold: {counter['passed_threshold']}}}\n")
    return "\n".join(lines)
