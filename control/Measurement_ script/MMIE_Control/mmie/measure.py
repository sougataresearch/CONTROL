"""
================================================================================
 mmie/measure.py -- the measurement engines
================================================================================
 DISCRETE engine (used by BOTH 3x3 and 4x4_discrete) -- your exact flowchart:

     Move motors -> wait until stopped (MoveTo blocks) -> Settling time 1
     -> Trigger IDS camera -> wait until acquisition done (grab_frame blocks)
     -> quality check + save BMP -> VERIFY file on disk
     -> print "Saved: 30_60.bmp" -> update checkpoint -> Settling time 2
     -> next state.

 Which two motors step through the grid depends on the mode:
     3x3          -> PSG_POL and PSA_POL rotate; no QWPs in the beam path.
     4x4_discrete -> PSG_QWP and PSA_QWP rotate; both polarizers are parked
                     once at FIXED_POL_OPTICAL_ANGLE_4X4 and never move again.

 CONTINUOUS engine (4x4, QWP ratio e.g. 1:5): STUB ONLY for now -- we still
 need to decide image timing (frame-rate-based vs angle-based). See TODO.
================================================================================
"""

import os                                     # join paths for image files
import time                                   # the two settling sleeps

from . import config                          # timings and fixed angles
from .angles import build_combinations, preview_combinations, filename_for
from .motors import confirm                   # the same y/N permission gate
from .runlog import make_run_folder, RunLog, Checkpoint, find_resumable_run


def _stepping_roles(mode):
    """Return (psg_role, psa_role): WHICH two motors step through the grid."""
    if mode == "3x3":                                            # linear polarizers only
        return "PSG_POL", "PSA_POL"
    return "PSG_QWP", "PSA_QWP"                                  # 4x4: the QWPs step


def park_polarizers_for_4x4(bank):
    """In 4x4 modes the two polarizers are set ONCE to a fixed optical angle."""
    ang = config.FIXED_POL_OPTICAL_ANGLE_4X4                     # e.g. optical 0 deg
    confirm(f"Park PSG_POL and PSA_POL at fixed optical {ang} deg (they will NOT move again)")
    bank.motors["PSG_POL"].move_to_optical(ang)                  # each motor applies its OWN
    time.sleep(config.INTER_MOTOR_SETTLE_S)                      # zero_offset internally, so
    bank.motors["PSA_POL"].move_to_optical(ang)                  # motor angles differ per unit
    print("Polarizers parked and fixed for the whole 4x4 run.")


def run_discrete(bank, cam, psg_angles, psa_angles, use_dark=False):
    """
    THE main acquisition loop for 3x3 and 4x4_discrete.
      bank        : MotorBank already brought up (connected/homed/zeroed)
      cam         : IDSCamera already open/configured/armed
      psg_angles  : list of OPTICAL angles for the PSG stepping element
      psa_angles  : list of OPTICAL angles for the PSA stepping element
    """
    mode = bank.mode                                             # "3x3" or "4x4_discrete"
    psg_role, psa_role = _stepping_roles(mode)                   # who actually moves
    combos = build_combinations(psg_angles, psa_angles)          # full PSG x PSA grid
    preview_combinations(combos)                                 # show first few + total (your req.)

    # -------- crash recovery: offer to resume an unfinished run ----------------
    start_index = 0                                              # default: fresh start
    resumable = find_resumable_run(mode)                         # newest unfinished folder?
    if resumable:                                                # found one -> ask the user
        ans = input(f"\nUnfinished run found: {resumable}\nResume it? [y/N]: ")
        if ans.strip().lower() in ("y", "yes"):                  # user wants to resume
            folder = resumable                                   # reuse the old folder
            start_index = Checkpoint(folder).load() + 1          # continue AFTER last done
            print(f"Resuming at state #{start_index + 1} of {len(combos)}")
        else:
            folder = make_run_folder(mode)                       # user declined -> new folder
    else:
        folder = make_run_folder(mode)                           # nothing to resume -> new folder
    print(f"Data folder: {folder}")

    log = RunLog(folder)                                         # master text log
    cp = Checkpoint(folder)                                      # resume file
    log.header(mode, bank.motors, combos,                        # reproducibility block
               extra=f"stepping motors: {psg_role} x {psa_role}, resume_from={start_index}")

    confirm(f"START measurement: {len(combos) - start_index} images to acquire")  # final gate

    for i in range(start_index, len(combos)):                    # -------- MAIN LOOP --------
        g_opt, a_opt = combos[i]                                 # optical angles of this state
        print(f"\n--- State {i + 1}/{len(combos)}:  PSG={g_opt} deg, PSA={a_opt} deg ---")

        bank.motors[psg_role].move_to_optical(g_opt)             # 1. move PSG element (blocks)
        bank.motors[psa_role].move_to_optical(a_opt)             # 2. move PSA element (blocks)
        #                                                        # -> "wait until motors stop" done:
        #                                                        #    MoveTo() only returns when settled
        time.sleep(config.SETTLE_AFTER_MOVE_S)                   # 3. Settling time 1 (optics/mechanics)

        frame = cam.grab_frame()                                 # 4+5. trigger camera, wait for frame
        fname = filename_for(g_opt, a_opt)                       # 6. "30_60.bmp" from OPTICAL angles
        fpath = os.path.join(folder, fname)                      # full path inside run folder
        mean = cam.save_bmp(frame, fpath, subtract_dark=use_dark)# 6+7. save BMP + verify on disk
        #                                                        #      prints "Saved: 30_60.bmp"
        log.write(f"OK {i}: {fname} mean={mean:.2f}")            # 8. append to master log
        cp.save(i, len(combos))                                  # 9. checkpoint AFTER success
        time.sleep(config.SETTLE_AFTER_SAVE_S)                   # 10. Settling time 2 (disk/buffer)

    log.write("RUN COMPLETE")                                    # final log line
    print(f"\n*** MEASUREMENT COMPLETE: {len(combos)} states. Data in {folder} ***")
    return folder                                                # for the analysis notebooks


def run_continuous(bank, cam, ratio_psg=1.0, ratio_psa=5.0):
    """
    4x4 CONTINUOUS mode -- QWPs rotate simultaneously at velocity ratio 1:5,
    until PSG_QWP completes one full 360 deg turn.

    *** NOT IMPLEMENTED YET, ON PURPOSE. ***
    Open decisions we agreed to discuss first:
      (a) capture on FRAME RATE (camera free-runs at N fps, angles read back per
          frame and stored in the log), or
      (b) capture on ANGLE (poll PSG_QWP position, trigger every X deg).
    Implementation sketch when we decide:
      - set velocity via device.SetVelocityParams(accel, max_vel) per motor
        (max_vel of PSA_QWP = 5x that of PSG_QWP)
      - start both with device.MoveContinuous(MotorDirection.Forward)
      - loop: read positions, trigger cam per chosen rule, stop when PSG_QWP
        has swept 360 deg, then device.Stop(timeout) on both.
    """
    raise NotImplementedError("Continuous mode: waiting for image-timing decision (frame-rate vs angle).")
