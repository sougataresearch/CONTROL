"""Regression test for reflection_theory.py's Fresnel/Airy/Jones-to-Mueller
physics (3x3 sub-block version), using known special cases with an
analytically predictable answer.

Run from INSIDE this folder:

    python -m unittest test_reflection_theory -v
"""

from __future__ import annotations

import unittest

import numpy as np

from reflection_theory import (
    airy_reflection,
    bare_substrate_mueller,
    fresnel_coefficients,
    film_on_substrate_mueller,
)


def _diattenuation(m3x3: np.ndarray) -> float:
    """Diattenuation straight from M's first row (S1,S2), same formula the
    4x4 polar_decomposition.py module uses -- duplicated here in miniature
    since 3x3 mode has no polar_decomposition.py of its own."""
    return float(np.linalg.norm(m3x3[0, 1:3]))


class ReflectionTheoryTests(unittest.TestCase):
    def test_brewster_angle_p_reflection_vanishes(self):
        n1, n2 = 1.0, 1.5
        theta_b = np.degrees(np.arctan(n2 / n1))

        r_s, r_p = fresnel_coefficients(complex(n1, 0), complex(n2, 0), theta_b)
        self.assertAlmostEqual(abs(r_p), 0.0, places=10)
        self.assertGreater(abs(r_s), 0.1)

    def test_bare_substrate_matrix_is_3x3_and_diattenuation_matches_brewster(self):
        n1, n2 = 1.0, 1.5
        theta_b = np.degrees(np.arctan(n2 / n1))

        m = bare_substrate_mueller(substrate_n=n2, substrate_k=0.0, angle_of_incidence_deg=theta_b)
        self.assertEqual(m.shape, (3, 3))
        self.assertAlmostEqual(m[0, 0], 1.0, places=10)
        # At Brewster's angle, only s-polarization reflects -- diattenuation is 1.
        self.assertAlmostEqual(_diattenuation(m), 1.0, places=8)

    def test_energy_conservation_for_lossless_dielectric(self):
        for theta in (0.0, 15.0, 45.0, 80.0):
            r_s, r_p = fresnel_coefficients(complex(1.0, 0), complex(1.5, 0), theta)
            self.assertGreaterEqual(abs(r_s) ** 2, 0.0)
            self.assertLessEqual(abs(r_s) ** 2, 1.0)
            self.assertGreaterEqual(abs(r_p) ** 2, 0.0)
            self.assertLessEqual(abs(r_p) ** 2, 1.0)

    def test_zero_thickness_film_reduces_to_bare_interface(self):
        theta, wavelength = 30.0, 632.8
        substrate_n, substrate_k = 3.88, 0.02
        film_n, film_k = 1.46, 0.0

        r_s_direct, r_p_direct = fresnel_coefficients(
            complex(1.0, 0), complex(substrate_n, -substrate_k), theta
        )
        r_s_thin, r_p_thin = airy_reflection(
            complex(1.0, 0), complex(film_n, -film_k), complex(substrate_n, -substrate_k),
            theta, wavelength, film_thickness_nm=1e-6,
        )
        self.assertAlmostEqual(r_s_thin.real, r_s_direct.real, places=5)
        self.assertAlmostEqual(r_p_thin.real, r_p_direct.real, places=5)

    def test_index_matched_film_is_optically_invisible(self):
        theta, wavelength = 25.0, 550.0
        n, k = 2.0, 0.05

        r_s_bare, r_p_bare = fresnel_coefficients(complex(1.0, 0), complex(n, -k), theta)
        for thickness in (10.0, 100.0, 500.0):
            r_s_film, r_p_film = airy_reflection(
                complex(1.0, 0), complex(n, -k), complex(n, -k), theta, wavelength, thickness
            )
            self.assertAlmostEqual(r_s_film.real, r_s_bare.real, places=6, msg=f"thickness={thickness}")
            self.assertAlmostEqual(r_p_film.real, r_p_bare.real, places=6, msg=f"thickness={thickness}")

    def test_film_on_substrate_matrix_is_3x3_and_normalized(self):
        m = film_on_substrate_mueller(
            substrate_n=3.88, substrate_k=0.02, film_n=1.46, film_k=0.0,
            film_thickness_nm=100.0, angle_of_incidence_deg=65.0, wavelength_nm=632.8,
        )
        self.assertEqual(m.shape, (3, 3))
        self.assertAlmostEqual(m[0, 0], 1.0, places=10)


if __name__ == "__main__":
    unittest.main()
