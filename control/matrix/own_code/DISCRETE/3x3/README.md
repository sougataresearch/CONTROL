# 3x3 Mueller matrix reconstruction

Reconstructs a sample's 3x3 Mueller matrix from N intensity images captured
by the rig in `Measuremt_ script/discreate_angle` in **3x3 mode**. Works for
any number of images and any sample -- the code never assumes a fixed image
count or hardcodes what the sample is.

Naming your run folders correctly matters more than anything in this
README -- see `../../NAMING.md` for the one rule, and for repeat rounds of
the same sample see `average_rounds.py` in this folder. **Recommended
default: capture >= 3 rounds and use `average_rounds.py`** rather than
`main.py` alone -- a single round only gives you a point estimate with no
idea how much it would vary on a recapture (`main.py` will print a reminder
of this if it doesn't see a `_round<NN>` suffix on the run folder).

This folder is fully self-contained: it does not import or depend on
anything in `own_code/4x4`, `matrix/tinghuye/`,
`Mueller_calculation_36_images_method.py`, or `Measuremt_ script/`. It only
reads the images and JSON config those scripts already produce.

## Physics background, from zero

Skip this section if you already know what a Stokes vector and a Mueller
matrix are. If you don't, read this before "The physics" further down --
everything else in this README assumes it.

### What "polarization" means for a beam of light

Light is an oscillating electric field. "Polarization" describes the shape
traced by that oscillation as the light travels -- a straight line
(**linear** polarization, at some angle), a circle (**circular**
polarization, spinning left or right), or something in between (elliptical).
Ordinary room light or sunlight is **unpolarized**: it's a rapid, random mix
of every angle and handedness, averaging out to no preferred direction at
all. A polarizer is a filter that only lets one specific linear direction
through; a quarter-wave plate (QWP) is a different kind of filter that
delays one direction of oscillation relative to the perpendicular one,
which is what turns linear polarization into circular (and vice versa).

### The Stokes vector: describing a polarization state with 4 numbers

Rather than describing "the shape of the oscillation" in words, it's
described with four numbers, `(S0, S1, S2, S3)`, called a **Stokes
vector**:

- `S0` -- total intensity (brightness), regardless of polarization.
- `S1` -- how much more horizontal-vs-vertical linear polarization there is
  than the reverse (positive = more horizontal, negative = more vertical).
- `S2` -- the same idea, but for the 45°/135° linear directions instead of
  0°/90°.
- `S3` -- how much more right-circular polarization there is than
  left-circular (or the reverse, if negative).

Unpolarized light is `[1, 0, 0, 0]` -- some brightness, no preference in any
of the three polarization directions. Perfect horizontal linear light is
`[1, 1, 0, 0]`. Perfect right-circular light is `[1, 0, 0, 1]`. A partially
polarized, partially elliptical beam is just some other combination in
between.

**3x3 mode only ever measures `(S0, S1, S2)`** -- it has no way to generate
or detect the `S3` (circular) component, because it has no QWP. That's the
"3x3" in the name: a 3-number Stokes vector, and a 3x3 matrix instead of the
full 4x4. See `own_code/4x4` for the version that includes `S3`.

### The Mueller matrix: what a sample does to a Stokes vector

Every optical element -- a polarizer, a wave plate, a piece of stressed
plastic, a biological tissue sample -- transforms an incoming Stokes vector
into an outgoing one, and (as long as the element behaves linearly, which
essentially all real samples do) that transformation is just a
matrix-vector multiplication:

```
Stokes_out = M @ Stokes_in
```

`M` (3x3 here, `S0,S1,S2` only) is the **Mueller matrix** -- it's the
complete description of how the sample affects polarization. Reconstructing
`M` is the entire goal of this code. A few landmark values: `M` = identity
means the sample does nothing to polarization (e.g. air); `M`'s top row
alone determines how much the sample's transmitted brightness depends on
input polarization angle (**diattenuation**); `M`'s first column alone
determines how strongly the sample imposes its own polarization on
originally-unpolarized light (**polarizance**).

### Why you can't just point a camera at the sample and read off `M`

A camera pixel only measures **intensity** (one number, `S0` of whatever
light reaches it) -- it cannot directly see `S1` or `S2`. So to figure out
all 9 unknown entries of `M`, the trick is:

1. **Generate** several different *known* input Stokes vectors, one at a
   time, by rotating a polarizer (the PSG, "polarization state generator")
   to different angles before the sample.
2. **Analyze** the light coming out by rotating a second polarizer (the
   PSA, "polarization state analyzer") to different angles after the
   sample, and record the camera's intensity reading each time.
3. Each single (PSG angle, PSA angle, measured intensity) combination gives
   you one linear equation relating the 9 unknown entries of `M` to that
   one measured number.
4. With enough different (PSG, PSA) combinations -- at least 9, and more is
   better (see "why more images improve precision" below) -- there are
   enough equations to solve for every entry of `M`.

That's the entire acquisition scheme in `Measuremt_ script/discreate_angle`
(3x3 mode): sweep the PSG and PSA polarizers through a grid of angles,
save one intensity image per combination, and hand the whole stack to this
code to solve for `M`. Everything below ("The physics") is the precise
mechanics of steps 1, 2, and 4.

## Run this

Open `main.py` and edit the one line near the top:

```python
RUN_DIRECTORY = r"G:\control\Data\03072026\lp\lp30"
```

to point at whatever 3x3 run you want to process -- any folder containing
an `Images/` subfolder and a `Config/experiment_config.json` with
`"mode": "3x3"`. It does not need to be inside this project, or on the same
drive as anything else. Then:

```
python main.py
```

You'll be prompted in the terminal for the polarizer extinction ratio:

```text
Polarizer extinction ratio Imin/Imax [0]: 0.02
```

Type your measured value and press Enter, or just press Enter (blank) to
accept the suggested default shown in brackets — the ideal value (`0`) the
first time you run this, then whatever you entered last time after that
(remembered in `.last_calibration.json` next to `main.py`, not committed to
git).

Results are saved to
`C:\COMPARE_CASES\RESULT\transmission\3x3\reconstructions\<date>\.../<run
folder name>\` by default -- the run directory's own date/sample-type path
is mirrored under that folder (e.g. `Data/03072026/lp/lp30` ->
`RESULT/transmission/3x3/reconstructions/03072026/lp/lp30/`), so the same
sample name captured on a different date doesn't collide with or overwrite
an earlier result. If `RUN_DIRECTORY` isn't under a dated
`Data/<8-digit-date>/...` layout, it falls back to just
`RESULT/transmission/3x3/reconstructions/<run folder name>/`. `RESULT/` is a
single shared output root for every tool in this whole project (see the root
README) -- it's created automatically the first time anything writes to it;
you never need to create it yourself. Set `OUTPUT_DIRECTORY` at the top of
`main.py` if you want a specific run's output somewhere else instead.

You can also pass everything as command-line arguments instead of editing
the file or answering the prompt, e.g. for scripting multiple runs without
touching `main.py`:

```
python main.py "G:\control\Data\03072026\lp\lp45" --out "G:\some\other\folder" --extinction 0.02
```

- `--extinction` -- measured polarizer extinction ratio (Imin/Imax). Omit it to be prompted interactively instead.

`main.py` is the only file you run. `image_loader.py` and
`solve_mueller.py` are library modules it imports -- they are not meant to
be executed on their own (though `solve_mueller.py` also has a small
`__main__` for a quick print-only check without saving any files:
`python solve_mueller.py <run_directory>`).

If you point this at a run whose `experiment_config.json` says `"mode":
"4x4"`, it will refuse with a clear error instead of silently misreading the
filenames -- use `own_code/4x4` for that run instead.

## What gets written to `RESULT/transmission/3x3/reconstructions/<date>/.../<run folder name>/`

| File | Contents |
|---|---|
| `mueller_matrix_normalized.npy` | `(H, W, 3, 3)` array, every pixel's Mueller matrix, normalized so `m00 = 1` |
| `mueller_matrix_raw.npy` | Same shape, before the `m00` normalization |
| `residual_rms.npy` | `(H, W)` per-pixel fit error -- how well the reconstructed matrix explains the measured intensities |
| `calibration_used.json` | `{"extinction_ratio": ...}` -- lets `validate_against_theory.py` verify it can safely reuse this reconstruction instead of redoing it (see below) |
| `summary.txt` | Condition number, mean residual, the spatially-averaged Mueller matrix, and provenance (git commit, timestamp, source run, calibration), as text |
| `mueller_matrix_overview.png` | 3x3 grid of grayscale maps, one per matrix element |
| `residual_rms.png` | Heatmap of the residual, for spotting bad pixels/regions at a glance |

## The pipeline, in order

`main.py` calls the other two modules in this order every time it runs:

1. **`image_loader.load_run(run_directory)`**
   Opens `Config/experiment_config.json`, confirms `mode == "3x3"` (raises a
   clear error otherwise), then scans `Images/` for every file named
   `psg_angle_psa_angle.ext` and loads it. Returns a `RunImages3x3` object:
   the two angle arrays and the stacked images, in filename order. This is
   the only place that reads image files or the config.

2. **`solve_mueller.reconstruct(run, extinction_ratio)`**
   Calls into `mueller_forward_model` to build one equation per image,
   stacks all N of them, and solves for the Mueller matrix by least squares.
   Returns a `MuellerResult3x3`: the per-pixel matrix, the spatial mean, and
   diagnostics (condition number, residual).

`mueller_forward_model.py` itself has no I/O -- it's pure physics, called by
`solve_mueller.py`, not directly by `main.py`.

3. **`main.save_outputs(result, out_dir)`**
   Writes everything in the table above.

## Which file do I run, and in what order?

`main.py` is the only file required for a single capture. The other four
scripts are optional, used in this order as your workflow matures:

| Order | Script | When to use it |
|---|---|---|
| 1 | `main.py` | Every single capture. Reconstructs and saves one run's Mueller matrix. |
| 2 | `average_rounds.py` | You captured >= 3 repeat rounds of the *same* sample (see `../../NAMING.md`). Reconstructs each round independently (identical math to `main.py`), then reports the mean and standard deviation *across rounds* -- this is the only place round-to-round repeatability (not just per-pixel noise) is measured. Edit `ROUND_DIRECTORIES` at the top, then `python average_rounds.py`. |
| 3 | `validate_against_theory.py` | You have a reference sample with a *known* theoretical answer (air, or a linear polarizer at a known angle) and want to check the reconstruction against it. Edit `SAMPLE_DIRECTORIES`, then `python validate_against_theory.py` -- see "Checking against a known theoretical matrix" below for what it computes. |
| 4 (conditional) | `fit_calibration.py` | Only if step 3 showed your air sample's reconstruction deviating from identity by more than noise. Numerically searches for the `extinction_ratio` value that minimizes that deviation, so you're not guessing it by hand. Edit `AIR_DIRECTORY`, then `python fit_calibration.py`. See "Calibrating from an air capture" below. |

### Checking against a known theoretical matrix (`validate_against_theory.py`)

For samples whose true Mueller matrix you already know -- air (should
reconstruct to the identity matrix) or a linear polarizer at a known angle
(should reconstruct to the textbook linear-polarizer formula) --
`validate_against_theory.py` reconstructs each configured sample (reusing
`main.py`'s saved reconstruction automatically if one already exists with
matching `extinction_ratio`, otherwise redoing it from the raw images) and
reports how far the reconstruction deviates from theory, using the
**Frobenius norm** of the difference between the two matrices:

```
error = ||measured - theory||_F = sqrt( sum over every element of (measured_ij - theory_ij)^2 )
```

This is the exact same "distance between two matrices" calculation used
throughout this project -- see the root README's full walkthrough (with a
worked numeric example) of why this specific formula, and why it always
ranks identically to Mean Squared Error (MSE): `Frobenius = sqrt(N) x RMSE`,
where `N = 9` for a 3x3 matrix. It also prints each non-air sample's error as
a multiple of air's own error ("2.3x air's baseline") -- if every sample's
ratio is close to 1x, the deviation is a systematic PSG/PSA modeling issue
(affecting every sample equally), not something specific to any one sample.

### Calibrating from an air capture (`fit_calibration.py`)

Air's true Mueller matrix is the identity matrix, with certainty -- there's
no ambiguity to argue about, unlike a real sample. That makes it the ideal
reference for tuning the one free physical parameter this pipeline has,
`extinction_ratio`: `fit_calibration.py` reconstructs your air capture
repeatedly with a range of candidate `extinction_ratio` values (a
coarse-to-fine grid search, refining around whichever candidate currently
gives the lowest error), and reports the value that makes the reconstruction
closest to the identity matrix. If that improved value doesn't meaningfully
reduce the error versus the ideal default (`0`), your air data is already
consistent with an ideal polarizer and there's nothing to fix.

**What this can't fix:** a PSG/PSA angle-zero misalignment (`ZERO_OFFSET`)
is a physical motor calibration living in the *acquisition* side's
`config.py` (found with its own `calibration.py`, under `Measuremt_
script/discreate_angle/`) -- no amount of curve-fitting after the fact can
correct for images captured at the wrong actual angle. If `fit_calibration.py`'s
best achievable error is still well above your per-pixel noise floor (see
`residual_rms` in `main.py`'s output), that's the more likely culprit, and
the fix requires physically recalibrating the motor zero and recapturing.

## The physics

### Why a rotated polarizer has to be built from a "sandwich"

A polarizer's Mueller matrix is usually written for its transmission axis at
0 degrees, `M(0)`. If you physically rotate the polarizer by `theta`, you
can't just use `M(0)` -- you have to rotate the reference frame the light's
Stokes vector is expressed in, apply the un-rotated matrix, then rotate the
frame back:

```
M(theta) = R(-theta) @ M(0) @ R(theta)
```

`mueller_rotator(theta)` builds `R(theta)`. `mueller_linear_polarizer(theta)`
builds its `M(0)` and applies this sandwich -- that is the only way it
computes anything; there is no closed-form shortcut hardcoded anywhere.

### Generating and analyzing a polarization state

Unpolarized light `[1,0,0,0]` passes through a rotating polarizer (PSG).
`generator_stokes_3x3(angle)` returns the first 3 Stokes components of the
result. Symmetrically, `analyzer_vector_3x3(angle)` gives the row vector
such that `intensity = analyzer_vector . Stokes_before_analyzer` for the
rotating analyzer polarizer (PSA).

Because 3x3 mode never uses a QWP, it can only generate and analyze *linear*
polarization states. It will still reconstruct a valid matrix for *any*
sample (air, a polarizer, a QWP, tissue...), but it can only recover the
`S0,S1,S2` sub-block of that sample's true Mueller matrix -- any
circular-polarization-coupled elements (e.g. a QWP's `m03`, `m30`, `m33`)
are invisible to a linear-states-only measurement. That's a property of the
3x3 method itself, not a limitation of this code -- see `own_code/4x4` for
the method that can see those elements.

### From per-image intensities to a full matrix

For one image, with generator Stokes vector `S` (length 3) and analyzer
vector `A` (length 3):

```
intensity = A . M . S = sum_ij  A_i * M_ij * S_j
```

That's linear in the unknown `M_ij` entries, so it can be rewritten as
`intensity = H . vec(M)` where `H = kron(A, S)` (length 9). Stack all N
images' `H` rows into one matrix and solve:

```
vec(M) = pinv(H) @ intensities
```

`solve_mueller.py` does this once, vectorized over every pixel
simultaneously (all pixels share the same `H` -- only the intensities
differ), which is why it works identically whether N is 9 or 49: more
images just means more rows in `H`, and `pinv` does a least-squares fit
instead of an exact solve. That's also *why* more images improve precision
-- an overdetermined least-squares fit averages out per-image noise,
whereas the exact-9-image case has no such averaging.

`condition_number` (in `summary.txt`) is a sanity check on `H` itself: a
well-conditioned angle set (e.g. evenly spaced angles) gives a low number;
if it's very large, the chosen angles don't distinguish the matrix elements
well and the fit will amplify noise.

`residual_rms` measures, per pixel, how well the fitted matrix actually
reproduces the N measured intensities -- large residuals flag pixels (or
whole datasets) where the linear model doesn't fit well, e.g. from motion,
saturation, or a bad image.

### Ideal vs. calibrated optics

By default the polarizer is modeled as ideal (extinction ratio `0`, perfect
axis). This is the one parameter on `reconstruct()`, and `main.py` prompts
for it every run (or accepts `--extinction` on the command line) rather
than silently assuming the ideal value. If you accept the ideal default
while your real polarizers deviate from it, the reconstruction will carry a
small systematic bias -- type your measured value at the prompt once you
have one.

## What you'd need to change to run this on new data

Nothing in the code -- only:

- `RUN_DIRECTORY` at the top of `main.py` (or the CLI argument), to point at
  a different 3x3 run.
- `--extinction`, once you have a real calibration number for your
  polarizers (currently defaults to ideal).

The code auto-detects everything else (image count, angles, image size)
from `Config/experiment_config.json` and the filenames in `Images/`.

## Requirements

`numpy`, `matplotlib`, `Pillow` (PIL) -- `main.py` and `average_rounds.py`
each check for these on startup and `pip install` whichever are missing
into the same Python interpreter that's running the script, before doing
anything else. If `pip` itself isn't available in that interpreter (e.g. a
minimal/embedded Python), you'll still need to point at one that has it --
see the "Getting the right Python interpreter" note below.

### Getting the right Python interpreter

If you're running this from VS Code and see `ModuleNotFoundError` despite
the auto-install above, it likely means the interpreter VS Code is using
doesn't have `pip` either (some minimal/tool-specific Python installs
don't). Point VS Code at a full Python installation instead -- e.g. an
Anaconda/Miniconda install, or python.org's installer -- via `Ctrl+Shift+P`
-> "Python: Select Interpreter", or by setting `python.defaultInterpreterPath`
in `.vscode/settings.json` at the repository root.
