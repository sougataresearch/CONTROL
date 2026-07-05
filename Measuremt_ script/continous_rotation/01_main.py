"""Interactive entry point for 4x4 CONTINUOUS rotation.

Deliberate duplicate of discreate_angle/01_main.py's orchestration shape
(hardware bring-up once, then a multi-sample loop, transcript-per-sample,
error handling) but for continuous rotation only — there is no 3x3/4x4
mode choice here, this folder only ever runs one experiment shape. The one
thing this file CANNOT do yet is actually spin the QWPs and capture frames:
that is continuous_engine.ContinuousEngine.run_continuous(), which raises
NotImplementedError until the frame-rate-vs-angle trigger decision is made
(see that module's docstring). Everything up to that point — environment
checks, hardware bring-up, camera verification, plan/config persistence —
is real and runs today, including in dry-run mode. There is no --resume:
continuous rotation is a single uninterrupted revolution, not a resumable
state list (see checkpoint_manager.py).
"""

from __future__ import annotations

import shutil
import signal
import threading
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
from continuous_engine import ContinuousEngine, EmergencyStopRequested
from logger_manager import SessionTranscript
from motor_controller import MotorController
from rotation_plan import continuous_plan
from utils import (
    check_environment,
    create_run_directory,
    optical_to_motor,
    parse_ratio,
    rename_run_directory,
    write_json,
    yes_no,
)


def ask_float(prompt: str) -> float:
    while True:
        try:
            return float(input(prompt).strip())
        except ValueError:
            print("Enter a numeric angle.")


def ask_positive_float(prompt: str, default: float) -> float:
    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        try:
            value = default if not text else float(text)
            if value <= 0:
                raise ValueError
            return value
        except ValueError:
            print("Enter a number greater than zero.")


def print_environment_report() -> bool:
    print("\nEnvironment verification")
    all_ok = True
    for name, passed, detail in check_environment():
        print(f"  {'OK' if passed else 'MISSING':7} {name}: {detail}")
        all_ok &= passed
    return all_ok


def confirm_stage(text: str) -> None:
    """Ask a yes/no confirmation before a safety-sensitive step; "no"
    cancels the whole session (KeyboardInterrupt, caught in run_fresh_session())."""

    if not yes_no(text):
        raise KeyboardInterrupt("Operator cancelled initialization.")


def ask_metadata() -> ExperimentMetadata:
    """Ask operator/sample/comments. The sample name doubles as this
    round's run-folder name (see utils.create_run_directory()/
    rename_run_directory())."""

    return ExperimentMetadata(
        operator=input("Operator Name: ").strip(),
        sample=input("Sample Name: ").strip(),
        comments=input("Comments: ").strip(),
    )


def ask_fixed_and_ratio() -> tuple[dict[str, float], tuple[int, int]]:
    """Ask the two fixed polarizer angles and the QWP rotation ratio for
    one sample. Always 4x4 continuous — no mode choice."""

    fixed = {
        "PSG_Polarizer": ask_float("Fixed PSG Polarizer optical angle: ") % 360,
        "PSA_Analyzer": ask_float("Fixed PSA Analyzer optical angle: ") % 360,
    }
    while True:
        try:
            ratio = parse_ratio(input("QWP rotation ratio, slow:fast (e.g. 1:5): "))
            break
        except (ValueError, IndexError) as exc:
            print(f"Invalid ratio: {exc}")
    print(f"Fixed angles: {fixed}")
    print(f"Rotation ratio (PSG_QWP:PSA_QWP): {ratio}")
    return fixed, ratio


def initialize_motors(motors: MotorController, timing: TimingSettings) -> None:
    """discover -> connect_all -> initialize_all -> enable_all -> (ask +
    set velocity) -> home_all -> move_to_optical_zero_all, each behind a
    confirm_stage. Called once per session (not per sample — see
    run_fresh_session()).

    The velocity/acceleration prompt is pre-filled with config.py's
    base_angular_velocity_deg_s/rotation_accel_deg_s2 (press Enter to
    accept, or type a different number for this session) — applied as a
    uniform baseline for point-to-point moves (homing, optical-zero,
    parking the polarizers), set explicitly in software rather than left at
    whatever the device/Kinesis profile last stored. PSA_QWP is later
    re-set to this baseline times the chosen rotation ratio, per sample,
    right before continuous spinning starts (see continuous_engine.py's
    docstring, step 3)."""

    motors.discover()
    confirm_stage("Continue with the listed devices?")
    motors.connect_all()
    confirm_stage("All active motors connected. Initialize them sequentially?")
    motors.initialize_all()
    confirm_stage("Initialization complete. Enable motors?")
    motors.enable_all()
    print("Motors enabled.")
    velocity = ask_positive_float(
        "Rotation velocity for all active motors (deg/s)", timing.base_angular_velocity_deg_s
    )
    accel = ask_positive_float(
        "Rotation acceleration for all active motors (deg/s^2)", timing.rotation_accel_deg_s2
    )
    motors.set_all_velocity(velocity, accel)
    # Persist the operator's answer back into timing so continuous_engine.py
    # later re-sets PSA_QWP's spin velocity from the SAME base rate the
    # operator just chose here, rather than silently falling back to
    # whatever config.py's default still says.
    timing.base_angular_velocity_deg_s = velocity
    timing.rotation_accel_deg_s2 = accel
    confirm_stage("Velocity set. Home active motors?")
    motors.home_all()
    confirm_stage("Homing complete. Move to configured optical zero offsets?")
    motors.move_to_optical_zero_all()


def setup_sample_stage(timing: TimingSettings, dry_run: bool) -> float | None:
    """Optionally bring up the motorized SAMPLE stage for THIS sample and set
    its optical angle, before the rest of this sample's setup.

    Deliberate duplicate of discreate_angle/01_main.py's
    setup_sample_stage() — same reasoning: run the SAME bring-up sequence as
    initialize_motors() (discover -> connect -> initialize -> enable ->
    ask + set velocity -> home -> move to optical zero) but scoped to just
    "SAMPLE" — moving to optical zero right after homing is a sanity
    checkpoint that the configured offset is loading correctly, before
    asking for the real target angle. Then ask the target optical angle,
    move there via optical_to_motor(angle, ZERO_OFFSET["SAMPLE"]) so the
    operator can verify the orientation with a polarimeter, then disconnect
    the stage
    again so the operator can set the sample aside for the empty-beam-path
    camera reference capture, reinserting it at the existing "insert the
    sample now" prompt right before continuous rotation starts.

    Returns the chosen optical angle, or None if this sample has no
    motorized SAMPLE stage.
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
        "SAMPLE stage rotation velocity (deg/s)", timing.base_angular_velocity_deg_s
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


def park_fixed_polarizers(motors: MotorController, fixed_angles: dict[str, float]) -> None:
    """Move PSG_Polarizer/PSA_Analyzer to this sample's fixed optical angle.
    They never move again for the rest of this sample's run — only the two
    QWPs rotate. Called per sample (fixed angles can differ sample to
    sample), unlike initialize_motors()."""

    confirm_stage(
        f"Park PSG_Polarizer at optical {fixed_angles['PSG_Polarizer']:.3f}° and "
        f"PSA_Analyzer at optical {fixed_angles['PSA_Analyzer']:.3f}°?"
    )
    motors.move_motor_angle(
        "PSG_Polarizer", optical_to_motor(fixed_angles["PSG_Polarizer"], ZERO_OFFSET["PSG_Polarizer"])
    )
    motors.move_motor_angle(
        "PSA_Analyzer", optical_to_motor(fixed_angles["PSA_Analyzer"], ZERO_OFFSET["PSA_Analyzer"])
    )
    print("Polarizers parked at their fixed optical angle for this sample.")


def detect_camera(camera: CameraController) -> None:
    """Probe the camera and confirm it before any motor step runs — same
    fail-fast ordering as discreate_angle's detect_camera()."""

    camera.discover()
    confirm_stage("Camera detection succeeded. Continue with hardware initialization?")


def guided_camera_setup(
    dry_run: bool,
    camera_settings: CameraSettings,
) -> None:
    """Cockpit checks while Python has released the camera. Called once per
    session (not per sample). Only one manual Cockpit check here since
    exposure/frame rate selection is the only thing that needs a human
    look — the automatic bright/dark reference pair happens afterward, per
    sample, in capture_camera_references()."""

    if dry_run:
        print(
            "Dry-run mode: IDS Peak Cockpit checks are simulated and saved camera "
            "defaults are retained."
        )
        return

    confirm_stage("Turn ON the illumination/light source. Is it on?")
    print("\nCAMERA CHECK — FIXED-ANGLE STATE")
    input(
        "Open IDS Peak Cockpit, confirm the image looks as expected at the fixed "
        "polarizer angles, then CLOSE Cockpit and press Enter..."
    )
    confirm_stage("Is IDS Peak Cockpit fully closed?")

    print("\nSELECT EXPERIMENT SETTINGS")
    input(
        "Open IDS Peak Cockpit, choose exposure time and frame rate, write down "
        "both values, then CLOSE Cockpit and press Enter..."
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
    fixed_angles: dict[str, float],
    motors: MotorController,
    camera: CameraController,
    camera_settings: CameraSettings,
) -> None:
    """Capture quantitative bright/dark references without modifying pixels.

    Called for EVERY sample, not just once — the operator may have bumped
    the setup while swapping samples by hand. Moves PSA_Analyzer briefly
    off its fixed angle (+90°) for the dark shot, then back — this happens
    before continuous rotation starts, so it does not conflict with "the
    analyzer never moves during acquisition." Bright/dark ratio is computed
    over an automatically-selected ROI (see camera_controller.select_roi()),
    same approach and reasoning as discreate_angle/01_main.py's
    capture_camera_references().
    """

    reference_dir = run_directory / "Results"
    fixed_psg = fixed_angles["PSG_Polarizer"]
    fixed_psa = fixed_angles["PSA_Analyzer"]

    if dry_run:
        print(f"Capturing bright reference at fixed PSG={fixed_psg}°, PSA={fixed_psa}°...")
        camera.test_frame(reference_dir / "BrightReference_fixed.bmp")
        motors.move_motor_angle("PSA_Analyzer", optical_to_motor((fixed_psa + 90) % 360, ZERO_OFFSET["PSA_Analyzer"]))
        print("Capturing dark reference at fixed PSG, PSA+90°...")
        camera.test_frame(reference_dir / "DarkReference_fixed_plus90.bmp")
        motors.move_motor_angle("PSA_Analyzer", optical_to_motor(fixed_psa, ZERO_OFFSET["PSA_Analyzer"]))
        print(
            "Dry-run mode: reference files and statistics were verified, but "
            "physical polarization contrast cannot be evaluated (no ROI selected)."
        )
        return

    confirm_stage(
        "Illumination is ON? (required before the automatic bright/dark reference capture)"
    )

    print(f"Capturing bright reference at fixed PSG={fixed_psg}°, PSA={fixed_psa}°...")
    bright = camera.test_frame(reference_dir / "BrightReference_fixed.bmp")
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

    motors.move_motor_angle("PSA_Analyzer", optical_to_motor((fixed_psa + 90) % 360, ZERO_OFFSET["PSA_Analyzer"]))
    print("Capturing dark reference at fixed PSG, PSA+90°...")
    dark = camera.test_frame(reference_dir / "DarkReference_fixed_plus90.bmp")
    dark_mean = roi_mean(camera.last_image_array, roi)
    motors.move_motor_angle("PSA_Analyzer", optical_to_motor(fixed_psa, ZERO_OFFSET["PSA_Analyzer"]))

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
    details = traceback.format_exc()
    path = run / "Logs" / "error_traceback.txt"
    path.write_text(details, encoding="utf-8")
    print(f"Full error traceback saved to: {path}")
    print(details)


def run_fresh_session(initial_run: Path) -> int:
    """Multi-sample session: hardware bring-up once, then loop over as many
    samples as the operator wants, each getting its own
    Data/YYYY-MM-DD_<sample name> folder.

    Deliberate duplicate of discreate_angle/01_main.py's run_fresh_session()
    shape, minus mode selection (always 4x4 continuous) and the disk-space
    check (continuous has no fixed image count decided ahead of time).
    Owns its own transcript lifecycle for the same reason: each new sample
    gets a fresh run folder, and therefore a fresh
    Logs/terminal_transcript.txt. The transcript is stopped before every
    rename/create and a new one started right after — Windows/NTFS refuses
    to rename a directory while a file inside it (the log itself) is still
    open, even by the same process. The very first sample renames
    ``initial_run`` (a "pending" placeholder main() created before hardware
    bring-up) in place; every later sample gets a genuinely new folder.

    A NotImplementedError from the (currently unbuilt) acquisition engine
    ends the WHOLE session immediately, same as before the multi-sample
    loop existed — it is a structural "this isn't built yet" condition that
    would recur identically for every sample, not a per-sample hardware
    fault. A real failure (MotorError/CameraError/etc.) instead asks
    whether to skip that sample and continue with the next one.
    """

    run = initial_run
    transcript = SessionTranscript(run / "Logs" / "terminal_transcript.txt")
    transcript.start()

    motors: MotorController | None = None
    camera: CameraController | None = None
    try:
        environment_ok = print_environment_report()
        dry_run = yes_no("Use dry-run mode?", default=not environment_ok)
        if not dry_run and not environment_ok:
            print("Required production dependencies are missing; non-dry operation is unsafe.")
            return 2

        camera_settings = CameraSettings()
        timing_settings = TimingSettings()
        stop_event = threading.Event()
        motors = MotorController(ACTIVE_MOTORS, timing_settings, dry_run)
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
        guided_camera_setup(dry_run, camera_settings)
        camera.initialize(ask_settings=ask_camera_settings)

        first_sample = True
        while True:
            metadata = ask_metadata()
            transcript.stop()
            if first_sample:
                run = rename_run_directory(run, metadata.sample)
            else:
                run = create_run_directory(DATA_ROOT, metadata.sample)
            transcript = SessionTranscript(run / "Logs" / "terminal_transcript.txt")
            transcript.start()

            # Set/verify the sample's own orientation on the motorized SAMPLE
            # stage (if any) before the rest of this sample's setup, since the
            # sample must then be set aside for the empty-beam-path camera
            # reference capture below.
            sample_stage_optical_angle = setup_sample_stage(timing_settings, dry_run)

            fixed_angles, ratio = ask_fixed_and_ratio()
            config = ExperimentConfig(
                metadata=metadata,
                run_directory=run,
                dry_run=dry_run,
                fixed_angles=fixed_angles,
                rotation_ratio=ratio,
                camera=camera_settings,
                timing=timing_settings,
                sample_stage_optical_angle=sample_stage_optical_angle,
            )
            write_json(run / "Config" / "rotation_plan.json", continuous_plan(ratio, fixed_angles))
            write_json(run / "Config" / "experiment_config.json", config.to_dict())

            free = shutil.disk_usage(run).free
            print(f"Free disk space: {free / 1024**3:.2f} GB")
            if not yes_no("Begin acquisition for this sample?"):
                print(f"Configuration retained at {run}")
                return 0

            if not first_sample:
                confirm_stage("Remove the previous sample so the beam path is empty, then confirm.")

            try:
                park_fixed_polarizers(motors, fixed_angles)
                capture_camera_references(run, dry_run, fixed_angles, motors, camera, camera_settings)
                confirm_stage(
                    "Reference and camera verification complete. Insert the sample now, "
                    "then confirm to start continuous rotation."
                )
                engine = ContinuousEngine(config, motors, camera, stop_event)
                completed, failed = engine.run_continuous()
                print(f"Continuous run complete: {completed} frames, {failed} failures.")
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
            except NotImplementedError as exc:
                print(f"Continuous acquisition not started: {exc}")
                return 0
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
    """Process entry point. There is no --resume: continuous rotation is a
    single uninterrupted revolution, not a resumable state list. A "pending"
    placeholder run directory is created first (before hardware bring-up, so
    the transcript captures it), then run_fresh_session() renames it to the
    first sample's name and loops over as many samples as requested."""

    initial_run = create_run_directory(DATA_ROOT, "pending")
    return run_fresh_session(initial_run)


if __name__ == "__main__":
    raise SystemExit(main())
