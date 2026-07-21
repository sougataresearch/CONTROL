"""Automated tests for this folder's hardware-independent logic.

Run from INSIDE this folder (imports are flat, matching 01_main.py's own
import style — there is no package structure here):

    python -m unittest test_pure_functions -v

Covers the pure-logic pieces that were, until now, only ever checked by
manually running 01_main.py in dry-run mode and reading the terminal output —
these tests catch regressions in that logic automatically instead. Anything
that talks to real Kinesis/IDS Peak hardware is out of scope here by design;
see calibration.py and a real dry-run/real-hardware session for that.
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
    format_angle,
    parse_angle_spec,
    rename_run_directory,
    sanitize_folder_name,
)
from state_generator import generate_3x3, generate_4x4_discrete
from motor_controller import angular_error_deg
from checkpoint_manager import CheckpointManager
from state_generator import MeasurementState

NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None


class OpticalToMotorTests(unittest.TestCase):
    def test_basic_offset(self):
        self.assertAlmostEqual(optical_to_motor(30, 50), 80.0)

    def test_wraps_past_360(self):
        self.assertAlmostEqual(optical_to_motor(320, 50), 10.0)

    def test_zero_offset_identity(self):
        self.assertAlmostEqual(optical_to_motor(123.4, 0), 123.4)

    def test_negative_optical_wraps_positive(self):
        self.assertAlmostEqual(optical_to_motor(-10, 0), 350.0)


class FormatAngleTests(unittest.TestCase):
    def test_whole_number_has_no_decimal(self):
        self.assertEqual(format_angle(30.0), "30")

    def test_fractional_keeps_trailing_digits(self):
        self.assertEqual(format_angle(22.5), "22.5")

    def test_wraps_before_formatting(self):
        self.assertEqual(format_angle(370.0), "10")


class ParseAngleSpecTests(unittest.TestCase):
    def test_divide_form_excludes_full_circle_point(self):
        self.assertEqual(parse_angle_spec("360/90"), [0.0, 90.0, 180.0, 270.0])

    def test_comma_list(self):
        self.assertEqual(parse_angle_spec("0,30,60"), [0.0, 30.0, 60.0])

    def test_rejects_zero_step(self):
        with self.assertRaises(ValueError):
            parse_angle_spec("360/0")

    def test_rejects_duplicate_after_wrapping(self):
        with self.assertRaises(ValueError):
            parse_angle_spec("0,360")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            parse_angle_spec("")


class SanitizeFolderNameTests(unittest.TestCase):
    def test_passthrough_for_simple_name(self):
        self.assertEqual(sanitize_folder_name("PolarizerA"), "PolarizerA")

    def test_replaces_windows_illegal_characters(self):
        self.assertEqual(sanitize_folder_name('a:b/c\\d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_strips_trailing_dots_and_spaces(self):
        self.assertEqual(sanitize_folder_name("Sample1.. "), "Sample1")

    def test_falls_back_to_default_for_empty_result(self):
        self.assertEqual(sanitize_folder_name("///"), "sample")


class RunDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_creates_expected_subfolders(self):
        run = create_run_directory(self.root, "SampleA")
        for child in ("Images", "Logs", "Config", "DarkFrames", "Reports", "Checkpoints", "Results"):
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


class StateGeneratorTests(unittest.TestCase):
    def test_generate_3x3_produces_full_cross_product(self):
        states = generate_3x3([0, 90], [0, 45, 90])
        self.assertEqual(len(states), 6)
        self.assertEqual(states[0].filename, "0_0.bmp")
        self.assertEqual(states[-1].filename, "90_90.bmp")

    def test_generate_3x3_indices_are_sequential(self):
        states = generate_3x3([0, 90], [0, 90])
        self.assertEqual([state.index for state in states], [0, 1, 2, 3])

    def test_generate_4x4_discrete_keeps_polarizers_fixed(self):
        fixed = {"PSG_Polarizer": 15.0, "PSA_Analyzer": 45.0}
        states = generate_4x4_discrete([0, 90], [0, 90], fixed)
        for state in states:
            self.assertEqual(state.optical_angles["PSG_Polarizer"], 15.0)
            self.assertEqual(state.optical_angles["PSA_Analyzer"], 45.0)

    def test_filename_uses_the_varying_angles_not_motor_angles(self):
        states = generate_4x4_discrete([30], [60], {"PSG_Polarizer": 0.0, "PSA_Analyzer": 0.0})
        self.assertEqual(states[0].filename, "30_60.bmp")


class AngularErrorDegTests(unittest.TestCase):
    def test_no_error_when_equal(self):
        self.assertAlmostEqual(angular_error_deg(10, 10), 0.0)

    def test_shortest_path_across_zero(self):
        # Naive subtraction would give 358; the true distance is 2.
        self.assertAlmostEqual(angular_error_deg(1, 359), 2.0)

    def test_half_circle_is_worst_case(self):
        self.assertAlmostEqual(angular_error_deg(0, 180), 180.0)


class CheckpointManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.checkpoints = CheckpointManager(self.root / "checkpoint.json")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_fresh_checkpoint_starts_before_the_first_state(self):
        self.assertEqual(self.checkpoints.next_index(), 0)

    def test_update_advances_next_index(self):
        state = MeasurementState(2, {"PSG_Polarizer": 0.0}, {"PSG_Polarizer": 0.0}, "0.bmp")
        self.checkpoints.update(state)
        self.assertEqual(self.checkpoints.next_index(), 3)

    def test_complete_marks_run_finished_without_changing_next_index(self):
        state = MeasurementState(1, {}, {}, "x.bmp")
        self.checkpoints.update(state)
        self.checkpoints.complete(total_states=4)
        payload = self.checkpoints.load()
        self.assertTrue(payload["experiment_completed"])
        self.assertEqual(self.checkpoints.next_index(), 2)


@unittest.skipUnless(NUMPY_AVAILABLE, "numpy is not installed in this environment")
class SelectRoiTests(unittest.TestCase):
    """These exercise camera_controller.select_roi()/roi_mean() against a
    real NumPy array — the one piece of this project's recent changes that
    could only be checked by code review where numpy is unavailable (dry-run
    mode skips ROI selection entirely). Run this on a machine with numpy
    installed (the lab PC already requires it) to actually execute these."""

    def test_prefers_flat_region_over_a_brighter_but_uneven_one(self):
        import numpy as np
        from camera_controller import roi_mean, select_roi

        image = np.full((400, 400), 20, dtype=np.uint8)
        image[0:100, 0:100] = 200  # bright but not perfectly flat
        image[0, 0] = 210
        image[200:300, 200:300] = 150  # dimmer but perfectly flat

        roi = select_roi(image, window_size=100, stride=100, min_mean=100.0)
        self.assertEqual(roi, (200, 200, 100, 100))
        self.assertAlmostEqual(roi_mean(image, roi), 150.0)

    def test_rejects_regions_with_saturated_pixels(self):
        import numpy as np
        from camera_controller import select_roi

        image = np.full((200, 200), 200, dtype=np.uint8)
        image[0:100, 0:100] = 255  # saturated, must be excluded
        image[100:200, 100:200] = 200  # flat and unsaturated

        roi = select_roi(image, window_size=100, stride=100, min_mean=50.0)
        self.assertEqual(roi, (100, 100, 100, 100))

    def test_raises_when_nothing_meets_the_brightness_floor(self):
        import numpy as np
        from camera_controller import select_roi

        image = np.zeros((100, 100), dtype=np.uint8)
        with self.assertRaises(Exception):
            select_roi(image, window_size=50, stride=50, min_mean=10.0)


if __name__ == "__main__":
    unittest.main()
