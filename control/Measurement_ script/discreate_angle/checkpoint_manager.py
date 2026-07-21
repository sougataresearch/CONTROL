"""Crash-safe progress checkpoints."""

from __future__ import annotations

import json
from pathlib import Path

from state_generator import MeasurementState
from utils import write_json


class CheckpointManager:
    """Track the last successfully completed measurement state for one run,
    written atomically (via utils.write_json) so it is always safe to read
    even after a crash. Backs Checkpoints/checkpoint.json."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        """Return the raw checkpoint dict, or a fresh "nothing done yet"
        default if no checkpoint file exists (first run)."""

        if not self.path.exists():
            return {"last_completed_index": -1, "experiment_completed": False}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def next_index(self) -> int:
        """Index of the first state that still needs to run. Used by
        measurement_engine.run_discrete() to slice the states list on
        --resume, so already-completed states are never redone.

        Returning total_states for a completed run makes the state slice empty,
        rather than accidentally reacquiring and overwriting every image.
        """

        checkpoint = self.load()
        return int(checkpoint.get("last_completed_index", -1)) + 1

    def update(self, state: MeasurementState) -> None:
        """Record ``state`` as the last successfully completed one. Called
        from measurement_engine.run_discrete() only AFTER the image for that
        state has been acquired, saved, verified, and logged — never before,
        so the checkpoint can never point past a state whose image is
        missing or unverified."""

        write_json(
            self.path,
            {
                "last_completed_index": state.index,
                "filename": state.filename,
                "optical_angles": state.optical_angles,
                "motor_angles": state.motor_angles,
                "experiment_completed": False,
            },
        )

    def complete(self, total_states: int) -> None:
        """Mark the whole run as finished (all states done). Called once,
        at the end of measurement_engine.run_discrete(), after the loop over
        all states finishes without error."""

        payload = self.load()
        payload.update({"total_states": total_states, "experiment_completed": True})
        write_json(self.path, payload)
