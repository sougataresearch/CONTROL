# 4x4 Mueller matrix reconstruction — continuous rotation

Reconstructs a sample's full 4x4 Mueller matrix from N intensity frames
captured by the rig in `Measuremt_ script/continous_rotation` (fixed
`PSG_Polarizer`/`PSA_Analyzer`, continuously spinning `PSG_QWP`/`PSA_QWP` at
a configured revolution ratio, e.g. `1:5`). Works for any number of frames
and any sample — the code never assumes a fixed frame count or hardcodes
what the sample is.

This folder is fully self-contained: it does not import or depend on
anything in `../../DISCRETE/`, `matrix/tinghuye/`,
`Mueller_calculation_36_images_method.py`, or `Measuremt_ script/`. It only
reads the images, the per-frame CSV log, and the JSON config that
`continous_rotation/01_main.py` already produces.

## How this differs from `../../DISCRETE/4x4/`

The physics is **identical** — `mueller_forward_model.py` in this folder is
a verbatim copy of the discrete pipeline's: a fixed polarizer followed by a
rotating QWP, on both the generator and analyzer side. What differs is only
how the (angle, intensity) pairs are gathered:

- **Discrete**: a handful of named states (16, 49, 144, ...), each image's
  PSG_QWP/PSA_QWP angle read straight from its filename.
- **Continuous**: many more frames (360 by default, one per degree of
  PSG_QWP travel over one full revolution — see `capture_angle_step_deg` in
  `continous_rotation/config.py`), each frame's *actual polled* angle read
  from `Logs/experiment_log.csv` rather than its filename (which is rounded
  to 1 decimal for readability only — see `image_loader.py`'s docstring).

Because `solve_mueller.py`'s reconstruction is already a **generalized
least-squares fit** over however many (angle, intensity) rows a run has —
not a fit tied to a specific discrete grid — a continuous sweep's 360
non-grid samples slot into the exact same linear system a handful of
discrete-angle images would build. **No separate Fourier/harmonic-analysis
step is needed.** More, well-distributed samples over a full revolution
just improve the fit's conditioning and average out noise, the same way
more discrete images do in the other pipeline.

## Why angle-triggered capture, not frame-rate free-run

`continuous_engine.py` (the acquisition side) fires the camera every time
PSG_QWP crosses a fixed angular step, rather than free-running the camera
at a fixed fps. A least-squares (or, classically, harmonic) fit over one
revolution wants images at *known* angles; angle-triggered capture
guarantees that directly regardless of real hardware's velocity ripple
(acceleration jitter, encoder noise). Frame-rate free-run would only give
evenly-spaced angles if velocity were perfectly constant, and would still
need the real encoder angle logged per frame to correct for when it isn't —
so angle-triggered is both simpler and more directly correct here. Every
frame's actual angle (not the nominal threshold) is what ends up in
`Logs/experiment_log.csv` and is read by `image_loader.py`.

## Run this

Open `main.py` and edit the one line near the top:

```python
RUN_DIRECTORY = r"G:\control\Data\continuous\sample1"
```

to point at whatever continuous run you want to process — any folder
containing `Images/`, `Logs/experiment_log.csv`, and
`Config/experiment_config.json` with a populated `"fixed_angles"` works. It
does not need to be inside this project, or on the same drive as anything
else. Then:

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

Results are saved to
`C:\COMPARE_CASES\RESULT\transmission\continuous_4x4\reconstructions\<date>\.../<run
folder name>\` by default — if `RUN_DIRECTORY` sits under a dated
`Data/<8-digit-date>/...` layout, that same date/sample-type path is
mirrored under that folder so the same sample name captured on a different
date doesn't collide with an earlier result; otherwise it falls back to
just `RESULT/transmission/continuous_4x4/reconstructions/<run folder
name>/` (the common case for continuous captures, which aren't usually
organized by date). `RESULT/` is a single shared output root for every tool
in this whole project (see the root README) — it's created automatically
the first time anything writes to it; you never need to create it yourself.
Set `OUTPUT_DIRECTORY` at the top of `main.py` if you want a specific run's
output somewhere else instead.

You can also pass everything as command-line arguments instead of editing
the file or answering the prompts:

```
python main.py "G:\control\Data\continuous\sample2" --out "G:\some\other\folder" --extinction 0.02 --retardance 88.5
```

- `--extinction` — measured polarizer extinction ratio (Imin/Imax). Omit it to be prompted interactively instead.
- `--retardance` — measured QWP retardance in degrees. Omit it to be prompted interactively instead.

`main.py` is the only file you run. `image_loader.py` and
`solve_mueller.py` are library modules it imports — `solve_mueller.py` also
has a small `__main__` for a quick print-only check without saving any
files: `python solve_mueller.py <run_directory>`.

### Dark-current subtraction

Every camera sensor reads out a nonzero baseline even with no real signal
hitting it (thermal/read noise, ADC bias, hot pixels). Left uncorrected,
that constant offset biases every reconstructed Mueller matrix element,
worst where the true signal is small (e.g. near-crossed polarizers).

To correct for it: after capturing this run's frames (same exposure/gain,
same camera settings), block all light reaching the camera -- either turn
off the light source, or remove/cover all optical components. Both are
equally valid: dark current depends on the sensor and exposure, not on
what's in the beam path. Capture one or more frames this way and save them
as image files (any names) into a `Dark/` subfolder next to `Images/`, i.e.
`<run_directory>/Dark/`.

If `Dark/` is present, `image_loader.py` averages every frame inside it
into a single reference and subtracts it (clipped at 0) from every image
before reconstruction -- several dark frames are worth capturing since
averaging them reduces the reference's own read noise. If `Dark/` is
absent, `main.py` prints a warning explaining this and proceeds on raw
intensities, exactly as before this feature existed -- nothing breaks on
old runs that don't have a `Dark/` folder. Whether subtraction was applied,
how many frames were averaged, and the mean dark level are all recorded in
`summary.txt` and printed to the terminal.

## What gets written to `RESULT/transmission/continuous_4x4/reconstructions/<date>/.../<run folder name>/`

| File | Contents |
|---|---|
| `mueller_matrix_normalized.npy` | `(H, W, 4, 4)` array, every pixel's Mueller matrix, normalized so `m00 = 1` |
| `mueller_matrix_raw.npy` | Same shape, before the `m00` normalization |
| `residual_rms.npy` | `(H, W)` per-pixel fit error — how well the reconstructed matrix explains the measured intensities |
| `calibration_used.json` | `{"extinction_ratio": ..., "retardance_deg": ...}` — lets `validate_against_theory.py` verify it can safely reuse this reconstruction instead of redoing it (see below) |
| `summary.txt` | Condition number, mean residual, and the spatially-averaged Mueller matrix, plus provenance (git commit, timestamp, source run, calibration), as text |
| `mueller_matrix_overview.png` | 4x4 grid of grayscale maps, one per matrix element |
| `residual_rms.png` | Heatmap of the residual, for spotting bad frames/regions at a glance |

## The pipeline, in order

1. **`image_loader.load_run(run_directory)`**
   Reads `Config/experiment_config.json` for `fixed_angles["PSG_Polarizer"]`/
   `["PSA_Analyzer"]`, then reads `Logs/experiment_log.csv` for every
   `Status == "SUCCESS"` frame's actual `PSG_QWP Angle`/`PSA_QWP Angle`,
   matching each logged frame index to its image file under `Images/`
   (`frame_{index:04d}_*`). A logged `SUCCESS` row with no matching file is
   skipped with a warning rather than guessed at; `FAILED` rows are skipped
   silently (the camera already logged why). Returns a `RunImages4x4`
   object — same shape as the discrete pipeline's, so `solve_mueller.py`
   needs no reconstruction-side changes at all.

2. **`solve_mueller.reconstruct(run, extinction_ratio, retardance_deg)`**
   Identical to `../../DISCRETE/4x4/solve_mueller.py` — see that folder's
   README for the full linear-algebra derivation
   (`intensity = A · M · S = H · vec(M)`, stacked over every frame, solved
   by `pinv`). Returns a `MuellerResult4x4`: the per-pixel matrix, the
   spatial mean, and diagnostics (condition number, residual).

3. **`main.save_outputs(result, out_dir)`**
   Writes everything in the table above.

## Which file do I run, and in what order?

`main.py` is the only file required for a single capture. There's no
`average_rounds.py` in this folder (continuous captures aren't organized
into repeat rounds the same way discrete ones are). The other two scripts
are optional, used in this order as your workflow matures:

| Order | Script | When to use it |
|---|---|---|
| 1 | `main.py` | Every single capture. Reconstructs and saves one run's Mueller matrix. |
| 2 | `validate_against_theory.py` | You have a reference sample with a *known* theoretical answer (air, an ideal linear polarizer, or an ideal QWP at a known angle). Edit `SAMPLE_DIRECTORIES`, then `python validate_against_theory.py`. Same Frobenius-norm calculation as `../../DISCRETE/4x4/README.md` describes in full (worked example there) -- `N = 16` here too. |
| 3 (conditional) | `fit_calibration.py` | Only if step 2 showed your air sample deviating from identity by more than noise. Numerically searches (coordinate descent) for the `(extinction_ratio, retardance_deg)` pair that minimizes that deviation. Edit `AIR_DIRECTORY`, then `python fit_calibration.py`. See `../../DISCRETE/4x4/README.md`'s fuller explanation -- identical method, applied to a continuous-rotation air capture instead of a discrete one. Can't fix a `ZERO_OFFSET` motor-zero misalignment; that's a physical recalibration on the acquisition side. |

## Ideal vs. calibrated optics

By default the polarizer is modeled as ideal (extinction ratio `0`, perfect
axis) and the QWP as an ideal quarter-wave plate (retardance exactly `90`).
`main.py` prompts for both every run (or accepts `--extinction`/
`--retardance` on the command line) rather than silently assuming ideal
values — type your measured values at the prompts once you have them.
Accepting the ideal defaults while real optics deviate from them carries a
small systematic bias into the reconstruction, exactly as in the discrete
pipeline.

## What you'd need to change to run this on new data

Nothing in the code — only:

- `RUN_DIRECTORY` at the top of `main.py` (or the CLI argument), to point at
  a different continuous run.
- `--extinction` / `--retardance`, once you have real calibration numbers.

The code auto-detects everything else (frame count, per-frame QWP angles,
the fixed polarizer/analyzer angles, image size) from
`Config/experiment_config.json`, `Logs/experiment_log.csv`, and the images
in `Images/`.

## Requirements

`numpy`, `matplotlib`, `Pillow` (PIL) — `main.py` checks for these on
startup and `pip install`s whichever are missing into the same Python
interpreter that's running the script, before doing anything else. If
`pip` itself isn't available in that interpreter, see
`../../DISCRETE/4x4/README.md`'s "Getting the right Python interpreter" note.
