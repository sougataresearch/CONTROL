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
    images: np.ndarray          # (N, H, W) float64 intensities
    files: list


def _read_image(path: Path) -> np.ndarray:
    arr = np.array(Image.open(path))
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    return arr.astype(np.float64)


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

    return RunImages4x4(fixed_angles, psg_qwp_angles, psa_qwp_angles, images, ordered_paths)
