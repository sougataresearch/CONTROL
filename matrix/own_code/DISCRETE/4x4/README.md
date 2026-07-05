# 4x4 Mueller matrix reconstruction

Reconstructs a sample's full 4x4 Mueller matrix from N intensity images
captured by the rig in `Measuremt_ script/discreate_angle` in **4x4 mode**
(fixed `PSG_Polarizer`/`PSA_Analyzer`, rotating `PSG_QWP`/`PSA_QWP`). Works
for any number of images and any sample -- the code never assumes a fixed
image count or hardcodes what the sample is.

**No 4x4 dataset has been captured yet** (as of this writing you only have
3x3 data: air, lp30, lp45, lp90). `RUN_DIRECTORY` in `main.py` is a
placeholder -- point it at your first real 4x4 run once you have one.

Naming your run folders correctly matters more than anything in this
README -- see `../../NAMING.md` for the one rule, and for repeat rounds of
the same sample see `average_rounds.py` in this folder.

This folder is fully self-contained: it does not import or depend on
anything in `own_code/3x3`, `matrix/tinghuye/`,
`Mueller_calculation_36_images_method.py`, or `Measuremt_ script/`. It only
reads the images and JSON config those scripts already produce.

## Physics background, from zero

Skip this section if you already know what a Stokes vector and a Mueller
matrix are, and just want the 4x4-specific parts -- read `own_code/3x3`'s
README first if this is genuinely new, it builds the same ideas up more
slowly.

### The short version

A beam of light's polarization state is described by four numbers, the
**Stokes vector** `(S0, S1, S2, S3)`: `S0` is total brightness, `S1` and
`S2` describe linear polarization (horizontal-vs-vertical, and
45°-vs-135°), and `S3` describes circular polarization (right-vs-left
handed). A sample's effect on polarization is a **Mueller matrix** `M`
(here, the full 4x4): `Stokes_out = M @ Stokes_in`. A camera can only
measure `S0` (plain intensity) of whatever light reaches it, so `M`'s 16
unknown entries are found by generating several *known* input Stokes
states (the PSG) and analyzing the output through several known filter
settings (the PSA), recording one intensity per combination, and solving
the resulting system of linear equations. See the 3x3 README for the fuller
version of this explanation, including a walkthrough of *why* a camera
pixel can't see `S1`/`S2`/`S3` directly.

### Why 4x4 needs a QWP, and 3x3 doesn't

A plain rotating polarizer (3x3 mode) can only ever *produce* or *detect*
linear polarization -- rotate it to any angle and the output is still some
combination of `S0,S1,S2`, never any `S3`. To generate or analyze circular
polarization, you need a **quarter-wave plate (QWP)**: it delays the
component of light aligned with its "slow axis" by a quarter of a
wavelength relative to the perpendicular "fast axis" component. Fed 45°
linear light, that quarter-wavelength delay is exactly enough to turn the
straight-line oscillation into a circular one. Rotate the QWP to a
different angle relative to the incoming linear polarization and you get
every state in between -- linear, elliptical, and fully circular -- which
is how a rotating QWP traces out a path across the *entire* Poincaré sphere
(the geometric picture of all possible Stokes vectors) instead of being
confined to the linear-only equator that 3x3 mode is stuck on.

That's the physical reason this rig's 4x4 mode keeps the linear polarizer
**fixed** and only rotates the **QWP**: the fixed polarizer sets one
specific input linear state, and rotating the QWP relative to it sweeps
through every reachable polarization state -- linear, circular, and
elliptical -- giving enough equations to solve for all 16 unknowns of `M`,
including the ones (`m03, m30, m33`, etc.) that involve `S3` and are
completely invisible to 3x3 mode.

## Run this

Open `main.py` and edit the one line near the top:

```python
RUN_DIRECTORY = r"G:\control\Data\03072026\qwp\qwp90"
```

to point at whatever 4x4 run you want to process -- any folder containing
an `Images/` subfolder and a `Config/experiment_config.json` with `"mode":
"4x4"` and a populated `"fixed_angles"` (the `PSG_Polarizer`/`PSA_Analyzer`
angles that were held constant for that run). It does not need to be inside
this project, or on the same drive as anything else. Then:

```
python main.py
```

You'll be prompted in the terminal for the polarizer extinction ratio and
the QWP retardance:

```text
Polarizer extinction ratio Imin/Imax [0]: 0.02
QWP retardance in degrees [90]: 88.5
```

Type your measured values and press Enter, or just press Enter (blank) on
either to accept the suggested default shown in brackets — the ideal values
(`0`/`90`) the first time you run this, then whatever you entered last time
after that (remembered in `.last_calibration.json` next to `main.py`, not
committed to git).

Results are saved to `own_code/4x4/Results/<run folder name>/` by default
(e.g. `Results/qwp90/`) -- deliberately *not* inside the data folder, since
`RUN_DIRECTORY` may point somewhere else entirely. Set `OUTPUT_DIRECTORY` at
the top of `main.py` if you want them somewhere specific instead.

You can also pass everything as command-line arguments instead of editing
the file or answering the prompts, e.g. for scripting multiple runs without
touching `main.py`:

```
python main.py "G:\control\Data\03072026\qwp\qwp45" --out "G:\some\other\folder" --extinction 0.02 --retardance 88.5
```

- `--extinction` -- measured polarizer extinction ratio (Imin/Imax). Omit it to be prompted interactively instead.
- `--retardance` -- measured QWP retardance in degrees. Omit it to be prompted interactively instead.

`main.py` is the only file you run. `image_loader.py` and
`solve_mueller.py` are library modules it imports -- they are not meant to
be executed on their own (though `solve_mueller.py` also has a small
`__main__` for a quick print-only check without saving any files:
`python solve_mueller.py <run_directory>`).

If you point this at a run whose `experiment_config.json` says `"mode":
"3x3"`, it will refuse with a clear error instead of silently misreading
the filenames -- use `own_code/3x3` for that run instead.

## What gets written to `Results/<run folder name>/`

| File | Contents |
|---|---|
| `mueller_matrix_normalized.npy` | `(H, W, 4, 4)` array, every pixel's Mueller matrix, normalized so `m00 = 1` |
| `mueller_matrix_raw.npy` | Same shape, before the `m00` normalization |
| `residual_rms.npy` | `(H, W)` per-pixel fit error -- how well the reconstructed matrix explains the measured intensities |
| `summary.txt` | Condition number, mean residual, and the spatially-averaged Mueller matrix, as text |
| `mueller_matrix_overview.png` | 4x4 grid of grayscale maps, one per matrix element |
| `residual_rms.png` | Heatmap of the residual, for spotting bad pixels/regions at a glance |

## The pipeline, in order

`main.py` calls the other two modules in this order every time it runs:

1. **`image_loader.load_run(run_directory)`**
   Opens `Config/experiment_config.json`, confirms `mode == "4x4"` and reads
   `fixed_angles["PSG_Polarizer"]`/`["PSA_Analyzer"]` (raises a clear error
   if either is missing), then scans `Images/` for every file named
   `psg_qwp_angle_psa_qwp_angle.ext` and loads it. Returns a `RunImages4x4`
   object: the fixed angles, the two QWP angle arrays, and the stacked
   images, in filename order. This is the only place that reads image files
   or the config.

2. **`solve_mueller.reconstruct(run, extinction_ratio, retardance_deg)`**
   Calls into `mueller_forward_model` to build one equation per image,
   stacks all N of them, and solves for the Mueller matrix by least squares.
   Returns a `MuellerResult4x4`: the per-pixel matrix, the spatial mean, and
   diagnostics (condition number, residual).

`mueller_forward_model.py` itself has no I/O -- it's pure physics, called by
`solve_mueller.py`, not directly by `main.py`.

3. **`main.save_outputs(result, out_dir)`**
   Writes everything in the table above.

## The physics

### Why a rotated polarizer or QWP has to be built from a "sandwich"

An optical element's Mueller matrix is usually written for its axis at 0
degrees, `M(0)`. If you physically rotate the element by `theta`, you can't
just use `M(0)` -- you have to rotate the reference frame the light's Stokes
vector is expressed in, apply the un-rotated matrix, then rotate the frame
back:

```
M(theta) = R(-theta) @ M(0) @ R(theta)
```

`mueller_rotator(theta)` builds `R(theta)`. `mueller_linear_polarizer(theta)`
and `mueller_retarder(theta)` each build their own `M(0)` and apply this
sandwich -- that is the only way either function computes anything; there
is no closed-form shortcut hardcoded anywhere.

### Generating and analyzing a polarization state

Unpolarized light `[1,0,0,0]` passes through the **fixed** polarizer, then
the **rotating** QWP (PSG). `generator_stokes_4x4(qwp_angle,
fixed_polarizer_angle)` chains both Mueller matrices and returns the
resulting full Stokes vector. `analyzer_vector_4x4(qwp_angle,
fixed_analyzer_angle)` does the mirror version for the analyzer side
(rotating QWP first, then the fixed analyzer), giving the row vector such
that `intensity = analyzer_vector . Stokes_before_analyzer`.

This exactly matches `generate_4x4_discrete` in
`Measuremt_ script/discreate_angle/state_generator.py`: the polarizer stays
put, only the QWP sweeps.

### From per-image intensities to a full matrix

For one image, with generator Stokes vector `S` (length 4) and analyzer
vector `A` (length 4):

```
intensity = A . M . S = sum_ij  A_i * M_ij * S_j
```

That's linear in the unknown `M_ij` entries, so it can be rewritten as
`intensity = H . vec(M)` where `H = kron(A, S)` (length 16). Stack all N
images' `H` rows into one matrix and solve:

```
vec(M) = pinv(H) @ intensities
```

`solve_mueller.py` does this once, vectorized over every pixel
simultaneously (all pixels share the same `H` -- only the intensities
differ), which is why it works identically whether N is 16 or 144: more
images just means more rows in `H`, and `pinv` does a least-squares fit
instead of an exact solve. That's also *why* more images improve precision
-- an overdetermined least-squares fit averages out per-image noise,
whereas the exact-16-image case has no such averaging.

`condition_number` (in `summary.txt`) is a sanity check on `H` itself: a
well-conditioned QWP angle set (e.g. evenly spaced angles) gives a low
number; if it's very large, the chosen angles don't distinguish the matrix
elements well and the fit will amplify noise.

`residual_rms` measures, per pixel, how well the fitted matrix actually
reproduces the N measured intensities -- large residuals flag pixels (or
whole datasets) where the linear model doesn't fit well, e.g. from motion,
saturation, or a bad image.

### Ideal vs. calibrated optics

By default the polarizer is modeled as ideal (extinction ratio `0`, perfect
axis) and the QWP as an ideal quarter-wave plate (retardance exactly `90`).
These are the two parameters on `reconstruct()`, and `main.py` prompts for
both every run (or accepts `--extinction`/`--retardance` on the command
line) rather than silently assuming ideal values. If you accept the ideal
defaults while your real optics deviate from them, the reconstruction will
carry a small systematic bias -- type your measured values at the prompts
once you have them.

## What you'd need to change to run this on new data

Nothing in the code -- only:

- `RUN_DIRECTORY` at the top of `main.py` (or the CLI argument), to point at
  a different 4x4 run.
- `--extinction` / `--retardance`, once you have real calibration numbers
  for your polarizers/QWPs (currently both default to ideal).

The code auto-detects everything else (image count, QWP angles, the fixed
polarizer/analyzer angles, image size) from `Config/experiment_config.json`
and the filenames in `Images/`.

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
