"""Phase 3: fit the polarizer extinction ratio directly from a captured air
run, instead of guessing it, by numerically searching for the
extinction_ratio value that makes air's reconstruction closest to its known
true Mueller matrix (the identity) -- the "single-reference calibration"
phase from NAMING.md/README's calibration discussion.

Conditional: only run this if validate_against_theory.py already showed
air's error clearly above your noise floor. If air is already close to
identity, there's nothing to fit -- keep using the ideal default (0).

This can only fit extinction_ratio. A PSG/PSA angle-zero misalignment
(ZERO_OFFSET) is a physical motor calibration living in the acquisition
side's config.py, found with its own calibration.py -- no amount of
reconstruction-side fitting can correct for a wrong motor zero after the
fact; see the own_code README's calibration discussion for that path.

To run: edit AIR_DIRECTORY below to point at a captured air run, then:

    python fit_calibration.py

It prints the best-fit extinction_ratio and the before/after Frobenius
error against identity, then asks whether to save that value as this
folder's new default (.last_calibration.json) so main.py's next prompt
suggests it.
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
# EDIT THIS to point at a captured air run (3x3 mode).
# ---------------------------------------------------------------------------
AIR_DIRECTORY = r"C:\COMPARE_CASES\Data\02072026\air"
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


def _error_for(run, extinction_ratio: float) -> float:
    result = reconstruct(run, extinction_ratio=extinction_ratio)
    return float(np.linalg.norm(result.matrix_mean - np.eye(3)))


def _coarse_to_fine_search(run, lo: float = 0.0, hi: float = 0.3,
                            steps: int = 31, refinements: int = 4):
    """Grid search, not a gradient/scipy optimizer -- a single air run is
    cheap to reconstruct (small system matrix, one pinv), so a plain
    coarse-to-fine grid is simple, has no convergence surprises, and needs
    no extra dependency beyond numpy. Each refinement narrows the search
    window around the current best point and re-samples it more finely."""

    best_k, best_err = lo, _error_for(run, lo)
    for k in np.linspace(lo, hi, steps):
        err = _error_for(run, float(k))
        if err < best_err:
            best_err, best_k = err, float(k)

    for _ in range(refinements):
        span = (hi - lo) / steps * 2
        lo, hi = max(0.0, best_k - span), best_k + span
        for k in np.linspace(lo, hi, steps):
            err = _error_for(run, float(k))
            if err < best_err:
                best_err, best_k = err, float(k)

    return best_k, best_err


def main() -> None:
    air_dir = Path(AIR_DIRECTORY)
    run = load_run(air_dir)

    default_err = _error_for(run, 0.0)
    best_k, best_err = _coarse_to_fine_search(run)

    print(f"Air directory: {air_dir}")
    print(f"Default (ideal, extinction_ratio=0) Frobenius error vs identity: {default_err:.6f}")
    print(f"Best-fit extinction_ratio: {best_k:.6f}  (Frobenius error: {best_err:.6f})")

    if best_err >= default_err:
        print(
            "\nFitting did not improve on the ideal default -- your air data is "
            "already consistent with extinction_ratio=0. No calibration change needed."
        )
    else:
        improvement = (1 - best_err / default_err) * 100 if default_err > 0 else 0.0
        print(f"Improvement: {improvement:.1f}% lower error than the ideal default.")
        print(
            "\nIf this improved error is still well above your per-pixel noise "
            "floor (see residual_rms in main.py's output), the remaining "
            "discrepancy is likely a PSG/PSA angle-zero (ZERO_OFFSET) issue, "
            "not extinction ratio -- see the own_code README's calibration "
            "discussion for the physical recalibration + recapture procedure."
        )

    out_dir = RESULT_ROOT / "transmission" / "3x3" / "calibration_fit"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "fit_result.txt", "w", encoding="utf-8") as fh:
        fh.write(f"Air directory: {air_dir}\n")
        fh.write(f"Default (extinction_ratio=0) Frobenius error vs identity: {default_err:.6f}\n")
        fh.write(f"Best-fit extinction_ratio: {best_k:.6f}\n")
        fh.write(f"Best-fit Frobenius error vs identity: {best_err:.6f}\n")
        fh.write("\n--- Provenance ---\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Git commit: {_git_commit_hash()}\n")
    print(f"\nSaved fit report to {out_dir / 'fit_result.txt'}")

    if best_err < default_err:
        answer = input(
            f"\nSave extinction_ratio={best_k:.6f} as this folder's new default "
            "for main.py's next prompt? [y/N]: "
        ).strip().lower()
        if answer in ("y", "yes"):
            last_calibration = _load_last_calibration()
            last_calibration["extinction_ratio"] = best_k
            _save_last_calibration(last_calibration)
            print("Saved to .last_calibration.json.")
        else:
            print("Not saved -- .last_calibration.json left unchanged.")


if __name__ == "__main__":
    main()
