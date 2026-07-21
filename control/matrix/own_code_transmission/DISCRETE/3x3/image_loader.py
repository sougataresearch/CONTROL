"""Discover 3x3 intensity images and their PSG/PSA angle pairs for a run.

Filename convention (state_generator.py, Measuremt_ script/discreate_angle),
3x3 mode only: "{PSG_Polarizer_optical}_{PSA_Analyzer_optical}.ext" -- both
angles are optical angles (already ZERO_OFFSET-corrected), which is what
mueller_forward_model.py expects. There is no fixed-angle side channel in
3x3 mode: both PSG_Polarizer and PSA_Analyzer vary, and both are named
directly in the filename.

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
    images: np.ndarray      # (N, H, W) float64 intensities, dark-subtracted if available
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

    return RunImages3x3(
        psg_angles, psa_angles, images, paths,
        dark_subtracted, dark_count, dark_level_mean,
    )
