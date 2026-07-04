"""Single entry point for the 3x3 Mueller matrix pipeline: load a run's
images, reconstruct its 3x3 Mueller matrix, and save every output
(per-element maps, an overview plot, the mean matrix, and fit diagnostics)
to disk.

This is the only file you need to run. It imports image_loader.py and
solve_mueller.py from this same folder -- see README.md (in this folder)
for what each one does and the physics behind them.

To run: edit RUN_DIRECTORY below to point at whatever 3x3 run you want to
process (it does not need to live under this project at all), then just run
this file -- no arguments required.

    python main.py

CLI arguments still work too, and override RUN_DIRECTORY/OUTPUT_DIRECTORY
for a one-off run without editing the file:

    python main.py <run_directory> [--out OUTPUT_DIR] [--extinction E]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from image_loader import load_run
from solve_mueller import MuellerResult3x3, reconstruct

# ---------------------------------------------------------------------------
# EDIT THIS to point at the 3x3 run you want to process. Any folder that
# contains Images/ and Config/experiment_config.json (mode "3x3") works --
# it does not need to be inside this project or on the same drive.
# ---------------------------------------------------------------------------
RUN_DIRECTORY = r"G:\control\Data\03072026\lp\lp30"

# Where results are saved. None = a Results/<run folder name> subfolder next
# to this script, independent of wherever RUN_DIRECTORY actually is.
OUTPUT_DIRECTORY = None
# ---------------------------------------------------------------------------


def default_output_directory(run_dir: Path) -> Path:
    return Path(__file__).resolve().parent / "Results" / run_dir.name


def save_outputs(result: MuellerResult3x3, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    np.save(out_dir / "mueller_matrix_normalized.npy", result.matrix)
    np.save(out_dir / "mueller_matrix_raw.npy", result.matrix_raw)
    np.save(out_dir / "residual_rms.npy", result.residual_rms)

    np.set_printoptions(precision=4, suppress=True)
    with open(out_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write("Mode: 3x3\n")
        fh.write(f"System matrix condition number: {result.condition_number:.3f}\n")
        fh.write(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}\n")
        fh.write("Mean Mueller matrix (spatial average, normalized by m00):\n")
        fh.write(np.array2string(result.matrix_mean))
        fh.write("\n")

    fig, axes = plt.subplots(3, 3, figsize=(9, 9))
    im = None
    for i in range(3):
        for j in range(3):
            ax = axes[i, j]
            im = ax.imshow(result.matrix[:, :, i, j], cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(f"m{i}{j}", fontsize=9)
    fig.subplots_adjust(right=0.88)
    cbar_ax = fig.add_axes([0.90, 0.15, 0.02, 0.7])
    fig.colorbar(im, cax=cbar_ax)
    fig.suptitle("Recovered 3x3 Mueller matrix (per pixel, normalized)")
    fig.savefig(out_dir / "mueller_matrix_overview.png", dpi=200)
    plt.close(fig)

    fig2, ax2 = plt.subplots(figsize=(5, 4))
    im2 = ax2.imshow(result.residual_rms, cmap="inferno")
    ax2.set_title("Per-pixel fit residual (RMS)")
    fig2.colorbar(im2, ax=ax2)
    fig2.savefig(out_dir / "residual_rms.png", dpi=200)
    plt.close(fig2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", nargs="?", default=None,
                         help="Folder with Images/ and Config/experiment_config.json "
                              "(default: RUN_DIRECTORY set at the top of this file)")
    parser.add_argument("--out", default=None,
                         help="Output folder (default: OUTPUT_DIRECTORY set at the top of this file)")
    parser.add_argument("--extinction", type=float, default=0.0,
                         help="Polarizer extinction ratio Imin/Imax (default: ideal, 0)")
    args = parser.parse_args()

    run_dir = Path(args.run_directory or RUN_DIRECTORY)
    out_dir = Path(args.out or OUTPUT_DIRECTORY) if (args.out or OUTPUT_DIRECTORY) else default_output_directory(run_dir)

    run = load_run(run_dir)
    result = reconstruct(run, extinction_ratio=args.extinction)
    save_outputs(result, out_dir)

    np.set_printoptions(precision=4, suppress=True)
    print(f"Mode: 3x3, images used: {len(run.files)}")
    print(f"System matrix condition number: {result.condition_number:.3f}")
    print(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}")
    print("Mean Mueller matrix (normalized by m00):")
    print(result.matrix_mean)
    print(f"Saved outputs to {out_dir}")


if __name__ == "__main__":
    main()
