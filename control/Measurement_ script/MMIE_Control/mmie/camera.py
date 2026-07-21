"""
================================================================================
 mmie/camera.py -- IDS U3-3890CP-M-GL (Rev 2.2) via the IDS peak SDK
================================================================================
 Your camera is a uEye+ (GenICam / Vision-standard) model, so it is driven by
 the modern **IDS peak** SDK (NOT the legacy pyueye/uEye API).

 Install on the lab PC:
   1. IDS peak software from ids-imaging.com (includes the "IDS peak Cockpit"
      GUI -- useful for a first manual test of the camera).
   2. pip install ids_peak ids_peak_ipl numpy pillow

 Acquisition model used here (exactly your flowchart):
   configure once -> for each state: SOFTWARE-trigger one frame -> wait until
   the frame arrives -> convert to 8-bit numpy -> quality check -> save BMP ->
   verify file on disk -> only then return control to the measurement loop.
================================================================================
"""

import os                                    # file-size verification after save
import time                                  # (kept for optional retry pauses)
import numpy as np                           # pixel math (mean intensity, dark frame)
from PIL import Image                        # writes standard uncompressed BMP files

from . import config                         # exposure, thresholds, sizes, timeouts

# ------------------------------------------------------------------
# Import IDS peak lazily-safe: Notebook 0 reports a clean message if missing.
# ------------------------------------------------------------------
PEAK_OK = False                              # flag for Notebook 0
PEAK_ERR = None                              # human-readable failure reason
try:
    from ids_peak import ids_peak            # core GenICam device/stream API
    from ids_peak_ipl import ids_peak_ipl    # image processing lib (format conversion)
    from ids_peak import ids_peak_ipl_extension  # bridge: buffer -> ipl image
    PEAK_OK = True                           # both packages imported fine
except Exception as e:
    PEAK_ERR = repr(e)                       # keep reason for diagnostics


class IDSCamera:
    """One software-triggered IDS peak camera producing validated BMP files."""

    def __init__(self):
        self.device = None                   # opened device handle
        self.nodemap = None                  # GenICam parameter tree
        self.stream = None                   # image data stream
        self.dark_frame = None               # optional master dark frame (float64 array)

    # ------------------------------------------------------------ open
    def open(self):
        """Find the first IDS camera on USB3 and open it exclusively."""
        ids_peak.Library.Initialize()                                        # start the peak runtime
        dm = ids_peak.DeviceManager.Instance()                               # singleton device manager
        dm.Update()                                                          # scan for cameras
        if dm.Devices().empty():                                             # nothing found?
            raise RuntimeError("No IDS camera found. Check USB3 cable and IDS peak install.")
        self.device = dm.Devices()[0].OpenDevice(ids_peak.DeviceAccessType_Control)  # exclusive open
        self.nodemap = self.device.RemoteDevice().NodeMaps()[0]              # parameter tree of the camera
        model = self.nodemap.FindNode("DeviceModelName").Value()             # read model string
        serial = self.nodemap.FindNode("DeviceSerialNumber").Value()         # read serial string
        print(f"[CAMERA] CONNECTED: {model} (S/N {serial})")                 # printed confirmation

    # ------------------------------------------------------- configure
    def configure(self):
        """Set pixel format, exposure, gain, and SOFTWARE trigger mode."""
        n = self.nodemap                                                     # shorthand
        n.FindNode("UserSetSelector").SetCurrentEntry("Default")             # start from factory defaults
        n.FindNode("UserSetLoad").Execute()                                  # apply the default user set
        n.FindNode("UserSetLoad").WaitUntilDone()                            # wait for it to finish
        n.FindNode("PixelFormat").SetCurrentEntry(config.CAM_PIXEL_FORMAT)   # e.g. Mono8
        n.FindNode("ExposureTime").SetValue(config.CAM_EXPOSURE_US)          # exposure in microseconds
        try:
            n.FindNode("Gain").SetValue(config.CAM_GAIN)                     # analog gain if available
        except Exception:
            pass                                                             # some models name it differently
        # ---- the three lines that put the camera under FULL code control ----
        n.FindNode("TriggerSelector").SetCurrentEntry("ExposureStart")       # trigger controls exposure start
        n.FindNode("TriggerMode").SetCurrentEntry("On")                      # no free-running frames
        n.FindNode("TriggerSource").SetCurrentEntry("Software")              # WE fire each frame from code
        print(f"[CAMERA] CONFIGURED: {config.CAM_PIXEL_FORMAT}, "
              f"exposure {config.CAM_EXPOSURE_US/1000:.2f} ms, software trigger ON")

    # ---------------------------------------------------- start stream
    def start(self):
        """Allocate buffers and start the (armed but idle) acquisition engine."""
        ds = self.device.DataStreams()                                       # available stream interfaces
        self.stream = ds[0].OpenDataStream()                                 # open the first (only) one
        payload = self.nodemap.FindNode("PayloadSize").Value()               # bytes needed per frame
        n_buf = max(self.stream.NumBuffersAnnouncedMinRequired(), 3)         # driver minimum, at least 3
        for _ in range(n_buf):                                               # allocate + queue buffers
            buf = self.stream.AllocAndAnnounceBuffer(payload)                # reserve memory
            self.stream.QueueBuffer(buf)                                     # hand it to the driver
        # lock transport-layer params during acquisition (required by peak)
        self.nodemap.FindNode("TLParamsLocked").SetValue(1)
        self.stream.StartAcquisition(ids_peak.AcquisitionStartMode_Default)  # engine ready...
        self.nodemap.FindNode("AcquisitionStart").Execute()                  # ...and armed. Waiting for triggers.
        self.nodemap.FindNode("AcquisitionStart").WaitUntilDone()            # confirm the command completed
        print("[CAMERA] STREAM ARMED (waiting for software triggers)")

    # -------------------------------------------------- grab one frame
    def grab_frame(self):
        """Fire ONE software trigger, wait for the frame, return it as a numpy array."""
        self.nodemap.FindNode("TriggerSoftware").Execute()                   # <-- the trigger itself
        self.nodemap.FindNode("TriggerSoftware").WaitUntilDone()             # command accepted by camera
        buf = self.stream.WaitForFinishedBuffer(config.CAM_TIMEOUT_MS)       # BLOCK until frame arrives
        ipl_img = ids_peak_ipl_extension.BufferToImage(buf)                  # raw buffer -> ipl image
        mono8 = ipl_img.ConvertTo(ids_peak_ipl.PixelFormatName_Mono8)        # ensure plain 8-bit mono
        arr = mono8.get_numpy_2D().copy()                                    # COPY before requeueing buffer!
        self.stream.QueueBuffer(buf)                                         # give buffer back to driver
        return arr                                                           # shape (H, W), dtype uint8

    # -------------------------------------------------- dark frame step
    def capture_dark_frame(self):
        """Average DARK_FRAME_COUNT frames with the source blocked -> master dark."""
        input(f"\n>>> Cover the light source now, then press Enter to capture "
              f"{config.DARK_FRAME_COUNT} dark frames...")                   # human-in-the-loop step
        frames = [self.grab_frame().astype(np.float64)                       # grab as float for averaging
                  for _ in range(config.DARK_FRAME_COUNT)]                   # N frames back-to-back
        self.dark_frame = np.mean(frames, axis=0)                            # pixel-wise average
        print(f"[CAMERA] Master dark frame stored. Mean level = {self.dark_frame.mean():.2f} counts")
        input(">>> Uncover the light source, then press Enter to continue...") # restore the beam

    # ----------------------------------------------- quality + BMP save
    def save_bmp(self, arr, filepath, subtract_dark=False):
        """
        Quality-check the frame, save as BMP, then VERIFY the file on disk.
        Returns the mean intensity (also written to the log by the caller).
        """
        mean_val = float(arr.mean())                                         # cheap integrity metric
        if mean_val < config.MEAN_TOO_DARK:                                  # all-black frame?
            print(f"    !! WARNING: mean {mean_val:.2f} -- frame nearly black (cable? shutter? source off?)")
        if mean_val > config.MEAN_TOO_BRIGHT:                                # saturated frame?
            print(f"    !! WARNING: mean {mean_val:.2f} -- frame near saturation (reduce exposure/gain)")
        out = arr                                                            # default: save raw data
        if subtract_dark and self.dark_frame is not None:                    # optional dark subtraction
            out = np.clip(arr.astype(np.float64) - self.dark_frame, 0, 255).astype(np.uint8)
        Image.fromarray(out, mode="L").save(filepath, format="BMP")          # write uncompressed 8-bit BMP
        # ---- verify the file actually landed on disk with a sane size ------
        expected_min = arr.shape[0] * arr.shape[1]                           # payload bytes (header adds more)
        actual = os.path.getsize(filepath)                                   # what is really on disk
        if actual < expected_min:                                            # truncated / half-written?
            raise IOError(f"BMP verify FAILED: {filepath} is {actual} B, expected >= {expected_min} B")
        print(f"    Saved: {os.path.basename(filepath)}  "
              f"({actual/1e6:.1f} MB, mean intensity {mean_val:.1f})")       # your confirmation line
        return mean_val                                                      # caller logs this number

    # ------------------------------------------------------------ close
    def close(self):
        """Stop acquisition and release the camera cleanly."""
        try:
            self.nodemap.FindNode("AcquisitionStop").Execute()               # stop the sensor
            self.stream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)# stop the stream engine
            self.nodemap.FindNode("TLParamsLocked").SetValue(0)              # unlock transport params
            self.stream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)       # drop queued buffers
            for buf in self.stream.AnnouncedBuffers():                       # free every buffer
                self.stream.RevokeBuffer(buf)
        except Exception as e:
            print(f"[CAMERA] close warning: {e}")                            # never crash on cleanup
        finally:
            ids_peak.Library.Close()                                         # shut the peak runtime
            print("[CAMERA] DISCONNECTED")                                   # printed confirmation
