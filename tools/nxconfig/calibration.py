"""Rules of the calibration segment of a project (SYS-89, stage K3 of doc/architecture/kalibrierung.md).

memory.py places the segment (memory.calibration: region, size); this module checks what the segment means for the
rest of the project, with the YAML path of the value to change:

- `memory.calibration` itself must exist as soon as one calibration parameter of the ECU is safety relevant (SYS-96,
  follow-up of SYS-90): without a segment a safety relevant value stays an ordinary RTE-03 value in RAM, with no
  place `on_invalid` could even apply to. An error names the component, the parameter, its safety class and the
  swc.yaml that declares it (the same style as the `on_invalid` finding below).
- `profile: production` has no write path to the values: the project must not have an `xcp` section (decision E5 a
  of the user 2026-09-28; the production profile is the one the safety evidence refers to).
- `on_invalid` must be `refuse` as soon as one calibration parameter of the ECU is safety relevant: a `safety`
  class other than qm in the `parameters:` of a component's swc.yaml (RTE-26, the one definition of calibration
  objects, decision E1 a). Parameters a Simulink model has but swc.yaml does not define stay qm (rtegen warns).
- `memory.calibration` needs a DTC whose `event.bsw` is `calibration.segment` (SYS-101, follow-up of a board finding
  2026-09-28/9c): RteSystem.reportCalibrationToDem() looks that event up with findReportedEvent() at every start-up
  and, without a matching DTC, finds nothing and stays silent - an invalid segment then leaves no trace in the
  fault memory, whatever `on_invalid` does. Missing while `on_invalid` is `refuse` or `defaults` is an error (the
  application stops or runs on defaults - the two reactions the safety case and the field both need to see in the
  DTC list); missing while `on_invalid` is `warn` is only a warning (the application keeps running on the invalid
  segment regardless, so the DTC is diagnostic sugar, not a safety gap).
- The reverse also holds: a DTC with `event.bsw: calibration.segment` needs `memory.calibration` (SYS-101). Without
  a segment `NX_CALIBRATION_SEGMENT` is never defined (tools/bswgen), reportCalibrationToDem() is not even compiled
  and nothing ever calls dem::IFaultMemory::report() for that event - the DTC would keep "no test result" forever
  (ISO 14229-1, D.2.2). An error names the DTC and its diag.yaml.
- The DTC of `calibration.segment` must mature from a single report (SYS-102, follow-up of the same board finding,
  9c: "mit der Entprellung der Vorlage (failed_threshold 10) reift der DTC nie"): reportCalibrationToDem() calls
  dem::IFaultMemory::report() exactly once at start-up, FAILED or PASSED, never again - a `dtcs[].debounce` copied
  from a periodically sampled event (a port resampled every fault_memory.cycle_ms, e.g. the demo's 0x904001
  template) needs several such samples before it matures and would stay "no test result" forever for a DTC that
  is never resampled. `debounce.counter` must reach `failed_threshold` on the first failed sample (jump_up, or 0,
  plus `increment`) and `passed_threshold` on the first passed sample (jump_down, or 0, minus `decrement`) - the
  simplest choice is `failed_threshold: 1`, `passed_threshold: -1` with `increment`/`decrement` left at 1;
  `debounce.time` needs `failed_ms` and `passed_ms` at or below `fault_memory.cycle_ms`, the only way a duration
  elapses within the single cycle after the report. Missing this while `on_invalid` is `refuse` or `defaults` is
  an error (the same two reactions the SYS-101 rule above already treats as safety relevant); `warn` is only a
  warning, the same split as SYS-101.
- The same silent gap applies to the DTC of `calibration.plausibility` (SYS-90): PlausibilityMonitor.startUp()
  reports it exactly once at start-up in every case (Calibration.cpp with a segment, RteSystem.cpp in place
  without one), checked only when a safety relevant parameter exists (`NX_RTE_HAS_PARAMETER_CHECKS`, the same
  `relevant` list as above). Only a write path can report it a second time (Plausibility.cpp acceptWrite(), every
  refused XCP write) - that needs both `memory.calibration` and an `xcp` section, so the rule applies whenever
  either is missing and is out of scope only with both present. Without `memory.calibration` the reaction is
  fixed (refuse, no on_invalid to read) and the finding is always an error; with a segment it follows
  `on_invalid` like the segment's own rule above.

The swc.yaml files are only read for their `parameters[].safety`; the full check of the component description stays
with tools/swc/swc_yaml.py and tools/rtegen. diag.yaml is only read for `dtcs[].event.bsw` and `dtcs[].debounce`;
the full check of the diagnostic description stays with diagdesc.py. Family neutral, no Qt.
"""

from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .config import DIAG_LEVEL, Config, Reporter

SWC_FILE = "swc.yaml"
SAFE_CLASSES = ("qm",)
# RteSystem.reportCalibrationToDem (ecu/application/src/systems/RteSystem.cpp, SYS-89): the BSW event name a DTC
# names in diag.yaml (dtcs[].event.bsw) to receive the result of the start-up check of the calibration segment.
CALIBRATION_SEGMENT_EVENT = "calibration.segment"
# PlausibilityMonitor::startUp (ecu/calibration/src/calibration/PlausibilityMonitor.cpp, SYS-90): the BSW event
# name of the in-place check that runs instead of the segment check when the project has no memory.calibration.
CALIBRATION_PLAUSIBILITY_EVENT = "calibration.plausibility"


@dataclass(frozen=True)
class SafetyParameter:
    """A calibration parameter with a safety class other than qm."""

    component: str
    parameter: str
    safety: str
    file: Path


def safety_parameters(cfg: Config) -> list[SafetyParameter]:
    """Safety relevant calibration parameters of the components of the ECU description (none without one).

    Component folders are relative to the rte.yaml (format 2, RTE-22). A folder without swc.yaml (a Simulink
    component described only by its ARXML) or a file that cannot be read contributes nothing here; tools/rtegen
    reports such files with their own messages.
    """
    if cfg.rte is None or cfg.rte_file is None:
        return []
    base = cfg.rte_file.parent
    found: list[SafetyParameter] = []
    for component in cfg.rte.components:
        file = (base / component.path / SWC_FILE).resolve()
        if not file.is_file():
            continue
        try:
            tree = YAML(typ="safe").load(file.read_text(encoding="utf-8"))
        except (OSError, YAMLError):
            continue
        parameters = tree.get("parameters") if isinstance(tree, dict) else None
        for entry in parameters if isinstance(parameters, list) else []:
            if not isinstance(entry, dict):
                continue
            safety = entry.get("safety")
            if isinstance(safety, str) and safety not in SAFE_CLASSES:
                found.append(SafetyParameter(component.name, str(entry.get("name")), safety, file))
    return found


def _dtc_of_bsw_event(cfg: Config, event: str) -> tuple[int, object] | None:
    """Index and DTC of diag.yaml whose event.bsw is `event`; None without a diagnostic description or a match."""
    if cfg.diag is None:
        return None
    for index, dtc in enumerate(cfg.diag.dtcs):
        if dtc.event.bsw == event:
            return index, dtc
    return None


def calibration_segment_dtc(cfg: Config) -> tuple[int, object] | None:
    """Index and DTC of diag.yaml whose event is RteSystem's report of the calibration segment (SYS-89, SYS-101).

    None if the project has no diagnostic description (cfg.diag), or none of its DTCs names the event.
    """
    return _dtc_of_bsw_event(cfg, CALIBRATION_SEGMENT_EVENT)


def _debounce_gap(dtc, cycle_ms: int | None) -> str | None:
    """Why `dtc.debounce` (diagdesc.py schema) does not mature a DTC its BSW event reports at most once (SYS-102);
    None if it does mature on that single report, in both directions.

    Counter debouncing (ISO 14229-1 D.6): a fresh counter starts at 0, so the first failed sample reaches
    `max(0, jump_up) + increment` and the first passed sample `min(0, jump_down) - decrement` (jump_up/jump_down
    left out count as 0, the schema default). Time debouncing needs the elapsed time to reach `failed_ms`/
    `passed_ms` within the single fault memory cycle that follows the report (`cycle_ms`); without a fault_memory
    section (cycle_ms unknown - diagdesc.py already reports the missing section) the check is skipped, not guessed.
    """
    counter = dtc.debounce.counter
    if counter is not None:
        jump_up = counter.jump_up if counter.jump_up is not None else 0
        jump_down = counter.jump_down if counter.jump_down is not None else 0
        first_failed = max(0, jump_up) + counter.increment
        first_passed = min(0, jump_down) - counter.decrement
        if first_failed >= counter.failed_threshold and first_passed <= counter.passed_threshold:
            return None
        return (f"counter debouncing reaches {first_failed} on the first failed sample and {first_passed} on the "
                f"first passed sample (jump_up/jump_down plus increment/decrement), but failed_threshold is "
                f"{counter.failed_threshold} and passed_threshold is {counter.passed_threshold}: the DTC would "
                "keep \"no test result\" forever; use failed_threshold: 1 and passed_threshold: -1 (or thresholds "
                "the first sample already reaches)")
    if cycle_ms is None:
        return None
    time = dtc.debounce.time
    if time.failed_ms <= cycle_ms and time.passed_ms <= cycle_ms:
        return None
    return (f"time debouncing needs failed_ms and passed_ms at or below fault_memory.cycle_ms ({cycle_ms}) to "
            f"mature within the one fault memory cycle that follows the report; failed_ms {time.failed_ms} and "
            f"passed_ms {time.passed_ms} do not - the DTC would keep \"no test result\" forever")


def _cycle_ms(cfg: Config) -> int | None:
    """fault_memory.cycle_ms of the project's diagnostic description; None without one (cfg.diag or its
    fault_memory section missing - diagdesc.py already reports the missing section on its own)."""
    if cfg.diag is None or cfg.diag.fault_memory is None:
        return None
    return cfg.diag.fault_memory.cycle_ms


def check(cfg: Config, rep: Reporter) -> list[SafetyParameter]:
    """Rules of memory.calibration against xcp, the safety classes and the DTCs of SYS-101/SYS-102; returns the
    safety relevant parameters."""
    memory = cfg.project.memory
    use = memory.calibration if memory is not None else None
    relevant = safety_parameters(cfg)
    found = calibration_segment_dtc(cfg)
    if use is None:
        if relevant:
            first = relevant[0]
            more = f" and {len(relevant) - 1} more" if len(relevant) > 1 else ""
            rep.error("project", ("memory", "calibration"), f"memory.calibration is missing: {first.component}."
                      f"{first.parameter} is {first.safety}{more} ({first.file.as_posix()}); a safety relevant "
                      "calibration parameter (RTE-26) needs its own calibration segment (SYS-89) so an invalid "
                      "value can be refused (on_invalid: refuse) instead of staying an ordinary RTE-03 value in RAM")
        if found is not None:
            index, dtc = found
            rep.error(DIAG_LEVEL, ("dtcs", index, "event", "bsw"),
                      f"dtcs[{index}] {dtc.name} has event.bsw {CALIBRATION_SEGMENT_EVENT} but the project has no "
                      "memory.calibration (SYS-101): without a segment NX_CALIBRATION_SEGMENT is not defined, "
                      "RteSystem never calls reportCalibrationToDem() and this DTC would keep \"no test result\" "
                      "forever; add memory.calibration or give the DTC a different event")
    else:
        if use.profile == "production" and cfg.project.xcp is not None:
            rep.error("project", ("xcp",), "memory.calibration.profile is production: the production profile has "
                      "no write path to the calibration values, remove the xcp section (a development build of "
                      "the same ECU keeps it)")
        if relevant and use.on_invalid != "refuse":
            first = relevant[0]
            more = f" and {len(relevant) - 1} more" if len(relevant) > 1 else ""
            rep.error("project", ("memory", "calibration", "on_invalid"),
                      f"on_invalid {use.on_invalid} is not allowed: {first.component}.{first.parameter} is "
                      f"{first.safety}{more} ({first.file.as_posix()}); a segment that does not fit the "
                      "application must stop the application functions (refuse)")
        # SYS-101: a project without any diagnostic description (no project.diag, ARCH-51) has no fault memory to
        # add the DTC to either - out of scope here, diagdesc.py/diag.py do not require one. Only a project that
        # already describes diagnostics must describe this event too.
        if cfg.diag is not None and found is None:
            report = rep.error if use.on_invalid in ("refuse", "defaults") else rep.warning
            report("project", ("memory", "calibration"),
                   f"no DTC has event.bsw {CALIBRATION_SEGMENT_EVENT} (diag.yaml dtcs[]) while on_invalid is "
                   f"{use.on_invalid} (SYS-101): RteSystem.reportCalibrationToDem() finds nothing with "
                   "findReportedEvent() and stays silent, so an invalid segment leaves no trace in the fault "
                   "memory; add a DTC with that event")
        elif found is not None:
            # SYS-102 (board finding 2026-09-28/9c): reportCalibrationToDem() reports FAILED or PASSED exactly
            # once at start-up, so the DTC's debounce must already mature on that single report - a counter or
            # time debounce copied from a periodically resampled event (several samples needed) would never
            # mature.
            index, dtc = found
            gap = _debounce_gap(dtc, _cycle_ms(cfg))
            if gap is not None:
                report = rep.error if use.on_invalid in ("refuse", "defaults") else rep.warning
                report(DIAG_LEVEL, ("dtcs", index, "debounce"),
                       f"dtcs[{index}] {dtc.name} debounces {CALIBRATION_SEGMENT_EVENT} so it never matures while "
                       f"on_invalid is {use.on_invalid} (SYS-102): reportCalibrationToDem() reports FAILED or "
                       f"PASSED exactly once at start-up - {gap}")
    # SYS-102, SYS-90: PlausibilityMonitor.startUp() reports calibration.plausibility exactly once at start-up in
    # every case (Calibration.cpp with a segment, RteSystem.cpp in place without one) - checked only while a
    # safety relevant parameter actually runs it (NX_RTE_HAS_PARAMETER_CHECKS, the same `relevant` list as above).
    # Only a refused XCP write (Plausibility.cpp acceptWrite()) can report it a second time, and that needs both
    # memory.calibration and an xcp section - with both present the single-report guarantee no longer holds.
    if relevant and not (use is not None and cfg.project.xcp is not None):
        plausibility = _dtc_of_bsw_event(cfg, CALIBRATION_PLAUSIBILITY_EVENT)
        if plausibility is not None:
            index, dtc = plausibility
            gap = _debounce_gap(dtc, _cycle_ms(cfg))
            if gap is not None:
                if use is None:
                    report, reason = rep.error, "memory.calibration is missing (SYS-90), the reaction is fixed "\
                        "(refuse)"
                else:
                    report = rep.error if use.on_invalid in ("refuse", "defaults") else rep.warning
                    reason = f"on_invalid is {use.on_invalid}"
                report(DIAG_LEVEL, ("dtcs", index, "debounce"),
                       f"dtcs[{index}] {dtc.name} debounces {CALIBRATION_PLAUSIBILITY_EVENT} so it never matures "
                       f"while {reason} (SYS-102): PlausibilityMonitor.startUp() reports FAILED or PASSED exactly "
                       f"once at start-up - {gap}")
    return relevant
