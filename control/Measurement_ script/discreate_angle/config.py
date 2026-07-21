"""Central configuration and immutable experiment data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# PROJECT_ROOT: the folder this file lives in (g:\control). Used to anchor
# every other path in the project so the software works regardless of the
# directory it is launched from.
PROJECT_ROOT = Path(__file__).resolve().parent
# DATA_ROOT: where every experiment run folder (Data/YYYY-MM-DD_RunXX) is created.
# Change this only if you want runs written somewhere other than the project folder.
DATA_ROOT = PROJECT_ROOT / "Data"

# EXPERIMENT SETTING — MUST EDIT PER LAB SETUP.
# Maps each optical element to the USB serial number printed on its Thorlabs
# K10CR2/M rotator. motor_controller.py uses this dict to find/connect the
# correct physical device for each named axis. An empty string means "this
# motor is not present" — only valid for the two QWP axes in 3x3 mode, since
# ACTIVE_MOTORS["3x3"] never asks for them.
MOTOR_SN: dict[str, str] = {
    "PSG_Polarizer": "55542004",
    "PSG_QWP": "",
    "PSA_QWP": "",
    "PSA_Analyzer": "55542504",
    # Not part of ACTIVE_MOTORS — a separate motorized stage for a sample
    # mount. Used two ways: (1) calibration.verify_with_reference_sample(),
    # a KNOWN reference optic for system self-verification; (2)
    # 01_main.setup_sample_stage(), an OPTIONAL per-sample motorized mount an
    # operator may use to set/verify a real sample's orientation before
    # inserting it for measurement. Leave blank if no SAMPLE stage exists.
    "SAMPLE": "",
}

# EXPERIMENT SETTING — MUST EDIT PER LAB SETUP.
# For each motor, the MOTOR (physical/encoder) angle that corresponds to
# optical zero (0 degrees in the optical/polarization frame). Determined
# experimentally with calibration.py, then hand-copied in here. Every
# optical angle typed by the operator is converted to a motor angle with
# utils.optical_to_motor(optical, offset) = (optical + offset) % 360.
# Wrong values here silently rotate every measurement by a constant offset.
ZERO_OFFSET: dict[str, float] = {
    "PSG_Polarizer": 121.7,
    "PSG_QWP": 0.0,
    "PSA_QWP": 0.0,
    "PSA_Analyzer": 61.55,
    "SAMPLE": 0.0,
}

# EXPERIMENT SETTING — verify on each lab computer.
# Folder where Thorlabs Kinesis 64-bit is installed. motor_controller.py adds
# this to sys.path/DLL search path before importing the .NET Kinesis assemblies.
KINESIS_DIR = Path(r"C:\Program Files\Thorlabs\Kinesis")
# The three Kinesis .NET DLLs the software depends on. utils.check_environment()
# verifies each one exists before allowing a non-dry-run experiment. Only
# change this if a Kinesis upgrade renames/adds required assemblies.
REQUIRED_KINESIS_DLLS = (
    "Thorlabs.MotionControl.DeviceManagerCLI.dll",
    "Thorlabs.MotionControl.GenericMotorCLI.dll",
    "Thorlabs.MotionControl.IntegratedStepperMotorsCLI.dll",
)
# EXPERIMENT SETTING — verify against Kinesis.
# Must match the device settings profile name shown for the K10CR2 in the
# Kinesis application (Settings tab). motor_controller.initialize_all() sets
# configuration.DeviceSettingsName to this string before loading the motor
# configuration; a mismatch causes Kinesis to load the wrong motion profile.
MOTOR_SETTINGS_NAME = "K10CR2"

# EXPERIMENT SETTING — VERIFY AGAINST THE ACTUAL CAMERA DATASHEET.
# Fallback sensor dimensions used ONLY in dry-run (there is no real device to
# read Width/Height from). Real runs instead read the camera's actual
# configured Width/Height GenICam nodes — see CameraController.frame_width/
# frame_height — so this constant cannot silently produce a wrong disk-space
# estimate on real hardware, only in dry-run. Chosen as the larger (safer,
# over- rather than under-estimating) of two conflicting values found in this
# project: this file previously defaulted to 3840x2748, while
# MMIE_Control/mmie/config.py documents the same IDS U3-3890CP-M-GL at
# 4000x3000. Confirm the correct value against the datasheet or a real
# camera.Width/Height() readback and correct this if needed.
FALLBACK_SENSOR_WIDTH = 4000
FALLBACK_SENSOR_HEIGHT = 3000


@dataclass(slots=True)
class CameraSettings:
    """Camera controls intentionally limited to portable IDS Peak features.

    An instance of this holds both what the operator *requested* (exposure_us,
    frame_rate_fps, gain — pre-filled from the last saved values, then
    overwritten by guided_camera_setup() in 01_main.py) and what the camera
    *actually applied* (the applied_* fields, filled in only by
    CameraController.initialize() after talking to real hardware). Every
    field here is written into Config/experiment_config.json for that run.
    """

    # EXPERIMENT SETTING — default only; the operator is prompted to confirm/
    # override this every run in guided_camera_setup() (01_main.py).
    exposure_us: float = 10_000.0
    # EXPERIMENT SETTING — default only; same prompt-and-override as exposure.
    frame_rate_fps: float = 30.0
    gain: float = 1.0
    # How long (ms) camera_controller.acquire() waits for a triggered frame
    # before giving up. Raise this if frame_rate/exposure makes captures slow.
    timeout_ms: int = 5_000
    pixel_format: str = "Mono8"
    # Number of *extra* attempts after the first failed acquisition before
    # camera_controller.acquire_save_verify() raises CameraError.
    max_retries: int = 2
    retry_backoff_s: float = 1.0
    # Image-quality warning thresholds checked in camera_controller.save_bmp().
    # Mean intensity below this prints a "may be black" warning.
    mean_too_dark: float = 1.0
    # Mean intensity above this prints a "may be saturated" warning.
    mean_too_bright: float = 250.0
    # EXPERIMENT SETTING. camera_controller.select_roi()'s sliding-window size
    # (pixels) used to find a flat, sufficiently bright region on the bright
    # reference frame, for the bright/dark reference ratio only.
    roi_window_size: int = 200
    # Step (pixels) between candidate ROI windows. Smaller = finer search, slower.
    roi_stride: int = 100
    # Minimum acceptable mean intensity for a candidate ROI window.
    roi_min_mean: float = 50.0
    # model/serial_number: filled in automatically by CameraController.discover()/
    # initialize() — do not set manually.
    model: str = ""
    serial_number: str = ""
    # applied_*: filled in automatically after the camera confirms the value it
    # actually used (may differ slightly from the requested value due to
    # hardware quantization). None until CameraController.initialize() runs.
    applied_exposure_us: float | None = None
    applied_frame_rate_fps: float | None = None
    applied_gain: float | None = None


@dataclass(slots=True)
class TimingSettings:
    """Delays surrounding mechanical motion and image capture.

    Every field can be tuned here without touching the control-flow code in
    motor_controller.py / measurement_engine.py.
    """

    # How long (ms) MotorController.move_motor_angle() waits for one MoveTo
    # call to finish before Kinesis reports a timeout.
    motor_timeout_ms: int = 60_000
    # Pause after commanding a move, before triggering the camera (lets
    # mechanical vibration settle). Used in measurement_engine.run_discrete()
    # and 01_main.move_analyzer_to_optical().
    settling_before_s: float = 0.5
    # Pause after each completed state, before moving to the next one.
    settling_after_s: float = 0.2
    # Pause after each motor finishes homing.
    homing_settle_s: float = 1.0
    # Pause inserted between initializing/moving consecutive motors, since
    # motors are always driven sequentially, never in parallel.
    inter_motor_settle_s: float = 2.0
    # Pause after enabling each motor, before homing.
    enable_settle_s: float = 1.0
    # Number of *extra* attempts after a failed move before
    # MotorController.move_motor_angle() raises MotorError.
    motor_max_retries: int = 2
    motor_retry_backoff_s: float = 1.0
    # EXPERIMENT SETTING. Maximum allowed difference (degrees) between the
    # commanded motor angle and the encoder-reported position for a move to
    # be accepted. Tighten this for higher-precision work; loosen it if a
    # motor's encoder noise causes spurious retries.
    position_tolerance_deg: float = 0.1
    # EXPERIMENT SETTING. Explicit velocity profile applied to every active
    # motor once, during initialize_motors() — set in software
    # (MotorController.set_all_velocity(), Kinesis SetVelocityParams())
    # rather than left at whatever default happens to be stored on the
    # device/Kinesis profile, so the rotation speed used for every point-to-
    # point move (homing, optical-zero, every measurement state) is known
    # and reproducible across motors/lab computers.
    rotation_velocity_deg_s: float = 10.0
    rotation_accel_deg_s2: float = 20.0


@dataclass(slots=True)
class ExperimentMetadata:
    """Free-text identifying information the operator types in at the start
    of every run (01_main.configure_experiment()). Stored verbatim in the
    saved config and in the final text report."""

    operator: str
    sample: str
    comments: str = ""


@dataclass(slots=True)
class ExperimentConfig:
    """Serializable snapshot sufficient to audit or resume an experiment.

    Built once in 01_main.configure_experiment() (or reloaded from disk on
    --resume) and threaded through the whole run. Everything the software
    decided or the operator answered lives on this object, and to_dict()/
    from_dict() are what make Config/experiment_config.json possible.
    """

    mode: str  # "3x3" or "4x4" — chosen first, fixes which motors are used.
    metadata: ExperimentMetadata
    run_directory: Path  # Data/YYYY-MM-DD_RunXX for this run.
    dry_run: bool = False  # True = simulate hardware; see README "Recommended first test".
    # 4x4 only: the fixed PSG_Polarizer/PSA_Analyzer optical angles the QWPs rotate around.
    fixed_angles: dict[str, float] = field(default_factory=dict)
    # The raw angle lists the operator typed, keyed by motor name. Kept so
    # states_from_config() can deterministically regenerate the exact same
    # MeasurementState list on --resume without re-asking the operator.
    state_inputs: dict[str, list[float]] = field(default_factory=dict)
    camera: CameraSettings = field(default_factory=CameraSettings)
    timing: TimingSettings = field(default_factory=TimingSettings)
    # Optional: this sample's optical angle on the motorized SAMPLE stage
    # (01_main.setup_sample_stage()), or None if this sample was placed by
    # hand instead. Purely a record for the saved config/report — the stage
    # itself is already disconnected again by the time this is written.
    sample_stage_optical_angle: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain JSON-safe types for utils.write_json()."""

        result = asdict(self)
        result["run_directory"] = str(self.run_directory)
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExperimentConfig":
        """Rehydrate the nested dataclasses stored in experiment_config.json.

        Called only from 01_main.main() when --resume is used, to turn the
        saved JSON back into real ExperimentConfig/CameraSettings/TimingSettings
        objects (dataclasses do not deserialize themselves automatically).
        """

        data = dict(payload)
        data["run_directory"] = Path(data["run_directory"])
        data["metadata"] = ExperimentMetadata(**data["metadata"])
        camera_data = dict(data.get("camera", {}))
        # Read checkpoints/configurations written before retries meant "additional
        # attempts" rather than "total attempts."
        if "retries" in camera_data and "max_retries" not in camera_data:
            camera_data["max_retries"] = max(0, int(camera_data.pop("retries")) - 1)
        data["camera"] = CameraSettings(**camera_data)
        data["timing"] = TimingSettings(**data.get("timing", {}))
        return cls(**data)


# Which motor names are active (connected/homed/moved) for each mode.
# 3x3 skips both QWPs entirely; 4x4 uses all four. Read by 01_main.run_session()
# to construct MotorController and by state_generator indirectly via ZERO_OFFSET.
ACTIVE_MOTORS = {
    "3x3": ("PSG_Polarizer", "PSA_Analyzer"),
    "4x4": ("PSG_Polarizer", "PSG_QWP", "PSA_QWP", "PSA_Analyzer"),
}
