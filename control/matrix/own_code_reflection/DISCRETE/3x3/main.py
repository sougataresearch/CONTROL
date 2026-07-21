"""Single entry point for the 3x3 Mueller matrix pipeline: load a run's
images, reconstruct its 3x3 Mueller matrix, and save every output
(per-element maps, an overview plot, the mean matrix, and fit diagnostics)
to disk.

This is the only file you need to run. It imports image_loader.py and
solve_mueller.py from this same folder -- see README.md (in this folder)
for what each one does and the physics behind them.

To run: edit RUN_DIRECTORY below to point at whatever 3x3 run you want to
process (it does not need to live under this project at all), then just run
this file:

    python main.py

You will be prompted in the terminal for the polarizer extinction ratio
(press Enter to accept the suggested default -- the ideal value, 0, the
first time; whatever you last used after that, remembered in
.last_calibration.json next to this file). Pass --extinction on the
command line instead to skip the prompt for a one-off/scripted run:

    python main.py <run_directory> [--out OUTPUT_DIR] [--extinction E]

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
import csv
import json
import re
from datetime import datetime
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
RUN_DIRECTORY = r"C:\COMPARE_CASES\Data\02072026\air"

# Where results are saved. None = a Results/<run folder name> subfolder next
# to this script, independent of wherever RUN_DIRECTORY actually is.
OUTPUT_DIRECTORY = None
# ---------------------------------------------------------------------------

# Remembers the last extinction ratio you actually used (typed at the
# prompt, or passed via --extinction), so the next run's prompt suggests
# that instead of resetting to the ideal default every time. Local to this
# machine -- not committed to git (see ../../../.gitignore).
_CALIBRATION_STATE_PATH = Path(__file__).resolve().parent / ".last_calibration.json"


def _load_last_calibration() -> dict:
    try:
        return json.loads(_CALIBRATION_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_last_calibration(values: dict) -> None:
    _CALIBRATION_STATE_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")


# Full history of every calibration value ever used, one row per run -- unlike
# .last_calibration.json above (which only remembers the single most recent
# value, for the next prompt's suggested default), this lets you look back and
# answer "what extinction ratio was in effect for a specific past capture?"
# Local to this machine -- not committed to git (see ../../../.gitignore).
_CALIBRATION_LOG_PATH = Path(__file__).resolve().parent / ".calibration_log.csv"


def _append_calibration_log(run_dir: Path, extinction_ratio: float) -> None:
    is_new = not _CALIBRATION_LOG_PATH.exists()
    with open(_CALIBRATION_LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(["timestamp", "run_directory", "extinction_ratio"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"), run_dir, extinction_ratio])


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


RESULT_ROOT = Path(r"C:\COMPARE_CASES\RESULT")


def default_output_directory(run_dir: Path) -> Path:
    return RESULT_ROOT / "reflection" / "3x3" / "reconstructions" / _date_relative_path(run_dir)


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


def ask_float(prompt: str, default: float) -> float:
    """Ask for a numeric value, showing ``default`` in brackets; press Enter
    (blank input) to accept it as-is. Loops until a parseable number is
    entered. Used for extinction_ratio, since that's a real, per-optic
    calibration number the operator should confirm every run rather than
    silently inheriting whatever default happens to be in this file."""

    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


def save_outputs(result: MuellerResult3x3, out_dir: Path, run_dir: Path,
                  extinction_ratio: float, run) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    np.save(out_dir / "mueller_matrix_normalized.npy", result.matrix)
    np.save(out_dir / "mueller_matrix_raw.npy", result.matrix_raw)
    np.save(out_dir / "residual_rms.npy", result.residual_rms)

    # Lets validate_against_theory.py verify a cached reconstruction here was
    # made with the calibration it's about to use, before reusing it instead
    # of redoing the reconstruction from scratch.
    (out_dir / "calibration_used.json").write_text(
        json.dumps(
            {"extinction_ratio": extinction_ratio, "dark_subtracted": run.dark_subtracted},
            indent=2,
        ),
        encoding="utf-8",
    )

    np.set_printoptions(precision=4, suppress=True)
    with open(out_dir / "summary.txt", "w", encoding="utf-8") as fh:
        fh.write("Mode: 3x3\n")
        fh.write(f"System matrix condition number: {result.condition_number:.3f}\n")
        fh.write(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}\n")
        fh.write("Mean Mueller matrix (spatial average, normalized by m00):\n")
        fh.write(np.array2string(result.matrix_mean))
        fh.write("\n\n")
        fh.write("--- Provenance ---\n")
        fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fh.write(f"Git commit: {_git_commit_hash()}\n")
        fh.write(f"Source run: {run_dir}\n")
        fh.write(f"Extinction ratio: {extinction_ratio}\n")
        if run.dark_subtracted:
            fh.write(
                f"Dark-current subtraction: applied ({run.dark_frame_count} frame(s) "
                f"averaged from {run_dir / 'Dark'}, mean dark level "
                f"{run.dark_level_mean:.4f})\n"
            )
        else:
            fh.write("Dark-current subtraction: NOT applied (no Dark/ folder found)\n")

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
    parser.add_argument("--extinction", type=float, default=None,
                         help="Polarizer extinction ratio Imin/Imax; omit to be "
                              "prompted for it interactively (suggested default: ideal, 0)")
    args = parser.parse_args()

    run_dir = Path(args.run_directory or RUN_DIRECTORY)
    out_dir = Path(args.out or OUTPUT_DIRECTORY) if (args.out or OUTPUT_DIRECTORY) else default_output_directory(run_dir)
    last_calibration = _load_last_calibration()
    extinction_ratio = args.extinction if args.extinction is not None else ask_float(
        "Polarizer extinction ratio Imin/Imax", last_calibration.get("extinction_ratio", 0.0)
    )
    _save_last_calibration({"extinction_ratio": extinction_ratio})
    _append_calibration_log(run_dir, extinction_ratio)

    run = load_run(run_dir)
    result = reconstruct(run, extinction_ratio=extinction_ratio)
    save_outputs(result, out_dir, run_dir, extinction_ratio, run)

    np.set_printoptions(precision=4, suppress=True)
    print(f"Mode: 3x3, images used: {len(run.files)}")
    if run.dark_subtracted:
        print(f"Dark-current subtraction: applied ({run.dark_frame_count} frame(s), "
              f"mean dark level {run.dark_level_mean:.4f})")
    else:
        print("Dark-current subtraction: NOT applied (no Dark/ folder found)")
    print(f"System matrix condition number: {result.condition_number:.3f}")
    print(f"Mean fit residual (RMS): {result.residual_rms.mean():.6f}")
    print("Mean Mueller matrix (normalized by m00):")
    print(result.matrix_mean)
    print(f"Saved outputs to {out_dir}")

    if not re.search(r"_round\d+$", run_dir.name, re.IGNORECASE):
        print(
            "\nNote: this looks like a single-round capture, so you only have a "
            "point estimate with no idea how much it would vary on a recapture. "
            "Recommended default (see NAMING.md): capture >= 3 rounds "
            "(<sample>_round01, _round02, ...) and run average_rounds.py for a "
            "mean + standard deviation across rounds."
        )


if __name__ == "__main__":
    main()
