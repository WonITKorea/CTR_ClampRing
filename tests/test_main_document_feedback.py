"""Regression tests for the 2026-07-30 CTR layout review comments."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QValidator
from PyQt5.QtWidgets import QApplication, QGroupBox, QLabel, QSizePolicy

from main import (
    AXIS_TRAVEL_MAX_MM,
    MOTION_RAMP_MAX_MS,
    USB_MOTION_SPEED_MAX_MM_MIN,
    ClampTestMachineApp,
)


class DocumentFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = ClampTestMachineApp()

    def tearDown(self):
        self.window.stop_camera_recording(notify=False)
        self.window.stop_position_motion_status_monitor()
        self.window.deleteLater()
        self.application.processEvents()

    def test_numeric_inputs_expose_approved_ranges(self):
        self.assertEqual(
            self.window.in_min_len.validator().top(),
            AXIS_TRAVEL_MAX_MM,
        )
        self.assertEqual(
            self.window.in_speed.validator().top(),
            USB_MOTION_SPEED_MAX_MM_MIN,
        )
        self.assertEqual(
            self.window.in_mr_acceleration_ms.validator().top(),
            MOTION_RAMP_MAX_MS,
        )
        state, _, _ = self.window.in_mr_relative_move_mm.validator().validate(
            "197",
            0,
        )
        self.assertNotEqual(state, QValidator.Acceptable)

    def test_one_time_controls_live_in_the_settings_dialog(self):
        self.assertTrue(
            self.window.settings_dialog.isAncestorOf(self.window.unit_combo)
        )
        self.assertTrue(
            self.window.settings_dialog.isAncestorOf(
                self.window.in_camera_index
            )
        )
        self.assertTrue(
            self.window.settings_dialog.isAncestorOf(
                self.window.mr_connection_combo
            )
        )

    def test_settings_tabs_have_padded_two_column_pages(self):
        self.assertGreaterEqual(
            self.window.settings_dialog.minimumWidth(),
            920,
        )
        self.assertTrue(self.window.settings_tabs.tabBar().expanding())

        for tab_index in range(self.window.settings_tabs.count()):
            page = self.window.settings_tabs.widget(tab_index)
            self.assertNotIsInstance(page, QGroupBox)
            margins = page.layout().contentsMargins()
            self.assertGreaterEqual(margins.left(), 20)
            self.assertGreaterEqual(margins.top(), 20)

        fc400_grid = self.window.in_fc400_daq_channel.parentWidget().layout()
        camera_grid = self.window.in_camera_index.parentWidget().layout()
        connection_grid = self.window.mr_connection_combo.parentWidget().layout()
        for form_grid in (fc400_grid, camera_grid, connection_grid):
            self.assertGreaterEqual(form_grid.columnMinimumWidth(0), 250)
            self.assertGreater(form_grid.columnStretch(1), 0)

        self.window.settings_dialog.show()
        self.application.processEvents()
        tab_bar = self.window.settings_tabs.tabBar()
        font_metrics = tab_bar.fontMetrics()
        for tab_index in range(tab_bar.count()):
            tab_rect = tab_bar.tabRect(tab_index)
            tab_text = tab_bar.tabText(tab_index)
            self.assertGreaterEqual(
                tab_rect.width(),
                font_metrics.horizontalAdvance(tab_text) + 8,
            )
            self.assertGreaterEqual(
                tab_rect.height(),
                font_metrics.height() + 6,
            )
        self.window.settings_dialog.hide()

    def test_main_left_columns_and_system_log_can_expand(self):
        main_layout = self.window.centralWidget().layout()
        left_container = main_layout.itemAt(0).widget()
        left_grid = left_container.layout()
        self.assertGreaterEqual(left_container.minimumWidth(), 820)
        self.assertGreaterEqual(left_grid.columnMinimumWidth(0), 275)
        self.assertGreaterEqual(left_grid.columnMinimumWidth(1), 275)
        self.assertEqual(
            left_grid.columnStretch(2),
            left_grid.columnStretch(0) + left_grid.columnStretch(1),
        )

        for input_widget in (
            self.window.in_report_no,
            self.window.in_min_len,
        ):
            group = input_widget.parentWidget()
            margins = group.layout().contentsMargins()
            self.assertEqual(
                group.sizePolicy().verticalPolicy(),
                QSizePolicy.Expanding,
            )
            self.assertGreaterEqual(group.layout().verticalSpacing(), 10)
            self.assertGreaterEqual(margins.top(), 18)
            self.assertGreaterEqual(input_widget.sizeHint().height(), 30)

        self.assertEqual(
            self.window.system_log.sizePolicy().verticalPolicy(),
            QSizePolicy.Expanding,
        )
        self.assertGreater(self.window.system_log.maximumHeight(), 10_000)
        system_log_group = self.window.system_log.parentWidget()
        position_layout = system_log_group.parentWidget().layout()
        self.assertEqual(
            position_layout.stretch(position_layout.indexOf(system_log_group)),
            1,
        )

    def test_right_column_orders_camera_graph_and_table(self):
        main_layout = self.window.centralWidget().layout()
        right_container = main_layout.itemAt(1).widget()
        right_layout = right_container.layout()
        visual_layout = right_layout.itemAt(0).layout()
        camera_group = self.window.lbl_camera_preview.parentWidget()

        self.assertIs(visual_layout.itemAt(0).widget(), camera_group)
        self.assertIs(visual_layout.itemAt(1).widget(), self.window.chart)
        self.assertIs(right_layout.itemAt(1).widget(), self.window.table)
        for camera_action_button in (
            self.window.btn_camera_toggle,
            self.window.btn_camera_baseline,
            self.window.btn_camera_clear_baseline,
            self.window.btn_camera_record,
        ):
            self.assertGreaterEqual(
                camera_action_button.minimumHeight(),
                38,
            )

    def test_mr_settings_labels_use_readable_font_size(self):
        for settings_group in (
            self.window.in_mr_motion_speed.parentWidget(),
            self.window.mr_connection_combo.parentWidget(),
        ):
            settings_group.ensurePolished()
            labels = settings_group.findChildren(QLabel)
            self.assertTrue(labels)
            self.assertTrue(
                all(label.font().pixelSize() >= 11 for label in labels)
            )

        for status_label in self.window.mr_axis_status_labels:
            status_label.ensurePolished()
            self.assertGreaterEqual(status_label.font().pixelSize(), 11)
            self.assertGreaterEqual(status_label.minimumHeight(), 58)

        for position_control_button in (
            self.window.btn_mr_servo_on,
            self.window.btn_mr_servo_off,
            self.window.btn_mr_home,
            self.window.btn_mr_move_relative,
            self.window.btn_mr_jog_minus,
            self.window.btn_mr_jog_plus,
            self.window.btn_mr_stop,
            self.window.btn_mr_rapid_stop,
            self.window.btn_mr_refresh_status,
        ):
            self.assertGreaterEqual(
                position_control_button.minimumHeight(),
                36,
            )

        for connection_button in (
            self.window.btn_mr_connect,
            self.window.btn_mr_system_start,
            self.window.btn_mr_apply_six_axis,
        ):
            self.assertGreaterEqual(connection_button.minimumHeight(), 38)

        for left_action_button in (
            self.window.btn_open_settings,
            self.window.btn_zero,
        ):
            self.assertGreaterEqual(left_action_button.minimumHeight(), 38)

        for sample_action_button in (
            self.window.btn_add_sample,
            self.window.btn_remove_sample,
            self.window.btn_clear_samples,
        ):
            self.assertGreaterEqual(sample_action_button.minimumHeight(), 36)

        self.assertGreaterEqual(self.window.btn_start.minimumHeight(), 46)

    def test_fc400_buffer_keeps_intermediate_peak_sample(self):
        self.window.ni_daq_task = SimpleNamespace(
            in_stream=SimpleNamespace(avail_samp_per_chan=3),
            read=Mock(return_value=[1.0, 9.0, 2.0]),
        )
        config = {
            "zero_voltage": 0.0,
            "full_scale_voltage": 10.0,
            "full_scale_load": 1000.0,
        }
        with patch.object(
            self.window,
            "get_fc400_config",
            return_value=config,
        ):
            measurement = self.window.read_fc400_measurement()

        self.assertEqual(measurement["samples"], [100.0, 900.0, 200.0])
        self.assertEqual(measurement["value"], 200.0)
        self.assertEqual(measurement["peak_value"], 900.0)

    def test_buffered_overload_stops_before_position_read(self):
        self.window.is_test_running = True
        self.window.live_motion_cycle_active = True
        self.window.live_motion_config = {"load_limit": 5.0}
        self.window.target_strokes = 3
        self.window.data_unit = "N"
        self.window.unit = "N"
        self.window.sensor_zeros = [0.0] * 6
        measurement = {
            "value": 2.0,
            "samples": [1.0, 7.0, 2.0],
            "stable": None,
            "voltage": 2.0,
        }

        with (
            patch.object(
                self.window,
                "read_fc400_measurement",
                return_value=measurement,
            ),
            patch.object(
                self.window,
                "read_position_feedback",
                side_effect=AssertionError(
                    "position read must not delay an overload stop"
                ),
            ) as read_position,
            patch.object(
                self.window,
                "trigger_load_limit_emergency_stop",
            ) as emergency_stop,
        ):
            self.window.hardware_test_step()

        read_position.assert_not_called()
        emergency_stop.assert_called_once_with(7.0, 5.0)
        self.assertEqual(
            self.window.time_series_data[-1]["State"],
            "LOAD_LIMIT_TRIP",
        )

    def test_load_limit_stop_failure_retains_controller(self):
        monitor = SimpleNamespace(
            _jog_active=False,
            _motion_command_may_be_active=False,
            stop_all_axes=Mock(
                side_effect=RuntimeError("axis 4 stop failed")
            ),
            stop=Mock(),
            close=Mock(),
        )
        self.window.position_monitor = monitor
        self.window.position_motion_may_be_active = True
        self.window.load_limit_tripped = True

        closed = self.window.close_position_monitor()

        self.assertFalse(closed)
        self.assertIs(self.window.position_monitor, monitor)
        self.assertTrue(self.window.position_controller_close_failed)
        monitor.stop_all_axes.assert_called_once()
        monitor.stop.assert_not_called()
        monitor.close.assert_not_called()
        self.window.position_monitor = None
        self.window.position_motion_may_be_active = False

    def test_camera_recording_writes_overlay_and_releases_writer(self):
        writer = Mock()
        writer.isOpened.return_value = True
        capture = Mock()
        capture.get.return_value = 30.0
        self.window.camera_capture = capture

        with (
            patch("main.cv2.VideoWriter_fourcc", return_value=1234),
            patch("main.cv2.VideoWriter", return_value=writer),
        ):
            started = self.window.start_camera_recording(
                os.path.join(os.getcwd(), "test_recording.avi")
            )
            self.window.write_camera_recording_frame(
                np.zeros((48, 64, 3), dtype=np.uint8)
            )
            self.window.stop_camera_recording(notify=False)

        self.assertTrue(started)
        writer.write.assert_called_once()
        writer.release.assert_called_once()

    def test_completed_graph_can_be_sorted_by_position(self):
        self.window.time_series_data = [
            {
                "Time [sec]": 0.1,
                "Position [mm]": 20.0,
                **{
                    f"Axis {axis} Calibrated [N]": float(axis)
                    for axis in range(1, 7)
                },
            },
            {
                "Time [sec]": 0.2,
                "Position [mm]": 10.0,
                **{
                    f"Axis {axis} Calibrated [N]": float(axis + 10)
                    for axis in range(1, 7)
                },
            },
        ]
        self.window.is_test_running = False
        self.window.review_mode_combo.setCurrentIndex(1)
        with patch.object(self.window.chart, "plot_data") as plot_data:
            self.window.refresh_review_controls()
            self.window.review_slider.setValue(0)

        self.assertEqual(self.window.review_indices, [1, 0])
        plotted_values = plot_data.call_args.kwargs.get(
            "data",
            plot_data.call_args.args[0],
        )
        self.assertEqual(plotted_values, [11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
        self.assertIn("10.000 mm", self.window.lbl_review_position.text())

    def test_multiple_samples_are_accumulated_and_exported_as_pages(self):
        for sample_number in ("1", "2"):
            self.window.in_sample_no.setText(sample_number)
            self.window.test_start_ts = f"20260730_12000{sample_number}"
            self.window.test_start_display_time = "2026-07-30 12:00:00"
            self.window.stroke_data_history = [[1.0] * 6]
            self.window.stroke_position_history = [50.0]
            self.window.latest_live_snapshot = [1.0] * 6
            self.window.current_sample_result_key = None
            self.assertTrue(
                self.window.add_current_sample_result(auto=True)
            )

        self.assertEqual(len(self.window.sample_results), 2)

        fake_pdf = Mock()
        fake_pdf.__enter__ = Mock(return_value=fake_pdf)
        fake_pdf.__exit__ = Mock(return_value=False)
        figures = [Mock(), Mock()]
        with (
            patch.object(
                self.window,
                "get_report_samples",
                return_value=list(self.window.sample_results),
            ),
            patch.object(
                self.window,
                "build_report_figure",
                side_effect=figures,
            ),
            patch(
                "main.QFileDialog.getSaveFileName",
                return_value=("combined.pdf", "PDF Files (*.pdf)"),
            ),
            patch("main.PdfPages", return_value=fake_pdf),
            patch("main.plt.close"),
            patch("main.QMessageBox.information"),
        ):
            self.window.export_pdf()

        self.assertEqual(fake_pdf.savefig.call_count, 2)


if __name__ == "__main__":
    unittest.main()
