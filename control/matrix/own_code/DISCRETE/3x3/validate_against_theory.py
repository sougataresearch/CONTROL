"""Phase 2: validate the 3x3 solver against samples with a known theoretical
Mueller matrix -- air (should reconstruct to identity) and a linear
polarizer at a known angle (should reconstruct to the ideal LP formula) --
using the exact same reconstruction as main.py.

This is the calibration baseline: if air's error and the LP samples' error
look similar (same pattern, similar magnitude), the discrepancy is a
systematic PSG/PSA modeling problem (angle offset, extinction ratio), not
something sample-specific -- see NAMING.md and the own_code READMEs for the
fuller discussion of what to do next in that case.

To run: edit SAMPLE_DIRECTORIES below to list every dataset you want
checked (folder name must be "air" or "lp<angle>" so the theoretical
target can be inferred -- extend theoretical_matrix() for other known
references), then:

    python validate_against_theory.py

Before processing anything, it prints every configured folder with an
OK/MISSING check and asks you to confirm the list is complete and correct
-- a forgotten or mistyped folder would otherwise silently produce one
fewer comparison with no error.
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
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from image_loader import load_run
from solve_mueller import reconstruct

# ---------------------------------------------------------------------------
# EDIT THIS: every dataset to check against its known theoretical matrix.
# Folder name drives theoretical_matrix() below -- "air" or "lp<angle>".
# ---------------------------------------------------------------------------
SAMPLE_DIRECTORIES = [
    r"G:\control\Data\02072026\air",
    r"G:\control\Data\03072026\lp\lp30",
    r"G:\control\Data\03072026\lp\lp45",
    r"G:\control\Data\03072026\lp\lp90",
]

EXTINCTION_RATIO = 0.002
# ---------------------------------------------------------------------------


def confirm_sample_directories(paths: list) -> None:
    """Print every configured SAMPLE_DIRECTORIES entry with an OK/MISSING
    check, and ask the operator to confirm the list is complete and correct
    before running. A forgotten or mistyped folder is otherwise silent --
    each directory is validated independently, so the run would just
    quietly produce one fewer comparison instead of erroring."""

    print("Sample directories configured for validation:")
    missing = []
    for path in paths:
        exists = Path(path).is_dir()
        print(f"  [{'OK' if exists else 'MISSING'}] {path}")
        if not exists:
            missing.append(path)

    if missing:
        print(
            f"\n{len(missing)} folder(s) not found. Fix SAMPLE_DIRECTORIES at "
            "the top of this file, then run again."
        )
        raise SystemExit(1)

    answer = input(
        f"\nAll {len(paths)} folder(s) exist. Is this the complete and "
        "correct list of samples to validate? [y/N]: "
    ).strip().lower()
    if answer not in ("y", "yes"):
        print("Edit SAMPLE_DIRECTORIES at the top of this file, then run again.")
        raise SystemExit(0)


def theoretical_matrix(sample_name: str) -> np.ndarray:
    """Infer the ideal 3x3 Mueller matrix from the sample's folder name --
    "air" -> identity, "lp<angle>" -> an ideal linear polarizer, "qwp<angle>"
    -> the 3x3 sub-block an ideal quarter-wave plate reduces to under a
    linear-states-only (3x3) measurement -- ignores whatever the QWP does
    to circular polarization, since 3x3 mode can't see that regardless of
    how good the reconstruction is. Add a case here for any other known
    reference sample."""

    name = sample_name.lower()
    if name == "air":
        return np.eye(3)

    match = re.match(r"^lp(-?\d+(?:\.\d+)?)$", name)
    if match:
        theta = np.deg2rad(float(match.group(1)))
        c, s = np.cos(2 * theta), np.sin(2 * theta)
        return np.array([
            [1.0, c, s],
            [c, c * c, c * s],
            [s, c * s, s * s],
        ])

    match = re.match(r"^qwp(-?\d+(?:\.\d+)?)$", name)
    if match:
        theta = np.deg2rad(float(match.group(1)))
        c, s = np.cos(2 * theta), np.sin(2 * theta)
        return np.array([
            [1.0, 0.0, 0.0],
            [0.0, c * c, c * s],
            [0.0, c * s, s * s],
        ])

    raise ValueError(
        f"No known theoretical matrix for sample {sample_name!r} -- "
        "add a case to theoretical_matrix() for this reference."
    )


_DATE_DIR_RE = re.compile(r"^\d{8}$")


def _date_relative_path(path: Path) -> Path:
    """Return the portion of path from its date folder (an 8-digit
    ddmmyyyy folder, e.g. "03072026") onward, so results saved under this
    path preserve the same date/sample structure as control/Data -- a
    sample name captured on two different dates won't collide or overwrite
    each other's results. Falls back to just the path's own name if no date
    folder is found (e.g. a run directory outside the dated Data layout)."""
    parts = path.parts
    for i, part in enumerate(parts):
        if _DATE_DIR_RE.match(part):
            return Path(*parts[i:])
    return Path(path.name)


def _git_commit_hash() -> str:
    """Short git commit hash of the code that produced this result, so a
    result can always be traced back to the exact code version -- Results/
    isn't git-tracked itself, so without this there's no other link between
    an output and the code state that generated it. Falls back gracefully
    if git isn't available or this isn't a git checkout."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unversioned"


def main() -> None:
    confirm_sample_directories(SAMPLE_DIRECTORIES)

    base_dir = Path(__file__).resolve().parent / "Results" / "validation_against_theory"
    base_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for sample_dir in SAMPLE_DIRECTORIES:
        sample_dir = Path(sample_dir)
        sample_name = sample_dir.name
        theory = theoretical_matrix(sample_name)

        run = load_run(sample_dir)
        result = reconstruct(run, extinction_ratio=EXTINCTION_RATIO)

        mean_matrix_error = float(np.linalg.norm(result.matrix_mean - theory))
        diff = result.matrix - theory[None, None, :, :]
        per_pixel_error = np.sqrt((diff ** 2).sum(axis=(2, 3)))

        out_dir = base_dir / _date_relative_path(sample_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        np.save(out_dir / "theory.npy", theory)
        np.save(out_dir / "experimental_mean.npy", result.matrix_mean)
        np.save(out_dir / "per_pixel_frobenius_error.npy", per_pixel_error)

        fig, axes = plt.subplots(1, 3, figsize=(12, 4.2))
        panels = [theory, result.matrix_mean, result.matrix_mean - theory]
        titles = ["Theory", "Experiment (mean)", "Experiment - Theory"]
        im = None
        for ax, mat, title in zip(axes, panels, titles):
            im = ax.imshow(mat, cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_title(title, fontsize=10)
            ax.set_xticks(range(3))
            ax.set_yticks(range(3))
        fig.colorbar(im, ax=axes, fraction=0.025, pad=0.02)
        fig.suptitle(f"{sample_name}: theory vs. experiment (Frobenius error {mean_matrix_error:.4f})")
        fig.savefig(out_dir / "comparison.png", dpi=200)
        plt.close(fig)

        fig2, ax2 = plt.subplots(figsize=(5, 4))
        im2 = ax2.imshow(per_pixel_error, cmap="inferno")
        ax2.set_title(f"{sample_name}: per-pixel Frobenius error vs. theory")
        fig2.colorbar(im2, ax=ax2)
        fig2.savefig(out_dir / "error_map.png", dpi=200)
        plt.close(fig2)

        rows.append((sample_name, mean_matrix_error, float(per_pixel_error.mean())))
        print(f"{sample_name}: mean-matrix Frobenius error = {mean_matrix_error:.4f}, "
              f"mean per-pixel Frobenius error = {per_pixel_error.mean():.4f}")

    with open(base_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write("sample      | mean-matrix Frobenius error | mean per-pixel Frobenius error\n")
        for name, mean_err, pix_err in rows:
            fh.write(f"{name:11s} | {mean_err:27.4f} | {pix_err:.4f}\n")
        fh.write("\n--- Provenance ---\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Git commit: {_git_commit_hash()}\n")
        fh.write(f"Sample directories: {SAMPLE_DIRECTORIES}\n")
        fh.write(f"Extinction ratio: {EXTINCTION_RATIO}\n")

    air_row = next((r for r in rows if r[0].lower() == "air"), None)
    print()
    if air_row is not None and air_row[1] > 0:
        print("Comparing each sample's error against air's baseline error:")
        for name, mean_err, _ in rows:
            if name.lower() != "air":
                print(f"  {name}: {mean_err:.4f}  ({mean_err / air_row[1]:.1f}x air's {air_row[1]:.4f})")
        print(
            "\nIf these ratios are all close to 1x, the error looks systematic "
            "(PSG/PSA modeling), not sample-specific -- see the own_code "
            "README's calibration discussion for what to do next."
        )
    else:
        print("No 'air' baseline found among SAMPLE_DIRECTORIES -- add one to "
              "establish whether error is systematic or sample-specific.")

    print(f"\nSaved validation outputs to {base_dir}")


if __name__ == "__main__":
    main()
