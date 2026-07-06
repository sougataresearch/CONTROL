"""Generalized least-squares 3x3 Mueller matrix reconstruction from N images.

Takes however many PSG/PSA angle-pair images a run actually has -- 9, 36,
49, whatever -- with no assumption about the specific count or angles. One
system-matrix row per image, built from the real rotation-sandwich physics
in mueller_forward_model.py; solved per pixel in a single vectorized
least-squares fit.

Works for any sample (air, a polarizer at any angle, a QWP, tissue, ...) --
the sample's identity never enters this code, only the known PSG/PSA angles
and the measured intensities. Note the physics limitation this inherits:
3x3 mode only generates/analyzes linear polarization states, so it can only
recover the S0,S1,S2 sub-block of a sample's true Mueller matrix.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from image_loader import RunImages3x3, load_run
from mueller_forward_model import analyzer_vector_3x3, generator_stokes_3x3


@dataclass
class MuellerResult3x3:
    matrix: np.ndarray          # (H, W, 3, 3) per-pixel Mueller matrix, normalized by m00
    matrix_raw: np.ndarray      # (H, W, 3, 3) before m00 normalization
    matrix_mean: np.ndarray     # (3, 3) spatial average of the normalized matrix
    condition_number: float    # conditioning of the system matrix (diagnostic)
    residual_rms: np.ndarray   # (H, W) RMS fit residual per pixel (diagnostic)


def _build_system_matrix(run: RunImages3x3, extinction_ratio: float) -> np.ndarray:
    """One row per image: kron(analyzer_vector, generator_stokes)."""

    n = len(run.files)
    rows = np.empty((n, 9), dtype=np.float64)
    for k in range(n):
        s = generator_stokes_3x3(run.psg_angles[k], extinction_ratio)
        a = analyzer_vector_3x3(run.psa_angles[k], extinction_ratio)
        rows[k] = np.kron(a, s)
    return rows


def reconstruct(run: RunImages3x3, extinction_ratio: float = 0.0) -> MuellerResult3x3:
    """extinction_ratio defaults to an ideal polarizer (0); pass a measured
    value once you have one."""

    h = _build_system_matrix(run, extinction_ratio)
    h_pinv = np.linalg.pinv(h)
    condition_number = np.linalg.cond(h)

    n, height, width = run.images.shape
    b = run.images.reshape(n, -1)          # (N, H*W)
    m_vec = h_pinv @ b                     # (9, H*W)
    residual = h @ m_vec - b               # (N, H*W)
    residual_rms = np.sqrt(np.mean(residual ** 2, axis=0)).reshape(height, width)

    matrix_raw = m_vec.T.reshape(height, width, 3, 3)
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

    return MuellerResult3x3(matrix, matrix_raw, matrix_mean, condition_number, residual_rms)


def reconstruct_run(run_dir: str, extinction_ratio: float = 0.0) -> MuellerResult3x3:
    run = load_run(run_dir)
    return reconstruct(run, extinction_ratio)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python solve_mueller.py <run_directory> [extinction_ratio]")
        raise SystemExit(1)

    ext = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    result = reconstruct_run(sys.argv[1], ext)
    np.set_printoptions(precision=4, suppress=True)
    print(f"System matrix condition number: {result.condition_number:.3f}")
    print(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}")
    print("Mean Mueller matrix (spatial average, normalized by m00):")
    print(result.matrix_mean)
