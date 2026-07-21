"""Validation, environment checks, and run-directory helpers for continuous mode.

Deliberate duplicate of discreate_angle/utils.py, trimmed to what continuous
rotation actually needs: no angle-list parsing (continuous only takes two
fixed floats and a rotation ratio, asked directly in 01_main.py), and no
BMP-count disk estimate (continuous has no fixed image count decided ahead of
time — see continuous_engine.py for why).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable

from config import DATA_ROOT, KINESIS_DIR, REQUIRED_KINESIS_DLLS


def optical_to_motor(optical_angle: float, offset: float) -> float:
    """motor_angle = (optical_angle + zero_offset) % 360. Same core formula
    as discreate_angle/utils.py; duplicated so this folder has no cross-import."""

    return (float(optical_angle) + float(offset)) % 360.0


def parse_ratio(text: str) -> tuple[int, int]:
    """Parse "slow:fast" revolution-ratio text (e.g. "1:5" -> PSA_QWP spins
    5x for every 1 revolution of PSG_QWP)."""

    slow, fast = (int(part.strip()) for part in text.split(":", 1))
    if slow <= 0 or fast <= 0:
        raise ValueError("Rotation ratio values must be positive integers.")
    return slow, fast


_RUN_SUBFOLDERS = ("Images", "Logs", "Config", "Reports", "Checkpoints", "Results")


def sanitize_folder_name(name: str) -> str:
    """Make ``name`` safe as a single Windows/POSIX path component.

    Deliberate duplicate of discreate_angle/utils.py's sanitize_folder_name().
    """

    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    # An all-symbol input (e.g. "///") cleans to a non-empty string of just
    # underscores, which technically works as a folder name but isn't a
    # usable sample identifier — fall back for that case too, not just a
    # literally empty result.
    if not any(character.isalnum() for character in cleaned):
        return "sample"
    return cleaned


def _next_available(path: Path) -> Path:
    """Return ``path`` if it doesn't exist yet, else the first
    "<path>_02", "<path>_03", ... that doesn't."""

    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.name}_{counter:02d}")
        if not candidate.exists():
            return candidate
        counter += 1


def create_run_directory(root: Path, name: str) -> Path:
    """Create a fresh, collision-free "YYYY-MM-DD_<name>" directory tree.

    ``name`` is normally the sample name (see 01_main.run_fresh_session()).
    Results holds the bright/dark reference BMPs; continuous mode's engine
    does not populate the others (Images, DarkFrames) yet.
    """

    root.mkdir(parents=True, exist_ok=True)
    prefix = date.today().isoformat()
    run = _next_available(root / f"{prefix}_{sanitize_folder_name(name)}")
    for child in _RUN_SUBFOLDERS:
        (run / child).mkdir(parents=True, exist_ok=False)
    return run


def rename_run_directory(run: Path, name: str) -> Path:
    """Rename an existing run directory in place to "YYYY-MM-DD_<name>".

    The caller MUST stop the transcript (closing its log file) before
    calling this — Windows/NTFS refuses to rename a directory while a file
    inside it is still open, even by the same process — and start a new one
    immediately after; see 01_main.run_fresh_session().
    """

    prefix = date.today().isoformat()
    target = _next_available(run.parent / f"{prefix}_{sanitize_folder_name(name)}")
    if target != run:
        run.rename(target)
    return target


def write_json(path: Path, payload: object) -> None:
    """Atomic JSON write: write to a ``.tmp`` sibling, then os.replace().

    Retries os.replace() a few times on PermissionError: rapid repeated
    writes to the same path — e.g. checkpoint.json updated once per
    captured frame during continuous rotation — occasionally race a
    transient antivirus/indexing lock on the freshly-written .tmp file on
    Windows. Observed in testing at a fast simulated capture rate; the
    retry is a no-op cost the rest of the time.
    """

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    last_error: OSError | None = None
    for attempt in range(3):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    raise last_error


def check_environment(output_root: Path = DATA_ROOT) -> list[tuple[str, bool, str]]:
    """Import/filesystem-only diagnostic checks, safe to run without hardware."""

    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))
    for package, import_name in (
        ("pythonnet", "clr"),
        ("IDS Peak", "ids_peak"),
        ("OpenCV", "cv2"),
        ("NumPy", "numpy"),
        ("Pandas", "pandas"),
    ):
        found = importlib.util.find_spec(import_name) is not None
        checks.append((package, found, "available" if found else "not importable"))
    checks.append(("Kinesis directory", KINESIS_DIR.is_dir(), str(KINESIS_DIR)))
    for dll in REQUIRED_KINESIS_DLLS:
        path = KINESIS_DIR / dll
        checks.append((dll, path.is_file(), str(path)))
    output_root.mkdir(parents=True, exist_ok=True)
    writable = os.access(output_root, os.W_OK)
    checks.append(("Data directory writable", writable, str(output_root.resolve())))
    free_gb = shutil.disk_usage(output_root).free / 1024**3
    checks.append(("Free disk >= 1 GB", free_gb >= 1.0, f"{free_gb:.2f} GB"))
    return checks


def yes_no(prompt: str, default: bool = False) -> bool:
    """Ask a Y/n question; blank input accepts ``default``."""

    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    return default if not answer else answer in {"y", "yes"}


def print_angles(label: str, optical: Iterable[float], offset: float) -> None:
    """Print an angle list next to its motor-angle equivalent."""

    optical_list = list(optical)
    motor_list = [optical_to_motor(value, offset) for value in optical_list]
    print(f"{label} optical angles: {optical_list}")
    print(f"{label} motor angles:   {motor_list}")
