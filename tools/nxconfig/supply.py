# SPDX-License-Identifier: Apache-2.0
"""Supply of the ECU: battery monitor of a UJA113x and the local wake-up inputs of the power supply (IO-33).

Battery monitor (project.yaml modules.<device> of a device served by sbcUja113x, parameters in its module.yaml):
the thresholds are voltages in mV, the SBC compares on its own grid (data sheet UJA113X_SER Rev. 2, 7.8.2 and
tables 15-17: threshold = code / 255 x 20 V, release window = code x 4 / 255 x 20 V). The driver rounds on the safe
side (sbc::Uja113x::batteryThresholdCode/batteryHysteresisCode); the same arithmetic stands here, so `nxconfig check`
shows the values the SBC really uses (info, no finding). Findings, all with YAML path:

  - the release windows overlap (undervoltage ends at or above where the overvoltage ends)      error
  - a threshold inside 0 < U < 2 V, below the input range of the monitor (table 90, Vi 2..20 V)   warning
  - a switch-off on an event that never comes (undervoltage 0, overvoltage at full scale)         warning
  - a switch-off on undervoltage without delay: a short dip (inrush current of a load, IO-13)
    switches CAN or V2 off at once                                                                warning
  - shutdown_delay_ms without any switch-off                                                      warning

Supply DTCs (IO-59, diag.yaml): a DTC whose event is `bsw.power.undervoltage` or `bsw.power.overvoltage` is sampled
by the RTE from the BSW service bsw.power (tools/rtegen). Without `failed_below`/`failed_above` the provider's state
decides - its thresholds are the project values of its module (modules.<device>.undervoltage_mv/overvoltage_mv of a
UJA113x); with them (mV) the measured supply voltage. Findings, with the YAML path of diag.yaml:

  - the limit points the wrong way (undervoltage with failed_above, overvoltage with failed_below), or `result`
    is not a limit                                                                                  error
  - another bsw.power.* name as event (only undervoltage/overvoltage are events)                    error
  - no used device provides the role power_supply: the DTC (or a condition bsw.power.ok) never
    gets a result resp. is never fulfilled                                                          warning
  - no limit and the SBC monitors no such threshold (undervoltage_mv 0, overvoltage at full scale)  warning
  - a limit outside the input range of the SBC's monitor (2..20 V)                                  warning

Local wake-up inputs (generic, module.yaml `pins` with `local_wakeup`): a pin the board wires as wake_input
(devices[].pins) is the local wake-up input LOCAL<n> of the power supply. modules.ecuState.wakeup_local may only name
inputs the board wires that way:

  - the named input is a pin the board wires otherwise (e.g. none), or the part has no such input   error
  - the named input is a pin the board does not describe (the supply does not offer it)            warning
"""

from .config import DIAG_LEVEL, Config, Reporter
from .model import WAKE_INPUT_USE

SBC_MODULES = ("sbcUja113x",)
FULL_SCALE_MV = 20000                # 20 V full scale of the monitor ADC
THRESHOLD_STEPS = 255
HYSTERESIS_CODES = 15
INPUT_MIN_MV = 2000                  # table 90: Vi of pins BAT/BATSENSE in Normal mode 2..20 V
ACCURACY_MV = 300                    # table 90: VADC(acc) +/- 300 mV
POWER_SUPPLY_ROLE = "power_supply"
ECU_STATE = "ecuState"
WAKEUP_LOCAL = "wakeup_local"
# IO-59: BSW events of the supply (sampled by the RTE) and the direction of their limit; the condition source
SUPPLY_EVENTS = {"bsw.power.undervoltage": "failed_below", "bsw.power.overvoltage": "failed_above"}
SUPPLY_CONDITION = "bsw.power.ok"
POWER_PREFIX = "bsw.power."
# parameters of the battery monitor; a driver of an older neXtBSW (before IO-33) has none of them
BATTERY_PARAMETERS = ("battery_source", "undervoltage_mv", "overvoltage_mv", "undervoltage_hysteresis_mv",
                      "overvoltage_hysteresis_mv", "can_shutdown", "v2_shutdown", "shutdown_delay_ms")


def threshold_code(mv: int, round_up: bool) -> int:
    """sbc::Uja113x::batteryThresholdCode."""
    if mv >= FULL_SCALE_MV:
        return 0xFF
    product = mv * THRESHOLD_STEPS
    code = (product + FULL_SCALE_MV - 1) // FULL_SCALE_MV if round_up else product // FULL_SCALE_MV
    return min(code, 0xFF)


def threshold_mv(code: int) -> int:
    """sbc::Uja113x::batteryThresholdMv (rounded to the nearest mV)."""
    return (code * FULL_SCALE_MV + THRESHOLD_STEPS // 2) // THRESHOLD_STEPS


def hysteresis_code(mv: int) -> int:
    """sbc::Uja113x::batteryHysteresisCode (rounded down, never wider than asked for)."""
    return min((mv * THRESHOLD_STEPS) // (4 * FULL_SCALE_MV), HYSTERESIS_CODES)


def hysteresis_mv(code: int) -> int:
    """sbc::Uja113x::batteryHysteresisMv."""
    return ((code & 0x0F) * 4 * FULL_SCALE_MV + THRESHOLD_STEPS // 2) // THRESHOLD_STEPS


def effective(values: dict) -> dict[str, int]:
    """Thresholds and release windows of a UJA113x as the SBC uses them, in mV."""
    return {
        "undervoltage_mv": threshold_mv(threshold_code(values["undervoltage_mv"], True)),
        "overvoltage_mv": threshold_mv(threshold_code(values["overvoltage_mv"], False)),
        "undervoltage_hysteresis_mv": hysteresis_mv(hysteresis_code(values["undervoltage_hysteresis_mv"])),
        "overvoltage_hysteresis_mv": hysteresis_mv(hysteresis_code(values["overvoltage_hysteresis_mv"])),
    }


def _shuts(value: str, event: str) -> bool:
    return value in (event, "both")


def _sbc_devices(cfg: Config):
    integration = cfg.integration
    return [d for d in (integration.devices if integration is not None else [])
            if d.module in SBC_MODULES and all(k in d.parameters for k in BATTERY_PARAMETERS)]


def _path(cfg: Config, device: str, parameter: str) -> tuple:
    """modules.<device>.<parameter> if the project sets it, else modules.<device> (the default of the module applies)."""
    given = cfg.project.modules.get(device, {})
    return ("modules", device, parameter) if parameter in given else ("modules", device)


def _battery(cfg: Config, rep: Reporter):
    for dev in _sbc_devices(cfg):
        v = dev.parameters
        eff = effective(v)
        name = f"device {dev.name}"
        uv_on = v["undervoltage_mv"] > 0
        ov_on = threshold_code(v["overvoltage_mv"], False) < 0xFF
        for key, on in (("undervoltage_mv", uv_on), ("overvoltage_mv", ov_on)):
            if on and v[key] < INPUT_MIN_MV:
                rep.warning("project", _path(cfg, dev.name, key),
                            f"{key} {v[key]} mV of {name} is below the input range of the battery monitor "
                            f"({INPUT_MIN_MV}..{FULL_SCALE_MV} mV, UJA113x data sheet table 90): the event is not "
                            "reliable there")
        if uv_on and ov_on:
            uv_end = eff["undervoltage_mv"] + eff["undervoltage_hysteresis_mv"]
            ov_end = eff["overvoltage_mv"] - eff["overvoltage_hysteresis_mv"]
            if uv_end >= ov_end:
                rep.error("project", _path(cfg, dev.name, "undervoltage_hysteresis_mv"),
                          f"release windows of {name} overlap: the undervoltage event ends at {uv_end} mV "
                          f"({eff['undervoltage_mv']} + {eff['undervoltage_hysteresis_mv']} mV), the overvoltage "
                          f"event at {ov_end} mV ({eff['overvoltage_mv']} - {eff['overvoltage_hysteresis_mv']} mV); "
                          "lower the windows or move the thresholds apart")
        shutdowns = {k: v[k] for k in ("can_shutdown", "v2_shutdown")}
        for key, value in shutdowns.items():
            what = "CAN" if key == "can_shutdown" else "V2"
            if _shuts(value, "undervoltage") and not uv_on:
                rep.warning("project", _path(cfg, dev.name, key),
                            f"{key} {value} of {name}: undervoltage_mv is 0, the undervoltage event never comes and "
                            f"{what} is never switched off by it")
            if _shuts(value, "overvoltage") and not ov_on:
                rep.warning("project", _path(cfg, dev.name, key),
                            f"{key} {value} of {name}: overvoltage_mv is at full scale ({FULL_SCALE_MV} mV), the "
                            f"overvoltage event never comes and {what} is never switched off by it")
            if _shuts(value, "undervoltage") and uv_on and v["shutdown_delay_ms"] == 0:
                rep.warning("project", _path(cfg, dev.name, key),
                            f"{key} {value} of {name} with shutdown_delay_ms 0: the SBC switches {what} off within "
                            "one conversion of its monitor (6 us), so a short dip below "
                            f"{eff['undervoltage_mv']} mV - e.g. the inrush current of a load (IO-13) - drops it "
                            "at once; set shutdown_delay_ms to ride through such dips")
        if v["shutdown_delay_ms"] > 0 and all(value == "none" for value in shutdowns.values()):
            rep.warning("project", _path(cfg, dev.name, "shutdown_delay_ms"),
                        f"shutdown_delay_ms {v['shutdown_delay_ms']} of {name} has no effect: can_shutdown and "
                        "v2_shutdown are none")


def local_wakeup_pins(cfg: Config) -> tuple[object | None, dict[int, tuple[str, str | None]]]:
    """Power supply device of the project and its local wake-up inputs: number -> (pin, board use or None if the
    board does not describe the pin). Only pins the part has (module.yaml pins[].parts)."""
    from fnmatch import fnmatchcase

    integration = cfg.integration
    if integration is None:
        return None, {}
    for dev in integration.devices:
        manifest = cfg.modules.manifests.get(dev.module)
        if manifest is None or POWER_SUPPLY_ROLE not in manifest.provides:
            continue
        inputs = {}
        for pin, spec in manifest.pins.items():
            if spec.local_wakeup is None:
                continue
            if spec.parts and not any(fnmatchcase(dev.part, p) for p in spec.parts):
                continue
            inputs[spec.local_wakeup] = (pin, dev.pins.get(pin))
        return dev, inputs
    return None, {}


def _wakeup_local(cfg: Config, rep: Reporter):
    integration = cfg.integration
    use = integration.modules.get(ECU_STATE) if integration is not None else None
    if use is None:
        return
    value = use.parameters.get(WAKEUP_LOCAL)
    if not isinstance(value, str) or value in ("all", "none"):
        return
    dev, inputs = local_wakeup_pins(cfg)
    if dev is None:
        return                              # no power supply: ecuState reports it at run time (SYS-02)
    given = cfg.project.modules.get(ECU_STATE, {})
    path = ("modules", ECU_STATE, WAKEUP_LOCAL) if WAKEUP_LOCAL in given else ("modules", ECU_STATE)
    board = cfg.board.board.name
    for name in value.split(","):
        number = int(name[len("LOCAL"):])
        found = inputs.get(number)
        if found is None:
            offered = ", ".join(f"LOCAL{n} = {p}" for n, (p, _) in sorted(inputs.items())) or "none"
            rep.error("project", path, f"{WAKEUP_LOCAL}: {name} is no local wake-up input of {dev.name} "
                      f"({dev.part}; inputs of the part: {offered})")
            continue
        pin, wiring = found
        if wiring is None:
            rep.warning("project", path, f"{WAKEUP_LOCAL}: {name} is pin {pin} of {dev.name}, which board {board} "
                        f"does not describe (devices[].pins.{pin}): the supply does not offer it as wake-up source")
        elif wiring != WAKE_INPUT_USE:
            rep.error("project", path, f"{WAKEUP_LOCAL}: {name} is pin {pin} of {dev.name}, which board {board} "
                      f"wires as {wiring}, not as {WAKE_INPUT_USE} (devices[].pins.{pin})")


def supply_providers(cfg: Config) -> list:
    """IO-59: the used devices of the integration whose module provides the role power_supply."""
    integration = cfg.integration
    manifests = cfg.modules.manifests if cfg.modules is not None else {}
    return [d for d in (integration.devices if integration is not None else [])
            if d.module in manifests and POWER_SUPPLY_ROLE in manifests[d.module].provides]


def _supply_dtcs(cfg: Config, rep: Reporter):
    """IO-59: the DTCs and conditions of diag.yaml on bsw.power.* against the supply of the project."""
    desc = getattr(cfg, "diag", None)
    if desc is None:
        return
    providers = supply_providers(cfg)
    sbcs = {d.name: d for d in _sbc_devices(cfg)}
    none = ("no used device provides the role power_supply (module.yaml provides), so nothing publishes "
            "bsw.power")
    for index, cond in enumerate(desc.enable_conditions):
        if cond.bsw == SUPPLY_CONDITION and not providers:
            rep.warning(DIAG_LEVEL, ("enable_conditions", index, "bsw"),
                        f"condition {cond.name} on {SUPPLY_CONDITION}: {none} - it is never fulfilled")
    for index, dtc in enumerate(desc.dtcs):
        event = dtc.event.bsw or ""
        if not event.startswith(POWER_PREFIX):
            continue
        path = ("dtcs", index, "event")
        direction = SUPPLY_EVENTS.get(event)
        if direction is None:
            rep.error(DIAG_LEVEL, path + ("bsw",),
                      f"DTC {dtc.name}: {event} is no event of the supply; the events are "
                      f"{', '.join(SUPPLY_EVENTS)} (a value like bsw.power.supply_mv is read through an identifier "
                      "or a port, not as an event)")
            continue
        wrong = "failed_above" if direction == "failed_below" else "failed_below"
        if getattr(dtc.event, wrong) is not None:
            rep.error(DIAG_LEVEL, path + (wrong,),
                      f"DTC {dtc.name}: {event} fails {'below' if direction == 'failed_below' else 'above'} its "
                      f"threshold - give {direction} (mV), not {wrong}")
            continue
        if dtc.event.result not in (None, "limit"):
            rep.error(DIAG_LEVEL, path + ("result",),
                      f"DTC {dtc.name}: {event} is sampled from the supply voltage or the provider's state; result "
                      f"{dtc.event.result} does not apply (leave it out, or limit with {direction})")
            continue
        limit = getattr(dtc.event, direction)
        if not providers:
            rep.warning(DIAG_LEVEL, path + ("bsw",),
                        f"DTC {dtc.name} on {event}: {none} - the DTC never gets a result")
            continue
        under = direction == "failed_below"
        for dev in providers:
            sbc = sbcs.get(dev.name)
            if sbc is None:
                continue
            v = sbc.parameters
            if limit is None:
                off = (v["undervoltage_mv"] <= 0) if under else (threshold_code(v["overvoltage_mv"], False) >= 0xFF)
                if off:
                    key = "undervoltage_mv" if under else "overvoltage_mv"
                    rep.warning(DIAG_LEVEL, path + ("bsw",),
                                f"DTC {dtc.name} on {event} without {direction}: the state of {dev.name} decides, "
                                f"and its battery monitor has no {'undervoltage' if under else 'overvoltage'} "
                                f"threshold (modules.{dev.name}.{key} {v[key]}) - set it, or give {direction} (mV)")
            elif not (INPUT_MIN_MV <= limit <= FULL_SCALE_MV):
                rep.warning(DIAG_LEVEL, path + (direction,),
                            f"DTC {dtc.name}: {direction} {limit} mV is outside the input range of the battery monitor "
                            f"of {dev.name} ({INPUT_MIN_MV}..{FULL_SCALE_MV} mV, UJA113x data sheet table 90)")


def check(cfg: Config, rep: Reporter):
    """Battery monitor values of the UJA113x instances, the local wake-up inputs of the power supply and the supply
    DTCs of the diagnostic description (IO-59)."""
    _battery(cfg, rep)
    _wakeup_local(cfg, rep)
    _supply_dtcs(cfg, rep)


def info(cfg: Config) -> list[str]:
    """Readable values for `nxconfig check` (no finding): the SBC grid and the local wake-up inputs."""
    lines = []
    for dev in _sbc_devices(cfg):
        v = dev.parameters
        eff = effective(v)
        uv = f"{eff['undervoltage_mv']} mV" if v["undervoltage_mv"] > 0 else "never"
        ov = f"{eff['overvoltage_mv']} mV" if threshold_code(v["overvoltage_mv"], False) < 0xFF else "never"
        delay = (f"after {v['shutdown_delay_ms']} ms by the driver" if v["shutdown_delay_ms"] > 0
                 else "at once by the SBC")
        lines.append(f"{dev.name} ({dev.module}) battery monitor on {v['battery_source'].upper()}: undervoltage "
                     f"{uv} (release +{eff['undervoltage_hysteresis_mv']} mV), overvoltage {ov} (release "
                     f"-{eff['overvoltage_hysteresis_mv']} mV), accuracy +/-{ACCURACY_MV} mV; CAN off: "
                     f"{v['can_shutdown']}, V2 off: {v['v2_shutdown']}"
                     + (f" ({delay})" if (v["can_shutdown"], v["v2_shutdown"]) != ("none", "none") else ""))
    dev, inputs = local_wakeup_pins(cfg)
    if dev is not None and inputs:
        wake = [f"LOCAL{n} = {pin}" for n, (pin, wiring) in sorted(inputs.items()) if wiring == WAKE_INPUT_USE]
        use = cfg.integration.modules.get(ECU_STATE)
        chosen = use.parameters.get(WAKEUP_LOCAL) if use is not None else None
        lines.append(f"{dev.name} local wake-up inputs wired by the board: {', '.join(wake) or 'none'}"
                     + (f"; selected (modules.{ECU_STATE}.{WAKEUP_LOCAL}): {chosen}" if chosen is not None else ""))
    return lines
