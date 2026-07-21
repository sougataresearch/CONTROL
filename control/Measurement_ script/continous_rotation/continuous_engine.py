"""The continuous-rotation acquisition loop: angle-triggered capture.

Decision made: ANGLE-triggered capture, not frame-rate free-run. Reasoning
(see the project discussion this module used to document as an open
question): the Mueller matrix reconstruction for this dual-rotating-QWP
polarimeter (matrix/own_code/CONTINOUS/4x4) needs images at KNOWN,
evenly-spaced angles over one full PSG_QWP revolution. Angle-triggered
capture — poll PSG_QWP's encoder and fire the camera every time it crosses
a fixed angular step (config.TimingSettings.capture_angle_step_deg,
default 1.0 deg -> 360 images/revolution) — guarantees that directly,
regardless of any velocity ripple from real hardware (acceleration
jitter, encoder noise). Frame-rate free-run would only give evenly-spaced
angles if the motor's velocity were perfectly constant, which K10CR2
hardware is not, and would still require logging the real encoder angle
per frame to correct for that — so angle-triggered is both simpler and
more directly correct here.

Each frame's file name and the per-frame CSV log record the ACTUAL polled
PSG_QWP/PSA_QWP angles at capture, not the nominal threshold — see
image_loader.py in the reconstruction folder for the filename convention
this produces.
"""

from __future__ import annotations

import threading
import time

from camera_controller import CameraController, CameraError
from checkpoint_manager import CheckpointManager
from config import MOTOR_SN, ExperimentConfig, ROTATING_MOTORS
from logger_manager import ExperimentLogger, write_report
from motor_controller import MotorController


class EmergencyStopRequested(RuntimeError):
    """Raised when the operator's Ctrl-C stop event is detected mid-run.
    Mirrors discreate_angle/measurement_engine.EmergencyStopRequested."""


class ContinuousEngine:
    """Owns the continuous-rotation acquisition loop.

    Constructed the same way as discreate_angle's MeasurementEngine so that
    01_main.py's hardware bring-up and cleanup code is structurally similar
    across both folders, even though nothing here shares an import.
    """

    def __init__(
        self,
        config: ExperimentConfig,
        motors: MotorController,
        camera: CameraController,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.motors = motors
        self.camera = camera
        self.stop_event = stop_event or threading.Event()
        self.checkpoints = CheckpointManager(config.run_directory / "Checkpoints" / "checkpoint.json")
        self.logger = ExperimentLogger(config.run_directory / "Logs" / "experiment_log.csv")

    def _ensure_running(self) -> None:
        """Check the shared stop_event before each risky step. If Ctrl-C was
        pressed, immediately issue emergency stops to both devices and raise
        EmergencyStopRequested so the run is abandoned cleanly rather than
        left half-done. Mirrors discreate_angle/measurement_engine.py."""

        if self.stop_event.is_set():
            self.motors.emergency_stop()
            self.camera.emergency_stop()
            raise EmergencyStopRequested("Acquisition stopped by operator.")

    def run_continuous(self) -> tuple[int, int]:
        """Spin PSG_QWP/PSA_QWP through one PSG_QWP revolution, capturing a
        frame every capture_angle_step_deg of PSG_QWP travel.

        Sequence:
          1. Move both QWPs to a known starting angle (0) — point-to-point,
             using whatever velocity initialize_motors() already set.
          2. Set the continuous-spin velocity: PSG_QWP at
             timing.base_angular_velocity_deg_s, PSA_QWP at that times the
             configured rotation ratio (e.g. 5x for a 1:5 ratio).
          3. Start continuous rotation on both QWPs (PSG_QWP's direction
             defines "forward" for the revolution-completion check).
          4. Poll PSG_QWP's position; every time cumulative travel crosses
             the next capture_angle_step_deg threshold, trigger the camera
             (angle-triggered — see module docstring), log the frame, and
             checkpoint it.
          5. Stop once PSG_QWP has traveled a full 360 deg (within
             revolution_tolerance_deg) AND the last threshold has been
             captured, then stop_continuous() on both QWPs.
        Returns (completed_frame_count, failed_frame_count). Any
        unrecoverable exception (other than a real capture failure, which
        is logged and counted as a per-frame failure) propagates up to
        01_main.py's per-sample error handling, same as discrete mode.
        """

        started = time.monotonic()
        timing = self.config.timing
        slow_ratio, fast_ratio = self.config.rotation_ratio
        step_deg = timing.capture_angle_step_deg
        images_dir = self.config.run_directory / "Images"
        images_dir.mkdir(parents=True, exist_ok=True)

        self._ensure_running()
        print("Moving PSG_QWP/PSA_QWP to their starting angle (0 deg) before spinning.")
        self.motors.move_motor_angle("PSG_QWP", 0.0)
        self.motors.move_motor_angle("PSA_QWP", 0.0)

        fast_velocity = timing.base_angular_velocity_deg_s * (fast_ratio / slow_ratio)
        print(
            f"Setting spin velocity: PSG_QWP {timing.base_angular_velocity_deg_s:.3f} deg/s, "
            f"PSA_QWP {fast_velocity:.3f} deg/s (ratio {slow_ratio}:{fast_ratio})."
        )
        self.motors.set_velocity("PSG_QWP", timing.base_angular_velocity_deg_s, timing.rotation_accel_deg_s2)
        self.motors.set_velocity("PSA_QWP", fast_velocity, timing.rotation_accel_deg_s2)

        self._ensure_running()
        start_angle = self.motors.encoder_positions()["PSG_QWP"]
        for name in ROTATING_MOTORS:
            self.motors.start_continuous(name, forward=True)

        frame_index = 0
        completed = 0
        failed = 0
        next_threshold_deg = 0.0
        total_frames = int(round(360.0 / step_deg))

        try:
            while True:
                self._ensure_running()
                positions = self.motors.encoder_positions()
                psg_angle = positions["PSG_QWP"]
                psa_angle = positions["PSA_QWP"]
                traveled_deg = (psg_angle - start_angle) % 360.0
                # total_frames thresholds (0, step, 2*step, ... < 360) are all
                # captured within the first lap, so this stop condition is
                # reached before traveled_deg would ever wrap back near 0.
                if frame_index >= total_frames:
                    break

                if traveled_deg >= next_threshold_deg:
                    image_path = images_dir / (
                        f"frame_{frame_index:04d}_psg{psg_angle:.1f}_psa{psa_angle:.1f}.bmp"
                    )
                    try:
                        attempts = self.camera.acquire_save_verify(image_path)
                        self.logger.log(frame_index, psg_angle, psa_angle, attempts, "SUCCESS")
                        self.checkpoints.record_frame(frame_index, psg_angle, psa_angle)
                        completed += 1
                        print(f"[{frame_index + 1}/{total_frames}] Captured {image_path.name}")
                    except CameraError as exc:
                        failed += 1
                        self.logger.log(
                            frame_index, psg_angle, psa_angle, exc.attempts, "FAILED", str(exc)
                        )
                        print(f"[{frame_index + 1}/{total_frames}] Frame failed: {exc}")
                    frame_index += 1
                    next_threshold_deg += step_deg

                time.sleep(timing.position_poll_interval_s)

            self.checkpoints.complete(frame_index)
            return completed, failed
        finally:
            for name in ROTATING_MOTORS:
                try:
                    self.motors.stop_continuous(name)
                except Exception as exc:
                    print(f"Warning: could not stop {name} cleanly: {exc}")
            elapsed = time.monotonic() - started
            write_report(
                self.config.run_directory / "Reports" / "ExperimentReport.txt",
                self.config,
                {name: MOTOR_SN[name] for name in self.motors.names},
                completed,
                failed,
                elapsed,
            )
