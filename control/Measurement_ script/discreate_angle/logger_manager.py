"""CSV audit logging and final human-readable reports."""

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
from state_generator import MeasurementState


class _TeeStream(TextIOBase):
    """Write terminal output to both the original stream and a durable log.

    Used to replace sys.stdout/sys.stderr for the run's duration, so every
    print() the software does is duplicated into terminal_transcript.txt
    without changing any print() call site.
    """

    def __init__(self, original: TextIOBase, log_handle: TextIOBase, lock: threading.Lock) -> None:
        self.original = original
        self.log_handle = log_handle
        self.lock = lock

    def write(self, text: str) -> int:
        with self.lock:
            self.original.write(text)
            self.log_handle.write(text)
            # Flush every message so a crash loses as little diagnostic context as possible.
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
    """Capture stdout, stderr, prompts, and operator answers for one run.

    Created and start()ed at the very top of 01_main.main(), before the
    first prompt, so mode selection onward is fully recorded to
    Logs/terminal_transcript.txt. stop() restores normal stdout/stderr/input
    and is always called from main()'s ``finally`` block.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIOBase | None = None
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._original_input = builtins.input
        self._lock = threading.Lock()

    def start(self) -> None:
        """Open the transcript file, replace sys.stdout/sys.stderr with
        _TeeStream wrappers, and monkey-patch builtins.input so both the
        prompt text and the operator's typed answer are written to the log."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)
        self._handle.write(
            f"\n{'=' * 72}\nSESSION START {datetime.now().astimezone().isoformat()}\n{'=' * 72}\n"
        )
        self._handle.flush()
        sys.stdout = _TeeStream(self._original_stdout, self._handle, self._lock)
        sys.stderr = _TeeStream(self._original_stderr, self._handle, self._lock)

        def audited_input(prompt: str = "") -> str:
            # Printing the prompt through the tee records it. The typed answer is
            # written directly to the log to avoid displaying it twice onscreen.
            if prompt:
                print(prompt, end="", flush=True)
            answer = self._original_input("")
            with self._lock:
                self._handle.write(f"[OPERATOR INPUT] {answer}\n")
                self._handle.flush()
            return answer

        builtins.input = audited_input

    def stop(self) -> None:
        """Restore the original stdout/stderr/input and close the transcript
        file. Safe to call even if start() was never called (no-op)."""

        if self._handle is None:
            return
        print(f"SESSION END {datetime.now().astimezone().isoformat()}")
        builtins.input = self._original_input
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        self._handle.close()
        self._handle = None


class ExperimentLogger:
    """Append one CSV row per measurement state to Logs/experiment_log.csv.
    Columns record both what was commanded and what the hardware reported,
    so a mismatch is auditable after the fact even without re-running."""

    FIELDNAMES = (
        "State Index",
        "Filename",
        "Commanded Optical Angles",
        "Commanded Motor Angles",
        "Reported Motor Positions",
        "Timestamp",
        "Attempt Count",
        "Status",
        "Error Message",
    )

    def __init__(self, path: Path) -> None:
        """Create the CSV with a header row if it doesn't exist yet (so
        --resume appends to the same file instead of overwriting it)."""

        self.path = path
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                csv.DictWriter(handle, fieldnames=self.FIELDNAMES).writeheader()

    def log(
        self,
        state: MeasurementState,
        reported_positions: Mapping[str, float],
        attempt_count: int,
        status: str,
        error_message: str = "",
    ) -> None:
        """Append one row for ``state``. Called once per state from
        measurement_engine.run_discrete(), with status "SUCCESS" or "FAILED"."""

        with self.path.open("a", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=self.FIELDNAMES).writerow(
                {
                    "State Index": state.index,
                    "Filename": state.filename,
                    "Commanded Optical Angles": repr(state.optical_angles),
                    "Commanded Motor Angles": repr(state.motor_angles),
                    "Reported Motor Positions": repr(dict(reported_positions)),
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
    total: int,
    failed: int,
    elapsed_s: float,
) -> None:
    """Write the human-readable Reports/ExperimentReport.txt summary.

    Called from measurement_engine.run_discrete()'s ``finally`` block, so it
    is written whether the run finished, failed, or was Ctrl-C stopped
    (using whatever total/failed counts were reached so far).
    """

    lines = [
        "MMIE Experiment Report",
        "=" * 22,
        f"Operator: {config.metadata.operator}",
        f"Sample: {config.metadata.sample}",
        f"Comments: {config.metadata.comments}",
        f"Mode: {config.mode}",
        f"Dry run: {config.dry_run}",
        f"Motor serial numbers: {dict(serial_numbers)}",
        f"Fixed optical angles: {config.fixed_angles}",
        f"Camera settings: {config.camera}",
        f"Total images: {total}",
        f"Failed images: {failed}",
        f"Elapsed time (s): {elapsed_s:.3f}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
