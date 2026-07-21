"""Standalone tool: for every over-determined 3x3-Mueller-matrix capture found
under a data root (36 images = 6 PSG angles x 6 PSA angles), compare the
reconstruction error of the full 36-image (over-determined) fit against every
possible 9-image (3-angle x 3-angle) subset drawn from the same data, ranked
against the known theoretical matrix.

Answers, per sample and overall:
  1. Does using all 36 images actually beat every 9-image subset?
  2. Which specific 3-angle combo (e.g. (0,60,120) vs (0,30,60)) gives the
     lowest error, and is that consistent across samples?

This folder is fully self-contained: its own copy of the rotation-sandwich
physics, image loader, and theoretical-matrix formulas. It does not import
anything from control/ or angle_subset_comparison/. Results are written
under the shared RESULT/subset_error_analysis/3x3/ tree (see RESULTS_DIR
below) -- data under Data/ is only ever read.

Usage:
    python analyze_subsets.py

The script scans DATA_ROOT for every run folder, prints the list of runs it
found, and asks the operator to confirm the list is complete before doing any
analysis (in case a run folder is missing or DATA_ROOT needs adjusting).
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
    print(f"Installing missing dependencies: {', '.join(missing)}", flush=True)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    except subprocess.CalledProcessError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--break-system-packages", *missing]
        )

    # The user site-packages directory may not have existed when this
    # interpreter started, so its path-finder cache can be stale even though
    # site.getusersitepackages() is already on sys.path. Refresh it.
    import site
    importlib.invalidate_caches()
    site.addsitedir(site.getusersitepackages())


_ensure_dependencies()

import itertools
import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# EDIT THIS if your captures live somewhere else. The script recursively
# scans every subfolder of DATA_ROOT for 3x3-mode runs.
# ---------------------------------------------------------------------------
DATA_ROOT = r"C:\COMPARE_CASES\Data"

# How many angles per side to draw into each candidate subset (3 -> 9-image
# combinations, matching the minimum acquisition for a 3x3 matrix).
SUBSET_SIZE = 3

# A run is only usable for this analysis if it has at least this many unique
# angles per side (need >= SUBSET_SIZE to form any subset, and > SUBSET_SIZE
# for the comparison to be meaningful against an over-determined baseline).
MIN_UNIQUE_ANGLES = SUBSET_SIZE + 1

EXTINCTION_RATIO = 0.0

RESULTS_DIR = Path(r"C:\COMPARE_CASES\RESULT") / "subset_error_analysis" / "3x3"
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Physics: the rotation sandwich M(theta) = R(-theta) @ M(0) @ R(theta).
# ---------------------------------------------------------------------------

def mueller_rotator(theta_deg: float) -> np.ndarray:
    t = np.deg2rad(theta_deg)
    c, s = np.cos(2 * t), np.sin(2 * t)
    return np.array([
        [1, 0, 0, 0],
        [0, c, s, 0],
        [0, -s, c, 0],
        [0, 0, 0, 1],
    ], dtype=np.float64)


def mueller_linear_polarizer(theta_deg: float, extinction_ratio: float = 0.0) -> np.ndarray:
    k = extinction_ratio
    m0 = 0.5 * np.array([
        [1 + k, 1 - k, 0, 0],
        [1 - k, 1 + k, 0, 0],
        [0, 0, 2 * np.sqrt(k), 0],
        [0, 0, 0, 2 * np.sqrt(k)],
    ], dtype=np.float64)
    return mueller_rotator(-theta_deg) @ m0 @ mueller_rotator(theta_deg)


def generator_stokes_3x3(psg_polarizer_deg: float, extinction_ratio: float = 0.0) -> np.ndarray:
    s_in = np.array([1.0, 0.0, 0.0, 0.0])
    return (mueller_linear_polarizer(psg_polarizer_deg, extinction_ratio) @ s_in)[:3]


def analyzer_vector_3x3(psa_analyzer_deg: float, extinction_ratio: float = 0.0) -> np.ndarray:
    return mueller_linear_polarizer(psa_analyzer_deg, extinction_ratio)[0, :3]


# ---------------------------------------------------------------------------
# Theoretical targets.
# ---------------------------------------------------------------------------

def theoretical_matrix(sample_name: str) -> np.ndarray:
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


# ---------------------------------------------------------------------------
# Run discovery + loading.
# ---------------------------------------------------------------------------

FILENAME_RE = re.compile(r"^(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")


def discover_runs(data_root: str) -> list[Path]:
    """Recursively find every folder under data_root that is a 3x3-mode run
    (has Config/experiment_config.json with mode == '3x3')."""
    root = Path(data_root)
    runs = []
    for config_path in root.rglob("Config/experiment_config.json"):
        run_dir = config_path.parent.parent
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if config.get("mode") == "3x3":
            runs.append(run_dir)
    return sorted(runs)


def load_run(run_dir: Path):
    image_dir = run_dir / "Images"
    paths = sorted(
        p for p in image_dir.iterdir()
        if p.is_file() and FILENAME_RE.match(p.stem)
    )
    if not paths:
        raise FileNotFoundError(f"No angle-named images found in {image_dir}")

    psg_angles = np.empty(len(paths), dtype=np.float64)
    psa_angles = np.empty(len(paths), dtype=np.float64)
    frames = []
    for i, path in enumerate(paths):
        match = FILENAME_RE.match(path.stem)
        psg_angles[i] = float(match.group(1))
        psa_angles[i] = float(match.group(2))
        arr = np.array(Image.open(path))
        if arr.ndim == 3:
            arr = arr[..., :3].mean(axis=2)
        frames.append(arr.astype(np.float64))

    images = np.stack(frames, axis=0)
    return psg_angles, psa_angles, images, run_dir.name


# ---------------------------------------------------------------------------
# Reconstruction restricted to an arbitrary subset of already-loaded images.
# ---------------------------------------------------------------------------

def reconstruct_subset(psg_angles, psa_angles, images, indices, extinction_ratio=0.0):
    n = len(indices)
    h = np.empty((n, 9), dtype=np.float64)
    for row, idx in enumerate(indices):
        s = generator_stokes_3x3(psg_angles[idx], extinction_ratio)
        a = analyzer_vector_3x3(psa_angles[idx], extinction_ratio)
        h[row] = np.kron(a, s)

    h_pinv = np.linalg.pinv(h)
    condition_number = float(np.linalg.cond(h))

    height, width = images.shape[1], images.shape[2]
    b = images[indices].reshape(n, -1)
    m_vec = h_pinv @ b
    matrix_raw = m_vec.T.reshape(height, width, 3, 3)

    # Average the RAW (unnormalized) matrix across pixels first, then
    # normalize once -- a handful of pixels near-zero in m00 would otherwise
    # blow up if normalized before averaging.
    matrix_mean_raw = matrix_raw.mean(axis=(0, 1))
    matrix_mean = matrix_mean_raw / matrix_mean_raw[0, 0]
    return matrix_mean, condition_number


# ---------------------------------------------------------------------------
# Full Mueller-matrix dump per subset (text + JSON) -- just the matrix values
# and their difference from theory, no scalar error/rank/condition tables.
# ---------------------------------------------------------------------------

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


def _write_matrices(out_dir: Path, sample_name: str, run_dir: Path, theory: np.ndarray,
                     total_images: int, full_matrix: np.ndarray, rows: list,
                     extinction_ratio: float) -> None:
    lines = []
    lines.append(f"Sample: {sample_name}")
    lines.append(f"Source: {run_dir}")
    lines.append("")
    lines.append("Theoretical Mueller matrix:")
    lines.append(_format_matrix(theory))
    lines.append("")
    lines.append(f"Full {total_images}-image (all angles) reconstruction:")
    lines.append(_format_matrix(full_matrix))
    lines.append("Difference from theory:")
    lines.append(_format_matrix(full_matrix - theory))
    lines.append("")
    lines.append("=" * 70)

    json_rows = []
    for combo, _deviation, matrix in rows:
        diff = matrix - theory
        lines.append(f"\nSubset {combo}:")
        lines.append("Reconstructed Mueller matrix:")
        lines.append(_format_matrix(matrix))
        lines.append("Difference from theory (reconstructed - theory):")
        lines.append(_format_matrix(diff))

        json_rows.append({
            "angle_subset": list(combo),
            "matrix": matrix.tolist(),
            "difference_from_theory": diff.tolist(),
        })

    lines.append("")
    lines.append("--- Provenance ---")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Git commit: {_git_commit_hash()}")
    lines.append(f"Extinction ratio: {extinction_ratio}")

    (out_dir / "matrices.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {
        "sample_name": sample_name,
        "source": str(run_dir),
        "theoretical_matrix": theory.tolist(),
        "full_baseline": {
            "total_images": total_images,
            "matrix": full_matrix.tolist(),
            "difference_from_theory": (full_matrix - theory).tolist(),
        },
        "subsets": json_rows,
        "provenance": {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "git_commit": _git_commit_hash(),
            "extinction_ratio": extinction_ratio,
        },
    }
    (out_dir / "matrices.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-sample analysis.
# ---------------------------------------------------------------------------

def analyze_run(run_dir: Path) -> dict | None:
    psg_angles, psa_angles, images, sample_name = load_run(run_dir)
    unique_angles = sorted(set(psg_angles.tolist()) & set(psa_angles.tolist()))
    total_images = len(psg_angles)

    if len(unique_angles) < MIN_UNIQUE_ANGLES:
        print(f"  Skipping {sample_name} ({run_dir}): only {len(unique_angles)} "
              f"unique angle(s) present, need >= {MIN_UNIQUE_ANGLES} for this analysis.")
        return None

    try:
        theory = theoretical_matrix(sample_name)
    except ValueError as exc:
        print(f"  Skipping {sample_name} ({run_dir}): {exc}")
        return None

    # Mirror the run's date/sample path from under DATA_ROOT (e.g.
    # "03072026/lp/lp30") so results from the same sample name captured on
    # different dates don't collide or overwrite each other.
    out_dir = RESULTS_DIR / run_dir.relative_to(Path(DATA_ROOT))
    out_dir.mkdir(parents=True, exist_ok=True)

    all_indices = np.arange(total_images)
    full_matrix, _ = reconstruct_subset(
        psg_angles, psa_angles, images, all_indices, EXTINCTION_RATIO
    )
    full_deviation = float(np.linalg.norm(full_matrix - theory))

    rows = []
    for combo in itertools.combinations(unique_angles, SUBSET_SIZE):
        indices = [i for i in range(total_images)
                   if psg_angles[i] in combo and psa_angles[i] in combo]
        if len(indices) != SUBSET_SIZE ** 2:
            continue
        matrix_mean, _ = reconstruct_subset(
            psg_angles, psa_angles, images, indices, EXTINCTION_RATIO
        )
        deviation = float(np.linalg.norm(matrix_mean - theory))
        rows.append((combo, deviation, matrix_mean))

    rows.sort(key=lambda r: r[1])

    print(f"\n=== {sample_name} ({run_dir}) ===")
    print(f"Unique angles: {unique_angles} ({total_images} images total)")
    print(f"Lowest-deviation subset: {rows[0][0]}" if rows else "No valid subsets found")

    _write_matrices(out_dir, sample_name, run_dir, theory, total_images, full_matrix, rows,
                     EXTINCTION_RATIO)

    # One bar chart: every angle-subset combination plus the full-angle
    # capture, sorted so the lowest bar is the combination that deviates
    # least from the theoretical Mueller matrix.
    all_entries = rows + [(("all", total_images), full_deviation, full_matrix)]
    all_entries.sort(key=lambda r: r[1])
    labels = [
        "+".join(f"{a:g}" for a in combo) if combo[0] != "all" else f"ALL ({combo[1]} imgs)"
        for combo, _, _ in all_entries
    ]
    deviations = [d for _, d, _ in all_entries]
    colors = ["tab:orange" if combo[0] == "all" else "tab:blue" for combo, _, _ in all_entries]

    fig, ax = plt.subplots(figsize=(max(10, len(all_entries) * 0.5), 5))
    ax.bar(range(len(all_entries)), deviations, color=colors)
    ax.set_xticks(range(len(all_entries)))
    ax.set_xticklabels(labels, rotation=90, fontsize=7)
    ax.set_ylabel("Deviation from theoretical Mueller matrix")
    ax.set_title(f"{sample_name}: which angle combination deviates least from theory "
                 "(lowest bar = best)")
    fig.tight_layout()
    fig.savefig(out_dir / "deviation_chart.png", dpi=200)
    plt.close(fig)

    return {
        "sample_name": sample_name,
        "run_dir": str(run_dir),
        "total_images": total_images,
        "full_deviation": full_deviation,
        "full_matrix": full_matrix,
        "theory": theory,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Main: discover, confirm with operator, analyze, summarize.
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Scanning for 3x3-mode runs under: {DATA_ROOT}\n")
    runs = discover_runs(DATA_ROOT)

    if not runs:
        print("No 3x3-mode runs found. Check DATA_ROOT at the top of this script.")
        return

    print(f"Found {len(runs)} run folder(s):")
    for run_dir in runs:
        print(f"  - {run_dir}")

    answer = input(
        "\nIs this the complete, correct list of runs to analyze? "
        "[y/N, or edit DATA_ROOT and rerun]: "
    ).strip().lower()
    if answer not in ("y", "yes"):
        print("Aborting. Adjust DATA_ROOT at the top of analyze_subsets.py and rerun.")
        return

    results = []
    for run_dir in runs:
        try:
            result = analyze_run(run_dir)
        except Exception as exc:
            print(f"  Error analyzing {run_dir}: {exc}")
            continue
        if result is not None:
            results.append(result)

    if not results:
        print("\nNo runs were suitable for subset analysis (need an over-determined "
              f"grid with >= {MIN_UNIQUE_ANGLES} unique angles per side).")
        return

    print(f"\nAll results saved under: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
