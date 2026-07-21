"""Generic angle-sweep runner shared by all 4 cases.

Moves one mount (Cases 1-3) or two coupled mounts (Case 4) through a grid
of optical angles, captures n_frames_average frames per angle and
averages them (a capability that does not exist anywhere in
camera_controller.py -- it is built fresh here), optionally subtracts a
dark-current reference (from preflight.capture_dark_frame), saves each
averaged frame, and returns the (angle, ROI-mean intensity) array
fit_and_plot.py fits against theory.py's models.

Mount names are this project's canonical hardware names (matching
config.ZERO_OFFSET's keys): "PSG_Polarizer" (P1), "PSG_QWP" (QWP1),
"PSA_QWP" (QWP2), "PSA_Analyzer" (P2) -- FRIENDLY_NAME below maps these
onto the short names used in saved filenames.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from camera_controller import CameraController, roi_mean
from config import ROI_ROWS_COLS, ZERO_OFFSET, roi_xywh
from motor_controller import MotorController
from utils import format_angle, optical_to_motor

FRIENDLY_NAME = {
    "PSG_Polarizer": "P1",
    "PSG_QWP": "QWP1",
    "PSA_QWP": "QWP2",
    "PSA_Analyzer": "P2",
}

# Dry-run intensity placeholder, matching camera_controller.py's own
# dry-run convention (CameraController.save_bmp() reports
# last_mean_intensity = 125.0 in dry-run, since there is no real pixel
# array to compute a mean from).
DRY_RUN_INTENSITY_PLACEHOLDER = 125.0


def check_nyquist(angle_step_deg: float, highest_harmonic_cycles: int) -> float:
    """Print the Nyquist arithmetic for a harmonic completing
    ``highest_harmonic_cycles`` full cycles over a 0-360 degree sweep, and
    warn (does not raise) if ``angle_step_deg`` doesn't satisfy it. Returns
    the oversampling factor (bare-Nyquist-limit / actual step) so callers
    can log or assert on it themselves."""

    period_deg = 360.0 / highest_harmonic_cycles
    bare_nyquist_limit_deg = period_deg / 2.0
    oversampling = bare_nyquist_limit_deg / angle_step_deg
    print(
        f"Nyquist check: highest harmonic {highest_harmonic_cycles} cycles/360deg "
        f"(period {period_deg:.3f} deg), bare Nyquist limit {bare_nyquist_limit_deg:.3f} deg, "
        f"step {angle_step_deg:.3f} deg -> {oversampling:.2f}x oversampling."
    )
    if oversampling < 1.0:
        print(
            "WARNING: this step size does NOT satisfy the Nyquist criterion for "
            "this harmonic -- fitted coefficients at or above this harmonic will "
            "alias into lower ones. Reduce the step size before trusting this "
            "case's fit."
        )
    return oversampling


def _build_filename(
    primary_mount: str, theta_primary: float,
    secondary_mount: str | None, theta_secondary: float | None,
) -> str:
    parts = [f"{FRIENDLY_NAME[primary_mount]}_{format_angle(theta_primary)}"]
    if secondary_mount is not None:
        parts.append(f"{FRIENDLY_NAME[secondary_mount]}_{format_angle(theta_secondary)}")
    return "_".join(parts) + ".bmp"


def _capture_one_point(
    camera: CameraController,
    path: Path,
    n_frames_average: int,
    dark_frame,
    roi: tuple[int, int, int, int],
) -> float:
    """Capture n_frames_average frames, average them, optionally subtract
    the dark reference, save the result, and return its ROI mean -- the
    single (angle, intensity) data point for this angle."""

    if camera.dry_run:
        camera.save_bmp(None, path)
        return DRY_RUN_INTENSITY_PLACEHOLDER

    frames = [camera.acquire().astype(np.float64) for _ in range(n_frames_average)]
    averaged = np.mean(frames, axis=0)
    if dark_frame is not None:
        averaged = np.clip(averaged - dark_frame, 0.0, 255.0)
    image = averaged.astype(np.uint8)
    camera.save_bmp(image, path)
    return roi_mean(image, roi)


def run_sweep(
    motors: MotorController,
    camera: CameraController,
    out_dir: Path,
    primary_mount: str,
    angle_step_deg: float,
    angle_start_deg: float = 0.0,
    angle_stop_deg: float = 360.0,
    secondary_mount: str | None = None,
    coupling_ratio: float | None = None,
    qwp2_direction_sign: int | None = None,
    n_frames_average: int = 1,
    dark_frame=None,
    roi_rows_cols: tuple[int, int, int, int] = ROI_ROWS_COLS,
    settle_s: float = 0.3,
) -> np.ndarray:
    """Sweep ``primary_mount`` from angle_start_deg to angle_stop_deg
    (exclusive) in angle_step_deg steps. If ``secondary_mount`` is given
    (Case 4 only), it is driven to
    ``(qwp2_direction_sign * coupling_ratio * theta_primary) % 360`` at
    every step.

    ``coupling_ratio``/``qwp2_direction_sign`` are REQUIRED (no default)
    whenever ``secondary_mount`` is given -- there is no safe default sign
    for the QWP1:QWP2 rotation-direction relationship (see the README's
    "Sign convention" section); it must be physically confirmed against
    real hardware and passed explicitly every time.

    Returns an (N, 2) array of (optical angle in degrees, ROI-mean
    intensity), in sweep order.
    """

    if secondary_mount is not None:
        if coupling_ratio is None or qwp2_direction_sign is None:
            raise ValueError(
                "secondary_mount was given but coupling_ratio/qwp2_direction_sign "
                "were not. Case 4's QWP1:QWP2 rotation-direction sign has no safe "
                "default -- it must be physically confirmed against real hardware "
                "first (see README's 'Sign convention' section), then passed "
                "explicitly here."
            )
        if qwp2_direction_sign not in (1, -1):
            raise ValueError(f"qwp2_direction_sign must be 1 or -1, got {qwp2_direction_sign!r}.")

    out_dir.mkdir(parents=True, exist_ok=True)
    roi = roi_xywh(roi_rows_cols)
    angles = np.arange(angle_start_deg, angle_stop_deg, angle_step_deg)
    records = []

    for raw_theta in angles:
        theta_primary = float(raw_theta) % 360.0
        motor_angle_primary = optical_to_motor(theta_primary, ZERO_OFFSET[primary_mount])
        motors.move_motor_angle(primary_mount, motor_angle_primary)

        theta_secondary = None
        if secondary_mount is not None:
            theta_secondary = (qwp2_direction_sign * coupling_ratio * theta_primary) % 360.0
            motor_angle_secondary = optical_to_motor(theta_secondary, ZERO_OFFSET[secondary_mount])
            motors.move_motor_angle(secondary_mount, motor_angle_secondary)

        time.sleep(settle_s)

        filename = _build_filename(primary_mount, theta_primary, secondary_mount, theta_secondary)
        intensity = _capture_one_point(camera, out_dir / filename, n_frames_average, dark_frame, roi)
        records.append((theta_primary, intensity))

    print(f"Sweep complete: {len(records)} angle(s) captured, saved to {out_dir}.")
    return np.array(records, dtype=np.float64)
