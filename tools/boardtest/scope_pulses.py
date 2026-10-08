# SPDX-License-Identifier: Apache-2.0
"""SYS-147: pulses on one channel of an oscilloscope capture (CSV of LabControl `acquire`, PicoScope 2204A).

Board finding 2026-09-30: a pulse of about 0.2 ms / 5 V on MOT1_GD_DRV_EN, caught by a single trigger that was armed
since the IO-45 test - which event produced it could not be told afterwards. This script evaluates the capture of the
re-run (checklist in doc/changelog.d/SYS-147.md) instead of reading widths off a screen:

    .venv/Scripts/python tools/boardtest/scope_pulses.py <capture.csv> --channel B [--threshold 2.5]
        [--low] [--expect none|one|any] [--min-width-ms X] [--max-width-ms Y]

- A pulse is an interval above the threshold (``--low``: below it, e.g. an SPI chip select). A level that is already
  active at the start of the capture or still active at its end is reported as open (width unknown on that side).
- ``--expect none``: passed if the channel never becomes active (reset or flash without intervention: DRV_EN must stay
  low). ``--expect one``: exactly one closed pulse whose width lies in [min, max] (pin case A: J-Link write to PSOR,
  taken back by the hold of S1 (1 ms poll) or the read-back of SafeIo, whichever comes first).
- Exit code 0 passed, 1 not passed, 2 the file is not such a capture.

The CSV format: comment lines ``# ...`` (scope, sample interval, trigger index, channel ranges), a header
``time_s,<ch>_V,...`` and one row per sample. Only the standard library is used.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


class CaptureError(Exception):
    """The file is not a capture in the expected format."""


@dataclass(frozen=True)
class Pulse:
    start_s: float | None  # None: already active at the start of the capture
    end_s: float | None    # None: still active at the end of the capture

    @property
    def closed(self) -> bool:
        return self.start_s is not None and self.end_s is not None

    @property
    def width_s(self) -> float | None:
        return (self.end_s - self.start_s) if self.closed else None


def read_channel(path: Path, channel: str) -> tuple[list[float], list[float]]:
    """Time and voltage of `channel` (A, B, ...) from a LabControl capture CSV."""
    times: list[float] = []
    volts: list[float] = []
    column = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if column is None:
                names = line.split(",")
                if not names or names[0] != "time_s":
                    raise CaptureError(f"{path}: header 'time_s,...' expected, found {line[:60]!r}")
                wanted = f"{channel}_V"
                if wanted not in names:
                    raise CaptureError(f"{path}: channel {channel} not in the capture (columns {names[1:]})")
                column = names.index(wanted)
                continue
            fields = line.split(",")
            try:
                times.append(float(fields[0]))
                volts.append(float(fields[column]))
            except (IndexError, ValueError) as exc:
                raise CaptureError(f"{path}: row {line[:60]!r} is not numeric") from exc
    if column is None or not times:
        raise CaptureError(f"{path}: no samples")
    return times, volts


def find_pulses(times: list[float], volts: list[float], threshold: float, low: bool = False) -> list[Pulse]:
    """Intervals in which the level is active (above `threshold`, or below it with `low`)."""
    def active(v: float) -> bool:
        return (v < threshold) if low else (v > threshold)

    pulses: list[Pulse] = []
    start: float | None = None
    state = active(volts[0])
    opened_at_start = state
    for t, v in zip(times[1:], volts[1:]):
        now = active(v)
        if now == state:
            continue
        if now:
            start = t
            opened_at_start = False
        else:
            pulses.append(Pulse(None if opened_at_start else start, t))
        state = now
    if state:
        pulses.append(Pulse(None if opened_at_start else start, None))
    return pulses


def judge(pulses: list[Pulse], expect: str, min_width_s: float | None, max_width_s: float | None) -> list[str]:
    """Deviations from the expectation; empty if passed."""
    problems: list[str] = []
    if expect == "none":
        if pulses:
            problems.append(f"expected no active level, found {len(pulses)} pulse(s)")
        return problems
    if expect == "one":
        if len(pulses) != 1:
            problems.append(f"expected exactly one pulse, found {len(pulses)}")
            return problems
        if not pulses[0].closed:
            problems.append("the pulse is open (active at the start or at the end of the capture)")
            return problems
    for p in pulses:
        if not p.closed:
            continue
        w = p.width_s
        if min_width_s is not None and w < min_width_s:
            problems.append(f"pulse at {p.start_s * 1e3:.3f} ms is {w * 1e3:.3f} ms wide, below {min_width_s * 1e3:.3f} ms")
        if max_width_s is not None and w > max_width_s:
            problems.append(f"pulse at {p.start_s * 1e3:.3f} ms is {w * 1e3:.3f} ms wide, above {max_width_s * 1e3:.3f} ms")
    return problems


def describe(p: Pulse) -> str:
    start = "open" if p.start_s is None else f"{p.start_s * 1e3:9.3f} ms"
    end = "open" if p.end_s is None else f"{p.end_s * 1e3:9.3f} ms"
    width = "-" if p.width_s is None else f"{p.width_s * 1e3:.3f} ms"
    return f"  start {start}  end {end}  width {width}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--channel", default="A")
    parser.add_argument("--threshold", type=float, default=2.5, help="volts at the probe tip (default 2.5)")
    parser.add_argument("--low", action="store_true", help="active low (pulses below the threshold)")
    parser.add_argument("--expect", choices=("none", "one", "any"), default="any")
    parser.add_argument("--min-width-ms", type=float, default=None)
    parser.add_argument("--max-width-ms", type=float, default=None)
    args = parser.parse_args(argv)
    try:
        times, volts = read_channel(args.csv, args.channel)
    except (OSError, CaptureError) as exc:
        print(f"error: {exc}")
        return 2
    pulses = find_pulses(times, volts, args.threshold, args.low)
    step = (times[-1] - times[0]) / max(len(times) - 1, 1)
    print(f"{args.csv.name}: channel {args.channel}, {len(times)} samples, {step * 1e6:.3f} us per sample, "
          f"{len(pulses)} pulse(s) {'below' if args.low else 'above'} {args.threshold} V")
    for p in pulses:
        print(describe(p))
    problems = judge(pulses, args.expect,
                     None if args.min_width_ms is None else args.min_width_ms / 1e3,
                     None if args.max_width_ms is None else args.max_width_ms / 1e3)
    for text in problems:
        print(f"NOT PASSED: {text}")
    if not problems and args.expect != "any":
        print("passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
