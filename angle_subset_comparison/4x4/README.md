# angle_subset_comparison/4x4

Answers one question: a 4×4 Mueller matrix only needs **16 images** (4
PSG_QWP angles × 4 PSA_QWP angles) to solve for. If a run captured **more**
than that — e.g. 72 images at an 8×9 grid, or 108 at 12×9 — was that extra
data worth it? Does using every captured image actually give a Mueller
matrix closer to the known theoretical one than some specific 16-image
subset does? And *which* 4-angle QWP subset comes closest to theory?

This is the **4×4 discrete** counterpart to `../3x3/`, and the
**single-sample** version of this analysis: it processes one run at a time
(edit `SAMPLE_DIRECTORY` at the top of `compare_subsets_4x4.py` and rerun
per sample). If you want every over-determined 4×4 run under `Data/`
processed automatically in one go, see the sibling folder
`subset_error_analysis/4x4/` instead — same physics, same calculation, just
scanning many samples rather than one you point it at.

It models this rig's 4×4 generator/analyzer: a **fixed** polarizer followed
by a **rotating** QWP (`PSG_QWP`/`PSA_QWP` step through a grid of angles;
`PSG_Polarizer`/`PSA_Analyzer` stay fixed for the whole run and are read
from that run's `Config/experiment_config.json`).

**4×4 discrete only.** Not applicable to continuous rotation
(`control/matrix/own_code/CONTINOUS/4x4/`) — that mode's QWPs spin
continuously with no fixed angle grid, so there's no discrete set of angles
to draw combinations from.

Fully self-contained: its own copy of the fixed-polarizer + rotating-QWP
rotation-sandwich physics, image loader, and theoretical-matrix formulas
(matching `control/matrix/own_code/DISCRETE/4x4/`). It never imports from
`control/`, and only reads from `Data/` — results are written to the shared
`RESULT/angle_subset_comparison/4x4/` tree, not into `control/`.

## Running it

Edit `SAMPLE_DIRECTORY` at the top of `compare_subsets_4x4.py` to point at
one 4×4 run with a full N×N QWP-angle grid (so every 4-angle subset actually
has all 16 images present), then:

```
python compare_subsets_4x4.py
```

## What gets written

Results land under `C:\COMPARE_CASES\RESULT\angle_subset_comparison\4x4\<date>\...\<sample>\`,
mirroring the same date/sample-type path the run has under `Data/`, so the
same sample name captured on a different date never overwrites an earlier
result.

- **`matrices.txt` / `matrices.json`** — the theoretical matrix, the full
  all-angles reconstruction, and every 16-image subset's actual
  reconstructed Mueller matrix, each alongside its full element-wise
  difference from theory.
- **`deviation_chart.png`** — one bar chart. Every QWP-angle-subset
  combination (plus the full-angle capture, in orange) is placed on the
  x-axis, sorted so the **lowest bar is the combination that deviates least
  from theory overall**.

## How "how far off is this matrix" becomes one number

Exactly the same calculation as the 3×3 tools, just with a 4×4 (16-element)
matrix instead of a 3×3 (9-element) one. See `../3x3/README.md` or the root
`README.md` for the full step-by-step worked example (element-wise
difference → square each → sum → square root) and the deep explanation of
the Frobenius norm and how it relates to MSE/RMSE — every word of that
explanation applies here unchanged, just substitute "16 elements" for "9
elements" and `sqrt(16) = 4` for `sqrt(9) = 3` when converting to/from RMSE:

```
Frobenius norm = sqrt(Σ over all 16 elements of (A_ij − B_ij)²) = sqrt(16) × RMSE = 4 × RMSE
```

In code, the whole calculation (for either matrix size) is the same one
line:

```python
deviation = float(np.linalg.norm(matrix_mean - theory))
```

`np.linalg.norm` computes the Frobenius norm of a 2D array regardless of
its shape, so nothing else about the calculation changes for 4×4 versus
3×3 — only the physics used to build the reconstruction matrix does.
