# Folder naming convention (applies to every capture, every script)

This is the one rule to remember. Everything in `own_code/3x3` and
`own_code/4x4` depends on the run folder being named correctly -- there is
no way for the code to guess your intent from a wrong name, so this is the
one place a typo actually costs you.

## The rule

```
<sample>[_round<NN>]
```

placed under `Data/<date>/<sample-type>/`, exactly as you're already doing:

```
Data/03072026/lp/lp30/                Images/  Config/experiment_config.json
Data/03072026/lp/lp30_round01/        Images/  Config/experiment_config.json
Data/03072026/lp/lp30_round02/        Images/  Config/experiment_config.json
Data/03072026/qwp/qwp45/              Images/  Config/experiment_config.json
```

- **`<sample>`** -- short and descriptive: `lp30`, `qwp45`, `air`. Whatever
  you'd call the sample out loud.
- **`_round<NN>`** -- add this suffix **only** when you deliberately
  re-capture the *same* sample, *same* angle set, to average out
  round-to-round error (see `average_rounds.py` in each mode's folder).
  Always two digits (`round01`, not `round1`) so 10+ rounds still sort and
  read correctly.
- Leave the suffix off entirely for a normal, one-time capture -- don't
  write `_round01` if there's no `_round02` coming.

## Why this is the one rule, not several

- The acquisition scripts in `Measuremt_ script/discreate_angle` already
  name each run's folder after the sample you typed in at the "sample name"
  prompt -- `Data/YYYY-MM-DD_<sample name>/` (see its own `README.md`,
  "Output folders"). If you happen to type the same sample name again in
  one session, it auto-suffixes `_02`, `_03`, ... rather than overwriting.
  That auto-suffix is *not* the same thing as this project's `_round<NN>`
  convention -- when you copy that run's `Images/`/`Config/` out into
  `G:\control\Data\<date>\<type>\<sample>...\` for analysis (see
  `Measuremt_ script/discreate_angle/README.md`'s note on this), that copy
  is the moment to apply `_round<NN>` deliberately if it really is a repeat
  round meant for `average_rounds.py` -- don't just carry an auto `_02`
  forward and assume the two mean the same thing.
- The mode (3x3 vs 4x4) is never encoded in the folder name -- it's already
  recorded in that run's `Config/experiment_config.json` (`"mode"`), and
  both pipelines read it from there and refuse to run on the wrong mode's
  data with a clear error. You don't need to remember to encode it twice.
- `average_rounds.py` derives the sample name for its aggregate output by
  stripping `_round<NN>` back off (`lp30_round01` -> `lp30`) -- so the
  single-run and multi-round results end up in comparably named output
  folders without you having to type the sample name twice.

## Quick reference: which file do I run?

| Situation | Run |
|---|---|
| One capture, 3x3 mode | `own_code/3x3/main.py` |
| One capture, 4x4 mode | `own_code/4x4/main.py` |
| Several repeat rounds, 3x3 mode | `own_code/3x3/average_rounds.py` |
| Several repeat rounds, 4x4 mode | `own_code/4x4/average_rounds.py` |

In every case, the only thing you edit is the folder path(s) at the top of
the file.
