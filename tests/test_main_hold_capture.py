"""Hold-end result capture tests; DAQ and motion hardware are fully faked."""

import copy
import io
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib.pyplot as plt
import pandas as pd
from PyQt5.QtWidgets import QApplication

from main import ClampTestMachineApp


class HoldCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("main.NIDAQMX_AVAILABLE", False).start()
        self.window = ClampTestMachineApp()
        self.window.chk_position_monitor.setChecked(False)
        self.window.update_chart = Mock()
        self.window.is_test_running = True
        self.window.target_strokes = 2
        self.window.live_motion_config = {
            "minimum_mm": 0.0, "maximum_mm": 50.0, "tolerance_mm": 0.01,
            "hold_seconds": 5.0, "target_strokes": 2, "counts_per_mm": 1000.0,
            "load_limit": 1000.0,
        }
        self.window.data_unit = self.window.unit = "N"
        self.clock = patch("main.time.monotonic", return_value=1000.0).start()
        self.controller = Mock(axis_number=1)
        patch.object(self.window, "get_position_controller", return_value=self.controller).start()
        self.read_daq = patch.object(self.window, "read_fc400_measurement").start()
        self.read_position = patch.object(self.window, "read_position_feedback").start()
        self.moves = []
        patch.object(self.window, "start_live_motion_move", side_effect=self.start_move).start()
        patch("main.QMessageBox.information").start()
        patch("main.QMessageBox.critical").start()
        self.window.begin_live_motion_cycle(0.0)

    def tearDown(self):
        self.window.is_test_running = False
        self.window.live_motion_cycle_active = False
        self.window.set_daq_live_enabled(False)
        self.window.timer.stop()
        self.window.deleteLater()
        self.application.processEvents()

    def start_move(self, target, state, current):
        # The result must already be frozen when the return command is issued.
        self.moves.append((state, copy.deepcopy(self.window.stroke_data_history)))
        self.window.test_state = state
        self.window.live_motion_target_mm = target
        self.window.live_motion_deadline = self.clock.return_value + 100.0

    def tick(self, now, position, raw, buffered=None):
        self.clock.return_value = now
        self.read_position.return_value = (position, round(position * 1000))
        self.controller.read_axis_status.return_value = {
            "position": round(position * 1000), "operating": False, "in_position": True,
        }
        self.read_daq.return_value = {
            "value": raw[0], "values": list(raw), "stable": None,
            "voltage": raw[0] / 100, "voltages": [value / 100 for value in raw],
            "samples_by_channel": buffered or [[value] for value in raw],
        }
        self.window.hardware_test_step()

    def capture_hold(self):
        self.window.sensor_zeros = [-2, 4, -6, 8, -10, 12]
        self.tick(1000.0, 49.0, [200.0] * 6)  # Travel peak must not be a result.
        self.tick(1001.0, 50.0, [100.0] * 6)  # Arrival starts the hold timer.
        self.tick(1003.0, 50.0, [150.0] * 6)  # Nor the earlier hold peak.
        self.assertEqual(self.window.stroke_data_history, [])
        self.tick(1006.0, 49.998, [-12, 24, -36, 48, -60, 72])
        return [10, 20, 30, 40, 50, 60]

    def test_result_is_latest_six_channel_hold_sample_before_return(self):
        expected = self.capture_hold()
        self.assertEqual(self.window.stroke_data_history, [expected])
        self.assertEqual(self.window.stroke_position_history, [49.998])
        self.assertEqual(self.moves[-1], ("MOVING_TO_MIN", [expected]))
        self.assertEqual(self.window.raw_data, [-12, 24, -36, 48, -60, 72])
        self.assertEqual(self.window.get_calibrated_data(), expected)
        row = self.window.time_series_data[-1]
        self.assertEqual(row["State"], "HOLDING_MAX")
        self.assertEqual(row["Result Capture"], "HOLD_END")
        self.assertEqual(row["Axis 1 Raw [N]"], -12)
        for axis, value in enumerate(expected, 1):
            self.assertEqual(row[f"Axis {axis} Calibrated [N]"], value)
            self.assertEqual(self.window.table.item(axis - 1, 3).text(), f"{value:.2f}")
        self.assertTrue(all(row["Result Capture"] == "" for row in self.window.time_series_data[:-1]))
        self.tick(1007.0, 20.0, [300.0] * 6)
        self.assertEqual(self.window.stroke_data_history, [expected])

    def test_multiple_strokes_do_not_mix_travel_or_previous_hold_values(self):
        first = self.capture_hold()
        self.window.sensor_zeros = [0.0] * 6
        self.tick(1007.0, 0.0, [400.0] * 6)
        self.assertEqual(self.window.current_stroke, 1)
        self.tick(1008.0, 50.0, [350.0] * 6)
        self.tick(1013.0, 50.001, [7, 8, 9, 10, 11, 12])
        second = [7, 8, 9, 10, 11, 12]
        self.assertEqual(self.window.stroke_data_history, [first, second])
        self.tick(1014.0, 0.0, [200.0] * 6)
        self.assertFalse(self.window.is_test_running)
        self.assertEqual(len(self.window.sample_results), 1)
        result = self.window.sample_results[0]
        self.assertEqual(result["stroke_data_history"], [first, second])
        self.assertEqual(result["final_calibrated_base"], second)
        self.assertEqual(result["stroke_capture_mode"], "hold_end")

    def test_zero_hold_waits_for_post_arrival_sample(self):
        self.window.live_motion_config["hold_seconds"] = 0.0
        self.tick(1000.0, 50.0, [100.0] * 6)
        self.assertEqual(self.window.test_state, "HOLDING_MAX")
        self.assertEqual(self.window.stroke_data_history, [])
        self.tick(1000.1, 50.0, [25.0] * 6)
        self.assertEqual(self.window.stroke_data_history, [[25.0] * 6])
        self.assertEqual(self.window.test_state, "MOVING_TO_MIN")

    def test_aborted_hold_has_no_fabricated_result_but_csv_keeps_raw_series(self):
        self.tick(1000.0, 50.0, [-100.0] * 6)
        self.tick(1002.0, 50.0, [-80.0] * 6)
        self.window.stop_test(completed=False)
        self.window.ensure_export_snapshot()
        self.assertEqual(self.window.stroke_data_history, [])
        self.assertIsNone(self.window.build_current_sample_result())
        with (
            patch("main.QFileDialog.getSaveFileName", return_value=("unused.csv", "")),
            patch.object(pd.DataFrame, "to_csv", autospec=True) as save,
        ):
            self.window.export_csv()
        frame = save.call_args.args[0]
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.iloc[-1]["Axis 1 Raw [N]"], -80.0)
        self.assertEqual(frame.iloc[-1]["Result Capture"], "")
        self.assertNotIn("No", frame.columns)

    def test_buffered_overload_still_trips_before_capture_or_motion(self):
        self.tick(1000.0, 50.0, [100.0] * 6)
        self.read_position.reset_mock()
        with patch.object(self.window, "trigger_load_limit_emergency_stop") as stop:
            self.tick(1005.0, 50.0, [20.0] * 6, buffered=[[-1200.0, 20.0]] * 6)
        stop.assert_called_once_with(1200.0, 1000.0)
        self.read_position.assert_not_called()
        self.assertEqual(self.window.stroke_data_history, [])
        self.assertEqual(self.window.time_series_data[-1]["State"], "LOAD_LIMIT_TRIP")
        self.assertEqual(len(self.moves), 1)

    def test_csv_pdf_and_sample_use_same_hold_values_after_unit_change(self):
        expected = self.capture_hold()
        self.tick(1007.0, 20.0, [300.0] * 6)
        self.window.stop_test(completed=False)
        self.window.change_unit("kgf")
        sample = self.window.build_current_sample_result()
        self.assertEqual(sample["final_calibrated_base"], expected)
        # Inspect exactly the DataFrames passed to CSV, without filesystem I/O.
        with (
            patch("main.QFileDialog.getSaveFileName", return_value=("unused.csv", "")),
            patch.object(pd.DataFrame, "to_csv", autospec=True) as save,
            patch("builtins.open", return_value=io.StringIO()),
        ):
            self.window.export_csv()
        summary = save.call_args_list[0].args[0]
        series = save.call_args_list[1].args[0]
        self.assertEqual(summary.iloc[0]["Capture"], "Hold end")
        self.assertEqual(summary.iloc[0]["Stroke [mm]"], 49.998)
        self.assertEqual(series.iloc[-2]["Result Capture"], "HOLD_END")
        figure = self.window.build_report_figure(sample)
        try:
            table = next(ax for ax in figure.axes if ax.get_label() == "report_data").tables[0]
            polar = next(ax for ax in figure.axes if ax.name == "polar")
            self.assertIn("Hold-end", polar.get_title())
            for axis, value in enumerate(expected, 1):
                converted = value / 9.80665
                self.assertEqual(summary.iloc[0][f"Axis {axis} [kgf]"], round(converted, 2))
                self.assertEqual(table[(1, axis + 1)].get_text().get_text(), f"{converted:.2f}")
                self.assertAlmostEqual(polar.collections[0].get_offsets()[axis - 1, 1], converted)
                self.assertEqual(series.iloc[-2][f"Axis {axis} Calibrated [N]"], value)
        finally:
            plt.close(figure)


if __name__ == "__main__":
    unittest.main()
