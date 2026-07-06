"""Validate the reflection-mode 4x4 solver against samples with a
computable theoretical Mueller matrix (bare substrate, or substrate + one
thin film -- see reflection_theory.py/theoretical_mueller.py), using the
exact same reconstruction as main.py.

Unlike the transmission-mode validate_against_theory.py (where "air" gives
a universal known-identity baseline), a reflection sample's theoretical
matrix depends on real physical parameters you must supply -- there's no
single ideal reference every sample should reduce to. So for each
configured sample, this prompts you for that sample's physical parameters
(wavelength, angle of incidence, substrate/film n & k, film thickness --
same prompts as theoretical_mueller.py, sharing its .theory_log.csv so a
value you entered once is suggested again next time, editable at any time)
computes the theoretical matrix, reconstructs the real capture, and reports
both the Frobenius-norm error and the mean squared error (MSE) between them.

To run: edit SAMPLE_DIRECTORIES below to list every reflection dataset you
want checked (folder name is used as this sample's label in
.theory_log.csv), then:

    python validate_against_theory.py

Before processing anything, it prints every configured folder with an
OK/MISSING check and asks you to confirm the list is complete and correct.
You'll then be prompted for the polarizer extinction ratio and QWP
retardance (shared with main.py's .last_calibration.json in this folder),
applied to every sample so they're all reconstructed on an equal footing --
and, per sample, for that sample's physical parameters as described above.

If main.py already reconstructed a given sample with this exact
extinction_ratio/retardance_deg (saved under its default
Results/<date-relative-path> location), that cached reconstruction is
reused instead of redoing it from raw images.
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from image_loader import load_run
from solve_mueller import reconstruct
from theoretical_mueller import get_or_prompt_matrix

# ---------------------------------------------------------------------------
# EDIT THIS: every reflection dataset to check. Folder name is used as this
# sample's label in .theory_log.csv (theoretical_mueller.py's parameter log).
# ---------------------------------------------------------------------------
SAMPLE_DIRECTORIES = [
    r"C:\COMPARE_CASES\Data\reflection\03072026\si_bare",
    r"C:\COMPARE_CASES\Data\reflection\03072026\si_sio2_100nm",
]
# ---------------------------------------------------------------------------

RESULT_ROOT = Path(r"C:\COMPARE_CASES\RESULT")

# Shared with main.py in this same folder.
_CALIBRATION_STATE_PATH = Path(__file__).resolve().parent / ".last_calibration.json"


def _load_last_calibration() -> dict:
    try:
        return json.loads(_CALIBRATION_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_last_calibration(values: dict) -> None:
    _CALIBRATION_STATE_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")


def ask_float(prompt: str, default: float) -> float:
    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


def confirm_sample_directories(paths: list) -> None:
    print("Sample directories configured for validation:")
    missing = []
    for path in paths:
        exists = Path(path).is_dir()
        print(f"  [{'OK' if exists else 'MISSING'}] {path}")
        if not exists:
            missing.append(path)

    if missing:
        print(f"\n{len(missing)} folder(s) not found. Fix SAMPLE_DIRECTORIES at "
              "the top of this file, then run again.")
        raise SystemExit(1)

    answer = input(
        f"\nAll {len(paths)} folder(s) exist. Is this the complete and "
        "correct list of samples to validate? [y/N]: "
    ).strip().lower()
    if answer not in ("y", "yes"):
        print("Edit SAMPLE_DIRECTORIES at the top of this file, then run again.")
        raise SystemExit(0)


_DATE_DIR_RE = re.compile(r"^\d{8}$")


def _date_relative_path(path: Path) -> Path:
    parts = path.parts
    for i, part in enumerate(parts):
        if _DATE_DIR_RE.match(part):
            return Path(*parts[i:])
    return Path(path.name)


@dataclass
class _CachedResult:
    matrix: np.ndarray
    matrix_mean: np.ndarray


def _load_cached_reconstruction(sample_dir: Path, extinction_ratio: float, retardance_deg: float):
    cache_dir = (RESULT_ROOT / "reflection" / "4x4" / "reconstructions"
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

    matrix = np.load(matrix_path)
    matrix_raw = np.load(raw_path)
    mean_raw = matrix_raw.mean(axis=(0, 1))
    matrix_mean = mean_raw / mean_raw[0, 0]
    return _CachedResult(matrix=matrix, matrix_mean=matrix_mean)


def _git_commit_hash() -> str:
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

    base_dir = RESULT_ROOT / "reflection" / "4x4" / "validation_against_theory"
    base_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for sample_dir in SAMPLE_DIRECTORIES:
        sample_dir = Path(sample_dir)
        sample_label = sample_dir.name
        print(f"\n--- {sample_label}: theoretical parameters ---")
        theory = get_or_prompt_matrix(sample_label)

        result = _load_cached_reconstruction(sample_dir, extinction_ratio, retardance_deg)
        if result is not None:
            print(f"{sample_label}: reusing main.py's cached reconstruction (matching calibration)")
        else:
            run = load_run(sample_dir)
            result = reconstruct(run, extinction_ratio=extinction_ratio, retardance_deg=retardance_deg)

        diff_mean = result.matrix_mean - theory
        frobenius_error = float(np.linalg.norm(diff_mean))
        mse = float(np.mean(diff_mean ** 2))

        diff_per_pixel = result.matrix - theory[None, None, :, :]
        per_pixel_frobenius = np.sqrt((diff_per_pixel ** 2).sum(axis=(2, 3)))
        per_pixel_mse = (diff_per_pixel ** 2).mean(axis=(2, 3))

        out_dir = base_dir / _date_relative_path(sample_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        np.save(out_dir / "theory.npy", theory)
        np.save(out_dir / "experimental_mean.npy", result.matrix_mean)
        np.save(out_dir / "per_pixel_frobenius_error.npy", per_pixel_frobenius)
        np.save(out_dir / "per_pixel_mse.npy", per_pixel_mse)

        fig, axes = plt.subplots(1, 3, figsize=(13, 4.5))
        panels = [theory, result.matrix_mean, diff_mean]
        titles = ["Theory", "Experiment (mean)", "Experiment - Theory"]
        im = None
        for ax, mat, title in zip(axes, panels, titles):
            im = ax.imshow(mat, cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_title(title, fontsize=10)
            ax.set_xticks(range(4))
            ax.set_yticks(range(4))
        fig.colorbar(im, ax=axes, fraction=0.025, pad=0.02)
        fig.suptitle(f"{sample_label}: theory vs. experiment "
                     f"(Frobenius error {frobenius_error:.4f}, MSE {mse:.6f})")
        fig.savefig(out_dir / "comparison.png", dpi=200)
        plt.close(fig)

        fig2, axes2 = plt.subplots(1, 2, figsize=(10, 4))
        im2a = axes2[0].imshow(per_pixel_frobenius, cmap="inferno")
        axes2[0].set_title(f"{sample_label}: per-pixel Frobenius error")
        fig2.colorbar(im2a, ax=axes2[0])
        im2b = axes2[1].imshow(per_pixel_mse, cmap="inferno")
        axes2[1].set_title(f"{sample_label}: per-pixel MSE")
        fig2.colorbar(im2b, ax=axes2[1])
        fig2.savefig(out_dir / "error_map.png", dpi=200)
        plt.close(fig2)

        rows.append((sample_label, frobenius_error, mse,
                     float(per_pixel_frobenius.mean()), float(per_pixel_mse.mean())))
        print(f"{sample_label}: Frobenius error (mean matrix) = {frobenius_error:.4f}, "
              f"MSE (mean matrix) = {mse:.6f}")
        print(f"{sample_label}: mean per-pixel Frobenius error = {per_pixel_frobenius.mean():.4f}, "
              f"mean per-pixel MSE = {per_pixel_mse.mean():.6f}")

    with open(base_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write("sample                | Frobenius error | MSE      | mean px Frobenius | mean px MSE\n")
        for name, frob, mse, px_frob, px_mse in rows:
            fh.write(f"{name:22s} | {frob:15.4f} | {mse:.6f} | {px_frob:18.4f} | {px_mse:.6f}\n")
        fh.write("\n--- Provenance ---\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Git commit: {_git_commit_hash()}\n")
        fh.write(f"Sample directories: {SAMPLE_DIRECTORIES}\n")
        fh.write(f"Extinction ratio: {extinction_ratio}\n")
        fh.write(f"Retardance (deg): {retardance_deg}\n")

    print(f"\nSaved validation outputs to {base_dir}")


if __name__ == "__main__":
    main()
