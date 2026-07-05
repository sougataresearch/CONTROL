"""Aggregate a 3x3 Mueller matrix reconstruction across multiple repeated
rounds of the same sample, to get a mean and standard deviation *across
rounds* -- round-to-round repeatability, not just per-pixel noise within a
single round (that's what residual_rms in main.py already covers).

Each round is a separate, complete run folder (its own Images/ and
Config/experiment_config.json) laid out as siblings, e.g.:

    lp30_round01/  lp30_round02/  lp30_round03/

Every round is reconstructed independently with the exact same
solve_mueller.reconstruct() used for a single run in main.py -- this script
only aggregates each round's matrix_mean afterward; it does not change how
any individual round is reconstructed.

To run: edit ROUND_DIRECTORIES below to list every round's folder, then:

    python average_rounds.py
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

import re
from pathlib import Path

import numpy as np

from image_loader import load_run
from solve_mueller import reconstruct

# ---------------------------------------------------------------------------
# EDIT THIS: every round of the same sample, same angle set, 3x3 mode.
# ---------------------------------------------------------------------------
ROUND_DIRECTORIES = [
    r"G:\control\Data\03072026\lp\lp30_round01",
    r"G:\control\Data\03072026\lp\lp30_round02",
    r"G:\control\Data\03072026\lp\lp30_round03",
]

# Name for the aggregate output folder. None = derived from the first
# round's folder name with a trailing "_roundN" stripped, e.g. "lp30".
SAMPLE_NAME = None

EXTINCTION_RATIO = 0.0
# ---------------------------------------------------------------------------


def _default_sample_name(round_dirs: list) -> str:
    first = Path(round_dirs[0]).name
    return re.sub(r"_round\d+$", "", first, flags=re.IGNORECASE) or first


def main() -> None:
    round_dirs = [Path(p) for p in ROUND_DIRECTORIES]
    sample_name = SAMPLE_NAME or _default_sample_name(ROUND_DIRECTORIES)

    per_round_matrices = []
    per_round_conditions = []
    per_round_residuals = []
    for round_dir in round_dirs:
        run = load_run(round_dir)
        result = reconstruct(run, extinction_ratio=EXTINCTION_RATIO)
        per_round_matrices.append(result.matrix_mean)
        per_round_conditions.append(result.condition_number)
        per_round_residuals.append(result.residual_rms.mean())
        print(f"{round_dir.name}: condition number {result.condition_number:.3f}, "
              f"mean residual {result.residual_rms.mean():.6f}")

    stacked = np.stack(per_round_matrices, axis=0)  # (n_rounds, 3, 3)
    mean_matrix = stacked.mean(axis=0)
    std_matrix = stacked.std(axis=0, ddof=1) if len(round_dirs) > 1 else np.zeros_like(mean_matrix)

    out_dir = Path(__file__).resolve().parent / "Results" / f"{sample_name}_multi_round"
    out_dir.mkdir(parents=True, exist_ok=True)

    np.save(out_dir / "per_round_matrices.npy", stacked)
    np.save(out_dir / "mean_matrix.npy", mean_matrix)
    np.save(out_dir / "std_matrix.npy", std_matrix)

    np.set_printoptions(precision=4, suppress=True)
    with open(out_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write(f"Sample: {sample_name}\n")
        fh.write(f"Rounds: {[d.name for d in round_dirs]}\n")
        fh.write(f"Per-round condition numbers: {[round(c, 3) for c in per_round_conditions]}\n")
        fh.write(f"Per-round mean residual (RMS): {[round(r, 6) for r in per_round_residuals]}\n")
        fh.write("Mean Mueller matrix across rounds (m00-normalized):\n")
        fh.write(np.array2string(mean_matrix))
        fh.write("\nStandard deviation across rounds:\n")
        fh.write(np.array2string(std_matrix))
        fh.write("\n")

    print(f"\nMean matrix across {len(round_dirs)} rounds:")
    print(mean_matrix)
    print("Standard deviation across rounds:")
    print(std_matrix)
    print(f"Saved aggregate results to {out_dir}")


if __name__ == "__main__":
    main()
