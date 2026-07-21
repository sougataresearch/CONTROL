"""Describe a continuous-rotation run's plan (no discrete states exist).

Equivalent in spirit to discreate_angle/state_generator.py, but there is no
MeasurementState list here — continuous rotation has no discrete steps, so
this module only serializes the operator's chosen ratio and fixed angles.
"""

from __future__ import annotations


def continuous_plan(ratio: tuple[int, int], fixed_polarizers: dict[str, float]) -> dict[str, object]:
    """Serialize the intended (slow, fast) revolution ratio and fixed
    polarizer angles to Config/rotation_plan.json, so the plan is on record
    even before continuous_engine.run_continuous() is implemented."""

    slow, fast = ratio
    return {
        "termination": "PSG_QWP completes one revolution",
        "relative_revolutions": {"PSG_QWP": slow, "PSA_QWP": fast},
        "fixed_polarizers": fixed_polarizers,
    }
