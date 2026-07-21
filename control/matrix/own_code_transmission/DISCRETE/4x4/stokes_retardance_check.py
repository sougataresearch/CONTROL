"""Cross-check the polar-decomposition retardance estimate (main.py /
polar_decomposition.py) against a second, independently-derived retardance
estimate built directly from per-generator-state Stokes vectors -- using
the exact same captured images, no new capture needed.

Why this is a genuine independent check, not the same computation twice:
main.py's route (1) fits a fully general 16-unknown Mueller matrix with no
assumption about what kind of sample it is, then (2) *afterward* removes
diattenuation and reads retardance off the trace of what's left -- exact
only if depolarization is negligible (see polar_decomposition.py).

This script does the opposite: it (1) assumes upfront the sample IS a pure
ideal linear retarder (no diattenuation, no depolarization), and (2) fits
its two unknowns -- fast-axis angle and retardance -- directly against
every generator state's actual input/output Stokes vector pair, via a
coarse-to-fine grid search (no scipy dependency; mueller_forward_model.py's
own mueller_retarder() is reused directly for the model, so there is no
separately hand-derived formula to get wrong).

If both routes agree closely, that's strong, independent validation. If
they disagree, it's diagnostic: likely real depolarization, or the
diattenuator-then-retarder decomposition ordering not fitting this sample
well.

How a generator state's OUTPUT Stokes vector is recovered: a camera pixel
only ever measures S0 (intensity) -- never S1/S2/S3 directly (see this
folder's README). For one fixed PSG_QWP angle (one known input Stokes
vector S_in), this run's images at every PSA_QWP angle captured for that
PSG angle give >= 4 intensity readings through different analyzer
settings; solving that small linear system recovers the full S_out for
that specific input. Doing this for every PSG_QWP angle in the run gives
many (S_in, S_out) pairs -- enough to fit the two-parameter retarder model.

To run: edit RUN_DIRECTORY below to point at the sample's 4x4 run, then:

    python stokes_retardance_check.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

_REQUIRED_PACKAGES = {
    "numpy": "numpy",
    "matplotlib": "matplotlib",
    "PIL": "Pillow",
}


def _ensure_dependencies() -> None:
    missing = [pip_name for module_name, pip_name in _REQUIRED_PACKAGES.items()
               if importlib.util.find_spec(module_name) is None]
    if not missing:
        return
    print(f"Installing missing dependencies: {', '.join(missing)}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    except subprocess.CalledProcessError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--break-system-packages", *missing]
        )


_ensure_dependencies()

import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 -- registers the '3d' projection

from image_loader import load_run
from mueller_forward_model import analyzer_vector_4x4, generator_stokes_4x4, mueller_retarder
from polar_decomposition import decompose
from solve_mueller import reconstruct

# ---------------------------------------------------------------------------
# EDIT THIS to point at the sample's 4x4 run.
# ---------------------------------------------------------------------------
RUN_DIRECTORY = r"C:\COMPARE_CASES\Data\08072026\2026-07-08_Retarder"

# If you know what the sample's retardance SHOULD be, set it here to get a
# deviation-from-expected line in the output for both methods. Set to None
# to skip that comparison.
#
# Manufacturer calibration: 128.3 +/- 0.2 deg at 530nm, 125.7 +/- 0.2 deg at
# 540nm. Our rig runs at 532 +/- 2nm, so the reference value must be
# interpolated to that wavelength, not read off either calibration point
# directly: slope = (125.7-128.3)/(540-530) = -0.26 deg/nm, giving
# 128.3 + (-0.26)*(532-530) = 127.78 deg at 532nm (ranging 127.26-128.3 deg
# across the +/-2nm wavelength uncertainty).
EXPECTED_RETARDANCE_DEG = 127.78
# ---------------------------------------------------------------------------

RESULT_ROOT = Path(r"C:\COMPARE_CASES\RESULT")

# Shared (read-only here) with main.py's calibration state in this same
# folder, so this script's prompts default to whatever you last calibrated
# with main.py -- it does not overwrite that file.
_CALIBRATION_STATE_PATH = Path(__file__).resolve().parent / ".last_calibration.json"


def _load_last_calibration() -> dict:
    try:
        return json.loads(_CALIBRATION_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def ask_float(prompt: str, default: float) -> float:
    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


_DATE_DIR_RE = re.compile(r"^\d{8}$")


def _date_relative_path(path: Path) -> Path:
    parts = path.parts
    for i, part in enumerate(parts):
        if _DATE_DIR_RE.match(part):
            return Path(*parts[i:])
    return Path(path.name)


def _git_commit_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unversioned"


def _format_vector(v: np.ndarray) -> str:
    return "[" + ", ".join(f"{x:+.4f}" for x in v) + "]"


# ---------------------------------------------------------------------------
# Step 1: recover each generator state's OUTPUT Stokes vector from this
# run's own images (spatial mean intensity per image, then a small linear
# solve per PSG_QWP angle).
# ---------------------------------------------------------------------------

def recover_stokes_pairs(run, extinction_ratio: float, rig_retardance_deg: float):
    """Returns a list of (psg_angle, S_in_normalized, S_out_normalized,
    n_analyzer_samples) for every PSG_QWP angle in the run that has >= 4
    distinct PSA_QWP samples (the minimum needed to solve for a 4-unknown
    Stokes vector)."""

    psg_fixed = run.fixed_angles["PSG_Polarizer"]
    psa_fixed = run.fixed_angles["PSA_Analyzer"]

    mean_intensity = run.images.mean(axis=(1, 2))  # (N,) spatial mean per image

    pairs = []
    skipped = []
    for psg_angle in sorted(set(run.psg_qwp_angles.tolist())):
        indices = np.where(run.psg_qwp_angles == psg_angle)[0]
        if len(indices) < 4:
            skipped.append((psg_angle, len(indices)))
            continue

        a_rows = np.array([
            analyzer_vector_4x4(run.psa_qwp_angles[i], psa_fixed, rig_retardance_deg, extinction_ratio)
            for i in indices
        ])
        intensities = mean_intensity[indices]

        s_out, *_ = np.linalg.lstsq(a_rows, intensities, rcond=None)
        if abs(s_out[0]) < 1e-12:
            skipped.append((psg_angle, len(indices)))
            continue
        s_out_normalized = s_out / s_out[0]

        s_in = generator_stokes_4x4(psg_angle, psg_fixed, rig_retardance_deg, extinction_ratio)
        s_in_normalized = s_in / s_in[0]

        pairs.append((psg_angle, s_in_normalized, s_out_normalized, len(indices)))

    return pairs, skipped


# ---------------------------------------------------------------------------
# Step 2: fit (fast_axis_deg, retardance_deg) directly against every
# (S_in, S_out) pair, assuming an ideal linear retarder. Coarse-to-fine grid
# search -- no scipy dependency, and mueller_retarder() is reused directly
# so there's no separately hand-derived model formula to get wrong.
# ---------------------------------------------------------------------------

def _cost(theta_deg: float, delta_deg: float, pairs) -> float:
    """Sum of squared errors on S1,S2,S3 only (not S0 -- an ideal retarder
    trivially preserves normalized S0, so including it would just measure
    unrelated transmission/absorption loss, not axis/retardance fit
    quality)."""

    model = mueller_retarder(theta_deg, delta_deg)
    total = 0.0
    for _psg_angle, s_in, s_out, _n in pairs:
        predicted = model @ s_in
        total += float(np.sum((predicted[1:4] - s_out[1:4]) ** 2))
    return total


def fit_axis_and_retardance(pairs, theta_range=(0.0, 180.0), delta_range=(0.0, 180.0)):
    """Two-pass grid search: a coarse 2deg-step pass over the full range,
    then a fine 0.02deg-step pass over a +/-3deg window around the coarse
    best. Two unknowns, ~100 (S_in,S_out) pairs at most -- brute-force grid
    search is fast enough (< 1s total) and avoids adding scipy as a new
    project dependency."""

    coarse_thetas = np.arange(theta_range[0], theta_range[1], 2.0)
    coarse_deltas = np.arange(delta_range[0], delta_range[1] + 0.01, 2.0)
    best_cost = np.inf
    best_theta, best_delta = coarse_thetas[0], coarse_deltas[0]
    for theta in coarse_thetas:
        for delta in coarse_deltas:
            cost = _cost(theta, delta, pairs)
            if cost < best_cost:
                best_cost, best_theta, best_delta = cost, theta, delta

    fine_thetas = np.arange(best_theta - 3.0, best_theta + 3.0001, 0.02)
    fine_deltas = np.arange(best_delta - 3.0, best_delta + 3.0001, 0.02)
    for theta in fine_thetas:
        for delta in fine_deltas:
            cost = _cost(theta, delta, pairs)
            if cost < best_cost:
                best_cost, best_theta, best_delta = cost, theta, delta

    return float(best_theta % 180.0), float(best_delta), float(best_cost)


def main() -> None:
    run_dir = Path(RUN_DIRECTORY)
    last_calibration = _load_last_calibration()
    extinction_ratio = ask_float(
        "Polarizer extinction ratio Imin/Imax", last_calibration.get("extinction_ratio", 0.0)
    )
    rig_retardance_deg = ask_float(
        "Rig's own QWP retardance in degrees (calibration for PSG_QWP/PSA_QWP, "
        "NOT the sample's retardance)", last_calibration.get("retardance_deg", 90.0)
    )

    run = load_run(run_dir)

    pairs, skipped = recover_stokes_pairs(run, extinction_ratio, rig_retardance_deg)
    if skipped:
        print(f"Skipped {len(skipped)} PSG_QWP angle(s) with < 4 analyzer samples "
              "(not enough to solve for that state's output Stokes vector):")
        for psg_angle, n in skipped:
            print(f"  PSG_QWP={psg_angle:g}: only {n} analyzer sample(s)")
    if len(pairs) < 2:
        raise SystemExit(
            f"Only {len(pairs)} usable generator state(s) found -- need at least 2 "
            "different PSG_QWP angles (ideally many more) to fit both fast-axis "
            "angle and retardance. Check the run has a real PSG_QWP x PSA_QWP grid."
        )
    print(f"Recovered {len(pairs)} generator states' output Stokes vectors "
          f"(from {len(pairs) + len(skipped)} PSG_QWP angles total).")

    axis_deg, stokes_retardance_deg, fit_cost = fit_axis_and_retardance(pairs)
    print(f"\nStokes-vector fit: fast axis = {axis_deg:.3f} deg, "
          f"retardance = {stokes_retardance_deg:.3f} deg (fit cost {fit_cost:.6f})")

    # Independent route: the full 16-parameter Mueller-matrix reconstruction
    # + post-hoc polar decomposition, on the exact same images.
    result = reconstruct(run, extinction_ratio=extinction_ratio, retardance_deg=rig_retardance_deg)
    decomposition = decompose(result.matrix_mean)
    matrix_retardance_deg = float(decomposition.retardance_deg)
    diattenuation = float(decomposition.diattenuation)
    depolarization_index = float(decomposition.depolarization_index)
    print(f"Matrix-decomposition estimate: retardance = {matrix_retardance_deg:.3f} deg "
          f"(diattenuation {diattenuation:.4f}, depolarization index {depolarization_index:.4f})")

    agreement_deg = abs(stokes_retardance_deg - matrix_retardance_deg)
    print(f"\nAgreement between the two methods: {agreement_deg:.3f} deg apart")

    out_dir = RESULT_ROOT / "transmission" / "4x4" / "stokes_retardance_check" / _date_relative_path(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Poincare-sphere sanity plot: measured (S1,S2,S3) points vs. what the
    # fitted (axis, retardance) model predicts for each input -- points
    # landing on the fitted curve indicate a clean linear retarder; scatter
    # off it indicates depolarization or a non-retarder effect.
    model = mueller_retarder(axis_deg, stokes_retardance_deg)
    measured = np.array([s_out[1:4] for _p, _si, s_out, _n in pairs])
    predicted = np.array([(model @ s_in)[1:4] for _p, s_in, _so, _n in pairs])

    fig = plt.figure(figsize=(7, 7))
    ax = fig.add_subplot(111, projection="3d")
    u, v = np.mgrid[0:2 * np.pi:40j, 0:np.pi:20j]
    ax.plot_wireframe(np.cos(u) * np.sin(v), np.sin(u) * np.sin(v), np.cos(v),
                       color="lightgray", linewidth=0.3, alpha=0.5)
    ax.scatter(*measured.T, color="tab:blue", label="Measured S_out (per PSG angle)", s=25)
    ax.scatter(*predicted.T, color="tab:orange", marker="x", label="Fitted-model prediction", s=25)
    for m, p in zip(measured, predicted):
        ax.plot([m[0], p[0]], [m[1], p[1]], [m[2], p[2]], color="gray", linewidth=0.5)
    ax.set_xlabel("S1")
    ax.set_ylabel("S2")
    ax.set_zlabel("S3")
    ax.set_title(f"Poincare sphere: measured vs. fitted-retarder model\n"
                 f"(axis {axis_deg:.2f} deg, retardance {stokes_retardance_deg:.2f} deg)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "poincare_sphere_fit.png", dpi=200)
    plt.close(fig)

    with open(out_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write("Per-generator-state recovered Stokes vectors\n")
        fh.write("(S_in and S_out both normalized by their own S0)\n\n")
        fh.write(f"{'PSG_QWP':>8s} | {'n_analyzer':>10s} | {'S_in':>28s} | {'S_out (measured)':>28s}\n")
        for psg_angle, s_in, s_out, n in pairs:
            fh.write(f"{psg_angle:8.2f} | {n:10d} | {_format_vector(s_in):>28s} | "
                      f"{_format_vector(s_out):>28s}\n")
        if skipped:
            fh.write("\nSkipped (< 4 analyzer samples):\n")
            for psg_angle, n in skipped:
                fh.write(f"  PSG_QWP={psg_angle:g}: only {n} analyzer sample(s)\n")

        fh.write("\n--- Method 1: direct Stokes-vector fit (assumes ideal retarder) ---\n")
        fh.write(f"Fitted fast-axis angle: {axis_deg:.3f} deg\n")
        fh.write(f"Fitted retardance: {stokes_retardance_deg:.3f} deg\n")
        fh.write(f"Fit residual cost (sum of squared S1/S2/S3 errors): {fit_cost:.6f}\n")

        fh.write("\n--- Method 2: full Mueller-matrix reconstruction + polar decomposition ---\n")
        fh.write(f"Diattenuation: {diattenuation:.4f}\n")
        fh.write(f"Depolarization index: {depolarization_index:.4f}\n")
        fh.write(f"Estimated retardance: {matrix_retardance_deg:.3f} deg "
                 "(exact only if depolarization index is close to 1)\n")

        fh.write(f"\n--- Agreement between the two methods ---\n")
        fh.write(f"|Method 1 - Method 2| = {agreement_deg:.3f} deg\n")

        if EXPECTED_RETARDANCE_DEG is not None:
            fh.write(f"\n--- Comparison to expected retardance ({EXPECTED_RETARDANCE_DEG:g} deg) ---\n")
            fh.write(f"Method 1 (Stokes fit) deviation: "
                     f"{stokes_retardance_deg - EXPECTED_RETARDANCE_DEG:+.3f} deg\n")
            fh.write(f"Method 2 (matrix decomposition) deviation: "
                     f"{matrix_retardance_deg - EXPECTED_RETARDANCE_DEG:+.3f} deg\n")

        fh.write("\n--- Provenance ---\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Git commit: {_git_commit_hash()}\n")
        fh.write(f"Source run: {run_dir}\n")
        fh.write(f"Extinction ratio: {extinction_ratio}\n")
        fh.write(f"Rig QWP retardance (calibration input, not sample retardance): {rig_retardance_deg}\n")
        if run.dark_subtracted:
            fh.write(f"Dark-current subtraction: applied ({run.dark_frame_count} frame(s))\n")
        else:
            fh.write("Dark-current subtraction: NOT applied\n")

    print(f"\nSaved outputs to {out_dir}")


if __name__ == "__main__":
    main()
