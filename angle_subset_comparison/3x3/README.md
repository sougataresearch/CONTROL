# angle_subset_comparison

Answers one question: a 3×3 Mueller matrix only needs **9 images** (3 PSG
angles × 3 PSA angles) to solve for. If a run captured **more** than that —
e.g. 36 images at a 6×6 angle grid — was that extra data worth it? Does using
all 36 images actually give a Mueller matrix closer to the known theoretical
one than some specific 9-image subset does? And *which* 3-angle subset
(`(0,30,60)`? `(0,60,120)`?) comes closest to theory?

This is the **single-sample** version of this analysis: it processes one
run at a time (edit `SAMPLE_DIRECTORY` at the top of `compare_subsets.py`
and rerun per sample). If you want every over-determined run under `Data/`
processed automatically in one go, see the sibling folder
`subset_error_analysis/` instead — same physics, same calculation, just
scanning many samples rather than one you point it at.

Fully self-contained: its own copy of the rotation-sandwich physics, image
loader, and theoretical-matrix formulas (matching
`control/matrix/own_code/DISCRETE/3x3/`). It never imports from `control/`,
and only reads from `Data/` — results are written to the shared
`RESULT/angle_subset_comparison/3x3/` tree, not into `control/`.

## Running it

Edit `SAMPLE_DIRECTORY` at the top of `compare_subsets.py` to point at one
3×3 run with a full N×N angle grid (so every 3-angle subset actually has all
9 images present), then:

```
python compare_subsets.py
```

## What gets written

Results land under `C:\COMPARE_CASES\RESULT\angle_subset_comparison\3x3\<date>\...\<sample>\`,
mirroring the same date/sample-type path the run has under `Data/` (e.g.
`Data/03072026/lp/lp30` → `RESULT/angle_subset_comparison/3x3/03072026/lp/lp30/`),
so the same sample name captured on a different date never overwrites an
earlier result.

- **`matrices.txt` / `matrices.json`** — the theoretical matrix, the full
  all-angles reconstruction, and every 9-image subset's actual reconstructed
  Mueller matrix, each alongside its full element-wise difference from
  theory. This is the raw, un-reduced comparison: nothing is boiled down to
  one number here, so you can inspect exactly which matrix elements are off
  and by how much.
- **`deviation_chart.png`** — one bar chart. Every angle-subset combination
  (plus the full-angle capture, in orange) is placed on the x-axis, sorted
  so the **lowest bar is the combination that deviates least from theory
  overall**. This is where the 9-number difference matrix above gets
  collapsed into a single bar height — the rest of this README explains
  exactly how and why.

## How "how far off is this matrix" becomes one number

Every reconstruction gives you a 3×3 matrix, and you're comparing it against
a 3×3 theoretical matrix. That's 9 individual differences — comparing 20
different 9-number spreads by eye to rank subsets isn't practical, so the
chart needs one number per subset that summarizes "how different are these
two matrices overall."

Take `lp30`'s full 36-image reconstruction from `matrices.txt` as a concrete
example. The theoretical matrix for a linear polarizer at 30°, and what was
actually reconstructed:

```
Theory:                          Reconstructed:
[ 1.0000  0.5000  0.8660 ]       [ 1.0000  0.5185  0.8586 ]
[ 0.5000  0.2500  0.4330 ]       [ 0.5074  0.2649  0.4341 ]
[ 0.8660  0.4330  0.7500 ]       [ 0.8542  0.4415  0.7339 ]
```

**Step 1 — element-wise difference.** Subtract theory from the
reconstruction, element by element (this is exactly the "Difference from
theory" matrix already printed in `matrices.txt`):

```
[  0.0000   0.0185  -0.0074 ]
[  0.0074   0.0149   0.0011 ]
[ -0.0118   0.0085  -0.0161 ]
```

**Step 2 — square every one of the 9 differences**, so negative and
positive errors both count as "bad" instead of canceling out (a +0.02 error
and a -0.02 error are equally wrong, but a plain sum would have them cancel
to 0 and hide the error entirely):

```
0.0000² = 0.00000000     0.0185² = 0.00034225     0.0074² = 0.00005476
0.0074² = 0.00005476     0.0149² = 0.00022201     0.0011² = 0.00000121
0.0118² = 0.00013924     0.0085² = 0.00007225     0.0161² = 0.00025921
```

**Step 3 — add all 9 squared differences together:**

```
0.00034225 + 0.00005476 + 0.00005476 + 0.00022201 + 0.00000121
+ 0.00013924 + 0.00007225 + 0.00025921 = 0.00114569
```

**Step 4 — take the square root**, to undo the squaring from step 2 and
bring the number back to the same scale/units as the original matrix
entries:

```
sqrt(0.00114569) ≈ 0.0338
```

That `0.0338` is exactly the number reported for `lp30`'s full-image
baseline, and it's the same four-step calculation (difference → square →
sum → square root) behind every bar in `deviation_chart.png`. In code, this
whole four-step process is just:

```python
deviation = float(np.linalg.norm(matrix_mean - theory))
```

because `np.linalg.norm` on a 2D array does exactly steps 1–4 for you by
default.

## This number has a name: the Frobenius norm

The quantity computed above — square every element-wise difference, add
them up, square root the sum — is called the **Frobenius norm** of the
difference matrix, written `‖A − B‖_F`:

```
‖A − B‖_F = sqrt( Σ over every row i, column j of (A_ij − B_ij)² )
```

**Why this and not something else?** Think of an ordinary 2D or 3D
distance: the distance between two points `(x1, y1)` and `(x2, y2)` is
`sqrt((x1−x2)² + (y1−y2)²)` — square the difference in each coordinate, add
them, square root. That's the Euclidean distance formula you already know.
The Frobenius norm is *exactly that same formula*, just applied to a matrix
instead of a point — if you took this 3×3 matrix and laid its 9 numbers out
in a single row instead of a grid, the Frobenius norm of the difference is
precisely the ordinary Euclidean distance between those two 9-number lists.
Nothing new is being invented here; it's the most natural way anyone would
generalize "distance between two numbers" to "distance between two grids of
numbers."

This also directly explains why **squaring** matters in step 2 above:
without it, a matrix that's `+0.02` too high in one spot and `−0.02` too low
in another would report zero total error (they'd cancel), even though both
spots are equally wrong. Squaring makes every error contribute positively,
regardless of its sign — the same reason ordinary distance formulas square
differences instead of just summing them.

## Isn't Mean Squared Error (MSE) the usual thing to use?

Yes — and the good news is you don't have to choose between them, because
**they always rank things identically.** MSE and RMSE (root mean squared
error) are the same calculation as the Frobenius norm above, just with one
extra step:

```
MSE  = (1/N) × Σ (A_ij − B_ij)²                <- average the squared differences
RMSE = sqrt(MSE)                                 <- square root, back to original units
Frobenius norm = sqrt(Σ (A_ij − B_ij)²) = sqrt(N) × RMSE
```

where `N` is the number of matrix elements (`N = 9` for a 3×3 matrix). So
for this 3×3 case, Frobenius norm is always exactly `sqrt(9) = 3` times
RMSE — not a different measurement, just a different constant multiplier on
the *same* underlying quantity (`Σ (A_ij − B_ij)²`, the raw sum of squared
errors). Because multiplying every value in a list by the same positive
constant (here, 3) never changes their *order*, ranking subsets by
Frobenius norm gives you the *exact same ranking* you'd get from ranking
them by MSE or RMSE instead. Whichever combo has the lowest Frobenius norm
also has the lowest MSE — always, no exceptions.

So Frobenius norm isn't "better than MSE" in the sense of measuring
something MSE misses — it's the same information, just skipping the
division by `N` and taking the root a step earlier. Two real reasons this
convention (Frobenius norm, not MSE) is what this script — and the
Mueller-matrix/optics literature generally — reports instead:

1. **It's the standard, basis-independent way to describe "distance between
   two matrices."** It has a clean geometric meaning (ordinary Euclidean
   distance, as shown above) and is unaffected by the arbitrary choice of
   how the matrix's elements happen to be arranged into a grid — a property
   MSE also has here, but Frobenius norm is the name and convention you'll
   see used when comparing Mueller matrices in published papers, so
   reporting it here keeps these numbers directly comparable to that
   literature.
2. **It matches the physical units of the matrix elements**, same as RMSE
   does (both are "back in the original units" after undoing the squaring),
   whereas plain MSE is in squared units and harder to interpret at a
   glance — e.g. an MSE of `0.0000011` for `lp30`'s full-image case doesn't
   immediately tell you "typical error is a bit over a hundredth," while
   its Frobenius norm (`0.0338`) or RMSE (`0.0338 / 3 ≈ 0.0113`) does.

If you'd rather think in RMSE terms, converting any number in this
project's output is just dividing by `sqrt(9) = 3` — the ranking of
subsets, and which one "wins," is identical either way.
