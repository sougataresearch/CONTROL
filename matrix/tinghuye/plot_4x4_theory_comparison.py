from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import TwoSlopeNorm


ANALYSIS_ROOT = Path("20260515_4X4_song_4x4_analysis")
OUTPUT_NAME = "Mueller_4x4_average_theory_error.png"
ERROR_COLOR_LIMIT = 0.2


def rotation_matrix(angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(angle_deg)
    cos2 = np.cos(2.0 * theta)
    sin2 = np.sin(2.0 * theta)
    return np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, cos2, sin2, 0.0],
            [0.0, -sin2, cos2, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def ideal_linear_polarizer(angle_deg: float) -> np.ndarray:
    theta = np.deg2rad(angle_deg)
    stokes = np.array([1.0, np.cos(2.0 * theta), np.sin(2.0 * theta), 0.0])
    return np.outer(stokes, stokes)


def ideal_retarder(angle_deg: float, retardance_deg: float) -> np.ndarray:
    delta = np.deg2rad(retardance_deg)
    horizontal = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, np.cos(delta), np.sin(delta)],
            [0.0, 0.0, -np.sin(delta), np.cos(delta)],
        ],
        dtype=np.float64,
    )
    return rotation_matrix(-angle_deg) @ horizontal @ rotation_matrix(angle_deg)


def theory_for_sample(sample_name: str) -> tuple[np.ndarray, str]:
    if sample_name == "air":
        return np.eye(4, dtype=np.float64), "Identity"

    if sample_name.startswith("p"):
        angle = float(sample_name[1:])
        return ideal_linear_polarizer(angle), f"Ideal LP {angle:g} deg"

    if sample_name.startswith("qwp"):
        angle = float(sample_name[3:])
        theory = ideal_retarder(angle, retardance_deg=-90.0)
        return theory, f"Ideal QWP {angle:g} deg, retardance -90 deg"

    raise ValueError(f"Unknown sample naming convention: {sample_name}")


def annotate_matrix(ax: plt.Axes, matrix: np.ndarray) -> None:
    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            value = matrix[row, col]
            ax.text(
                col,
                row,
                f"{value:+.3f}",
                ha="center",
                va="center",
                color="black",
                fontsize=9,
            )


def plot_matrix_comparison(
    sample_name: str,
    measured: np.ndarray,
    theory: np.ndarray,
    theory_label: str,
    output_path: Path,
) -> None:
    diff = measured - theory
    diff_limit = ERROR_COLOR_LIMIT

    fig, axes = plt.subplots(1, 3, figsize=(15.5, 5.0), constrained_layout=True)
    fig.suptitle(f"4x4 Matrix comparison - {sample_name}", fontsize=16)

    panels = [
        ("Measured average", measured, -1.0, 1.0, None),
        (f"Theory\n{theory_label}", theory, -1.0, 1.0, None),
        ("Measured - theory", diff, -diff_limit, diff_limit, TwoSlopeNorm(0.0, -diff_limit, diff_limit)),
    ]

    for ax, (title, matrix, vmin, vmax, norm) in zip(axes, panels):
        if norm is None:
            image = ax.imshow(matrix, cmap="jet", vmin=vmin, vmax=vmax)
        else:
            image = ax.imshow(matrix, cmap="jet", norm=norm)

        ax.set_title(title, fontsize=12)
        ax.set_xticks(range(4))
        ax.set_yticks(range(4))
        ax.set_xlabel("column")
        ax.set_ylabel("row")
        annotate_matrix(ax, matrix)
        fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220)
    plt.close(fig)


def load_measured_average(summary_path: Path) -> np.ndarray:
    with summary_path.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    return np.asarray(summary["average_normalized_mueller"], dtype=np.float64)


def main() -> None:
    if not ANALYSIS_ROOT.exists():
        raise FileNotFoundError(f"Analysis folder does not exist: {ANALYSIS_ROOT}")

    generated = []
    for sample_dir in sorted(path for path in ANALYSIS_ROOT.iterdir() if path.is_dir()):
        summary_path = sample_dir / "analysis_summary.json"
        if not summary_path.exists():
            continue

        measured = load_measured_average(summary_path)
        theory, theory_label = theory_for_sample(sample_dir.name)
        output_path = sample_dir / OUTPUT_NAME

        plot_matrix_comparison(
            sample_name=sample_dir.name,
            measured=measured,
            theory=theory,
            theory_label=theory_label,
            output_path=output_path,
        )
        generated.append(output_path)

    print("Generated comparison figures:")
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
