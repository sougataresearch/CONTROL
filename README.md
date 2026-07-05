# CONTROL

Control and analysis software for a Mueller Matrix Imaging Ellipsometer (MMIE):
four Thorlabs K10CR2/M rotation mounts (two polarizers, two quarter-wave
plates) driven through Thorlabs Kinesis, and one IDS U3-3890CP-M-GL camera
driven through the IDS Peak SDK.

## Repository layout

```
control/
├── Measuremt_ script/
│   ├── discreate_angle/       ← 3x3 and 4x4 discrete acquisition (production)
│   ├── continous_rotation/    ← 4x4 continuous rotation (independent, WIP)
│   ├── MMIE_Control/          ← earlier notebook-based reference implementation
│   └── check_config_sync.py   ← standalone: diffs motor calibration between the two folders
└── matrix/                    ← offline Mueller matrix reconstruction from saved images
    ├── NAMING.md               ← the one folder-naming rule, used by every capture
    ├── own_code/
    │   ├── DISCRETE/
    │   │   ├── 3x3/            ← reconstructs a 3x3 Mueller matrix from discrete-angle images
    │   │   └── 4x4/            ← reconstructs a full 4x4 Mueller matrix from discrete-angle images
    │   └── CONTINOUS/
    │       └── 4x4/            ← reconstructs a full 4x4 Mueller matrix from a continuous-rotation run
    ├── tinghuye/                ← earlier from-scratch reconstruction scripts, kept for reference
    └── Mueller_calculation_36_images_method.py  ← reference-paper's canonical 36-image method
```

**The whole project, end to end:** capture images with an acquisition
folder under `Measuremt_ script/` → copy that run's images and config into a
sample-labeled folder following `matrix/NAMING.md` → run the matching
pipeline under `matrix/own_code/DISCRETE/` or `matrix/own_code/CONTINOUS/`
to get a reconstructed Mueller matrix. Each destination has its own README
with a "physics background, from zero" section — together they explain,
from no prior assumed knowledge, what a Stokes vector and a Mueller matrix
are, why the rig rotates the motors it does, and exactly how the images
turn into a matrix.

The two acquisition folders under `Measuremt_ script/` are **deliberately
independent** — neither imports from the other, and neither shares a `Data/`
run directory. They cover different experiment shapes and evolved to have
different acquisition loops, so keeping them separate avoids one mode's
control flow leaking into the other's.

### `Measuremt_ script/discreate_angle/`

The current, working entry point for **3×3** (2 polarizers) and **4×4
discrete** (2 polarizers + 2 stepped QWPs) Mueller matrix acquisition. Run
`python 01_main.py`. Hardware bring-up (discover/connect/initialize/
enable/home/zero) and camera Cockpit setup happen once per session; the
operator is then looped through as many samples as needed without
restarting the script — each sample gets its own `Data/YYYY-MM-DD_<sample
name>` folder, its own auto-selected bright/dark reference ROI (re-verified
every sample), and a prompt to insert/remove the sample at the right point
so nothing measures with the beam path in the wrong state. A real
acquisition failure offers to skip that sample and continue with the next
one rather than aborting the whole queue; motors rehome and everything
disconnects for real only once the operator is done with all samples.
Retried/verified image acquisition, crash-safe checkpointing (`--resume`,
single-sample only), CSV logging, and a final report per sample round out
the flow. See its own `README.md` for full operator instructions and a
function-by-function reference.

### `Measuremt_ script/continous_rotation/`

The entry point for **4×4 continuous rotation only** (a 3×3 continuous mode
— dual rotating linear polarizers, no QWPs — was considered and
deliberately not built), where both QWPs spin continuously at a fixed
revolution ratio (classically 1:5 for a dual-rotating-retarder polarimeter
— see that folder's README) instead of stepping through discrete angles.
Structurally mirrors `discreate_angle/`'s session/multi-sample/disconnect
behavior described above. The acquisition loop (`continuous_engine.py`)
captures with **angle-triggered** timing: the camera fires every time
`PSG_QWP` crosses a configured angular step
(`TimingSettings.capture_angle_step_deg`, default 1°, i.e. 360 frames per
revolution), logging each frame's *actual* polled `PSG_QWP`/`PSA_QWP`
angle — not the nominal threshold — to `Logs/experiment_log.csv`. This was
chosen over frame-rate free-run because the reconstruction side needs
images at known angles regardless of real hardware's velocity ripple; see
that module's docstring for the full reasoning.

### `Measuremt_ script/MMIE_Control/`

An earlier, notebook-driven (`NB0`–`NB4`) reference implementation of the
same hardware control, kept for comparison. Not required to run either
folder above.

### `matrix/`

Everything here is offline: it reads previously captured images and never
touches the motors or camera. Nothing in `matrix/` is imported by, or
modifies, anything under `Measuremt_ script/`.

- **`NAMING.md`** — the one folder-naming rule every capture should follow
  before either pipeline below can use it (what to call a single run vs. a
  repeat round, and why). Read this first if you're not sure where a run's
  images should live.

- **`own_code/DISCRETE/3x3/`** — reconstructs a sample's 3×3 Mueller matrix
  (linear-polarization sub-block only) from however many PSG/PSA-angle
  images a run actually has, using the real rotation physics of a rotated
  polarizer rather than a fixed-image-count shortcut. Works on any sample —
  air, a polarizer, a QWP, tissue — since the sample's identity never
  enters the code. `main.py` is the one file you run for a single capture;
  `average_rounds.py` aggregates several repeat rounds of the same sample
  into a mean and standard deviation. See its `README.md` for a full
  physics primer (assuming no prior polarimetry background) and a
  function-by-function walkthrough.

- **`own_code/DISCRETE/4x4/`** — the full 4×4 counterpart for
  `discreate_angle/`'s discrete-angle acquisition: same architecture, same
  usage pattern (`main.py` / `average_rounds.py`), but models the rig's
  fixed-polarizer + rotating-QWP generator/analyzer instead, so it can also
  recover the circular-polarization-coupled entries a 3×3 measurement
  cannot see.

  `own_code/DISCRETE/3x3/` and `own_code/DISCRETE/4x4/` are deliberately
  independent — no shared files, each with its own complete copy of the
  rotation physics, its own image loader (which refuses to run on the other
  mode's data with a clear error), and its own README.

- **`own_code/CONTINOUS/4x4/`** — the 4×4 counterpart for
  `continous_rotation/`'s continuous-rotation acquisition (4×4 only — see
  above). Same physics as `DISCRETE/4x4/` (a verbatim copy of
  `mueller_forward_model.py`) and the same generalized least-squares
  reconstruction, since that fit already handles "however many
  (angle, intensity) samples a run has" with no assumption about a discrete
  grid — a continuous sweep's 360 non-grid samples need no separate
  Fourier/harmonic-analysis step. The one real difference is
  `image_loader.py`: it reads each frame's actual polled angle from
  `Logs/experiment_log.csv` (skipping any frame logged `FAILED`, or a
  `SUCCESS` row with no matching image file) rather than parsing it from
  the filename. See its `README.md` for the full comparison.

- **`tinghuye/`** — earlier from-scratch reconstruction scripts (3×3 and
  4×4, built from a fixed small angle set, plus a theory-vs-experiment
  comparison plot). Kept for reference; superseded in capability by
  `own_code/` (arbitrary image count, real rig integration, calibration
  hooks) but useful as a simpler standalone read of the same math.

- **`Mueller_calculation_36_images_method.py`** — the reference paper's
  original method: reconstructs a 4×4 Mueller matrix from exactly 36 images
  captured at the canonical H/V/P/M/R/L polarization-state combinations
  (a different acquisition pattern from what this rig's `discreate_angle`
  currently produces — see `own_code/4x4/README.md`'s discussion of why),
  plus polar decomposition into diattenuation, polarizance, and
  depolarization maps.

  Based on: S. Obando-Vasquez, A. Doblas, and C. Trujillo, *"Apparatus and
  method to estimate the Mueller matrix in bright-field microscopy,"*
  Applied Optics (2021).

### `Measuremt_ script/check_config_sync.py`

Standalone diagnostic (not called by either `01_main.py`) that diffs
`MOTOR_SN`/`ZERO_OFFSET` between the two acquisition folders' `config.py`
files — they're hand-duplicated by design (no shared code), so nothing else
catches them drifting apart after a recalibration or hardware swap in only
one file. Run `python check_config_sync.py` from inside `Measuremt_ script/`
after any hardware change.

## Getting started

1. Install Thorlabs Kinesis (64-bit) and the IDS Peak SDK on the lab PC.
2. Pick the acquisition folder matching your experiment (`discreate_angle`
   for 3×3/4×4-discrete, `continous_rotation` for 4×4-continuous) and follow
   its own `README.md` for setup, calibration, and run instructions.
3. Run `python 01_main.py` from inside that folder — always that file, never
   the other modules directly.
4. Use dry-run mode first to verify the full software pipeline without
   touching hardware.
5. Each acquisition folder has a `test_pure_functions.py` covering its
   hardware-independent logic — run `python -m unittest test_pure_functions
   -v` from inside it. A few ROI-selection tests need NumPy and are skipped
   without it (dry-run never exercises that code either).
6. Once you have captured data, run the matching reconstruction pipeline to
   get a Mueller matrix:
   - Discrete 3×3/4×4: copy that run's `Images/` folder and
     `Config/experiment_config.json` into a sample-labeled folder following
     `matrix/NAMING.md`, then run `matrix/own_code/DISCRETE/3x3/main.py` or
     `matrix/own_code/DISCRETE/4x4/main.py` (matching the mode you captured).
   - Continuous 4×4: point `matrix/own_code/CONTINOUS/4x4/main.py`'s
     `RUN_DIRECTORY` at the run folder directly (it reads `Images/`,
     `Logs/experiment_log.csv`, and `Config/experiment_config.json` from
     wherever that folder is — no renaming/copying step required).

   New to the physics behind any of this? Start with the "Physics
   background, from zero" section in
   `Measuremt_ script/discreate_angle/README.md` or
   `matrix/own_code/DISCRETE/3x3/README.md` — either builds the same ideas
   up from no prior assumed knowledge.
