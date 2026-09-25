"""DAQ preview tests; acquisition and all device I/O are faked."""

import copy
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from main import ClampTestMachineApp


class DaqLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = ClampTestMachineApp()
        self.window.chk_position_monitor.setChecked(False)
        self.window.update_chart = Mock()
        self.task = MagicMock()
        self.task.ai_channels.__len__.return_value = 6
        self.task.in_stream.avail_samp_per_chan = 2
        self.task.timing.samp_clk_rate = 1000.0
        self.task.read.return_value = [[axis / 10, axis / 10] for axis in range(1, 7)]
        self.task_factory = Mock(return_value=self.task)
        self.addCleanup(patch.stopall)
        patch("main.NIDAQMX_AVAILABLE", True).start()
        patch("main.nidaqmx", SimpleNamespace(Task=self.task_factory)).start()
        patch("main.TerminalConfiguration", SimpleNamespace(DIFF="DIFF", RSE="RSE")).start()
        patch("main.AcquisitionType", SimpleNamespace(CONTINUOUS="CONTINUOUS")).start()
        patch("main.READ_ALL_AVAILABLE", -1).start()
        patch.object(self.window, "open_position_monitor", side_effect=AssertionError("No motion-board I/O")).start()

    def tearDown(self):
        self.window.is_test_running = False
        self.window.set_daq_live_enabled(False)
        self.window.timer.stop()
        self.window.deleteLater()
        self.application.processEvents()

    def test_idle_timer_updates_six_channels_and_uses_one_task(self):
        self.window.start_daq_live_monitor()
        QTest.qWait(250)

        self.assertGreaterEqual(self.task.read.call_count, 2)
        self.task_factory.assert_called_once()
        self.task.close.assert_not_called()
        self.assertEqual(self.window.raw_data, [10, 20, 30, 40, 50, 60])
        self.assertEqual(self.window.table.item(5, 4).text(), "0.60000")

        self.task.read.return_value = [[0.75, 0.8]] * 6
        self.window.poll_daq_live()
        self.assertEqual(self.window.table.item(5, 1).text(), "80.00")
        self.assertEqual(self.window.table.item(5, 4).text(), "0.80000")
        self.assertEqual(self.task.read.call_args.kwargs["timeout"], 0.0)
        self.assertFalse(self.window.is_test_running)
        self.assertEqual(self.window.time_series_data, [])

    def test_empty_buffer_returns_without_blocking_or_reopening(self):
        self.task.in_stream.avail_samp_per_chan = 0
        self.window.start_daq_live_monitor()
        for _ in range(3):
            self.window.poll_daq_live()
        self.task.read.assert_not_called()
        self.task_factory.assert_called_once()
        self.task.close.assert_not_called()
        self.assertTrue(self.window.daq_live_timer.isActive())
        self.assertIn("샘플 대기", self.window.lbl_daq_live.text())

    def test_negative_preview_preserves_raw_but_displays_load_magnitude(self):
        self.task.read.return_value = [[value] for value in (-0.1, -0.2, -0.3, -0.4, -0.5, -0.6)]
        self.window.sensor_zeros = [-5.0, 5.0, -10.0, 10.0, 0.0, -60.0]
        self.window.start_daq_live_monitor()
        self.window.poll_daq_live()

        raw = [-10.0, -20.0, -30.0, -40.0, -50.0, -60.0]
        magnitude = [5.0, 25.0, 20.0, 50.0, 50.0, 0.0]
        self.assertEqual(self.window.raw_data, raw)
        for index, value in enumerate(raw):
            self.assertEqual(self.window.table.item(index, 1).text(), f"{value:.2f}")
            self.assertEqual(self.window.table.item(index, 3).text(), f"{magnitude[index]:.2f}")
            self.assertEqual(self.window.table.item(index, 4).text(), f"{value / 100:.5f}")
        self.assertEqual(self.window.table.item(0, 2).text(), "-5.00")
        self.assertEqual(self.window.get_calibrated_data(), magnitude)
        self.assertEqual(self.window.time_series_data, [])
        self.assertEqual(self.window.latest_live_snapshot, [0.0] * 6)

        # Render the actual chart to verify the points no longer collapse to zero.
        ClampTestMachineApp.update_chart(self.window, reset_scale=True)
        radii = self.window.chart.ax.collections[0].get_offsets()[:, 1].tolist()
        self.assertEqual(radii, magnitude)

        self.window.unit = "kgf"
        self.window.update_table()
        for index, value in enumerate(magnitude):
            self.assertAlmostEqual(self.window.get_calibrated_data()[index], value / 9.80665)
        self.assertEqual(self.window.raw_data, raw)

    def test_review_displays_magnitudes_without_rewriting_signed_records(self):
        row = {
            "Time [sec]": 0.1,
            **{f"Axis {axis} Calibrated [N]": -float(axis) for axis in range(1, 7)},
            **{f"Axis {axis} Raw [N]": -float(axis) for axis in range(1, 7)},
        }
        self.window.time_series_data = [row]
        records = copy.deepcopy(self.window.time_series_data)
        self.window.refresh_review_controls()
        radii = self.window.chart.ax.collections[0].get_offsets()[:, 1].tolist()
        self.assertEqual(radii, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertEqual(self.window.time_series_data, records)

    def test_tare_reuses_live_task_and_updates_each_channel_offset(self):
        self.window.start_daq_live_monitor()
        self.window.poll_daq_live()
        with (
            patch("main.QMessageBox.information"),
            patch("main.QMessageBox.warning") as warning,
        ):
            self.window.zero_sensors()
        warning.assert_not_called()
        self.assertEqual(self.window.sensor_zeros, [10, 20, 30, 40, 50, 60])
        self.task.read.return_value = [[axis / 10 + 0.1] for axis in range(1, 7)]
        self.window.poll_daq_live()
        self.task_factory.assert_called_once()
        self.task.close.assert_not_called()
        for axis in range(6):
            self.assertEqual(self.window.table.item(axis, 3).text(), "10.00")

    def seed_completed_test(self):
        self.window.stroke_data_history = [[8.0] * 6, [9.0] * 6]
        self.window.stroke_position_history = [40.0, 50.0]
        self.window.time_series_data = [
            {"Time [sec]": index / 10, "Position [mm]": position,
             **{f"Axis {axis} Calibrated [N]": load for axis in range(1, 7)}}
            for index, position, load in ((1, 40.0, 8.0), (2, 50.0, 9.0))
        ]
        self.window.current_stroke = 2
        self.window.time_elapsed = 0.2
        self.window.test_start_ts = "20260925_160000"
        self.window.test_start_display_time = "2026-09-25 16:00:00"
        self.window.latest_live_snapshot = [9.0] * 6
        self.window.latest_live_position_mm = 50.0
        self.window.latest_live_position_counts = 50_000
        result = self.window.build_current_sample_result()
        self.window.sample_results = [result]
        self.window.current_sample_result_key = result["key"]
        self.window.refresh_sample_results_table()
        self.window.refresh_review_controls()
        self.window.review_slider.setValue(0)
        return {
            name: copy.deepcopy(getattr(self.window, name))
            for name in (
                "stroke_data_history", "stroke_position_history", "time_series_data",
                "current_stroke", "time_elapsed", "test_start_ts", "test_start_display_time",
                "latest_live_snapshot", "latest_live_position_mm", "latest_live_position_counts",
                "sample_results", "current_sample_result_key", "review_indices",
                "review_selected_data_index",
            )
        }

    def test_tare_preserves_records_exports_and_selected_history(self):
        before = self.seed_completed_test()
        graph_before = self.window.chart.ax.collections[0].get_offsets().tolist()
        label_before = self.window.lbl_review_position.text()
        with (
            patch("main.QMessageBox.information"),
            patch("main.QMessageBox.warning") as warning,
            patch.object(self.window, "read_position_feedback") as read_position,
        ):
            self.window.btn_zero.click()
        warning.assert_not_called()
        read_position.assert_not_called()
        self.assertEqual(self.window.btn_zero.text(), "영점조절 (Offset)")
        self.assertEqual(self.window.sensor_zeros, [10, 20, 30, 40, 50, 60])
        for name, value in before.items():
            self.assertEqual(getattr(self.window, name), value, name)
        for axis in range(6):
            self.assertEqual(self.window.table.item(axis, 3).text(), "0.00")
        self.assertEqual(self.window.review_slider.value(), 0)
        self.assertEqual(self.window.lbl_review_position.text(), label_before)
        self.assertEqual(self.window.chart.ax.collections[0].get_offsets().tolist(), graph_before)
        self.window.update_chart.assert_not_called()
        exported = self.window.build_current_sample_result()
        self.assertEqual(exported["stroke_data_history"], before["stroke_data_history"])
        self.assertEqual(exported["time_series_data"], before["time_series_data"])
        self.assertEqual(exported["test_start_ts"], before["test_start_ts"])
        self.assertFalse(self.window.add_current_sample_result(auto=True))

    def test_failed_or_invalid_tare_preserves_offset_and_records(self):
        before = self.seed_completed_test()
        self.window.raw_data = [3.0] * 6
        self.window.sensor_zeros = [-2.0] * 6
        readers = (
            Mock(side_effect=RuntimeError("read failed")),
            Mock(return_value={"value": 10.0, "values": [10.0] * 5}),
            Mock(return_value={"value": 10.0, "values": [10.0] * 5 + [float("nan")]}),
        )
        for reader in readers:
            with (
                self.subTest(reader=reader),
                patch.object(self.window, "read_fc400_measurement", reader),
                patch("main.QMessageBox.warning") as warning,
                patch("main.QMessageBox.information") as success,
            ):
                self.window.zero_sensors()
            warning.assert_called_once()
            success.assert_not_called()
            self.assertEqual(self.window.raw_data, [3.0] * 6)
            self.assertEqual(self.window.sensor_zeros, [-2.0] * 6)
            for name, value in before.items():
                self.assertEqual(getattr(self.window, name), value, name)

    def test_tare_keeps_fallback_export_snapshot_and_position(self):
        self.seed_completed_test()
        self.window.stroke_data_history = []
        self.window.stroke_position_history = []
        with patch("main.QMessageBox.information"):
            self.window.zero_sensors()
        self.window.ensure_export_snapshot()
        self.assertEqual(self.window.stroke_data_history, [[9.0] * 6])
        self.assertEqual(self.window.stroke_position_history, [50.0])

    def test_tare_remains_blocked_during_a_test(self):
        self.window.is_test_running = True
        self.window.sensor_zeros = [-2.0] * 6
        with (
            patch.object(self.window, "read_fc400_measurement") as read,
            patch("main.QMessageBox.warning") as warning,
        ):
            self.window.zero_sensors()
        warning.assert_called_once()
        read.assert_not_called()
        self.assertEqual(self.window.sensor_zeros, [-2.0] * 6)

    def test_preview_does_not_consume_test_samples_and_resumes_after_stop(self):
        self.window.start_daq_live_monitor()
        self.window.poll_daq_live()
        self.task.read.reset_mock()
        self.window.is_test_running = True
        self.window.poll_daq_live()
        self.task.read.assert_not_called()
        self.window.set_daq_live_enabled(False)
        self.task.close.assert_not_called()
        self.window.start_daq_live_monitor()
        self.window.stop_test()
        self.task.close.assert_not_called()
        self.window.poll_daq_live()
        self.task.read.assert_called_once()
        self.task_factory.assert_called_once()

    def test_preview_preserves_recorded_data_and_review_graph(self):
        self.window.time_series_data = [{"Time [sec]": 0.1, "Axis 1 Calibrated [N]": 8}]
        self.window.stroke_data_history = [[8.0] * 6]
        self.window.latest_live_snapshot = [8.0] * 6
        self.window.review_selected_data_index = 0
        records = copy.deepcopy(self.window.time_series_data)
        self.window.start_daq_live_monitor()
        self.window.poll_daq_live()
        self.assertEqual(self.window.time_series_data, records)
        self.assertEqual(self.window.stroke_data_history, [[8.0] * 6])
        self.assertEqual(self.window.latest_live_snapshot, [8.0] * 6)
        self.window.update_chart.assert_not_called()
        self.assertEqual(self.window.raw_data[5], 60.0)
        self.window.show_live_daq_chart()
        self.assertIsNone(self.window.review_selected_data_index)
        self.window.update_chart.assert_called_once()

    def test_read_error_stops_preview_and_marks_values_stale(self):
        self.window.start_daq_live_monitor()
        self.window.poll_daq_live()
        self.task.read.side_effect = RuntimeError("device disconnected")
        with patch("main.QMessageBox.critical") as popup:
            self.window.poll_daq_live()
        popup.assert_not_called()
        self.assertFalse(self.window.daq_live_timer.isActive())
        self.assertIsNone(self.window.ni_daq_task)
        self.task.close.assert_called_once()
        self.assertIn("갱신 중단", self.window.lbl_daq_live.text())
        self.assertEqual(self.window.table.item(0, 4).text(), "--")
        self.assertFalse(self.window.fc400_device_ready)

    def test_acquisition_change_releases_task_before_next_open(self):
        self.window.start_daq_live_monitor()
        self.window.poll_daq_live()
        with patch.object(self.window, "refresh_ni_devices"):
            self.window.in_fc400_sample_rate.setText("500")
            self.window.on_daq_acquisition_changed()
        self.task.close.assert_called_once()
        self.assertIsNone(self.window.ni_daq_task)
        self.window.poll_daq_live()
        self.assertEqual(self.task_factory.call_count, 2)
        self.assertEqual(self.task.timing.cfg_samp_clk_timing.call_args.args, (500.0,))


if __name__ == "__main__":
    unittest.main()
