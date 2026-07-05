"""Forward-model Mueller matrix for the 4x4 (fixed-polarizer + rotating-QWP)
polarization state generator/analyzer.

4x4 only: PSG_Polarizer/PSA_Analyzer are held fixed for the whole run (their
angles come from Config/experiment_config.json's "fixed_angles"), while
PSG_QWP/PSA_QWP rotate. Both the polarizer and the retarder Mueller matrices
are built directly from the physical rotation sandwich
M(theta) = R(-theta) @ M(0) @ R(theta) -- no closed-form shortcut.
"""

from __future__ import annotations

import numpy as np


def mueller_rotator(theta_deg: float) -> np.ndarray:
    """4x4 Mueller rotation matrix R(theta) for a reference-frame rotation."""

    t = np.deg2rad(theta_deg)
    c, s = np.cos(2 * t), np.sin(2 * t)
    return np.array([
        [1, 0, 0, 0],
        [0, c, s, 0],
        [0, -s, c, 0],
        [0, 0, 0, 1],
    ], dtype=np.float64)


def mueller_linear_polarizer(theta_deg: float, extinction_ratio: float = 0.0) -> np.ndarray:
    """Mueller matrix of the fixed linear polarizer/analyzer at theta_deg.

    extinction_ratio = Imin/Imax through the polarizer (0 = ideal, fully
    blocks the orthogonal axis). Pass a measured value here once you have one.
    """

    k = extinction_ratio
    m0 = 0.5 * np.array([
        [1 + k, 1 - k, 0, 0],
        [1 - k, 1 + k, 0, 0],
        [0, 0, 2 * np.sqrt(k), 0],
        [0, 0, 0, 2 * np.sqrt(k)],
    ], dtype=np.float64)
    return mueller_rotator(-theta_deg) @ m0 @ mueller_rotator(theta_deg)


def mueller_retarder(theta_deg: float, retardance_deg: float = 90.0) -> np.ndarray:
    """Mueller matrix of a linear retarder (the QWP) with fast axis at theta_deg.

    retardance_deg defaults to an ideal quarter-wave plate (90 deg). Pass a
    measured value here once you have one.
    """

    delta = np.deg2rad(retardance_deg)
    m0 = np.array([
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, np.cos(delta), np.sin(delta)],
        [0, 0, -np.sin(delta), np.cos(delta)],
    ], dtype=np.float64)
    return mueller_rotator(-theta_deg) @ m0 @ mueller_rotator(theta_deg)


def generator_stokes_4x4(
    psg_qwp_deg: float,
    psg_polarizer_fixed_deg: float,
    retardance_deg: float = 90.0,
    extinction_ratio: float = 0.0,
) -> np.ndarray:
    """Full Stokes vector from unpolarized light through the fixed polarizer,
    then the quarter-wave plate rotating at psg_qwp_deg."""

    s_in = np.array([1.0, 0.0, 0.0, 0.0])
    polarizer = mueller_linear_polarizer(psg_polarizer_fixed_deg, extinction_ratio)
    qwp = mueller_retarder(psg_qwp_deg, retardance_deg)
    return qwp @ polarizer @ s_in


def analyzer_vector_4x4(
    psa_qwp_deg: float,
    psa_analyzer_fixed_deg: float,
    retardance_deg: float = 90.0,
    extinction_ratio: float = 0.0,
) -> np.ndarray:
    """Row vector: intensity = analyzer_vector . Stokes_before_analyzer, for a
    rotating QWP followed by the fixed linear analyzer."""

    qwp = mueller_retarder(psa_qwp_deg, retardance_deg)
    analyzer = mueller_linear_polarizer(psa_analyzer_fixed_deg, extinction_ratio)
    return (analyzer @ qwp)[0, :]
