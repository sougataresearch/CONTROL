"""
================================================================================
 mmie/angles.py -- all ANGLE arithmetic in one place
================================================================================
 Covers your three rules:
   RULE 1: user gives OPTICAL angles; motor angle = (zero_offset + optical) % 360
           (e.g. optical 30 on PSG polarizer whose zero is motor 50 -> motor 80;
            optical 320 with zero 50 -> 370 -> wraps to 10)
   RULE 2: angle lists can be given two ways:
           "360/10"          -> 36 positions: 0,10,20,...,350  (360 == 0, so excluded)
           "0,30,60,90"      -> exactly those optical angles
           [0, 30, 60, 90]   -> a real Python list also works
   RULE 3: PSG list and PSA list can be DIFFERENT; the measurement grid is the
           full cross product (every PSG angle paired with every PSA angle).
================================================================================
"""

from itertools import product           # builds the PSG x PSA cross product for us


def parse_angle_spec(spec):
    """
    Turn a user 'angle spec' into a sorted list of OPTICAL angles (floats).

    Accepted forms:
      "360/10"        -> divide form: full circle in steps of 10 deg -> [0,10,...,350]
      "180/45"        -> works for any span/step: [0,45,90,135] (180 excluded only
                         when span is a full 360; for partial spans the end IS included)
      "0,30,60,90"    -> comma list of explicit angles
      [0, 30, 60]     -> already a list/tuple -> used as-is
    """
    # --- case A: caller already passed a real list/tuple of numbers -------------
    if isinstance(spec, (list, tuple)):
        return [float(a) % 360.0 for a in spec]            # normalize each into [0,360)

    text = str(spec).strip()                               # work with a clean string

    # --- case B: divide form "SPAN/STEP" ----------------------------------------
    if "/" in text and "," not in text:
        span_s, step_s = text.split("/")                   # split "360/10" -> "360","10"
        span, step = float(span_s), float(step_s)          # convert both to numbers
        if step <= 0:
            raise ValueError("step must be > 0")           # guard against 360/0
        n = int(round(span / step))                        # how many steps fit in the span
        angles = [i * step for i in range(n)]              # 0, step, 2*step, ... (span excluded)
        if span < 360.0:                                   # for PARTIAL spans (e.g. 180/45)
            angles.append(span)                            # ...the end point is physically distinct,
        #                                                  # so include it; for 360 it equals 0 -> skip
        return [a % 360.0 for a in angles]                 # normalize just in case

    # --- case C: comma list "0,30,60" --------------------------------------------
    parts = [p for p in text.split(",") if p.strip()]      # split and drop empty entries
    return [float(p) % 360.0 for p in parts]               # convert each to float in [0,360)


def optical_to_motor(optical_angle, zero_offset):
    """
    RULE 1 implemented in one line.
    Example: zero_offset=50, optical=320 -> 370 -> 370-360 = 10 (the '% 360' does the minus).
    """
    return (float(zero_offset) + float(optical_angle)) % 360.0   # always lands in [0, 360)


def build_combinations(psg_angles, psa_angles):
    """
    Full cross product: every PSG optical angle paired with every PSA optical angle.
    Returns a list of (psg_optical, psa_optical) tuples, PSG-major order
    (PSG stays put while PSA sweeps -> minimizes PSG motor moves).
    len(result) == len(psg_angles) * len(psa_angles)   e.g. 6 x 6 = 36 images.
    """
    return [(g, a) for g, a in product(psg_angles, psa_angles)]  # itertools does the pairing


def preview_combinations(combos, n_show=8):
    """
    Pretty-print the FIRST few combinations plus the total count, so you can
    visually confirm the grid BEFORE any motor moves (your requirement).
    """
    total = len(combos)                                          # total images to be captured
    print(f"Total combinations (images): {total}")               # e.g. "Total combinations: 36"
    for i, (g, a) in enumerate(combos[:n_show]):                 # show only the first n_show
        print(f"  #{i+1:>4}:  PSG optical = {g:7.2f} deg   |   PSA optical = {a:7.2f} deg")
    if total > n_show:                                           # tell user the list continues
        print(f"  ... ({total - n_show} more)")


def filename_for(psg_optical, psa_optical):
    """
    Your naming rule: '<PSG optical>_<PSA optical>.bmp' using OPTICAL angles only.
    Integers print without decimals (30_0.bmp); non-integers keep one decimal (22.5_0.bmp).
    """
    def fmt(a):                                                  # tiny helper to format one angle
        a = float(a)                                             # ensure it is a number
        return str(int(a)) if a == int(a) else f"{a:.1f}"        # 30.0 -> "30", 22.5 -> "22.5"
    return f"{fmt(psg_optical)}_{fmt(psa_optical)}.bmp"          # e.g. "30_60.bmp"
