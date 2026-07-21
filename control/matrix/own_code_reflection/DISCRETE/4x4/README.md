# 4x4 Mueller matrix reconstruction — reflection mode

Reconstructs a **reflective** sample's full 4x4 Mueller matrix (a mirror, a
bare silicon wafer, or a silicon wafer with a thin SiO2 layer) from N
intensity images captured by the same rig as
`../../../own_code/DISCRETE/4x4/` (fixed `PSG_Polarizer`/`PSA_Analyzer`,
rotating `PSG_QWP`/`PSA_QWP`), but folded into a reflection geometry — see
"Physical setup" below. Works for any number of images and any flat,
specularly-reflecting sample.

This folder is fully self-contained: it does not import or depend on
anything in `../3x3/`, `../../CONTINOUS/`, or `../../../own_code/`. It only
reads the images and JSON config the acquisition scripts already produce.

## Physics background, from zero

If you haven't read `../../../own_code/DISCRETE/3x3/README.md`'s "Physics
background, from zero" section yet, read that first — it explains what a
Stokes vector and a Mueller matrix are, and why a camera pixel can't
directly see polarization. Everything below assumes you already know that.

### Why reflection needs different physics than transmission

In transmission mode (`own_code/`), the sample sits *between* the
generator and analyzer optics, and its Mueller matrix is built from a
known formula (an ideal polarizer or retarder, parameterized by angle).
That works because you already know *what kind* of optical element the
sample is.

For a reflective sample, you generally *don't* know its Mueller matrix in
advance the same way — a bare Si wafer's reflectance depends on its
refractive index (which you look up or measure separately), the angle of
incidence, and the wavelength. So reflection mode needs two genuinely
different things that transmission mode doesn't:

1. A way to *compute* a theoretical reflection Mueller matrix from real
   physical parameters (wavelength, angle of incidence, material indices,
   film thickness) — this is `reflection_theory.py`.
2. Because there's no single universal "this is what every sample should
   equal" reference (unlike air ≡ identity in transmission mode), you
   supply those physical parameters yourself, per sample, at the terminal
   — this is `theoretical_mueller.py`.

Everything about the empirical reconstruction itself (turning captured
images into a measured Mueller matrix) is **completely unchanged** from
transmission mode — see "What's reused unchanged" below.

### What's reused unchanged, what's new here

- **`image_loader.py`, `mueller_forward_model.py`, `solve_mueller.py`,
  `main.py`, `average_rounds.py`, `fit_calibration.py`, and
  `polar_decomposition.py`** are byte-for-byte copies of
  `../../../own_code/DISCRETE/4x4/`'s versions. The least-squares
  reconstruction (`intensity = A . M . S`, solved for `M` by `pinv`) never
  looks at what the sample physically *is* — only at the known PSG/PSA
  angles and the measured intensities — so it works identically whether
  the sample transmits or reflects light. See that folder's own README for
  the full derivation of this math; it is not repeated here.
- **`reflection_theory.py`, `theoretical_mueller.py`, and
  `validate_against_theory.py` are new** — this is the reflection-specific
  physics and the tool that uses it.

### Fresnel reflection: the physics of a single interface

When light hits a flat boundary between two materials (air and a silicon
wafer, say), some of it reflects. How much reflects — and how it changes
the light's polarization — depends on the **complex refractive index**
`n - i*k` of each material (`n` bends the light, `k` absorbs it) and the
**angle of incidence** (measured from the surface normal, i.e. straight
down onto the surface = 0°).

Crucially, light polarized **in the plane of incidence** (called **p**,
for "parallel") and light polarized **perpendicular to it** (called **s**,
for the German *senkrecht*, "perpendicular") reflect *differently* — this
is the whole reason reflection changes a beam's polarization state at all.
The **Fresnel equations** give the complex reflection coefficient for
each:

```
r_s = (n1 cos(theta1) - n2 cos(theta2)) / (n1 cos(theta1) + n2 cos(theta2))
r_p = (n2 cos(theta1) - n1 cos(theta2)) / (n2 cos(theta1) + n1 cos(theta2))
```

where `theta1` is your angle of incidence, `n1`/`n2` are the two materials'
complex indices, and `theta2` (the angle the light would refract to, if it
transmitted rather than reflected) comes from Snell's law,
`n1 sin(theta1) = n2 sin(theta2)`. `r_s`/`r_p` are complex numbers because
reflection can shift the light's *phase*, not just its amplitude — that
phase shift is exactly what lets reflection turn linear polarization into
elliptical.

`fresnel_coefficients()` in `reflection_theory.py` implements this
directly, working entirely in terms of `cos(theta2)` (computed via a
complex square root) so it never needs to compute a literal complex angle
— which is what lets it handle absorbing materials (`k > 0`, like silicon)
without special-casing.

**A useful sanity check built into this: Brewster's angle.** For a
non-absorbing material, there's a special angle of incidence,
`theta_B = arctan(n2/n1)`, at which `r_p` becomes *exactly zero* — only
s-polarized light reflects at all. This is why polarized sunglasses work
(most reflected glare off water/glass is s-polarized near this angle) and
is used in `test_reflection_theory.py` as an exact, easily-verified check
of the Fresnel formula rather than trusting it from memory.

### Thin-film reflection: the Airy formula

A bare substrate has one interface. A wafer with a thin film on top (e.g.
SiO2 on Si) has **two** — air-to-film, and film-to-substrate — and light
reflecting off each interferes with light reflecting off the other,
because both reflected beams are coherent (same light source, same
wavelength) and travel slightly different path lengths (crossing the film
twice). This interference is *wavelength-dependent* and
*thickness-dependent* — it's the same physics behind soap-bubble colors.

The **Airy summation formula** gives the *total* reflection coefficient for
exactly one film layer, combining both interfaces' Fresnel coefficients
with the coherent phase picked up crossing the film:

```
r_total = (r01 + r12 * exp(-2i*beta)) / (1 + r01*r12 * exp(-2i*beta))
```

computed separately for s and p polarization, where `r01`/`r12` are the
ordinary Fresnel coefficients at the two interfaces, and
`beta = 2*pi * thickness * n_film * cos(theta_in_film) / wavelength` is the
phase picked up crossing the film once (`cos(theta_in_film)` again from
Snell's law). `airy_reflection()` implements exactly this.

This is the `N=1` special case of the general **transfer-matrix method
(TMM)**, which handles any number of stacked layers by multiplying a 2x2
characteristic matrix per layer. This project only implements the one-film
case (Airy) because the two stated use cases — bare Si, or Si with one
SiO2 layer — never need more than one layer; supporting a second deposited
film would require implementing the general TMM instead.

**Two sanity checks built into this**, both in
`test_reflection_theory.py`:
- As `film_thickness_nm -> 0`, the Airy result must converge to the plain
  two-medium Fresnel result between air and the substrate directly (there's
  no film to speak of anymore).
- If the "film" has the *exact same* index as the substrate, there's no
  optical contrast at the buried interface, so the Airy result must equal
  the bare single-interface Fresnel result, regardless of the (physically
  meaningless in that case) thickness.

### From Fresnel/Airy coefficients to a Mueller matrix

`r_p` and `r_s` together describe the sample as a **Jones matrix** —
`diag(r_p, r_s)` in the (p, s) basis (no off-diagonal terms, because a flat
isotropic sample doesn't mix p- and s-polarized light into each other).
`jones_diagonal_to_mueller()` converts that into the equivalent 4x4 Mueller
matrix via the standard formula:

```
M = 0.5 * [[ |rp|^2+|rs|^2,  |rp|^2-|rs|^2,  0,                0              ],
           [ |rp|^2-|rs|^2,  |rp|^2+|rs|^2,  0,                0              ],
           [ 0,               0,              2*Re(rp*conj(rs)), 2*Im(rp*conj(rs))],
           [ 0,               0,             -2*Im(rp*conj(rs)), 2*Re(rp*conj(rs))]]
```

`bare_substrate_mueller()` and `film_on_substrate_mueller()` chain
Fresnel/Airy with this conversion and normalize the result so `M[0,0] = 1`,
matching the convention `solve_mueller.py`'s reconstruction already uses.

### Basis alignment — the one thing you must get right physically

The (p, s) basis above is defined by the **plane of incidence** — the
plane containing both the incoming and reflected beam. Your rig's PSG/PSA
"0 degrees" must be physically aligned with the p-axis (in that plane) for
a comparison against `reflection_theory.py`'s output to mean anything. The
*reconstruction* doesn't care about this at all — it'll recover a correct
measured matrix in whatever basis your PSG/PSA zero actually points to —
but if that basis isn't p/s-aligned, you'd be comparing your measured
matrix (in one basis) against a theoretical matrix computed in a different
basis, and the "error" you'd see would be a frame mismatch, not a real
physical discrepancy. There is no automatic detection or correction for
this in the code — it's a physical alignment you verify on the bench.

## Physical setup

The acquisition scripts (`Measuremt_ script/`) assume a straight-through
optical path by default. Reflection requires physically folding the camera
arm so it sits at the reflected beam's angle (specular reflection:
reflection angle = angle of incidence), rather than in a straight line with
the source. This is a hardware change on your bench, not a code change —
nothing in `Measuremt_ script/` needs editing.

## Run this — full workflow, in order

| Order | Script | What it does |
|---|---|---|
| 1 | `main.py` | Reconstructs one capture's measured Mueller matrix (+ polar decomposition). Required for every sample. |
| 2 | `average_rounds.py` | Optional. You captured >= 3 repeat rounds of the *same* sample — reports mean + standard deviation across rounds. |
| 3 | `theoretical_mueller.py` | Optional standalone step. Computes and saves a sample's *theoretical* reflection Mueller matrix from physical parameters you type in. |
| 4 | `validate_against_theory.py` | Compares a sample's measured (step 1) and theoretical (step 3, or computed inline) matrices, reporting Frobenius error and MSE. |
| 5 (conditional) | `fit_calibration.py` | Only if an air/mirror reference in step 4 shows more deviation than expected. Fits `extinction_ratio`/`retardance_deg` from a known reference capture. |

### Step 1 — `main.py`

Identical usage to `../../../own_code/DISCRETE/4x4/README.md` — open
`main.py`, edit `RUN_DIRECTORY` to point at your reflection capture (any
folder with `Images/` and `Config/experiment_config.json`, `"mode":
"4x4"`, populated `"fixed_angles"`), then `python main.py`. You'll be
prompted for `extinction_ratio`/`retardance_deg` exactly as in transmission
mode — these describe your PSG/PSA optics, not the sample, so the prompts
and their meaning are identical regardless of reflection vs. transmission.

Results are saved to
`C:\COMPARE_CASES\RESULT\reflection\4x4\reconstructions\<date>\.../<run
folder name>\` — same date/sample-path-mirroring behavior as transmission
mode, just under the `reflection` branch of the shared `RESULT/` root (see
the root README). Writes the same file set as transmission's 4x4 tool:
`mueller_matrix_normalized.npy`, `mueller_matrix_raw.npy`,
`residual_rms.npy`, `calibration_used.json`, `diattenuation_map.npy`,
`polarizance_map.npy`, `depolarization_index_map.npy`,
`retardance_deg_map.npy`, `summary.txt`, `mueller_matrix_overview.png`,
`residual_rms.png`, `polar_decomposition.png` — see
`../../../own_code/DISCRETE/4x4/README.md`'s table for what each contains,
and its "Polar decomposition" section for what diattenuation/polarizance/
depolarization index/estimated retardance mean and how they're computed.

Also supports dark-current subtraction, identically to transmission mode —
see `../../../own_code/DISCRETE/4x4/README.md`'s "Dark-current subtraction"
section: capture 1+ frames with the camera blocked (source off, or all
components removed/covered — either is fine), save them into
`<run_directory>/Dark/`, and `image_loader.py` averages and subtracts them
automatically. Optional — proceeds on raw intensities with a warning if
`Dark/` is absent.

### Step 3 — `theoretical_mueller.py`

Run `python theoretical_mueller.py`. You'll be prompted for a **sample
label** (any name you choose, e.g. `si_bare` or `si_sio2_100nm` — used to
look up/save this sample's parameters later), then:

```text
Wavelength (nm) [632.8]:
Angle of incidence (deg, from surface normal) [65]:
Substrate refractive index n [3.88]:
Substrate extinction coefficient k [0.02]:
Is there a thin film on top of the substrate? [y/N]:
```

(and, if you answer yes: film n, film k, film thickness in nm). Press
Enter on any prompt to accept the default shown in brackets — a generic
starting value the first time, or whatever you entered last time for that
same sample label after that. **Every value you type is manual input** —
nothing is looked up from a material database; you supply the real numbers
for your actual sample (from a datasheet, a separate ellipsometry
measurement, or literature values for that material at that wavelength).

Every parameter set you enter is appended as a new row to
`.theory_log.csv` next to this script (plain CSV, not committed to git —
open it in Excel or a text editor and edit it by hand at any time; the
*last* row for a given sample label is what gets suggested as the default
next time). The computed matrix is saved to
`RESULT/reflection/4x4/theoretical_matrices/<sample_label>.npy`.

### Step 4 — `validate_against_theory.py`

Run `python validate_against_theory.py`. Edit `SAMPLE_DIRECTORIES` at the
top first to list every reflection dataset you want checked (the folder
name becomes that sample's label in `.theory_log.csv`, shared with step
3). It will:

1. Confirm your configured folder list (OK/MISSING check).
2. Prompt for `extinction_ratio`/`retardance_deg` once, applied to every
   sample.
3. For each sample: prompt for its theory parameters (same prompts as step
   3, sharing the same log — if you already ran `theoretical_mueller.py`
   for this sample, its values are suggested as defaults here too),
   reconstruct (reusing `main.py`'s cached output automatically if one
   already exists with matching calibration), and report the error.

**How the error is calculated.** Let `measured` be the reconstructed mean
matrix and `theory` the computed theoretical matrix (both 4x4, 16 elements
total). Two numbers are reported, both from the same element-wise
difference:

```
diff = measured - theory                              (element-wise, 16 numbers)

Frobenius error = ||diff||_F = sqrt( sum of every diff_ij squared )
MSE             = mean of every diff_ij squared        =  (Frobenius error)^2 / 16
```

Concretely: square each of the 16 differences (so a `+0.02` error and a
`-0.02` error both count as equally wrong, instead of canceling out),
add them all up, and either take the square root (Frobenius error, same
units as the matrix elements) or divide by 16 first and then optionally
take the square root (MSE, or RMSE if you do take that root). These
always rank samples/calibration-candidates *identically* — whichever has
the lower Frobenius error also has the lower MSE, always, since one is
just a fixed multiple of a monotonic transform of the other. Both are
reported because Frobenius error is the standard convention in the
Mueller-matrix/optics literature (directly comparable to published
numbers), while MSE is the more familiar general statistics quantity —
see the root README's fuller walkthrough of this relationship (with a
worked numeric example) for the 3x3 case, which generalizes unchanged
here with `N = 16` instead of `N = 9`.

Saved per sample to
`RESULT/reflection/4x4/validation_against_theory/<date-relative-path>/`:
`theory.npy`, `experimental_mean.npy`, `per_pixel_frobenius_error.npy`,
`per_pixel_mse.npy`, `comparison.png` (theory / experiment / difference
side by side), `error_map.png` (per-pixel Frobenius and MSE heatmaps).
A `summary.txt` at the top level tabulates every sample's numbers plus
provenance.

### Step 5 — `fit_calibration.py` (conditional)

Same method as `../../../own_code/DISCRETE/4x4/README.md`'s "Calibrating
from an air capture" section — coordinate descent over
`(extinction_ratio, retardance_deg)` — but for reflection mode you'd point
`AIR_DIRECTORY` at a capture of a sample with a matrix you're confident
about independently (e.g. a good first-surface mirror, or a bare-Si
capture already cross-checked against a trusted reference measurement),
since there's no universal "should be identity" reference the way air is
in transmission mode. Skip this step entirely unless `validate_against_theory.py`
showed a reference sample deviating more than your noise floor would
explain.

## What you'd need to change to run this on new data

Nothing in the code — only:

- `RUN_DIRECTORY` in `main.py`, `SAMPLE_DIRECTORIES` in
  `validate_against_theory.py`, `AIR_DIRECTORY` in `fit_calibration.py`, to
  point at your actual captures.
- The physical parameters you type in at `theoretical_mueller.py`'s or
  `validate_against_theory.py`'s prompts, for your actual sample.
- `--extinction`/`--retardance` (or the prompts), once you have real
  calibration numbers for your polarizer/QWP optics.

## Requirements

`numpy`, `matplotlib`, `Pillow` (PIL) — every script here checks for these
on startup and `pip install`s whichever are missing into the same Python
interpreter that's running it. See
`../../../own_code/DISCRETE/4x4/README.md`'s "Getting the right Python
interpreter" note if you hit a `ModuleNotFoundError` despite that.
