"""Empirically compare 4x4 Mueller matrix reconstruction error across every
possible 16-image combination (4 PSG_QWP angles x 4 PSA_QWP angles, the
*same* 4-angle subset used on both sides) drawn from an existing
over-determined N-image capture, plus the full N-image reconstruction as a
baseline. Answers two questions empirically, using data you've already
captured (no new capture needed):

  1. Does using more images (the full capture) actually give a lower
     deviation from the known theoretical matrix than any 16-image subset?
  2. Among all 16-image subsets, which specific 4-angle choice does best?

4x4 discrete only (fixed polarizer + rotating QWP generator/analyzer, per
control/matrix/own_code/DISCRETE/4x4/) -- NOT applicable to continuous
rotation, which has no fixed angle grid to draw discrete subsets from.

Fully self-contained: no imports from control/, subset_error_analysis/, or
angle_subset_comparison/3x3/ -- its own copy of the fixed-polarizer +
rotating-QWP rotation-sandwich physics, image loader, and theoretical-matrix
formulas (kept deliberately in sync with
control/matrix/own_code/DISCRETE/4x4's physics).

To run: edit SAMPLE_DIRECTORY below to point at one existing 4x4 run that
has a full N x N QWP-angle grid (so every 4-angle subset actually has all
16 images present), then:

    python compare_subsets_4x4.py
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
# EDIT THIS to point at one existing 4x4 run with a full QWP-angle grid.
# ---------------------------------------------------------------------------
DATA_ROOT = r"C:\COMPARE_CASES\Data"
SAMPLE_DIRECTORY = r"C:\COMPARE_CASES\Data\03072026\qwp\qwp90"

# How many QWP angles per side to draw into each candidate subset (4 ->
# 16-image combinations, matching a normal 4x4 minimum acquisition).
SUBSET_SIZE = 4

EXTINCTION_RATIO = 0.0
RETARDANCE_DEG = 90.0
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Physics: fixed polarizer + rotating QWP generator/analyzer, the same
# rotation sandwich M(theta) = R(-theta) @ M(0) @ R(theta) as
# control/matrix/own_code/DISCRETE/4x4/mueller_forward_model.py.
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


def mueller_retarder(theta_deg: float, retardance_deg: float = 90.0) -> np.ndarray:
    delta = np.deg2rad(retardance_deg)
    m0 = np.array([
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, np.cos(delta), np.sin(delta)],
        [0, 0, -np.sin(delta), np.cos(delta)],
    ], dtype=np.float64)
    return mueller_rotator(-theta_deg) @ m0 @ mueller_rotator(theta_deg)


def generator_stokes_4x4(psg_qwp_deg: float, psg_polarizer_fixed_deg: float,
                          retardance_deg: float = 90.0, extinction_ratio: float = 0.0) -> np.ndarray:
    s_in = np.array([1.0, 0.0, 0.0, 0.0])
    polarizer = mueller_linear_polarizer(psg_polarizer_fixed_deg, extinction_ratio)
    qwp = mueller_retarder(psg_qwp_deg, retardance_deg)
    return qwp @ polarizer @ s_in


def analyzer_vector_4x4(psa_qwp_deg: float, psa_analyzer_fixed_deg: float,
                         retardance_deg: float = 90.0, extinction_ratio: float = 0.0) -> np.ndarray:
    qwp = mueller_retarder(psa_qwp_deg, retardance_deg)
    analyzer = mueller_linear_polarizer(psa_analyzer_fixed_deg, extinction_ratio)
    return (analyzer @ qwp)[0, :]


# ---------------------------------------------------------------------------
# Theoretical targets, same formulas as
# control/matrix/own_code/DISCRETE/4x4/validate_against_theory.py.
# ---------------------------------------------------------------------------

def theoretical_matrix(sample_name: str) -> np.ndarray:
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


# ---------------------------------------------------------------------------
# Image loading: same "{PSG_QWP}_{PSA_QWP}.ext" filename convention as
# control/matrix/own_code/DISCRETE/4x4/image_loader.py, with PSG_Polarizer/
# PSA_Analyzer read from Config/experiment_config.json's "fixed_angles".
# ---------------------------------------------------------------------------

FILENAME_RE = re.compile(r"^(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")


def load_run(run_dir: str):
    run_dir = Path(run_dir)
    config_path = run_dir / "Config" / "experiment_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["mode"] != "4x4":
        raise ValueError(f"{run_dir} is mode {config['mode']!r}, not '4x4'.")

    fixed_angles = config.get("fixed_angles") or {}
    for name in ("PSG_Polarizer", "PSA_Analyzer"):
        if name not in fixed_angles:
            raise KeyError(
                f"{config_path}'s fixed_angles is missing {name!r}; 4x4 "
                "reconstruction needs both fixed angles to build the "
                "generator/analyzer Stokes vectors."
            )

    image_dir = run_dir / "Images"
    paths = sorted(
        p for p in image_dir.iterdir()
        if p.is_file() and FILENAME_RE.match(p.stem)
    )
    if not paths:
        raise FileNotFoundError(f"No angle-named images found in {image_dir}")

    psg_qwp_angles = np.empty(len(paths), dtype=np.float64)
    psa_qwp_angles = np.empty(len(paths), dtype=np.float64)
    frames = []
    for i, path in enumerate(paths):
        match = FILENAME_RE.match(path.stem)
        psg_qwp_angles[i] = float(match.group(1))
        psa_qwp_angles[i] = float(match.group(2))
        arr = np.array(Image.open(path))
        if arr.ndim == 3:
            arr = arr[..., :3].mean(axis=2)
        frames.append(arr.astype(np.float64))

    images = np.stack(frames, axis=0)
    return psg_qwp_angles, psa_qwp_angles, images, fixed_angles, run_dir.name


# ---------------------------------------------------------------------------
# Reconstruction restricted to an arbitrary subset of already-loaded images.
# ---------------------------------------------------------------------------

def reconstruct_subset(psg_qwp_angles, psa_qwp_angles, images, fixed_angles, indices,
                        retardance_deg=90.0, extinction_ratio=0.0):
    psg_polarizer_fixed = fixed_angles["PSG_Polarizer"]
    psa_analyzer_fixed = fixed_angles["PSA_Analyzer"]

    n = len(indices)
    h = np.empty((n, 16), dtype=np.float64)
    for row, idx in enumerate(indices):
        s = generator_stokes_4x4(psg_qwp_angles[idx], psg_polarizer_fixed,
                                  retardance_deg, extinction_ratio)
        a = analyzer_vector_4x4(psa_qwp_angles[idx], psa_analyzer_fixed,
                                 retardance_deg, extinction_ratio)
        h[row] = np.kron(a, s)

    h_pinv = np.linalg.pinv(h)

    height, width = images.shape[1], images.shape[2]
    b = images[indices].reshape(n, -1)
    m_vec = h_pinv @ b
    matrix_raw = m_vec.T.reshape(height, width, 4, 4)

    # Average the RAW (unnormalized) matrix across pixels first, then
    # normalize once -- a handful of pixels near-zero in m00 would otherwise
    # blow up if normalized before averaging.
    matrix_mean_raw = matrix_raw.mean(axis=(0, 1))
    matrix_mean = matrix_mean_raw / matrix_mean_raw[0, 0]
    return matrix_mean


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
                     extinction_ratio: float, retardance_deg: float) -> None:
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
    lines.append(f"Retardance (deg): {retardance_deg}")

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
            "retardance_deg": retardance_deg,
        },
    }
    (out_dir / "matrices.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    psg_qwp_angles, psa_qwp_angles, images, fixed_angles, sample_name = load_run(SAMPLE_DIRECTORY)
    theory = theoretical_matrix(sample_name)

    unique_angles = sorted(set(psg_qwp_angles.tolist()) & set(psa_qwp_angles.tolist()))
    total_images = len(psg_qwp_angles)
    print(f"Sample: {sample_name}")
    print(f"Unique QWP angles found: {unique_angles} ({total_images} images total)")

    # Mirror the run's date/sample path from under DATA_ROOT (e.g.
    # "03072026/qwp/qwp90") so results from the same sample name captured on
    # different dates don't collide or overwrite each other.
    out_dir = (Path(r"C:\COMPARE_CASES\RESULT") / "angle_subset_comparison" / "4x4"
               / Path(SAMPLE_DIRECTORY).relative_to(Path(DATA_ROOT)))
    out_dir.mkdir(parents=True, exist_ok=True)

    all_indices = np.arange(total_images)
    full_matrix = reconstruct_subset(
        psg_qwp_angles, psa_qwp_angles, images, fixed_angles, all_indices,
        RETARDANCE_DEG, EXTINCTION_RATIO
    )
    full_deviation = float(np.linalg.norm(full_matrix - theory))

    rows = []
    for combo in itertools.combinations(unique_angles, SUBSET_SIZE):
        indices = [i for i in range(total_images)
                   if psg_qwp_angles[i] in combo and psa_qwp_angles[i] in combo]
        if len(indices) != SUBSET_SIZE ** 2:
            print(f"  Skipping {combo}: only {len(indices)}/{SUBSET_SIZE ** 2} images present")
            continue
        matrix_mean = reconstruct_subset(
            psg_qwp_angles, psa_qwp_angles, images, fixed_angles, indices,
            RETARDANCE_DEG, EXTINCTION_RATIO
        )
        deviation = float(np.linalg.norm(matrix_mean - theory))
        rows.append((combo, deviation, matrix_mean))

    rows.sort(key=lambda r: r[1])
    print(f"Lowest-deviation subset: {rows[0][0]}" if rows else "No valid subsets found")

    _write_matrices(out_dir, sample_name, theory, total_images, full_matrix, rows,
                     Path(SAMPLE_DIRECTORY), EXTINCTION_RATIO, RETARDANCE_DEG)

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
    ax.set_title(f"{sample_name}: which QWP-angle combination deviates least from theory "
                 "(lowest bar = best)")
    fig.tight_layout()
    fig.savefig(out_dir / "deviation_chart.png", dpi=200)
    plt.close(fig)

    print(f"\nSaved outputs to {out_dir}")


if __name__ == "__main__":
    main()
