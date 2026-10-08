"""Family-neutral calculation kernels for clocks and communication timings (doc/architecture/README.md, section 4).

No configuration objects, no register encodings: every function takes frequencies and the limits of the hardware
block and returns the chosen integer settings. The family clock models (clock.py) combine them with the clock tree
of their family; the generators encode the result into registers. Exact arithmetic with Fraction throughout.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterator


@dataclass(frozen=True)
class PllLimits:
    input_hz: tuple[int, int]           # reference after the input divider (phase detector)
    vco_hz: tuple[int, int]
    input_divider: tuple[int, int]
    multiplier: tuple[int, int]
    output_dividers: tuple[int, ...]    # possible VCO -> PLL output dividers (S32K1 SPLL: fixed 2)


@dataclass(frozen=True)
class PllSetting:
    input_divider: int
    multiplier: int
    output_divider: int
    input_hz: Fraction
    vco_hz: Fraction
    output_hz: Fraction


def pll_settings(reference_hz: int, limits: PllLimits) -> Iterator[PllSetting]:
    """All PLL settings within the limits for a reference clock (oscillator), in no particular order."""
    for div in range(limits.input_divider[0], limits.input_divider[1] + 1):
        pfd = Fraction(reference_hz, div)
        if not limits.input_hz[0] <= pfd <= limits.input_hz[1]:
            continue
        for mult in range(limits.multiplier[0], limits.multiplier[1] + 1):
            vco = pfd * mult
            if not limits.vco_hz[0] <= vco <= limits.vco_hz[1]:
                continue
            for out in limits.output_dividers:
                yield PllSetting(div, mult, out, pfd, vco, vco / out)


def pll_preference(s: PllSetting) -> tuple:
    """Sort key: integer reference frequency first (all derived clocks stay whole numbers of Hz), then the
    highest reference (smallest multiplier, least jitter), then the highest VCO frequency."""
    return (s.input_hz.denominator != 1, -s.input_hz, -s.vco_hz, s.output_divider)


def smallest_divider(clock_hz, max_hz, dividers) -> int | None:
    """Smallest divider from `dividers` (ascending) that brings clock_hz to at most max_hz."""
    for d in dividers:
        if Fraction(clock_hz) / d <= max_hz:
            return d
    return None


# ----------------------------------------------------------------------------------------------- CAN bit timing


@dataclass(frozen=True)
class CanTimingLimits:
    """Limits of a classic CAN controller, all segments in time quanta (tq)."""

    prescaler: tuple[int, int]
    prop_seg: tuple[int, int]
    phase_seg1: tuple[int, int]
    phase_seg2: tuple[int, int]
    sjw: tuple[int, int]
    quanta: tuple[int, int]             # tq per bit including the sync segment


@dataclass(frozen=True)
class CanTiming:
    clock_hz: int
    prescaler: int
    prop_seg: int
    phase_seg1: int
    phase_seg2: int
    sjw: int

    @property
    def quanta(self) -> int:
        return 1 + self.prop_seg + self.phase_seg1 + self.phase_seg2

    @property
    def bitrate(self) -> Fraction:
        return Fraction(self.clock_hz, self.prescaler * self.quanta)

    @property
    def sample_point(self) -> Fraction:
        """Sample point as a fraction of the bit (13/16 = 81.25 %)."""
        return Fraction(1 + self.prop_seg + self.phase_seg1, self.quanta)


def _within(value: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= value <= bounds[1]


def can_bit_timing(clock_hz: int, bitrate: int, sample_point: Fraction, limits: CanTimingLimits,
                   sjw: int | None = None, prop_seg: int | None = None) -> tuple[CanTiming | None, str | None]:
    """Bit timing with exactly `bitrate` and the sample point closest to `sample_point` (fraction of the bit).

    Choice: smallest sample point deviation, then most tq per bit (finest resolution). Phase segment 2 follows from
    the sample point and is at least the controller minimum. Without `prop_seg` the tq before the sample point are
    split with phase segment 1 = phase segment 2 where possible (Bosch rule for the largest oscillator tolerance),
    the remainder in the propagation segment. Without `sjw`: as large as allowed (min(4, phase_seg1, phase_seg2)).
    Returns (timing, None) or (None, reason).
    """
    best, best_key = None, None
    exact_rate = False
    for tq in range(limits.quanta[1], limits.quanta[0] - 1, -1):
        if clock_hz % (bitrate * tq):
            continue
        presc = clock_hz // (bitrate * tq)
        if not _within(presc, limits.prescaler):
            continue
        exact_rate = True
        target = sample_point * tq
        for before in sorted({int(target), int(target) + 1}):     # tq up to the sample point incl. sync
            pseg2 = tq - before
            tseg1 = before - 1
            if not _within(pseg2, limits.phase_seg2):
                continue
            if prop_seg is not None:
                prop = prop_seg
                pseg1 = tseg1 - prop
            else:
                pseg1 = min(max(pseg2, tseg1 - limits.prop_seg[1], limits.phase_seg1[0]), limits.phase_seg1[1])
                prop = tseg1 - pseg1
            if not (_within(prop, limits.prop_seg) and _within(pseg1, limits.phase_seg1)):
                continue
            jump = sjw if sjw is not None else min(limits.sjw[1], pseg1, pseg2)
            if not (_within(jump, limits.sjw) and jump <= pseg1 and jump <= pseg2):
                continue
            timing = CanTiming(clock_hz, presc, prop, pseg1, pseg2, jump)
            key = (abs(timing.sample_point - sample_point), -tq)
            if best_key is None or key < best_key:
                best, best_key = timing, key
    if best is not None:
        return best, None
    if not exact_rate:
        return None, (f"{bitrate} bit/s cannot be derived exactly from {clock_hz} Hz with "
                      f"{limits.quanta[0]}..{limits.quanta[1]} tq per bit and prescaler "
                      f"{limits.prescaler[0]}..{limits.prescaler[1]}")
    parts = []
    if prop_seg is not None:
        parts.append(f"prop_seg_tq {prop_seg}")
    if sjw is not None:
        parts.append(f"sjw_tq {sjw}")
    return None, (f"no bit timing for {bitrate} bit/s at {clock_hz} Hz fits the segment limits"
                  + (f" with {', '.join(parts)}" if parts else "")
                  + f" (prop {limits.prop_seg[0]}..{limits.prop_seg[1]}, phase1 "
                  f"{limits.phase_seg1[0]}..{limits.phase_seg1[1]}, phase2 {limits.phase_seg2[0]}.."
                  f"{limits.phase_seg2[1]}, sjw {limits.sjw[0]}..{limits.sjw[1]} tq)")


# ----------------------------------------------------------------------------------------------- UART baud rate


@dataclass(frozen=True)
class BaudLimits:
    oversampling: tuple[int, int]
    divider: tuple[int, int]
    preferred_oversampling: int         # chosen on equal error (e.g. the reset value of the register)


@dataclass(frozen=True)
class BaudSetting:
    clock_hz: int
    oversampling: int
    divider: int

    @property
    def baudrate(self) -> Fraction:
        return Fraction(self.clock_hz, self.oversampling * self.divider)

    def error(self, wanted: int) -> Fraction:
        """Relative deviation (positive: faster than wanted)."""
        return self.baudrate / wanted - 1


def baud_divider(clock_hz: int, baudrate: int, limits: BaudLimits) -> BaudSetting | None:
    """Oversampling and divider with the smallest baud rate error; on equal error the preferred oversampling,
    otherwise the highest (better noise immunity). None if no divider is in range."""
    best, best_key = None, None
    for osr in range(limits.oversampling[0], limits.oversampling[1] + 1):
        ideal = Fraction(clock_hz, baudrate * osr)
        for div in {int(ideal), int(ideal) + 1}:
            if not _within(div, limits.divider):
                continue
            s = BaudSetting(clock_hz, osr, div)
            key = (abs(s.error(baudrate)), osr != limits.preferred_oversampling, -osr)
            if best_key is None or key < best_key:
                best, best_key = s, key
    return best
