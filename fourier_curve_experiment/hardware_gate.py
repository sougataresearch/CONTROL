"""Per-case physical hardware confirmation gate.

Every case in this experiment requires the operator to physically add or
remove an optic from the beam path before it can run. This is a
deliberate, manual, per-case safety gate -- there is NO flag or config
value anywhere in this project that bypasses it. If you're looking for a
way to skip this for scripting/automation convenience: don't -- getting
this wrong means capturing an entire case's data with the wrong optics in
the beam, which silently invalidates that case's fit against the
theoretical model in theory.py.
"""

from __future__ import annotations


CASE_MESSAGES: dict[int, str] = {
    1: (
        "Case 1 -- P1 only (Malus's law).\n"
        "  - P1 (fixed input polarizer) must be IN the beam.\n"
        "  - QWP1, sample, QWP2, P2 must all be OUT of the beam path.\n"
        "  - P1 will be rotated through a full 360 degree sweep."
    ),
    2: (
        "Case 2 -- P1 fixed, QWP1 rotating, P2 fixed.\n"
        "  - P1 and P2 must be IN the beam, at their fixed optical angles.\n"
        "  - QWP1 must be IN the beam -- it is what rotates this case.\n"
        "  - QWP2 and sample must be OUT of the beam path.\n"
        "  - This case's fitted 'a2' term is your PSG-arm alignment check --\n"
        "    a large a2 usually means P1/QWP1 aren't at the relative angle\n"
        "    you think they are."
    ),
    3: (
        "Case 3 -- P1 fixed, QWP2 rotating, P2 fixed.\n"
        "  - P1 and P2 must be IN the beam, at their fixed optical angles.\n"
        "  - QWP2 must be IN the beam -- it is what rotates this case.\n"
        "  - QWP1 and sample must be OUT of the beam path.\n"
        "  - This case's fitted 'a2' term is your PSA-arm alignment check,\n"
        "    mirroring Case 2 for QWP2/P2 instead of P1/QWP1."
    ),
    4: (
        "Case 4 -- full PCSCA, air (no sample), QWP1:QWP2 coupled 5:1.\n"
        "  - P1, QWP1, QWP2, P2 must ALL be IN the beam.\n"
        "  - Sample must be OUT of the beam path -- air is the 'sample' for\n"
        "    this case, standing in for an identity Mueller matrix.\n"
        "  - QWP1 is the driven axis; QWP2 will be commanded to exactly 5x\n"
        "    QWP1's optical angle at every step. Before running this case,\n"
        "    you should already have physically confirmed the QWP1/QWP2\n"
        "    rotation-direction sign convention (see README's 'Sign\n"
        "    convention' section) -- getting this wrong silently inverts\n"
        "    the coupling without crashing anything."
    ),
}


def require_confirmation(case_number: int) -> None:
    """Print the required physical setup for ``case_number`` and block
    until the operator types 'y'. Any other input (including a blank
    line) re-prints the prompt rather than proceeding -- there is no
    default-yes shortcut, since a mis-typed Enter here is exactly the
    kind of mistake this gate exists to catch."""

    if case_number not in CASE_MESSAGES:
        raise ValueError(f"No hardware-gate message defined for case {case_number}.")

    print("=" * 78)
    print(CASE_MESSAGES[case_number])
    print("=" * 78)
    while True:
        answer = input("Confirm the physical setup above is correct? [y/n]: ").strip().lower()
        if answer == "y":
            print(f"Case {case_number} hardware setup confirmed by operator.\n")
            return
        if answer == "n":
            print(
                f"Case {case_number} NOT confirmed -- fix the physical setup, "
                "then answer 'y' when ready. (Ctrl-C to abort entirely.)"
            )
            continue
        print("Please type exactly 'y' or 'n'.")
