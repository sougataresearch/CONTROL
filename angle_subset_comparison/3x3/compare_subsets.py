"""Empirically compare 3x3 Mueller matrix reconstruction error across every
possible 9-image combination (3 PSG angles x 3 PSA angles, the *same*
3-angle subset used on both sides) drawn from an existing over-determined
N-image capture -- e.g. a 36-image, 6x6-angle run -- plus the full N-image
reconstruction as a baseline. Answers two questions empirically, using data
you've already captured (no new capture needed):

  1. Does using more images (the full 36) actually give a lower error
     against the known theoretical matrix than any 9-image subset?
  2. Among all 9-image subsets, which specific 3-angle choice does best,
     and does the system matrix's condition number (computable without
     knowing theory) predict that ranking?

Fully self-contained: no imports from control/matrix/own_code/ -- its own
copy of the rotation-sandwich physics, image loader, and theoretical-matrix
formulas (kept deliberately in sync with own_code/DISCRETE/3x3's physics,
verified identical up to floating-point noise).

To run: edit SAMPLE_DIRECTORY below to point at one existing 3x3 run that
has a full N x N angle grid (so every 3-angle subset actually has all 9
images present), then:

    python compare_subsets.py
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

import itertools
import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# EDIT THIS to point at one existing 3x3 run with a full angle grid (e.g.
# the 6x6=36-image lp30/lp45/lp90/air datasets).
# ---------------------------------------------------------------------------
DATA_ROOT = r"C:\COMPARE_CASES\Data"
SAMPLE_DIRECTORY = r"C:\COMPARE_CASES\Data\03072026\lp\lp30"

# How many angles per side to draw into each candidate subset (3 -> 9-image
# combinations, matching a normal 3x3 minimum acquisition).
SUBSET_SIZE = 3

EXTINCTION_RATIO = 0.0
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Physics: the rotation sandwich M(theta) = R(-theta) @ M(0) @ R(theta),
# same as own_code/DISCRETE/3x3/mueller_forward_model.py.
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
# Theoretical targets, same formulas as own_code/DISCRETE/3x3/validate_against_theory.py
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
# Image loading: same "psg_psa.ext" filename convention as own_code/DISCRETE/3x3.
# ---------------------------------------------------------------------------

FILENAME_RE = re.compile(r"^(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")


def load_run(run_dir: str):
    run_dir = Path(run_dir)
    config_path = run_dir / "Config" / "experiment_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["mode"] != "3x3":
        raise ValueError(f"{run_dir} is mode {config['mode']!r}, not '3x3'.")

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
    # normalize once -- not the reverse. A handful of pixels can have a raw
    # m00 near zero (real sensor/reconstruction noise), and normalizing
    # each pixel individually before averaging lets those few pixels'
    # division blow-ups dominate the mean completely. Averaging raw values
    # first is naturally robust to that: a handful of bad pixels contribute
    # negligibly to a sum over millions, whereas post-normalization they can
    # produce errors many orders of magnitude too large.
    matrix_mean_raw = matrix_raw.mean(axis=(0, 1))
    matrix_mean = matrix_mean_raw / matrix_mean_raw[0, 0]
    return matrix_mean, condition_number


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


def _write_matrices(out_dir: Path, sample_name: str, theory: np.ndarray, total_images: int,
                     full_matrix: np.ndarray, rows: list, run_dir: Path,
                     extinction_ratio: float) -> None:
    lines = []
    lines.append(f"Sample: {sample_name}")
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
    lines.append(f"Source run: {run_dir}")
    lines.append(f"Extinction ratio: {extinction_ratio}")

    (out_dir / "matrices.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {
        "sample_name": sample_name,
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
            "source_run": str(run_dir),
            "extinction_ratio": extinction_ratio,
        },
    }
    (out_dir / "matrices.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    psg_angles, psa_angles, images, sample_name = load_run(SAMPLE_DIRECTORY)
    theory = theoretical_matrix(sample_name)

    unique_angles = sorted(set(psg_angles.tolist()) & set(psa_angles.tolist()))
    total_images = len(psg_angles)
    print(f"Sample: {sample_name}")
    print(f"Unique angles found: {unique_angles} ({total_images} images total)")

    # Mirror the run's date/sample path from under DATA_ROOT (e.g.
    # "03072026/lp/lp30") so results from the same sample name captured on
    # different dates don't collide or overwrite each other.
    out_dir = (Path(__file__).resolve().parent / "Results"
               / Path(SAMPLE_DIRECTORY).relative_to(Path(DATA_ROOT)))
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
            print(f"  Skipping {combo}: only {len(indices)}/{SUBSET_SIZE ** 2} images present")
            continue
        matrix_mean, _ = reconstruct_subset(
            psg_angles, psa_angles, images, indices, EXTINCTION_RATIO
        )
        deviation = float(np.linalg.norm(matrix_mean - theory))
        rows.append((combo, deviation, matrix_mean))

    rows.sort(key=lambda r: r[1])
    print(f"Lowest-deviation subset: {rows[0][0]}" if rows else "No valid subsets found")

    _write_matrices(out_dir, sample_name, theory, total_images, full_matrix, rows,
                     Path(SAMPLE_DIRECTORY), EXTINCTION_RATIO)

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

    print(f"\nSaved outputs to {out_dir}")


if __name__ == "__main__":
    main()
