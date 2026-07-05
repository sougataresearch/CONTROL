"""Central configuration and data models for 4x4 CONTINUOUS rotation mode.

This file is a deliberate duplicate of discreate_angle/config.py, not an
import from it — the two acquisition modes are kept fully independent per
folder, so nothing here is shared with discreate_angle/. Only continuous
mode's four motors, its camera/timing knobs, and its (fixed-angle, ratio)
experiment shape live here; there is no MeasurementState/state_inputs
concept because continuous rotation has no discrete steps.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT_ROOT / "Data"

# EXPERIMENT SETTING — MUST EDIT PER LAB SETUP. Keep in sync by hand with
# discreate_angle/config.py's MOTOR_SN — the two are not read from a shared
# file on purpose, so verify both after any hardware change.
MOTOR_SN: dict[str, str] = {
    "PSG_Polarizer": "55542004",
    "PSG_QWP": "",
    "PSA_QWP": "",
    "PSA_Analyzer": "55542504",
    # Not part of ACTIVE_MOTORS — a separate motorized stage for a sample
    # mount. Used two ways: (1) calibration.verify_with_reference_sample(), a
    # KNOWN reference optic for system self-verification; (2)
    # 01_main.setup_sample_stage(), an OPTIONAL per-sample motorized mount an
    # operator may use to set/verify a real sample's orientation before
    # inserting it for measurement. Leave blank if no SAMPLE stage exists.
    "SAMPLE": "",
}

# EXPERIMENT SETTING — MUST EDIT PER LAB SETUP. Motor angle that equals
# optical zero for each axis. See discreate_angle/config.py for the same
# note in more detail; kept in sync by hand, not by import.
ZERO_OFFSET: dict[str, float] = {
    "PSG_Polarizer": 121.7,
    "PSG_QWP": 0.0,
    "PSA_QWP": 0.0,
    "PSA_Analyzer": 61.55,
    "SAMPLE": 0.0,
}

KINESIS_DIR = Path(r"C:\Program Files\Thorlabs\Kinesis")
REQUIRED_KINESIS_DLLS = (
    "Thorlabs.MotionControl.DeviceManagerCLI.dll",
    "Thorlabs.MotionControl.GenericMotorCLI.dll",
    "Thorlabs.MotionControl.IntegratedStepperMotorsCLI.dll",
)
MOTOR_SETTINGS_NAME = "K10CR2"

# EXPERIMENT SETTING — VERIFY AGAINST THE ACTUAL CAMERA DATASHEET. Deliberate
# duplicate of discreate_angle/config.py's FALLBACK_SENSOR_WIDTH/HEIGHT — see
# that file's docstring for why this exists (two conflicting hardcoded guesses
# were found elsewhere in this project). Used only in dry-run, by
# CameraController.frame_width/frame_height; real runs read the camera's own
# configured Width/Height GenICam nodes instead.
FALLBACK_SENSOR_WIDTH = 4000
FALLBACK_SENSOR_HEIGHT = 3000

# 4x4 continuous always uses all four motors: the two polarizers are parked
# at a fixed optical angle, while both QWPs spin continuously at the
# configured revolution ratio.
ACTIVE_MOTORS: tuple[str, ...] = ("PSG_Polarizer", "PSG_QWP", "PSA_QWP", "PSA_Analyzer")
# The two motors that actually rotate continuously (as opposed to being
# parked at a fixed angle). Read by motor_controller's continuous-move calls.
ROTATING_MOTORS: tuple[str, ...] = ("PSG_QWP", "PSA_QWP")


@dataclass(slots=True)
class CameraSettings:
    """Requested + applied camera values. Structurally the same fields as
    discreate_angle's CameraSettings — duplicated, not imported — since
    continuous mode's actual trigger mode (software-per-frame vs hardware
    free-run) is still an open decision; see continuous_engine.py."""

    exposure_us: float = 10_000.0
    frame_rate_fps: float = 30.0
    gain: float = 1.0
    timeout_ms: int = 5_000
    pixel_format: str = "Mono8"
    max_retries: int = 2
    retry_backoff_s: float = 1.0
    mean_too_dark: float = 1.0
    mean_too_bright: float = 250.0
    # EXPERIMENT SETTING. camera_controller.select_roi()'s sliding-window size
    # (pixels) used to find a flat, sufficiently bright region on the bright
    # reference frame, for the bright/dark reference ratio only.
    roi_window_size: int = 200
    # Step (pixels) between candidate ROI windows. Smaller = finer search, slower.
    roi_stride: int = 100
    # Minimum acceptable mean intensity for a candidate ROI window.
    roi_min_mean: float = 50.0
    model: str = ""
    serial_number: str = ""
    applied_exposure_us: float | None = None
    applied_frame_rate_fps: float | None = None
    applied_gain: float | None = None


@dataclass(slots=True)
class TimingSettings:
    """Delays, retries, and tolerances around motor/camera operations,
    plus the continuous-rotation-specific velocity/tolerance settings that
    discreate_angle's TimingSettings has no equivalent for."""

    motor_timeout_ms: int = 60_000
    settling_before_s: float = 0.5
    settling_after_s: float = 0.2
    homing_settle_s: float = 1.0
    inter_motor_settle_s: float = 2.0
    enable_settle_s: float = 1.0
    motor_max_retries: int = 2
    motor_retry_backoff_s: float = 1.0
    position_tolerance_deg: float = 0.1
    # EXPERIMENT SETTING. Angular velocity (deg/s) of the SLOWER QWP
    # (PSG_QWP); the faster QWP's velocity is this times the configured
    # rotation ratio. Tune once real hardware timing/inertia is known.
    base_angular_velocity_deg_s: float = 10.0
    # EXPERIMENT SETTING. Acceleration (deg/s^2) applied to both QWPs when
    # starting/stopping continuous rotation.
    rotation_accel_deg_s2: float = 20.0
    # How close (deg) a polled PSG_QWP position must be to its starting
    # angle to be accepted as "one full revolution complete".
    revolution_tolerance_deg: float = 0.5
    # How often (seconds) the engine polls motor position while spinning.
    position_poll_interval_s: float = 0.05
    # EXPERIMENT SETTING. Angle-triggered capture: fire the camera every time
    # PSG_QWP crosses this many degrees of additional travel. 1.0 deg ->
    # 360 images per revolution; 0.5 deg -> 720. Reconstruction
    # (matrix/own_code/CONTINOUS/4x4) needs at least ~25 evenly-spaced
    # samples per revolution to resolve the 12th-harmonic content of a 1:5
    # dual-rotating-QWP polarimeter; 360 gives generous oversampling for a
    # robust least-squares fit without demanding an unrealistic trigger rate.
    capture_angle_step_deg: float = 1.0


@dataclass(slots=True)
class ExperimentMetadata:
    operator: str
    sample: str
    comments: str = ""


@dataclass(slots=True)
class ExperimentConfig:
    """Serializable snapshot of one continuous-rotation run.

    Structurally simpler than discreate_angle's ExperimentConfig: there is
    no ``mode``/``subtype`` choice (this folder only ever runs 4x4
    continuous) and no ``state_inputs`` (no discrete state list exists).
    """

    metadata: ExperimentMetadata
    run_directory: Path
    dry_run: bool = False
    # The fixed PSG_Polarizer/PSA_Analyzer optical angles the QWPs rotate around.
    fixed_angles: dict[str, float] = field(default_factory=dict)
    # (slow, fast) relative revolution counts, e.g. (1, 5) == PSA_QWP spins
    # 5x for every 1 revolution of PSG_QWP.
    rotation_ratio: tuple[int, int] = (1, 1)
    camera: CameraSettings = field(default_factory=CameraSettings)
    timing: TimingSettings = field(default_factory=TimingSettings)
    # Optional: this sample's optical angle on the motorized SAMPLE stage
    # (01_main.setup_sample_stage()), or None if placed by hand instead.
    sample_stage_optical_angle: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["run_directory"] = str(self.run_directory)
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExperimentConfig":
        data = dict(payload)
        data["run_directory"] = Path(data["run_directory"])
        data["metadata"] = ExperimentMetadata(**data["metadata"])
        data["camera"] = CameraSettings(**data.get("camera", {}))
        data["timing"] = TimingSettings(**data.get("timing", {}))
        ratio = data.get("rotation_ratio", (1, 1))
        data["rotation_ratio"] = tuple(ratio)
        return cls(**data)
