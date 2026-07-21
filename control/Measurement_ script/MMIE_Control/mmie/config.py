"""
================================================================================
 mmie/config.py  --  SINGLE SOURCE OF TRUTH for the MMIE control system
================================================================================
 EVERYTHING you may ever need to change lives in THIS file:
   - motor serial numbers
   - zero-offset angles (motor angle that gives optical 0 deg on polarimeter)
   - settling times
   - camera settings
   - data folder location
 The notebooks and other modules only *read* from here. Edit here, re-run.
================================================================================
"""

import os                                  # used to build data folder paths

# ------------------------------------------------------------------
# 1. KINESIS INSTALL LOCATION (Thorlabs software on the lab Windows PC)
# ------------------------------------------------------------------
KINESIS_PATH = r"C:\Program Files\Thorlabs\Kinesis"   # default install path; change if yours differs

# ------------------------------------------------------------------
# 2. MOTOR IDENTITY TABLE
# ------------------------------------------------------------------
# Each motor is referred to EVERYWHERE in the code by its ROLE NAME
# (the dictionary key), never by raw serial number.
#
#   "serial"      : the S/N printed on the K10CR2 body label
#   "zero_offset" : the MOTOR angle (deg) at which the OPTICAL axis of the
#                   component reads 0 deg on your polarimeter.
#                   ==> optical 0 deg  ==  motor at zero_offset
#                   ==> motor_angle = (zero_offset + optical_angle) % 360
#   The zero_offset values below are EXAMPLES from your description
#   (e.g. polarizer optical 0 = motor 50). REPLACE with your measured values.
# ------------------------------------------------------------------
MOTORS = {
    "PSG_POL": {                      # Polarizer in the Polarization State Generator arm
        "serial":      "55542504",    # <-- read from photo of the device label
        "zero_offset": 50.0,          # <-- EXAMPLE. Put your measured value here.
    },
    "PSG_QWP": {                      # Quarter-Wave Plate in the PSG arm
        "serial":      "REPLACE_ME",  # <-- you still need to send me this S/N
        "zero_offset": 60.0,          # <-- EXAMPLE. Put your measured value here.
    },
    "PSA_QWP": {                      # Quarter-Wave Plate in the PSA arm
        "serial":      "REPLACE_ME",  # <-- you still need to send me this S/N
        "zero_offset": 0.0,           # <-- EXAMPLE. Put your measured value here.
    },
    "PSA_POL": {                      # Analyzer (polarizer) in the Polarization State Analyzer arm
        "serial":      "55542004",    # <-- read from photo of the device label
        "zero_offset": 0.0,           # <-- EXAMPLE. Put your measured value here.
    },
}

# Which motors does each measurement mode actually need?
# (Mode is selected FIRST, and only these motors get connected/homed.)
MODE_MOTORS = {
    "3x3":            ["PSG_POL", "PSA_POL"],                       # only the two linear polarizers rotate
    "4x4_discrete":   ["PSG_POL", "PSG_QWP", "PSA_QWP", "PSA_POL"], # polarizers set once (fixed), QWPs stepped
    "4x4_continuous": ["PSG_POL", "PSG_QWP", "PSA_QWP", "PSA_POL"], # polarizers set once (fixed), QWPs spin
}

# Fixed OPTICAL angle for the polarizers in the 4x4 modes
# (you said: polarizers do not rotate during 4x4, they sit at a fixed optical
#  angle, e.g. 0 deg -> the code converts that to the correct motor angle
#  automatically using each motor's own zero_offset).
FIXED_POL_OPTICAL_ANGLE_4X4 = 0.0     # deg, optical. Change if you fix them elsewhere.

# ------------------------------------------------------------------
# 3. KINESIS DEVICE-SETTINGS NAME
# ------------------------------------------------------------------
# In the Kinesis GUI, when you load a stage, a settings profile name is shown
# (e.g. "K10CR2" or "K10CR2 (M)"). If LoadMotorConfiguration complains,
# open Kinesis GUI once, note the exact string, and put it here.
MOTOR_SETTINGS_NAME = "K10CR2"

# ------------------------------------------------------------------
# 4. TIMING / SETTLING (all in seconds unless stated)
# ------------------------------------------------------------------
POLLING_RATE_MS        = 250     # how often Kinesis polls the motor status (ms)
ENABLE_SETTLE_S        = 1.0     # pause after EnableDevice (relays latch)
INTER_MOTOR_SETTLE_S   = 2.0     # pause BETWEEN motors during init/enable/home
                                 # (your "reduce mechanical stress" requirement)
HOMING_TIMEOUT_MS      = 120000  # max wait for one homing operation (2 min)
MOVE_TIMEOUT_MS        = 60000   # max wait for one absolute move (1 min)
SETTLE_AFTER_MOVE_S    = 1.5     # "Settling time 1": optics/mechanics rest AFTER
                                 # motors stop, BEFORE camera trigger
SETTLE_AFTER_SAVE_S    = 0.5     # "Settling time 2": disk/camera buffer rest AFTER
                                 # image is saved, BEFORE next motor move

# ------------------------------------------------------------------
# 5. CAMERA (IDS U3-3890CP-M-GL Rev.2.2  ->  IDS peak SDK)
# ------------------------------------------------------------------
CAM_PIXEL_FORMAT   = "Mono8"     # 8-bit mono; change to "Mono12" later if needed
CAM_EXPOSURE_US    = 10000.0     # exposure time in MICROseconds (10 ms). Tune per setup.
CAM_GAIN           = 1.0         # analog gain (1.0 = off). Tune per setup.
CAM_TIMEOUT_MS     = 5000        # max wait for a triggered frame to arrive
CAM_WIDTH          = 4000        # full sensor width  (used for BMP size validation)
CAM_HEIGHT         = 3000        # full sensor height (used for BMP size validation)
DARK_FRAME_COUNT   = 5           # frames averaged for the optional master dark frame

# Image-quality warning thresholds (data-integrity check after each save)
MEAN_TOO_DARK      = 1.0         # mean pixel value below this  -> warn "black frame?"
MEAN_TOO_BRIGHT    = 250.0       # mean pixel value above this  -> warn "saturated?" (8-bit max 255)

# ------------------------------------------------------------------
# 6. DATA STORAGE
# ------------------------------------------------------------------
DATA_ROOT = r"C:\MMIE_Data"      # top-level folder; run folders are created inside
                                 # e.g. C:\MMIE_Data\2026-07-02_Mueller_4x4_Run_01\
CHECKPOINT_FILENAME = "checkpoint.json"   # crash-recovery file inside each run folder
LOG_FILENAME        = "run_log.txt"       # human-readable master log inside each run folder


def validate_config():
    """Quick sanity check called by Notebook 0. Returns a list of problems (empty = OK)."""
    problems = []                                            # collect issues here
    for name, m in MOTORS.items():                           # loop over all four motors
        if not str(m["serial"]).isdigit():                   # serials must be pure digits
            problems.append(f"{name}: serial '{m['serial']}' is not set (still a placeholder?)")
        if not (0.0 <= float(m["zero_offset"]) < 360.0):     # offsets must be within one turn
            problems.append(f"{name}: zero_offset {m['zero_offset']} outside [0, 360)")
    if not os.path.isdir(KINESIS_PATH):                      # Kinesis must be installed
        problems.append(f"Kinesis folder not found at {KINESIS_PATH}")
    return problems                                          # empty list means all good
