"""Phase 3: fit the polarizer extinction ratio and QWP retardance directly
from a captured CONTINUOUS-rotation air run, instead of guessing them, by
numerically searching for the (extinction_ratio, retardance_deg) pair that
makes air's reconstruction closest to its known true Mueller matrix (the
identity) -- the "single-reference calibration" phase from NAMING.md/
README's calibration discussion. Deliberate duplicate in spirit of
../../DISCRETE/4x4/fit_calibration.py.

Conditional: only run this if validate_against_theory.py already showed
air's error clearly above your noise floor. If air is already close to
identity, there's nothing to fit -- keep using the ideal defaults (0, 90).

This can only fit extinction_ratio/retardance_deg. A PSG/PSA angle-zero
misalignment (ZERO_OFFSET) is a physical motor calibration living in the
acquisition side's config.py, found with its own calibration.py -- no amount
of reconstruction-side fitting can correct for a wrong motor zero after the
fact; see the own_code README's calibration discussion for that path.

To run: edit AIR_DIRECTORY below to point at a captured air run (4x4 mode),
then:

    python fit_calibration.py

It prints the best-fit (extinction_ratio, retardance_deg) and the
before/after Frobenius error against identity, then asks whether to save
those values as this folder's new default (.last_calibration.json) so
main.py's next prompt suggests them.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

_REQUIRED_PACKAGES = {
    "numpy": "numpy",
    "PIL": "Pillow",
}


def _ensure_dependencies() -> None:
    """Install any of this script's required packages that aren't already
    present, using the same Python interpreter running this script. Falls
    back to --break-system-packages if a plain install is blocked by an
    externally-managed environment (PEP 668, e.g. a uv-managed Python)."""

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
from datetime import datetime
from pathlib import Path

import numpy as np

from image_loader import load_run
from solve_mueller import reconstruct

# ---------------------------------------------------------------------------
# EDIT THIS to point at a captured continuous-rotation air run (Images/,
# Logs/experiment_log.csv, Config/experiment_config.json).
# ---------------------------------------------------------------------------
AIR_DIRECTORY = r"G:\control\Data\continuous\air"
# ---------------------------------------------------------------------------

RESULT_ROOT = Path(r"C:\COMPARE_CASES\RESULT")

# Shared with main.py/validate_against_theory.py in this same folder.
_CALIBRATION_STATE_PATH = Path(__file__).resolve().parent / ".last_calibration.json"


def _load_last_calibration() -> dict:
    try:
        return json.loads(_CALIBRATION_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_last_calibration(values: dict) -> None:
    _CALIBRATION_STATE_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")


def _git_commit_hash() -> str:
    """Short git commit hash of the code that produced this result -- see
    main.py's own copy of this helper for the full rationale."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unversioned"


def _error_for(run, extinction_ratio: float, retardance_deg: float) -> float:
    result = reconstruct(run, extinction_ratio=extinction_ratio, retardance_deg=retardance_deg)
    return float(np.linalg.norm(result.matrix_mean - np.eye(4)))


def _coarse_to_fine_1d(objective, lo: float, hi: float, steps: int = 31, refinements: int = 4):
    """Grid search over a single parameter, narrowing the window around the
    current best point each refinement round. Used inside the coordinate
    descent below to fit one parameter at a time."""

    best_x, best_err = lo, objective(lo)
    for x in np.linspace(lo, hi, steps):
        err = objective(float(x))
        if err < best_err:
            best_err, best_x = err, float(x)

    for _ in range(refinements):
        span = (hi - lo) / steps * 2
        # Clamp below 0: extinction_ratio must stay >= 0 (mueller_linear_polarizer
        # takes sqrt(k), which is NaN for negative k), and retardance has no
        # meaningful negative range either -- without this, a best_x near 0
        # lets the window drift negative and corrupts the whole search.
        window_lo, window_hi = max(0.0, best_x - span), best_x + span
        for x in np.linspace(window_lo, window_hi, steps):
            err = objective(float(x))
            if err < best_err:
                best_err, best_x = err, float(x)

    return best_x, best_err


def _fit_extinction_and_retardance(run, rounds: int = 5):
    """Coordinate descent: alternately fit extinction_ratio with retardance
    held fixed, then retardance with extinction_ratio held fixed, repeating
    a few rounds. Two coupled parameters and no existing scipy dependency in
    this project made a plain alternating 1D grid search the simplest
    correct option, rather than adding a 2D optimizer dependency."""

    extinction_ratio, retardance_deg = 0.0, 90.0
    for _ in range(rounds):
        extinction_ratio, _ = _coarse_to_fine_1d(
            lambda k: _error_for(run, k, retardance_deg), lo=0.0, hi=0.3
        )
        retardance_deg, err = _coarse_to_fine_1d(
            lambda r: _error_for(run, extinction_ratio, r), lo=60.0, hi=120.0
        )
    return extinction_ratio, retardance_deg, err


def main() -> None:
    air_dir = Path(AIR_DIRECTORY)
    run = load_run(air_dir)

    default_err = _error_for(run, 0.0, 90.0)
    best_extinction, best_retardance, best_err = _fit_extinction_and_retardance(run)

    print(f"Air directory: {air_dir}")
    print(f"Default (ideal, extinction_ratio=0, retardance=90) Frobenius error vs identity: {default_err:.6f}")
    print(f"Best-fit extinction_ratio: {best_extinction:.6f}")
    print(f"Best-fit retardance (deg): {best_retardance:.4f}")
    print(f"Best-fit Frobenius error vs identity: {best_err:.6f}")

    if best_err >= default_err:
        print(
            "\nFitting did not improve on the ideal defaults -- your air data is "
            "already consistent with extinction_ratio=0, retardance=90. No "
            "calibration change needed."
        )
    else:
        improvement = (1 - best_err / default_err) * 100 if default_err > 0 else 0.0
        print(f"Improvement: {improvement:.1f}% lower error than the ideal defaults.")
        print(
            "\nIf this improved error is still well above your per-pixel noise "
            "floor (see residual_rms in main.py's output), the remaining "
            "discrepancy is likely a PSG/PSA angle-zero (ZERO_OFFSET) issue, "
            "not extinction ratio/retardance -- see the own_code README's "
            "calibration discussion for the physical recalibration + "
            "recapture procedure."
        )

    out_dir = RESULT_ROOT / "reflection" / "continuous_4x4" / "calibration_fit"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "fit_result.txt", "w", encoding="utf-8") as fh:
        fh.write(f"Air directory: {air_dir}\n")
        fh.write(f"Default (extinction_ratio=0, retardance=90) Frobenius error vs identity: {default_err:.6f}\n")
        fh.write(f"Best-fit extinction_ratio: {best_extinction:.6f}\n")
        fh.write(f"Best-fit retardance (deg): {best_retardance:.4f}\n")
        fh.write(f"Best-fit Frobenius error vs identity: {best_err:.6f}\n")
        fh.write("\n--- Provenance ---\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Git commit: {_git_commit_hash()}\n")
    print(f"\nSaved fit report to {out_dir / 'fit_result.txt'}")

    if best_err < default_err:
        answer = input(
            f"\nSave extinction_ratio={best_extinction:.6f}, retardance={best_retardance:.4f} "
            "as this folder's new default for main.py's next prompt? [y/N]: "
        ).strip().lower()
        if answer in ("y", "yes"):
            last_calibration = _load_last_calibration()
            last_calibration["extinction_ratio"] = best_extinction
            last_calibration["retardance_deg"] = best_retardance
            _save_last_calibration(last_calibration)
            print("Saved to .last_calibration.json.")
        else:
            print("Not saved -- .last_calibration.json left unchanged.")


if __name__ == "__main__":
    main()
