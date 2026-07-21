"""Interactive entry point for the Mueller Matrix Imaging Ellipsometer."""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import threading
import time
import traceback
from pathlib import Path

from camera_controller import CameraController, roi_mean, select_roi
from config import (
    ACTIVE_MOTORS,
    DATA_ROOT,
    ZERO_OFFSET,
    CameraSettings,
    ExperimentConfig,
    ExperimentMetadata,
    TimingSettings,
)
from measurement_engine import EmergencyStopRequested, MeasurementEngine
from logger_manager import SessionTranscript
from motor_controller import MotorController
from state_generator import generate_3x3, generate_4x4_discrete
from utils import (
    check_environment,
    create_run_directory,
    estimate_disk_bytes,
    parse_angle_spec,
    print_angles,
    optical_to_motor,
    rename_run_directory,
    write_json,
    yes_no,
)


# -----------------------------------------------------------------------
# Small input helpers — each loops until the operator gives a valid answer.
# -----------------------------------------------------------------------

def ask_choice(prompt: str, choices: set[str]) -> str:
    """Ask until the operator types one of ``choices`` exactly (e.g. "1"/"2")."""

    while True:
        answer = input(prompt).strip()
        if answer in choices:
            return answer
        print(f"Enter one of: {', '.join(sorted(choices))}")


def ask_float(prompt: str) -> float:
    """Ask until the operator types a parseable float (used for single
    fixed polarizer angles in 4x4 mode)."""

    while True:
        try:
            return float(input(prompt).strip())
        except ValueError:
            print("Enter a numeric angle.")


def ask_positive_float(prompt: str, default: float) -> float:
    """Ask for a positive runtime setting while showing the saved default."""

    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        try:
            value = default if not text else float(text)
            if value <= 0:
                raise ValueError
            return value
        except ValueError:
            print("Enter a number greater than zero.")


def ask_angles(prompt: str) -> list[float]:
    """Ask until utils.parse_angle_spec() accepts the text (either "360/step"
    or a comma-separated angle list). Used for every PSG/PSA/QWP angle-list prompt."""

    while True:
        try:
            return parse_angle_spec(input(prompt).strip())
        except ValueError as exc:
            print(f"Invalid angle specification: {exc}")


def choose_mode_first() -> str:
    """Ask 3x3 vs 4x4. Returns "3x3" or "4x4".

    This is intentionally the first input call: active hardware depends on it.
    Everything downstream (which motors get discovered/connected/homed, which
    ACTIVE_MOTORS entry is used) is decided by this single choice — for the
    whole multi-sample session (see run_fresh_session()), not just one sample.
    """

    print("1 : 3×3 Mueller Matrix")
    print("2 : 4×4 Mueller Matrix")
    return "3x3" if ask_choice("Select measurement mode: ", {"1", "2"}) == "1" else "4x4"


def print_environment_report() -> bool:
    """Run utils.check_environment() and print an OK/MISSING line per check.
    Returns True only if every check passed. The return value feeds directly
    into the dry-run default and the "can we run for real" gate."""

    print("\nEnvironment verification")
    all_ok = True
    for name, passed, detail in check_environment():
        print(f"  {'OK' if passed else 'MISSING':7} {name}: {detail}")
        all_ok &= passed
    return all_ok


def ask_metadata() -> ExperimentMetadata:
    """Ask operator/sample/comments.

    Split out from the old configure_experiment() because the sample name
    is needed earlier than the rest of a sample's configuration: it doubles
    as this round's run-folder name (see utils.create_run_directory()/
    rename_run_directory()), which must exist before anything about this
    sample is logged.
    """

    return ExperimentMetadata(
        operator=input("Operator Name: ").strip(),
        sample=input("Sample Name: ").strip(),
        comments=input("Comments: ").strip(),
    )


def ask_angles_for_mode(mode: str) -> tuple[dict[str, float], dict[str, list[float]], list]:
    """Ask the mode-specific angle inputs and build this sample's states.

    The other half of the old configure_experiment(): everything here is
    asked AFTER the sample's run folder already exists (see ask_metadata()),
    since none of it is needed to name that folder. Returns
    (fixed_angles, state_inputs, states) — fixed_angles is {} for 3x3.
    """

    if mode == "3x3":
        psg = ask_angles("PSG Polarizer angles (e.g. 360/10 or 0,30,60): ")
        psa = ask_angles("PSA Analyzer angles: ")
        print_angles("PSG Polarizer", psg, ZERO_OFFSET["PSG_Polarizer"])
        print_angles("PSA Analyzer", psa, ZERO_OFFSET["PSA_Analyzer"])
        states = generate_3x3(psg, psa)
        return {}, {"PSG_Polarizer": psg, "PSA_Analyzer": psa}, states

    fixed = {
        "PSG_Polarizer": ask_float("Fixed PSG Polarizer optical angle: ") % 360,
        "PSA_Analyzer": ask_float("Fixed PSA Analyzer optical angle: ") % 360,
    }
    psg = ask_angles("PSG QWP angles: ")
    psa = ask_angles("PSA QWP angles: ")
    print_angles("PSG QWP", psg, ZERO_OFFSET["PSG_QWP"])
    print_angles("PSA QWP", psa, ZERO_OFFSET["PSA_QWP"])
    states = generate_4x4_discrete(psg, psa, fixed)
    return fixed, {"PSG_QWP": psg, "PSA_QWP": psa}, states


def states_from_config(config: ExperimentConfig) -> list:
    """Rebuild deterministic states for an explicit ``--resume`` run.

    Mirrors ask_angles_for_mode()'s state-generation branch, but reads the
    angle lists back from config.state_inputs/fixed_angles (saved in
    Config/experiment_config.json) instead of asking the operator again —
    this is what guarantees a resumed run reproduces the exact same
    MeasurementState list (and therefore the same filenames/indices) as the
    original run, so the checkpoint's last_completed_index still lines up.
    """

    if config.mode == "3x3":
        return generate_3x3(
            config.state_inputs["PSG_Polarizer"],
            config.state_inputs["PSA_Analyzer"],
        )
    return generate_4x4_discrete(
        config.state_inputs["PSG_QWP"],
        config.state_inputs["PSA_QWP"],
        config.fixed_angles,
    )


def confirm_stage(text: str) -> None:
    """Ask a yes/no confirmation before a safety-sensitive step; treats "no"
    as a full cancellation (raises KeyboardInterrupt, caught by the calling
    session function, which stops motors and exits cleanly)."""

    if not yes_no(text):
        raise KeyboardInterrupt("Operator cancelled initialization.")


def initialize_motors(motors: MotorController, timing: TimingSettings) -> None:
    """Run the full hardware bring-up sequence for the active motors, with
    an operator confirmation gate before each stage:
    discover -> connect_all -> initialize_all -> enable_all ->
    (ask + set velocity) -> home_all -> move_to_optical_zero_all. Called
    once per session (not per sample — see run_fresh_session()), after the
    "Begin hardware initialization?" confirmation. See motor_controller.py
    for what each stage does.

    The velocity/acceleration prompt is pre-filled with config.py's
    rotation_velocity_deg_s/rotation_accel_deg_s2 (press Enter to accept
    them as-is, or type a different number for this session) — same
    ask-with-a-default pattern as guided_camera_setup()'s exposure/frame
    rate prompts, rather than silently applying the config default with no
    chance to override it per run.
    """

    motors.discover()
    confirm_stage("Continue with the listed devices?")
    motors.connect_all()
    confirm_stage("All active motors connected. Initialize them sequentially?")
    motors.initialize_all()
    confirm_stage("Initialization complete. Enable motors?")
    motors.enable_all()
    print("Motors enabled.")
    velocity = ask_positive_float(
        "Rotation velocity for all active motors (deg/s)", timing.rotation_velocity_deg_s
    )
    accel = ask_positive_float(
        "Rotation acceleration for all active motors (deg/s^2)", timing.rotation_accel_deg_s2
    )
    motors.set_all_velocity(velocity, accel)
    confirm_stage("Velocity set. Home active motors?")
    motors.home_all()
    confirm_stage("Homing complete. Move to configured optical zero offsets?")
    motors.move_to_optical_zero_all()


def setup_sample_stage(timing: TimingSettings, dry_run: bool) -> float | None:
    """Optionally bring up the motorized SAMPLE stage for THIS sample and set
    its optical angle, before any other per-sample setup runs.

    Asked right after the sample's metadata/run folder are known, and before
    capture_camera_references() (which needs an empty beam path). If the
    operator says yes, this runs the exact same bring-up sequence as
    initialize_motors() — discover -> connect -> initialize -> enable ->
    (ask + set velocity) -> home -> move to optical zero — but scoped to
    just the "SAMPLE" motor (config.MOTOR_SN["SAMPLE"]/
    ZERO_OFFSET["SAMPLE"]), since MotorController.names is otherwise fixed
    to whatever ACTIVE_MOTORS[mode] chose for this session. Moving to
    optical zero right after homing (before asking for the real target
    angle) is a sanity checkpoint that the configured offset is loading
    correctly — the same reference move initialize_motors() does for the
    other motors. Then asks the target optical angle (e.g. 30, 45, or any
    arbitrary angle) and moves there via optical_to_motor(angle,
    ZERO_OFFSET["SAMPLE"]) so the operator can verify the orientation with
    a polarimeter.

    Once verified, the SAMPLE stage is disconnected again immediately — the
    operator then physically lifts the whole mounted assembly out of the
    beam path and sets it aside while the rest of instrument setup (camera
    bright/dark references) runs with an empty beam, then reinserts it
    (still fixed at the angle just set) at the existing "insert the sample
    now" prompt right before acquisition.

    Returns the chosen optical angle, or None if the operator has no
    motorized SAMPLE stage for this sample (the rest of the flow is
    unaffected either way).
    """

    if not yes_no("Do you have a motorized SAMPLE stage for this sample?", default=False):
        return None

    sample_motor = MotorController(("SAMPLE",), timing, dry_run)
    sample_motor.discover()
    confirm_stage("Continue with the SAMPLE stage listed above?")
    sample_motor.connect_all()
    confirm_stage("SAMPLE stage connected. Initialize it?")
    sample_motor.initialize_all()
    confirm_stage("SAMPLE stage initialized. Enable it?")
    sample_motor.enable_all()
    print("SAMPLE stage enabled.")
    velocity = ask_positive_float(
        "SAMPLE stage rotation velocity (deg/s)", timing.rotation_velocity_deg_s
    )
    accel = ask_positive_float(
        "SAMPLE stage rotation acceleration (deg/s^2)", timing.rotation_accel_deg_s2
    )
    sample_motor.set_all_velocity(velocity, accel)
    confirm_stage("Velocity set. Home the SAMPLE stage?")
    sample_motor.home_all()
    confirm_stage("Homing complete. Move to the configured optical zero offset?")
    sample_motor.move_to_optical_zero_all()

    optical_angle = ask_float(
        "Sample optical angle to set on the SAMPLE stage (e.g. 30, 45, or any arbitrary angle): "
    ) % 360
    motor_angle = optical_to_motor(optical_angle, ZERO_OFFSET["SAMPLE"])
    sample_motor.move_motor_angle("SAMPLE", motor_angle)
    print(
        f"SAMPLE stage at optical {optical_angle:.3f}° (motor {motor_angle:.3f}°). "
        "Verify this orientation with a polarimeter now."
    )
    confirm_stage("Orientation verified. Disconnect the SAMPLE stage and set it aside for camera setup?")
    sample_motor.close()
    print(
        "SAMPLE stage disconnected. Keep the sample set aside, still at this angle, "
        "until the 'insert the sample now' prompt just before acquisition."
    )
    return optical_angle


def move_analyzer_to_optical(motors: MotorController, timing: TimingSettings, optical_angle: float) -> None:
    """Move the analyzer using its configured optical-zero calibration.

    Converts ``optical_angle`` to a motor angle with ZERO_OFFSET["PSA_Analyzer"],
    commands the move, then sleeps timing.settling_before_s. Used by the
    guided camera-check sequence (bright at optical 0, dark at optical 90)
    and by capture_camera_references() — never during the main measurement
    loop, which instead goes through motor_controller.move_state().
    """

    motor_angle = optical_to_motor(optical_angle, ZERO_OFFSET["PSA_Analyzer"])
    print(
        f"Moving PSA Analyzer to optical {optical_angle:.3f}° "
        f"(motor {motor_angle:.3f}°)."
    )
    motors.move_motor_angle("PSA_Analyzer", motor_angle)
    time.sleep(timing.settling_before_s)


def detect_camera(camera: CameraController) -> None:
    """Probe the camera and confirm it before any motor step runs.

    Called first, before initialize_motors(), so a missing/broken camera
    aborts the session immediately instead of after motors have already
    been connected, initialized, enabled, and homed — homing especially
    takes real time, and there is no point spending it if the camera was
    never going to work. Only camera.discover() runs here (a brief
    open-then-release probe); the camera is not actually opened for
    acquisition until guided_camera_setup()'s Cockpit checks are done and
    CameraController.initialize() is called.
    """

    camera.discover()
    confirm_stage("Camera detection succeeded. Continue with hardware initialization?")


def guided_camera_setup(
    dry_run: bool,
    motors: MotorController,
    camera: CameraController,
    camera_settings: CameraSettings,
    timing: TimingSettings,
) -> None:
    """Guide Cockpit checks while Python has released the camera.

    Called once per session (not per sample), after motors reach optical
    zero and before camera.initialize(). Camera presence was already
    confirmed earlier by detect_camera(), before any motor step. Sequence
    (see README "Camera preparation before every experiment"):
      1. Light-source reminder — operator confirms the illumination is on
         before any Cockpit check, since every check below needs it.
      2. Bright check at PSG=0, PSA=0 — operator opens Cockpit, confirms
         bright, closes Cockpit.
      3. Move PSA_Analyzer to optical 90 (move_analyzer_to_optical) — dark
         check — operator opens Cockpit, confirms darker, closes Cockpit.
      4. Move PSA_Analyzer back to optical 0.
      5. Operator opens Cockpit one more time to pick exposure/frame rate,
         writes both numbers down, closes Cockpit.
      6. ask_positive_float() collects those two numbers into
         camera_settings.exposure_us / frame_rate_fps (still just requested
         values — nothing has touched the real camera driver yet; that
         happens later in CameraController.initialize()).
    Dry-run mode skips every Cockpit prompt and keeps the saved defaults.
    """

    if dry_run:
        print(
            "Dry-run mode: IDS Peak Cockpit checks are simulated and saved camera "
            "defaults are retained."
        )
        return

    confirm_stage("Turn ON the illumination/light source. Is it on?")

    print("\nCAMERA CHECK 1 — BRIGHT STATE")
    print("PSG Polarizer and PSA Analyzer are at optical 0°.")
    input(
        "Open IDS Peak Cockpit, confirm the 0_0 image is bright, then CLOSE "
        "Cockpit and press Enter..."
    )
    confirm_stage("Is IDS Peak Cockpit fully closed?")

    move_analyzer_to_optical(motors, timing, 90.0)
    print("\nCAMERA CHECK 2 — DARK STATE")
    input(
        "Open IDS Peak Cockpit, confirm the 0_90 image is darker, then CLOSE "
        "Cockpit and press Enter..."
    )
    confirm_stage("Is IDS Peak Cockpit fully closed?")

    move_analyzer_to_optical(motors, timing, 0.0)
    print("\nCAMERA CHECK 3 — SELECT EXPERIMENT SETTINGS")
    input(
        "Open IDS Peak Cockpit at 0_0, choose exposure time and frame rate, "
        "write down both values, then CLOSE Cockpit and press Enter..."
    )
    confirm_stage("Is IDS Peak Cockpit fully closed?")

    exposure_ms = ask_positive_float(
        "Exposure time selected in IDS Peak Cockpit (ms)",
        camera_settings.exposure_us / 1000.0,
    )
    frame_rate_fps = ask_positive_float(
        "Frame rate selected in IDS Peak Cockpit (fps)",
        camera_settings.frame_rate_fps,
    )
    camera_settings.exposure_us = exposure_ms * 1000.0
    camera_settings.frame_rate_fps = frame_rate_fps
    print(f"Requested experiment exposure: {exposure_ms:.3f} ms")
    print(f"Requested experiment frame rate: {frame_rate_fps:.3f} fps")


def capture_camera_references(
    run_directory: Path,
    dry_run: bool,
    motors: MotorController,
    camera: CameraController,
    camera_settings: CameraSettings,
    timing: TimingSettings,
) -> None:
    """Capture quantitative bright/dark references without modifying pixels.

    Called for EVERY sample (not just once — the operator may have bumped
    the setup while swapping samples by hand), after camera.initialize()
    the first time and before each sample's "insert the sample" prompt.
    Moves to PSA optical 0 (bright) and 90 (dark), saves each as a real BMP
    via camera.test_frame(). The bright/dark ratio itself is computed over
    an automatically-selected ROI (see camera_controller.select_roi())
    rather than the whole frame, since edge vignetting/glare can distort a
    whole-frame average independent of actual polarization contrast — the
    ROI is picked once per sample, on that sample's bright frame, and reused
    on its dark frame so both means come from the same pixels. For real
    (non-dry-run) hardware, warns (and asks for confirmation to continue)
    if the bright reference isn't actually brighter than dark, or if it
    contains saturated (255) pixels anywhere in the frame. This is the
    software's only automatic sanity check that the polarizers are actually
    crossed/aligned correctly before committing to a full scan.
    """

    reference_dir = run_directory / "Results"
    move_analyzer_to_optical(motors, timing, 0.0)

    if dry_run:
        print("Capturing bright reference at PSG=0°, PSA=0°...")
        camera.test_frame(reference_dir / "BrightReference_0_0.bmp")
        move_analyzer_to_optical(motors, timing, 90.0)
        print("Capturing dark reference at PSG=0°, PSA=90°...")
        camera.test_frame(reference_dir / "DarkReference_0_90.bmp")
        move_analyzer_to_optical(motors, timing, 0.0)
        print(
            "Dry-run mode: reference files and statistics were verified, but "
            "physical polarization contrast cannot be evaluated (no ROI selected)."
        )
        return

    confirm_stage(
        "Illumination is ON? (required before the automatic bright/dark reference capture)"
    )

    print("Capturing bright reference at PSG=0°, PSA=0°...")
    bright = camera.test_frame(reference_dir / "BrightReference_0_0.bmp")
    roi = select_roi(
        camera.last_image_array,
        camera_settings.roi_window_size,
        camera_settings.roi_stride,
        camera_settings.roi_min_mean,
    )
    write_json(
        run_directory / "Config" / "roi.json",
        {"x": roi[0], "y": roi[1], "width": roi[2], "height": roi[3]},
    )
    bright_mean = roi_mean(camera.last_image_array, roi)

    move_analyzer_to_optical(motors, timing, 90.0)
    print("Capturing dark reference at PSG=0°, PSA=90°...")
    dark = camera.test_frame(reference_dir / "DarkReference_0_90.bmp")
    dark_mean = roi_mean(camera.last_image_array, roi)
    move_analyzer_to_optical(motors, timing, 0.0)

    contrast = float("inf") if dark_mean == 0 else bright_mean / dark_mean
    print(
        f"Polarization reference result (ROI {roi[2]}x{roi[3]} at {roi[0]},{roi[1]}) — "
        f"bright mean: {bright_mean:.3f}, dark mean: {dark_mean:.3f}, "
        f"bright/dark ratio: {contrast:.3f}"
    )

    problems = []
    if bright_mean <= dark_mean:
        problems.append("bright-reference ROI mean is not greater than dark-reference ROI mean")
    if int(bright["saturated_pixels"]) > 0:
        problems.append(
            f"bright reference contains {bright['saturated_pixels']} pixels at 255 (whole frame)"
        )
    if problems:
        print("CAMERA VERIFICATION WARNING: " + "; ".join(problems))
        confirm_stage("Continue despite the camera verification warning?")
    else:
        print("Camera bright/dark and saturation verification passed.")


def write_error_traceback(run: Path) -> None:
    """Persist the full active exception while also showing it in the transcript.

    Must be called from inside an ``except`` block (relies on
    traceback.format_exc() reading the currently-handled exception).
    Writes Logs/error_traceback.txt inside ``run`` (the CURRENT sample's
    folder, which may differ from the session's initial folder — see
    run_fresh_session()).
    """

    details = traceback.format_exc()
    path = run / "Logs" / "error_traceback.txt"
    path.write_text(details, encoding="utf-8")
    print(f"Full error traceback saved to: {path}")
    print(details)


def check_disk_space(run: Path, state_count: int, camera: CameraController) -> bool:
    """Print the estimated-vs-free disk space for ``state_count`` images and
    return whether there's enough room. Called once per sample (not just
    once per session), since earlier samples in the same session consume
    space that reduces what's left for later ones. Uses the camera's own
    actual frame_width/frame_height (real hardware) or the documented
    dry-run fallback, rather than a second guessed constant — see
    config.FALLBACK_SENSOR_WIDTH/HEIGHT."""

    estimate = estimate_disk_bytes(state_count, camera.frame_width, camera.frame_height)
    free = shutil.disk_usage(run).free
    print(f"Estimated image space: {estimate / 1024**3:.2f} GB; free: {free / 1024**3:.2f} GB")
    if estimate > free:
        print("Insufficient disk space for the planned images.")
        return False
    return True


def run_resumed_session(
    arguments: argparse.Namespace,
    run: Path,
    resumed_config: ExperimentConfig,
) -> int:
    """Resume a single interrupted run from its checkpoint.

    Deliberately does NOT enter the multi-sample loop that a fresh session
    does (see run_fresh_session()) — it recovers exactly the one saved
    experiment, using the mode/angles/camera settings frozen in its
    Config/experiment_config.json, then exits. Run the script again without
    --resume afterward to measure additional samples.
    Returns a process exit code (0 success, 1 error, 2 blocked by a
    pre-check, 130 stopped/cancelled).
    """

    config = resumed_config
    mode = config.mode
    environment_ok = print_environment_report()
    dry_run = config.dry_run
    if not dry_run and not environment_ok:
        print("Required production dependencies are missing; non-dry operation is unsafe.")
        return 2

    states = states_from_config(config)
    print(f"Resuming saved {mode} experiment: {run}")
    if config.sample_stage_optical_angle is not None:
        print(
            f"Sample stage was set to optical {config.sample_stage_optical_angle:.3f}° "
            "in the original session — not re-asked on resume."
        )

    if not yes_no("Begin hardware initialization and acquisition?"):
        print(f"Configuration retained at {run}")
        return 0

    stop_event = threading.Event()
    motors = MotorController(ACTIVE_MOTORS[mode], config.timing, dry_run)
    camera = CameraController(config.camera, dry_run)

    def request_stop(_signum, _frame) -> None:
        stop_event.set()
        motors.emergency_stop()
        camera.emergency_stop()

    def ask_camera_settings() -> tuple[float, float]:
        exposure_ms = ask_positive_float(
            "Exposure time (ms)", config.camera.exposure_us / 1000.0
        )
        frame_rate_fps = ask_positive_float(
            "Frame rate (fps)", config.camera.frame_rate_fps
        )
        print(
            f"Retrying with exposure {exposure_ms:.3f} ms, "
            f"frame rate {frame_rate_fps:.3f} fps."
        )
        return exposure_ms * 1000.0, frame_rate_fps

    signal.signal(signal.SIGINT, request_stop)
    try:
        detect_camera(camera)
        initialize_motors(motors, config.timing)
        guided_camera_setup(dry_run, motors, camera, config.camera, config.timing)
        write_json(run / "Config" / "experiment_config.json", config.to_dict())
        camera.initialize(ask_settings=ask_camera_settings)
        write_json(run / "Config" / "experiment_config.json", config.to_dict())
        # Needs the camera's actual frame_width/height, only known after
        # initialize() — see check_disk_space()'s docstring.
        if not check_disk_space(run, len(states), camera):
            return 2
        capture_camera_references(run, dry_run, motors, camera, config.camera, config.timing)
        confirm_stage(
            "Reference and camera verification complete. Insert the sample now, "
            "then confirm to start the measurement."
        )
        engine = MeasurementEngine(config, motors, camera, stop_event)
        completed, failed = engine.run_discrete(states)
        print(f"Experiment complete: {completed} images, {failed} failures.")
        print(f"Data directory: {run}")
        # Plain yes_no, not confirm_stage: declining just skips the rehome,
        # it should not cancel an already-successful experiment.
        if yes_no("Rehome motors before disconnect? (ensure nothing will interfere with rotation)", default=True):
            try:
                motors.home_all()
            except Exception as exc:
                print(f"Post-measurement rehoming warning: {exc}")
        else:
            print("Rehoming skipped by operator.")
        return 0
    except EmergencyStopRequested as exc:
        print(exc)
        return 130
    except KeyboardInterrupt:
        motors.emergency_stop()
        print("Cancelled by operator.")
        return 130
    except Exception as exc:
        motors.emergency_stop()
        print(f"Experiment aborted: {type(exc).__name__}: {exc}")
        write_error_traceback(run)
        return 1
    finally:
        try:
            camera.close()
        except Exception as exc:
            print(f"Camera cleanup warning: {exc}")
        motors.close()


def run_fresh_session(initial_run: Path) -> int:
    """Multi-sample session: hardware bring-up once, then loop over as many
    samples as the operator wants, each getting its own
    Data/YYYY-MM-DD_<sample name> folder.

    Owns its own transcript lifecycle (unlike run_resumed_session(), which
    lets main() manage one unchanging transcript for the whole call) because
    each new sample gets a fresh run folder, and therefore a fresh
    Logs/terminal_transcript.txt, once its name is known. The transcript is
    stopped before every rename/create and a new one started right after —
    Windows/NTFS refuses to rename a directory while a file inside it (the
    log itself) is still open, even by the same process, so this is
    required, not just tidy. The very first sample renames ``initial_run``
    (a "pending" placeholder main() created before mode selection) in
    place; every later sample gets a genuinely new folder via
    create_run_directory(), since its data must not land in the previous
    sample's folder. Either way the log's content survives intact — only
    its path changes — so the new transcript just appends and continues it.

    Mode is chosen once and fixed for the whole session (switching between
    3x3/4x4 mid-session would change which motors are active, which really
    would require a reconnect — that stays a restart-the-script situation).
    Bright/dark reference verification IS repeated for every sample, since
    the operator may bump the setup while swapping samples by hand.

    If one sample's measurement fails (a real MotorError/CameraError/etc. —
    not an emergency stop, which always ends the whole session immediately),
    the operator is asked whether to skip it and continue with the next
    sample, rather than the entire remaining queue being aborted.

    Returns a process exit code (0 success/expected-stop, 1 error,
    2 blocked by a pre-check, 130 stopped/cancelled).
    """

    run = initial_run
    transcript = SessionTranscript(run / "Logs" / "terminal_transcript.txt")
    transcript.start()

    motors: MotorController | None = None
    camera: CameraController | None = None
    try:
        mode = choose_mode_first()
        environment_ok = print_environment_report()
        dry_run = yes_no("Use dry-run mode?", default=not environment_ok)
        if not dry_run and not environment_ok:
            print("Required production dependencies are missing; non-dry operation is unsafe.")
            return 2

        camera_settings = CameraSettings()
        timing_settings = TimingSettings()
        stop_event = threading.Event()
        motors = MotorController(ACTIVE_MOTORS[mode], timing_settings, dry_run)
        camera = CameraController(camera_settings, dry_run)

        def request_stop(_signum, _frame) -> None:
            stop_event.set()
            motors.emergency_stop()
            camera.emergency_stop()

        def ask_camera_settings() -> tuple[float, float]:
            exposure_ms = ask_positive_float("Exposure time (ms)", camera_settings.exposure_us / 1000.0)
            frame_rate_fps = ask_positive_float("Frame rate (fps)", camera_settings.frame_rate_fps)
            print(
                f"Retrying with exposure {exposure_ms:.3f} ms, "
                f"frame rate {frame_rate_fps:.3f} fps."
            )
            return exposure_ms * 1000.0, frame_rate_fps

        signal.signal(signal.SIGINT, request_stop)

        # One-time bring-up, before any sample is known.
        detect_camera(camera)
        initialize_motors(motors, timing_settings)
        guided_camera_setup(dry_run, motors, camera, camera_settings, timing_settings)
        camera.initialize(ask_settings=ask_camera_settings)

        first_sample = True
        while True:
            metadata = ask_metadata()
            # The transcript's log file must be closed before the folder
            # containing it can be renamed — Windows/NTFS refuses to rename a
            # directory while any file inside it is still open, even by the
            # same process. Stopping first (for every sample, including the
            # first) and starting a new transcript right after is what makes
            # the rename safe; the log's content survives the rename either
            # way, so appending at the new path continues it seamlessly.
            transcript.stop()
            if first_sample:
                run = rename_run_directory(run, metadata.sample)
            else:
                run = create_run_directory(DATA_ROOT, metadata.sample)
            transcript = SessionTranscript(run / "Logs" / "terminal_transcript.txt")
            transcript.start()

            # Set/verify the sample's own orientation on the motorized SAMPLE
            # stage (if any) BEFORE the rest of this sample's setup, since the
            # sample must then be set aside for the empty-beam-path camera
            # reference capture below.
            sample_stage_optical_angle = setup_sample_stage(timing_settings, dry_run)

            fixed_angles, state_inputs, states = ask_angles_for_mode(mode)
            print(f"Total states: {len(states)}")
            config = ExperimentConfig(
                mode=mode,
                metadata=metadata,
                run_directory=run,
                dry_run=dry_run,
                fixed_angles=fixed_angles,
                state_inputs=state_inputs,
                camera=camera_settings,
                timing=timing_settings,
                sample_stage_optical_angle=sample_stage_optical_angle,
            )
            write_json(run / "Config" / "experiment_config.json", config.to_dict())

            if not check_disk_space(run, len(states), camera):
                return 2
            if not yes_no("Begin acquisition for this sample?"):
                print(f"Configuration retained at {run}")
                return 0

            if not first_sample:
                confirm_stage("Remove the previous sample so the beam path is empty, then confirm.")

            try:
                capture_camera_references(run, dry_run, motors, camera, camera_settings, timing_settings)
                confirm_stage(
                    "Reference and camera verification complete. Insert the sample now, "
                    "then confirm to start the measurement."
                )
                engine = MeasurementEngine(config, motors, camera, stop_event)
                completed, failed = engine.run_discrete(states)
                print(f"Experiment complete: {completed} images, {failed} failures.")
                print(f"Data directory: {run}")
                # Plain yes_no, not confirm_stage: declining just skips the
                # rehome for this sample, it should not cancel the whole
                # session the way every other confirm_stage() gate does.
                if yes_no("Rehome motors before disconnect? (ensure nothing will interfere with rotation)", default=True):
                    try:
                        motors.home_all()
                    except Exception as exc:
                        print(f"Post-measurement rehoming warning: {exc}")
                else:
                    print("Rehoming skipped by operator.")
            except (EmergencyStopRequested, KeyboardInterrupt):
                # An emergency stop/Ctrl-C always ends the WHOLE session, never
                # just this sample — propagate to the outer handler below.
                raise
            except Exception as exc:
                motors.emergency_stop()
                print(f"Sample failed: {type(exc).__name__}: {exc}")
                write_error_traceback(run)
                if not yes_no("This sample failed. Continue with another sample?", default=False):
                    return 1
                first_sample = False
                continue

            first_sample = False
            if not yes_no("Measure another sample?"):
                return 0
    except EmergencyStopRequested as exc:
        print(exc)
        return 130
    except KeyboardInterrupt:
        if motors is not None:
            motors.emergency_stop()
        print("Cancelled by operator.")
        return 130
    except Exception as exc:
        if motors is not None:
            motors.emergency_stop()
        print(f"Session aborted: {type(exc).__name__}: {exc}")
        write_error_traceback(run)
        return 1
    finally:
        if camera is not None:
            try:
                camera.close()
            except Exception as exc:
                print(f"Camera cleanup warning: {exc}")
        if motors is not None:
            motors.close()
        transcript.stop()


def main() -> int:
    """Process entry point (called from ``if __name__ == "__main__"`` at the
    bottom of this file). This is the ONLY function that should be invoked
    to start the program — see README "Which file should I run?".

    Dispatches to one of two self-contained session functions, each owning
    its own transcript/error-traceback/hardware-cleanup lifecycle:
      --resume RUN_DIRECTORY -> run_resumed_session(): recovers exactly the
        one saved experiment in RUN_DIRECTORY, no multi-sample loop.
      (no --resume) -> run_fresh_session(): a "pending" placeholder run
        directory is created first (before mode selection, so the
        transcript captures it), then run_fresh_session() renames it to the
        first sample's name and loops over as many samples as requested.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume",
        type=Path,
        metavar="RUN_DIRECTORY",
        help="resume an existing run from its checkpoint instead of creating a new run",
    )
    arguments = parser.parse_args()

    if arguments.resume:
        run = arguments.resume.resolve()
        config_path = run / "Config" / "experiment_config.json"
        if not config_path.is_file():
            parser.error(f"saved configuration does not exist: {config_path}")
        resumed_config = ExperimentConfig.from_dict(
            json.loads(config_path.read_text(encoding="utf-8"))
        )
        # The command-line path is authoritative if the folder was moved.
        resumed_config.run_directory = run

        transcript = SessionTranscript(run / "Logs" / "terminal_transcript.txt")
        transcript.start()
        try:
            return run_resumed_session(arguments, run, resumed_config)
        except KeyboardInterrupt:
            print("Session cancelled before hardware acquisition.")
            return 130
        except Exception as exc:
            print(f"Unhandled session error: {type(exc).__name__}: {exc}")
            write_error_traceback(run)
            return 1
        finally:
            transcript.stop()

    initial_run = create_run_directory(DATA_ROOT, "pending")
    return run_fresh_session(initial_run)


if __name__ == "__main__":
    raise SystemExit(main())
