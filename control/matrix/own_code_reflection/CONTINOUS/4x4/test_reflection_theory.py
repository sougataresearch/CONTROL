"""Regression test for reflection_theory.py's Fresnel/Airy/Jones-to-Mueller
physics, using known special cases with an analytically predictable answer
-- Brewster's angle, energy conservation, the thin-film-thickness-to-zero
limit, and the equal-index limit -- rather than trusting the formulas from
memory alone.

Run from INSIDE this folder:

    python -m unittest test_reflection_theory -v
"""

from __future__ import annotations

import unittest

import numpy as np

from polar_decomposition import depolarization_index, diattenuation
from reflection_theory import (
    airy_reflection,
    bare_substrate_mueller,
    fresnel_coefficients,
    film_on_substrate_mueller,
    jones_diagonal_to_mueller,
)


class ReflectionTheoryTests(unittest.TestCase):
    def test_brewster_angle_p_reflection_vanishes(self):
        # For a non-absorbing dielectric, r_p == 0 exactly at Brewster's
        # angle theta_B = arctan(n2/n1) -- a textbook, easily-checked case.
        n1, n2 = 1.0, 1.5
        theta_b = np.degrees(np.arctan(n2 / n1))

        r_s, r_p = fresnel_coefficients(complex(n1, 0), complex(n2, 0), theta_b)
        self.assertAlmostEqual(abs(r_p), 0.0, places=10)
        self.assertGreater(abs(r_s), 0.1)  # s-reflection should NOT vanish

        m = jones_diagonal_to_mueller(r_p, r_s)
        m = m / m[0, 0]
        _, d = diattenuation(m)
        # r_p=0 means only s-polarization reflects -- a perfectly polarizing
        # reflection, diattenuation should be exactly 1.
        self.assertAlmostEqual(float(d), 1.0, places=8)

    def test_lossless_dielectric_reflection_is_nondepolarizing(self):
        # A flat, non-absorbing, non-scattering interface is a deterministic
        # (Jones-describable) process -- its Mueller matrix must be "pure",
        # i.e. depolarization_index == 1, at any angle of incidence.
        for theta in (0.1, 20.0, 45.0, 70.0):
            m = bare_substrate_mueller(substrate_n=1.5, substrate_k=0.0,
                                        angle_of_incidence_deg=theta)
            self.assertAlmostEqual(float(depolarization_index(m)), 1.0, places=8,
                                    msg=f"theta={theta}")

    def test_energy_conservation_for_lossless_dielectric(self):
        # |r_s|^2 and |r_p|^2 (reflectances) must each be in [0, 1] for a
        # non-absorbing medium -- reflectance can't exceed the incident power.
        for theta in (0.0, 15.0, 45.0, 80.0):
            r_s, r_p = fresnel_coefficients(complex(1.0, 0), complex(1.5, 0), theta)
            self.assertGreaterEqual(abs(r_s) ** 2, 0.0)
            self.assertLessEqual(abs(r_s) ** 2, 1.0)
            self.assertGreaterEqual(abs(r_p) ** 2, 0.0)
            self.assertLessEqual(abs(r_p) ** 2, 1.0)

    def test_zero_thickness_film_reduces_to_bare_interface(self):
        # A film of ~zero thickness shouldn't be optically distinguishable
        # from going straight from air to the substrate -- the Airy formula
        # at film_thickness_nm -> 0 must match the direct two-medium Fresnel
        # result between air and the substrate.
        theta, wavelength = 30.0, 632.8
        substrate_n, substrate_k = 3.88, 0.02   # roughly silicon at 633nm
        film_n, film_k = 1.46, 0.0              # roughly SiO2, lossless

        r_s_direct, r_p_direct = fresnel_coefficients(
            complex(1.0, 0), complex(substrate_n, -substrate_k), theta
        )
        r_s_thin, r_p_thin = airy_reflection(
            complex(1.0, 0), complex(film_n, -film_k), complex(substrate_n, -substrate_k),
            theta, wavelength, film_thickness_nm=1e-6,
        )
        self.assertAlmostEqual(r_s_thin.real, r_s_direct.real, places=5)
        self.assertAlmostEqual(r_s_thin.imag, r_s_direct.imag, places=5)
        self.assertAlmostEqual(r_p_thin.real, r_p_direct.real, places=5)
        self.assertAlmostEqual(r_p_thin.imag, r_p_direct.imag, places=5)

    def test_index_matched_film_is_optically_invisible(self):
        # If the "film" has the exact same index as the substrate, there's
        # no second interface to speak of -- Airy's result must match the
        # bare single-interface Fresnel result between air and that index,
        # regardless of the (physically meaningless, since no contrast)
        # thickness value.
        theta, wavelength = 25.0, 550.0
        n, k = 2.0, 0.05

        r_s_bare, r_p_bare = fresnel_coefficients(complex(1.0, 0), complex(n, -k), theta)
        for thickness in (10.0, 100.0, 500.0):
            r_s_film, r_p_film = airy_reflection(
                complex(1.0, 0), complex(n, -k), complex(n, -k), theta, wavelength, thickness
            )
            self.assertAlmostEqual(r_s_film.real, r_s_bare.real, places=6,
                                    msg=f"thickness={thickness}")
            self.assertAlmostEqual(r_p_film.real, r_p_bare.real, places=6,
                                    msg=f"thickness={thickness}")

    def test_film_on_substrate_mueller_is_normalized_and_nondepolarizing(self):
        m = film_on_substrate_mueller(
            substrate_n=3.88, substrate_k=0.02, film_n=1.46, film_k=0.0,
            film_thickness_nm=100.0, angle_of_incidence_deg=65.0, wavelength_nm=632.8,
        )
        self.assertAlmostEqual(m[0, 0], 1.0, places=10)
        self.assertAlmostEqual(float(depolarization_index(m)), 1.0, places=8)


if __name__ == "__main__":
    unittest.main()
