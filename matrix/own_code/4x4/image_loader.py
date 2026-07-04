"""Discover 4x4 intensity images and their PSG/PSA QWP angle pairs for a run.

Filename convention (state_generator.py, Measuremt_ script/discreate_angle),
4x4 mode only: "{PSG_QWP_optical}_{PSA_QWP_optical}.ext". PSG_Polarizer and
PSA_Analyzer are held fixed for the whole run and are NOT in the filename --
their values live in that run's Config/experiment_config.json under
"fixed_angles". All angles are optical angles (already ZERO_OFFSET-
corrected), which is what mueller_forward_model.py expects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

FILENAME_RE = re.compile(r"^(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")


@dataclass
class RunImages4x4:
    fixed_angles: dict       # {"PSG_Polarizer": ..., "PSA_Analyzer": ...}
    psg_qwp_angles: np.ndarray  # (N,) PSG_QWP optical angle per image
    psa_qwp_angles: np.ndarray  # (N,) PSA_QWP optical angle per image
    images: np.ndarray          # (N, H, W) float64 intensities
    files: list


def _read_image(path: Path) -> np.ndarray:
    arr = np.array(Image.open(path))
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    return arr.astype(np.float64)


def _mode_and_fixed_angles(run_dir: Path) -> tuple:
    config_path = run_dir / "Config" / "experiment_config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Could not find {config_path}; cannot confirm this run is 4x4 mode "
            "or read its fixed PSG_Polarizer/PSA_Analyzer angles."
        )
    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    if config["mode"] != "4x4":
        raise ValueError(
            f"{config_path} says mode={config['mode']!r}, not '4x4'. "
            "Use the 3x3 pipeline (own_code/3x3) for this run instead."
        )
    fixed_angles = config.get("fixed_angles") or {}
    for name in ("PSG_Polarizer", "PSA_Analyzer"):
        if name not in fixed_angles:
            raise KeyError(
                f"{config_path}'s fixed_angles is missing {name!r}; "
                "4x4 reconstruction needs both fixed angles to build the "
                "generator/analyzer Stokes vectors."
            )
    return fixed_angles


def load_run(run_dir, images_subdir: str = "Images") -> RunImages4x4:
    """Load every angle-named image under run_dir/images_subdir, in filename order."""

    run_dir = Path(run_dir)
    fixed_angles = _mode_and_fixed_angles(run_dir)

    image_dir = run_dir / images_subdir
    paths = sorted(
        p for p in image_dir.iterdir()
        if p.is_file() and FILENAME_RE.match(p.stem)
    )
    if not paths:
        raise FileNotFoundError(f"No angle-named images found in {image_dir}")

    psg_qwp_angles = np.empty(len(paths), dtype=np.float64)
    psa_qwp_angles = np.empty(len(paths), dtype=np.float64)
    frames = []
    for i, path in enumerate(paths):
        match = FILENAME_RE.match(path.stem)
        psg_qwp_angles[i] = float(match.group(1))
        psa_qwp_angles[i] = float(match.group(2))
        frames.append(_read_image(path))

    shapes = {frame.shape for frame in frames}
    if len(shapes) > 1:
        raise ValueError(f"Inconsistent image sizes in {image_dir}: {shapes}")

    images = np.stack(frames, axis=0)
    return RunImages4x4(fixed_angles, psg_qwp_angles, psa_qwp_angles, images, paths)
