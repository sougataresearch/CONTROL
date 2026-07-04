"""Discover 3x3 intensity images and their PSG/PSA angle pairs for a run.

Filename convention (state_generator.py, Measuremt_ script/discreate_angle),
3x3 mode only: "{PSG_Polarizer_optical}_{PSA_Analyzer_optical}.ext" -- both
angles are optical angles (already ZERO_OFFSET-corrected), which is what
mueller_forward_model.py expects. There is no fixed-angle side channel in
3x3 mode: both PSG_Polarizer and PSA_Analyzer vary, and both are named
directly in the filename.
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
class RunImages3x3:
    psg_angles: np.ndarray  # (N,) PSG_Polarizer optical angle per image
    psa_angles: np.ndarray  # (N,) PSA_Analyzer optical angle per image
    images: np.ndarray      # (N, H, W) float64 intensities
    files: list


def _read_image(path: Path) -> np.ndarray:
    arr = np.array(Image.open(path))
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    return arr.astype(np.float64)


def _check_mode(run_dir: Path) -> None:
    config_path = run_dir / "Config" / "experiment_config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Could not find {config_path}; cannot confirm this run is 3x3 mode."
        )
    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    if config["mode"] != "3x3":
        raise ValueError(
            f"{config_path} says mode={config['mode']!r}, not '3x3'. "
            "Use the 4x4 pipeline (own_code/4x4) for this run instead."
        )


def load_run(run_dir, images_subdir: str = "Images") -> RunImages3x3:
    """Load every angle-named image under run_dir/images_subdir, in filename order."""

    run_dir = Path(run_dir)
    _check_mode(run_dir)

    image_dir = run_dir / images_subdir
    paths = sorted(
        p for p in image_dir.iterdir()
        if p.is_file() and FILENAME_RE.match(p.stem)
    )
    if not paths:
        raise FileNotFoundError(f"No angle-named images found in {image_dir}")

    psg_angles = np.empty(len(paths), dtype=np.float64)
    psa_angles = np.empty(len(paths), dtype=np.float64)
    frames = []
    for i, path in enumerate(paths):
        match = FILENAME_RE.match(path.stem)
        psg_angles[i] = float(match.group(1))
        psa_angles[i] = float(match.group(2))
        frames.append(_read_image(path))

    shapes = {frame.shape for frame in frames}
    if len(shapes) > 1:
        raise ValueError(f"Inconsistent image sizes in {image_dir}: {shapes}")

    images = np.stack(frames, axis=0)
    return RunImages3x3(psg_angles, psa_angles, images, paths)
