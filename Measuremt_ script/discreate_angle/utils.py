"""Validation, angle parsing, environment checks, and run-directory helpers."""

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
    """Convert an optical coordinate to its wrapped physical motor coordinate.

    This is THE core calibration formula used everywhere in the project:
    motor_angle = (optical_angle + zero_offset) % 360. ``offset`` always
    comes from config.ZERO_OFFSET[motor_name]. Called by state_generator
    (to build each MeasurementState), 01_main (camera-check moves, angle
    previews), and calibration.py (verification scans).
    """

    return (float(optical_angle) + float(offset)) % 360.0


def format_angle(angle: float) -> str:
    """Make stable, filesystem-safe angle labels without unnecessary decimals.

    Used only for building image filenames (e.g. "45_90.bmp" instead of
    "45.000000_90.000000.bmp"). Whole-number angles print without a decimal
    point; fractional angles keep up to 6 decimals with trailing zeros
    stripped.
    """

    value = float(angle) % 360.0
    return str(int(value)) if value.is_integer() else f"{value:.6f}".rstrip("0").rstrip(".")


def parse_angle_spec(text: str) -> list[float]:
    """Parse either ``360/step`` or a comma-separated optical-angle array.

    Called from 01_main.ask_angles() every time the operator is asked for an
    angle list (PSG/PSA polarizer or QWP angles). Two accepted formats:
      "360/10"        -> 0, 10, 20, ..., 350   (360 itself is excluded,
                                                 since it equals 0)
      "0,30,60,90"     -> exactly those angles, each wrapped into 0-360
    Raises ValueError (shown to the operator, who is re-prompted) if the
    span/step aren't positive, the list is empty, or the same angle would
    appear twice after wrapping (duplicates would overwrite the same image
    filename).
    """

    text = text.strip()
    if "/" in text:
        numerator_text, step_text = (part.strip() for part in text.split("/", 1))
        span, step = float(numerator_text), float(step_text)
        if span <= 0 or step <= 0:
            raise ValueError("Span and step must both be positive.")
        values: list[float] = []
        current = 0.0
        # A tolerance prevents floating-point noise from accidentally including 360.
        while current < span - 1e-10:
            values.append(current % 360.0)
            current += step
    else:
        values = [float(part.strip()) % 360.0 for part in text.split(",") if part.strip()]
    if not values:
        raise ValueError("At least one angle is required.")
    # Duplicate optical states would overwrite identically named image files.
    if len(set(values)) != len(values):
        raise ValueError("Angle states must be unique after wrapping to 0–360°.")
    return values


_RUN_SUBFOLDERS = ("Images", "Logs", "Config", "DarkFrames", "Reports", "Checkpoints", "Results")


def sanitize_folder_name(name: str) -> str:
    """Make ``name`` safe as a single Windows/POSIX path component.

    Replaces characters Windows forbids in file/folder names, strips
    trailing dots/spaces (also disallowed by Windows), and falls back to
    "sample" if nothing usable remains (e.g. an all-symbol input).
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

    ``name`` is normally the sample name (see 01_main.run_fresh_session()),
    sanitized via sanitize_folder_name(); if a folder by that name already
    exists (e.g. the same sample measured twice), a "_02", "_03", ... suffix
    is appended. Creates the seven standard subfolders (Images, Logs,
    Config, DarkFrames, Reports, Checkpoints, Results).
    """

    root.mkdir(parents=True, exist_ok=True)
    prefix = date.today().isoformat()
    run = _next_available(root / f"{prefix}_{sanitize_folder_name(name)}")
    for child in _RUN_SUBFOLDERS:
        (run / child).mkdir(parents=True, exist_ok=False)
    return run


def rename_run_directory(run: Path, name: str) -> Path:
    """Rename an existing run directory in place to "YYYY-MM-DD_<name>".

    Used once, for the very first sample of a fresh session: main() creates
    a "pending" placeholder (via create_run_directory()) before mode
    selection so the transcript can capture it, then this renames that
    folder to the first sample's real name once it's known. The caller
    MUST stop the transcript (closing its log file) before calling this —
    Windows/NTFS refuses to rename a directory while a file inside it is
    still open, even by the same process — and start a new one immediately
    after; see 01_main.run_fresh_session(). Subsequent samples in the same
    session get a genuinely new folder via create_run_directory() instead,
    since their data must not overwrite the previous sample's.
    """

    prefix = date.today().isoformat()
    target = _next_available(run.parent / f"{prefix}_{sanitize_folder_name(name)}")
    if target != run:
        run.rename(target)
    return target


def write_json(path: Path, payload: object) -> None:
    """Write JSON atomically so interruption cannot leave a partial document.

    Writes to a "<name>.tmp" sibling file first, then uses os.replace() (an
    atomic rename on both Windows and POSIX) to swap it into place. This is
    what makes checkpoint.json and experiment_config.json safe to read even
    if power is lost or Ctrl-C lands mid-write — the reader always sees
    either the old complete file or the new complete file, never a partial one.

    Retries os.replace() a few times on WindowsError/PermissionError: rapid
    repeated writes to the same path (e.g. a checkpoint updated many times a
    second) occasionally race a transient antivirus/indexing lock on the
    freshly-written .tmp file on Windows — observed in testing at a fast
    simulated continuous-rotation capture rate. The retry is a no-op cost on
    the far more common case where nothing else is touching the file.
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
    """Return diagnostic results without requiring hardware SDK imports.

    Called from 01_main.print_environment_report() right after mode
    selection. Every check here is import-based or filesystem-based only —
    it never talks to actual hardware — so it is always safe to run,
    including in dry-run mode. Checks, in order:
      1. Python version >= 3.11
      2. Each required Python package is importable (pythonnet, IDS Peak,
         OpenCV, NumPy, Pandas)
      3. The Kinesis install directory exists (config.KINESIS_DIR)
      4. Each required Kinesis DLL exists inside it (config.REQUIRED_KINESIS_DLLS)
      5. DATA_ROOT is writable
      6. At least 1 GB free disk space
    If any check fails, 01_main forces dry-run mode (or blocks a real run).
    """

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


def estimate_disk_bytes(image_count: int, width: int, height: int) -> int:
    """Conservative uncompressed 8-bit BMP estimate, including small headers.

    Called from 01_main.check_disk_space() after states are generated, to
    warn the operator (and abort) if the planned scan would not fit on
    disk. width/height are deliberately required, not defaulted here —
    01_main passes camera.frame_width/frame_height, the camera's own
    actual configured frame size (real hardware) or
    config.FALLBACK_SENSOR_WIDTH/HEIGHT (dry-run only), rather than a
    second guessed constant living in this file too.
    """

    return image_count * (width * height + 4096)


def yes_no(prompt: str, default: bool = False) -> bool:
    """Ask a Y/n question; blank input accepts ``default``. Used for every
    confirmation prompt in 01_main.py (confirm_stage, dry-run choice, disk
    space, "begin acquisition", camera-verification warnings)."""

    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    return default if not answer else answer in {"y", "yes"}


def print_angles(label: str, optical: Iterable[float], offset: float) -> None:
    """Print both the optical angles the operator typed and the motor angles
    they translate to (using ``offset`` = ZERO_OFFSET[motor]), so the
    operator can sanity-check the calibration before committing to a run."""

    optical_list = list(optical)
    motor_list = [optical_to_motor(value, offset) for value in optical_list]
    print(f"{label} optical angles: {optical_list}")
    print(f"{label} motor angles:   {motor_list}")
