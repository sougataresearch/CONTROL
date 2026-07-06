"""Standalone calculator: prompts you for every physical parameter needed
to compute a theoretical reflection Mueller matrix (bare substrate, or
substrate + one thin film -- see reflection_theory.py), then saves the
result and logs every parameter set you've ever entered to an editable CSV.

To run:

    python theoretical_mueller.py

You'll be asked for a sample label (used to look up/save this sample's
parameters), then wavelength, angle of incidence, substrate n/k, whether
there's a film on top, and if so film n/k/thickness. Press Enter on any
prompt to accept the suggested default -- whatever you last entered for
THIS sample label, read back from .theory_log.csv (edit that file by hand
at any time; the last row for a given sample label is what's suggested
next time).

Saves the computed 4x4 Mueller matrix to
Results/theoretical_matrices/<sample_label>.npy, and appends every
parameter set (whether you changed anything or not) as a new row to
.theory_log.csv -- a full history, not just the latest value, so you can
always see what was used for a specific past calculation.

validate_against_theory.py in this same folder reuses this same lookup: if
you've already computed a sample's theory here, it won't ask again unless
you tell it to.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

_REQUIRED_PACKAGES = {
    "numpy": "numpy",
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

import csv
from datetime import datetime
from pathlib import Path

import numpy as np

from reflection_theory import bare_substrate_mueller, film_on_substrate_mueller

RESULT_ROOT = Path(r"C:\COMPARE_CASES\RESULT")

_THEORY_LOG_PATH = Path(__file__).resolve().parent / ".theory_log.csv"
_LOG_FIELDS = [
    "timestamp", "sample_label", "wavelength_nm", "angle_of_incidence_deg",
    "substrate_n", "substrate_k", "has_film", "film_n", "film_k", "film_thickness_nm",
]


def _read_log_rows() -> list:
    if not _THEORY_LOG_PATH.exists():
        return []
    with open(_THEORY_LOG_PATH, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _last_row_for(sample_label: str) -> dict:
    """Last logged parameter row for this sample_label -- the log is
    append-only, so the last matching row is the most recent, and thus the
    best default to suggest. Returns {} if this sample_label has never been
    logged before (falls back to ideal/typical defaults)."""

    rows = [r for r in _read_log_rows() if r.get("sample_label") == sample_label]
    return rows[-1] if rows else {}


def _append_log_row(row: dict) -> None:
    is_new = not _THEORY_LOG_PATH.exists()
    with open(_THEORY_LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def ask_float(prompt: str, default: float) -> float:
    while True:
        text = input(f"{prompt} [{default:g}]: ").strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


def ask_yes_no(prompt: str, default: bool) -> bool:
    suggested = "Y/n" if default else "y/N"
    while True:
        text = input(f"{prompt} [{suggested}]: ").strip().lower()
        if not text:
            return default
        if text in ("y", "yes"):
            return True
        if text in ("n", "no"):
            return False
        print("Enter y or n.")


def prompt_for_parameters(sample_label: str) -> dict:
    """Ask for every physical parameter, showing the last-logged value for
    this sample_label (if any) as the suggested default -- editable every
    time, never silently reused without you seeing it."""

    last = _last_row_for(sample_label)

    wavelength_nm = ask_float("Wavelength (nm)", float(last.get("wavelength_nm", 632.8)))
    angle_of_incidence_deg = ask_float(
        "Angle of incidence (deg, from surface normal)",
        float(last.get("angle_of_incidence_deg", 65.0)),
    )
    substrate_n = ask_float("Substrate refractive index n", float(last.get("substrate_n", 3.88)))
    substrate_k = ask_float("Substrate extinction coefficient k", float(last.get("substrate_k", 0.02)))

    last_has_film = str(last.get("has_film", "")).strip().lower() in ("true", "1", "yes")
    has_film = ask_yes_no("Is there a thin film on top of the substrate?", last_has_film)

    film_n = film_k = film_thickness_nm = 0.0
    if has_film:
        film_n = ask_float("Film refractive index n", float(last.get("film_n", 1.46)))
        film_k = ask_float("Film extinction coefficient k", float(last.get("film_k", 0.0)))
        film_thickness_nm = ask_float(
            "Film thickness (nm)", float(last.get("film_thickness_nm", 100.0))
        )

    return {
        "wavelength_nm": wavelength_nm,
        "angle_of_incidence_deg": angle_of_incidence_deg,
        "substrate_n": substrate_n,
        "substrate_k": substrate_k,
        "has_film": has_film,
        "film_n": film_n,
        "film_k": film_k,
        "film_thickness_nm": film_thickness_nm,
    }


def compute_matrix(params: dict) -> np.ndarray:
    if params["has_film"]:
        return film_on_substrate_mueller(
            substrate_n=params["substrate_n"], substrate_k=params["substrate_k"],
            film_n=params["film_n"], film_k=params["film_k"],
            film_thickness_nm=params["film_thickness_nm"],
            angle_of_incidence_deg=params["angle_of_incidence_deg"],
            wavelength_nm=params["wavelength_nm"],
        )
    return bare_substrate_mueller(
        substrate_n=params["substrate_n"], substrate_k=params["substrate_k"],
        angle_of_incidence_deg=params["angle_of_incidence_deg"],
    )


def get_or_prompt_matrix(sample_label: str, interactive: bool = True) -> np.ndarray:
    """Used by validate_against_theory.py: compute this sample's
    theoretical matrix, prompting interactively for parameters (with
    last-logged values as defaults) and logging the result, exactly as
    running this script directly would."""

    params = prompt_for_parameters(sample_label) if interactive else _last_row_for(sample_label)
    matrix = compute_matrix(params)
    _append_log_row({"timestamp": datetime.now().isoformat(timespec="seconds"),
                      "sample_label": sample_label, **params})
    return matrix


def main() -> None:
    sample_label = input("Sample label (used to look up/save this sample's parameters): ").strip()
    if not sample_label:
        print("A sample label is required.")
        raise SystemExit(1)

    params = prompt_for_parameters(sample_label)
    matrix = compute_matrix(params)

    _append_log_row({"timestamp": datetime.now().isoformat(timespec="seconds"),
                      "sample_label": sample_label, **params})

    out_dir = RESULT_ROOT / "reflection" / "continuous_4x4" / "theoretical_matrices"
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / f"{sample_label}.npy", matrix)

    np.set_printoptions(precision=4, suppress=True)
    print(f"\nTheoretical 4x4 Mueller matrix for {sample_label!r}:")
    print(matrix)
    print(f"\nSaved to {out_dir / f'{sample_label}.npy'}")
    print(f"Logged parameters to {_THEORY_LOG_PATH}")


if __name__ == "__main__":
    main()
