# MMIE Control Software — Discrete Angle Acquisition

Python control software for a Mueller Matrix Imaging Ellipsometer using:

- Four Thorlabs K10CR2/M cage rotators
- One IDS U3-3890CP-M-GL camera
- Thorlabs Kinesis through `pythonnet`
- IDS Peak

This folder covers **3×3** and **4×4 discrete** (stepped-QWP) acquisition only.
4×4 continuous rotation is a separate, independent implementation in
`../continous_rotation/` — the two folders share no code or run data.

Capturing images is only half the job — turning them into a Mueller matrix
happens afterward, offline, in `matrix/own_code/3x3/` (or `4x4/`) at the
root of this repository. See "After you've captured images" below for the
one manual step that connects the two.

## Physics background, from zero

This section explains *why* the software asks you to rotate two (or four)
motors to a grid of angles and snap a picture at each one — read it if
you're operating this rig without necessarily having a polarimetry
background. The reconstruction side (`matrix/own_code/`) has the fuller
mathematical version of the same ideas — this is the operator-facing
summary of the same physics.

**Polarization** is the shape a light wave's oscillation traces as it
travels: a straight line at some angle (linear), a circle (circular,
spinning one way or the other), or something in between (elliptical).
Ordinary light is unpolarized — a fast random mix of every angle, averaging
to no preference at all. A **polarizer** only lets one linear angle through;
a **quarter-wave plate (QWP)** delays one axis of oscillation relative to
the perpendicular one, which is what turns linear polarization into
circular (and back).

A polarization state is written as four numbers, `(S0,S1,S2,S3)`, called a
**Stokes vector**: `S0` is plain brightness, `S1`/`S2` describe linear
polarization along two different reference angles, and `S3` describes
circular polarization. What a sample *does* to polarization — its complete
optical fingerprint — is a 4×4 (or, linear-only, 3×3) matrix, the
**Mueller matrix** `M`, such that `Stokes_out = M @ Stokes_in`.

The camera can only ever read plain brightness (`S0`) of the light hitting
it — it has no way to directly see `S1`, `S2`, or `S3`. So to pin down every
unknown entry of `M`, this rig:

1. **Generates** a series of *known* input polarization states before the
   sample, by rotating `PSG_Polarizer` (and, in 4×4 mode, `PSG_QWP`) to
   different angles.
2. **Analyzes** the light coming out, by rotating `PSA_Analyzer` (and,
   in 4×4 mode, `PSA_QWP`) to different angles after the sample.
3. Records one brightness value per (generator angle, analyzer angle)
   combination — that's one image, one filename, one equation relating the
   unknown entries of `M` to that one number.
4. Once enough combinations have been captured (at minimum 9 for 3×3, 16
   for 4×4 — more is better for precision, see the reconstruction README),
   there are enough equations to solve for every entry of `M`. That solving
   step is exactly what `matrix/own_code/` does with the images this folder
   produces.

**Why 3×3 only uses two motors (both polarizers), and 4×4 uses all four:**
a plain rotating polarizer can only ever generate or detect *linear*
polarization — no rotation angle of it produces or sees any `S3`
(circular) component, so a two-polarizer rig can only recover the
`S0,S1,S2` sub-block of `M` (a 3×3 matrix). Reaching every polarization
state — linear, circular, and elliptical — requires a QWP: fed light at
45° to its axis, the quarter-wavelength delay it introduces converts
straight-line oscillation into circular. That's why 4×4 mode holds the
polarizers at a **fixed** angle and instead rotates the **QWPs** — the
fixed polarizer sets one specific linear input, and rotating the QWP
relative to it sweeps through every other reachable state, which is what's
needed to solve for the full 16-entry Mueller matrix, including the
circular-polarization-coupled entries (`m03`, `m30`, `m33`, ...) that 3×3
mode cannot see at all.

## Testing

```powershell
python -m unittest test_pure_functions -v
```

Covers the hardware-independent logic (angle parsing/conversion, folder
naming/collision handling, state generation, checkpoint math, ROI
selection) that was previously only checked by manually running `01_main.py`
in dry-run mode. Three ROI-selection tests need NumPy and are skipped if
it isn't installed — run this on the lab PC (which already requires NumPy)
to actually exercise them; dry-run mode never does, since there are no real
pixels to select an ROI from.

`../check_config_sync.py` (one level up, shared with `continous_rotation/`)
is a separate, standalone script — not part of either `01_main.py` — that
diffs `MOTOR_SN`/`ZERO_OFFSET` between this folder's `config.py` and the
other's, since the two are hand-duplicated by design and nothing else
catches them drifting apart after a recalibration. Run it by hand after any
hardware change.

## Which file should I run?

Run only:

```powershell
python 01_main.py
```

`01_main.py` is the operator entry point. It automatically imports and calls the
other Python files in the correct order. Do **not** run `motor_controller.py`,
`camera_controller.py`, or the other modules individually.

The `MMIE_Control` directory is a separate notebook-based reference
implementation. It is not required when running `01_main.py`.

## First-time setup on the lab computer

### 1. Copy the complete project folder

Keep all these files together:

```text
01_main.py
config.py
utils.py
state_generator.py
motor_controller.py
camera_controller.py
measurement_engine.py
logger_manager.py
checkpoint_manager.py
calibration.py
README.md
```

### 2. Install the hardware software

Install:

1. Thorlabs Kinesis 64-bit
2. IDS Peak SDK
3. Python 3.11 or newer

Install the required Python packages:

```powershell
python -m pip install pythonnet numpy pandas opencv-python
```

Install the IDS Peak Python packages supplied or recommended by your IDS Peak
installation. Confirm that these imports work:

```python
from ids_peak import ids_peak
from ids_peak import ids_peak_ipl_extension
from ids_peak_ipl import ids_peak_ipl
```

### 3. Edit `config.py`

Enter the real serial number for each motor:

```python
MOTOR_SN = {
    "PSG_Polarizer": "...",
    "PSG_QWP": "...",
    "PSA_QWP": "...",
    "PSA_Analyzer": "...",
    # Only fill this in if you have a motorized SAMPLE stage (see "Motorized
    # SAMPLE stage" below). Leave "" if the sample is placed by hand.
    "SAMPLE": "",
}
```

Enter the measured motor position corresponding to optical zero:

```python
ZERO_OFFSET = {
    "PSG_Polarizer": 0.0,
    "PSG_QWP": 0.0,
    "PSA_QWP": 0.0,
    "PSA_Analyzer": 0.0,
    "SAMPLE": 0.0,
}
```

Check that `KINESIS_DIR` points to the Kinesis installation and that
`MOTOR_SETTINGS_NAME` matches the K10CR2 profile shown by Kinesis.

Do not perform a real measurement until the serial numbers and offsets have
been verified.

## Recommended first test

Run:

```powershell
python 01_main.py
```

Choose **dry-run mode** when prompted. Dry run:

- Does not load Kinesis or IDS Peak
- Does not connect, home, or move physical motors
- Does not trigger the physical camera
- Executes the real measurement workflow
- Generates synthetic BMP images
- Creates logs, checkpoints, configuration files, and a report

Use a small angle set such as:

```text
0,90
```

This produces four states when both PSG and PSA use the same two angles.

## Running a real experiment

Connect and power the required motors and camera, then run:

```powershell
python 01_main.py
```

The program performs the following sequence. Everything above the dashed
line happens **once** for the whole session; everything below it repeats
**per sample** — see "Measuring multiple samples in one session" below.

```text
Select 3×3 or 4×4 mode
        ↓
Verify software environment
        ↓
Detect the camera (fails fast, before any motor time is spent)
        ↓
Discover, connect, initialize, and enable the required motors
        ↓
Set rotation velocity/acceleration explicitly (not a device default), then home
        ↓
Move to optical-zero offsets
        ↓
Initialize and test the camera (Cockpit exposure/frame-rate selection)
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Enter operator, sample name, and comments (sample name -> run folder name)
        ↓
If a motorized SAMPLE stage is used: bring it up, set/verify its optical
angle with a polarimeter, then disconnect it and set it aside
        ↓
Enter optical-angle states
        ↓
Preview optical angles, motor angles, and total states
        ↓
Estimate required disk space
        ↓
Capture bright/dark reference (auto ROI) — repeated every sample
        ↓
Insert the sample, then run measurement states
        ↓
Rehome motors, write final checkpoint and report
        ↓
Ask: measure another sample, or done?
```

The software asks for confirmation before safety-sensitive initialization
stages.

## Measuring multiple samples in one session

`01_main.py` keeps the camera and motors connected for as many samples as
you want to measure — you don't need to restart the script between them.
After one sample's measurement completes, you're asked **"Measure another
sample?"**. Answering yes takes you back to "Enter operator, sample name,
and comments" for the next sample, reusing the same hardware bring-up
(mode, motor connections, camera exposure/frame-rate) from the start of the
session — only the sample-specific steps (angles, reference capture,
measurement) repeat.

Each sample gets its own `Data/YYYY-MM-DD_<sample name>` folder (see
"Output folders" below) and its own `Logs/terminal_transcript.txt` — the
one-time hardware bring-up only appears in the **first** sample's
transcript, since it only happened once.

Before every sample after the first, you're asked to confirm the
**previous** sample has been removed (the beam path must be empty for the
bright/dark reference check that follows).

If a sample's measurement fails for a real reason (a motor or camera
error — not an emergency stop, which always ends the whole session
immediately), you're asked whether to skip that sample and continue with
the next one, rather than losing the rest of the queue.

Mode (3×3 vs 4×4) is fixed for the whole session — switching between them
mid-session would change which motors are active, which really does
require a reconnect, so that stays a restart-the-script situation.

## Rotation velocity

Every active motor's velocity/acceleration is set explicitly in software
(`MotorController.set_all_velocity()`, Kinesis `SetVelocityParams()`) once
per session, after `enable_all()` and before `home_all()` — **not** left at
whatever happened to already be stored on the device or its Kinesis
profile. This is the same trapezoidal profile `MoveTo()` uses for every
point-to-point move (homing, optical-zero, every measurement state), so
every motor moves at a known, reproducible speed regardless of what was
last configured in the Kinesis application.

You are asked for both numbers every session, pre-filled with `config.py`'s
defaults — press Enter to keep the default, or type a new value to
override it just for this run (same pattern as the exposure/frame-rate
prompts in camera setup):

```text
Rotation velocity for all active motors (deg/s) [10]: 15
Rotation acceleration for all active motors (deg/s^2) [20]:
```

The example above types `15` for velocity and presses Enter (blank) to
accept the `20` default for acceleration. To change what's pre-filled on
every future run, edit `config.py`:

```python
TimingSettings.rotation_velocity_deg_s = 10.0   # deg/s, all active motors
TimingSettings.rotation_accel_deg_s2 = 20.0      # deg/s^2, all active motors
```

The motorized `SAMPLE` stage (below) is asked the same two prompts
separately during its own bring-up, since it's a different, single-axis
`MotorController` instance — its answers only apply to that stage, not the
other motors.

## Motorized SAMPLE stage (optional, per sample)

If your specimen is mounted on its own motorized rotation stage
(`config.MOTOR_SN["SAMPLE"]`/`ZERO_OFFSET["SAMPLE"]`), `01_main.py` asks
right after that sample's operator/sample/comments prompt:

```text
Do you have a motorized SAMPLE stage for this sample?
```

Answering yes runs the exact same bring-up sequence as the other motors —
discover → connect → initialize → enable → (ask + set velocity) → home →
move to optical zero — scoped to just the `SAMPLE` axis. Moving to optical
zero right after homing is a sanity checkpoint (confirms the configured
offset is loading correctly) before asking for the real target angle, e.g.
`30`, `45`, or any arbitrary angle, and moving there:

```text
motor angle = (sample optical angle + ZERO_OFFSET["SAMPLE"]) modulo 360
```

Verify that orientation with a polarimeter, confirm, and the SAMPLE stage
is disconnected again immediately. Physically lift the mounted assembly out
of the beam path and set it aside — the rest of instrument setup below
(bright/dark reference capture) needs an empty beam. The sample is then
reinserted (still fixed at the angle you just set) at the usual "insert the
sample now" prompt, right before acquisition starts.

Answering no (the default) skips this entirely — nothing else about the
flow changes for a sample placed by hand. The chosen angle, if any, is
saved as `sample_stage_optical_angle` in `Config/experiment_config.json`.

This is separate from `calibration.verify_with_reference_sample()`, which
uses the same `SAMPLE` motor but for a *known* reference optic during
system self-verification, not for orienting a real specimen.

## Camera preparation before every experiment

The camera is probed for its model and serial number **before any motor is
discovered, connected, or homed** — if no camera is found, the run aborts
immediately, so a missing/misconfigured camera never costs you the time of
homing motors first. Everything else below happens after the motors reach
optical zero:

1. It probes the IDS camera and prints its model and serial number (this
   happens up front, before motor initialization — see above).
2. Once the motors are at optical zero, it asks you to confirm the
   illumination/light source is turned on, before any Cockpit check.
3. At PSG `0°`, PSA `0°`, it asks you to open IDS Peak Cockpit and confirm the
   bright state.
4. You must close Cockpit and confirm that it is closed.
5. The software moves the PSA analyzer to optical `90°`.
6. It asks you to open Cockpit and confirm that the image is darker.
7. You close Cockpit, and the analyzer returns to optical `0°`.
8. At `0_0`, use Cockpit to select exposure time and frame rate.
9. Close Cockpit and enter those two values in the Python terminal.
10. Python applies the values through the IDS API and reads them back. If the
    camera rejects either value, it prints the error and asks for both
    values again — see "If the exposure time or frame rate..." below.
11. The requested and actual camera values are printed and saved.
12. Python asks you to confirm the illumination is still ON — there is no
    physical shutter, so this catches the light having been switched off
    during the Cockpit checks above, before the automatic captures below.
13. Python captures automatic `0_0` bright and `0_90` dark references.
    Minimum, maximum, mean, and pixels equal to 255 are reported for the
    whole frame, but the bright/dark **ratio** is computed only over an
    automatically-selected ROI — see "Bright/dark reference ROI" below.
14. You are asked to insert the sample now (everything above runs with an
    empty beam path, since a sample would distort the reference checks).
15. A final confirmation is required before measurement begins.

### Bright/dark reference ROI

The bright/dark ratio check does not average the whole frame — vignetting
or edge glare can shift a whole-frame mean independent of actual
polarization contrast. Instead, `camera_controller.select_roi()` slides a
window (`CameraSettings.roi_window_size`, step `roi_stride`) across the
bright reference frame and picks the **flattest** region (lowest standard
deviation) among windows that are bright enough (`roi_min_mean`) and free of
saturated pixels — deliberately not just the brightest spot, so an uneven
(e.g. Gaussian) beam profile doesn't win over a genuinely flat-illuminated
area. That same region is then reused on the dark frame so both means come
from identical pixels. The chosen region is saved to `Config/roi.json`.

Do not leave IDS Peak Cockpit open while Python is acquiring images. Cockpit
and Python can compete for control of the same camera.

If the exposure time or frame rate you enter is outside what the camera
actually supports at that setting (for example, the IDS U3-3890's minimum
frame rate is roughly 1.6 fps — entering something lower, or swapping the
exposure and frame-rate values by mistake, will be rejected), the software
prints the camera's error and asks for both values again. It does **not**
abort the run or require redoing motor homing/connecting — only the
exposure/frame-rate step is retried, on the same already-open camera
connection.

The runtime prompts use:

```text
Exposure time selected in IDS Peak Cockpit (ms)
Frame rate selected in IDS Peak Cockpit (fps)
```

Images are saved without automatic exposure, intensity rescaling, dark
subtraction, or saturation correction. In Mono8, a pixel value of 255 indicates
the top of the representable range. The software reports such pixels but does
not modify them.

## Mode behavior

### 3×3 mode

Only these motors are used:

- `PSG_Polarizer`
- `PSA_Analyzer`

The QWP motors are not connected, initialized, homed, or moved.

### 4×4 discrete mode

All four motors are initialized. The polarizers are placed at fixed optical
angles, while the two QWPs step through the requested angle combinations.

4×4 **continuous** rotation is not handled by this folder at all — see
`../continous_rotation/README.md`.

## Entering angles

Full-circle step syntax:

```text
360/10
```

This generates:

```text
0, 10, 20, ..., 350
```

The equivalent 360° state is not included.

Manual syntax:

```text
0,30,60,90,120,150
```

All entered angles are optical angles. The motor command is calculated as:

```text
motor angle = (optical angle + zero offset) modulo 360
```

Image filenames contain optical angles, not motor positions.

## Measurement loop

For each state, the software:

1. Commands each required motor.
2. Waits for motion to finish.
3. Compares commanded and motor-reported positions.
4. Retries a failed move up to two times.
5. Waits for mechanical settling.
6. Sends a software trigger to the camera.
7. Waits for image acquisition.
8. Retries failed acquisition up to two times.
9. Saves and decodes the BMP to verify it.
10. Writes the CSV log.
11. Updates the checkpoint only after success.
12. Continues to the next state.

The default retry delay and position tolerance can be changed in `config.py`.

## Emergency stop

Press:

```text
Ctrl-C
```

The program requests an immediate motor stop, stops camera acquisition,
preserves the last successful checkpoint, and disconnects the devices.

Keep the physical hardware emergency-stop or power-isolation method accessible.
Software stopping is not a substitute for laboratory hardware safety controls.

## Output folders

Each sample creates its own folder, named after the sample name you typed
in (sanitized for the filesystem; a repeated sample name gets a `_02`,
`_03`, ... suffix rather than overwriting the first):

```text
Data/
└── YYYY-MM-DD_<sample name>/
    ├── Images/
    ├── Logs/
    ├── Config/
    ├── DarkFrames/
    ├── Reports/
    ├── Checkpoints/
    └── Results/
```

Important files:

- `Images/*.bmp` — captured images
- `Logs/experiment_log.csv` — commanded and reported positions and status
- `Logs/terminal_transcript.txt` — terminal output, prompts, and operator answers
- `Logs/error_traceback.txt` — full technical traceback when an error occurs
- `Config/experiment_config.json` — complete saved experiment configuration
- `Config/roi.json` — the auto-selected bright/dark reference ROI (x, y, width, height)
- `Checkpoints/checkpoint.json` — last successfully completed state
- `Reports/ExperimentReport.txt` — final experiment summary
- `Results/BrightReference_0_0.bmp` — pre-measurement bright reference
- `Results/DarkReference_0_90.bmp` — pre-measurement dark reference

The transcript is flushed continuously so it remains useful after most crashes.
It includes environment results, confirmations, device identities, requested
and applied camera settings, image statistics, warnings, and retry messages.

## After you've captured images: getting a Mueller matrix out of them

This folder only captures and saves images — it does not compute a Mueller
matrix. That happens afterward, offline, in `matrix/own_code/3x3/` or
`matrix/own_code/4x4/` at the repository root (pick the one matching the
mode you ran).

The one manual step in between: copy that run's `Images/` folder and
`Config/experiment_config.json` (the reconstruction code needs nothing
else — `Logs/`, `Checkpoints/`, `Reports/` are for your own records) into a
sample-labeled folder following the one naming rule in `matrix/NAMING.md`,
e.g. `G:\control\Data\<date>\<sample-type>\<sample name>\`. This run
folder's own name (`Data/YYYY-MM-DD_<sample name>`, see "Output folders"
above) already carries the sample name — you're reorganizing it by
date/type, not renaming it from scratch. If you deliberately captured the
same sample multiple times to average out error, see
`matrix/own_code/<mode>/average_rounds.py` and `matrix/NAMING.md`'s
`_round<NN>` suffix, rather than reusing this folder's own auto `_02`/`_03`
disambiguation suffix (that suffix just avoids overwriting a same-named
folder — it isn't the multi-round convention the analysis side expects).

## Resuming an interrupted experiment

Use the directory of the interrupted run:

```powershell
python 01_main.py --resume "Data\YYYY-MM-DD_<sample name>"
```

`--resume` recovers exactly that one sample — it does not enter the
multi-sample loop described above. Once it finishes, run `python 01_main.py`
again (without `--resume`) to measure additional samples.

The saved configuration is loaded, and acquisition continues after the last
successful checkpoint. Do not manually alter images or the checkpoint before
resuming.

## What each Python module does

| File | Purpose | Run directly? |
|---|---|---|
| `01_main.py` | Operator prompts and complete experiment orchestration | **Yes** |
| `config.py` | Motor identities, offsets, camera settings, and timing | No |
| `utils.py` | Environment checks, angle parsing, paths, and JSON writing | No |
| `state_generator.py` | Generates 3×3 and 4×4 optical states | No |
| `motor_controller.py` | Kinesis discovery, initialization, motion, and stopping | No |
| `camera_controller.py` | IDS configuration, triggering, saving, and verification | No |
| `measurement_engine.py` | Ordered measurement loop and error handling | No |
| `logger_manager.py` | CSV logging and final report generation | No |
| `checkpoint_manager.py` | Atomic crash-recovery checkpoints | No |
| `calibration.py` | Optical-zero and verification-scan utilities | No |
| `test_pure_functions.py` | Automated tests for the hardware-independent logic above (`python -m unittest test_pure_functions -v`) | No — run via `unittest`, see "Testing" above |

Every function below also has a matching explanatory comment directly above
it in the source file — read this table alongside the code with `#`
comments open to cross-check both at once.

## Settings that need to change for an experiment

### One-time, per lab computer / per hardware setup (edit `config.py`)

| Setting | File / location | What it controls |
|---|---|---|
| `MOTOR_SN` | `config.py` | USB serial number of each K10CR2/M rotator, plus a `"SAMPLE"` entry for the optional motorized sample stage (not part of any experiment's `ACTIVE_MOTORS`) — used both by `calibration.verify_with_reference_sample()` (a known reference optic) and by `01_main.setup_sample_stage()` (a real specimen's orientation). Must match the physical device for that axis or `MotorController.discover()` raises `MotorError`. Leave blank if no SAMPLE stage exists. |
| `ZERO_OFFSET` | `config.py` | Motor angle that equals optical zero for each axis, found with `calibration.py`. Wrong values silently rotate every measurement by a constant offset. |
| `KINESIS_DIR` | `config.py` | Path to the Thorlabs Kinesis install. Checked by `utils.check_environment()` and used by `motor_controller._load_kinesis()`. |
| `MOTOR_SETTINGS_NAME` | `config.py` | Must match the K10CR2 device-settings profile name shown in Kinesis. |
| `CameraSettings.mean_too_dark` / `mean_too_bright` | `config.py` | Image-quality warning thresholds used in `camera_controller.save_bmp()`. Advisory only — does not block a run. |
| `FALLBACK_SENSOR_WIDTH` / `FALLBACK_SENSOR_HEIGHT` | `config.py` | Dry-run-only frame size for the disk-space estimate — verify against your camera's actual datasheet/reported dimensions. Real runs read the camera's own `Width`/`Height` instead (`CameraController.frame_width`/`frame_height`), so this constant can't silently misestimate a real run. |
| `TimingSettings.position_tolerance_deg` | `config.py` | Maximum allowed motor position error before a move is retried/failed. |
| `TimingSettings.rotation_velocity_deg_s` / `rotation_accel_deg_s2` | `config.py` | Explicit velocity/acceleration applied to every active motor (`MotorController.set_all_velocity()`) — see "Rotation velocity" above. |
| `TimingSettings.*_s` delays, `motor_max_retries`, `CameraSettings.max_retries` | `config.py` | Retry counts and settle/backoff delays; tune for your hardware's noise and speed. |

### Every experiment (answered as prompts by `01_main.py`, not edited in code)

| Prompt | Asked by | Stored in |
|---|---|---|
| 3×3 vs 4×4 mode | `choose_mode_first()` (once per session) | `ExperimentConfig.mode` |
| Dry-run vs real | `run_fresh_session()` (`utils.yes_no`, once per session) | `ExperimentConfig.dry_run` |
| Operator / sample / comments | `ask_metadata()` (per sample) | `ExperimentConfig.metadata` |
| PSG/PSA (or QWP) angle lists | `ask_angles_for_mode()` (per sample) | `ExperimentConfig.state_inputs` |
| Fixed polarizer angles (4×4 only) | `ask_float()` in `ask_angles_for_mode()` | `ExperimentConfig.fixed_angles` |
| Exposure time (ms) / frame rate (fps) | `guided_camera_setup()` (`ask_positive_float`) | `ExperimentConfig.camera.exposure_us` / `frame_rate_fps` |
| Every safety confirmation (`confirm_stage`) | throughout `01_main.py` | not stored — answering "no" cancels that stage |

Everything in the second table is designed to be changed per run through the
terminal prompts, not by editing source files. Only the first table
(`config.py`) should normally need source edits, and only when the physical
hardware setup itself changes (different rotor swapped in, re-calibrated
zero, new lab PC, etc.).

## Function-by-function reference

### `01_main.py` — operator prompts and orchestration (run this file)

| Function | What it does |
|---|---|
| `ask_choice` / `ask_float` / `ask_positive_float` / `ask_angles` | Loop-until-valid input helpers for a choice set, a plain float, a positive float (camera exposure/frame rate), and an angle spec (`utils.parse_angle_spec`). |
| `choose_mode_first` | First prompt of every fresh session: 3×3 vs 4×4. Fixes which motors are active for the whole session (all samples). |
| `print_environment_report` | Runs `utils.check_environment()`, prints OK/MISSING per check, returns whether all passed. |
| `ask_metadata` | Asks operator/sample/comments for one sample. The sample name doubles as that sample's run-folder name. |
| `setup_sample_stage` | Optional per-sample step, asked right after `ask_metadata`: if the operator has a motorized `SAMPLE` stage, brings it up (discover → connect → initialize → enable → home), asks the target optical angle, moves there, then disconnects it again for the empty-beam camera reference capture. Returns the chosen angle (saved as `ExperimentConfig.sample_stage_optical_angle`) or `None`. |
| `ask_angles_for_mode` | Asks the mode-specific angle prompts and builds that sample's `ExperimentConfig` pieces (`fixed_angles`, `state_inputs`) and `MeasurementState` list via `state_generator`. |
| `states_from_config` | Rebuilds the identical `MeasurementState` list from a saved config, for `--resume`, without re-asking the operator. |
| `confirm_stage` | Yes/no gate before a safety-sensitive step; "no" cancels the whole session. |
| `detect_camera` | Probes the camera (`camera.discover()`) and confirms it, before any motor step — so a missing camera aborts before motor time is spent homing. |
| `initialize_motors` | Runs discover → connect → initialize → enable → set velocity → home → move-to-optical-zero, each behind a `confirm_stage`. Runs once per session. |
| `move_analyzer_to_optical` | Moves `PSA_Analyzer` to a given optical angle (used by camera checks/references, not the main measurement loop). |
| `guided_camera_setup` | Confirms the light source is on, then walks the operator through the IDS Peak Cockpit bright/dark/exposure checks and records the chosen exposure/frame rate. Runs once per session. |
| `capture_camera_references` | Re-confirms illumination is on, captures/verifies the `BrightReference_0_0.bmp` / `DarkReference_0_90.bmp` images, selects the bright/dark ROI, and checks bright > dark with no saturation. Runs once **per sample**. |
| `check_disk_space` | Prints estimated-vs-free image space for one sample's state count; run once per sample, since earlier samples in the same session consume space too. |
| `write_error_traceback` | Saves the current exception's full traceback to `Logs/error_traceback.txt` (in the CURRENT sample's folder). |
| `ask_camera_settings` (nested) | The `ask_settings` callback passed to `camera.initialize()`; re-prompts for exposure/frame rate when the camera rejects them. |
| `run_resumed_session` | Recovers exactly the one `--resume`d sample; no multi-sample loop. Structurally the old single-sample flow. |
| `run_fresh_session` | The multi-sample session: mode → environment → hardware bring-up once, then loops asking metadata → angles → disk check → reference capture → measurement → rehome → "another sample?" per sample. Owns its own `SessionTranscript` lifecycle (a new transcript per sample folder) and only disconnects the camera/motors for real when the operator is done with all samples. Returns the process exit code. |
| `main` | Entry point: dispatches to `run_resumed_session` (`--resume`) or creates a "pending" placeholder folder and calls `run_fresh_session`. |

### `config.py` — settings and data models (not run directly)

| Item | What it does |
|---|---|
| `PROJECT_ROOT`, `DATA_ROOT` | Anchor paths; `DATA_ROOT` is where `Data/YYYY-MM-DD_<sample name>` folders are created. |
| `MOTOR_SN`, `ZERO_OFFSET`, `KINESIS_DIR`, `REQUIRED_KINESIS_DLLS`, `MOTOR_SETTINGS_NAME` | Hardware identity/calibration constants — see "Settings that need to change" above. |
| `CameraSettings` | Dataclass of requested + applied camera values (exposure, frame rate, gain, retry/timeout, warning thresholds). |
| `TimingSettings` | Dataclass of every delay, retry count, and position tolerance used around motor/camera operations. |
| `ExperimentMetadata` | Operator/sample/comments text. |
| `ExperimentConfig` | The full serializable snapshot of one run (mode, metadata, angles, camera/timing settings); `to_dict()`/`from_dict()` make `Config/experiment_config.json` and `--resume` possible. |
| `ACTIVE_MOTORS` | Which motor names are active for `"3x3"` vs `"4x4"` mode. |

### `utils.py` — shared helpers (not run directly)

| Function | What it does |
|---|---|
| `optical_to_motor` | Core calibration formula: `motor = (optical + zero_offset) % 360`. |
| `format_angle` | Turns an angle into a clean, filesystem-safe label for filenames. |
| `parse_angle_spec` | Parses `"360/step"` or `"a,b,c"` angle text into a validated, duplicate-free list. |
| `sanitize_folder_name` | Makes a sample name safe as a Windows/POSIX folder-name component. |
| `create_run_directory` | Creates a fresh `Data/YYYY-MM-DD_<name>` folder tree (collision-avoided) with its seven subfolders. |
| `rename_run_directory` | Renames an existing run folder to `Data/YYYY-MM-DD_<name>` in place — used once, for the first sample of a session, after a "pending" placeholder is created. |
| `write_json` | Atomic JSON write (write to `.tmp`, then rename) so crashes can't leave a half-written file. |
| `check_environment` | Import/filesystem-only diagnostic checks (Python version, packages, Kinesis DLLs, disk space). |
| `estimate_disk_bytes` | BMP size estimate used for the pre-run disk-space check. Takes width/height as required arguments — see `config.FALLBACK_SENSOR_WIDTH`/`HEIGHT` and `CameraController.frame_width`/`frame_height` for where callers get them from. |
| `yes_no` | Y/n prompt helper used throughout `01_main.py`. |
| `print_angles` | Prints an angle list next to its motor-angle equivalent, for operator sanity-checking. |

### `state_generator.py` — builds the measurement plan (not run directly)

| Item | What it does |
|---|---|
| `MeasurementState` | One planned move+capture step: index, optical angles, motor angles, output filename. |
| `generate_3x3` | Cartesian product of PSG_Polarizer × PSA_Analyzer angles. |
| `generate_4x4_discrete` | Cartesian product of PSG_QWP × PSA_QWP angles, polarizers held fixed. |

### `motor_controller.py` — Kinesis motor control (not run directly)

| Function | What it does |
|---|---|
| `angular_error_deg` | Shortest angular distance between two wrapped (0–360°) angles. |
| `MotorController.discover` | Lists USB motors and verifies every active motor's `MOTOR_SN` is configured and present. |
| `MotorController.connect_all` / `initialize_all` / `enable_all` / `home_all` | Sequential Kinesis bring-up: connect, load settings profile, enable, home — one motor at a time, never in parallel. |
| `MotorController.set_velocity` / `set_all_velocity` | Sets one (or every active) motor's velocity/acceleration explicitly in software (Kinesis `SetVelocityParams`) — see "Rotation velocity" above. |
| `MotorController.move_to_optical_zero_all` | Moves every active motor to its `ZERO_OFFSET`. |
| `MotorController.move_motor_angle` | Moves one axis and verifies the encoder position is within `position_tolerance_deg`, retrying on failure. The core move primitive everything else calls. |
| `MotorController.move_state` | Moves every axis needed for one `MeasurementState`, in a fixed order. |
| `MotorController.encoder_positions` | Reads back current reported positions for all connected motors. |
| `MotorController.emergency_stop` / `close` | Immediate stop (Ctrl-C path) and orderly shutdown (always runs via `finally`). |

### `camera_controller.py` — IDS Peak camera control (not run directly)

| Function | What it does |
|---|---|
| `CameraController.discover` | Briefly opens the camera to read its model/serial, then releases it so Cockpit can be opened. |
| `CameraController.initialize` | Opens the device/data stream once, then applies exposure/gain/frame-rate/pixel-format and starts acquisition. Accepts an optional `ask_settings` callback that re-prompts for exposure/frame rate (without reopening the device) if the camera rejects them — see `CameraSettingsError` below. |
| `CameraController._apply_acquisition_settings` | The retryable part of `initialize()`: applies pixel format/exposure/gain/frame rate and reads back what the camera actually accepted. Raises `CameraSettingsError` if exposure or frame rate is rejected. |
| `CameraController._start_streaming` | The non-retryable part of `initialize()`: switches to software trigger, allocates buffers, and starts acquisition. Only runs once settings are accepted. |
| `CameraController.acquire` | Fires a software trigger and returns one Mono8 frame as a NumPy array. |
| `CameraController.save_bmp` | Writes the frame to disk and computes/prints min/max/mean/saturated-pixel statistics (no correction is ever applied to the pixels). |
| `CameraController.verify_image` | Confirms the saved file is a real, decodable image. |
| `CameraController.acquire_save_verify` | Combines acquire → save → verify with retries; the single entry point every image capture uses. |
| `CameraController.test_frame` | Used for the bright/dark reference shots. |
| `CameraController.close` / `emergency_stop` | Orderly shutdown and Ctrl-C-path immediate stop. Both are best-effort and never raise; they skip the Acquisition-Stop calls entirely if acquisition was never actually started (e.g. `initialize()` failed before `_start_streaming()` ran), since the SDK errors on stopping a stream that was never started. |
| `CameraSettingsError` | Subclass of `CameraError` raised only for a rejected exposure/frame-rate value, so `01_main.py` can retry instead of aborting the whole run. |

### `measurement_engine.py` — the measurement loop (not run directly)

| Item | What it does |
|---|---|
| `EmergencyStopRequested` | Raised when the operator's Ctrl-C stop event is detected mid-run. |
| `MeasurementEngine.run_discrete` | For each `MeasurementState` (skipping already-checkpointed ones): move → settle → trigger/save/verify → log → checkpoint → settle. Writes the final report on the way out regardless of outcome. |

### `logger_manager.py` — logging and reporting (not run directly)

| Item | What it does |
|---|---|
| `SessionTranscript` | Tees stdout/stderr and `input()` prompts/answers into `Logs/terminal_transcript.txt` for the whole session. |
| `ExperimentLogger` | Appends one CSV row per state to `Logs/experiment_log.csv` (commanded vs. reported positions, attempt count, status). |
| `write_report` | Writes the final human-readable `Reports/ExperimentReport.txt` summary. |

### `checkpoint_manager.py` — crash recovery (not run directly)

| Function | What it does |
|---|---|
| `CheckpointManager.load` / `next_index` | Reads the last completed state index (or "nothing done yet"). |
| `CheckpointManager.update` | Records a state as completed, only after its image is verified and logged. |
| `CheckpointManager.complete` | Marks the whole run as finished. |

### `calibration.py` — manual calibration helpers (not run directly, not called by `01_main.py`)

| Function | What it does |
|---|---|
| `move_to_calibration_zero` | Moves one motor to a candidate optical-zero angle so the operator can visually confirm it, before hand-copying the value into `config.ZERO_OFFSET`. |
| `verification_scan` | Sweeps a motor across a list of optical angles and records commanded-vs-encoder pairs, to check calibration accuracy across the full range. |
| `verify_with_reference_sample` | Moves the motorized `SAMPLE` stage (a known reference optic, e.g. a linear polarizer at a documented angle — not a normal experiment's specimen) to a target optical angle, for validating a measured Mueller matrix against the reference optic's known theoretical one. |

## Before collecting research data

Verify all of the following:

- Correct serial number is assigned to every optical component.
- Every optical-zero offset has been measured experimentally.
- Motor direction and angle wrapping are correct.
- Reported motor positions remain within the configured tolerance.
- Exposure and gain do not produce black or saturated images.
- The test-frame image has the expected orientation and dimensions.
- Available disk space is sufficient.
- A small real-hardware scan completes successfully before a full scan.
