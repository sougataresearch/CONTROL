"""Crash-safe progress checkpoints for continuous rotation runs.

Unlike discreate_angle/checkpoint_manager.py, there is no discrete state
index to resume from — continuous rotation is one uninterrupted revolution
of PSG_QWP. A checkpoint here records the last frame captured during that
revolution (for audit/debugging) but does NOT support resuming mid-rotation;
an interrupted continuous run must restart from the beginning of the
revolution. See continuous_engine.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from utils import write_json


class CheckpointManager:
    """Track progress of one continuous-rotation run. Backs
    Checkpoints/checkpoint.json."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {"frames_captured": 0, "revolution_completed": False}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record_frame(self, frame_index: int, psg_qwp_angle: float, psa_qwp_angle: float) -> None:
        """Record the most recent frame captured during the current
        (single, non-resumable) revolution."""

        write_json(
            self.path,
            {
                "frames_captured": frame_index + 1,
                "last_psg_qwp_angle": psg_qwp_angle,
                "last_psa_qwp_angle": psa_qwp_angle,
                "revolution_completed": False,
            },
        )

    def complete(self, total_frames: int) -> None:
        """Mark the revolution as finished."""

        payload = self.load()
        payload.update({"frames_captured": total_frames, "revolution_completed": True})
        write_json(self.path, payload)
