# SPDX-License-Identifier: Apache-2.0
"""Sample groups (IO-62): board ADC channels converted after every hardware trigger of a PWM group.

Concept: doc/architecture/fast-control-loop.md section 3 (contract `bspSampleGroup`, family mapping 3.2). The chain
is PWM timer trigger output -> trigger router -> sample sequencer (one per ADC) -> ADC conversions -> one interrupt
at the end of the whole group. The three levels:

  derivative  trigger_router        router inputs (codes), outputs (register, selector field), which inputs the
                                    trigger outputs of each PWM timer are, which outputs are fault inputs (SYS-86),
                                    whether a register lock freezes the whole register
              sample_sequencers[]   sequencer per ADC: router output that starts it, conversions per trigger,
                                    back-to-back, delay counter width and clock
  board       adc[]                 which channel is on which ADC (the existing board fact `instance`)
  project     sample_groups.<name>  channels in result order, PWM group as trigger source, delay from the trigger
                                    point (pwm.<group>.trigger, IO-61); interrupts.ranks {source: sample_group}

Derived here (the family generator only turns it into register values): the sequencer and slot of every channel,
the router output of every sequencer, the delay in sequencer clocks, the sample instant of every channel relative
to the trigger point, the end of the group, the sequencer whose last conversion completes the group (its ADC raises
the interrupt) and the conversion time of every used ADC (the converter settings of modules.bspAdcAccess).

Software conversions and the hardware chain: the ADC instances of a sample group are EXCLUSIVE to the chain. On the
s32k1xx family the trigger mode is one bit per converter (ADC SC2[ADTRG]); with the hardware trigger selected a
software write of SC1[0] starts nothing, and a software conversion started in between would take the result
registers the sequence writes to. A documented interleaving would need the software converter of the family port
(platforms/s32k1xx/port/.../AnalogInput.cpp) to switch the trigger mode per conversion, which breaks the chain for
that period - so a software user of such an instance (use.adc, adc_internal, adc_sampling) is an error here, not a
warning. The slow supervision of the same quantities (pwm_period_average, IO-31) stays on ADCs without a group.

Checks, each with YAML path:
  - the derivative describes a trigger router and sample sequencers                                         error
  - the PWM group is used, has a trigger output (pwm.<group>.trigger), and the router knows its timer       error
  - every channel is a fitted board ADC entry, listed once, its pin free of other users                    error
  - every ADC of the group has a sequencer, with enough slots                                               error
  - one sequencer (and so one ADC) per group; router outputs and locked registers used once (hardware
    thresholds of SYS-86 included)                                                                          error
  - no software conversion on an ADC of a group                                                             error
  - the delay fits the sequencer counter; the whole group ends inside one PWM period                        error
  - an interrupt rank of the group (NVIC derivatives)                                                       error
  - channels that should be sampled together share an ADC: the time offset between them                    warning
"""

from dataclasses import dataclass, field
from fractions import Fraction

from .config import Config, Reporter

GROUP = "sample_groups"


@dataclass(frozen=True)
class SampleMember:
    """One channel of a group: where it is converted and when it is sampled."""

    name: str                       # board ADC entry
    adc: str                        # ADC instance
    channel: str                    # external channel of the instance (e.g. SE4)
    pin: str
    sequencer: str                  # sample sequencer of the ADC
    slot: int                       # conversion index in its sequencer (s32k1xx: pre-trigger m -> SC1[m], R[m])
    sample_ns: Fraction             # start of its sample phase after the trigger point


@dataclass(frozen=True)
class SequencerUse:
    """One sequencer (one ADC) of a group."""

    name: str
    adc: str
    target: str                     # router output that starts it
    register_index: int             # router register of that output
    selector: int                   # selector field of that output
    slots: int                      # conversions used
    conversion_ns: int              # one conversion of its ADC (converter settings of bspAdcAccess)
    end_ns: Fraction                # end of its last conversion after the trigger point


@dataclass(frozen=True)
class SampleGroupUse:
    """Checked sample group: what the family generator needs, and what `nxconfig check` prints."""

    name: str
    pwm: str                        # PWM group
    timer: str                      # its timer instance
    trigger_point: str              # pwm.<group>.trigger.point
    router: str
    timer_trigger: dict             # {"counter": code, "match": code} router input codes of the timer's triggers
    delay_ns: int                   # configured
    delay_ticks: int                # sequencer clocks
    sequencer_hz: Fraction
    realised_delay_ns: Fraction     # trigger point -> first sample, with the start clocks of the sequencer
    members: tuple[SampleMember, ...]   # in result order (project order)
    sequencers: tuple[SequencerUse, ...]  # in the order of first use
    completing: str                 # sequencer whose last conversion ends the group (its ADC raises the interrupt)
    end_ns: Fraction                # end of the whole group after the trigger point
    period_ns: Fraction             # realised PWM period
    back_to_back: bool
    confirmed: bool
    warnings: tuple[str, ...] = field(default=())


def _claims(cfg: Config):
    from .checks import _resources      # late: checks imports this module
    return _resources(cfg, Reporter())


def _pwm_period_ns(cfg: Config, grp, use) -> Fraction | None:
    from .checks import PWM_COUNTERS, PwmTimingError, pwm_timing   # late: checks imports this module
    limits = PWM_COUNTERS.get(cfg.derivative.derivative.family)
    if limits is None or cfg.clock is None or not limits.instance.match(grp.instance):
        return None
    try:
        timing = pwm_timing(limits, grp.name, grp.instance, cfg.clock.counter_hz(grp.instance), use.frequency_hz,
                            use.deadtime_ns, center=use.alignment == "center")
    except PwmTimingError:
        return None                                 # reported by the PWM check
    return Fraction(1_000_000_000) / timing.frequency_hz


def _conversion_ns(cfg: Config, adc: str) -> int | None:
    """One conversion of `adc` with the converter settings of the project (tools/nxconfig/adc.py)."""
    from . import adc as nxadc
    plan = cfg.adc_access
    if plan is None or cfg.clock is None:
        return None
    functional = cfg.clock.peripheral_hz.get(adc)
    if functional is None:
        return None
    adc_hz = int(Fraction(functional) / plan.clock_divider)
    bus_hz = int(cfg.clock.domains["bus"])
    return (nxadc._clocks_to_ns(plan.conversion_clocks, adc_hz)
            + nxadc._clocks_to_ns(plan.adder_bus_clocks * plan.averages, bus_hz))


def _software_users(cfg: Config) -> dict[str, list[tuple[str, tuple]]]:
    """ADC instance -> software users (what, YAML path of the project)."""
    users: dict[str, list[tuple[str, tuple]]] = {}
    for ch in cfg.selected("adc"):
        users.setdefault(ch.instance, []).append((f"adc {ch.name} (use.adc)", ("use", "adc")))
    for i, ch in enumerate(cfg.project.adc_internal):
        users.setdefault(ch.instance, []).append((f"adc_internal {ch.name}", ("adc_internal", i)))
    return users


def check(cfg: Config, rep: Reporter) -> list[SampleGroupUse]:
    """Sample groups of the project; groups with errors are left out (the messages say why)."""
    wanted = cfg.project.sample_groups
    if not wanted:
        return []
    derivative = cfg.derivative.derivative.name
    router = cfg.derivative.trigger_router
    sequencers = {s.adc: s for s in cfg.derivative.sample_sequencers}
    if router is None or not sequencers:
        rep.error("project", (GROUP,), f"sample groups need a trigger router and sample sequencers, but derivative "
                                       f"{derivative} describes {'none' if router is None else 'no sequencer'} "
                                       f"(mcudb trigger_router, sample_sequencers)")
        return []
    targets = {t.name: t for t in router.targets}
    board_adc = {ch.name: ch for ch in cfg.board.adc}
    used_pwm = {grp.name: (grp, use) for grp, use in cfg.selected_pwm()}
    software = _software_users(cfg)
    ranked = {r.name for r in cfg.project.interrupts.ranks if r.source == "sample_group"}
    nvic = cfg.derivative.derivative.nvic_priority_bits is not None

    # router outputs already taken: the fault inputs of the hardware thresholds (SYS-86), with their lock
    taken: dict[str, str] = {}                      # target name -> user
    locked: dict[int, str] = {}                     # register index -> user that locks it
    for t in getattr(cfg, "hw_thresholds", None) or []:
        outputs = router.fault_inputs.get(t.timer, [])
        if t.fault_input < len(outputs) and outputs[t.fault_input] in targets:
            out = targets[outputs[t.fault_input]]
            taken[out.name] = f"hardware threshold {t.name}"
            if router.register_lock:
                locked[out.register_index] = f"hardware threshold {t.name}"
    sequencer_user: dict[str, str] = {}
    claims = None
    result = []
    for name, grp_cfg in wanted.items():
        base = (GROUP, name)
        errors_before = sum(m.severity == "error" for m in rep.messages)

        def error(path, text, _base=base, _name=name):
            rep.error("project", _base + tuple(path), f"sample group {_name}: {text}")

        # PWM group and its trigger output
        entry = used_pwm.get(grp_cfg.pwm)
        if entry is None:
            error(("pwm",), f"PWM group {grp_cfg.pwm} is not used by the project (pwm: "
                            f"{', '.join(used_pwm) or '-'})")
            continue
        grp, use = entry
        if use.trigger is None:
            error(("pwm",), f"PWM group {grp.name} has no trigger output: set pwm.{grp.name}.trigger.point "
                            f"(zero_low or zero_high, IO-61)")
            continue
        timer_trigger = router.timer_triggers.get(grp.instance)
        if timer_trigger is None:
            error(("pwm",), f"the trigger outputs of {grp.instance} (timer of PWM group {grp.name}) reach no input "
                            f"of the {router.name} of {derivative} (mcudb trigger_router.timer_triggers: "
                            f"{', '.join(router.timer_triggers) or '-'})")
            continue

        # channels
        members_raw = []
        seen: dict[str, int] = {}
        for i, ch_name in enumerate(grp_cfg.channels):
            ch = board_adc.get(ch_name)
            if ch is None:
                error(("channels", i), f"board {cfg.board.board.name} has no ADC entry {ch_name} (adc[].name)")
                continue
            if ch_name in seen:
                error(("channels", i), f"{ch_name} is listed twice (first at channels[{seen[ch_name]}])")
                continue
            seen[ch_name] = i
            if not ch.fitted:
                error(("channels", i), f"the circuit of {ch_name} is not fitted on board {cfg.board.board.name} "
                                       f"(adc[].fitted: false)")
                continue
            members_raw.append((i, ch))
        if sum(m.severity == "error" for m in rep.messages) != errors_before:
            continue

        # pins: nobody else may use them (the channels of a group are no software ADC channels)
        if claims is None:
            claims = _claims(cfg)
        pin_ok = True
        for i, ch in members_raw:
            claim = claims.get(f"pin {ch.pin}")
            # the member itself read in software (use.adc) is the exclusivity finding below, not a pin conflict
            if claim is not None and claim.owner != f"adc {ch.name}":
                error(("channels", i), f"pin {ch.pin} of {ch.name} is used by {claim.owner}")
                pin_ok = False
        if not pin_ok:
            continue

        # sequencers per ADC, in the order of first use
        by_adc: dict[str, list[tuple[int, object]]] = {}
        for i, ch in members_raw:
            by_adc.setdefault(ch.instance, []).append((i, ch))
        seq_ok = True
        for adc, chs in by_adc.items():
            first = chs[0][0]
            seq = sequencers.get(adc)
            if seq is None:
                error(("channels", first), f"{adc} has no sample sequencer on {derivative} (mcudb sample_sequencers: "
                                           f"{', '.join(f'{s.name} -> {s.adc}' for s in sequencers.values())})")
                seq_ok = False
                continue
            if len(chs) > seq.slots:
                error(("channels", chs[seq.slots][0]), f"{len(chs)} channels on {adc}, but its sequencer {seq.name} "
                                                       f"converts at most {seq.slots} per trigger")
                seq_ok = False
                continue
            if seq.trigger_target not in targets:
                rep.error("derivative", ("sample_sequencers",), f"sequencer {seq.name}: trigger_target "
                                                                f"{seq.trigger_target} is no output of {router.name}")
                seq_ok = False
                continue
            if seq.name in sequencer_user:
                error(("channels", first), f"{adc} (sequencer {seq.name}) already belongs to "
                                           f"{sequencer_user[seq.name]}; one ADC serves one sample group")
                seq_ok = False
                continue
            out = targets[seq.trigger_target]
            if out.name in taken:
                error(("channels", first), f"the {router.name} output {out.name} that starts {seq.name} is used by "
                                           f"{taken[out.name]}")
                seq_ok = False
                continue
            if out.register_index in locked:
                error(("channels", first), f"the {router.name} register {out.register_index} of output {out.name} "
                                           f"(start of {seq.name}) is locked until reset by "
                                           f"{locked[out.register_index]}; a lock freezes every field of it")
                seq_ok = False
                continue
            users = software.get(adc)
            if users:
                what = ", ".join(u for u, _ in users)
                error(("channels", first), f"{adc} is exclusive to the hardware chain of the group, but the project "
                                           f"converts on it in software: {what}; move these channels to another "
                                           f"ADC or into a sample group (the trigger mode is one setting per ADC)")
                seq_ok = False
                continue
        if not seq_ok:
            continue

        # timing
        period = _pwm_period_ns(cfg, grp, use)
        conversions = {adc: _conversion_ns(cfg, adc) for adc in by_adc}
        missing = [adc for adc, ns in conversions.items() if ns is None]
        if period is None or missing:
            what = ("the converter settings of modules.bspAdcAccess or the clock of " + ", ".join(missing)
                    if missing else f"the realised period of PWM group {grp.name}")
            error((), f"the timing cannot be derived without {what}")
            continue
        if cfg.adc_access.averages > 1:
            rep.warning("project", base, f"sample group {name}: the converter averages {cfg.adc_access.averages} "
                                         f"conversions per result (modules.bspAdcAccess.averages): every sample of "
                                         f"the group spreads over {conversions[next(iter(by_adc))]} ns instead of "
                                         f"one sample phase")
        seqs_used = [sequencers[adc] for adc in by_adc]
        domains = {s.clock for s in seqs_used}
        if len(domains) != 1:
            error((), f"the sequencers {', '.join(s.name for s in seqs_used)} run on different clocks; one delay "
                      f"value cannot start them together")
            continue
        seq_hz = Fraction(cfg.clock.domains[domains.pop()])
        ticks = int((Fraction(grp_cfg.delay_ns) * seq_hz / 10**9) + Fraction(1, 2))
        limit = min((1 << s.counter_bits) - 1 for s in seqs_used)
        if ticks > limit:
            error(("delay_ns",), f"{grp_cfg.delay_ns} ns are {ticks} clocks of the sequencer ({float(seq_hz) / 1e6:g} "
                                 f"MHz); its counter holds at most {limit} ({float(limit * 10**9 / seq_hz):.0f} ns)")
            continue
        clock_ns = Fraction(10**9) / seq_hz
        start = max(s.start_clocks for s in seqs_used)
        first_ns = ticks * clock_ns + start * clock_ns
        members: dict[int, SampleMember] = {}
        seq_uses = []
        end = Fraction(0)
        completing = None
        warnings = []
        for adc, chs in by_adc.items():
            seq = sequencers[adc]
            conv = conversions[adc]
            t = first_ns
            for slot, (i, ch) in enumerate(chs):
                if slot > 0:
                    t += (seq.start_clocks * clock_ns) if seq.back_to_back else 0
                members[i] = SampleMember(ch.name, adc, ch.channel, ch.pin, seq.name, slot, t)
                t += conv
            out = targets[seq.trigger_target]
            seq_uses.append(SequencerUse(seq.name, adc, out.name, out.register_index, out.selector, len(chs), conv, t))
            if completing is None or t > end:
                end, completing = t, seq.name
            if not seq.back_to_back and len(chs) > 1:
                error(("channels", chs[1][0]), f"sequencer {seq.name} has no back-to-back mode; several channels on "
                                               f"{adc} would need planned delays (not supported)")
            if len(chs) > 1:
                offset = members[chs[1][0]].sample_ns - members[chs[0][0]].sample_ns
                span = members[chs[-1][0]].sample_ns - members[chs[0][0]].sample_ns
                text = (f"sample group {name}: {', '.join(c.name for _, c in chs)} are on the same ADC {adc} and are "
                        f"sampled one after another, {float(offset):.0f} ns apart ({float(span):.0f} ns from the "
                        f"first to the last); quantities that must be sampled at the same instant belong on "
                        f"different ADCs")
                rep.warning("project", base + ("channels", chs[1][0]), text)
                warnings.append(text)
        if sum(m.severity == "error" for m in rep.messages) != errors_before:
            continue
        if end >= period:
            error(("channels",), f"the group ends {float(end):.0f} ns after the trigger point, not inside one period "
                                 f"of PWM group {grp.name} ({float(period):.0f} ns): the next trigger would come "
                                 f"while it converts; fewer channels per ADC, a shorter delay or a lower frequency")
            continue
        if nvic and name not in ranked:
            path = ("interrupts", "ranks") if cfg.project.interrupts.ranks else base
            rep.error("project", path, f"sample group {name} has no interrupt rank: its group complete interrupt "
                                       f"needs one (interrupts.ranks: {{source: sample_group, name: {name}, "
                                       f"rank: <n>}})")
            continue
        for s in seqs_used:
            sequencer_user[s.name] = f"sample group {name}"
            taken[s.trigger_target] = f"sample group {name}"
        confirmed = not grp_cfg.verify and not use.verify and not grp.verify
        result.append(SampleGroupUse(
            name, grp.name, grp.instance, use.trigger.point, router.name,
            {"counter": router.sources[timer_trigger.counter], "match": router.sources[timer_trigger.match]},
            grp_cfg.delay_ns, ticks, seq_hz, first_ns, tuple(members[i] for i in sorted(members)), tuple(seq_uses),
            completing, end, period, all(s.back_to_back for s in seqs_used), confirmed, tuple(warnings)))
    return result


def info(cfg: Config) -> list[str]:
    """One readable line per sample group (nxconfig check, not a message)."""
    lines = []
    for g in getattr(cfg, "sample_groups", None) or []:
        per = "; ".join(f"{s.adc} ({s.name}): " + ", ".join(
            f"{m.name} {float(m.sample_ns):.0f} ns" for m in g.members if m.sequencer == s.name)
            for s in g.sequencers)
        lines.append(f"sample group {g.name}: trigger of PWM group {g.pwm} ({g.timer}, {g.trigger_point}) + "
                     f"{float(g.realised_delay_ns):.0f} ns -> {per}; complete {float(g.end_ns):.0f} ns after the "
                     f"trigger point ({float(g.end_ns * 100 / g.period_ns):.1f} % of the period "
                     f"{float(g.period_ns):.0f} ns), interrupt of {next(s.adc for s in g.sequencers if s.name == g.completing)}")
    return lines
