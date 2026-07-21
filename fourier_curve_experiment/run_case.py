"""Single entry point: orchestrates one calibration case end-to-end --

    hardware_gate -> preflight -> sweep -> fit_and_plot -> save everything

To run:

    python run_case.py --case 1
    python run_case.py --case 4 --qwp2-direction-sign 1
    python run_case.py --case 1 --dry-run   (simulated hardware, no real motors/camera)

You will be prompted for the polarizer/QWP mount's camera exposure/gain
(no auto-exposure/auto-gain exists in this project's camera_controller.py
-- see preflight.check_exposure_and_gain_applied) and, for Case 4 only,
the QWP1:QWP2 rotation-direction sign, which has NO default -- you must
have physically confirmed it against real hardware first (see README's
"Sign convention" section) before this will run.

Results are saved to
RESULT/calibration/fourier_curve_experiment/case<N>_<name>/<timestamp>/.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

_REQUIRED_PACKAGES = {
    "numpy": "numpy",
    "matplotlib": "matplotlib",
    "scipy": "scipy",
}


def _ensure_dependencies() -> None:
    """Install any of this script's required packages that aren't already
    present, using the same Python interpreter running this script.
    cv2/pythonnet/ids_peak are deliberately NOT eagerly installed here --
    camera_controller.py/motor_controller.py only import them lazily, on
    the non-dry-run path, matching this whole project's existing
    convention (dry-run must work without the hardware SDKs installed)."""

    missing = [pip_name for module_name, pip_name in _REQUIRED_PACKAGES.items()
               if importlib.util.find_spec(module_name) is None]
    if not missing:
        return
    print(f"Installing missing dependencies: {', '.join(missing)}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    except subprocess.CalledProcessError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--break-system-packages", *missing]
        )


_ensure_dependencies()

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np

import fit_and_plot
import hardware_gate
import preflight
import sweep
from camera_controller import CameraController
from config import ACTIVE_MOTORS, CameraSettings, TimingSettings
from motor_controller import MotorController

RESULT_ROOT = Path(r"C:\COMPARE_CASES\RESULT")

CASE_OUTPUT_NAMES = {
    1: "case1_p1_only",
    2: "case2_qwp1",
    3: "case3_qwp2",
    4: "case4_full_pcsca",
}

# Per-case sweep parameters -- see README's "Nyquist / sampling
# requirement" table for how angle_step_deg/highest_harmonic_cycles were
# chosen for each case.
CASE_SPECS = {
    1: dict(primary_mount="PSG_Polarizer", secondary_mount=None, coupling_ratio=None,
            angle_step_deg=5.0, highest_harmonic_cycles=2),
    2: dict(primary_mount="PSG_QWP", secondary_mount=None, coupling_ratio=None,
            angle_step_deg=10.0, highest_harmonic_cycles=4),
    3: dict(primary_mount="PSA_QWP", secondary_mount=None, coupling_ratio=None,
            angle_step_deg=10.0, highest_harmonic_cycles=4),
    4: dict(primary_mount="PSG_QWP", secondary_mount="PSA_QWP", coupling_ratio=5.0,
            angle_step_deg=3.0, highest_harmonic_cycles=24),
}


def default_output_directory(case_number: int) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (RESULT_ROOT / "calibration" / "fourier_curve_experiment"
            / CASE_OUTPUT_NAMES[case_number] / timestamp)


def _git_commit_hash() -> str:
    """Short git commit hash of the code that produced this result --
    matches the same provenance pattern used everywhere in control/matrix/."""

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unversioned"


def ask_float(prompt: str, default: float) -> float:
    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


def ask_direction_sign() -> int:
    print(
        "\nCase 4 requires the QWP1:QWP2 rotation-direction sign. This has NO "
        "default -- physically confirm it first (move QWP1 alone by a small "
        "positive optical step, watch which way it turns as viewed from the "
        "beam; do the same for QWP2 alone; if they turn the SAME physical "
        "way, the sign is +1, otherwise -1). See README's 'Sign convention' "
        "section."
    )
    while True:
        text = input("QWP1:QWP2 rotation-direction sign (+1 or -1): ").strip()
        if text in ("1", "+1"):
            return 1
        if text == "-1":
            return -1
        print("Enter exactly '1', '+1', or '-1'.")


def run_case(args: argparse.Namespace) -> None:
    case_number = args.case
    out_dir = Path(args.out) if args.out else default_output_directory(case_number)
    case_name = CASE_OUTPUT_NAMES[case_number]

    hardware_gate.require_confirmation(case_number)

    exposure_us = args.exposure if args.exposure is not None else ask_float(
        "Camera exposure (microseconds)", 1000.0
    )
    gain = args.gain if args.gain is not None else ask_float("Camera gain", 1.0)
    frame_rate_fps = args.frame_rate if args.frame_rate is not None else ask_float(
        "Camera frame rate (fps)", 30.0
    )

    qwp2_direction_sign = None
    spec = dict(CASE_SPECS[case_number])
    if spec["secondary_mount"] is not None:
        qwp2_direction_sign = args.qwp2_direction_sign if args.qwp2_direction_sign is not None else ask_direction_sign()
        sweep.check_nyquist(spec["angle_step_deg"], spec["highest_harmonic_cycles"])

    timing = TimingSettings()
    motors = MotorController(ACTIVE_MOTORS, timing, dry_run=args.dry_run)
    camera = CameraController(
        CameraSettings(exposure_us=exposure_us, gain=gain, frame_rate_fps=frame_rate_fps),
        dry_run=args.dry_run,
    )

    try:
        motors.discover()
        motors.connect_all()
        motors.initialize_all()
        motors.enable_all()
        motors.set_all_velocity(timing.rotation_velocity_deg_s, timing.rotation_accel_deg_s2)
        motors.home_all()
        motors.move_to_optical_zero_all()

        camera.discover()
        camera.initialize()

        preflight_result = preflight.run_preflight(
            motors, camera, out_dir, case_name,
            dark_frame_count=args.dark_frame_count,
        )

        angle_intensity = sweep.run_sweep(
            motors, camera, out_dir / "Images",
            primary_mount=spec["primary_mount"],
            angle_step_deg=spec["angle_step_deg"],
            secondary_mount=spec["secondary_mount"],
            coupling_ratio=spec["coupling_ratio"],
            qwp2_direction_sign=qwp2_direction_sign,
            n_frames_average=args.n_frames_average,
            dark_frame=preflight_result.dark_frame,
        )
        np.save(out_dir / "angle_intensity.npy", angle_intensity)

        if case_number == 1:
            fit_result = fit_and_plot.fit_and_plot_case1(angle_intensity, out_dir)
        elif case_number in (2, 3):
            fit_result = fit_and_plot.fit_and_plot_case23(
                angle_intensity, f"Case {case_number}", out_dir
            )
        else:
            fit_result = fit_and_plot.fit_and_plot_case4(angle_intensity, out_dir)

        payload = {
            "case": case_number,
            "fit": fit_result,
            "provenance": {
                "generated": datetime.now().isoformat(timespec="seconds"),
                "git_commit": _git_commit_hash(),
                "dry_run": args.dry_run,
                "n_frames_average": args.n_frames_average,
                "dark_frame_count": args.dark_frame_count,
                "qwp2_direction_sign": qwp2_direction_sign,
                "camera": {
                    "requested_exposure_us": exposure_us,
                    "requested_gain": gain,
                    "requested_frame_rate_fps": frame_rate_fps,
                    "applied_exposure_us": camera.settings.applied_exposure_us,
                    "applied_gain": camera.settings.applied_gain,
                },
                "motor_positions_at_start": preflight_result.motor_positions_at_start,
            },
        }
        (out_dir / "fit_params.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nCase {case_number} complete. Results saved to {out_dir}")
    finally:
        camera.close()
        motors.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=int, required=True, choices=(1, 2, 3, 4))
    parser.add_argument("--dry-run", action="store_true",
                         help="Simulate motors/camera instead of touching real hardware.")
    parser.add_argument("--out", default=None, help="Output directory override.")
    parser.add_argument("--exposure", type=float, default=None, help="Camera exposure, microseconds.")
    parser.add_argument("--gain", type=float, default=None, help="Camera gain.")
    parser.add_argument("--frame-rate", type=float, default=None, help="Camera frame rate, fps.")
    parser.add_argument("--n-frames-average", type=int, default=4,
                         help="Frames averaged per angle (default: 4).")
    parser.add_argument("--dark-frame-count", type=int, default=5,
                         help="Frames averaged for the dark-current reference (default: 5).")
    parser.add_argument("--qwp2-direction-sign", type=int, default=None, choices=(1, -1),
                         help="Case 4 only. No default -- see README's 'Sign convention' section.")
    args = parser.parse_args()
    run_case(args)


if __name__ == "__main__":
    main()
