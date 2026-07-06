"""Phase 4: reduce a reconstructed 4x4 Mueller matrix to a handful of
physically-meaningful scalar/vector diagnostics -- diattenuation,
polarizance, and a depolarization index -- rather than leaving you to read
16 raw numbers per pixel by eye.

Works on a single (4, 4) matrix (e.g. solve_mueller's matrix_mean) or a
whole per-pixel stack of shape (..., 4, 4) (e.g. solve_mueller's matrix) --
every function here is written with NumPy's leading "..." batch dimensions,
so it generalizes to any pixel grid with no special-casing.

What's implemented, and why these specific quantities:

- Diattenuation and polarizance are read directly off M's first row/column
  (see the own_code README's explanation of what M's rows/columns mean
  physically) -- no decomposition needed, low risk of a subtle formula
  error, and already exactly what NAMING.md/the README describe.

- The depolarization index uses the Gil-Bernabeu (1985) definition
  P_delta(M) = sqrt((||M||_F^2 - m00^2) / (3 * m00^2)), which ranges from 0
  (fully depolarizing) to 1 (a "pure"/deterministic Mueller-Jones matrix,
  whether or not it also diattenuates) -- chosen over the fuller Lu-Chipman
  eigenvalue-based index because it's a single closed-form expression with
  no decomposition/inversion step, so there's less room for a sign or
  convention mistake to creep in. Verified in test_polar_decomposition.py
  against known cases (identity, ideal polarizer, ideal retarder, and a
  diagonal partial depolarizer with hand-computed expected values).

- Retardance is estimated by removing only the diattenuation (via the
  Lu-Chipman diattenuator matrix and its inverse) and then reading the
  trace of what remains -- exact for a diattenuator-then-linear-retarder
  sample with NO depolarization (verified in the test), but only an
  approximation once real depolarization is present, since removing a
  depolarizer's effect on the trace would require the full eigenvalue-based
  decomposition this module deliberately doesn't implement. Treat this
  number as indicative, not exact, once depolarization_index is well below 1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_IDENTITY_3 = np.eye(3)


@dataclass
class PolarDecomposition:
    diattenuation_vector: np.ndarray   # (..., 3)
    diattenuation: np.ndarray          # (...)      scalar per pixel/matrix
    polarizance_vector: np.ndarray     # (..., 3)
    polarizance: np.ndarray            # (...)
    depolarization_index: np.ndarray   # (...)      1 = non-depolarizing, 0 = fully depolarizing
    retardance_deg: np.ndarray         # (...)      see module docstring's accuracy caveat


def diattenuation(matrix: np.ndarray):
    """Diattenuation vector/scalar straight from M's first row (excluding
    m00), normalized by m00. See the own_code README: this alone determines
    how much the sample's transmitted brightness depends on input
    polarization angle."""

    m00 = matrix[..., 0, 0]
    d_vector = matrix[..., 0, 1:4] / m00[..., None]
    d_scalar = np.linalg.norm(d_vector, axis=-1)
    return d_vector, d_scalar


def polarizance(matrix: np.ndarray):
    """Polarizance vector/scalar straight from M's first column (excluding
    m00), normalized by m00. See the own_code README: this alone determines
    how strongly the sample imposes its own polarization on originally-
    unpolarized light."""

    m00 = matrix[..., 0, 0]
    p_vector = matrix[..., 1:4, 0] / m00[..., None]
    p_scalar = np.linalg.norm(p_vector, axis=-1)
    return p_vector, p_scalar


def depolarization_index(matrix: np.ndarray) -> np.ndarray:
    """Gil-Bernabeu depolarization index: 1 for a non-depolarizing
    ("pure"/deterministic) Mueller matrix, down to 0 for one that fully
    scrambles polarization into unpolarized light. See the module docstring
    for why this formula (not the Lu-Chipman eigenvalue-based one) was
    chosen, and test_polar_decomposition.py for the hand-verified cases."""

    m00 = matrix[..., 0, 0]
    frobenius_sq = np.sum(matrix ** 2, axis=(-2, -1))
    ratio = (frobenius_sq - m00 ** 2) / (3 * m00 ** 2)
    return np.sqrt(np.clip(ratio, 0.0, 1.0))


def _diattenuator_matrix(d_vector: np.ndarray, d_scalar: np.ndarray) -> np.ndarray:
    """Build the 4x4 Lu-Chipman diattenuator matrix M_D for a batch of
    diattenuation vectors -- the homogeneous-diattenuator form, matching
    mueller_linear_polarizer()'s own structure (same reference this
    project's forward model already uses)."""

    d_safe = np.clip(d_scalar, 0.0, 0.999999)
    d_hat = np.divide(d_vector, d_scalar[..., None], out=np.zeros_like(d_vector),
                       where=(d_scalar[..., None] > 0))
    m_d_scalar = np.sqrt(np.clip(1.0 - d_safe ** 2, 0.0, 1.0))

    outer = d_hat[..., :, None] * d_hat[..., None, :]
    sub = (m_d_scalar[..., None, None] * _IDENTITY_3
           + (1.0 - m_d_scalar[..., None, None]) * outer)

    batch_shape = d_vector.shape[:-1]
    m_diattenuator = np.zeros(batch_shape + (4, 4))
    m_diattenuator[..., 0, 0] = 1.0
    m_diattenuator[..., 0, 1:4] = d_vector
    m_diattenuator[..., 1:4, 0] = d_vector
    m_diattenuator[..., 1:4, 1:4] = sub
    return m_diattenuator


def estimate_retardance_deg(matrix: np.ndarray) -> np.ndarray:
    """Estimate total retardance by removing only the diattenuator
    component and reading the trace of what's left. Exact for a
    diattenuator-then-linear-retarder sample with no depolarization; an
    approximation once depolarization_index is well below 1 -- see the
    module docstring."""

    m00 = matrix[..., 0, 0]
    normalized = matrix / m00[..., None, None]

    d_vector, d_scalar = diattenuation(normalized)
    m_diattenuator = _diattenuator_matrix(d_vector, d_scalar)
    m_diattenuator_inv = np.linalg.inv(m_diattenuator)

    remainder = normalized @ m_diattenuator_inv
    trace = np.trace(remainder, axis1=-2, axis2=-1)
    cos_delta = np.clip(trace / 2.0 - 1.0, -1.0, 1.0)
    return np.degrees(np.arccos(cos_delta))


def decompose(matrix: np.ndarray) -> PolarDecomposition:
    """All four diagnostics at once, for a single (4,4) matrix or a whole
    (..., 4, 4) per-pixel stack."""

    d_vector, d_scalar = diattenuation(matrix)
    p_vector, p_scalar = polarizance(matrix)
    return PolarDecomposition(
        diattenuation_vector=d_vector,
        diattenuation=d_scalar,
        polarizance_vector=p_vector,
        polarizance=p_scalar,
        depolarization_index=depolarization_index(matrix),
        retardance_deg=estimate_retardance_deg(matrix),
    )
