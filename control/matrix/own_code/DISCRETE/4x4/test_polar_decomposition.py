"""Regression test for polar_decomposition.py, using synthetic Mueller
matrices built directly from mueller_forward_model.py's own functions, where
the "correct" diattenuation/polarizance/depolarization/retardance answer is
known ahead of time by construction.

Run from INSIDE this folder:

    python -m unittest test_polar_decomposition -v

Why this exists: polar decomposition formulas are notorious for subtle sign
and convention differences between sources. Rather than trust a formula
copied from memory, each case here builds a Mueller matrix with a KNOWN
diattenuation/retardance/depolarization baked in (using this project's own
mueller_linear_polarizer()/mueller_retarder(), or a hand-built diagonal
depolarizer) and asserts polar_decomposition.py recovers that exact value.
"""

from __future__ import annotations

import unittest

import numpy as np

from mueller_forward_model import mueller_linear_polarizer, mueller_retarder
from polar_decomposition import (
    decompose,
    depolarization_index,
    diattenuation,
    estimate_retardance_deg,
    polarizance,
)


class PolarDecompositionTests(unittest.TestCase):
    def test_identity_is_nondepolarizing_with_no_diattenuation(self):
        result = decompose(np.eye(4))
        self.assertAlmostEqual(result.diattenuation, 0.0, places=10)
        self.assertAlmostEqual(result.polarizance, 0.0, places=10)
        self.assertAlmostEqual(result.depolarization_index, 1.0, places=10)

    def test_pure_diattenuator_diattenuation_matches_formula(self):
        k = 0.1  # extinction ratio
        theta = 20.0
        m = mueller_linear_polarizer(theta, extinction_ratio=k)
        m = m / m[0, 0]  # normalize, as solve_mueller always does before this module sees it

        expected_d = (1 - k) / (1 + k)

        d_vector, d_scalar = diattenuation(m)
        p_vector, p_scalar = polarizance(m)

        self.assertAlmostEqual(d_scalar, expected_d, places=8)
        # A homogeneous linear diattenuator imparts exactly its own
        # diattenuation as polarizance on unpolarized light -- both should match.
        self.assertAlmostEqual(p_scalar, expected_d, places=8)
        np.testing.assert_allclose(d_vector, p_vector, atol=1e-8)

        # An ideal (non-depolarizing) diattenuator is still "pure": index should be 1.
        self.assertAlmostEqual(depolarization_index(m), 1.0, places=8)

    def test_pure_retarder_has_no_diattenuation_and_correct_retardance(self):
        for theta, retardance in [(0.0, 90.0), (37.0, 90.0), (15.0, 120.0)]:
            m = mueller_retarder(theta, retardance_deg=retardance)

            d_vector, d_scalar = diattenuation(m)
            p_vector, p_scalar = polarizance(m)

            self.assertAlmostEqual(d_scalar, 0.0, places=10,
                                    msg=f"theta={theta}, retardance={retardance}")
            self.assertAlmostEqual(p_scalar, 0.0, places=10,
                                    msg=f"theta={theta}, retardance={retardance}")
            self.assertAlmostEqual(depolarization_index(m), 1.0, places=8,
                                    msg=f"theta={theta}, retardance={retardance}")

            recovered = estimate_retardance_deg(m)
            self.assertAlmostEqual(float(recovered), retardance, places=6,
                                    msg=f"theta={theta}, retardance={retardance}")

    def test_diattenuator_then_retarder_no_depolarization_exact_retardance(self):
        # Composite sample: a linear retarder acting after a linear
        # diattenuator (M = M_R @ M_D), with NO depolarization -- the case
        # estimate_retardance_deg() is documented as exact for.
        k, theta_d = 0.05, 25.0
        retardance, theta_r = 100.0, -40.0

        m_d = mueller_linear_polarizer(theta_d, extinction_ratio=k)
        m_r = mueller_retarder(theta_r, retardance_deg=retardance)
        m = m_r @ m_d
        m = m / m[0, 0]

        recovered_retardance = estimate_retardance_deg(m)
        self.assertAlmostEqual(float(recovered_retardance), retardance, places=4)

        expected_d = (1 - k) / (1 + k)
        _, d_scalar = diattenuation(m)
        self.assertAlmostEqual(d_scalar, expected_d, places=6)

        # Still non-depolarizing (both components are deterministic/pure).
        self.assertAlmostEqual(depolarization_index(m), 1.0, places=6)

    def test_diagonal_depolarizer_matches_hand_computed_index(self):
        a1, a2, a3 = 0.8, 0.6, 0.4
        m = np.diag([1.0, a1, a2, a3])

        d_vector, d_scalar = diattenuation(m)
        p_vector, p_scalar = polarizance(m)
        self.assertAlmostEqual(d_scalar, 0.0, places=10)
        self.assertAlmostEqual(p_scalar, 0.0, places=10)

        # Hand-derived from the Gil-Bernabeu formula for this diagonal case:
        # ||M||_F^2 = 1 + a1^2 + a2^2 + a3^2, m00^2 = 1, so
        # index = sqrt((a1^2+a2^2+a3^2) / 3).
        expected_index = np.sqrt((a1 ** 2 + a2 ** 2 + a3 ** 2) / 3.0)
        self.assertAlmostEqual(depolarization_index(m), expected_index, places=10)
        self.assertLess(depolarization_index(m), 1.0)

    def test_vectorized_over_a_per_pixel_grid(self):
        # decompose() must work identically whether given a single (4,4)
        # matrix or a stack of them -- this is the "any pixel grid"
        # requirement from Phase 4's original spec.
        k, theta = 0.1, 20.0
        single = mueller_linear_polarizer(theta, extinction_ratio=k)
        single = single / single[0, 0]

        height, width = 3, 5
        stack = np.broadcast_to(single, (height, width, 4, 4)).copy()

        result_single = decompose(single)
        result_stack = decompose(stack)

        self.assertEqual(result_stack.diattenuation.shape, (height, width))
        np.testing.assert_allclose(
            result_stack.diattenuation, result_single.diattenuation, atol=1e-10
        )
        np.testing.assert_allclose(
            result_stack.depolarization_index, result_single.depolarization_index, atol=1e-10
        )


if __name__ == "__main__":
    unittest.main()
