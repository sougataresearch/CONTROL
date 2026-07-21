"""
================================================================================
 mmie/runlog.py -- run folders, master log file, crash-recovery checkpoint
================================================================================
 Implements improvement ideas 1 (checkpoint/resume) and 5 (structured folders):

   C:\\MMIE_Data\\2026-07-02_Mueller_4x4_discrete_Run_01\\
       |-- 0_0.bmp, 0_30.bmp, ...        (the data)
       |-- run_log.txt                   (human-readable master log)
       |-- checkpoint.json               (machine-readable resume state)
       |-- dark_frame.npy                (optional master dark, if captured)
================================================================================
"""

import os                                   # folder + file handling
import json                                 # checkpoint file format
import datetime                             # timestamps in folder names and log lines

from . import config                        # DATA_ROOT and filenames


def make_run_folder(mode):
    """Create a fresh, numbered, dated run folder and return its path."""
    today = datetime.date.today().isoformat()                     # "2026-07-02"
    run = 1                                                       # start numbering at 01
    while True:                                                   # find first free number
        name = f"{today}_Mueller_{mode}_Run_{run:02d}"            # e.g. ..._4x4_discrete_Run_01
        path = os.path.join(config.DATA_ROOT, name)               # full path under DATA_ROOT
        if not os.path.exists(path):                              # free slot found
            os.makedirs(path)                                     # create it (and parents)
            return path                                           # hand back to caller
        run += 1                                                  # else try the next number


class RunLog:
    """Append-only text log; every line gets a wall-clock timestamp."""

    def __init__(self, run_folder):
        self.path = os.path.join(run_folder, config.LOG_FILENAME) # run_log.txt inside the run folder

    def write(self, message):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")      # HH:MM:SS for each entry
        line = f"[{stamp}] {message}"                             # assemble the log line
        with open(self.path, "a", encoding="utf-8") as f:         # append mode -> crash-safe
            f.write(line + "\n")                                  # one line per event
        return line                                               # so callers can also print it

    def header(self, mode, motors, combos, extra=""):
        """Write the reproducibility block: serials, offsets, timings, grid size."""
        self.write("=" * 60)                                      # visual separator
        self.write(f"RUN START -- mode={mode}, total_states={len(combos)}")
        for m in motors.values():                                 # one line per motor used
            self.write(f"MOTOR {m.name}: S/N={m.serial}, zero_offset={m.zero_offset} deg")
        self.write(f"settle_after_move={config.SETTLE_AFTER_MOVE_S}s, "
                   f"settle_after_save={config.SETTLE_AFTER_SAVE_S}s, "
                   f"exposure={config.CAM_EXPOSURE_US}us, gain={config.CAM_GAIN}")
        if extra:                                                 # anything mode-specific
            self.write(extra)
        self.write("=" * 60)                                      # close the block


class Checkpoint:
    """Tiny JSON file storing the index of the last COMPLETED state."""

    def __init__(self, run_folder):
        self.path = os.path.join(run_folder, config.CHECKPOINT_FILENAME)  # checkpoint.json

    def save(self, last_done_index, total):
        """Overwrite the checkpoint after EVERY successful image save."""
        data = {"last_done_index": last_done_index,               # 0-based index just finished
                "total": total,                                   # grid size, as a sanity check
                "time": datetime.datetime.now().isoformat()}      # when it was written
        with open(self.path, "w", encoding="utf-8") as f:         # rewrite the whole (tiny) file
            json.dump(data, f, indent=2)                          # human-readable JSON

    def load(self):
        """Return last_done_index, or -1 if no checkpoint exists (fresh run)."""
        if not os.path.exists(self.path):                         # first run in this folder?
            return -1                                             # nothing completed yet
        with open(self.path, "r", encoding="utf-8") as f:         # read the JSON back
            return json.load(f).get("last_done_index", -1)        # default -1 if malformed


def find_resumable_run(mode):
    """
    Scan DATA_ROOT for the newest run folder of this mode that has an
    UNFINISHED checkpoint. Returns its path, or None if nothing to resume.
    Used at startup to offer: 'crash detected -- resume from image N?'
    """
    root = config.DATA_ROOT                                       # top-level data folder
    if not os.path.isdir(root):                                   # no data yet at all
        return None
    candidates = sorted(                                          # newest first
        [d for d in os.listdir(root) if f"_Mueller_{mode}_Run_" in d],
        reverse=True)
    for d in candidates:                                          # walk newest -> oldest
        cp = Checkpoint(os.path.join(root, d))                    # its checkpoint handler
        if os.path.exists(cp.path):                               # has a checkpoint file
            with open(cp.path, "r", encoding="utf-8") as f:       # inspect it
                data = json.load(f)
            if data.get("last_done_index", -1) < data.get("total", 0) - 1:  # unfinished?
                return os.path.join(root, d)                      # offer this one for resume
    return None                                                   # nothing resumable found
