# Reflection-mode Mueller matrix pipeline

Parallel to `../own_code/` (transmission), but for samples that reflect
light instead of passing it through -- a mirror, a bare silicon wafer, or a
silicon wafer with a thin SiO2 layer. Same acquisition hardware/scripts
(`Measuremt_ script/`) as transmission mode -- nothing there changes -- but
the optical bench must be folded so the camera arm sits at the reflected
beam's angle rather than in a straight line with the source. See "Physical
setup" below.

```
own_code_reflection/
├── DISCRETE/
│   ├── 3x3/     -- discrete-angle capture, 3x3 (linear-states-only) reconstruction
│   └── 4x4/     -- discrete-angle capture, full 4x4 reconstruction
└── CONTINOUS/
    └── 4x4/     -- continuous-rotation capture, 4x4 only (matches own_code's own scope)
```

Each subfolder has its own README with the full "physics background, from
zero" writeup (Fresnel reflection, the Airy thin-film formula,
Jones-to-Mueller conversion, basis alignment) and a step-by-step "run this,
in order" workflow table — start there for the details; this file is just
the map.

All results from every script in this tree are saved under
`C:\COMPARE_CASES\RESULT\reflection\...` — a single shared output root for
this whole project (see the root README's "RESULT/" section), created
automatically the first time anything writes to it.

## What's reused unchanged, what's new

- **`image_loader.py`, `mueller_forward_model.py`, `solve_mueller.py`,
  `main.py`, `average_rounds.py`, `fit_calibration.py`, and (4x4 only)
  `polar_decomposition.py`** are copied from `../own_code/` unchanged. The
  empirical reconstruction (turning captured images into a measured Mueller
  matrix) is geometry-agnostic -- it never knows or cares whether the
  sample transmitted or reflected the light, only the PSG/PSA angles and
  the measured intensities (see the root README's Frobenius-norm
  walkthrough). So none of that code needed to change for reflection.
- **`reflection_theory.py`, `theoretical_mueller.py`, and
  `validate_against_theory.py` are new** -- reflection is where a
  *theoretical* Mueller matrix needs new physics (Fresnel reflection /
  thin-film interference), which transmission's ideal-polarizer/QWP
  formulas don't cover.

## Workflow

1. **Capture**: same `Measuremt_ script/` tools as transmission mode, on
   your folded reflection rig. Copy the run into
   `Data/reflection/<date>/<sample>/` (or wherever you like -- these tools
   don't require any particular location, same as `own_code/`).
2. **Reconstruct**: `python main.py <run_directory>` -- exactly like
   transmission mode. Produces the measured Mueller matrix, per-pixel maps,
   and (4x4) polar decomposition (diattenuation, polarizance, depolarization
   index, estimated retardance).
3. **Compute theory** (optional standalone step): `python
   theoretical_mueller.py` -- prompts for wavelength, angle of incidence,
   substrate n/k, and (if there's a film) film n/k/thickness. Every value
   you enter is logged to `.theory_log.csv` (append-only, one row per
   calculation, plain CSV -- edit it by hand at any time), keyed by a
   sample label you provide; the next time you compute theory for that same
   label, the last logged values are suggested as defaults.
4. **Validate**: `python validate_against_theory.py` -- for each configured
   sample, prompts for that sample's theory parameters (same as step 3,
   sharing the same `.theory_log.csv`), reconstructs (or reuses a cached
   `main.py` reconstruction with matching calibration), and reports both
   the **Frobenius-norm error** and the **mean squared error (MSE)**
   between the measured and theoretical matrices, plus per-pixel error
   maps.

## Theory model scope

`reflection_theory.py` supports exactly two cases:

- **Bare substrate** (single interface, e.g. bare Si): plain Fresnel
  reflection coefficients.
- **Substrate + one thin film** (e.g. SiO2 on Si): the Airy summation
  formula -- the closed-form solution for exactly one film layer. This is
  the N=1 special case of the general transfer-matrix method (TMM); a
  second deposited layer, or anything needing more than one film, isn't
  supported and would need a genuine multi-layer TMM implementation added
  to `reflection_theory.py`.

Both cases assume a flat, isotropic, specularly-reflecting sample -- not
applicable to a rough or depolarizing surface (its Mueller matrix wouldn't
be well-described by a simple Jones-matrix model in the first place).

Every physics function in `reflection_theory.py` is validated in
`test_reflection_theory.py` against known special cases (Brewster's angle,
energy conservation, the zero-thickness and index-matched limits) rather
than trusted from memory -- see that file for what's actually checked.

## Physical setup (read before capturing)

- The acquisition scripts assume a straight-through optical path by
  default; reflection requires physically folding the camera arm to the
  angle of incidence (specular reflection: reflection angle = incidence
  angle). This is a hardware change on your bench, not a code change.
- **Basis alignment matters.** `reflection_theory.py` computes the
  theoretical matrix in the (p, s) basis defined by the plane of
  incidence -- p in-plane, s out-of-plane. Your PSG/PSA "0 degrees" must be
  physically aligned to that same p-axis for a comparison against the
  theoretical matrix to mean anything. The reconstruction itself doesn't
  care (it'll recover a correct matrix in whatever basis your PSG/PSA zero
  actually points to) -- but if that basis isn't p/s-aligned, the
  *theoretical* matrix you're comparing against is in the wrong frame, and
  the error numbers won't be meaningful. There's no automatic detection or
  correction for this -- verify the alignment physically.
- Record your angle of incidence per sample; `theoretical_mueller.py`'s log
  keeps it, but only you know if the bench was actually set to what you
  typed in.
