"""Pre-case hardware/data-quality checks: exposure/gain verification, a
dark-current reference capture, a live ROI sanity check, and a motor
position log -- all run once, immediately before each case's sweep, after
hardware_gate.require_confirmation() has already been answered.

Camera exposure/gain are asserted to have actually been applied as
requested rather than checking for an auto-exposure/auto-gain node:
camera_controller.py never sets or touches such a node anywhere in this
project (confirmed by grepping it -- see the fourier_curve_experiment
planning discussion), so this is confirming the hardware accepted the
requested values, not fighting an auto-mode that doesn't exist here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from camera_controller import CameraController
from config import ROI_ROWS_COLS, roi_xywh
from motor_controller import MotorController

EXPOSURE_TOLERANCE_US = 5.0
GAIN_TOLERANCE = 0.01

SATURATION_WARN_ABOVE = 240
TOO_DIM_WARN_BELOW = 20


def check_exposure_and_gain_applied(camera: CameraController) -> None:
    """Assert the camera actually applied the requested exposure_us/gain
    (camera_controller.CameraController.initialize()'s applied_* fields).
    Raises RuntimeError (a hard stop, not a warning) if either is missing
    or drifted beyond tolerance -- every case's absolute intensity scale
    depends on exposure/gain being what you think they are, and a silent
    mismatch here would corrupt the case without any other symptom."""

    settings = camera.settings
    if settings.applied_exposure_us is None or settings.applied_gain is None:
        raise RuntimeError(
            "Camera has not been initialized (applied_exposure_us/applied_gain "
            "are None) -- call camera.initialize() before running preflight checks."
        )
    exposure_drift = abs(settings.applied_exposure_us - settings.exposure_us)
    if exposure_drift > EXPOSURE_TOLERANCE_US:
        raise RuntimeError(
            f"Requested exposure {settings.exposure_us:.2f} us but camera applied "
            f"{settings.applied_exposure_us:.2f} us (drift {exposure_drift:.2f} us "
            f"> tolerance {EXPOSURE_TOLERANCE_US} us)."
        )
    gain_drift = abs(settings.applied_gain - settings.gain)
    if gain_drift > GAIN_TOLERANCE:
        raise RuntimeError(
            f"Requested gain {settings.gain:.4f} but camera applied "
            f"{settings.applied_gain:.4f} (drift {gain_drift:.4f} > tolerance "
            f"{GAIN_TOLERANCE})."
        )
    print(
        f"Exposure/gain confirmed fixed: {settings.applied_exposure_us:.2f} us, "
        f"gain {settings.applied_gain:.4f} (manually set -- no auto-exposure/"
        "auto-gain node exists in this project's camera_controller.py)."
    )


def capture_dark_frame(camera: CameraController, out_dir: Path, n_frames: int = 5):
    """Prompt the operator to block all light reaching the camera, then
    average n_frames real captures into a dark-current reference at this
    case's exposure/gain. Saves the averaged reference as dark_frame.npy
    (float64, for later subtraction in sweep.py) and
    dark_frame_preview.bmp (uint8, for a quick visual look) in out_dir.

    Returns the averaged float64 array on real hardware, or None in
    dry-run -- there is no real pixel array to construct one from in
    dry-run (matches camera_controller.py's own convention, where
    CameraController.last_image_array stays None in dry-run)."""

    out_dir.mkdir(parents=True, exist_ok=True)
    input(
        "Block the laser or cover the camera now (same exposure/gain as this "
        "case), then press Enter to capture the dark-current reference..."
    )
    if camera.dry_run:
        print("Dry-run: skipping real dark-frame averaging (no pixel array exists).")
        camera.save_bmp(None, out_dir / "dark_frame_preview.bmp")
        return None

    frames = [camera.acquire().astype(np.float64) for _ in range(n_frames)]
    dark_frame = np.mean(frames, axis=0)
    np.save(out_dir / "dark_frame.npy", dark_frame)
    camera.save_bmp(dark_frame.astype(np.uint8), out_dir / "dark_frame_preview.bmp")
    print(
        f"Dark-current reference captured: {n_frames} frame(s) averaged, "
        f"mean level {float(dark_frame.mean()):.3f}. Saved to {out_dir}."
    )
    input("Unblock the laser / uncover the camera now, then press Enter to continue...")
    return dark_frame


def check_roi(camera: CameraController, roi_rows_cols: tuple[int, int, int, int] = ROI_ROWS_COLS) -> None:
    """Capture one live preview frame and report the fixed ROI's min/max
    pixel value, warning if the ROI looks saturated or too dim. Advisory
    only -- does not raise -- since the operator needs to see the actual
    numbers to decide whether to fix illumination, not have the case
    silently aborted."""

    if camera.dry_run:
        print("Dry-run: skipping real ROI preview check (no pixel array exists).")
        return

    frame = camera.acquire()
    x, y, width, height = roi_xywh(roi_rows_cols)
    region = frame[y : y + height, x : x + width]
    minimum, maximum = int(region.min()), int(region.max())
    row_start, row_end, col_start, col_end = roi_rows_cols
    print(
        f"ROI preview -- rows {row_start}:{row_end}, cols {col_start}:{col_end}: "
        f"min={minimum}, max={maximum}"
    )
    if maximum > SATURATION_WARN_ABOVE:
        print(
            f"WARNING: ROI max {maximum} is above {SATURATION_WARN_ABOVE} -- may be "
            "near saturation. Consider reducing exposure/gain before continuing."
        )
    if minimum < TOO_DIM_WARN_BELOW:
        print(
            f"WARNING: ROI min {minimum} is below {TOO_DIM_WARN_BELOW} -- may be too "
            "dim. Consider increasing exposure/gain, or checking alignment, before "
            "continuing."
        )


def log_motor_positions(motors: MotorController, out_dir: Path, case_name: str) -> dict[str, float]:
    """Read back and save every connected motor's current encoder position
    -- the "software zero" this case actually started from. Returns the
    same dict that gets saved, so callers can embed it in other
    provenance records too."""

    out_dir.mkdir(parents=True, exist_ok=True)
    positions = motors.encoder_positions()
    payload = {"case": case_name, "motor_positions_at_start": positions}
    (out_dir / "motor_positions_at_start.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(f"Motor positions at case start: {positions}")
    return positions


@dataclass
class PreflightResult:
    dark_frame: "np.ndarray | None"
    motor_positions_at_start: dict[str, float]


def run_preflight(
    motors: MotorController,
    camera: CameraController,
    out_dir: Path,
    case_name: str,
    dark_frame_count: int = 5,
    roi_rows_cols: tuple[int, int, int, int] = ROI_ROWS_COLS,
) -> PreflightResult:
    """Runs every preflight check/capture in order, for one case:
    exposure/gain assertion -> ROI preview -> dark-frame capture -> motor
    position log. Called once by run_case.py, right after
    hardware_gate.require_confirmation() and before sweep.run_sweep()."""

    check_exposure_and_gain_applied(camera)
    check_roi(camera, roi_rows_cols)
    dark_frame = capture_dark_frame(camera, out_dir / "Dark", n_frames=dark_frame_count)
    motor_positions = log_motor_positions(motors, out_dir, case_name)
    return PreflightResult(dark_frame, motor_positions)
