"""Discover 4x4 continuous-rotation images and their per-frame PSG/PSA QWP
angles for a run.

Filename convention (continuous_engine.py, Measuremt_ script/
continous_rotation), 4x4 continuous only:
"frame_{index:04d}_psg{angle:.1f}_psa{angle:.1f}.ext". Unlike the discrete
pipeline, the ANGLE actually used here is not parsed from the filename --
that value is rounded to 1 decimal place for readability. The authoritative,
full-precision angle for each frame is read from
Logs/experiment_log.csv (written once per captured frame by
continuous_engine.py: real, polled encoder angles at the moment of capture,
not nominal/expected ones, since real hardware has velocity ripple). Only
rows with Status == "SUCCESS" are used; a frame logged as FAILED (or a
SUCCESS row whose image file is missing) is skipped rather than guessed at.
PSG_Polarizer/PSA_Analyzer fixed angles come from
Config/experiment_config.json's "fixed_angles", same convention as the
discrete pipeline.

If run_dir/Dark/ contains one or more image files (any names), they are
averaged into a dark-current reference frame and subtracted (then clipped
at 0) from every loaded image before reconstruction. If run_dir/Dark/
doesn't exist, run_dir/Results/DarkReference_*.bmp is used instead (the
naming convention some capture scripts use in place of a dedicated Dark/
folder) -- see _load_dark_frame(). Optional: if neither is found, a
warning is printed and reconstruction proceeds on raw intensities as
before.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class RunImages4x4:
    fixed_angles: dict       # {"PSG_Polarizer": ..., "PSA_Analyzer": ...}
    psg_qwp_angles: np.ndarray  # (N,) PSG_QWP angle per image, from the CSV log
    psa_qwp_angles: np.ndarray  # (N,) PSA_QWP angle per image, from the CSV log
    images: np.ndarray          # (N, H, W) float64 intensities, dark-subtracted if available
    files: list
    dark_subtracted: bool   # whether a Dark/ reference was found and applied
    dark_frame_count: int   # number of dark frames averaged (0 if none)
    dark_level_mean: float  # mean pixel value of the averaged dark frame (0.0 if none)


def _read_image(path: Path) -> np.ndarray:
    arr = np.array(Image.open(path))
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    return arr.astype(np.float64)


def _dark_reference_paths(run_dir: Path, dark_subdir: str = "Dark") -> list:
    """Find dark-current reference image files for run_dir, checking
    run_dir/dark_subdir first and falling back to run_dir/Results/
    DarkReference_*.bmp (the naming convention some capture scripts use
    instead of a dedicated Dark/ folder). Returns an empty list if none
    are found."""

    dark_dir = run_dir / dark_subdir
    if dark_dir.is_dir():
        return sorted(p for p in dark_dir.iterdir() if p.is_file())
    return sorted((run_dir / "Results").glob("DarkReference_*.bmp"))


def dark_reference_available(run_dir, dark_subdir: str = "Dark") -> bool:
    """Cheap existence check for whether load_run() would find and apply a
    dark-current reference for run_dir, without reading/averaging the
    actual image data. Used by validate_against_theory.py to invalidate a
    cached reconstruction whose dark-subtraction status no longer matches
    the run's current state (e.g. a dark reference was added after main.py
    last reconstructed it)."""

    return bool(_dark_reference_paths(Path(run_dir), dark_subdir))


def _load_dark_frame(run_dir: Path, dark_subdir: str = "Dark"):
    """Look for dark-current reference frames, captured at the same
    exposure/gain as this run but with the sensor seeing no real signal
    (light source off, or all components removed/blocked -- either is
    fine, since dark current is a property of the sensor and exposure,
    not of the beam path). One or several image files are accepted;
    several are averaged to reduce read noise in the reference itself.
    Returns (averaged_dark_frame_or_None, count)."""

    paths = _dark_reference_paths(run_dir, dark_subdir)
    if not paths:
        return None, 0
    frames = [_read_image(p) for p in paths]
    return np.mean(frames, axis=0), len(frames)


def _fixed_angles(run_dir: Path) -> dict:
    config_path = run_dir / "Config" / "experiment_config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Could not find {config_path}; cannot read this run's fixed "
            "PSG_Polarizer/PSA_Analyzer angles."
        )
    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    fixed_angles = config.get("fixed_angles") or {}
    for name in ("PSG_Polarizer", "PSA_Analyzer"):
        if name not in fixed_angles:
            raise KeyError(
                f"{config_path}'s fixed_angles is missing {name!r}; "
                "4x4 reconstruction needs both fixed angles to build the "
                "generator/analyzer Stokes vectors."
            )
    return fixed_angles


def load_run(
    run_dir,
    images_subdir: str = "Images",
    log_path: str = "Logs/experiment_log.csv",
) -> RunImages4x4:
    """Load every successfully-logged frame under run_dir, in frame-index order."""

    run_dir = Path(run_dir)
    fixed_angles = _fixed_angles(run_dir)

    csv_path = run_dir / log_path
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Could not find {csv_path}; continuous reconstruction reads "
            "per-frame angles from this CSV rather than the filename "
            "(see continuous_engine.py)."
        )

    image_dir = run_dir / images_subdir
    frame_indices: list[int] = []
    psg_angles: list[float] = []
    psa_angles: list[float] = []
    paths: list[Path] = []
    skipped_missing = 0

    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["Status"] != "SUCCESS":
                continue
            index = int(row["Frame Index"])
            matches = sorted(image_dir.glob(f"frame_{index:04d}_*"))
            if not matches:
                skipped_missing += 1
                continue
            frame_indices.append(index)
            psg_angles.append(float(row["PSG_QWP Angle"]))
            psa_angles.append(float(row["PSA_QWP Angle"]))
            paths.append(matches[0])

    if not paths:
        raise FileNotFoundError(
            f"No successfully logged frames with a matching image were found "
            f"under {image_dir} (checked against {csv_path})."
        )
    if skipped_missing:
        print(
            f"Warning: {skipped_missing} frame(s) logged SUCCESS in {csv_path.name} "
            "had no matching image file and were skipped."
        )

    order = np.argsort(frame_indices)
    frames = [_read_image(paths[i]) for i in order]

    shapes = {frame.shape for frame in frames}
    if len(shapes) > 1:
        raise ValueError(f"Inconsistent image sizes in {image_dir}: {shapes}")

    images = np.stack(frames, axis=0)
    psg_qwp_angles = np.array(psg_angles, dtype=np.float64)[order]
    psa_qwp_angles = np.array(psa_angles, dtype=np.float64)[order]
    ordered_paths = [paths[i] for i in order]

    dark_frame, dark_count = _load_dark_frame(run_dir)
    if dark_frame is not None:
        if dark_frame.shape != images.shape[1:]:
            raise ValueError(
                f"Dark frame shape {dark_frame.shape} doesn't match image shape "
                f"{images.shape[1:]} in {run_dir}"
            )
        images = np.clip(images - dark_frame[None, :, :], 0.0, None)
        dark_subtracted = True
        dark_level_mean = float(dark_frame.mean())
    else:
        print(
            f"No dark-current reference found at {run_dir / 'Dark'} or "
            f"{run_dir / 'Results' / 'DarkReference_*.bmp'}. To correct for "
            "sensor dark current: after capturing this run's images, block all light "
            "reaching the camera (turn off the source, or remove/cover all optical "
            "components -- either is fine, since dark current depends on the sensor "
            "and exposure settings, not the beam path) and capture 1 or more frames at "
            f"the SAME exposure/gain as this run. Save them as image files into "
            f"{run_dir / 'Dark'} (or as DarkReference_*.bmp in {run_dir / 'Results'}), "
            "then re-run. Proceeding WITHOUT dark-current subtraction for now."
        )
        dark_subtracted = False
        dark_level_mean = 0.0

    return RunImages4x4(
        fixed_angles, psg_qwp_angles, psa_qwp_angles, images, ordered_paths,
        dark_subtracted, dark_count, dark_level_mean,
    )
