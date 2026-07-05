"""Single entry point for the 4x4 Mueller matrix pipeline: load a run's
images, reconstruct its 4x4 Mueller matrix, and save every output
(per-element maps, an overview plot, the mean matrix, and fit diagnostics)
to disk.

This is the only file you need to run. It imports image_loader.py and
solve_mueller.py from this same folder -- see README.md (in this folder)
for what each one does and the physics behind them.

To run: edit RUN_DIRECTORY below to point at whatever 4x4 run you want to
process (it does not need to live under this project at all), then just run
this file -- no arguments required.

    python main.py

CLI arguments still work too, and override RUN_DIRECTORY/OUTPUT_DIRECTORY
for a one-off run without editing the file:

    python main.py <run_directory> [--out OUTPUT_DIR] [--extinction E] [--retardance R]
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

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from image_loader import load_run
from solve_mueller import MuellerResult4x4, reconstruct

# ---------------------------------------------------------------------------
# EDIT THIS to point at the 4x4 run you want to process. Any folder that
# contains Images/ and Config/experiment_config.json (mode "4x4", with
# fixed_angles for PSG_Polarizer/PSA_Analyzer) works -- it does not need to
# be inside this project or on the same drive.
# ---------------------------------------------------------------------------
RUN_DIRECTORY = r"G:\control\Data\03072026\qwp\qwp90"

# Where results are saved. None = a Results/<run folder name> subfolder next
# to this script, independent of wherever RUN_DIRECTORY actually is.
OUTPUT_DIRECTORY = None
# ---------------------------------------------------------------------------


def default_output_directory(run_dir: Path) -> Path:
    return Path(__file__).resolve().parent / "Results" / run_dir.name


def save_outputs(result: MuellerResult4x4, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    np.save(out_dir / "mueller_matrix_normalized.npy", result.matrix)
    np.save(out_dir / "mueller_matrix_raw.npy", result.matrix_raw)
    np.save(out_dir / "residual_rms.npy", result.residual_rms)

    np.set_printoptions(precision=4, suppress=True)
    with open(out_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write("Mode: 4x4\n")
        fh.write(f"System matrix condition number: {result.condition_number:.3f}\n")
        fh.write(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}\n")
        fh.write("Mean Mueller matrix (spatial average, normalized by m00):\n")
        fh.write(np.array2string(result.matrix_mean))
        fh.write("\n")

    fig, axes = plt.subplots(4, 4, figsize=(12, 12))
    im = None
    for i in range(4):
        for j in range(4):
            ax = axes[i, j]
            im = ax.imshow(result.matrix[:, :, i, j], cmap="RdBu_r", vmin=-1, vmax=1)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(f"m{i}{j}", fontsize=9)
    fig.subplots_adjust(right=0.88)
    cbar_ax = fig.add_axes([0.90, 0.15, 0.02, 0.7])
    fig.colorbar(im, cax=cbar_ax)
    fig.suptitle("Recovered 4x4 Mueller matrix (per pixel, normalized)")
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
    parser.add_argument("--retardance", type=float, default=90.0,
                         help="QWP retardance in degrees (default: ideal, 90)")
    args = parser.parse_args()

    run_dir = Path(args.run_directory or RUN_DIRECTORY)
    out_dir = Path(args.out or OUTPUT_DIRECTORY) if (args.out or OUTPUT_DIRECTORY) else default_output_directory(run_dir)

    run = load_run(run_dir)
    result = reconstruct(run, extinction_ratio=args.extinction, retardance_deg=args.retardance)
    save_outputs(result, out_dir)

    np.set_printoptions(precision=4, suppress=True)
    print(f"Mode: 4x4, images used: {len(run.files)}")
    print(f"Fixed angles: {run.fixed_angles}")
    print(f"System matrix condition number: {result.condition_number:.3f}")
    print(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}")
    print("Mean Mueller matrix (normalized by m00):")
    print(result.matrix_mean)
    print(f"Saved outputs to {out_dir}")


if __name__ == "__main__":
    main()
