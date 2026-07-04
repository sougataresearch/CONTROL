# CONTROL

Control and analysis software for a Mueller Matrix Imaging Ellipsometer (MMIE):
four Thorlabs K10CR2/M rotation mounts (two polarizers, two quarter-wave
plates) driven through Thorlabs Kinesis, and one IDS U3-3890CP-M-GL camera
driven through the IDS Peak SDK.

## Repository layout

```
control/
├── Measuremt_ script/
│   ├── discreate_angle/     ← 3x3 and 4x4 discrete acquisition (production)
│   ├── continous_rotation/  ← 4x4 continuous rotation (independent, WIP)
│   └── MMIE_Control/        ← earlier notebook-based reference implementation
└── matrix/                  ← offline Mueller matrix reconstruction from saved images
```

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
behavior described above. Hardware bring-up, camera setup, per-sample
bright/dark reference + ROI, and plan/config persistence are implemented
and runnable today (including dry-run); the acquisition loop itself
(`continuous_engine.py`) is an intentional `NotImplementedError` stub until
the frame-rate-free-run vs. angle-triggered capture decision is made — see
that module's docstring for the two options. Hitting that stub ends the
whole session immediately rather than offering "another sample?", since
the same error would recur for every sample.

### `Measuremt_ script/MMIE_Control/`

An earlier, notebook-driven (`NB0`–`NB4`) reference implementation of the
same hardware control, kept for comparison. Not required to run either
folder above.

### `matrix/`

`Mueller_calculation_36_images_method.py` — offline reconstruction of a
sample's 4×4 Mueller matrix from a set of 36 saved intensity images (the
canonical H/V/P/M/R/L polarization-state combinations), plus polar
decomposition into diattenuation, polarizance, and depolarization maps.
Standalone from the acquisition code above; point it at a folder of
previously captured images.

Based on: S. Obando-Vasquez, A. Doblas, and C. Trujillo, *"Apparatus and
method to estimate the Mueller matrix in bright-field microscopy,"* Applied
Optics (2021).

## Getting started

1. Install Thorlabs Kinesis (64-bit) and the IDS Peak SDK on the lab PC.
2. Pick the acquisition folder matching your experiment (`discreate_angle`
   for 3×3/4×4-discrete, `continous_rotation` for 4×4-continuous) and follow
   its own `README.md` for setup, calibration, and run instructions.
3. Run `python 01_main.py` from inside that folder — always that file, never
   the other modules directly.
4. Use dry-run mode first to verify the full software pipeline without
   touching hardware.
