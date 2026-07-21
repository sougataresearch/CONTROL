"""IDS Peak camera acquisition, BMP persistence, and integrity checks."""

from __future__ import annotations

import struct
import time
from pathlib import Path

from config import FALLBACK_SENSOR_HEIGHT, FALLBACK_SENSOR_WIDTH, CameraSettings


class CameraError(RuntimeError):
    """Raised when the camera cannot safely acquire/save/verify a frame.
    ``attempts`` records how many tries were actually made."""

    def __init__(self, message: str, attempts: int = 1) -> None:
        super().__init__(message)
        self.attempts = attempts


class CameraSettingsError(CameraError):
    """Raised specifically when the camera rejects the requested exposure or
    frame rate (e.g. a frame rate below the hardware's minimum for the
    current exposure time). Distinct from CameraError so callers can retry
    by asking the operator for new values, instead of aborting the whole
    session — see CameraController.initialize()'s ``ask_settings`` parameter."""


class CameraController:
    """Own the single IDS camera device for the run's lifetime.

    One instance is created per run in 01_main.run_session(). All hardware
    calls are gated by self.dry_run — dry-run mode never imports ids_peak
    and instead produces a synthetic BMP test pattern, so the exact same
    call sequence (discover -> initialize -> acquire/save/verify -> close)
    runs whether or not real hardware is attached.
    """

    def __init__(self, settings: CameraSettings, dry_run: bool = False) -> None:
        self.settings = settings  # config.CameraSettings for this run (exposure, gain, etc.)
        self.dry_run = dry_run
        self.device = None  # live ids_peak Device once opened
        self.data_stream = None  # live ids_peak DataStream once opened
        self.node_map = None  # GenICam node map used for every Get/SetNode call
        self._buffers: list[object] = []  # acquisition buffers, kept for cleanup in close()
        self._acquisition_started = False  # True only after AcquisitionStart succeeds
        self.last_mean_intensity: float | None = None
        self.last_image_stats: dict[str, float | int] = {}
        # Raw pixel array from the most recent save_bmp() call — None in dry-run
        # (no real pixels exist to select an ROI from). Used by select_roi()/
        # roi_mean() in 01_main.capture_camera_references().
        self.last_image_array = None
        # Frame dimensions used for the pre-run disk-space estimate
        # (utils.estimate_disk_bytes()). Real runs get these from the
        # camera's actual configured Width/Height GenICam nodes (set in
        # _apply_acquisition_settings()); dry-run has no device to read them
        # from, so initialize() fills in config.FALLBACK_SENSOR_WIDTH/HEIGHT.
        self.frame_width: int = 0
        self.frame_height: int = 0

    @staticmethod
    def _no_devices(devices) -> bool:
        """Support both IDS collection APIs seen across Peak SDK releases
        (some expose .empty(), older/newer ones only support len())."""

        return bool(devices.empty()) if hasattr(devices, "empty") else len(devices) == 0

    def discover(self) -> tuple[str, str]:
        """Probe and release the camera so IDS Peak Cockpit can be opened next.

        Opens the IDS Peak library just long enough to read the model/serial
        of the first detected device, then closes the library again — this
        deliberately releases the camera handle so the operator can open
        Cockpit afterward without a device-busy conflict. Called from
        01_main.guided_camera_setup(), before any Cockpit prompts.
        """

        if self.dry_run:
            model, serial = "SIMULATED IDS CAMERA", "SIM-CAMERA"
            print(f"Camera detected: {model} (S/N {serial})")
            return model, serial
        from ids_peak import ids_peak

        ids_peak.Library.Initialize()
        try:
            manager = ids_peak.DeviceManager.Instance()
            manager.Update()
            devices = manager.Devices()
            if self._no_devices(devices):
                raise CameraError("No IDS Peak camera was discovered.")
            descriptor = devices[0]
            model = str(descriptor.ModelName())
            serial = str(descriptor.SerialNumber())
            print(f"Camera detected: {model} (S/N {serial})")
            return model, serial
        finally:
            ids_peak.Library.Close()

    def initialize(self, ask_settings=None) -> None:
        """Open the camera for real acquisition and apply experiment settings.

        Real run: opens the device, loads its "Default" GenICam user set as
        a known baseline, then applies self.settings (pixel format, exposure,
        gain, frame rate) via _apply_acquisition_settings(), switches the
        trigger to software mode so images are captured on-demand, allocates
        acquisition buffers, and starts continuous acquisition
        (_start_streaming() — frames sit in the buffer queue until acquire()
        triggers and reads one out).
        Dry-run: just marks applied_* equal to the requested settings.
        Called once from 01_main.run_session(), after guided_camera_setup()
        (i.e. after the operator has closed Cockpit).

        ``ask_settings``, if given, is called with no arguments as
        ask_settings() -> (exposure_us, frame_rate_fps) whenever the camera
        rejects the current exposure/frame-rate combination
        (CameraSettingsError). This lets the operator be re-prompted and the
        settings re-applied WITHOUT reopening the device or data stream —
        homing/connecting the motors never has to be redone just because a
        typed-in exposure/frame-rate value was outside the camera's
        supported range. If ``ask_settings`` is None (or exhausted), the
        CameraSettingsError propagates as before.
        """

        if self.dry_run:
            self.settings.model = "SIMULATED IDS CAMERA"
            self.settings.serial_number = "SIM-CAMERA"
            self.settings.applied_exposure_us = self.settings.exposure_us
            self.settings.applied_frame_rate_fps = self.settings.frame_rate_fps
            self.settings.applied_gain = self.settings.gain
            self.frame_width = FALLBACK_SENSOR_WIDTH
            self.frame_height = FALLBACK_SENSOR_HEIGHT
            print("Camera initialized in dry-run mode.")
            print(f"Pixel format: {self.settings.pixel_format}")
            print(f"Applied exposure: {self.settings.exposure_us / 1000.0:.3f} ms")
            print(f"Applied frame rate: {self.settings.frame_rate_fps:.3f} fps")
            print(f"Applied gain: {self.settings.gain:.3f}")
            return
        from ids_peak import ids_peak
        from ids_peak import ids_peak_ipl_extension
        from ids_peak_ipl import ids_peak_ipl

        self.ids_peak = ids_peak
        self.ids_peak_ipl = ids_peak_ipl
        self.ids_peak_ipl_extension = ids_peak_ipl_extension
        ids_peak.Library.Initialize()
        manager = ids_peak.DeviceManager.Instance()
        manager.Update()
        devices = manager.Devices()
        if self._no_devices(devices):
            raise CameraError("No IDS Peak camera was discovered.")
        self.device = devices[0].OpenDevice(ids_peak.DeviceAccessType_Control)
        self.node_map = self.device.RemoteDevice().NodeMaps()[0]
        self.settings.model = str(self.node_map.FindNode("DeviceModelName").Value())
        self.settings.serial_number = str(
            self.node_map.FindNode("DeviceSerialNumber").Value()
        )
        print(
            f"Camera connected: {self.settings.model} "
            f"(S/N {self.settings.serial_number})"
        )
        streams = self.device.DataStreams()
        if not streams:
            raise CameraError("Camera exposes no data stream.")
        self.data_stream = streams[0].OpenDataStream()

        while True:
            try:
                self._apply_acquisition_settings()
                break
            except CameraSettingsError as exc:
                if ask_settings is None:
                    raise
                print(f"Camera rejected the requested settings: {exc}")
                self.settings.exposure_us, self.settings.frame_rate_fps = ask_settings()

        self._start_streaming()

    def _apply_acquisition_settings(self) -> None:
        """Load the camera's "Default" user set, then apply pixel format,
        exposure, gain, and frame rate from self.settings, reading back what
        the camera actually accepted into settings.applied_* (hardware can
        quantize a requested value).

        Safe to call more than once on the same already-open device/data
        stream — used by initialize()'s retry loop when the operator's
        exposure/frame-rate values are rejected (CameraSettingsError). Does
        NOT touch buffers, triggers, or acquisition state, so a retry never
        risks double-allocating buffers or double-starting acquisition.
        """

        # Begin from a known camera configuration before applying experiment values.
        try:
            self._set_node("UserSetSelector", "Default")
            command = self.node_map.FindNode("UserSetLoad")
            command.Execute()
            command.WaitUntilDone()
            print("Camera settings reset to factory default before applying this experiment's values.")
        except Exception as exc:
            print(f"Camera default-user-set warning: {exc}")

        self._set_node("PixelFormat", self.settings.pixel_format)
        # Ground truth for the disk-space estimate: the camera's own
        # configured frame size, not a guessed constant (see
        # config.FALLBACK_SENSOR_WIDTH/HEIGHT's docstring for why this
        # matters — two hardcoded guesses in this project disagreed).
        self.frame_width = int(self._read_node("Width"))
        self.frame_height = int(self._read_node("Height"))
        try:
            self._set_node("ExposureTime", self.settings.exposure_us)
        except Exception as exc:
            raise CameraSettingsError(f"Could not apply requested exposure time: {exc}") from exc
        try:
            self._set_node("Gain", self.settings.gain)
        except Exception as exc:
            print(f"Camera gain warning: {exc}")
        try:
            self._set_node("AcquisitionFrameRateEnable", True)
        except Exception:
            # Some cameras expose AcquisitionFrameRate directly without an enable node.
            pass
        try:
            self._set_node("AcquisitionFrameRate", self.settings.frame_rate_fps)
        except Exception as exc:
            raise CameraSettingsError(f"Could not apply requested frame rate: {exc}") from exc

        self.settings.applied_exposure_us = float(self._read_node("ExposureTime"))
        self.settings.applied_frame_rate_fps = float(
            self._read_node("AcquisitionFrameRate")
        )
        try:
            self.settings.applied_gain = float(self._read_node("Gain"))
        except Exception:
            self.settings.applied_gain = None
        print(f"Pixel format: {self.settings.pixel_format}")
        print(f"Requested exposure: {self.settings.exposure_us / 1000.0:.3f} ms")
        print(
            f"Applied exposure: "
            f"{self.settings.applied_exposure_us / 1000.0:.3f} ms"
        )
        print(f"Requested frame rate: {self.settings.frame_rate_fps:.3f} fps")
        print(
            f"Applied frame rate: "
            f"{self.settings.applied_frame_rate_fps:.3f} fps"
        )
        if self.settings.applied_gain is not None:
            print(f"Applied gain: {self.settings.applied_gain:.3f}")

    def _start_streaming(self) -> None:
        """Switch to software triggering, allocate/queue acquisition
        buffers, lock acquisition parameters, and start continuous
        acquisition. Called once from initialize(), only after
        _apply_acquisition_settings() has succeeded."""

        self._set_node("TriggerSelector", "ExposureStart")
        self._set_node("TriggerSource", "Software")
        self._set_node("TriggerMode", "On")

        payload_size = int(self.node_map.FindNode("PayloadSize").Value())
        buffer_count = max(int(self.data_stream.NumBuffersAnnouncedMinRequired()), 3)
        for _ in range(buffer_count):
            buffer = self.data_stream.AllocAndAnnounceBuffer(payload_size)
            self.data_stream.QueueBuffer(buffer)
            self._buffers.append(buffer)
        self.node_map.FindNode("TLParamsLocked").SetValue(1)
        self.data_stream.StartAcquisition(self.ids_peak.AcquisitionStartMode_Default)
        start = self.node_map.FindNode("AcquisitionStart")
        start.Execute()
        start.WaitUntilDone()
        self._acquisition_started = True
        print("IDS camera initialized for software triggering.")

    def _set_node(self, name: str, value: object) -> None:
        """Write one GenICam node. Strings use SetCurrentEntry (enum nodes
        like PixelFormat/TriggerSource); everything else uses SetValue."""

        node = self.node_map.FindNode(name)
        if isinstance(value, str):
            node.SetCurrentEntry(value)
        else:
            node.SetValue(value)

    def _read_node(self, name: str):
        """Read back one GenICam node's current value."""

        return self.node_map.FindNode(name).Value()

    def acquire(self) -> "np.ndarray | None":
        """Fire a software trigger and return one Mono8 frame as a NumPy array.

        Return an owned NumPy image; requeue the SDK buffer immediately
        (via .copy(), so the buffer can be reused by the SDK right away
        instead of being held until the caller is done with the array).
        Blocks up to settings.timeout_ms waiting for the frame. Dry-run
        returns None as a marker — save_bmp() generates a synthetic pattern
        instead of using this return value. Called from acquire_save_verify().
        """

        if self.dry_run:
            # Dry-run returns a marker; save_bmp creates a dependency-free test pattern.
            return None
        import numpy as np

        trigger = self.node_map.FindNode("TriggerSoftware")
        trigger.Execute()
        trigger.WaitUntilDone()
        buffer = self.data_stream.WaitForFinishedBuffer(self.settings.timeout_ms)
        try:
            image = self.ids_peak_ipl_extension.BufferToImage(buffer)
            mono8 = image.ConvertTo(self.ids_peak_ipl.PixelFormatName_Mono8)
            return mono8.get_numpy_2D().copy()
        finally:
            self.data_stream.QueueBuffer(buffer)

    def save_bmp(self, image, path: Path) -> None:
        """Write ``image`` to ``path`` as an uncompressed BMP and compute/print
        min, max, mean, and saturated-pixel (value == 255) statistics.

        Prints an "image-quality warning" if the mean falls outside
        [settings.mean_too_dark, settings.mean_too_bright] — this does NOT
        raise or block the run, it is advisory only. No exposure/gain/dark
        correction is ever applied to the saved pixels (see README "Camera
        preparation"). Called from acquire_save_verify() and directly from
        01_main.capture_camera_references() for the bright/dark reference shots.
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        if self.dry_run:
            self._write_simulated_bmp(path)
            self.last_mean_intensity = 125.0
            self.last_image_array = None
            self.last_image_stats = {
                "minimum": 1,
                "maximum": 250,
                "mean": 125.0,
                "saturated_pixels": 0,
                "saturated_percent": 0.0,
            }
            self._print_image_stats()
            return
        import cv2

        self.last_image_array = image
        self.last_mean_intensity = float(image.mean())
        saturated_pixels = int((image == 255).sum())
        self.last_image_stats = {
            "minimum": int(image.min()),
            "maximum": int(image.max()),
            "mean": self.last_mean_intensity,
            "saturated_pixels": saturated_pixels,
            "saturated_percent": saturated_pixels * 100.0 / int(image.size),
        }
        self._print_image_stats()
        if self.last_mean_intensity < self.settings.mean_too_dark:
            print(
                f"Image-quality warning: mean {self.last_mean_intensity:.2f}; "
                "frame may be black."
            )
        if self.last_mean_intensity > self.settings.mean_too_bright:
            print(
                f"Image-quality warning: mean {self.last_mean_intensity:.2f}; "
                "frame may be saturated."
            )
        if not cv2.imwrite(str(path), image):
            raise CameraError(f"OpenCV could not write {path}.")

    def _print_image_stats(self) -> None:
        """Print the min/max/mean/saturated-pixel line stored on
        self.last_image_stats by the most recent save_bmp() call."""

        stats = self.last_image_stats
        print(
            "Image statistics — "
            f"min: {stats['minimum']}, max: {stats['maximum']}, "
            f"mean: {stats['mean']:.3f}, "
            f"pixels at 255: {stats['saturated_pixels']} "
            f"({stats['saturated_percent']:.6f}%)"
        )

    def verify_image(self, path: Path) -> None:
        """Confirm the just-saved file is a real, decodable image, not a
        truncated/corrupt write. Checks file size, BMP signature ("BM") in
        dry-run, and a full cv2.imread() decode for real runs. Raises
        CameraError (triggering a retry) on any failure. Called from
        acquire_save_verify() right after save_bmp()."""

        if not path.is_file() or path.stat().st_size < 100:
            raise CameraError(f"Image is absent or too small: {path}")
        if self.dry_run:
            if path.read_bytes()[:2] != b"BM":
                raise CameraError(f"Dry-run image has an invalid BMP signature: {path}")
            return
        import cv2

        decoded = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if decoded is None or decoded.size == 0:
            raise CameraError(f"Image cannot be decoded: {path}")

    @staticmethod
    def _write_simulated_bmp(path: Path, width: int = 640, height: int = 480) -> None:
        """Write a valid 8-bit grayscale BMP using only the standard library
        (no cv2/numpy dependency), so dry-run mode works even without the
        camera SDK/OpenCV installed. Pixel values are a simple diagonal
        gradient pattern — content is meaningless, only used to exercise the
        save/verify/decode pipeline."""

        # Rows are padded to four-byte boundaries by the BMP format.
        row_size = (width + 3) & ~3
        pixels = bytearray()
        for y in range(height):
            row = bytes(((x + y) % 256 for x in range(width)))
            pixels.extend(row)
            pixels.extend(b"\0" * (row_size - width))
        palette = b"".join(bytes((value, value, value, 0)) for value in range(256))
        pixel_offset = 14 + 40 + len(palette)
        file_size = pixel_offset + len(pixels)
        file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
        info_header = struct.pack(
            "<IiiHHIIiiII", 40, width, height, 1, 8, 0, len(pixels), 2835, 2835, 256, 0
        )
        path.write_bytes(file_header + info_header + palette + pixels)

    def acquire_save_verify(self, path: Path) -> int:
        """Acquire a verified image and return the successful attempt number.

        Combines acquire() -> save_bmp() -> verify_image() into one retried
        unit: on any exception, waits settings.retry_backoff_s and tries
        again, up to settings.max_retries extra times, before raising
        CameraError. This is the single choke point every image capture in
        the project goes through (measurement_engine.run_discrete() per
        state, and 01_main.capture_camera_references() for the references).
        """

        last_error: Exception | None = None
        total_attempts = self.settings.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                self.save_bmp(self.acquire(), path)
                self.verify_image(path)
                return attempt
            except Exception as exc:
                last_error = exc
                print(f"Camera attempt {attempt}/{total_attempts} failed: {exc}")
                if attempt < total_attempts:
                    time.sleep(self.settings.retry_backoff_s)
        raise CameraError(
            f"Image acquisition failed after {total_attempts} attempts: {last_error}",
            attempts=total_attempts,
        )

    def test_frame(self, path: Path) -> dict[str, float | int]:
        """Capture, save, and verify one reference image at ``path``, then
        return its stats dict. Used for the bright/dark polarization
        reference shots in 01_main.capture_camera_references()."""

        self.acquire_save_verify(path)
        print(f"Camera test frame verified: {path}")
        return dict(self.last_image_stats)

    def close(self) -> None:
        """Stop acquisition, unlock parameters, flush/revoke buffers, and
        close the IDS Peak library. Called from the ``finally`` block in
        01_main.run_session() so it always runs, even after an error.

        Never raises: every step below is best-effort and independently
        guarded, mirroring MotorController.close(). If ``initialize()``
        failed before acquisition actually started (e.g. a rejected
        exposure/frame-rate value with no ask_settings retry, or no camera
        found), self._acquisition_started is still False, so the
        Acquisition-Stop calls — which the SDK rejects with a BadAccess
        error on a stream that was never started — are skipped entirely.
        A failure here must never prevent motors.close() from running
        afterward in 01_main.run_session().
        """

        if self.dry_run:
            print("Camera disconnected (dry-run).")
            return
        try:
            if self._acquisition_started:
                if self.node_map is not None:
                    try:
                        stop = self.node_map.FindNode("AcquisitionStop")
                        stop.Execute()
                        stop.WaitUntilDone()
                    except Exception as exc:
                        print(f"Camera acquisition-stop warning: {exc}")
                if self.data_stream is not None:
                    try:
                        self.data_stream.StopAcquisition(self.ids_peak.AcquisitionStopMode_Default)
                    except Exception as exc:
                        print(f"Camera stream-stop warning: {exc}")
                if self.node_map is not None:
                    try:
                        self.node_map.FindNode("TLParamsLocked").SetValue(0)
                    except Exception as exc:
                        print(f"Camera TLParamsLocked warning: {exc}")
                self._acquisition_started = False
            if self.data_stream is not None:
                try:
                    self.data_stream.Flush(self.ids_peak.DataStreamFlushMode_DiscardAll)
                    for buffer in self._buffers:
                        self.data_stream.RevokeBuffer(buffer)
                except Exception as exc:
                    print(f"Camera buffer cleanup warning: {exc}")
        finally:
            if hasattr(self, "ids_peak"):
                try:
                    self.ids_peak.Library.Close()
                except Exception as exc:
                    print(f"IDS Peak library close warning: {exc}")
            # Clear references so nothing in this object still looks "open" —
            # ids_peak.Library.Close() already released the device/data stream
            # at the SDK level (the same release mechanism discover() uses so
            # Cockpit can open the camera without a device-busy conflict); this
            # just makes that fact visible in the object's own state too.
            self._buffers = []
            self.data_stream = None
            self.node_map = None
            self.device = None
        print("Camera fully released — safe to open in Cockpit or reconnect for the next session.")

    def emergency_stop(self) -> None:
        """Best-effort immediate acquisition stop for the SIGINT path.
        Skips the Acquisition-Stop calls if acquisition was never actually
        started, for the same reason as close(). Never raises."""

        if self.dry_run or self.data_stream is None or not self._acquisition_started:
            return
        try:
            if self.node_map is not None:
                stop = self.node_map.FindNode("AcquisitionStop")
                stop.Execute()
                stop.WaitUntilDone()
            self.data_stream.StopAcquisition(self.ids_peak.AcquisitionStopMode_Default)
        except Exception as exc:
            print(f"Camera emergency-stop warning: {exc}")


def select_roi(image, window_size: int, stride: int, min_mean: float) -> tuple[int, int, int, int]:
    """Find the flattest sufficiently-bright square region in ``image``.

    Slides a ``window_size`` x ``window_size`` window across the frame with
    step ``stride``, scoring each candidate by standard deviation (lower =
    flatter). Candidates whose mean is below ``min_mean`` (too dark) or that
    contain any saturated (255) pixel are rejected outright — the winner is
    the flattest region among what remains, not the brightest or the most
    central, so a genuine flat-illuminated plateau is preferred over the
    peak of an uneven (e.g. Gaussian) beam profile. Returns (x, y, width,
    height). Only called on real (non-dry-run) frames — see
    01_main.capture_camera_references().
    """

    height, width = image.shape
    best: tuple[int, int, int, int] | None = None
    best_std: float | None = None
    for y in range(0, height - window_size + 1, stride):
        for x in range(0, width - window_size + 1, stride):
            region = image[y : y + window_size, x : x + window_size]
            mean = float(region.mean())
            if mean < min_mean:
                continue
            if int((region == 255).sum()) > 0:
                continue
            std = float(region.std())
            if best_std is None or std < best_std:
                best_std = std
                best = (x, y, window_size, window_size)
    if best is None:
        raise CameraError(
            "No region met the ROI brightness/saturation criteria; "
            "check illumination or lower CameraSettings.roi_min_mean."
        )
    return best


def roi_mean(image, roi: tuple[int, int, int, int]) -> float:
    """Mean pixel value within ``roi`` = (x, y, width, height)."""

    x, y, width, height = roi
    return float(image[y : y + height, x : x + width].mean())
