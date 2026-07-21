"""Generalized least-squares 4x4 Mueller matrix reconstruction from N images.

Takes however many PSG_QWP/PSA_QWP angle-pair images a run actually has --
16, 49, 144, whatever -- with no assumption about the specific count or
angles. One system-matrix row per image, built from the real
rotation-sandwich physics in mueller_forward_model.py (fixed polarizer +
rotating QWP, matching generate_4x4_discrete); solved per pixel in a single
vectorized least-squares fit.

Works for any sample (air, a polarizer, a QWP at any angle, tissue, ...) --
the sample's identity never enters this code, only the known fixed/rotating
PSG/PSA angles and the measured intensities.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from image_loader import RunImages4x4, load_run
from mueller_forward_model import analyzer_vector_4x4, generator_stokes_4x4


@dataclass
class MuellerResult4x4:
    matrix: np.ndarray          # (H, W, 4, 4) per-pixel Mueller matrix, normalized by m00
    matrix_raw: np.ndarray      # (H, W, 4, 4) before m00 normalization
    matrix_mean: np.ndarray     # (4, 4) spatial average of the normalized matrix
    condition_number: float    # conditioning of the system matrix (diagnostic)
    residual_rms: np.ndarray   # (H, W) RMS fit residual per pixel (diagnostic)


def _build_system_matrix(
    run: RunImages4x4,
    extinction_ratio: float,
    retardance_deg: float,
) -> np.ndarray:
    """One row per image: kron(analyzer_vector, generator_stokes)."""

    n = len(run.files)
    psg_fixed = run.fixed_angles["PSG_Polarizer"]
    psa_fixed = run.fixed_angles["PSA_Analyzer"]
    rows = np.empty((n, 16), dtype=np.float64)
    for k in range(n):
        s = generator_stokes_4x4(run.psg_qwp_angles[k], psg_fixed, retardance_deg, extinction_ratio)
        a = analyzer_vector_4x4(run.psa_qwp_angles[k], psa_fixed, retardance_deg, extinction_ratio)
        rows[k] = np.kron(a, s)
    return rows


def reconstruct(
    run: RunImages4x4,
    extinction_ratio: float = 0.0,
    retardance_deg: float = 90.0,
) -> MuellerResult4x4:
    """extinction_ratio/retardance_deg default to ideal optics; pass measured
    values once you have calibration numbers for your actual polarizers/QWPs."""

    h = _build_system_matrix(run, extinction_ratio, retardance_deg)
    h_pinv = np.linalg.pinv(h)
    condition_number = np.linalg.cond(h)

    n, height, width = run.images.shape
    b = run.images.reshape(n, -1)          # (N, H*W)
    m_vec = h_pinv @ b                     # (16, H*W)
    residual = h @ m_vec - b               # (N, H*W)
    residual_rms = np.sqrt(np.mean(residual ** 2, axis=0)).reshape(height, width)

    matrix_raw = m_vec.T.reshape(height, width, 4, 4)
    m00 = matrix_raw[:, :, 0, 0]
    m00_safe = np.where(m00 == 0, 1e-12, m00)
    matrix = matrix_raw / m00_safe[:, :, None, None]

    # matrix_mean is the average RAW matrix, normalized once -- not the
    # average of the per-pixel normalized "matrix" above. A handful of
    # pixels can have a raw m00 near zero (real sensor/reconstruction
    # noise); normalizing each pixel individually before averaging lets
    # those few pixels' division blow-ups dominate the mean by many orders
    # of magnitude. Averaging raw values first is naturally robust to that.
    matrix_mean_raw = matrix_raw.mean(axis=(0, 1))
    matrix_mean = matrix_mean_raw / matrix_mean_raw[0, 0]

    return MuellerResult4x4(matrix, matrix_raw, matrix_mean, condition_number, residual_rms)


def reconstruct_run(
    run_dir: str,
    extinction_ratio: float = 0.0,
    retardance_deg: float = 90.0,
) -> MuellerResult4x4:
    run = load_run(run_dir)
    return reconstruct(run, extinction_ratio, retardance_deg)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python solve_mueller.py <run_directory> [extinction_ratio] [retardance_deg]")
        raise SystemExit(1)

    ext = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    ret = float(sys.argv[3]) if len(sys.argv) > 3 else 90.0
    result = reconstruct_run(sys.argv[1], ext, ret)
    np.set_printoptions(precision=4, suppress=True)
    print(f"System matrix condition number: {result.condition_number:.3f}")
    print(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}")
    print("Mean Mueller matrix (spatial average, normalized by m00):")
    print(result.matrix_mean)
