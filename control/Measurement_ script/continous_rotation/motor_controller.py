"""Thorlabs K10CR2/M control for 4x4 CONTINUOUS rotation, via Kinesis .NET.

Deliberate duplicate of discreate_angle/motor_controller.py's hardware
bring-up (discover/connect/initialize/enable/home/move-to-optical-zero) and
single-axis move_motor_angle (still needed here to park the two polarizers
at a fixed optical angle before the QWPs start spinning). What is NEW here
and has no equivalent in discreate_angle: set_velocity/start_continuous/
stop_continuous — the primitives a continuous engine needs instead of
move_state(). These do not depend on the still-open frame-rate-vs-angle
trigger decision (see continuous_engine.py) so they are safe to build now.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterable

from config import KINESIS_DIR, MOTOR_SETTINGS_NAME, MOTOR_SN, ZERO_OFFSET, TimingSettings


class MotorError(RuntimeError):
    """Raised when a motor cannot safely complete an operation.
    ``attempts`` records how many tries were actually made."""

    def __init__(self, message: str, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


def angular_error_deg(commanded: float, reported: float) -> float:
    """Shortest absolute distance between two circular (0-360) coordinates."""

    return abs((reported - commanded + 180.0) % 360.0 - 180.0)


class MotorController:
    """Own all active devices and expose named, optical-system operations
    plus continuous-rotation primitives. One instance per run, scoped to
    config.ACTIVE_MOTORS (always all four for this folder)."""

    def __init__(self, names: Iterable[str], timing: TimingSettings, dry_run: bool = False) -> None:
        self.names = tuple(names)
        self.timing = timing
        self.dry_run = dry_run
        self.devices: dict[str, object] = {}
        self._simulated_positions = {name: 0.0 for name in self.names}
        self._simulated_spinning: set[str] = set()  # dry-run only
        self._simulated_velocities: dict[str, float] = {}  # dry-run only, deg/s, set by set_velocity()
        self._simulated_spin_start: dict[str, tuple[float, float]] = {}  # dry-run only: name -> (monotonic time, angle at start_continuous())
        self._dll_directory = None

    def _load_kinesis(self) -> None:
        """Load Kinesis lazily, only once dry_run is False."""

        if str(KINESIS_DIR) not in sys.path:
            sys.path.append(str(KINESIS_DIR))
        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(KINESIS_DIR))
        import clr  # type: ignore

        for assembly in (
            "Thorlabs.MotionControl.DeviceManagerCLI.dll",
            "Thorlabs.MotionControl.GenericMotorCLI.dll",
            "Thorlabs.MotionControl.IntegratedStepperMotorsCLI.dll",
        ):
            clr.AddReference(str(KINESIS_DIR / assembly))
        from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI
        from Thorlabs.MotionControl.IntegratedStepperMotorsCLI import CageRotator
        # NOT YET VERIFIED against the lab PC's installed Kinesis .NET assembly —
        # confirm this is the correct namespace for the continuous-move direction
        # enum before running start_continuous() on real hardware.
        from Thorlabs.MotionControl.GenericMotorCLI.Settings import MotorDirection
        from System import Decimal

        self.DeviceManagerCLI = DeviceManagerCLI
        self.CageRotator = CageRotator
        self.MotorDirection = MotorDirection
        self.Decimal = Decimal

    def discover(self) -> list[str]:
        """List motors on USB and confirm every active MOTOR_SN is present."""

        if self.dry_run:
            serials = [MOTOR_SN[name] or f"SIM-{name}" for name in self.names]
        else:
            self._load_kinesis()
            self.DeviceManagerCLI.BuildDeviceList()
            serials = [str(item) for item in self.DeviceManagerCLI.GetDeviceList()]
        print("Connected motor serial numbers:", ", ".join(serials) or "(none)")
        if not self.dry_run:
            missing_configuration = [name for name in self.names if not MOTOR_SN[name]]
            if missing_configuration:
                raise MotorError(
                    "Missing configured serial numbers for: "
                    + ", ".join(missing_configuration)
                )
            missing_devices = [name for name in self.names if MOTOR_SN[name] not in serials]
            for name in self.names:
                status = "FOUND" if MOTOR_SN[name] in serials else "NOT FOUND"
                print(f"  {name} ({MOTOR_SN[name]}): {status}")
            if missing_devices:
                raise MotorError(
                    "Required motors are not visible on USB: " + ", ".join(missing_devices)
                )
        return serials

    def _inter_motor_pause(self, index: int) -> None:
        if index < len(self.names) - 1:
            time.sleep(self.timing.inter_motor_settle_s)

    def connect_all(self) -> None:
        """Connect sequentially; never initialize devices concurrently."""

        for index, name in enumerate(self.names):
            serial = MOTOR_SN[name]
            print(f"[{name}] Connecting...")
            if self.dry_run:
                self.devices[name] = object()
                print(f"[{name}] Connected (dry-run).")
                self._inter_motor_pause(index)
                continue
            if not serial:
                raise MotorError(f"No serial number configured for {name}.")
            device = self.CageRotator.CreateCageRotator(serial)
            device.Connect(serial)
            self.devices[name] = device
            print(f"[{name}] Connected ({serial}).")
            self._inter_motor_pause(index)
        print(f"All motors connected: {', '.join(self.names)}.")

    def initialize_all(self) -> None:
        """Load each motor's Kinesis device-settings profile and start
        position polling."""

        for index, (name, device) in enumerate(self.devices.items()):
            print(f"[{name}] Initializing settings...")
            if not self.dry_run:
                if not device.IsSettingsInitialized():
                    device.WaitForSettingsInitialized(10_000)
                configuration = device.LoadMotorConfiguration(MOTOR_SN[name])
                configuration.DeviceSettingsName = MOTOR_SETTINGS_NAME
                configuration.UpdateCurrentConfiguration()
                device.StartPolling(250)
                time.sleep(0.5)
            print(f"[{name}] Initialized.")
            self._inter_motor_pause(index)
        print("All motors initialized.")

    def enable_all(self) -> None:
        """Energize each motor's motion controller."""

        for index, (name, device) in enumerate(self.devices.items()):
            print(f"[{name}] Enabling...")
            if not self.dry_run:
                device.EnableDevice()
                time.sleep(self.timing.enable_settle_s)
            print(f"[{name}] Enabled.")
            self._inter_motor_pause(index)
        print("All motors enabled.")

    def home_all(self) -> None:
        """Run each motor's homing routine (absolute reference for the
        encoder). Required once per power cycle before any move is trustworthy."""

        for index, (name, device) in enumerate(self.devices.items()):
            print(f"[{name}] Homing...")
            if not self.dry_run:
                device.Home(self.timing.motor_timeout_ms)
            self._simulated_positions[name] = 0.0
            time.sleep(self.timing.homing_settle_s)
            print(f"[{name}] Homed and settled.")
            self._inter_motor_pause(index)
        print("All motors homed.")

    def move_to_optical_zero_all(self) -> None:
        """Move every active motor to its config.ZERO_OFFSET motor angle."""

        for index, name in enumerate(self.names):
            self.move_motor_angle(name, ZERO_OFFSET[name])
            print(f"[{name}] At optical zero (motor {ZERO_OFFSET[name]:.4f}°).")
            self._inter_motor_pause(index)
        print("All motors at optical zero.")

    def move_motor_angle(self, name: str, angle: float) -> int:
        """Move and verify one axis, retrying failures with a fixed backoff.

        Used here only for the two polarizers (parked at a fixed optical
        angle) and for moving a QWP to its starting angle before continuous
        rotation begins — never while that axis is spinning.
        """

        angle = float(angle) % 360.0
        if name not in self.devices:
            raise MotorError(f"{name} is not connected.")
        last_error: Exception | None = None
        total_attempts = self.timing.motor_max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                if not self.dry_run:
                    self.devices[name].MoveTo(self.Decimal(angle), self.timing.motor_timeout_ms)
                self._simulated_positions[name] = angle
                reported = self.encoder_positions()[name]
                error = angular_error_deg(angle, reported)
                if error > self.timing.position_tolerance_deg:
                    raise MotorError(
                        f"{name} reported {reported:.6f}° after command {angle:.6f}° "
                        f"(error {error:.6f}°, tolerance "
                        f"{self.timing.position_tolerance_deg:.6f}°)."
                    )
                return attempt
            except Exception as exc:
                last_error = exc
                print(f"{name} move attempt {attempt}/{total_attempts} failed: {exc}")
                if attempt < total_attempts:
                    time.sleep(self.timing.motor_retry_backoff_s)
        raise MotorError(
            f"{name} move failed after {total_attempts} attempts: {last_error}",
            attempts=total_attempts,
        )

    def set_velocity(self, name: str, max_velocity_deg_s: float, accel_deg_s2: float) -> None:
        """Set the velocity profile a subsequent start_continuous() will use.

        Real hardware: Kinesis exposes this as device.SetVelocityParams(
        Decimal(accel), Decimal(max_velocity)) on the CageRotator. Units are
        deg/s and deg/s^2 to match config.TimingSettings.
        """

        if name not in self.devices:
            raise MotorError(f"{name} is not connected.")
        print(f"[{name}] Velocity set: max {max_velocity_deg_s:.3f} deg/s, accel {accel_deg_s2:.3f} deg/s^2")
        if self.dry_run:
            self._simulated_velocities[name] = max_velocity_deg_s
            return
        self.devices[name].SetVelocityParams(self.Decimal(accel_deg_s2), self.Decimal(max_velocity_deg_s))

    def set_all_velocity(self, max_velocity_deg_s: float, accel_deg_s2: float) -> None:
        """Apply the same explicit velocity/acceleration to every active
        motor. Called from 01_main.initialize_motors(), after enable_all()
        and before home_all(), as a uniform baseline for point-to-point
        moves (homing, optical-zero, parking the polarizers) — set in
        software rather than left at whatever the device/Kinesis profile
        last stored. This is distinct from the per-sample, ratio-scaled
        velocity continuous_engine.py sets on PSA_QWP right before spinning
        starts (see that module's docstring, step 3)."""

        for index, name in enumerate(self.names):
            self.set_velocity(name, max_velocity_deg_s, accel_deg_s2)
            self._inter_motor_pause(index)
        print("All motors set to the configured rotation velocity.")

    def start_continuous(self, name: str, forward: bool = True) -> None:
        """Begin continuous rotation on one QWP axis (non-blocking).

        Real hardware: Kinesis CageRotator.MoveContinuous(MotorDirection.Forward
        or .Backward). Dry-run marks the axis as spinning and records the
        wall-clock start time/angle, so encoder_positions() can report a
        realistically advancing simulated angle (start angle + velocity ×
        elapsed time) instead of a frozen one — set_velocity() must be
        called first so a velocity is on record to advance at.
        """

        if name not in self.devices:
            raise MotorError(f"{name} is not connected.")
        print(f"[{name}] Starting continuous rotation ({'forward' if forward else 'backward'}).")
        if self.dry_run:
            self._simulated_spinning.add(name)
            self._simulated_spin_start[name] = (time.monotonic(), self._simulated_positions[name])
            return
        direction = self.MotorDirection.Forward if forward else self.MotorDirection.Backward
        self.devices[name].MoveContinuous(direction)

    def stop_continuous(self, name: str) -> None:
        """Stop continuous rotation on one axis (blocking until stopped)."""

        if name not in self.devices:
            raise MotorError(f"{name} is not connected.")
        print(f"[{name}] Stopping continuous rotation...")
        if self.dry_run:
            if name in self._simulated_spinning:
                self._simulated_positions[name] = self._dry_run_spinning_angle(name)
            self._simulated_spinning.discard(name)
            self._simulated_spin_start.pop(name, None)
            return
        self.devices[name].Stop(self.timing.motor_timeout_ms)

    def _dry_run_spinning_angle(self, name: str) -> float:
        """Current simulated angle for a spinning axis: start angle plus
        velocity × elapsed wall-clock time, wrapped to 0-360."""

        start_time, start_angle = self._simulated_spin_start[name]
        velocity = self._simulated_velocities.get(name, 0.0)
        elapsed = time.monotonic() - start_time
        return (start_angle + velocity * elapsed) % 360.0

    def encoder_positions(self) -> dict[str, float]:
        """Read back the current reported position of every connected motor."""

        positions: dict[str, float] = {}
        for name, device in self.devices.items():
            if self.dry_run and name in self._simulated_spinning:
                positions[name] = self._dry_run_spinning_angle(name)
                continue
            positions[name] = (
                self._simulated_positions[name] if self.dry_run else float(str(device.Position))
            )
        return positions

    def emergency_stop(self) -> None:
        """Best-effort immediate stop of every connected motor. Never raises."""

        print("EMERGENCY STOP: stopping all connected motors.")
        for name, device in self.devices.items():
            if not self.dry_run:
                try:
                    device.StopImmediate()
                except Exception as exc:
                    print(f"Motor stop warning: {exc}")
            self._simulated_spinning.discard(name)

    def close(self) -> None:
        """Stop polling and disconnect every device."""

        names = tuple(self.devices)
        for name, device in self.devices.items():
            if not self.dry_run:
                try:
                    device.StopPolling()
                    device.Disconnect()
                except Exception as exc:
                    print(f"Motor shutdown warning: {exc}")
            print(f"[{name}] Disconnected.")
        self.devices.clear()
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None
        print(f"All motors disconnected: {', '.join(names) or '(none were connected)'}.")
