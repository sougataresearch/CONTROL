"""Single entry point for the 4x4 Mueller matrix pipeline: load a run's
images, reconstruct its 4x4 Mueller matrix, and save every output
(per-element maps, an overview plot, the mean matrix, and fit diagnostics)
to disk.

This is the only file you need to run. It imports image_loader.py and
solve_mueller.py from this same folder -- see README.md (in this folder)
for what each one does and the physics behind them.

To run: edit RUN_DIRECTORY below to point at whatever 4x4 run you want to
process (it does not need to live under this project at all), then just run
this file:

    python main.py

You will be prompted in the terminal for the polarizer extinction ratio and
the QWP retardance (press Enter on either to accept the suggested default
-- the ideal values, 0 and 90, the first time; whatever you last used
after that, remembered in .last_calibration.json next to this file). Pass
--extinction/--retardance on the command line instead to skip the prompts
for a one-off/scripted run:

    python main.py <run_directory> [--out OUTPUT_DIR] [--extinction E] [--retardance R]

CLI arguments also override RUN_DIRECTORY/OUTPUT_DIRECTORY without editing
the file.
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
import json
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

# Remembers the last extinction ratio/retardance you actually used (typed
# at the prompt, or passed via --extinction/--retardance), so the next
# run's prompt suggests that instead of resetting to the ideal default
# every time. Local to this machine -- not committed to git.
_CALIBRATION_STATE_PATH = Path(__file__).resolve().parent / ".last_calibration.json"


def _load_last_calibration() -> dict:
    try:
        return json.loads(_CALIBRATION_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_last_calibration(values: dict) -> None:
    _CALIBRATION_STATE_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")


def default_output_directory(run_dir: Path) -> Path:
    return Path(__file__).resolve().parent / "Results" / run_dir.name


def ask_float(prompt: str, default: float) -> float:
    """Ask for a numeric value, showing ``default`` in brackets; press Enter
    (blank input) to accept it as-is. Loops until a parseable number is
    entered. Used for extinction_ratio/retardance_deg, since these are real,
    per-optic calibration numbers the operator should confirm every run
    rather than silently inheriting whatever default happens to be in this
    file."""

    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


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
    parser.add_argument("--extinction", type=float, default=None,
                         help="Polarizer extinction ratio Imin/Imax; omit to be "
                              "prompted for it interactively (suggested default: ideal, 0)")
    parser.add_argument("--retardance", type=float, default=None,
                         help="QWP retardance in degrees; omit to be prompted for it "
                              "interactively (suggested default: ideal, 90)")
    args = parser.parse_args()

    run_dir = Path(args.run_directory or RUN_DIRECTORY)
    out_dir = Path(args.out or OUTPUT_DIRECTORY) if (args.out or OUTPUT_DIRECTORY) else default_output_directory(run_dir)
    last_calibration = _load_last_calibration()
    extinction_ratio = args.extinction if args.extinction is not None else ask_float(
        "Polarizer extinction ratio Imin/Imax", last_calibration.get("extinction_ratio", 0.0)
    )
    retardance_deg = args.retardance if args.retardance is not None else ask_float(
        "QWP retardance in degrees", last_calibration.get("retardance_deg", 90.0)
    )
    _save_last_calibration({"extinction_ratio": extinction_ratio, "retardance_deg": retardance_deg})

    run = load_run(run_dir)
    result = reconstruct(run, extinction_ratio=extinction_ratio, retardance_deg=retardance_deg)
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
