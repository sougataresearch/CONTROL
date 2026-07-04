# MMIE Control System — Mueller Matrix Imaging Ellipsometer

Automated control of 4× Thorlabs K10CR2 rotation mounts (PSG/PSA polarizers + QWPs)
and 1× IDS U3-3890CP-M-GL camera for 3×3 and 4×4 Mueller matrix imaging.

**This is an earlier, notebook-driven reference implementation, kept for
comparison — it is not required to run any current experiment.** The
production acquisition code is `../discreate_angle/` (3×3 and 4×4 discrete)
and `../continous_rotation/` (4×4 continuous, acquisition loop still
unimplemented); Mueller matrix reconstruction from captured images lives in
`matrix/own_code/` at the repository root. For the physics behind PSG/PSA,
Stokes vectors, and Mueller matrices — assuming no prior background — see
`../discreate_angle/README.md`'s "Physics background, from zero" section;
everything here operates on the same underlying concepts.

## Folder layout
```
MMIE_Control/
├── mmie/                       ← shared package (edit config.py ONLY)
│   ├── config.py               ← serials, zero offsets, timings, camera, paths
│   ├── angles.py               ← "360/10" parsing, optical→motor wrap, combos
│   ├── motors.py               ← K10CR2 via Kinesis .NET, confirmation gates
│   ├── camera.py               ← IDS peak: software trigger, BMP save + verify
│   ├── runlog.py               ← run folders, master log, checkpoint/resume
│   └── measure.py              ← discrete measurement engine (+ continuous stub)
├── NB0_Environment_Check.ipynb ← run FIRST on the lab PC (nothing moves)
├── NB1_Motor_Bringup.ipynb     ← detect→connect→init→enable→home→optical zero
├── NB2_State_Generation_Test.ipynb ← dry-run angle logic (no hardware needed)
├── NB3_Camera_Test.ipynb       ← one triggered frame, exposure tuning, dark frame
└── NB4_Measurement_Run.ipynb   ← the full experiment with checkpoint/resume
```

## Install on the lab Windows PC
1. **Thorlabs Kinesis (64-bit)** — default path `C:\Program Files\Thorlabs\Kinesis`.
   Open the Kinesis GUI once with a motor plugged in and note the settings-name
   string it shows for the K10CR2 (put it in `MOTOR_SETTINGS_NAME` if it differs).
2. **IDS peak** from ids-imaging.com — test the camera once in *IDS peak Cockpit*.
3. Python packages:
   ```
   pip install pythonnet ids_peak ids_peak_ipl numpy pillow
   ```

## Before the first real run — edit `mmie/config.py`
- [ ] Fill in the two missing QWP serial numbers (`REPLACE_ME`).
- [ ] Replace the EXAMPLE `zero_offset` values with your polarimeter-measured
      motor angles for optical 0° (per serial number!).
- [ ] Set `DATA_ROOT` to where you want the data saved.
- [ ] Tune `CAM_EXPOSURE_US` using Notebook 3.

## Run order
NB0 → NB2 (dry run, anywhere) → NB1 (motors only) → NB3 (camera only) → NB4 (real run).

## Key behaviors implemented
- Mode selected **first**; only the needed motors (2 or 4) are touched.
- Every stage gated by a y/N prompt; one motor at a time with settling gaps.
- Optical→motor conversion `(zero_offset + optical) % 360` (370° → 10°).
- `"360/10"` = 36 positions (360 ≡ 0); comma lists also accepted; PSG and PSA
  lists may differ → full cross-product grid, previewed before starting.
- Discrete loop per your flowchart: move → settle 1 → trigger → save BMP →
  verify file size → print `Saved: 30_60.bmp` → log → checkpoint → settle 2.
- Filenames use **optical** angles only. Run folders are dated + numbered and
  contain `run_log.txt` (serials, offsets, timings) and `checkpoint.json`.
- Crash mid-scan → re-run NB4, it detects the unfinished run and offers resume.
- Black-frame / saturation warnings after every capture; optional 5-frame
  master dark with guided cover/uncover prompts.

## Still open (waiting on you)
1. **Two QWP serial numbers** (photos of the labels are enough).
2. **Continuous 4×4 mode** — decide image timing: frame-rate-based capture
   (free-run at N fps, log angles per frame) vs angle-based capture (trigger
   every X° of PSG QWP). The stub in `measure.py` lists the implementation plan.
3. Measured zero offsets for all four motors.
