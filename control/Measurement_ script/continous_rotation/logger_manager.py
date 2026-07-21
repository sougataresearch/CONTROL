"""CSV audit logging and final human-readable reports — continuous mode.

SessionTranscript is an exact duplicate of discreate_angle's (no experiment-
specific logic in it). ExperimentLogger's columns differ from discrete mode's:
there is no "commanded state," only a captured frame with the motor angles
read back at capture time.
"""

from __future__ import annotations

import csv
import builtins
import sys
import threading
from datetime import datetime
from io import TextIOBase
from pathlib import Path
from typing import Mapping

from config import ExperimentConfig


class _TeeStream(TextIOBase):
    """Write terminal output to both the original stream and a durable log."""

    def __init__(self, original: TextIOBase, log_handle: TextIOBase, lock: threading.Lock) -> None:
        self.original = original
        self.log_handle = log_handle
        self.lock = lock

    def write(self, text: str) -> int:
        with self.lock:
            self.original.write(text)
            self.log_handle.write(text)
            self.original.flush()
            self.log_handle.flush()
        return len(text)

    def flush(self) -> None:
        with self.lock:
            self.original.flush()
            self.log_handle.flush()

    def isatty(self) -> bool:
        return self.original.isatty()


class SessionTranscript:
    """Capture stdout, stderr, prompts, and operator answers for one run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIOBase | None = None
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._original_input = builtins.input
        self._lock = threading.Lock()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)
        self._handle.write(
            f"\n{'=' * 72}\nSESSION START {datetime.now().astimezone().isoformat()}\n{'=' * 72}\n"
        )
        self._handle.flush()
        sys.stdout = _TeeStream(self._original_stdout, self._handle, self._lock)
        sys.stderr = _TeeStream(self._original_stderr, self._handle, self._lock)

        def audited_input(prompt: str = "") -> str:
            if prompt:
                print(prompt, end="", flush=True)
            answer = self._original_input("")
            with self._lock:
                self._handle.write(f"[OPERATOR INPUT] {answer}\n")
                self._handle.flush()
            return answer

        builtins.input = audited_input

    def stop(self) -> None:
        if self._handle is None:
            return
        print(f"SESSION END {datetime.now().astimezone().isoformat()}")
        builtins.input = self._original_input
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        self._handle.close()
        self._handle = None


class ExperimentLogger:
    """Append one CSV row per captured frame to Logs/experiment_log.csv."""

    FIELDNAMES = (
        "Frame Index",
        "PSG_QWP Angle",
        "PSA_QWP Angle",
        "Timestamp",
        "Attempt Count",
        "Status",
        "Error Message",
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                csv.DictWriter(handle, fieldnames=self.FIELDNAMES).writeheader()

    def log(
        self,
        frame_index: int,
        psg_qwp_angle: float,
        psa_qwp_angle: float,
        attempt_count: int,
        status: str,
        error_message: str = "",
    ) -> None:
        with self.path.open("a", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=self.FIELDNAMES).writerow(
                {
                    "Frame Index": frame_index,
                    "PSG_QWP Angle": psg_qwp_angle,
                    "PSA_QWP Angle": psa_qwp_angle,
                    "Timestamp": datetime.now().astimezone().isoformat(),
                    "Attempt Count": attempt_count,
                    "Status": status,
                    "Error Message": error_message,
                }
            )


def write_report(
    path: Path,
    config: ExperimentConfig,
    serial_numbers: Mapping[str, str],
    total_frames: int,
    failed: int,
    elapsed_s: float,
) -> None:
    """Write the human-readable Reports/ExperimentReport.txt summary."""

    lines = [
        "MMIE Continuous-Rotation Experiment Report",
        "=" * 42,
        f"Operator: {config.metadata.operator}",
        f"Sample: {config.metadata.sample}",
        f"Comments: {config.metadata.comments}",
        "Mode: 4x4 continuous",
        f"Dry run: {config.dry_run}",
        f"Motor serial numbers: {dict(serial_numbers)}",
        f"Fixed optical angles: {config.fixed_angles}",
        f"Rotation ratio (PSG_QWP:PSA_QWP): {config.rotation_ratio}",
        f"Camera settings: {config.camera}",
        f"Total frames: {total_frames}",
        f"Failed frames: {failed}",
        f"Elapsed time (s): {elapsed_s:.3f}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
