"""Generate deterministic measurement states from optical coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from config import ZERO_OFFSET
from utils import format_angle, optical_to_motor


@dataclass(frozen=True, slots=True)
class MeasurementState:
    """One planned motor-move + image-capture step. Immutable (frozen) so a
    generated plan can't be accidentally mutated mid-run.

    index          — 0-based position in the states list; also what
                      checkpoint_manager compares against to know where to resume.
    optical_angles — {motor_name: optical_angle} the operator asked for.
    motor_angles   — {motor_name: motor_angle}, i.e. optical_angles already
                      converted with optical_to_motor() using ZERO_OFFSET.
                      This is what motor_controller actually commands.
    filename       — the .bmp filename this state's image will be saved as
                      under Images/, built from optical angles (not motor
                      angles), e.g. "45_90.bmp".
    """

    index: int
    optical_angles: dict[str, float]
    motor_angles: dict[str, float]
    filename: str


def _state(index: int, optical: dict[str, float], filename_parts: tuple[float, float]) -> MeasurementState:
    """Shared helper: converts optical->motor angles for every axis in
    ``optical`` and builds the "a_b.bmp" filename from ``filename_parts``
    (the two angles that actually vary — QWP or polarizer pair)."""

    motor = {name: optical_to_motor(angle, ZERO_OFFSET[name]) for name, angle in optical.items()}
    filename = "_".join(format_angle(value) for value in filename_parts) + ".bmp"
    return MeasurementState(index, optical, motor, filename)


def generate_3x3(psg_angles: list[float], psa_angles: list[float]) -> list[MeasurementState]:
    """Build every (PSG_Polarizer, PSA_Analyzer) combination for 3x3 mode.

    Generate the Cartesian product; PSG is the outer, stable loop, so all
    PSA angles are swept before PSG advances (matches the printed/expected
    scan order). Produces len(psg_angles) * len(psa_angles) states.
    Called from 01_main.configure_experiment() and states_from_config().
    """

    states = []
    for index, (psg, psa) in enumerate(product(psg_angles, psa_angles)):
        optical = {"PSG_Polarizer": psg, "PSA_Analyzer": psa}
        states.append(_state(index, optical, (psg, psa)))
    return states


def generate_4x4_discrete(
    psg_qwp_angles: list[float],
    psa_qwp_angles: list[float],
    fixed_polarizers: dict[str, float],
) -> list[MeasurementState]:
    """Build every (PSG_QWP, PSA_QWP) combination for 4x4 discrete mode,
    while PSG_Polarizer/PSA_Analyzer stay fixed at the angles the operator
    entered (``fixed_polarizers``). PSG_QWP is the outer loop. Produces
    len(psg_qwp_angles) * len(psa_qwp_angles) states. Called from
    01_main.configure_experiment() and states_from_config()."""

    states = []
    for index, (psg_qwp, psa_qwp) in enumerate(product(psg_qwp_angles, psa_qwp_angles)):
        optical = {
            "PSG_Polarizer": fixed_polarizers["PSG_Polarizer"],
            "PSG_QWP": psg_qwp,
            "PSA_QWP": psa_qwp,
            "PSA_Analyzer": fixed_polarizers["PSA_Analyzer"],
        }
        states.append(_state(index, optical, (psg_qwp, psa_qwp)))
    return states
