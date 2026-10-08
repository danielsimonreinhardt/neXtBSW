# SPDX-License-Identifier: Apache-2.0
"""Module levels compared with the physical value of an analog connection (IO-21).

A module parameter with `sense: <connection>` (module.yaml, model.ParameterSpec.sense) is a level in a physical unit
of the function, e.g. the overcurrent of a high-side switch in mA load current (doc/architecture/
treiber-granularitaet.md section 5). The driver compares it with the converted sample of the ADC channel the board
wires to that connection; the conversion is the channel's characteristic (board adc[].sensor, sensor.py, generated as
nxAdcPhys_<channel>), so the part facts (k_ILIS, R_IS, ...) stand once in the board and never in the project.

Checks per used module instance and sensed parameter, all with YAML path:

  - the wired channel has a characteristic in the parameter's unit      error at board adc[i] (facts missing:
                                                                        no sensor block, or another unit)
  - the level lies in the range the channel reports over every code    error at modules.<device>.<parameter>
  - the characteristic is confirmed (source, not verify)                warning at board adc[i].sensor

info() gives the reader the ADC codes each level corresponds to (nxconfig check, no finding).
"""

from dataclasses import dataclass
from fractions import Fraction

from .config import Config, Reporter
from .sensor import SensorPlan, evaluate


@dataclass(frozen=True)
class SensedLevel:
    """One level of a module instance against the characteristic of its analog connection."""

    device: str
    module: str
    parameter: str
    value: int
    unit: str
    channel: str
    first_raw: int | None           # lowest code whose value reaches the level (>=), None if no code does
    last_raw: int | None            # highest such code
    first_value: int | None         # value of first_raw
    first_mv: int | None            # pin voltage of first_raw (mV, rounded down like adc.mv)
    per_code: Fraction              # average value step per converter code


def check(cfg: Config, rep: Reporter) -> list[SensedLevel]:
    """Sensed levels of the used module instances; channels or levels with errors are left out."""
    integration = cfg.integration
    if integration is None:
        return []
    board_adc = {ch.name: (i, ch) for i, ch in enumerate(cfg.board.adc)}
    plans: dict[str, SensorPlan] = cfg.adc_sensors
    vref = cfg.board.board.adc_reference_mv
    levels: list[SensedLevel] = []
    for dev in integration.devices:
        manifest = cfg.modules.manifests.get(dev.module)
        if manifest is None:
            continue
        by_connection: dict[str, list[tuple[str, object]]] = {}
        for name, spec in manifest.parameters.items():
            if spec.sense is not None:
                by_connection.setdefault(spec.sense, []).append((name, spec))
        for connection, specs in by_connection.items():
            target = dev.connect.get(connection)
            if target not in board_adc:
                continue                            # wiring errors are reported by the module integration
            index, ch = board_adc[target]
            names = ", ".join(name for name, _ in specs)
            units = sorted({spec.unit for _, spec in specs})
            who = (f"device {dev.name} ({dev.module}) compares {names} ({', '.join(units)}) with connection "
                   f"{connection}")
            if not ch.fitted:
                rep.error("board", ("adc", index), f"ADC {target}: {who}, but the channel is marked fitted: false")
                continue
            plan = plans.get(target)
            if plan is None:
                continue                            # the characteristic itself has errors (sensor.py reported them)
            for unit in units:
                if plan.unit == unit:
                    continue
                if plan.kind == "scale":
                    rep.error("board", ("adc", index), f"ADC {target}: {who}, but the channel has no sensor block "
                              f"(its value is the pin voltage in mV): the board facts that convert it into {unit} "
                              f"are missing (adc[].sensor, e.g. kind highside_is with k_ilis, r_is_ohm and source)")
                else:
                    rep.error("board", ("adc", index, "sensor"), f"ADC {target}: {who}, but the channel's sensor "
                              f"({plan.kind}) delivers {plan.unit}, not {unit}")
            if any(plan.unit != unit for unit in units):
                continue
            if not plan.confirmed:
                rep.warning("board", ("adc", index, "sensor" if ch.sensor is not None else "scale"),
                            f"ADC {target}: {who}; the characteristic of the channel is not confirmed (source missing "
                            f"or verify: true), so the levels of {dev.name} are not either")
            values = [evaluate(plan, raw)[0] for raw in range(plan.max_raw + 1)]
            per_code = Fraction(plan.value_max - plan.value_min, plan.max_raw)
            for name, spec in specs:
                value = dev.parameters[name]
                if not plan.value_min <= value <= plan.value_max:
                    rep.error("project", ("modules", dev.name, name),
                              f"{name} {value} {spec.unit} is outside the range the channel {target} of connection "
                              f"{connection} can report ({plan.value_min}..{plan.value_max} {plan.unit} over raw "
                              f"0..{plan.max_raw}, board adc[{index}]): the level is never reached")
                    continue
                reaching = [raw for raw, v in enumerate(values) if v >= value]
                first = reaching[0] if reaching else None
                levels.append(SensedLevel(
                    dev.name, dev.module, name, value, spec.unit, target, first,
                    reaching[-1] if reaching else None, values[first] if first is not None else None,
                    first * vref // plan.max_raw if first is not None and vref else None, per_code))
    return levels


def info(cfg: Config) -> list[str]:
    """Readable ADC codes of each sensed level (nxconfig check, not a message)."""
    lines = []
    for lv in getattr(cfg, "sensed_levels", []):
        head = f"{lv.device} ({lv.module}) {lv.parameter} {lv.value} {lv.unit}: channel {lv.channel}"
        step = f"{float(lv.per_code):.1f} {lv.unit} per code"
        if lv.first_raw is None:
            lines.append(f"{head} reaches the level at no code ({step})")
            continue
        pin = f", {lv.first_mv} mV at the pin" if lv.first_mv is not None else ""
        lines.append(f"{head} value >= level at raw {lv.first_raw}..{lv.last_raw} "
                     f"(raw {lv.first_raw} = {lv.first_value} {lv.unit}{pin}; {step})")
    return lines
