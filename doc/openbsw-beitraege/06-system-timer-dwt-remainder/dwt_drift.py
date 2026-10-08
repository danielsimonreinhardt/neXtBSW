# SPDX-License-Identifier: Apache-2.0
# Model of updateTicks() in
# executables/referenceApp/platforms/s32k148evb/bspConfiguration/src/bsp/SystemTimer/SystemTimer.cpp
# DWT at 80 MHz, TICK_FREQ_MHZ = 16 -> dwtTicksRatio = 5. One tick = 1/16 us.
# Compares the upstream version (state.lastDwt = curDwt) with the patched one
# (state.lastDwt += newTicks * state.dwtTicksRatio) against the true elapsed time.
# Usage: python dwt_drift.py   (plain Python 3, no packages; about one minute)
import random

RATIO = 5
MASK = 0xFFFFFFFF
DWT_MHZ = 80
TICK_MHZ = 16


def run(intervals, keep_remainder):
    ticks = 0
    last = 0
    cur = 0
    for d in intervals:
        cur = (cur + d) & MASK
        new = ((cur - last) & MASK) // RATIO
        ticks += new
        last = (last + new * RATIO) & MASK if keep_remainder else cur
    return ticks


def report(label, intervals):
    elapsed_cycles = sum(intervals)
    real_us = elapsed_cycles / float(DWT_MHZ)
    old_us = run(intervals, False) / float(TICK_MHZ)
    new_us = run(intervals, True) / float(TICK_MHZ)
    old_ppm = (old_us - real_us) / real_us * 1e6
    print("%-48s %9d calls, %8.3f s real time" % (label, len(intervals), real_us / 1e6))
    print(
        "    old %12.6f s (%+9.1f ppm, %+.4f s, %.3f s per hour)"
        % (old_us / 1e6, old_ppm, (old_us - real_us) / 1e6, old_ppm * 3600.0 / 1e6)
    )
    print(
        "    new %12.6f s (%+9.1f ppm, %+.4f s)"
        % (new_us / 1e6, (new_us - real_us) / real_us * 1e6, (new_us - real_us) / 1e6)
    )


if __name__ == "__main__":
    rnd = random.Random(20260917)
    # a) regular call, period an exact multiple of the ratio -> nothing is lost, the
    #    error only shows up once the call instants are not on the tick grid
    report("regular every 1 ms (80000 cycles = 16000 * 5)", [80000] * 60000)
    # b) periodic call with a few cycles of jitter, as in a real system
    report(
        "every ~997 us with jitter, 60 s",
        [997 * DWT_MHZ + rnd.randint(0, 9) for _ in range(60 * 1003)],
    )
    # c) 11 kHz of reads (task switches, async timeouts, polling drivers)
    report(
        "mixed ~11 kHz of reads with jitter, 60 s",
        [int(80e6 / 11000) + rnd.randint(0, 9) for _ in range(60 * 11000)],
    )
    # d) sysDelayUs busy wait: getSystemTimeUs32Bit polled as fast as the loop runs
    report("busy wait loop, ~25 cycles per poll, 1 s", [24 + rnd.randint(0, 3) for _ in range(2758620)])
