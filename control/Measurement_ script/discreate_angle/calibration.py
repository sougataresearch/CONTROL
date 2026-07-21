"""Small, explicit calibration and verification utilities."""

from __future__ import annotations

from collections.abc import Iterable

from config import TimingSettings, ZERO_OFFSET
from motor_controller import MotorController
from utils import optical_to_motor


def move_to_calibration_zero(motors: MotorController, name: str, offset: float) -> None:
    """Move one motor to a candidate optical-zero position so the operator
    can visually/optically confirm it (e.g. with a polarimeter or by eye
    through crossed polarizers).

    Move to a candidate offset; configuration remains a deliberate manual edit.
    This function only moves the motor and prints the value — it never writes
    to config.py. Once the operator visually confirms ``offset`` really is
    optical zero, they must manually copy it into config.ZERO_OFFSET[name].
    Not called anywhere in 01_main.py; intended for ad-hoc use (e.g. from a
    Python REPL or a short standalone script) during initial calibration.
    """

    motors.move_motor_angle(name, offset)
    print(f"{name}: motor {offset % 360:.6f}° is the candidate optical zero.")


def verification_scan(
    motors: MotorController,
    name: str,
    optical_angles: Iterable[float],
    offset: float,
) -> list[tuple[float, float]]:
    """Visit calibration points and return requested versus encoder coordinates.

    For each optical angle in ``optical_angles``, converts it to a motor
    angle with ``offset`` (normally config.ZERO_OFFSET[name]), moves there,
    and records (optical_angle, actual_encoder_reading). Use the returned
    list to check that the motor tracks the expected optical angle across
    the full range — large discrepancies indicate a bad ZERO_OFFSET or
    mechanical backlash. Also not called from 01_main.py; a calibration/
    verification helper for ad-hoc use.
    """

    readings = []
    for optical in optical_angles:
        motors.move_motor_angle(name, optical_to_motor(optical, offset))
        readings.append((optical, motors.encoder_positions()[name]))
    return readings


def verify_with_reference_sample(
    timing: TimingSettings, dry_run: bool, target_optical_angle: float
) -> float:
    """Move the motorized SAMPLE stage to a known reference optic's angle.

    The "sample" here is a KNOWN reference optic (e.g. a linear polarizer or
    QWP at a documented angle) mounted on its own motor, used to validate
    the whole measured Mueller matrix against the reference optic's known
    theoretical matrix. This is entirely separate from a normal experiment's
    unknown specimen, which is inserted by hand (see 01_main.py's "Insert
    the sample now" prompt) — SAMPLE is not in ACTIVE_MOTORS and this
    function is never called from 01_main.py.

    Builds and tears down its own single-axis MotorController rather than
    reusing an experiment's MotorController, since MotorController.names is
    fixed at construction to whatever config.ACTIVE_MOTORS[mode] chose, and
    SAMPLE is never one of those.
    """

    sample_motor = MotorController(("SAMPLE",), timing, dry_run)
    sample_motor.discover()
    sample_motor.connect_all()
    sample_motor.initialize_all()
    sample_motor.enable_all()
    sample_motor.home_all()
    motor_angle = optical_to_motor(target_optical_angle, ZERO_OFFSET["SAMPLE"])
    sample_motor.move_motor_angle("SAMPLE", motor_angle)
    print(f"SAMPLE reference optic at optical {target_optical_angle:.3f}° (motor {motor_angle:.3f}°).")
    sample_motor.close()
    return motor_angle
