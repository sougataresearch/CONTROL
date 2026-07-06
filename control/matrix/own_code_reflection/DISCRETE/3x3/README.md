# 3x3 Mueller matrix reconstruction — reflection mode

Reconstructs a **reflective** sample's 3x3 Mueller matrix (the S0,S1,S2
linear-polarization sub-block only) from N intensity images captured by the
same rig as `../../../own_code/DISCRETE/3x3/` (2 rotating polarizers, no
QWP), but folded into a reflection geometry — see "Physical setup" below.

This folder is fully self-contained: it does not import or depend on
anything in `../4x4/`, `../../CONTINOUS/`, or `../../../own_code/`. It only
reads the images and JSON config the acquisition scripts already produce.

## Physics background, from zero

If you haven't already, read
`../../../own_code/DISCRETE/3x3/README.md`'s "Physics background, from
zero" section first — it explains what a Stokes vector and a Mueller
matrix are, why 3x3 mode can only ever see the `S0,S1,S2` sub-block
(no QWP means no way to generate/detect circular polarization), and why a
camera pixel can't directly see polarization at all. Everything below
assumes you already know that.

### Why reflection needs different physics than transmission

In transmission mode, the sample sits between the generator and analyzer
optics and its Mueller matrix is a known formula (an ideal polarizer,
parameterized by angle) — you already know what kind of element the sample
is. A reflective sample generally isn't like that: its reflectance depends
on its refractive index, the angle of incidence, and the wavelength, none
of which the reconstruction code can guess. So reflection mode needs a way
to *compute* a theoretical matrix from real physical parameters
(`reflection_theory.py`) that you supply yourself per sample
(`theoretical_mueller.py`), rather than inferring it from a sample-type
label the way transmission mode's `lp<angle>`/`qwp<angle>` naming does.

The empirical reconstruction itself (turning captured images into a
measured matrix) is **completely unchanged** — `image_loader.py`,
`mueller_forward_model.py`, `solve_mueller.py`, `main.py`,
`average_rounds.py`, and `fit_calibration.py` are byte-for-byte copies of
`../../../own_code/DISCRETE/3x3/`'s versions, since that least-squares fit
never looks at what the sample physically is, only at known PSG/PSA angles
and measured intensities. See that folder's README for the full math
derivation; it's not repeated here.

### Fresnel reflection and the 3x3 sub-block

`reflection_theory.py` in this folder computes the exact same physics as
`../4x4/reflection_theory.py` — Fresnel reflection coefficients at a
single interface (`fresnel_coefficients()`), or the Airy thin-film formula
for a substrate with one film on top (`airy_reflection()`), converted to a
Mueller matrix via the standard Jones-to-Mueller formula
(`jones_diagonal_to_mueller()`). See that folder's README for the full
physics explanation (Fresnel equations, Brewster's angle, the Airy
formula, basis alignment) — it's identical here.

The **only** difference: `bare_substrate_mueller()`/
`film_on_substrate_mueller()` in this folder return just the top-left 3x3
**sub-block** of that same 4x4 matrix (`M[:3, :3]`), because that's all a
3x3 (no-QWP) measurement can ever see or verify — any `S3`-coupled terms
(the matrix's last row/column) are invisible to this method regardless of
how good the reconstruction is, exactly as in transmission mode's 3x3
tool.

### Basis alignment — the one thing you must get right physically

The Mueller matrix `reflection_theory.py` computes is expressed in the
(p, s) basis defined by the plane of incidence (p = in-plane, s =
out-of-plane). Your PSG/PSA "0 degrees" must be physically aligned to that
same p-axis for a comparison against the theoretical matrix to mean
anything — the reconstruction itself doesn't care (it recovers a correct
matrix in whatever basis your PSG/PSA zero actually points to), but if that
basis isn't p/s-aligned, you'd be comparing two matrices expressed in
different frames, and the "error" you'd see would be a frame mismatch, not
a real discrepancy. There's no automatic detection/correction for this —
verify the alignment physically on your bench.

## Physical setup

The acquisition scripts (`Measuremt_ script/`) assume a straight-through
optical path by default. Reflection requires physically folding the camera
arm to sit at the reflected beam's angle (specular reflection: reflection
angle = angle of incidence) rather than in a straight line with the
source. This is a hardware change on your bench, not a code change.

## Run this — full workflow, in order

| Order | Script | What it does |
|---|---|---|
| 1 | `main.py` | Reconstructs one capture's measured 3x3 Mueller matrix. Required for every sample. |
| 2 | `average_rounds.py` | Optional. You captured >= 3 repeat rounds of the *same* sample — reports mean + standard deviation across rounds. |
| 3 | `theoretical_mueller.py` | Optional standalone step. Computes and saves a sample's theoretical 3x3 reflection matrix from physical parameters you type in. |
| 4 | `validate_against_theory.py` | Compares a sample's measured (step 1) and theoretical (step 3, or computed inline) matrices, reporting Frobenius error and MSE. |
| 5 (conditional) | `fit_calibration.py` | Only if a trusted reference sample in step 4 shows more deviation than expected. Fits `extinction_ratio` from that reference capture. |

### Step 1 — `main.py`

Identical usage to `../../../own_code/DISCRETE/3x3/README.md` — open
`main.py`, edit `RUN_DIRECTORY` to point at your reflection capture (any
folder with `Images/` and `Config/experiment_config.json`, `"mode":
"3x3"`), then `python main.py`. You'll be prompted for `extinction_ratio`
exactly as in transmission mode — it describes your PSG/PSA polarizer
optics, not the sample.

Results are saved to
`C:\COMPARE_CASES\RESULT\reflection\3x3\reconstructions\<date>\.../<run
folder name>\` — same date/sample-path-mirroring as transmission mode,
under the `reflection` branch of the shared `RESULT/` root (see the root
README). Writes the same files as transmission's 3x3 tool:
`mueller_matrix_normalized.npy`, `mueller_matrix_raw.npy`,
`residual_rms.npy`, `calibration_used.json`, `summary.txt`,
`mueller_matrix_overview.png`, `residual_rms.png` — see
`../../../own_code/DISCRETE/3x3/README.md`'s table for what each contains.

### Step 3 — `theoretical_mueller.py`

Run `python theoretical_mueller.py`. You'll be prompted for a **sample
label** (any name, e.g. `si_bare` — used to look up/save this sample's
parameters later), then:

```text
Wavelength (nm) [632.8]:
Angle of incidence (deg, from surface normal) [65]:
Substrate refractive index n [3.88]:
Substrate extinction coefficient k [0.02]:
Is there a thin film on top of the substrate? [y/N]:
```

(and, if yes: film n, film k, film thickness in nm). Press Enter on any
prompt to accept the bracketed default — a generic value the first time,
or whatever you entered last time for that same sample label after that.
**Every value is manual input** — nothing is looked up automatically; you
supply the real numbers for your actual sample.

Every parameter set is appended to `.theory_log.csv` next to this script
(plain CSV, not committed to git — edit it by hand any time; the *last*
row for a sample label is the default suggested next time). The computed
3x3 matrix is saved to
`RESULT/reflection/3x3/theoretical_matrices/<sample_label>.npy`.

### Step 4 — `validate_against_theory.py`

Run `python validate_against_theory.py`. Edit `SAMPLE_DIRECTORIES` first
(folder name becomes the sample label in `.theory_log.csv`, shared with
step 3). It confirms your folder list, prompts once for `extinction_ratio`
(applied to every sample), then per sample prompts for theory parameters
(same log/defaults as step 3), reconstructs (reusing `main.py`'s cached
output if calibration matches), and reports the error.

**How the error is calculated** — identical method to the 4x4 reflection
tool and the transmission-mode tools, just with `N = 9` (a 3x3 matrix has
9 elements) instead of 16:

```
diff = measured - theory                              (element-wise, 9 numbers)

Frobenius error = ||diff||_F = sqrt( sum of every diff_ij squared )
MSE             = mean of every diff_ij squared        =  (Frobenius error)^2 / 9
```

Square each of the 9 element-wise differences (so a `+0.02` and a `-0.02`
error both count as equally wrong instead of canceling out), sum them, and
either square-root the sum (Frobenius error) or divide by 9 first (MSE).
These always rank identically — see the root README's full worked example
of this relationship (there shown for a 3x3 case using the exact same
`N = 9`).

Saved per sample to
`RESULT/reflection/3x3/validation_against_theory/<date-relative-path>/`:
`theory.npy`, `experimental_mean.npy`, `per_pixel_frobenius_error.npy`,
`per_pixel_mse.npy`, `comparison.png`, `error_map.png`. A top-level
`summary.txt` tabulates every sample's numbers plus provenance.

### Step 5 — `fit_calibration.py` (conditional)

Same method as `../../../own_code/DISCRETE/3x3/README.md`'s "Calibrating
from an air capture" section (a coarse-to-fine grid search over
`extinction_ratio`), but point `AIR_DIRECTORY` at a reflective sample you
trust independently (e.g. a good mirror), since there's no universal
"should be identity" reference the way air is in transmission mode. Skip
this entirely unless `validate_against_theory.py` showed more deviation
than your noise floor would explain.

## What you'd need to change to run this on new data

Nothing in the code — only:

- `RUN_DIRECTORY` in `main.py`, `SAMPLE_DIRECTORIES` in
  `validate_against_theory.py`, `AIR_DIRECTORY` in `fit_calibration.py`, to
  point at your actual captures.
- The physical parameters you type in at the theory prompts, for your
  actual sample.
- `--extinction` (or the prompt), once you have a real calibration number
  for your polarizer optics.

## Requirements

`numpy`, `matplotlib`, `Pillow` (PIL) — every script here checks for these
on startup and `pip install`s whichever are missing into the same Python
interpreter that's running it. See
`../../../own_code/DISCRETE/3x3/README.md`'s "Getting the right Python
interpreter" note if you hit a `ModuleNotFoundError` despite that.
