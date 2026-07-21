"""Validates theory.py's models/fits against synthetic data with KNOWN
baked-in coefficients, before any of it is trusted with real captured
frames -- same approach as control/matrix/*/test_*.py throughout this repo."""

from __future__ import annotations

import unittest

import numpy as np

from theory import (
    case1_model, fit_case1,
    case23_model, fit_case23,
    case4_model, fit_case4, case4_ratio_comparison, IDEAL_CASE4_COEFFS,
)


class TestCase1(unittest.TestCase):
    def test_recovers_known_stokes_parameters(self):
        S0, S1, S2 = 200.0, 150.0, -40.0
        theta = np.arange(0, 360, 5.0)
        intensity = case1_model(theta, S0, S1, S2)

        fit = fit_case1(theta, intensity)

        self.assertAlmostEqual(fit.S0, S0, places=6)
        self.assertAlmostEqual(fit.S1, S1, places=6)
        self.assertAlmostEqual(fit.S2, S2, places=6)
        self.assertLess(fit.residual_rms, 1e-8)

    def test_extinction_ratio_and_phase_for_a_known_near_ideal_polarizer(self):
        # A polarizer with a small but nonzero extinction ratio, phase
        # offset baked in at exactly 15 degrees.
        S0 = 100.0
        theta0_deg = 15.0
        amp = 95.0  # close to S0 -> high but finite extinction ratio
        S1 = amp * np.cos(np.deg2rad(2 * theta0_deg))
        S2 = amp * np.sin(np.deg2rad(2 * theta0_deg))

        theta = np.arange(0, 360, 5.0)
        intensity = case1_model(theta, S0, S1, S2)
        fit = fit_case1(theta, intensity)

        expected_extinction_ratio = (S0 + amp) / (S0 - amp)
        self.assertAlmostEqual(fit.extinction_ratio, expected_extinction_ratio, places=4)
        self.assertAlmostEqual(fit.theta0_deg, theta0_deg, places=4)

    def test_rejects_unphysical_fit_gracefully(self):
        # Degenerate/insufficient data (all zeros) can drive S0 <= amplitude;
        # confirm this raises a clear error rather than returning a bogus
        # negative extinction ratio silently.
        theta = np.arange(0, 360, 5.0)
        intensity = np.zeros_like(theta)
        with self.assertRaises(ValueError):
            fit_case1(theta, intensity)


class TestCase23(unittest.TestCase):
    def test_recovers_known_coefficients(self):
        a0, a2, b2, a4, b4 = 300.0, 12.0, -8.0, 120.0, 45.0
        theta = np.arange(0, 360, 10.0)
        intensity = case23_model(theta, a0, a2, b2, a4, b4)

        fit = fit_case23(theta, intensity)

        self.assertAlmostEqual(fit.a0, a0, places=6)
        self.assertAlmostEqual(fit.a2, a2, places=6)
        self.assertAlmostEqual(fit.b2, b2, places=6)
        self.assertAlmostEqual(fit.a4, a4, places=6)
        self.assertAlmostEqual(fit.b4, b4, places=6)
        self.assertLess(fit.residual_rms, 1e-8)

    def test_ideal_alignment_gives_near_zero_second_harmonic(self):
        # a2 = b2 = 0 baked in -- the ideal-alignment prediction.
        a0, a4, b4 = 300.0, 120.0, 45.0
        theta = np.arange(0, 360, 10.0)
        intensity = case23_model(theta, a0, 0.0, 0.0, a4, b4)

        fit = fit_case23(theta, intensity)

        self.assertAlmostEqual(fit.second_harmonic_magnitude, 0.0, places=6)

    def test_misalignment_shows_up_as_nonzero_second_harmonic(self):
        a0, a2, b2, a4, b4 = 300.0, 25.0, 0.0, 120.0, 45.0
        theta = np.arange(0, 360, 10.0)
        intensity = case23_model(theta, a0, a2, b2, a4, b4)

        fit = fit_case23(theta, intensity)

        self.assertGreater(fit.second_harmonic_magnitude, 20.0)


class TestCase4(unittest.TestCase):
    def test_recovers_known_coefficients_at_default_3deg_step(self):
        a0, a2, a4, a6, a8, a10 = 250.0, 50.0, -100.0, 100.0, 50.0, 50.0
        theta = np.arange(0, 360, 3.0)  # matches the default Case 4 step size
        intensity = case4_model(theta, a0, a2, a4, a6, a8, a10)

        fit = fit_case4(theta, intensity)

        self.assertAlmostEqual(fit.a0, a0, places=5)
        self.assertAlmostEqual(fit.a2, a2, places=5)
        self.assertAlmostEqual(fit.a4, a4, places=5)
        self.assertAlmostEqual(fit.a6, a6, places=5)
        self.assertAlmostEqual(fit.a8, a8, places=5)
        self.assertAlmostEqual(fit.a10, a10, places=5)
        self.assertLess(fit.residual_rms, 1e-6)

    def test_coarser_than_bare_nyquist_step_fails_to_recover_coefficients(self):
        # 15 degrees gives only 18/15 = 1.2 samples per period of the a10
        # (cos 20t) term (period 18 deg) -- below the 2-samples-per-period
        # Nyquist minimum, so this under-samples badly. This test exists to
        # prove the 3 deg default in sweep.py actually matters, not just to
        # exercise the fitter.
        a0, a2, a4, a6, a8, a10 = 250.0, 50.0, -100.0, 100.0, 50.0, 50.0
        theta = np.arange(0, 360, 15.0)
        intensity = case4_model(theta, a0, a2, a4, a6, a8, a10)

        fit = fit_case4(theta, intensity)

        # At least one coefficient should fail to recover correctly under
        # aliasing -- this is a sanity check on the Nyquist claim in the
        # README, not a requirement on any specific coefficient.
        recovered = [fit.a0, fit.a2, fit.a4, fit.a6, fit.a8, fit.a10]
        truth = [a0, a2, a4, a6, a8, a10]
        mismatches = [abs(r - t) > 1.0 for r, t in zip(recovered, truth)]
        self.assertTrue(any(mismatches))

    def test_ratio_comparison_is_scale_invariant(self):
        # Simulate a real camera's arbitrary intensity units: same SHAPE as
        # the ideal eq(52) prediction, but scaled by an arbitrary gain.
        # Assumes dark-current has already been subtracted (no additive
        # baseline) -- see case4_ratio_comparison's docstring.
        k = 37.5
        scaled = {name: k * value for name, value in IDEAL_CASE4_COEFFS.items()}
        theta = np.arange(0, 360, 3.0)
        intensity = case4_model(theta, **scaled)

        fit = fit_case4(theta, intensity)
        ratios = case4_ratio_comparison(fit)

        for name, r in ratios.items():
            self.assertAlmostEqual(r["fitted_ratio"], r["ideal_ratio"], places=4)

    def test_ratio_comparison_flags_a_real_shape_mismatch(self):
        # a6 deliberately wrong (misalignment/imperfect retardance stand-in)
        bad_coeffs = dict(IDEAL_CASE4_COEFFS)
        bad_coeffs["a6"] = 0.05  # should be 0.5
        theta = np.arange(0, 360, 3.0)
        intensity = case4_model(theta, **bad_coeffs)

        fit = fit_case4(theta, intensity)
        ratios = case4_ratio_comparison(fit)

        self.assertNotAlmostEqual(ratios["a6"]["fitted_ratio"],
                                   ratios["a6"]["ideal_ratio"], places=1)


if __name__ == "__main__":
    unittest.main()
