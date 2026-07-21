"""Runs Case 1 through Case 4 back-to-back.

    python run_all_cases.py [--dry-run] [--qwp2-direction-sign 1]

hardware_gate.require_confirmation() still runs before EVERY case
(including the first) -- there is no flag anywhere in this project that
skips it. Each case requires you to physically add or remove an optic
from the beam, and that is exactly the kind of manual step this gate
exists to force a deliberate confirmation of, every single time.

If any case raises (a camera/motor error, a failed hardware confirmation
loop via Ctrl-C, etc.), this stops immediately rather than continuing to
the next case with unknown hardware state.
"""

from __future__ import annotations

import argparse

from run_case import CASE_SPECS, run_case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="Simulate motors/camera instead of touching real hardware.")
    parser.add_argument("--exposure", type=float, default=None, help="Camera exposure, microseconds.")
    parser.add_argument("--gain", type=float, default=None, help="Camera gain.")
    parser.add_argument("--frame-rate", type=float, default=None, help="Camera frame rate, fps.")
    parser.add_argument("--n-frames-average", type=int, default=4,
                         help="Frames averaged per angle (default: 4).")
    parser.add_argument("--dark-frame-count", type=int, default=5,
                         help="Frames averaged for the dark-current reference (default: 5).")
    parser.add_argument("--qwp2-direction-sign", type=int, default=None, choices=(1, -1),
                         help="Case 4 only. No default -- see README's 'Sign convention' section.")
    base_args = parser.parse_args()

    for case_number in sorted(CASE_SPECS):
        print(f"\n{'=' * 78}\nStarting Case {case_number}\n{'=' * 78}")
        case_args = argparse.Namespace(
            case=case_number,
            dry_run=base_args.dry_run,
            out=None,
            exposure=base_args.exposure,
            gain=base_args.gain,
            frame_rate=base_args.frame_rate,
            n_frames_average=base_args.n_frames_average,
            dark_frame_count=base_args.dark_frame_count,
            qwp2_direction_sign=base_args.qwp2_direction_sign,
        )
        run_case(case_args)

    print("\nAll 4 cases complete.")


if __name__ == "__main__":
    main()
