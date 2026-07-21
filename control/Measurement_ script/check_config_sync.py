"""Standalone diagnostic: compare MOTOR_SN/ZERO_OFFSET between the two
acquisition folders' config.py files.

discreate_angle/config.py and continous_rotation/config.py deliberately do
NOT share code (see both folders' READMEs), but MOTOR_SN and ZERO_OFFSET are
supposed to describe the SAME physical hardware in both, hand-duplicated on
purpose. Nothing catches the two drifting apart after a recalibration or a
motor swap in only one file — a wrong offset "silently rotates every
measurement by a constant offset" (see either config.py's own comment).

This script is NOT imported by either 01_main.py; run it by hand after any
hardware change:

    python check_config_sync.py

Exits 0 if the two files agree, 1 if they disagree (printing every
difference), 2 if either config.py can't be loaded.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_config(folder_name: str):
    path = Path(__file__).resolve().parent / folder_name / "config.py"
    module_name = f"_config_{folder_name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # Must be registered before exec_module(): config.py's @dataclass(slots=True)
    # classes need sys.modules[cls.__module__] to resolve their (stringified,
    # due to `from __future__ import annotations`) field type annotations.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _diff_dict(name: str, discrete: dict, continuous: dict) -> list[str]:
    problems = []
    all_keys = sorted(set(discrete) | set(continuous))
    for key in all_keys:
        left = discrete.get(key, "<missing>")
        right = continuous.get(key, "<missing>")
        if left != right:
            problems.append(
                f"  {name}[{key!r}]: discreate_angle={left!r}  continous_rotation={right!r}"
            )
    return problems


def main() -> int:
    try:
        discrete = _load_config("discreate_angle")
        continuous = _load_config("continous_rotation")
    except Exception as exc:
        print(f"Could not load one of the config.py files: {exc}")
        return 2

    problems = []
    problems += _diff_dict("MOTOR_SN", discrete.MOTOR_SN, continuous.MOTOR_SN)
    problems += _diff_dict("ZERO_OFFSET", discrete.ZERO_OFFSET, continuous.ZERO_OFFSET)

    if problems:
        print("Configuration DRIFT detected between discreate_angle and continous_rotation:")
        print("\n".join(problems))
        return 1

    print("MOTOR_SN and ZERO_OFFSET agree between discreate_angle and continous_rotation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
