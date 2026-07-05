# MMIE Control Software — 4x4 Continuous Rotation

Companion folder to `../discreate_angle/`, which covers 3×3 and 4×4 discrete
acquisition. This folder is **independent** — no shared imports, no shared
run data — because continuous rotation's hardware lifecycle overlaps with
discrete mode (same four motors, same camera) but its acquisition loop does
not: there is no discrete state list, no per-state filename, and no
resumable checkpoint (see `checkpoint_manager.py`).

This folder is **4×4 only** — a 3×3 continuous mode (dual rotating linear
polarizers, no QWPs) was considered and deliberately not built; the two
polarizers stay fixed and only the two QWPs ever rotate here. If that
changes, `ACTIVE_MOTORS`/`ROTATING_MOTORS` in `config.py` would need to
become mode-keyed dicts (mirroring `discreate_angle/config.py`'s
`ACTIVE_MOTORS`), and `capture_camera_references()`/parking would need a
3×3 branch — see the project history for the fuller discussion.

## Physics background, from zero

See `../discreate_angle/README.md`'s physics section for the fundamentals
(Stokes vectors, Mueller matrices, why a QWP is needed at all) — this
section only covers what's specific to *continuous* rotation.

`discreate_angle/` gets its many (generator angle, analyzer angle, image)
data points by *stopping* the QWPs at a grid of discrete angles and
capturing one image per stop. The **dual-rotating-retarder** technique
implemented here instead spins both QWPs continuously, at a fixed angular
*ratio* to each other (classically 1:5 — the analyzer QWP turns 5° for
every 1° the generator QWP turns), while the camera keeps capturing frames.
Because the two QWPs are never at a fixed relative angle for long, every
captured frame corresponds to a different, unrepeated combination of
generator/analyzer states, and — over one full revolution of the slower
QWP — the frames sweep through a dense, well-conditioned set of
polarization states without ever having to stop and settle the motors
between shots. The intensity recorded at each moment, as a function of the
rotating QWP's instantaneous angle, is a periodic waveform; the *classical*
solution (Azzam 1978) fits its Fourier coefficients, which map directly to
the unknown entries of the sample's Mueller matrix — the 1:5 ratio
specifically is what guarantees no two harmonics in that waveform collide
and become impossible to separate.

This project takes a different but equivalent route:
`matrix/own_code/CONTINOUS/4x4/` reuses the **exact same generalized
per-pixel least-squares fit** as the discrete pipeline
(`matrix/own_code/DISCRETE/4x4/solve_mueller.py`) rather than doing a
Fourier decomposition. That fit already handles however many
`(generator angle, analyzer angle, intensity)` rows a run has, with no
assumption about a discrete angle grid — so a continuous sweep's ~360
non-grid samples over one revolution slot into the identical linear system
a handful of discrete-angle images would build; more, well-distributed
samples just improve the fit's conditioning, the same way more discrete
images do. This is why the acquisition schemes stay independent (different
capture loops, different data shapes) while the reconstruction math itself
is shared in spirit, not duplicated by a different method.

Getting there requires images at *known* angles, which is why acquisition
uses **angle-triggered** capture rather than a fixed frame rate — see
"Capturing frames: angle-triggered, not frame-rate free-run" below.

## Testing

```powershell
python -m unittest test_pure_functions -v
```

Covers this folder's hardware-independent logic — deliberate duplicate in
spirit of `../discreate_angle/test_pure_functions.py`. A couple of
ROI-selection tests need NumPy and are skipped if it isn't installed; run
this on the lab PC to actually exercise them, since dry-run mode never does.

`../check_config_sync.py` diffs `MOTOR_SN`/`ZERO_OFFSET` between this
folder's `config.py` and `discreate_angle/config.py` (hand-duplicated by
design, so nothing else catches drift after a recalibration). Run it by
hand after any hardware change.

## Current status

The full pipeline is implemented and runs today, including in dry-run mode:

- Environment verification, hardware bring-up (discover → connect →
  initialize → enable → set velocity → home → optical zero) — once per
  session, same safety-confirmation gates as discrete mode.
- Camera Cockpit-guided exposure/frame-rate setup — once per session.
- A multi-sample loop: parking `PSG_Polarizer`/`PSA_Analyzer` at that
  sample's fixed optical angle, an automatic bright/dark reference pair
  (`PSA_Analyzer` moves briefly to fixed+90° for the dark shot, then back —
  this happens before rotation starts, so it doesn't conflict with the
  analyzer never moving *during* acquisition), a prompt to insert the
  sample, then the continuous-rotation acquisition itself — repeated for as
  many samples as you want, each in its own folder. See "Measuring multiple
  samples in one session" below.
- A confirmation that illumination is on, asked again right before each
  sample's automatic bright/dark capture (there's no physical shutter, so
  this catches the light having been switched off while swapping samples).
- Saving `Config/rotation_plan.json`, `Config/roi.json`, and
  `Config/experiment_config.json` per sample.
- **The acquisition loop itself**
  (`continuous_engine.ContinuousEngine.run_continuous()`): moves both QWPs
  to a known start angle, sets `PSA_QWP`'s spin velocity to
  `PSG_QWP`'s base rate times the chosen rotation ratio, starts continuous
  rotation, then captures a frame every time `PSG_QWP` crosses
  `capture_angle_step_deg` of additional travel (default 1°, 360
  frames/revolution) until one full revolution is covered. Each frame's
  *actual* polled angle (not the nominal threshold) is logged to
  `Logs/experiment_log.csv` and checkpointed. See "Capturing frames:
  angle-triggered, not frame-rate free-run" below for why this scheme was
  chosen.

Once you have a captured run, `matrix/own_code/CONTINOUS/4x4/` reconstructs
its Mueller matrix — see that folder's README.

## Measuring multiple samples in one session

Same model as `discreate_angle/`: hardware bring-up happens once, then you're
looped through as many samples as you want without restarting the script.
Each sample gets its own `Data/YYYY-MM-DD_<sample name>` folder and its own
`Logs/terminal_transcript.txt` (the one-time bring-up only appears in the
first sample's transcript). Before every sample after the first, you're
asked to confirm the previous sample has been removed. A real acquisition
failure (a motor or camera error) asks whether to skip that sample and
continue with the next one; an emergency stop/Ctrl-C always ends the whole
session instead.

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
`30`, `45`, or any arbitrary angle, and moving there (`motor angle =
(optical angle + ZERO_OFFSET["SAMPLE"]) modulo 360`). Verify that orientation with a
polarimeter, confirm, and the SAMPLE stage is disconnected again
immediately — set the mounted assembly aside while the rest of instrument
setup (bright/dark reference capture) runs with an empty beam, then
reinsert it (still at the angle you just set) at the usual "insert the
sample now" prompt right before continuous rotation starts.

Answering no (the default) skips this entirely. The chosen angle, if any,
is saved as `sample_stage_optical_angle` in `Config/experiment_config.json`.
Separate from `calibration.verify_with_reference_sample()`, which uses the
same `SAMPLE` motor but for a *known* reference optic during system
self-verification, not for orienting a real specimen.

## Rotation velocity

Every active motor's velocity/acceleration is set explicitly in software
(`MotorController.set_all_velocity()`, Kinesis `SetVelocityParams()`) once
per session, after `enable_all()` and before `home_all()` — **not** left at
whatever happened to already be stored on the device or its Kinesis
profile. You are asked for both numbers every session, pre-filled with
`config.py`'s defaults — press Enter to keep the default, or type a new
value to override it just for this run:

```text
Rotation velocity for all active motors (deg/s) [10]: 15
Rotation acceleration for all active motors (deg/s^2) [20]:
```

The example above types `15` for velocity and presses Enter (blank) to
accept the `20` default for acceleration. This applies uniformly to all
four motors, since no sample's rotation ratio is known yet at bring-up
time; the value you enter is written back into `TimingSettings
.base_angular_velocity_deg_s`, so it also becomes the base rate
`continuous_engine.py` uses for the actual spin — not just the initial
point-to-point moves. Once a sample's ratio is chosen and continuous
spinning starts, `continuous_engine.py` re-sets `PSA_QWP`'s velocity to
`base_angular_velocity_deg_s × ratio` — e.g. `10 × 5 = 50 deg/s` for a
`1:5` ratio — while `PSG_QWP` stays at the base rate. To change what's
pre-filled on every future run, edit `config.py`'s `TimingSettings
.base_angular_velocity_deg_s`/`rotation_accel_deg_s2`.

The motorized `SAMPLE` stage (above) is asked the same two prompts
separately during its own bring-up, since it's a different, single-axis
`MotorController` instance.

## Capturing frames: angle-triggered, not frame-rate free-run

Two schemes were considered:

- **Frame-rate free-run** — camera free-runs at a fixed fps; after each
  frame, poll both QWP encoders and log their angle against that frame.
- **Angle-triggered** (chosen) — poll `PSG_QWP` position in a loop and fire
  a software trigger every time it crosses a configured angular step
  (`TimingSettings.capture_angle_step_deg`).

Angle-triggered was chosen because the reconstruction side
(`matrix/own_code/CONTINOUS/4x4/`) needs images at *known* angles; this
guarantees that directly regardless of real hardware's velocity ripple
(acceleration jitter, encoder noise), whereas frame-rate free-run only
gives evenly-spaced angles if velocity is perfectly constant — and would
still need the actual encoder angle logged per frame to correct for when it
isn't. The trade-off is a lower achievable frame rate, bounded by the
poll-plus-software-trigger round trip; at the default 10°/s base velocity
and 1° capture step, that's only ~10 triggers/second, comfortably within a
software-triggered IDS camera's reach. If the motor ever spins faster than
the capture pipeline can keep up, `continuous_engine.py` degrades
gracefully — it just captures as fast as it can (each successive frame
further apart in real angle than the nominal step) rather than crashing or
missing the stop condition; see that module's docstring.

## Bright/dark reference ROI

The bright/dark ratio is not a whole-frame average — vignetting or edge
glare can shift that independent of actual polarization contrast. Instead,
`camera_controller.select_roi()` slides a window
(`CameraSettings.roi_window_size`, step `roi_stride`) across the bright
reference frame and picks the **flattest** region (lowest standard
deviation) among windows bright enough (`roi_min_mean`) and free of
saturated pixels — not just the brightest spot, so an uneven beam profile
doesn't win over a genuinely flat-illuminated area. The same region is then
reused on the dark frame and saved to `Config/roi.json`. Skipped entirely in
dry-run (no real pixels to analyze).

## Which file should I run?

```powershell
python 01_main.py
```

Same rule as `discreate_angle/`: run only `01_main.py`. There is no mode
choice here — this folder always runs 4x4 continuous. There is also no
`--resume`; an interrupted continuous run restarts its revolution from
scratch (or, in a multi-sample session, restarts just that sample —
earlier completed samples are unaffected).

## Files

| File | Purpose |
|---|---|
| `01_main.py` | Operator prompts and orchestration (run this file). |
| `config.py` | Motor identities, offsets, camera settings, timing, and the continuous-only velocity/tolerance settings. |
| `utils.py` | Environment checks, per-sample run-directory creation/renaming (`Data/YYYY-MM-DD_<sample name>`), JSON writing, rotation-ratio parsing. |
| `motor_controller.py` | Kinesis discovery/bring-up plus `set_velocity`/`start_continuous`/`stop_continuous` — the primitives the acquisition loop uses. |
| `camera_controller.py` | IDS Peak configuration, software-triggered (angle-triggered) acquisition, BMP save/verify. |
| `rotation_plan.py` | Serializes the chosen ratio and fixed angles to JSON. |
| `checkpoint_manager.py` | Records progress within a single (non-resumable) revolution. |
| `logger_manager.py` | Transcript, per-frame CSV logging, final report — continuous-shaped columns. |
| `continuous_engine.py` | **The acquisition loop**: angle-triggered capture over one PSG_QWP revolution. Read its docstring for the design rationale. |
| `calibration.py` | Ad-hoc zero-offset/verification helpers, plus `verify_with_reference_sample()` — moves a motorized `SAMPLE` stage (a known reference optic, not a real specimen) for system self-verification. Not called from `01_main.py`. |
| `test_pure_functions.py` | Automated tests for this folder's hardware-independent logic. Run with `python -m unittest test_pure_functions -v`. |

## Settings to verify before any real run

`config.py`'s `MOTOR_SN` and `ZERO_OFFSET` are duplicated by hand from
`../discreate_angle/config.py`, not imported — if you recalibrate a motor
or swap hardware, update both files (or run `../check_config_sync.py` to
check they still agree):

```python
MOTOR_SN = {
    "PSG_Polarizer": "...",
    "PSG_QWP": "...",
    "PSA_QWP": "...",
    "PSA_Analyzer": "...",
    # Only fill this in if you have a motorized SAMPLE stage (see
    # "Motorized SAMPLE stage" above). Leave "" if the sample is placed by hand.
    "SAMPLE": "",
}

ZERO_OFFSET = {
    "PSG_Polarizer": 0.0,
    "PSG_QWP": 0.0,
    "PSA_QWP": 0.0,
    "PSA_Analyzer": 0.0,
    "SAMPLE": 0.0,
}
```

The `"SAMPLE"` entry is for the optional motorized `SAMPLE` stage, used by
`calibration.verify_with_reference_sample()` (a known reference optic) and
by `01_main.setup_sample_stage()` (orienting a real specimen); it is not
part of `ACTIVE_MOTORS`.

`FALLBACK_SENSOR_WIDTH`/`HEIGHT` are dry-run-only placeholders — verify
against your camera's actual datasheet. `CameraController.frame_width`/
`frame_height` read the real values from the camera on non-dry-run runs.

`TimingSettings.capture_angle_step_deg` (default `1.0`, 360 frames per
revolution) controls the acquisition side's angle-triggered capture density
— see "Capturing frames" above. Lower it (e.g. `0.5`, 720 frames) for finer
angular sampling, or raise it if your camera/software-trigger round trip
can't sustain the resulting rate; `matrix/own_code/CONTINOUS/4x4/` works
with whatever count a run actually produced.
