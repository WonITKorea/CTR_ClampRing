"""Headless UI regression tests for MR-MC240N cleanup safety."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest

from hardware import MR_CONNECTION_PCIE_API, MrMc240nPositionController
from main import ClampTestMachineApp
from tests.test_hardware_position_controller import fake_vendor_library


class PositionUiSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = ClampTestMachineApp()
        self.window.chk_position_monitor.setChecked(True)
        self.window.mr_connection_combo.setCurrentText(MR_CONNECTION_PCIE_API)

    def tearDown(self):
        self.window.stop_position_motion_status_monitor()
        if self.window.position_monitor is not None:
            monitor = self.window.position_monitor
            monitor.stop = Mock(return_value=None)
            monitor.close = Mock(return_value=None)
            monitor._jog_active = False
            monitor._motion_command_may_be_active = False
            self.window.position_motion_may_be_active = False
            self.window.close_position_monitor()
        self.window.chk_position_monitor.setChecked(False)
        self.window.deleteLater()
        self.application.processEvents()

    def test_connection_settings_lock_while_motion_may_be_active(self):
        self.window.position_motion_may_be_active = True

        self.window.update_position_control_state()

        self.assertFalse(self.window.in_mr_board_id.isEnabled())
        self.assertFalse(self.window.btn_mr_connect.isEnabled())
        self.assertTrue(self.window.btn_mr_rapid_stop.isEnabled())

    def test_mr_settings_tab_is_first(self):
        self.assertEqual(self.window.settings_tabs.tabText(0), "MR-MC240N 연결")

    def test_motion_buttons_do_not_require_arm_checkbox(self):
        self.window.chk_mr_motion_arm.setChecked(False)

        self.window.update_position_control_state()

        self.assertTrue(self.window.btn_mr_servo_on.isEnabled())
        self.assertTrue(self.window.btn_mr_home.isEnabled())
        self.assertTrue(self.window.btn_mr_jog_minus.isEnabled())
        self.assertTrue(self.window.btn_mr_jog_plus.isEnabled())

    def test_controller_motion_latch_also_locks_configuration(self):
        monitor = SimpleNamespace(
            _motion_command_may_be_active=True,
        )
        self.window.position_monitor = monitor
        self.window.position_motion_may_be_active = False

        self.window.update_position_control_state()

        self.assertFalse(self.window.in_mr_board_id.isEnabled())
        self.assertFalse(self.window.btn_mr_connect.isEnabled())
        self.assertFalse(self.window.btn_mr_home.isEnabled())

    def test_failed_rapid_stop_retains_controller_and_blocks_new_motion(self):
        monitor = SimpleNamespace(
            _jog_active=False,
            _motion_command_may_be_active=True,
            board_id=0,
            axis_number=1,
            dll_path="",
            auto_start_system=False,
            stop=Mock(side_effect=RuntimeError("simulated stop failure")),
            close=Mock(return_value=None),
        )
        self.window.position_monitor = monitor
        self.window.position_motion_may_be_active = True
        self.window.chk_mr_motion_arm.blockSignals(True)
        self.window.chk_mr_motion_arm.setChecked(True)
        self.window.chk_mr_motion_arm.blockSignals(False)

        closed = self.window.close_position_monitor()

        self.assertFalse(closed)
        self.assertIs(self.window.position_monitor, monitor)
        self.assertTrue(self.window.position_controller_close_failed)
        self.assertFalse(self.window.chk_mr_motion_arm.isEnabled())
        self.assertFalse(self.window.btn_mr_home.isEnabled())
        monitor.close.assert_not_called()

    def test_jog_release_stops_an_uncertain_jog_dispatch(self):
        monitor = SimpleNamespace(
            _jog_active=False,
            _motion_command_may_be_active=True,
            stop_jog=Mock(return_value=None),
        )
        self.window.position_monitor = monitor
        self.window.position_jog_command_active = False
        self.window.position_motion_may_be_active = True

        with patch.object(
            self.window,
            "get_position_controller",
            return_value=monitor,
        ):
            self.window.stop_position_jog()

        monitor.stop_jog.assert_called_once_with()
        self.assertFalse(self.window.position_jog_command_active)
        self.assertFalse(self.window.position_motion_may_be_active)

    def test_jog_button_holds_motion_until_real_release(self):
        # Exercise real Qt press/release events: disabling a pressed button
        # emits released synchronously even while the mouse is still held.
        for batch_enabled in (False, True):
            for direction in (0, 1):
                with self.subTest(batch=batch_enabled, direction=direction):
                    monitor = SimpleNamespace(
                        axis_number=1,
                        _jog_active=False,
                        _motion_command_may_be_active=False,
                    )

                    def start(*_args, **_kwargs):
                        monitor._jog_active = True
                        monitor._motion_command_may_be_active = True

                    def stop(*_args, **_kwargs):
                        monitor._jog_active = False
                        monitor._motion_command_may_be_active = False

                    monitor.start_jog = Mock(side_effect=start)
                    monitor.start_jog_axes = Mock(side_effect=start)
                    monitor.stop_jog = Mock(side_effect=stop)
                    monitor.stop_jog_axes = Mock(side_effect=stop)
                    self.window.position_monitor = monitor
                    axes = tuple(range(1, 7)) if batch_enabled else (1,)
                    button = (
                        self.window.btn_mr_jog_plus
                        if direction == 0 else self.window.btn_mr_jog_minus
                    )
                    opposite = (
                        self.window.btn_mr_jog_minus
                        if direction == 0 else self.window.btn_mr_jog_plus
                    )
                    start_mock = (
                        monitor.start_jog_axes if batch_enabled else monitor.start_jog
                    )
                    stop_mock = (
                        monitor.stop_jog_axes if batch_enabled else monitor.stop_jog
                    )
                    with (
                        patch.object(self.window, "get_position_controller", return_value=monitor),
                        patch.object(self.window, "is_six_axis_batch_enabled", return_value=batch_enabled),
                        patch.object(self.window, "get_position_command_axes", return_value=axes),
                        patch.object(self.window, "begin_position_motion_status_monitor"),
                        patch.object(self.window, "handle_position_command_error") as error,
                    ):
                        self.window.update_position_control_state()
                        try:
                            QTest.mousePress(button, Qt.LeftButton)
                            QTest.qWait(60)
                            stop_mock.assert_not_called()
                            self.assertTrue(button.isEnabled())
                            self.assertTrue(button.isDown())
                            self.assertFalse(opposite.isEnabled())
                            self.assertFalse(self.window.btn_mr_move_relative.isEnabled())
                            self.assertFalse(self.window.in_mr_axis_no.isEnabled())
                            self.assertTrue(self.window.btn_mr_stop.isEnabled())
                            self.assertTrue(self.window.btn_mr_rapid_stop.isEnabled())
                            self.window.update_position_control_state()
                            self.window.start_position_jog(direction)
                            start_mock.assert_called_once()
                            stop_mock.assert_not_called()
                        finally:
                            QTest.mouseRelease(button, Qt.LeftButton)
                        stop_mock.assert_called_once()
                        self.assertFalse(self.window.position_jog_command_active)
                        self.assertFalse(self.window.position_motion_may_be_active)
                        self.assertFalse(self.window.position_active_axes)
                        self.assertTrue(opposite.isEnabled())
                        error.assert_not_called()

    def test_failed_jog_start_immediately_attempts_a_stop(self):
        monitor = SimpleNamespace(
            axis_number=1,
            _jog_active=False,
            _motion_command_may_be_active=False,
            stop_jog=Mock(),
            stop=Mock(),
        )

        def fail_after_possible_dispatch(*_args):
            monitor._motion_command_may_be_active = True
            raise RuntimeError("simulated ambiguous dispatch")

        def confirm_stop():
            monitor._motion_command_may_be_active = False

        monitor.start_jog = Mock(side_effect=fail_after_possible_dispatch)
        monitor.stop_jog.side_effect = confirm_stop
        self.window.position_monitor = monitor
        self.window.position_home_established = True

        with (
            patch.object(
                self.window,
                "get_position_controller",
                return_value=monitor,
            ),
            patch.object(
                self.window,
                "get_position_motion_config",
                return_value={
                    "speed": 100,
                    "acceleration_ms": 10,
                    "deceleration_ms": 10,
                },
            ),
            patch("main.QMessageBox.critical"),
        ):
            self.window.start_position_jog(0)

        monitor.stop_jog.assert_called_once_with()
        monitor.stop.assert_not_called()
        self.assertFalse(self.window.position_jog_command_active)
        self.assertFalse(self.window.position_motion_may_be_active)

    def test_relative_move_is_not_blocked_by_disabled_software_limit(self):
        monitor = SimpleNamespace(
            axis_number=1,
            _jog_active=False,
            _motion_command_may_be_active=False,
            read_axis_status=Mock(),
            move_relative=Mock(),
        )
        self.window.position_monitor = monitor
        self.window.position_home_established = True
        # Hardware limits now provide overtravel protection.
        self.window.position_zero_offset_mm = 50.0

        with (
            patch.object(
                self.window,
                "get_position_controller",
                return_value=monitor,
            ),
            patch.object(
                self.window,
                "get_position_monitor_config",
                return_value={"counts_per_mm": 1000.0},
            ),
            patch.object(
                self.window,
                "get_position_motion_config",
                return_value={
                    "speed": 100,
                    "acceleration_ms": 10,
                    "deceleration_ms": 10,
                    "distance_mm": 1.0,
                },
            ),
            patch.object(
                self.window,
                "read_position_feedback",
                return_value=(146.0, 196_000),
            ),
            patch("main.QMessageBox.critical"),
        ):
            self.window.start_position_relative_move()

        monitor.move_relative.assert_called_once_with(1_000, 100, 10, 10)

    def test_home_validation_uses_current_reference_after_moving_away(self):
        for batch_enabled in (False, True):
            with self.subTest(batch=batch_enabled):
                statuses = {
                    axis: {
                        "position": 50_000,
                        "home_complete": False,
                        "home_required": False,
                        "home_established": True,
                    }
                    for axis in range(1, 7)
                }
                monitor = SimpleNamespace(
                    axis_number=1,
                    read_axis_status=Mock(
                        side_effect=lambda axis=1, **_kwargs: statuses[axis]
                    ),
                )
                self.window.position_home_established = False
                self.window.position_home_established_axes.clear()
                with patch.object(
                    self.window, "is_six_axis_batch_enabled", return_value=batch_enabled
                ):
                    self.window.require_position_home_established(monitor)
                    self.assertTrue(self.window.position_home_established)
                    self.assertIn(1, self.window.position_home_established_axes)

                    # Do not trust a cached home flag after a reset/reference
                    # loss, even if the old home completion signal is present.
                    statuses[1].update(
                        home_complete=True, home_required=True, home_established=False
                    )
                    with self.assertRaisesRegex(RuntimeError, "홈"):
                        self.window.require_position_home_established(monitor)
                    self.assertFalse(self.window.position_home_established)
                    self.assertNotIn(1, self.window.position_home_established_axes)

    def test_six_axis_option_starts_linked_interpolation_for_all_axes(self):
        status_by_axis = {
            axis: {
                "axis": axis,
                "position": axis * 10_000,
                "servo_ready": True,
                "servo_alarm": False,
                "operation_alarm": False,
                "operating": False,
            }
            for axis in range(1, 7)
        }
        monitor = SimpleNamespace(
            axis_number=1,
            _jog_active=False,
            _motion_command_may_be_active=False,
            read_axis_status=Mock(
                side_effect=lambda axis, track_motion=False: status_by_axis[axis]
            ),
            start_six_axis_linear_interpolation=Mock(),
        )

        def latch_batch(*_args, **_kwargs):
            monitor._motion_command_may_be_active = True

        monitor.start_six_axis_linear_interpolation.side_effect = latch_batch
        self.window.position_monitor = monitor
        self.window.chk_mr_six_axis_batch.setChecked(True)

        with (
            patch.object(
                self.window, "get_position_controller", return_value=monitor
            ),
            patch.object(
                self.window,
                "get_position_monitor_config",
                return_value={"counts_per_mm": 1000.0},
            ),
            patch.object(
                self.window,
                "get_position_motion_config",
                return_value={
                    "speed": 100,
                    "acceleration_ms": 10,
                    "deceleration_ms": 10,
                    "distance_mm": 1.0,
                },
            ),
            patch("main.QMessageBox.critical"),
        ):
            self.window.start_position_relative_move()

        monitor.start_six_axis_linear_interpolation.assert_called_once_with(
            {axis: axis * 10_000 + 1_000 for axis in range(1, 7)},
            100,
            10,
            10,
        )
        self.assertEqual(
            self.window.position_active_axes, set(range(1, 7))
        )

    def test_automatic_cycle_uses_linked_interpolation_for_all_six_axes(self):
        status_by_axis = {
            axis: {
                "axis": axis,
                "position": axis * 100,
                "servo_ready": True,
                "servo_alarm": False,
                "operation_alarm": False,
                "operating": False,
                "home_complete": True,
            }
            for axis in range(1, 7)
        }
        monitor = SimpleNamespace(
            axis_number=1,
            _jog_active=False,
            _motion_command_may_be_active=False,
            read_axis_status=Mock(
                side_effect=lambda axis, track_motion=False: status_by_axis[axis]
            ),
            start_six_axis_linear_interpolation=Mock(),
        )

        def latch_batch(*_args, **_kwargs):
            monitor._motion_command_may_be_active = True

        monitor.start_six_axis_linear_interpolation.side_effect = latch_batch
        self.window.position_monitor = monitor
        self.window.chk_mr_six_axis_batch.setChecked(True)
        self.window.live_motion_config = {
            "counts_per_mm": 1_000.0,
            "stroke_span_mm": 2.0,
            "tolerance_mm": 0.01,
            "speed_mm_min": 100,
            "acceleration_ms": 10,
            "deceleration_ms": 20,
        }

        with patch.object(
            self.window, "get_position_controller", return_value=monitor
        ):
            self.window.start_live_motion_move(1.0, "MOVING_TO_MAX")

        monitor.start_six_axis_linear_interpolation.assert_called_once_with(
            {axis: 1_000 for axis in range(1, 7)},
            100,
            10,
            20,
        )
        self.assertEqual(self.window.position_active_axes, set(range(1, 7)))
        self.assertEqual(self.window.position_motion_kind, "linear")

    def test_six_axis_monitor_waits_for_every_axis(self):
        monitor = SimpleNamespace(
            axis_number=1,
            _motion_command_may_be_active=True,
        )
        statuses = {
            axis: {
                "axis": axis,
                "position": 1_000,
                "servo_alarm": False,
                "operation_alarm": False,
                "operating": axis == 6,
                "operation_complete": axis != 6,
                "in_position": axis != 6,
                "home_complete": False,
            }
            for axis in range(1, 7)
        }
        monitor.read_axis_status = Mock(
            side_effect=lambda axis, track_motion=False: statuses[axis]
        )

        def clear_latch():
            monitor._motion_command_may_be_active = False

        monitor._clear_motion_latch = Mock(side_effect=clear_latch)
        self.window.position_monitor = monitor
        self.window.position_motion_may_be_active = True
        self.window.begin_position_batch_tracking(
            range(1, 7),
            "relative",
            target_counts={axis: 1_000 for axis in range(1, 7)},
        )
        self.window.begin_position_motion_status_monitor()

        self.window.poll_position_motion_status()
        self.assertTrue(monitor._motion_command_may_be_active)
        monitor._clear_motion_latch.assert_not_called()

        statuses[6]["operating"] = False
        statuses[6]["operation_complete"] = True
        statuses[6]["in_position"] = True
        self.window.poll_position_motion_status()

        monitor._clear_motion_latch.assert_called_once_with()
        self.assertFalse(self.window.position_motion_may_be_active)
        self.assertFalse(self.window.position_active_axes)

    def test_stop_open_failure_does_not_invent_motion_uncertainty(self):
        self.window.position_monitor = None
        self.window.position_jog_command_active = False
        self.window.position_motion_may_be_active = False

        with (
            patch.object(
                self.window,
                "get_position_controller",
                side_effect=RuntimeError("simulated open failure"),
            ),
            patch("main.QMessageBox.critical"),
        ):
            stopped = self.window.stop_position_motion(rapid=True)

        self.assertFalse(stopped)
        self.assertFalse(self.window.position_motion_may_be_active)
        self.assertTrue(self.window.btn_mr_connect.isEnabled())

    def test_test_end_stops_both_interpolation_groups_before_close(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                monitor = MrMc240nPositionController(board_id=0, axis_number=2)
                library = fake_vendor_library()
                monitor.library = library
                monitor._is_open = True
                monitor._motion_command_may_be_active = True
                monitor._motion_kind = "batch_linear"
                stop_axes = []
                checked_axes = []

                def stop_group(_board, _channel, axis, result):
                    stop_axes.append(axis)
                    result._obj.value = int(stop_axes.count(axis) >= 2)
                    return 0

                def read_stopped(_board, _channel, axis, _bit, result):
                    self.assertEqual(stop_axes, [1, 4, 1, 4])
                    checked_axes.append(axis)
                    result._obj.value = 0
                    return 0

                def close_board(_board):
                    self.assertEqual(checked_axes, list(range(1, 7)))
                    return 0

                library.sscDriveStopNoWait.side_effect = stop_group
                library.sscGetStatusBitSignalEx.side_effect = read_stopped
                library.sscClose.side_effect = close_board
                self.window.chk_mr_six_axis_batch.setChecked(True)
                self.window.position_monitor = monitor
                self.window.is_test_running = True
                self.window.live_motion_config = {}
                self.window.live_motion_cycle_active = not completed
                self.window.position_motion_may_be_active = True
                self.window.begin_position_batch_tracking(range(1, 7), "linear")

                self.window.stop_test(completed=completed)

                self.assertEqual(stop_axes, [1, 4, 1, 4])
                library.sscDriveStop.assert_not_called()
                library.sscSetCommandBitSignalEx.assert_not_called()
                library.sscClose.assert_called_once_with(0)
                self.assertIsNone(self.window.position_monitor)
                self.assertFalse(self.window.position_motion_may_be_active)
                self.assertFalse(self.window.position_active_axes)

    def test_test_end_fallback_keeps_all_six_axes_when_tracking_is_empty(self):
        self.window.chk_mr_six_axis_batch.setChecked(True)
        monitor = SimpleNamespace(
            _motion_command_may_be_active=True,
            _motion_kind="",
            _jog_active=False,
            stop=Mock(),
            stop_all_axes=Mock(),
            close=Mock(),
        )

        def stop_axes(*, axis_numbers, rapid, timeout_ms):
            self.assertEqual(list(axis_numbers), list(range(1, 7)))
            if not rapid:
                raise RuntimeError("simulated group stop failure")
            monitor._motion_command_may_be_active = False
            return {"mode": "software forced stop"}

        monitor.stop_all_axes.side_effect = stop_axes
        self.window.position_monitor = monitor
        self.window.is_test_running = True
        self.window.live_motion_config = {}
        self.window.live_motion_cycle_active = True
        self.window.position_active_axes.clear()

        self.window.stop_test()

        self.assertEqual(
            [entry.kwargs["rapid"] for entry in monitor.stop_all_axes.call_args_list],
            [False, True],
        )
        monitor.stop.assert_not_called()
        monitor.close.assert_called_once()
        self.assertIsNone(self.window.position_monitor)

    def test_test_end_failed_group_stops_retain_controller_for_all_axis_retry(self):
        self.window.chk_mr_six_axis_batch.setChecked(True)
        monitor = SimpleNamespace(
            _motion_command_may_be_active=True,
            _motion_kind="batch_linear",
            _jog_active=False,
            stop=Mock(),
            stop_all_axes=Mock(side_effect=RuntimeError("stop unconfirmed")),
            close=Mock(),
        )
        self.window.position_monitor = monitor
        self.window.is_test_running = True
        self.window.live_motion_config = {}
        self.window.live_motion_cycle_active = True

        self.window.stop_test()

        monitor.stop.assert_not_called()
        monitor.close.assert_not_called()
        self.assertTrue(self.window.position_motion_may_be_active)
        self.assertTrue(self.window.position_controller_close_failed)
        self.assertIs(self.window.position_monitor, monitor)
        self.assertEqual(self.window.position_active_axes, set(range(1, 7)))
        for entry in monitor.stop_all_axes.call_args_list:
            self.assertEqual(list(entry.kwargs["axis_numbers"]), list(range(1, 7)))
        self.assertFalse(self.window.btn_mr_home.isEnabled())

    def test_single_axis_test_end_keeps_selected_axis_stop(self):
        monitor = MrMc240nPositionController(board_id=0, axis_number=5)
        library = fake_vendor_library()
        monitor.library = library
        monitor._is_open = True
        monitor._motion_command_may_be_active = True
        monitor._motion_kind = "relative"
        self.window.position_monitor = monitor
        self.window.is_test_running = True
        self.window.live_motion_config = {}
        self.window.live_motion_cycle_active = True
        self.window.begin_position_batch_tracking((5,), "relative")

        self.window.stop_test()

        library.sscDriveStop.assert_called_once_with(0, 1, 5, 3000)
        library.sscDriveStopNoWait.assert_not_called()
        library.sscClose.assert_called_once_with(0)

    def test_motion_monitor_clears_ui_latch_after_confirmed_completion(self):
        monitor = SimpleNamespace(
            axis_number=1,
            _motion_command_may_be_active=True,
        )

        def complete_motion():
            monitor._motion_command_may_be_active = False
            return {
                "servo_alarm": False,
                "operation_alarm": False,
                "operating": False,
                "operation_complete": True,
                "position": 1_000,
            }

        monitor.read_axis_status = Mock(side_effect=complete_motion)
        self.window.position_monitor = monitor
        self.window.position_motion_may_be_active = True
        self.window.begin_position_motion_status_monitor()

        self.window.poll_position_motion_status()

        self.assertFalse(self.window.position_motion_status_timer.isActive())
        self.assertFalse(self.window.position_motion_may_be_active)
        self.assertTrue(self.window.btn_mr_connect.isEnabled())

    def test_uncertain_open_cleanup_immediately_locks_the_ui(self):
        monitor = SimpleNamespace(
            board_id=0,
            axis_number=1,
            dll_path="",
            auto_start_system=False,
            _is_open=True,
            _jog_active=False,
            _motion_command_may_be_active=False,
            open=Mock(side_effect=RuntimeError("simulated open failure")),
            close=Mock(side_effect=RuntimeError("simulated close failure")),
        )

        with patch("main.MrMc240nPositionController", return_value=monitor):
            with self.assertRaisesRegex(RuntimeError, "reference was retained"):
                self.window.open_position_monitor()

        self.assertIs(self.window.position_monitor, monitor)
        self.assertTrue(self.window.position_controller_close_failed)
        self.assertFalse(self.window.in_mr_board_id.isEnabled())
        self.assertFalse(self.window.btn_mr_home.isEnabled())
        self.assertFalse(self.window.chk_mr_motion_arm.isEnabled())


if __name__ == "__main__":
    unittest.main()
