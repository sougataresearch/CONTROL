"""Regression test for this folder's physics + reconstruction, using
synthetic (not real) images.

Run from INSIDE this folder:

    python -m unittest test_reconstruction -v

Why this exists: the fixed-polarizer + rotating-QWP rotation-sandwich
physics in mueller_forward_model.py is duplicated (by design, no shared
code) across every own_code/ mode and both angle_subset_comparison/
subset_error_analysis tools. A bug introduced in this copy would otherwise
only surface as a subtly-wrong Mueller matrix on a real capture, with
nothing to flag it. This test catches that immediately: it fabricates
images for a *known* theoretical sample (air -> identity matrix) by
computing exactly what the camera should record at each angle if that were
the true sample, feeds them through the real image_loader.load_run() and
solve_mueller.reconstruct(), and asserts the reconstruction recovers the
identity matrix to near-zero error. No real camera or hardware involved --
this is pure math in, pure math out.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from image_loader import load_run
from mueller_forward_model import analyzer_vector_4x4, generator_stokes_4x4
from solve_mueller import reconstruct

PSG_POLARIZER_FIXED = 5.0
PSA_ANALYZER_FIXED = -3.0
RETARDANCE_DEG = 90.0


def _write_synthetic_run(run_dir: Path, theory: np.ndarray, angles: list,
                          extinction_ratio: float = 0.0) -> None:
    (run_dir / "Config").mkdir(parents=True)
    (run_dir / "Images").mkdir(parents=True)
    config = {
        "mode": "4x4",
        "fixed_angles": {"PSG_Polarizer": PSG_POLARIZER_FIXED, "PSA_Analyzer": PSA_ANALYZER_FIXED},
    }
    (run_dir / "Config" / "experiment_config.json").write_text(json.dumps(config), encoding="utf-8")

    for psg in angles:
        for psa in angles:
            s = generator_stokes_4x4(psg, PSG_POLARIZER_FIXED, RETARDANCE_DEG, extinction_ratio)
            a = analyzer_vector_4x4(psa, PSA_ANALYZER_FIXED, RETARDANCE_DEG, extinction_ratio)
            intensity = float(a @ (theory @ s))
            arr = np.full((2, 2), intensity * 100.0, dtype=np.float32)
            Image.fromarray(arr, mode="F").save(run_dir / "Images" / f"{psg:g}_{psa:g}.tiff")


class SyntheticReconstructionTests(unittest.TestCase):
    def test_air_reconstructs_to_identity(self):
        theory = np.eye(4)
        # exactly 16 images, the 4x4 minimum. NOTE: angles evenly spaced by
        # 45 degrees (e.g. [0,45,90,135]) make the system matrix rank-deficient
        # for this fixed-polarizer+rotating-QWP model -- the QWP's Mueller
        # terms only depend on cos(2*theta)/sin(2*theta), and samples spaced
        # 90 degrees apart in that doubled angle alias each other, losing rank
        # (confirmed: rank 9/16 instead of 16/16). Avoid that spacing here and
        # in any real 4x4 acquisition plan.
        angles = [0.0, 30.0, 60.0, 90.0]

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            run = load_run(run_dir)
            result = reconstruct(run, extinction_ratio=0.0, retardance_deg=RETARDANCE_DEG)

            # Looser tolerance than the over-determined case below: this
            # grid's higher condition number (~640, vs ~2 for the 6x6 grid)
            # amplifies the synthetic images' float32-TIFF storage precision
            # (~1e-7 relative) by a couple of orders of magnitude -- still
            # many orders tighter than any real bug would produce.
            np.testing.assert_allclose(result.matrix_mean, theory, atol=1e-4)

    def test_overdetermined_grid_still_reconstructs_to_identity(self):
        theory = np.eye(4)
        angles = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0]  # 36 images, over-determined

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            run = load_run(run_dir)
            result = reconstruct(run, extinction_ratio=0.0, retardance_deg=RETARDANCE_DEG)

            np.testing.assert_allclose(result.matrix_mean, theory, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
