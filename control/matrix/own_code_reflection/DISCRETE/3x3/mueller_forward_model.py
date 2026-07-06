"""Forward-model Mueller matrix for an ideal rotated linear polarizer.

3x3 only: PSG_Polarizer and PSA_Analyzer are the only optical elements
involved (no QWP), so this file only ever deals with linear polarization
states. Built directly from the physical rotation sandwich
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
    """Mueller matrix of a linear polarizer with transmission axis at theta_deg.

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


def generator_stokes_3x3(psg_polarizer_deg: float, extinction_ratio: float = 0.0) -> np.ndarray:
    """(S0,S1,S2) produced by unpolarized light through a rotating linear polarizer."""

    s_in = np.array([1.0, 0.0, 0.0, 0.0])
    return (mueller_linear_polarizer(psg_polarizer_deg, extinction_ratio) @ s_in)[:3]


def analyzer_vector_3x3(psa_analyzer_deg: float, extinction_ratio: float = 0.0) -> np.ndarray:
    """Row vector over (S0,S1,S2): intensity = analyzer_vector . Stokes_before_analyzer."""

    return mueller_linear_polarizer(psa_analyzer_deg, extinction_ratio)[0, :3]
