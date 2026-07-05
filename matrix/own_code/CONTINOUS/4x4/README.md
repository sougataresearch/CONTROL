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

Results are saved to `own_code/CONTINOUS/4x4/Results/<run folder name>/` by
default — deliberately *not* inside the data folder, since `RUN_DIRECTORY`
may point somewhere else entirely. Set `OUTPUT_DIRECTORY` at the top of
`main.py` if you want them somewhere specific instead.

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

## What gets written to `Results/<run folder name>/`

| File | Contents |
|---|---|
| `mueller_matrix_normalized.npy` | `(H, W, 4, 4)` array, every pixel's Mueller matrix, normalized so `m00 = 1` |
| `mueller_matrix_raw.npy` | Same shape, before the `m00` normalization |
| `residual_rms.npy` | `(H, W)` per-pixel fit error — how well the reconstructed matrix explains the measured intensities |
| `summary.txt` | Condition number, mean residual, and the spatially-averaged Mueller matrix, as text |
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
