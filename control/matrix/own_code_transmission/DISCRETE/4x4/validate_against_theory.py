"""Phase 2: validate the 4x4 solver against samples with a known theoretical
Mueller matrix -- air (should reconstruct to identity), a linear polarizer
at a known angle, and a QWP at a known angle -- using the exact same
reconstruction as main.py.

Deliberate duplicate in spirit of ../3x3/validate_against_theory.py, but the
theoretical matrices here are NOT hand-derived: they call
mueller_forward_model.py's own mueller_linear_polarizer()/mueller_retarder()
directly. 4x4 matrices carry extra circular-polarization (S3-coupled) terms
that are easy to get wrong by hand-typing a formula -- reusing the exact
function the reconstruction itself is built from means any mismatch you see
here reflects a real calibration issue (angle offset, extinction ratio,
retardance), not a bug in a separately hand-derived "theory" formula.

This is the calibration baseline: if air's error and the LP/QWP samples'
error look similar (same pattern, similar magnitude), the discrepancy is a
systematic PSG/PSA modeling problem, not something sample-specific -- see
NAMING.md and the own_code READMEs for the fuller discussion of what to do
next in that case.

To run: edit SAMPLE_DIRECTORIES below to list every dataset you want
checked (folder name must be "air", "lp<angle>", or "qwp<angle>" so the
theoretical target can be inferred -- extend theoretical_matrix() for other
known references), then:

    python validate_against_theory.py

Before processing anything, it prints every configured folder with an
OK/MISSING check and asks you to confirm the list is complete and correct
-- a forgotten or mistyped folder would otherwise silently produce one
fewer comparison with no error. You will then be prompted for the polarizer
extinction ratio and QWP retardance (press Enter on either to accept the
suggested default -- whatever was last used by this script or main.py in
this same folder, remembered in .last_calibration.json), applied to every
sample in this run so they're all compared on an equal footing.

If main.py already reconstructed a given sample with this exact calibration
(saved under its default Results/<date-relative-path> location), that
cached reconstruction is reused instead of redoing it from raw images --
only on an exact calibration match, otherwise it's recomputed fresh.
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

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from image_loader import dark_reference_available, load_run
from mueller_forward_model import mueller_linear_polarizer, mueller_retarder
from solve_mueller import reconstruct

# ---------------------------------------------------------------------------
# EDIT THIS: every dataset to check against its known theoretical matrix.
# Folder name drives theoretical_matrix() below -- "air", "lp<angle>", or
# "qwp<angle>". Each folder must be 4x4 mode (Config/experiment_config.json
# with "fixed_angles" for PSG_Polarizer/PSA_Analyzer).
# ---------------------------------------------------------------------------
SAMPLE_DIRECTORIES = [
    r"C:\COMPARE_CASES\control\Data\03072026\qwp\air",
    r"C:\COMPARE_CASES\control\Data\03072026\qwp\lp30",
    r"C:\COMPARE_CASES\control\Data\03072026\qwp\lp45",
    r"C:\COMPARE_CASES\control\Data\03072026\qwp\qwp90",
]
# ---------------------------------------------------------------------------

RESULT_ROOT = Path(r"C:\COMPARE_CASES\RESULT")

# Shared with main.py in this same folder, so "last used" reflects whichever
# of the two scripts you ran most recently -- not committed to git.
_CALIBRATION_STATE_PATH = Path(__file__).resolve().parent / ".last_calibration.json"
_CALIBRATION_LOG_PATH = Path(__file__).resolve().parent / ".calibration_log.csv"


def _load_last_calibration() -> dict:
    try:
        return json.loads(_CALIBRATION_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_last_calibration(values: dict) -> None:
    _CALIBRATION_STATE_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")


def _append_calibration_log(extinction_ratio: float, retardance_deg: float) -> None:
    is_new = not _CALIBRATION_LOG_PATH.exists()
    with open(_CALIBRATION_LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(["timestamp", "run_directory", "extinction_ratio", "retardance_deg"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"),
                          "validate_against_theory (multiple)", extinction_ratio, retardance_deg])


def ask_float(prompt: str, default: float) -> float:
    """Ask for a numeric value, showing ``default`` in brackets; press Enter
    (blank input) to accept it as-is. Loops until a parseable number is
    entered."""

    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


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
    """Infer the ideal 4x4 Mueller matrix from the sample's folder name --
    "air" -> identity, "lp<angle>" -> mueller_linear_polarizer() at that
    angle (ideal, extinction 0), "qwp<angle>" -> mueller_retarder() at that
    angle (ideal, retardance 90). Calls mueller_forward_model.py's own
    functions rather than a separately hand-derived formula -- see the
    module docstring for why that matters for 4x4 specifically. Add a case
    here for any other known reference sample."""

    name = sample_name.lower()
    if name == "air":
        return np.eye(4)

    match = re.match(r"^lp(-?\d+(?:\.\d+)?)$", name)
    if match:
        theta = float(match.group(1))
        return mueller_linear_polarizer(theta, extinction_ratio=0.0)

    match = re.match(r"^qwp(-?\d+(?:\.\d+)?)$", name)
    if match:
        theta = float(match.group(1))
        return mueller_retarder(theta, retardance_deg=90.0)

    raise ValueError(
        f"No known theoretical matrix for sample {sample_name!r} -- "
        "add a case to theoretical_matrix() for this reference."
    )


@dataclass
class _CachedResult:
    matrix: np.ndarray
    matrix_mean: np.ndarray
    dark_subtracted: bool


def _load_cached_reconstruction(sample_dir: Path, extinction_ratio: float, retardance_deg: float):
    """If main.py already reconstructed this exact sample with this exact
    calibration AND dark-current status, reuse its saved output instead of
    redoing the reconstruction from raw images -- solve_mueller.reconstruct()
    is deterministic given the same images/extinction_ratio/retardance_deg, so
    recomputing it here would just be duplicate work. Returns None (falls back
    to a fresh reconstruction) if main.py hasn't been run for this sample, was
    run with different calibration values, or its dark-subtraction status no
    longer matches this run's current state (e.g. a dark reference was added
    since) -- a stale/mismatched cache would silently corrupt the comparison,
    so it's only reused on an exact match. Assumes main.py used its default
    output location (Results/<date-relative-path> next to main.py); a custom
    --out won't be found here."""

    cache_dir = (RESULT_ROOT / "transmission" / "4x4" / "reconstructions"
                 / _date_relative_path(sample_dir))
    calibration_path = cache_dir / "calibration_used.json"
    matrix_path = cache_dir / "mueller_matrix_normalized.npy"
    raw_path = cache_dir / "mueller_matrix_raw.npy"
    if not (calibration_path.exists() and matrix_path.exists() and raw_path.exists()):
        return None

    try:
        cached_calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if (cached_calibration.get("extinction_ratio") != extinction_ratio
            or cached_calibration.get("retardance_deg") != retardance_deg):
        return None
    if cached_calibration.get("dark_subtracted") != dark_reference_available(sample_dir):
        return None

    matrix = np.load(matrix_path)
    matrix_raw = np.load(raw_path)
    mean_raw = matrix_raw.mean(axis=(0, 1))
    matrix_mean = mean_raw / mean_raw[0, 0]
    return _CachedResult(matrix=matrix, matrix_mean=matrix_mean,
                          dark_subtracted=cached_calibration["dark_subtracted"])


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


def _format_matrix(m: np.ndarray) -> str:
    return "\n".join("    [" + ", ".join(f"{v:+.4f}" for v in row) + "]" for row in m)


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

    last_calibration = _load_last_calibration()
    extinction_ratio = ask_float(
        "Polarizer extinction ratio Imin/Imax", last_calibration.get("extinction_ratio", 0.0)
    )
    retardance_deg = ask_float(
        "QWP retardance in degrees", last_calibration.get("retardance_deg", 90.0)
    )
    _save_last_calibration({"extinction_ratio": extinction_ratio, "retardance_deg": retardance_deg})
    _append_calibration_log(extinction_ratio, retardance_deg)

    base_dir = RESULT_ROOT / "transmission" / "4x4" / "validation_against_theory"
    base_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    matrix_blocks = []
    for sample_dir in SAMPLE_DIRECTORIES:
        sample_dir = Path(sample_dir)
        sample_name = sample_dir.name
        theory = theoretical_matrix(sample_name)

        result = _load_cached_reconstruction(sample_dir, extinction_ratio, retardance_deg)
        if result is not None:
            dark_subtracted = result.dark_subtracted
            print(f"{sample_name}: reusing main.py's cached reconstruction (matching calibration)")
        else:
            run = load_run(sample_dir)
            result = reconstruct(run, extinction_ratio=extinction_ratio, retardance_deg=retardance_deg)
            dark_subtracted = run.dark_subtracted
        print(f"{sample_name}: dark-current subtraction "
              f"{'applied' if dark_subtracted else 'NOT applied'}")

        mean_matrix_error = float(np.linalg.norm(result.matrix_mean - theory))
        rmse = float(np.sqrt(np.mean((result.matrix_mean - theory) ** 2)))

        matrix_blocks.append(
            f"\n=== {sample_name} ===\n"
            f"Theoretical Mueller matrix:\n{_format_matrix(theory)}\n"
            f"Experimental Mueller matrix (mean):\n{_format_matrix(result.matrix_mean)}\n"
            f"Deviation (RMS): {rmse:.6f}\n"
        )

        diff = result.matrix - theory[None, None, :, :]
        per_pixel_error = np.sqrt((diff ** 2).sum(axis=(2, 3)))

        out_dir = base_dir / _date_relative_path(sample_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        np.save(out_dir / "theory.npy", theory)
        np.save(out_dir / "experimental_mean.npy", result.matrix_mean)
        np.save(out_dir / "per_pixel_frobenius_error.npy", per_pixel_error)

        fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
        panels = [theory, result.matrix_mean, result.matrix_mean - theory]
        titles = ["Theory", "Experiment (mean)", "Experiment - Theory"]
        im = None
        for ax, mat, title in zip(axes, panels, titles):
            im = ax.imshow(mat, cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_title(title, fontsize=10)
            ax.set_xticks(range(4))
            ax.set_yticks(range(4))
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

        rows.append((sample_name, mean_matrix_error, float(per_pixel_error.mean()), dark_subtracted))
        print(f"{sample_name}: mean-matrix Frobenius error = {mean_matrix_error:.4f}, "
              f"mean per-pixel Frobenius error = {per_pixel_error.mean():.4f}")

    with open(base_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write("sample      | mean-matrix Frobenius error | mean per-pixel Frobenius error | dark subtracted\n")
        for name, mean_err, pix_err, dark_subtracted in rows:
            fh.write(f"{name:11s} | {mean_err:27.4f} | {pix_err:.4f} | {dark_subtracted}\n")
        fh.write("\n--- Per-sample Mueller matrices (mean-matrix comparison) ---\n")
        fh.write("".join(matrix_blocks))
        fh.write("\n--- Provenance ---\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Git commit: {_git_commit_hash()}\n")
        fh.write(f"Sample directories: {SAMPLE_DIRECTORIES}\n")
        fh.write(f"Extinction ratio: {extinction_ratio}\n")
        fh.write(f"Retardance (deg): {retardance_deg}\n")

    air_row = next((r for r in rows if r[0].lower() == "air"), None)
    print()
    if air_row is not None and air_row[1] > 0:
        print("Comparing each sample's error against air's baseline error:")
        for name, mean_err, _, _ in rows:
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
