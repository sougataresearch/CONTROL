"""Sequential Thorlabs K10CR2/M control through the Kinesis .NET API."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterable

from config import KINESIS_DIR, MOTOR_SETTINGS_NAME, MOTOR_SN, ZERO_OFFSET, TimingSettings


class MotorError(RuntimeError):
    """Raised when a motor cannot safely complete an operation.

    ``attempts`` records how many tries were actually made, so callers
    (measurement_engine) can log the true attempt count even on failure.
    """

    def __init__(self, message: str, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


def angular_error_deg(commanded: float, reported: float) -> float:
    """Return the shortest absolute distance between two circular coordinates.

    Needed because angles wrap at 360: naively subtracting 359 from 1 gives
    358, but the true mechanical distance is only 2 degrees. Used by
    move_motor_angle() to decide whether a move is within tolerance.
    """

    return abs((reported - commanded + 180.0) % 360.0 - 180.0)


class MotorController:
    """Own all active devices and expose only named, optical-system operations.

    One instance is created per run in 01_main.run_session(), scoped to only
    the motors relevant for the chosen mode (config.ACTIVE_MOTORS). All
    hardware calls are gated by self.dry_run — when True, moves are recorded
    in self._simulated_positions instead of touching real devices, so the
    exact same code path runs in both dry-run and real experiments.
    """

    def __init__(self, names: Iterable[str], timing: TimingSettings, dry_run: bool = False) -> None:
        self.names = tuple(names)  # active motor names for this run's mode, in fixed order
        self.timing = timing  # config.TimingSettings — all delays/retries/tolerances
        self.dry_run = dry_run
        self.devices: dict[str, object] = {}  # name -> live Kinesis CageRotator object
        self._simulated_positions = {name: 0.0 for name in self.names}  # dry-run only
        self._dll_directory = None  # keeps the add_dll_directory handle alive

    def _load_kinesis(self) -> None:
        """Load Kinesis lazily, after the user has selected non-dry operation.

        Importing pythonnet/clr and the Thorlabs .NET assemblies is deferred
        until this point specifically so dry-run mode never requires Kinesis
        to be installed. Called once, from discover(), only when dry_run is False.
        """

        if str(KINESIS_DIR) not in sys.path:
            sys.path.append(str(KINESIS_DIR))
        # Python 3.8+ no longer searches PATH implicitly for dependent DLLs.
        # Retain the handle for the controller lifetime or Windows removes it.
        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(KINESIS_DIR))
        import clr  # type: ignore

        # Explicit DLL paths are more robust than relying on the process PATH.
        for assembly in (
            "Thorlabs.MotionControl.DeviceManagerCLI.dll",
            "Thorlabs.MotionControl.GenericMotorCLI.dll",
            "Thorlabs.MotionControl.IntegratedStepperMotorsCLI.dll",
        ):
            clr.AddReference(str(KINESIS_DIR / assembly))
        from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI
        from Thorlabs.MotionControl.IntegratedStepperMotorsCLI import CageRotator
        from System import Decimal

        self.DeviceManagerCLI = DeviceManagerCLI
        self.CageRotator = CageRotator
        self.Decimal = Decimal

    def discover(self) -> list[str]:
        """List motors visible on USB and (for real runs) confirm every
        required MOTOR_SN is both configured and physically present.

        Dry-run: fabricates serials from config.MOTOR_SN (or "SIM-<name>" if
        blank) without touching hardware.
        Real run: loads Kinesis, calls BuildDeviceList()/GetDeviceList(), then
        raises MotorError if any active motor's serial is unset in config.py
        or not found on the USB bus. Called from 01_main.initialize_motors(),
        first step of hardware setup.
        """
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
        """Sleep between motors, but skip the pause after the last one."""

        if index < len(self.names) - 1:
            time.sleep(self.timing.inter_motor_settle_s)

    def connect_all(self) -> None:
        """Connect sequentially; never initialize devices concurrently.

        For each active motor: creates a CageRotator for its MOTOR_SN and
        calls Connect(). Dry-run stores a placeholder object() instead.
        Called from 01_main.initialize_motors(), after discover().
        """

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
        """Load each motor's Kinesis device-settings profile (config.MOTOR_SETTINGS_NAME
        must match the K10CR2 profile) and start position polling (250 ms).
        Called from 01_main.initialize_motors(), after connect_all()."""

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
        """Energize each motor's motion controller. Called from
        01_main.initialize_motors(), after initialize_all()."""

        for index, (name, device) in enumerate(self.devices.items()):
            print(f"[{name}] Enabling...")
            if not self.dry_run:
                device.EnableDevice()
                time.sleep(self.timing.enable_settle_s)
            print(f"[{name}] Enabled.")
            self._inter_motor_pause(index)
        print("All motors enabled.")

    def home_all(self) -> None:
        """Run each motor's homing routine (establishes the absolute zero
        reference the encoder counts from). Required once per power cycle
        before any MoveTo is trustworthy. Called from
        01_main.initialize_motors(), after enable_all()."""

        for index, (name, device) in enumerate(self.devices.items()):
            print(f"[{name}] Homing...")
            if not self.dry_run:
                device.Home(self.timing.motor_timeout_ms)
            self._simulated_positions[name] = 0.0
            time.sleep(self.timing.homing_settle_s)
            print(f"[{name}] Homed and settled.")
            self._inter_motor_pause(index)
        print("All motors homed.")

    def set_velocity(self, name: str, max_velocity_deg_s: float, accel_deg_s2: float) -> None:
        """Explicitly set one motor's velocity profile in software.

        Real hardware: Kinesis exposes this as device.SetVelocityParams(
        Decimal(accel), Decimal(max_velocity)) on the CageRotator — this is
        the same trapezoidal profile MoveTo() uses for every point-to-point
        move, so setting it here (rather than leaving whatever was last
        stored on the device/Kinesis profile) is what makes every
        move_motor_angle() call use a known, reproducible speed.
        """

        if name not in self.devices:
            raise MotorError(f"{name} is not connected.")
        print(f"[{name}] Velocity set: max {max_velocity_deg_s:.3f} deg/s, accel {accel_deg_s2:.3f} deg/s^2")
        if self.dry_run:
            return
        self.devices[name].SetVelocityParams(self.Decimal(accel_deg_s2), self.Decimal(max_velocity_deg_s))

    def set_all_velocity(self, max_velocity_deg_s: float, accel_deg_s2: float) -> None:
        """Apply the same explicit velocity profile to every active motor.
        Called from 01_main.initialize_motors(), after enable_all() and
        before home_all(), so homing and every subsequent move already use
        the configured speed rather than a device default."""

        for index, name in enumerate(self.names):
            self.set_velocity(name, max_velocity_deg_s, accel_deg_s2)
            self._inter_motor_pause(index)
        print("All motors set to the configured rotation velocity.")

    def move_to_optical_zero_all(self) -> None:
        """Move every active motor to its config.ZERO_OFFSET motor angle,
        i.e. optical 0 degrees. Called from 01_main.initialize_motors(),
        the last hardware-initialization step before camera setup."""

        for index, name in enumerate(self.names):
            self.move_motor_angle(name, ZERO_OFFSET[name])
            print(f"[{name}] At optical zero (motor {ZERO_OFFSET[name]:.4f}°).")
            self._inter_motor_pause(index)
        print("All motors at optical zero.")

    def move_motor_angle(self, name: str, angle: float) -> int:
        """Move and verify one axis, retrying failures with a fixed backoff.

        ``angle`` is a MOTOR angle (already offset-corrected), not an optical
        angle. After each MoveTo, reads the encoder back and compares it to
        the commanded angle with angular_error_deg(); if the error exceeds
        config.TimingSettings.position_tolerance_deg, treats it as a failed
        attempt and retries (up to motor_max_retries extra times, sleeping
        motor_retry_backoff_s between tries) before raising MotorError.
        Returns the attempt number that succeeded (1 = first try, no retries
        needed). This is the single choke point all motor motion in the
        project goes through (move_to_optical_zero_all, move_state,
        01_main.move_analyzer_to_optical, calibration.py all call it).
        """

        angle = float(angle) % 360.0
        if name not in self.devices:
            raise MotorError(f"{name} is not connected.")
        last_error: Exception | None = None
        total_attempts = self.timing.motor_max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                if not self.dry_run:
                    # System.Decimal is the numeric type required by Kinesis MoveTo.
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

    def move_state(self, motor_angles: dict[str, float]) -> int:
        """Move each requested axis in deterministic optical-train order
        (self.names order, e.g. PSG_Polarizer before PSG_QWP before PSA_QWP
        before PSA_Analyzer), never in parallel. Returns the worst-case
        (maximum) attempt count across all axes moved, for logging. Called
        once per MeasurementState from measurement_engine.run_discrete()."""

        maximum_attempts = 1
        for name in self.names:
            if name in motor_angles:
                maximum_attempts = max(maximum_attempts, self.move_motor_angle(name, motor_angles[name]))
        return maximum_attempts

    def encoder_positions(self) -> dict[str, float]:
        """Read back the current reported position of every connected motor
        (from the simulated dict in dry-run, or device.Position for real
        hardware). Used for tolerance checks and for the CSV log's "Reported
        Motor Positions" column."""

        positions: dict[str, float] = {}
        for name, device in self.devices.items():
            positions[name] = (
                self._simulated_positions[name] if self.dry_run else float(str(device.Position))
            )
        return positions

    def emergency_stop(self) -> None:
        """Best-effort immediate stop of every connected motor (StopImmediate).
        Called from the SIGINT handler in 01_main.py and from
        measurement_engine on a stop-event. Never raises — logs a warning
        and continues if a device fails to respond."""

        print("EMERGENCY STOP: stopping all connected motors.")
        for device in self.devices.values():
            if not self.dry_run:
                try:
                    device.StopImmediate()
                except Exception as exc:
                    print(f"Motor stop warning: {exc}")

    def close(self) -> None:
        """Stop polling and disconnect every device, and release the DLL
        directory handle. Called from the ``finally`` block in
        01_main.run_session() so it always runs, even after an error."""

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
