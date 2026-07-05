"""Regression test for this folder's physics + reconstruction, using
synthetic (not real) images.

Run from INSIDE this folder:

    python -m unittest test_reconstruction -v

Why this exists: the rotation-sandwich physics in mueller_forward_model.py
is duplicated (by design, no shared code) across every own_code/ mode and
both angle_subset_comparison/subset_error_analysis tools. A bug introduced
in this copy would otherwise only surface as a subtly-wrong Mueller matrix
on a real capture, with nothing to flag it. This test catches that
immediately: it fabricates images for a *known* theoretical sample (air ->
identity matrix) by computing exactly what the camera should record at each
angle if that were the true sample, feeds them through the real
image_loader.load_run() and solve_mueller.reconstruct(), and asserts the
reconstruction recovers the identity matrix to near-zero error. No real
camera or hardware involved -- this is pure math in, pure math out.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from image_loader import load_run
from mueller_forward_model import analyzer_vector_3x3, generator_stokes_3x3
from solve_mueller import reconstruct


def _write_synthetic_run(run_dir: Path, theory: np.ndarray, angles: list,
                          extinction_ratio: float = 0.0) -> None:
    (run_dir / "Config").mkdir(parents=True)
    (run_dir / "Images").mkdir(parents=True)
    (run_dir / "Config" / "experiment_config.json").write_text(
        json.dumps({"mode": "3x3"}), encoding="utf-8"
    )

    for psg in angles:
        for psa in angles:
            s = generator_stokes_3x3(psg, extinction_ratio)
            a = analyzer_vector_3x3(psa, extinction_ratio)
            intensity = float(a @ (theory @ s))
            arr = np.full((2, 2), intensity * 100.0, dtype=np.float32)
            Image.fromarray(arr, mode="F").save(run_dir / "Images" / f"{psg:g}_{psa:g}.tiff")


class SyntheticReconstructionTests(unittest.TestCase):
    def test_air_reconstructs_to_identity(self):
        theory = np.eye(3)
        angles = [0.0, 45.0, 90.0]  # exactly 9 images, the 3x3 minimum

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            run = load_run(run_dir)
            result = reconstruct(run, extinction_ratio=0.0)

            np.testing.assert_allclose(result.matrix_mean, theory, atol=1e-8)

    def test_overdetermined_grid_still_reconstructs_to_identity(self):
        theory = np.eye(3)
        angles = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0]  # 36 images, over-determined

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            run = load_run(run_dir)
            result = reconstruct(run, extinction_ratio=0.0)

            np.testing.assert_allclose(result.matrix_mean, theory, atol=1e-8)


if __name__ == "__main__":
    unittest.main()
