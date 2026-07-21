"""Fits one case's (angle, intensity) sweep data to its theoretical model
(theory.py), computes residual RMS, plots measured data against both the
fit and an "ideal-case" reference curve, and prints the case-specific
diagnostic numbers (extinction ratio/phase for Case 1, the alignment
diagnostic for Cases 2/3, coefficient ratios for Case 4).

The "ideal-case" dashed reference curve on each plot is NOT a fixed
absolute-scale prediction (your camera's arbitrary intensity units aren't
guaranteed to match any particular convention's normalization) -- it is
built from the SAME fit's own overall amplitude, with only the
alignment-sensitive terms forced to their theoretically ideal values, so
it's a fair apples-to-apples visual comparison rather than a mismatched
absolute-scale curve.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless-safe; this project never needs an interactive window
import matplotlib.pyplot as plt
import numpy as np

from theory import (
    IDEAL_CASE4_COEFFS,
    Case1Fit, Case23Fit, Case4Fit,
    case1_model, case23_model, case4_model,
    case4_ratio_comparison,
    fit_case1, fit_case23, fit_case4,
)

_DENSE_THETA = np.linspace(0.0, 360.0, 721)  # 0.5 deg resolution for smooth plotted curves


def _plot(theta, intensity, fitted_curve, ideal_curve, title: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(theta, intensity, s=14, color="#1f77b4", label="Measured", zorder=3)
    ax.plot(_DENSE_THETA, fitted_curve, color="#d62728", linewidth=1.5, label="Fit", zorder=2)
    ax.plot(_DENSE_THETA, ideal_curve, color="#2ca02c", linewidth=1.5,
            linestyle="--", label="Ideal case (same scale)", zorder=1)
    ax.set_xlabel("Angle (deg)")
    ax.set_ylabel("ROI-mean intensity")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def fit_and_plot_case1(angle_intensity: np.ndarray, out_dir: Path) -> dict:
    theta, intensity = angle_intensity[:, 0], angle_intensity[:, 1]
    fit = fit_case1(theta, intensity)

    fitted_curve = case1_model(_DENSE_THETA, fit.S0, fit.S1, fit.S2)
    # Ideal case: same S0 (same overall brightness), but perfect extinction
    # (S2 = 0, S1 = S0) at the SAME fitted phase.
    ideal_amp = fit.S0
    theta0_rad = np.deg2rad(fit.theta0_deg)
    ideal_S1 = ideal_amp * np.cos(2 * theta0_rad)
    ideal_S2 = ideal_amp * np.sin(2 * theta0_rad)
    ideal_curve = case1_model(_DENSE_THETA, fit.S0, ideal_S1, ideal_S2)

    out_dir.mkdir(parents=True, exist_ok=True)
    _plot(theta, intensity, fitted_curve, ideal_curve,
          "Case 1 -- P1 only (Malus's law)", out_dir / "comparison.png")

    print(f"Case 1 fit -- residual RMS: {fit.residual_rms:.4f}")
    print(f"Case 1 extinction ratio (Imax/Imin): {fit.extinction_ratio:.2f}")
    print(f"Case 1 phase offset theta0: {fit.theta0_deg:.3f} deg")

    return {
        "case": 1,
        "S0": fit.S0, "S1": fit.S1, "S2": fit.S2,
        "extinction_ratio": fit.extinction_ratio,
        "theta0_deg": fit.theta0_deg,
        "residual_rms": fit.residual_rms,
    }


def fit_and_plot_case23(angle_intensity: np.ndarray, case_label: str, out_dir: Path) -> dict:
    """``case_label`` is "Case 2" or "Case 3", used only for print/plot labels."""

    theta, intensity = angle_intensity[:, 0], angle_intensity[:, 1]
    fit = fit_case23(theta, intensity)

    fitted_curve = case23_model(_DENSE_THETA, fit.a0, fit.a2, fit.b2, fit.a4, fit.b4)
    # Ideal case: same a0/a4/b4 (same overall shape/brightness), but the
    # alignment-sensitive second harmonic forced to its ideal-alignment
    # value, a2 = b2 = 0.
    ideal_curve = case23_model(_DENSE_THETA, fit.a0, 0.0, 0.0, fit.a4, fit.b4)

    out_dir.mkdir(parents=True, exist_ok=True)
    _plot(theta, intensity, fitted_curve, ideal_curve,
          f"{case_label} -- one rotating QWP between fixed polarizers",
          out_dir / "comparison.png")

    print(f"{case_label} fit -- residual RMS: {fit.residual_rms:.4f}")
    print(
        f"{case_label} ALIGNMENT DIAGNOSTIC -- second-harmonic magnitude "
        f"sqrt(a2^2+b2^2) = {fit.second_harmonic_magnitude:.4f} "
        f"(a2={fit.a2:.4f}, b2={fit.b2:.4f}); ideal-alignment prediction is 0. "
        f"Compare against a4={fit.a4:.4f} to judge how significant this is."
    )

    return {
        "case": case_label,
        "a0": fit.a0, "a2": fit.a2, "b2": fit.b2, "a4": fit.a4, "b4": fit.b4,
        "second_harmonic_magnitude": fit.second_harmonic_magnitude,
        "residual_rms": fit.residual_rms,
    }


def fit_and_plot_case4(angle_intensity: np.ndarray, out_dir: Path) -> dict:
    theta, intensity = angle_intensity[:, 0], angle_intensity[:, 1]
    fit = fit_case4(theta, intensity)
    ratios = case4_ratio_comparison(fit)

    fitted_curve = case4_model(_DENSE_THETA, fit.a0, fit.a2, fit.a4, fit.a6, fit.a8, fit.a10)
    # Ideal case: the ideal eq(52)-convention SHAPE, rescaled so its a0
    # matches the fit's own a0 -- absolute scale is never compared
    # directly (see theory.case4_model's docstring), only the ratios.
    scale = fit.a0 / IDEAL_CASE4_COEFFS["a0"]
    scaled_ideal = {name: scale * value for name, value in IDEAL_CASE4_COEFFS.items()}
    ideal_curve = case4_model(_DENSE_THETA, **scaled_ideal)

    out_dir.mkdir(parents=True, exist_ok=True)
    _plot(theta, intensity, fitted_curve, ideal_curve,
          "Case 4 -- full PCSCA, air sample, QWP1:QWP2 coupled 5:1",
          out_dir / "comparison.png")

    print(f"Case 4 fit -- residual RMS: {fit.residual_rms:.4f}")
    print("Case 4 fitted coefficients (absolute scale is arbitrary -- see docstring):")
    print(f"  a0={fit.a0:.4f}, a2={fit.a2:.4f}, a4={fit.a4:.4f}, "
          f"a6={fit.a6:.4f}, a8={fit.a8:.4f}, a10={fit.a10:.4f}")
    print("Case 4 coefficient RATIOS vs. ideal (fitted/a0 vs. ideal/a0):")
    for name, r in ratios.items():
        print(f"  {name}: fitted {r['fitted_ratio']:.4f}  ideal {r['ideal_ratio']:.4f}")

    return {
        "case": 4,
        "a0": fit.a0, "a2": fit.a2, "a4": fit.a4,
        "a6": fit.a6, "a8": fit.a8, "a10": fit.a10,
        "residual_rms": fit.residual_rms,
        "ratio_comparison": ratios,
    }
