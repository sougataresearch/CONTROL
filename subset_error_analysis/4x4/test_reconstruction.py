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

import analyze_subsets_4x4 as tool

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
            s = tool.generator_stokes_4x4(psg, PSG_POLARIZER_FIXED, RETARDANCE_DEG, extinction_ratio)
            a = tool.analyzer_vector_4x4(psa, PSA_ANALYZER_FIXED, RETARDANCE_DEG, extinction_ratio)
            intensity = float(a @ (theory @ s))
            arr = np.full((2, 2), intensity * 100.0, dtype=np.float32)
            Image.fromarray(arr, mode="F").save(run_dir / "Images" / f"{psg:g}_{psa:g}.tiff")


class SyntheticReconstructionTests(unittest.TestCase):
    def test_full_grid_reconstructs_to_identity(self):
        theory = np.eye(4)
        angles = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0]  # 36 images, over-determined

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            psg_qwp, psa_qwp, images, fixed_angles, sample_name = tool.load_run(run_dir)
            self.assertEqual(sample_name, "air")
            matrix_mean = tool.reconstruct_subset(
                psg_qwp, psa_qwp, images, fixed_angles, np.arange(len(psg_qwp)),
                RETARDANCE_DEG, extinction_ratio=0.0
            )

            np.testing.assert_allclose(matrix_mean, theory, atol=1e-6)

    def test_discover_runs_finds_the_synthetic_run(self):
        theory = np.eye(4)
        angles = [0.0, 30.0, 60.0, 90.0]

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, angles)

            runs = tool.discover_runs(tmp)

            self.assertEqual(runs, [run_dir])


if __name__ == "__main__":
    unittest.main()
