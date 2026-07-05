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
frames (plus the Logs/experiment_log.csv this pipeline actually reads
angles from) for a *known* theoretical sample (air -> identity matrix) by
computing exactly what the camera should record at each angle if that were
the true sample, feeds them through the real image_loader.load_run() and
solve_mueller.reconstruct(), and asserts the reconstruction recovers the
identity matrix to near-zero error. No real camera or hardware involved --
this is pure math in, pure math out.
"""

from __future__ import annotations

import csv
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


def _write_synthetic_run(run_dir: Path, theory: np.ndarray, psg_angles: list, psa_angles: list,
                          extinction_ratio: float = 0.0) -> None:
    (run_dir / "Config").mkdir(parents=True)
    (run_dir / "Images").mkdir(parents=True)
    (run_dir / "Logs").mkdir(parents=True)
    config = {
        "mode": "4x4",
        "fixed_angles": {"PSG_Polarizer": PSG_POLARIZER_FIXED, "PSA_Analyzer": PSA_ANALYZER_FIXED},
    }
    (run_dir / "Config" / "experiment_config.json").write_text(json.dumps(config), encoding="utf-8")

    log_path = run_dir / "Logs" / "experiment_log.csv"
    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Frame Index", "Status", "PSG_QWP Angle", "PSA_QWP Angle"])
        writer.writeheader()
        for i, (psg, psa) in enumerate(zip(psg_angles, psa_angles)):
            s = generator_stokes_4x4(psg, PSG_POLARIZER_FIXED, RETARDANCE_DEG, extinction_ratio)
            a = analyzer_vector_4x4(psa, PSA_ANALYZER_FIXED, RETARDANCE_DEG, extinction_ratio)
            intensity = float(a @ (theory @ s))
            arr = np.full((2, 2), intensity * 100.0, dtype=np.float32)
            Image.fromarray(arr, mode="F").save(
                run_dir / "Images" / f"frame_{i:04d}_psg{psg:.1f}_psa{psa:.1f}.tiff"
            )
            writer.writerow({
                "Frame Index": i, "Status": "SUCCESS",
                "PSG_QWP Angle": psg, "PSA_QWP Angle": psa,
            })


class SyntheticReconstructionTests(unittest.TestCase):
    def test_air_reconstructs_to_identity(self):
        theory = np.eye(4)
        # A continuous sweep's angles are naturally non-grid/well-distributed
        # (see module docstring); 60 samples over one revolution with a 1:5
        # PSG:PSA ratio (the classic dual-rotating-retarder scheme) mimics
        # that here.
        n = 60
        psg_angles = [360.0 * i / n for i in range(n)]
        psa_angles = [5.0 * angle for angle in psg_angles]

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "air"
            _write_synthetic_run(run_dir, theory, psg_angles, psa_angles)

            run = load_run(run_dir)
            result = reconstruct(run, extinction_ratio=0.0, retardance_deg=RETARDANCE_DEG)

            np.testing.assert_allclose(result.matrix_mean, theory, atol=1e-5)


if __name__ == "__main__":
    unittest.main()
