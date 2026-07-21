"""Fourier-series theoretical models for the 4 calibration cases, and the
scipy curve_fit wrappers that extract each case's coefficients from
measured (angle, intensity) data.

No hardware dependency at all -- every function here is pure math, so it
is fully testable with synthetic data (see test_theory.py) before any of
it ever touches a real captured frame. This mirrors the same
validate-against-known-cases approach used throughout control/matrix/
(e.g. reflection_theory.py's Brewster's-angle/energy-conservation tests).

Angles are always in DEGREES at the public-function boundary (matching
this whole project's convention); converted to radians internally for the
trig calls.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit


def _deg2rad(theta_deg: np.ndarray) -> np.ndarray:
    return np.deg2rad(theta_deg)


def _residual_rms(fitted: np.ndarray, measured: np.ndarray) -> float:
    return float(np.sqrt(np.mean((fitted - measured) ** 2)))


# ---------------------------------------------------------------------------
# Case 1 -- P1 only. Malus's law (Stokes form).
# ---------------------------------------------------------------------------

def case1_model(theta_deg, S0: float, S1: float, S2: float):
    """I(theta) = 0.5 * (S0 + S1*cos(2*theta) + S2*sin(2*theta)) -- the
    first row of an ideal polarizer's Mueller matrix dotted with the input
    Stokes vector. Algebraically identical to I = I0*cos^2(theta - theta0)."""
    t = _deg2rad(np.asarray(theta_deg, dtype=np.float64))
    return 0.5 * (S0 + S1 * np.cos(2 * t) + S2 * np.sin(2 * t))


@dataclass
class Case1Fit:
    S0: float
    S1: float
    S2: float
    extinction_ratio: float   # Imax / Imin
    theta0_deg: float         # phase offset of the fitted curve's maximum
    residual_rms: float
    fitted: np.ndarray


def fit_case1(theta_deg, intensity) -> Case1Fit:
    theta_deg = np.asarray(theta_deg, dtype=np.float64)
    intensity = np.asarray(intensity, dtype=np.float64)

    # Initial guess: S0 ~ 2*mean, S1 ~ peak-to-peak amplitude, S2 ~ 0 (a
    # reasonable starting point regardless of the true phase, since
    # curve_fit only needs to be in the right ballpark, not exact).
    p0 = [2.0 * intensity.mean(), (intensity.max() - intensity.min()) / 2.0, 0.0]
    popt, _ = curve_fit(case1_model, theta_deg, intensity, p0=p0)
    S0, S1, S2 = (float(v) for v in popt)

    amp = float(np.hypot(S1, S2))
    if S0 <= amp:
        raise ValueError(
            f"Fitted S0={S0:.4f} <= sqrt(S1^2+S2^2)={amp:.4f}; extinction "
            "ratio would be zero/negative, which isn't physical. Check the "
            "raw data first: dark-current subtraction applied? camera "
            "saturated? angle coverage span the full 360 degrees?"
        )
    extinction_ratio = (S0 + amp) / (S0 - amp)
    theta0_deg = 0.5 * float(np.degrees(np.arctan2(S2, S1)))

    fitted = case1_model(theta_deg, S0, S1, S2)
    return Case1Fit(S0, S1, S2, extinction_ratio, theta0_deg,
                     _residual_rms(fitted, intensity), fitted)


# ---------------------------------------------------------------------------
# Cases 2/3 -- one rotating QWP between two fixed polarizers.
# ---------------------------------------------------------------------------

def case23_model(theta_deg, a0: float, a2: float, b2: float, a4: float, b4: float):
    """I(theta) = a0/2 + (a2*cos(2t)+b2*sin(2t))/2 + (a4*cos(4t)+b4*sin(4t))/2

    Ideal-alignment prediction: a2 = b2 = 0 (see README's Case 2/3
    section for why -- this is the built-in alignment diagnostic)."""
    t = _deg2rad(np.asarray(theta_deg, dtype=np.float64))
    return (a0
            + a2 * np.cos(2 * t) + b2 * np.sin(2 * t)
            + a4 * np.cos(4 * t) + b4 * np.sin(4 * t)) / 2.0


@dataclass
class Case23Fit:
    a0: float
    a2: float
    b2: float
    a4: float
    b4: float
    second_harmonic_magnitude: float  # sqrt(a2^2 + b2^2) -- the alignment diagnostic
    residual_rms: float
    fitted: np.ndarray


def fit_case23(theta_deg, intensity) -> Case23Fit:
    theta_deg = np.asarray(theta_deg, dtype=np.float64)
    intensity = np.asarray(intensity, dtype=np.float64)

    p0 = [2.0 * intensity.mean(), 0.0, 0.0,
          (intensity.max() - intensity.min()) / 2.0, 0.0]
    popt, _ = curve_fit(case23_model, theta_deg, intensity, p0=p0)
    a0, a2, b2, a4, b4 = (float(v) for v in popt)

    fitted = case23_model(theta_deg, a0, a2, b2, a4, b4)
    second_harmonic_magnitude = float(np.hypot(a2, b2))
    return Case23Fit(a0, a2, b2, a4, b4, second_harmonic_magnitude,
                      _residual_rms(fitted, intensity), fitted)


# ---------------------------------------------------------------------------
# Case 4 -- full PCSCA, QWP1:QWP2 coupled 5:1, air (identity) sample.
# ---------------------------------------------------------------------------

def case4_model(theta_g_deg, a0: float, a2: float, a4: float,
                 a6: float, a8: float, a10: float):
    """I(theta_G) = a0 + a2*cos(4t) + a4*cos(8t) + a6*cos(12t) + a8*cos(16t)
    + a10*cos(20t), theta_G being the generator (QWP1) angle. Pure-cosine
    (no sine terms) because air/identity has no optical activity, so the
    curve is symmetric about theta_G = 0.

    a0..a10 are all free fit parameters -- this deliberately does NOT force
    the fit toward the ideal eq(52)-convention values (a0=1.25, a2=0.25,
    a4=-0.5, a6=0.5, a8=0.25, a10=0.25). Your camera's arbitrary intensity
    units aren't guaranteed to match that convention's absolute
    normalization, so only the RATIOS between fitted coefficients (see
    case4_ratio_comparison below) are meaningfully comparable to the ideal
    values -- never compare absolute fitted values to the ideal ones
    directly."""
    t = _deg2rad(np.asarray(theta_g_deg, dtype=np.float64))
    return (a0
            + a2 * np.cos(4 * t)
            + a4 * np.cos(8 * t)
            + a6 * np.cos(12 * t)
            + a8 * np.cos(16 * t)
            + a10 * np.cos(20 * t))


IDEAL_CASE4_COEFFS = {
    "a0": 1.25, "a2": 0.25, "a4": -0.5, "a6": 0.5, "a8": 0.25, "a10": 0.25,
}


@dataclass
class Case4Fit:
    a0: float
    a2: float
    a4: float
    a6: float
    a8: float
    a10: float
    residual_rms: float
    fitted: np.ndarray


def fit_case4(theta_g_deg, intensity) -> Case4Fit:
    theta_g_deg = np.asarray(theta_g_deg, dtype=np.float64)
    intensity = np.asarray(intensity, dtype=np.float64)

    amp0 = (intensity.max() - intensity.min()) / 4.0
    p0 = [intensity.mean(), amp0, -amp0, amp0, amp0, amp0]
    popt, _ = curve_fit(case4_model, theta_g_deg, intensity, p0=p0)
    a0, a2, a4, a6, a8, a10 = (float(v) for v in popt)

    fitted = case4_model(theta_g_deg, a0, a2, a4, a6, a8, a10)
    return Case4Fit(a0, a2, a4, a6, a8, a10,
                     _residual_rms(fitted, intensity), fitted)


def case4_ratio_comparison(fit: Case4Fit) -> dict:
    """Compares each fitted coefficient's RATIO to a0 against the ideal
    eq(52)-convention ratio -- never compares absolute values (see
    case4_model's docstring for why). This ratio comparison is only valid
    if dark-current has already been subtracted from the raw intensities
    (see preflight.py) -- an un-subtracted constant camera baseline would
    add a fixed offset to the fitted a0 without scaling a2..a10 the same
    way, which would corrupt every ratio here even if the instrument
    itself is perfectly aligned."""
    ratios = {}
    for name, ideal_value in IDEAL_CASE4_COEFFS.items():
        if name == "a0":
            continue
        fitted_value = getattr(fit, name)
        ratios[name] = {
            "fitted_ratio": fitted_value / fit.a0,
            "ideal_ratio": ideal_value / IDEAL_CASE4_COEFFS["a0"],
        }
    return ratios
