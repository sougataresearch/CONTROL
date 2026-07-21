"""Automated tests for this folder's hardware-independent logic.

Run from INSIDE this folder (imports are flat, matching 01_main.py's own
import style — there is no package structure here):

    python -m unittest test_pure_functions -v

Deliberate duplicate in spirit of discreate_angle/test_pure_functions.py,
covering this folder's own equivalents. Anything that talks to real
Kinesis/IDS Peak hardware is out of scope here by design.
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from utils import (
    create_run_directory,
    optical_to_motor,
    parse_ratio,
    rename_run_directory,
    sanitize_folder_name,
)
from motor_controller import angular_error_deg
from checkpoint_manager import CheckpointManager
from rotation_plan import continuous_plan

NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None


class OpticalToMotorTests(unittest.TestCase):
    def test_basic_offset(self):
        self.assertAlmostEqual(optical_to_motor(30, 50), 80.0)

    def test_wraps_past_360(self):
        self.assertAlmostEqual(optical_to_motor(320, 50), 10.0)


class ParseRatioTests(unittest.TestCase):
    def test_valid_ratio(self):
        self.assertEqual(parse_ratio("1:5"), (1, 5))

    def test_rejects_zero(self):
        with self.assertRaises(ValueError):
            parse_ratio("0:5")

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            parse_ratio("1:-5")

    def test_rejects_non_integer(self):
        with self.assertRaises(ValueError):
            parse_ratio("1.5:5")


class ContinuousPlanTests(unittest.TestCase):
    def test_plan_records_ratio_and_fixed_angles(self):
        plan = continuous_plan((1, 5), {"PSG_Polarizer": 0.0, "PSA_Analyzer": 90.0})
        self.assertEqual(plan["relative_revolutions"], {"PSG_QWP": 1, "PSA_QWP": 5})
        self.assertEqual(plan["fixed_polarizers"], {"PSG_Polarizer": 0.0, "PSA_Analyzer": 90.0})


class SanitizeFolderNameTests(unittest.TestCase):
    def test_passthrough_for_simple_name(self):
        self.assertEqual(sanitize_folder_name("PolarizerA"), "PolarizerA")

    def test_replaces_windows_illegal_characters(self):
        self.assertEqual(sanitize_folder_name('a:b/c\\d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_falls_back_to_default_for_all_symbol_input(self):
        self.assertEqual(sanitize_folder_name("///"), "sample")


class RunDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_creates_expected_subfolders(self):
        run = create_run_directory(self.root, "SampleA")
        for child in ("Images", "Logs", "Config", "Reports", "Checkpoints", "Results"):
            self.assertTrue((run / child).is_dir(), f"missing {child}")

    def test_collision_gets_numbered_suffix(self):
        first = create_run_directory(self.root, "SampleA")
        second = create_run_directory(self.root, "SampleA")
        self.assertNotEqual(first, second)
        self.assertTrue(second.name.endswith("_02"))

    def test_rename_moves_folder_and_contents(self):
        run = create_run_directory(self.root, "pending")
        marker = run / "Logs" / "terminal_transcript.txt"
        marker.write_text("hello", encoding="utf-8")
        renamed = rename_run_directory(run, "SampleA")
        self.assertTrue(renamed.name.endswith("SampleA"))
        self.assertEqual((renamed / "Logs" / "terminal_transcript.txt").read_text(encoding="utf-8"), "hello")
        self.assertFalse(run.exists())


class AngularErrorDegTests(unittest.TestCase):
    def test_shortest_path_across_zero(self):
        self.assertAlmostEqual(angular_error_deg(1, 359), 2.0)


class CheckpointManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.checkpoints = CheckpointManager(self.root / "checkpoint.json")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_fresh_checkpoint_has_zero_frames(self):
        payload = self.checkpoints.load()
        self.assertEqual(payload["frames_captured"], 0)
        self.assertFalse(payload["revolution_completed"])

    def test_record_frame_updates_progress(self):
        self.checkpoints.record_frame(9, psg_qwp_angle=45.0, psa_qwp_angle=225.0)
        payload = self.checkpoints.load()
        self.assertEqual(payload["frames_captured"], 10)
        self.assertEqual(payload["last_psg_qwp_angle"], 45.0)
        self.assertFalse(payload["revolution_completed"])

    def test_complete_marks_revolution_finished(self):
        self.checkpoints.record_frame(4, psg_qwp_angle=10.0, psa_qwp_angle=50.0)
        self.checkpoints.complete(total_frames=360)
        payload = self.checkpoints.load()
        self.assertTrue(payload["revolution_completed"])
        self.assertEqual(payload["frames_captured"], 360)


@unittest.skipUnless(NUMPY_AVAILABLE, "numpy is not installed in this environment")
class SelectRoiTests(unittest.TestCase):
    """See discreate_angle/test_pure_functions.py's identical tests for why
    this is the one piece most worth verifying on a machine with numpy
    installed — dry-run mode never exercises this code."""

    def test_prefers_flat_region_over_a_brighter_but_uneven_one(self):
        import numpy as np
        from camera_controller import roi_mean, select_roi

        image = np.full((400, 400), 20, dtype=np.uint8)
        image[0:100, 0:100] = 200
        image[0, 0] = 210
        image[200:300, 200:300] = 150

        roi = select_roi(image, window_size=100, stride=100, min_mean=100.0)
        self.assertEqual(roi, (200, 200, 100, 100))
        self.assertAlmostEqual(roi_mean(image, roi), 150.0)

    def test_rejects_regions_with_saturated_pixels(self):
        import numpy as np
        from camera_controller import select_roi

        image = np.full((200, 200), 200, dtype=np.uint8)
        image[0:100, 0:100] = 255
        image[100:200, 100:200] = 200

        roi = select_roi(image, window_size=100, stride=100, min_mean=50.0)
        self.assertEqual(roi, (100, 100, 100, 100))


if __name__ == "__main__":
    unittest.main()
