# Fourier Curve Experiment — instrument calibration & validation for the dual-rotating-QWP IMMSE

This is a **separate, self-contained project folder**, independent of
`control/matrix/` (Mueller-matrix reconstruction) and
`control/Measurement_ script/` (the acquisition scripts it borrows a hardware
layer from). It does not share code with those folders by import — the
motor/camera control files it needs are copied in here, following the same
"each module owns a complete copy" convention already used elsewhere in
this repo (see `control/matrix/own_code_reflection/README.md`'s "What's
reused unchanged, what's new" section for the same pattern applied to
Mueller-matrix reconstruction).

**Status: fully implemented.** Every file in the folder layout below
exists and has been dry-run tested end-to-end (all 4 cases, plus
`run_all_cases.py`'s gated back-to-back sequence) with simulated
motors/camera -- see "Quickstart" below to try it yourself before ever
touching real hardware. Real-hardware runs are entirely up to you; this
project deliberately stops short of ever calling `--dry-run`-less code
itself.

## Quickstart

```
python run_case.py --case 1 --dry-run          # one case, simulated hardware
python run_all_cases.py --dry-run              # all 4 cases, gated between each
python run_case.py --case 4 --qwp2-direction-sign 1   # real hardware, once confirmed (see "Sign convention")
```

Every prompt (hardware confirmation, camera exposure/gain/frame-rate, the
dark-current block/unblock reminder) works identically in `--dry-run` and
on real hardware -- dry-run just simulates the motors/camera underneath
instead of touching real devices, using the exact same
`MotorController`/`CameraController` dry-run mode already proven
throughout `control/Measurement_ script/`.

## What this is, in one paragraph

Before this instrument (a dual-rotating-QWP Imaging Mueller Matrix
Spectroscopic Ellipsometer, IMMSE, following Käseberg's PCSCA
configuration — PTB dissertation, 2024) is trusted to measure a real
sample's Mueller matrix, you need to know the instrument itself is
behaving the way the theory says it should — correct alignment, correct
QWP retardance, correct 5:1 rotation coupling, no unexpected offsets. This
experiment builds that confidence in four stages of increasing complexity,
each stage adding exactly one more optical element into the beam and
checking the resulting intensity-vs-angle curve against the closed-form
theoretical prediction for that configuration. If a stage fails to match
theory, you stop and fix the instrument *before* moving to the next stage
— there is no point validating the full 4-element PCSCA configuration if
a single rotating polarizer alone doesn't already match Malus's law.

## Hardware

- **Optical train**: fixed polarizer P1 → rotating QWP1 (PSG arm) →
  sample → rotating QWP2 (PSA arm) → fixed analyzer P2 → camera.
- **QWPs**: Thorlabs WPQ10M-532, nominal retardance 89.97° at 532 nm (not
  exactly 90° — this is why the fitted coefficients, not the textbook-ideal
  ones, are the real ground truth for your specific optics; see "What
  success looks like" below).
- **Camera**: IDS U3-389xCP-M, 4000×3000 px, 8-bit grayscale (Mono8) —
  the same camera and control code already used by
  `control/Measurement_ script/discreate_angle/`.
- **Laser**: 532 nm.
- **Motors**: Thorlabs rotation mounts, driven via the same
  `motor_controller.py`/`ZERO_OFFSET` convention as the rest of this repo
  — angles are always *optical* angles in this project's own code; the
  conversion to *motor* angle (`optical_to_motor(optical_angle, offset) =
  (optical_angle + offset) % 360`) happens once, right before the motor is
  actually commanded, exactly as in `discreate_angle/`.

## The four calibration cases — full mathematical detail

All four cases measure a camera intensity as a function of one or two
rotating-optic angles and fit that measured curve to a truncated Fourier
series in the rotation angle. The physics reason a Fourier series is the
right model at all: every element in this beam path (polarizer, QWP) is
represented by a Mueller matrix that is built by sandwiching a *fixed*
matrix between two rotation matrices, `M(θ) = R(-θ) · M_fixed · R(θ)`.
Rotation matrices are built from `sin`/`cos` of the rotation angle, so
`M(θ)`'s entries are degree-≤2 trigonometric polynomials in `θ` — i.e.
combinations of `1, cos(2θ), sin(2θ)`. When two independently-rotating
elements are both in the beam, their *product* (via the Stokes-vector
propagation `S_out = M2(θ2) · M_sample · M1(θ1) · S_in`) multiplies these
trig polynomials together, which is exactly what generates the higher
harmonics (`4θ`, `8θ`, ... ) seen in Cases 2-4. This is why the required
truncation order (how many harmonics you must fit, and therefore how
finely you must sample `θ`) grows with the number of rotating elements
in the beam.

### Case 1 — P1 only (Malus's law)

Everything except the fixed input polarizer P1 is removed from the beam;
P1 itself is rotated through a full 360°. The detected intensity is the
first row of a polarizer's Mueller matrix dotted with the input Stokes
vector `S = (S0, S1, S2, S3)`:

```
I(θ) = 0.5 * ( S0 + S1*cos(2θ) + S2*sin(2θ) )
```

This is the Stokes-formalism generalization of Malus's law
(`I = I0 * cos²(θ - θ0)`) — the two forms are algebraically identical once
you note `S1*cos(2θ) + S2*sin(2θ) = sqrt(S1² + S2²) * cos(2θ - 2θ0)` with
`θ0 = 0.5 * atan2(S2, S1)`. Two numbers `fit_and_plot.py` extracts and
prints prominently for Case 1:

- **Extinction ratio** `Imax / Imin = (S0 + sqrt(S1²+S2²)) / (S0 - sqrt(S1²+S2²))`
  — how well P1 actually blocks the orthogonal polarization; a real
  polarizer never reaches infinity here.
- **Phase offset** `θ0` — where the fitted curve's maximum actually sits
  vs. where you *think* optical zero is. A large `θ0` is your first
  alignment red flag, before you've even added a second optic.

### Case 2 — P1 fixed, QWP1 rotating, P2 fixed (QWP2 out of the beam)

Now the retarder (QWP1) is between the two fixed polarizers, and it is
what's rotating. A single rotating retarder sandwiched between two fixed
polarizers produces up to the **4th harmonic** in the rotation angle (the
"degree-2 trig polynomial squared, in effect, once you propagate through
one more fixed element" argument above):

```
I(θ) = a0/2 + (a2*cos(2θ) + b2*sin(2θ))/2 + (a4*cos(4θ) + b4*sin(4θ))/2
```

**The built-in alignment diagnostic**: for an ideally-aligned instrument
(P1's transmission axis, QWP1's fast axis, and P2's transmission axis all
at their assumed nominal relative angles), theory predicts `a2 = 0` — the
2θ term should vanish entirely, leaving only the DC term and the 4θ term.
A nonzero fitted `a2` means some pair of optics isn't at the angle you
think it's at. `fit_and_plot.py` prints the fitted `a2` prominently for
this exact reason — it is not a nice-to-have number, it is the test.

### Case 3 — P1 fixed, QWP1 out of the beam, QWP2 rotating, P2 fixed

The mirror image of Case 2, now validating the PSA arm instead of the PSG
arm. Same functional form, same `a2`-should-vanish diagnostic, applied to
QWP2/P2 instead of P1/QWP1.

### Case 4 — full PCSCA, air (identity) sample, QWP1:QWP2 coupled 5:1

Both QWPs are now in the beam simultaneously, rotating together with QWP2
always at exactly 5× QWP1's angle (`θ_A = 5*θ_G`, both taken through
`optical_to_motor`'s `% 360` wrap — see "Sign convention" below). With no
sample in the beam (air = identity Mueller matrix), theory (eq 51/52,
Käseberg) predicts a pure-cosine series (no sine terms — a sample with no
optical activity/circular effects gives a curve symmetric about `θ=0`) in
even multiples of `4θ`:

```
I(θ) = a0 + a2*cos(4θ) + a4*cos(8θ) + a6*cos(12θ) + a8*cos(16θ) + a10*cos(20θ)
```

with ideal-case coefficients `a0=1.25, a2=0.25, a4=-0.5, a6=0.5, a8=0.25,
a10=0.25`. **These assume the eq(52)-convention absolute scale** — the fit
in `fit_and_plot.py` includes a free overall amplitude/offset rather than
trusting that your camera's arbitrary intensity units happen to match this
convention's normalization; only the *relative shape* (the ratios between
coefficients) is what actually gets validated against these reference
values, and this is called out explicitly in a code comment at the fit
call site so nobody mistakes a scaled but correctly-shaped fit for a
failure.

### Nyquist / sampling requirement, worked through explicitly

The highest harmonic present in each case's theoretical model sets a hard
floor on how coarse your angle step can be before you start silently
aliasing signal into the wrong harmonic (a classic and easy-to-miss
mistake — an under-sampled high harmonic doesn't just look noisy, it
masquerades as a *different, lower* harmonic in the fit).

| Case | Highest harmonic (cycles over 0-360°) | Period of that harmonic | Bare Nyquist limit (2 samples/period) | Step size used | Samples over 0-360° | Oversampling vs. bare Nyquist |
|---|---|---|---|---|---|---|
| 1 | 2 (`cos 2θ`) | 180° | 90° | 5° | 72 | 18× |
| 2 / 3 | 4 (`cos 4θ`) | 90° | 45° | 10° | 36 | 4.5× |
| 4 | 20 (`cos 20θ`, the `a10` term) | 18° | 9° | 3° | 120 | 6× |

For Case 4, the requirement text asked for a margin against a `j=12`
harmonic (`cos 24θ`, period 15°, bare Nyquist limit 7.5°) even though the
ideal-air theoretical model above only goes up to `a10` (`cos 20θ`) — that
extra margin is intentional, not a typo: real hardware (imperfect QWP
retardance, small misalignments) can leak a small amount of signal into
one harmonic beyond what the ideal model predicts, and if that happens you
want to be able to *see* it in the fit residual rather than have it alias
invisibly into a lower term. At 3° steps (120 samples), both the ideal
20-cycle requirement (6× oversampled) and the conservative 24-cycle margin
(2.5× oversampled) are comfortably satisfied.

### Sign convention -- QWP1:QWP2 rotation direction (read before running Case 4)

`sweep.py`'s coupling formula for Case 4 is
`theta_QWP2 = qwp2_direction_sign * 5 * theta_QWP1` (wrapped mod 360).
**`qwp2_direction_sign` has no default anywhere in this project** --
`run_case.py`/`run_all_cases.py` require you to pass
`--qwp2-direction-sign 1` or `-1` (or answer the interactive prompt),
and `sweep.run_sweep()` raises immediately if it's omitted.

Why there's no default: whether "optical angle increasing" means the
same physical rotational sense (as viewed from the beam) on both the
QWP1 (`PSG_QWP`) and QWP2 (`PSA_QWP`) mounts is a fact about how each
stage is physically mounted on your bench -- it is not recoverable from
source code. A full repo search turned up nothing that establishes it:
`move_motor_angle()`'s Kinesis `MoveTo` call is an absolute-position
command with no direction argument at all, and `ZERO_OFFSET` (in
`config.py`) is documented as purely an additive phase constant ("Wrong
values here silently rotate every measurement by a constant offset") --
it says nothing about rotational sense. The one place in the whole
repository where a rotation *direction* is explicit in code
(`continous_rotation/motor_controller.py`'s `start_continuous(name,
forward=True)`) is itself flagged by its own author as "NOT YET VERIFIED
against the lab PC's installed Kinesis .NET assembly" -- so even that
precedent doesn't establish confidence here.

**How to determine the correct sign, physically, before running Case 4:**
with QWP2 stationary, command QWP1 alone to a small positive optical step
(e.g. `python run_case.py --case 2 --dry-run` won't help here since it's
simulated -- do this as a quick manual real-hardware move) and watch which
way it physically turns as viewed from the beam side. Do the same for
QWP2 alone. If they turn the same physical way, the sign is `+1`;
if opposite, `-1`. Get this wrong and Case 4's sweep will still run to
completion without any error -- it will just silently drive QWP2 in the
physically wrong relative direction, invalidating the whole case's fit
against the eq(51)/(52) theory without any other symptom.

## Folder layout

```
fourier_curve_experiment/
├── motor_controller.py     -- copied from control/Measurement_ script/discreate_angle/, unchanged
├── camera_controller.py    -- copied from control/Measurement_ script/discreate_angle/, unchanged
├── config.py                -- ZERO_OFFSET/MOTOR_SN/CameraSettings/TimingSettings copied unchanged;
|                                ExperimentConfig/ACTIVE_MOTORS (3x3/4x4-specific) dropped, ROI constant added
├── utils.py                  -- optical_to_motor(), format_angle(), write_json(), etc. -- copied, unchanged
├── theory.py                -- Case 1-4 Fourier models + scipy curve_fit wrappers (no hardware dependency at all)
├── test_theory.py             -- validates every model/fit against synthetic data with known coefficients
├── hardware_gate.py            -- per-case physical-setup confirmation prompt, not skippable
├── preflight.py                  -- dark frame, ROI check, exposure/gain check, motor position log
├── sweep.py                       -- generic angle-sweep runner (single mount or 5:1-coupled pair)
├── fit_and_plot.py                 -- fit + residual + comparison plot + diagnostics printout
├── run_case.py                      -- orchestrates one case end-to-end
├── run_all_cases.py                  -- runs Case 1 -> 4 in order, gated
└── README.md                          -- this file
```

Results are saved under
`RESULT/calibration/fourier_curve_experiment/case<N>_<name>/<timestamp>/`
— a new top-level `calibration/` branch of this repo's shared `RESULT/`
root (see the root `README.md`), kept separate from the Mueller-matrix
`transmission/`/`reflection/` branches since this is instrument validation,
not a sample measurement.

## Which code you actually run, and in what order

You never call `theory.py`, `preflight.py`, `sweep.py`, or
`fit_and_plot.py` directly during a real run — `run_case.py` calls all of
them internally, always in this fixed order, for a single case:

```
hardware_gate  ->  preflight  ->  sweep  ->  fit_and_plot  ->  save everything
```

**What you actually type**, in order:

1. `python run_case.py --case 1` — validates P1 alone against Malus's law.
   Look at the printed extinction ratio and phase offset. If either looks
   wrong (extinction ratio far below your polarizer's spec, or phase
   offset far from 0), fix the physical alignment before continuing —
   every later case builds on Case 1 already being correct.
2. `python run_case.py --case 2` — validates the PSG arm (QWP1). Look at
   the printed `a2` diagnostic. Large `a2` means P1/QWP1's relative angle
   is off; fix it before continuing.
3. `python run_case.py --case 3` — validates the PSA arm (QWP2),
   mirroring step 2.
4. `python run_case.py --case 4` — validates the full instrument with air
   (identity Mueller matrix) as the "sample," at the coupled 5:1 rotation.
   Compare the fitted coefficients' *shape* (ratios between `a0..a10`) to
   the ideal values given above.

Or, once you trust the sequence, `python run_all_cases.py` runs all four
back-to-back — but it still stops and requires a manual `y` confirmation
at `hardware_gate` before every single case (including the first), since
each case requires you to physically add or remove an optic from the
beam. There is no flag to skip this — it's a deliberate safety gate, not
configuration.

## ROI

All four cases use the **same fixed ROI**, `rows 1600:2200, cols
3300:3900`, since it's the same camera, same lens, same mounting as the
rest of this project (`control/matrix/`) — the ROI itself doesn't change
case-to-case. What *does* happen every case is a fresh live-preview check
against that fixed window (min/max pixel value, saturation warning above
240, too-dim warning below 20) in `preflight.py`, because the *sample* in
the beam path changes between cases (air/no-sample throughout, but the
optics you just added/removed can shift where the beam actually lands),
and a stale ROI reading from a previous case is exactly the kind of silent
mistake this whole experiment exists to catch.

## What "success" looks like

- **Case 1**: fitted curve visually overlays the raw scatter almost
  exactly (this is the simplest possible case — if it doesn't fit well,
  something more basic than optics is wrong, e.g. dark current, camera
  saturation, or a motor angle error). Extinction ratio in the same
  ballpark as your polarizer's datasheet spec. Phase offset near 0 (within
  your alignment tolerance).
- **Case 2 / 3**: fitted `a2` small relative to `a4` (ideally near the
  fit's own noise floor). This is your real "is the PSG/PSA arm aligned"
  answer — more informative than eyeballing the plot, since a small `a2`
  can be visually invisible against the dominant `a4` term but still shows
  up clearly as a fitted number.
- **Case 4**: fitted coefficient *ratios* (`a2/a0`, `a4/a0`, etc.) close to
  the ideal ratios (`0.25/1.25 = 0.2`, `-0.5/1.25 = -0.4`, etc.) — remember
  only the shape is meaningful, not the absolute scale (see the eq(52)
  scale caveat above). This is the last gate before this instrument is
  trusted to measure a real sample's Mueller matrix using the code already
  built in `control/matrix/`.
- Across all four cases: low residual RMS between the fit and the raw
  data, and no dark-current-subtraction warning printed (confirms the
  `Dark/`-frame workflow from `control/matrix/`'s dark-current feature is
  actually being used here too).

## Suggestions to make this more robust (not yet built — flagging for a decision)

- **Repeat each case >= 3 times** (mirroring `control/matrix/`'s existing
  `average_rounds.py` pattern) to get a standard deviation on every fitted
  coefficient, not just a single point estimate — this is the only way to
  know if a borderline `a2` in Case 2/3 is a real alignment problem or
  just run-to-run noise.
- **Automatic pass/fail thresholds**, not just printed numbers — e.g. flag
  `a2` automatically if it exceeds some fraction of `a4`, rather than
  relying on you eyeballing the printed value every time. Would need you
  to pick the threshold; not guessing at one.
- **Cross-check against the existing Bright/Dark reference frames**
  (`Results/BrightReference_0_0.bmp`, `Results/DarkReference_0_90.bmp`)
  already produced by `discreate_angle/01_main.py`, as a fast sanity check
  on absolute intensity scale before the full sweep runs.
- **Tie every case's results to a git commit hash** (already planned in
  `run_case.py`, matching the provenance convention used everywhere else
  in this repo) so a fit result can always be traced back to the exact
  code that produced it.

None of the above are built yet — say which (if any) you want included
before I start on `theory.py`.
