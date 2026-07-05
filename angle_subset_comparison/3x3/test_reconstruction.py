"""Regression test for this folder's physics + reconstruction, using
synthetic (not real) images.

Run from INSIDE this folder:

    python -m unittest test_reconstruction -v

Why this exists: this tool's rotation-sandwich physics is its own
self-contained copy (by design, no shared code with control/), so a bug
introduced here would otherwise only surface as a subtly-wrong deviation
number in deviation_chart.png, with nothing to flag it. This test catches
that immediately: it fabricates images for a *known* theoretical sample
(air -> identity matrix) by computing exactly what the camera should record
at each angle if that were the true sample, feeds them through the real
load_run() and reconstruct_subset(), and asserts the reconstruction
recovers the identity matrix to near-zero error. No real camera or hardware
involved -- this is pure math in, pure math out.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import compare_subsets as tool


def _write_synthetic_run(run_dir: Path, theory: np.ndarray, angles: list,
                          extinction_ratio: float = 0.0) -> None:
    (run_dir / "Config").mkdir(parents=True)
    (run_dir / "Images").mkdir(parents=True)
    (run_dir / "Config" / "experiment_config.json").write_text(
        json.dumps({"mode": "3x3"}), encoding="utf-8"
    )

    for psg in angles:
        for psa in angles:
            s = tool.generator_stokes_3x3(psg, extinction_ratio)
            a = tool.analyzer_vector_3x3(psa, extinction_ratio)
            intensity = float(a @ (theory @ s))
            arr = np.full((2, 2), intensity * 100.0, dtype=np.float32)
            Image.fromarray(arr, mode="F").save(run_dir / "Images" / f"{psg:g}_{psa:g}.tiff")


class SyntheticReconstructionTests(unittest.TestCase):
    def test_full_grid_reconstructs_to_identity(self):
        theory = np.eye(3)
        angles = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0]  # 36 images, over-determined

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            psg_angles, psa_angles, images, sample_name = tool.load_run(str(run_dir))
            self.assertEqual(sample_name, "air")
            matrix_mean, _condition_number = tool.reconstruct_subset(
                psg_angles, psa_angles, images, np.arange(len(psg_angles)), extinction_ratio=0.0
            )

            np.testing.assert_allclose(matrix_mean, theory, atol=1e-6)

    def test_minimal_9_image_subset_reconstructs_to_identity(self):
        theory = np.eye(3)
        angles = [0.0, 60.0, 120.0]  # exactly 9 images, the 3x3 minimum

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            psg_angles, psa_angles, images, sample_name = tool.load_run(str(run_dir))
            matrix_mean, _condition_number = tool.reconstruct_subset(
                psg_angles, psa_angles, images, np.arange(len(psg_angles)), extinction_ratio=0.0
            )

            np.testing.assert_allclose(matrix_mean, theory, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
