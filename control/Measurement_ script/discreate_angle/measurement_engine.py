"""The ordered, checkpointed acquisition workflow."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from camera_controller import CameraController
from checkpoint_manager import CheckpointManager
from config import MOTOR_SN, ExperimentConfig
from logger_manager import ExperimentLogger, write_report
from motor_controller import MotorController
from state_generator import MeasurementState


class EmergencyStopRequested(RuntimeError):
    """Raised by _ensure_running() when the operator has pressed Ctrl-C
    (stop_event was set by the SIGINT handler in 01_main.py). Propagates up
    through run_discrete() and is caught in 01_main.run_session()."""

    pass


class MeasurementEngine:
    """Coordinate motors and camera while keeping each completed image auditable.

    Owns a CheckpointManager and an ExperimentLogger scoped to this run's
    directory, and drives the actual move->capture loop. Created once in
    01_main.run_session(), right before "Camera verification complete. Start
    the measurement?".
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
        """Check the shared stop_event before/after each risky step (a motor
        move, a camera trigger). If Ctrl-C was pressed, immediately issues
        emergency stops to both devices and raises EmergencyStopRequested so
        the current state is abandoned cleanly rather than left half-done."""

        if self.stop_event.is_set():
            self.motors.emergency_stop()
            self.camera.emergency_stop()
            raise EmergencyStopRequested("Acquisition stopped by operator.")

    def _reported_positions_safely(self) -> dict[str, float]:
        """Keep the original failure loggable even if a position read also fails.

        Used only in the except-branch of run_discrete(), so a broken
        encoder read never masks the real error being logged.
        """

        try:
            return self.motors.encoder_positions()
        except Exception as exc:
            print(f"Position read warning while logging failure: {exc}")
            return {}

    def run_discrete(self, states: list[MeasurementState]) -> tuple[int, int]:
        """Execute the required move -> settle -> trigger -> verify -> settle
        sequence for every state, in order, resuming after any already-
        checkpointed states.

        For each MeasurementState:
          1. _ensure_running() — abort early if a stop was requested.
          2. motors.move_state() — move every axis for this state (retries
             internally; see motor_controller.move_motor_angle).
          3. sleep(timing.settling_before_s) — let vibration die down.
          4. _ensure_running() again, then camera.acquire_save_verify() —
             trigger, save, and verify the image (retries internally).
          5. Log SUCCESS with reported positions to the CSV, via self.logger.
          6. self.checkpoints.update(state) — ONLY after the image is
             verified and logged, so a resumed run never re-does or skips a
             state ambiguously.
          7. sleep(timing.settling_after_s) before the next state.
        Any unrecoverable exception logs a FAILED row and re-raises,
        stopping the whole run (caught by 01_main.run_session()). On the way
        out (success, failure, or stop), always writes the final text report.
        Returns (completed_count, failed_count).
        """

        started = time.monotonic()
        failures = 0
        completed = 0
        start_index = self.checkpoints.next_index()
        if start_index:
            print(f"Resuming at state {start_index + 1}; earlier states are checkpointed.")

        try:
            for state in states[start_index:]:
                self._ensure_running()
                print(f"[{state.index + 1}/{len(states)}] Moving motors: {state.motor_angles}")
                image_path = self.config.run_directory / "Images" / state.filename
                attempt_count = 1
                try:
                    # Kinesis MoveTo is blocking, so return means every sequential
                    # move stopped and its reported position passed tolerance checks.
                    motor_attempts = self.motors.move_state(state.motor_angles)
                    time.sleep(self.config.timing.settling_before_s)
                    self._ensure_running()
                    camera_attempts = self.camera.acquire_save_verify(image_path)
                    attempt_count = max(motor_attempts, camera_attempts)
                    reported = self.motors.encoder_positions()
                    self.logger.log(state, reported, attempt_count, "SUCCESS")
                    # A checkpoint is advanced only after image decode and log persistence.
                    self.checkpoints.update(state)
                    completed += 1
                    print(f"Verified and saved: {state.filename}")
                except Exception as exc:
                    failures += 1
                    attempt_count = max(attempt_count, int(getattr(exc, "attempts", 1)))
                    self.logger.log(
                        state,
                        self._reported_positions_safely(),
                        attempt_count,
                        "FAILED",
                        f"{type(exc).__name__}: {exc}",
                    )
                    print(f"State failed: {state.filename}: {exc}")
                    raise
                finally:
                    time.sleep(self.config.timing.settling_after_s)

            self.checkpoints.complete(len(states))
            return completed, failures
        finally:
            elapsed = time.monotonic() - started
            write_report(
                self.config.run_directory / "Reports" / "ExperimentReport.txt",
                self.config,
                {name: MOTOR_SN[name] for name in self.motors.names},
                completed,
                failures,
                elapsed,
            )
