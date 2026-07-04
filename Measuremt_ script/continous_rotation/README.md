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
rotating QWP's instantaneous angle, is a periodic waveform whose Fourier
coefficients directly correspond to the unknown entries of the sample's
Mueller matrix (the classical Fourier-analysis solution to this problem,
rather than the discrete per-image linear system used in
`matrix/own_code/`) — the 1:5 ratio specifically is what guarantees no two
harmonics in that waveform collide and become impossible to separate.

This is why continuous rotation isn't just "discrete mode but faster": the
underlying math needed to turn captured frames into a Mueller matrix
(Fourier analysis of a continuous waveform) is genuinely different from the
per-image linear-system solve in `matrix/own_code/`, which is why the two
acquisition schemes are independent and why no reconstruction code for
continuous data exists yet — see "Current status" below for what's actually
implemented today.

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

Everything up to the acquisition loop itself is implemented and runs today,
including in dry-run mode:

- Environment verification, hardware bring-up (discover → connect →
  initialize → enable → home → optical zero) — once per session, same
  safety-confirmation gates as discrete mode.
- Camera Cockpit-guided exposure/frame-rate setup — once per session.
- A multi-sample loop: parking `PSG_Polarizer`/`PSA_Analyzer` at that
  sample's fixed optical angle, an automatic bright/dark reference pair
  (`PSA_Analyzer` moves briefly to fixed+90° for the dark shot, then back —
  this happens before rotation starts, so it doesn't conflict with the
  analyzer never moving *during* acquisition), a prompt to insert the
  sample, then (once built) the acquisition itself — repeated for as many
  samples as you want, each in its own folder. See "Measuring multiple
  samples in one session" below.
- A confirmation that illumination is on, asked again right before each
  sample's automatic bright/dark capture (there's no physical shutter, so
  this catches the light having been switched off while swapping samples).
- Saving `Config/rotation_plan.json`, `Config/roi.json`, and
  `Config/experiment_config.json` per sample.

**Not implemented**: the actual continuous-rotation acquisition loop
(`continuous_engine.ContinuousEngine.run_continuous()`). Running
`01_main.py` today gets all the way through camera verification for the
first sample and then stops the whole session with a clear
`NotImplementedError` instead of pretending to spin the QWPs or capture
frames — this ends the session rather than offering "another sample?",
since the same error would just recur for every sample.

## Measuring multiple samples in one session

Same model as `discreate_angle/`: hardware bring-up happens once, then you're
looped through as many samples as you want without restarting the script.
Each sample gets its own `Data/YYYY-MM-DD_<sample name>` folder and its own
`Logs/terminal_transcript.txt` (the one-time bring-up only appears in the
first sample's transcript). Before every sample after the first, you're
asked to confirm the previous sample has been removed. A real acquisition
failure (once the engine exists) asks whether to skip that sample and
continue with the next one; a `NotImplementedError` or emergency
stop/Ctrl-C always ends the whole session instead.

## The one open decision blocking the acquisition loop

Pick one before implementing `continuous_engine.py`:

- **Frame-rate free-run** — camera free-runs at a fixed fps; after each
  frame, poll both QWP encoders and log their angle against that frame.
- **Angle-triggered** — poll QWP position in a tight loop and fire a
  software trigger every time it crosses a configured angular threshold.

See `continuous_engine.py`'s module docstring for the trade-offs and an
implementation sketch for each option.

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
| `motor_controller.py` | Kinesis discovery/bring-up plus `set_velocity`/`start_continuous`/`stop_continuous` — the primitives the future engine needs. |
| `camera_controller.py` | IDS Peak configuration, software-triggered acquisition, BMP save/verify. |
| `rotation_plan.py` | Serializes the chosen ratio and fixed angles to JSON. |
| `checkpoint_manager.py` | Records progress within a single (non-resumable) revolution. |
| `logger_manager.py` | Transcript, per-frame CSV logging, final report — continuous-shaped columns. |
| `continuous_engine.py` | **The unimplemented acquisition loop.** Read its docstring first. |
| `calibration.py` | Ad-hoc zero-offset/verification helpers, plus `verify_with_reference_sample()` — moves a motorized `SAMPLE` stage (a known reference optic, not a real specimen) for system self-verification. Not called from `01_main.py`. |
| `test_pure_functions.py` | Automated tests for this folder's hardware-independent logic. Run with `python -m unittest test_pure_functions -v`. |

## Settings to verify before any real run

`config.py`'s `MOTOR_SN` and `ZERO_OFFSET` are duplicated by hand from
`../discreate_angle/config.py`, not imported — if you recalibrate a motor
or swap hardware, update both files (or run `../check_config_sync.py` to
check they still agree). Both also carry a `"SAMPLE"` entry for
the optional motorized reference-optic stage used only by
`calibration.verify_with_reference_sample()`; it is not part of
`ACTIVE_MOTORS` and is never touched during a normal run.

`FALLBACK_SENSOR_WIDTH`/`HEIGHT` are dry-run-only placeholders — verify
against your camera's actual datasheet. `CameraController.frame_width`/
`frame_height` read the real values from the camera on non-dry-run runs.
