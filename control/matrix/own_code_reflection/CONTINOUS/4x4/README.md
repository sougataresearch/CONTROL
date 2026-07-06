# 4x4 Mueller matrix reconstruction — reflection mode, continuous rotation

Reconstructs a **reflective** sample's full 4x4 Mueller matrix from N
intensity frames captured by the same rig as
`../../../own_code/CONTINOUS/4x4/` (fixed `PSG_Polarizer`/`PSA_Analyzer`,
continuously spinning `PSG_QWP`/`PSA_QWP`), but folded into a reflection
geometry — see "Physical setup" below.

This folder is fully self-contained: it does not import or depend on
anything in `../../DISCRETE/`, or `../../../own_code/`. It only reads the
images, the per-frame CSV log, and the JSON config the acquisition scripts
already produce.

## How this differs from `../../DISCRETE/4x4/` (reflection) and from `../../../own_code/CONTINOUS/4x4/` (transmission)

Exactly the same two-way relationship as transmission mode's continuous
folder (see `../../../own_code/CONTINOUS/4x4/README.md`'s fuller
explanation):

- vs. **discrete reflection** (`../../DISCRETE/4x4/`): identical physics
  (`mueller_forward_model.py` is a verbatim copy), differing only in how
  (angle, intensity) pairs are gathered — a handful of named discrete
  states vs. many frames at continuously-varying angles read from
  `Logs/experiment_log.csv`. `solve_mueller.py`'s generalized
  least-squares fit handles either with no code difference, since it never
  assumes a fixed grid.
- vs. **continuous transmission** (`../../../own_code/CONTINOUS/4x4/`):
  identical acquisition-side handling, differing only in what
  `reflection_theory.py`/`theoretical_mueller.py`/
  `validate_against_theory.py` add — see below and
  `../../DISCRETE/4x4/README.md`'s fuller physics writeup (not repeated
  here).

## Physics background, from zero

Read `../../../own_code/DISCRETE/3x3/README.md`'s "Physics background,
from zero" section for Stokes vectors/Mueller matrices, then
`../../DISCRETE/4x4/README.md`'s physics sections (Fresnel reflection,
Brewster's angle, the Airy thin-film formula, Jones-to-Mueller conversion,
and the p/s basis-alignment requirement) for everything reflection-specific
— all of it applies here unchanged; only the acquisition method (continuous
vs. discrete) differs.

### What's reused unchanged, what's new here

- **`image_loader.py`, `mueller_forward_model.py`, `solve_mueller.py`,
  `main.py`, `fit_calibration.py`, and `polar_decomposition.py`** are
  byte-for-byte copies of `../../../own_code/CONTINOUS/4x4/`'s versions
  (physics/reconstruction) and `../../DISCRETE/4x4/`'s version
  (`polar_decomposition.py`). No `average_rounds.py` here, matching
  transmission's continuous folder (continuous captures aren't organized
  into repeat rounds the same way discrete ones are).
- **`reflection_theory.py`, `theoretical_mueller.py`, and
  `validate_against_theory.py` are new** — same reflection physics as
  `../../DISCRETE/4x4/`'s versions.

## Physical setup

The acquisition scripts (`Measuremt_ script/continous_rotation/`) assume a
straight-through optical path by default. Reflection requires physically
folding the camera arm to sit at the reflected beam's angle (specular
reflection: reflection angle = angle of incidence) rather than in a
straight line with the source. This is a hardware change on your bench,
not a code change.

## Run this — full workflow, in order

| Order | Script | What it does |
|---|---|---|
| 1 | `main.py` | Reconstructs one capture's measured Mueller matrix (+ polar decomposition). Required for every sample. |
| 2 | `theoretical_mueller.py` | Optional standalone step. Computes and saves a sample's theoretical reflection Mueller matrix from physical parameters you type in. |
| 3 | `validate_against_theory.py` | Compares a sample's measured (step 1) and theoretical (step 2, or computed inline) matrices, reporting Frobenius error and MSE. |
| 4 (conditional) | `fit_calibration.py` | Only if a trusted reference sample in step 3 shows more deviation than expected. Fits `extinction_ratio`/`retardance_deg` from that reference capture. |

### Step 1 — `main.py`

Identical usage to `../../../own_code/CONTINOUS/4x4/README.md` — open
`main.py`, edit `RUN_DIRECTORY` to point at your reflection capture (any
folder with `Images/`, `Logs/experiment_log.csv`, and
`Config/experiment_config.json` with populated `"fixed_angles"`), then
`python main.py`. Prompted for `extinction_ratio`/`retardance_deg` exactly
as in transmission mode — these describe your PSG/PSA optics, not the
sample.

Results are saved to
`C:\COMPARE_CASES\RESULT\reflection\continuous_4x4\reconstructions\<date>\.../<run
folder name>\` (or just `.../<run folder name>/` if there's no dated
`Data/<8-digit-date>/...` layout — the common case for continuous
captures) — under the `reflection` branch of the shared `RESULT/` root (see
the root README). Writes the same files as
`../../DISCRETE/4x4/README.md` documents in full:
`mueller_matrix_normalized.npy`, `mueller_matrix_raw.npy`,
`residual_rms.npy`, `calibration_used.json`, the four polar-decomposition
maps, `summary.txt`, `mueller_matrix_overview.png`, `residual_rms.png`,
`polar_decomposition.png`.

### Step 2 — `theoretical_mueller.py`

Identical prompts/behavior to `../../DISCRETE/4x4/README.md`'s "Step 3"
section (wavelength, angle of incidence, substrate n/k, optional film
n/k/thickness, all manual input, logged to `.theory_log.csv` next to this
script, matrix saved to
`RESULT/reflection/continuous_4x4/theoretical_matrices/<sample_label>.npy`)
— not repeated here.

### Step 3 — `validate_against_theory.py`

Identical calculation to `../../DISCRETE/4x4/README.md`'s "Step 4" section:
Frobenius error and MSE between the measured and theoretical 4x4 matrices
(`N = 16`), reported alongside per-pixel error maps. Reuses `main.py`'s
cached reconstruction automatically when calibration matches. See that
section for the full formula and worked explanation of why both numbers
are reported (they always rank identically).

Saved per sample to
`RESULT/reflection/continuous_4x4/validation_against_theory/<date-relative-path>/`:
`theory.npy`, `experimental_mean.npy`, `per_pixel_frobenius_error.npy`,
`per_pixel_mse.npy`, `comparison.png`, `error_map.png`, plus a top-level
`summary.txt`.

### Step 4 — `fit_calibration.py` (conditional)

Same coordinate-descent method as
`../../DISCRETE/4x4/README.md`/`../../../own_code/CONTINOUS/4x4/README.md`
describe — point `AIR_DIRECTORY` at a continuous-rotation capture of a
reflective reference sample you trust independently (e.g. a good mirror).
Skip unless `validate_against_theory.py` showed more deviation than your
noise floor would explain.

## What you'd need to change to run this on new data

Nothing in the code — only:

- `RUN_DIRECTORY` in `main.py`, `SAMPLE_DIRECTORIES` in
  `validate_against_theory.py`, `AIR_DIRECTORY` in `fit_calibration.py`.
- The physical parameters you type in at the theory prompts.
- `--extinction`/`--retardance` (or the prompts), once you have real
  calibration numbers.

## Requirements

`numpy`, `matplotlib`, `Pillow` (PIL) — every script here checks for these
on startup and installs whichever are missing. See
`../../../own_code/DISCRETE/4x4/README.md`'s "Getting the right Python
interpreter" note if you hit a `ModuleNotFoundError` despite that.
