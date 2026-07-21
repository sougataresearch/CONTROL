"""
================================================================================
 mmie/motors.py -- Thorlabs K10CR2 control via the Kinesis .NET API (pythonnet)
================================================================================
 One class = one physical motor. A MotorBank groups the motors needed for the
 selected measurement mode and performs each lifecycle step ONE MOTOR AT A TIME
 with settling gaps and printed confirmations (your requirement).

 Lifecycle:   detect -> connect -> initialize -> enable -> home -> (zero is
 handled in software: we never re-zero the hardware, we just ADD zero_offset
 when converting optical angles -> motor angles; see angles.optical_to_motor).

 NOTE ON "SETTING ZERO": homing sends the stage to its hardware reference
 (motor 0 deg). Your optical zero is at some other motor angle (e.g. 50 deg).
 We implement "set zero position" by MOVING each motor to its zero_offset after
 homing, so all components physically sit at OPTICAL 0 deg -- and every later
 move command is computed as (zero_offset + optical_angle) % 360.
================================================================================
"""

import os                                   # to extend PATH so Kinesis DLLs load
import sys                                  # to append Kinesis dir to sys.path
import time                                 # for settling sleeps

from . import config                        # all tunables come from config.py

# ------------------------------------------------------------------
# Load the Kinesis .NET assemblies exactly once, at import time.
# Wrapped in try/except so Notebook 0 can report a clean error message
# on machines where Kinesis / pythonnet is missing.
# ------------------------------------------------------------------
KINESIS_OK = False                          # flag other modules can check
KINESIS_ERR = None                          # human-readable failure reason
try:
    import clr                              # pythonnet bridge (pip install pythonnet)
    os.environ["PATH"] = config.KINESIS_PATH + os.pathsep + os.environ["PATH"]  # let Windows find C deps
    sys.path.append(config.KINESIS_PATH)    # let pythonnet find the DLLs
    clr.AddReference("Thorlabs.MotionControl.DeviceManagerCLI")          # device discovery
    clr.AddReference("Thorlabs.MotionControl.GenericMotorCLI")           # generic motor types
    clr.AddReference("Thorlabs.MotionControl.IntegratedStepperMotorsCLI")# K10CRx live here
    from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI # noqa: import after clr refs
    from Thorlabs.MotionControl.IntegratedStepperMotorsCLI import CageRotator  # K10CR1/K10CR2 class
    from System import Decimal              # Kinesis positions are .NET Decimal, not float
    KINESIS_OK = True                       # everything imported cleanly
except Exception as e:                      # keep the reason for Notebook 0 to display
    KINESIS_ERR = repr(e)


def _dec(x):
    """Convert a Python float to a .NET Decimal (what MoveTo expects)."""
    return Decimal(float(x))                # one-liner kept as function for readability


def _to_float(net_decimal):
    """Convert a .NET Decimal position back to a Python float for printing/math."""
    return float(str(net_decimal))          # str() round-trips Decimal safely


def confirm(step_description):
    """
    Your safety gate: NOTHING runs automatically. Prints what is about to
    happen and blocks until you type y/yes. Typing anything else aborts.
    """
    ans = input(f"\n>>> NEXT STEP: {step_description}\n    Proceed? [y/N]: ")  # ask in terminal
    if ans.strip().lower() not in ("y", "yes"):                                # accept y or yes only
        raise KeyboardInterrupt(f"User declined step: {step_description}")     # hard stop, on purpose
    return True                                                                # explicit approval given


class K10CR2Motor:
    """Wrapper around one physical K10CR2 rotation mount."""

    def __init__(self, role_name):
        self.name = role_name                                    # e.g. "PSG_POL" -- used in ALL printouts
        self.serial = config.MOTORS[role_name]["serial"]         # S/N from config table
        self.zero_offset = config.MOTORS[role_name]["zero_offset"]  # motor angle of optical 0
        self.device = None                                       # .NET device handle (set on connect)

    # ---------------------------------------------------------- connect
    def connect(self):
        """Create the CageRotator object and open USB communication."""
        self.device = CageRotator.CreateCageRotator(self.serial) # map serial -> device object
        self.device.Connect(self.serial)                         # open the USB link
        if not self.device.IsSettingsInitialized():              # firmware settings may lag
            self.device.WaitForSettingsInitialized(5000)         # wait up to 5 s for them
        print(f"[{self.name}] CONNECTED  (S/N {self.serial})")   # your printed confirmation

    # ------------------------------------------------------- initialize
    def initialize(self):
        """Load the K10CR2 motor configuration (gearing, units, limits)."""
        cfg = self.device.LoadMotorConfiguration(self.serial)    # pull config profile for this S/N
        cfg.DeviceSettingsName = config.MOTOR_SETTINGS_NAME      # "K10CR2" (check Kinesis GUI if it errors)
        cfg.UpdateCurrentConfiguration()                         # push settings into the device object
        print(f"[{self.name}] INITIALIZED (settings: {config.MOTOR_SETTINGS_NAME})")

    # ----------------------------------------------------------- enable
    def enable(self):
        """Start status polling and energize the stepper coils."""
        self.device.StartPolling(config.POLLING_RATE_MS)         # begin background status updates
        time.sleep(0.25)                                         # tiny gap so polling is alive
        self.device.EnableDevice()                               # energize the motor
        time.sleep(config.ENABLE_SETTLE_S)                       # let internal relays latch
        print(f"[{self.name}] ENABLED")                          # printed confirmation

    # ------------------------------------------------------------- home
    def home(self):
        """Send the stage to its hardware reference mark (motor 0 deg)."""
        print(f"[{self.name}] HOMING... (please wait)")          # announce before the blocking call
        self.device.Home(config.HOMING_TIMEOUT_MS)               # BLOCKS until homed or timeout
        print(f"[{self.name}] HOMED. Motor position = {self.position():.3f} deg")

    # -------------------------------------------------- go to optical 0
    def go_to_optical_zero(self):
        """Move to the motor angle where this component reads OPTICAL 0 deg."""
        print(f"[{self.name}] Moving to optical zero (motor {self.zero_offset:.2f} deg)...")
        self.device.MoveTo(_dec(self.zero_offset), config.MOVE_TIMEOUT_MS)  # blocking absolute move
        print(f"[{self.name}] AT OPTICAL ZERO. Motor position = {self.position():.3f} deg")

    # ------------------------------------------------- move (optical!) 
    def move_to_optical(self, optical_angle):
        """
        THE move command used during measurements. Takes an OPTICAL angle,
        adds zero_offset, wraps past 360 (your 370 -> 10 rule), then moves.
        BLOCKS until the motor reports the move is finished (Kinesis handles the wait).
        """
        from .angles import optical_to_motor                     # local import avoids circular refs
        motor_angle = optical_to_motor(optical_angle, self.zero_offset)  # (offset + optical) % 360
        print(f"[{self.name}] optical {optical_angle:7.2f} deg  ->  motor {motor_angle:7.2f} deg ... ", end="")
        self.device.MoveTo(_dec(motor_angle), config.MOVE_TIMEOUT_MS)    # blocking absolute move
        print(f"done (at {self.position():.3f})")                        # confirm settled position

    # --------------------------------------------------------- position
    def position(self):
        """Current MOTOR angle in degrees as a plain Python float."""
        return _to_float(self.device.Position)                   # .NET Decimal -> float

    # --------------------------------------------------------- shutdown
    def shutdown(self):
        """Clean release of the USB port (prevents 'device busy' next run)."""
        try:
            self.device.StopPolling()                            # stop background polling thread
            self.device.Disconnect()                             # close the USB link
            print(f"[{self.name}] DISCONNECTED")                 # printed confirmation
        except Exception as e:                                   # never let cleanup crash the run
            print(f"[{self.name}] shutdown warning: {e}")


class MotorBank:
    """
    Groups the motors required by the SELECTED MODE and executes each lifecycle
    step one-motor-at-a-time with inter-motor settling and user confirmations.
    """

    def __init__(self, mode):
        self.mode = mode                                         # "3x3" / "4x4_discrete" / "4x4_continuous"
        roles = config.MODE_MOTORS[mode]                         # which motors this mode needs
        self.motors = {r: K10CR2Motor(r) for r in roles}         # build wrapper objects (no USB yet)
        print(f"Mode '{mode}' uses {len(roles)} motor(s): {', '.join(roles)}")

    def detect(self):
        """Build the USB device list and report which of OUR serials are present."""
        DeviceManagerCLI.BuildDeviceList()                       # scan the USB bus (do this ONCE)
        found = [str(s) for s in DeviceManagerCLI.GetDeviceList()]  # all Kinesis serials found
        print(f"Kinesis devices detected on USB: {found}")       # raw list for your eyes
        all_ok = True                                            # assume success until proven otherwise
        for m in self.motors.values():                           # check each REQUIRED motor
            ok = m.serial in found                               # is its serial in the scan result?
            print(f"  {m.name:8s} (S/N {m.serial}): {'CONNECTED (visible on USB)' if ok else 'NOT FOUND!'}")
            all_ok = all_ok and ok                               # any miss flips the flag
        if not all_ok:                                           # refuse to continue with missing hardware
            raise RuntimeError("Not all required motors are visible on USB. Check cables/power.")
        return found                                             # returned for logging

    def _staged(self, step_name, fn):
        """
        Run one lifecycle step (connect/init/enable/home/zero) on EVERY motor,
        ONE AT A TIME, with a settling pause between motors, after asking you.
        """
        confirm(f"{step_name} for motors: {', '.join(self.motors)}")  # your permission gate
        for i, m in enumerate(self.motors.values()):             # iterate in dict (config) order
            fn(m)                                                # do the step on this one motor
            if i < len(self.motors) - 1:                         # no pause needed after the last one
                time.sleep(config.INTER_MOTOR_SETTLE_S)          # mechanical-stress settling gap
        print(f"--- {step_name}: ALL MOTORS DONE ---")           # stage-complete confirmation

    # ---- public stage methods, each one confirmation-gated -------------
    def connect_all(self):    self._staged("CONNECT",             lambda m: m.connect())
    def initialize_all(self): self._staged("INITIALIZE settings", lambda m: m.initialize())
    def enable_all(self):     self._staged("ENABLE",              lambda m: m.enable())
    def home_all(self):       self._staged("HOME",                lambda m: m.home())
    def zero_all(self):       self._staged("MOVE TO OPTICAL ZERO",lambda m: m.go_to_optical_zero())

    def full_bringup(self):
        """The complete startup sequence in your specified order."""
        self.detect()                                            # 1. which serials are on USB?
        self.connect_all()                                       # 2. open each device (gated)
        self.initialize_all()                                    # 3. load settings (gated)
        self.enable_all()                                        # 4. energize coils (gated)
        self.home_all()                                          # 5. hardware home (gated)
        self.zero_all()                                          # 6. park at optical 0 (gated)
        print("\n*** BRING-UP COMPLETE: all motors homed and at optical zero. ***")

    def shutdown_all(self):
        """Release every motor (safe to call even after a crash)."""
        for m in self.motors.values():                           # loop all wrappers
            if m.device is not None:                             # only if it ever connected
                m.shutdown()                                     # stop polling + disconnect
