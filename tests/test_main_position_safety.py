"""Headless UI regression tests for MR-MC240N cleanup safety."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from hardware import MR_CONNECTION_PCIE_API
from main import ClampTestMachineApp


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

    def test_relative_move_is_blocked_by_machine_coordinate_upper_limit(self):
        monitor = SimpleNamespace(
            axis_number=1,
            _jog_active=False,
            _motion_command_may_be_active=False,
            read_axis_status=Mock(),
            move_relative=Mock(),
        )
        self.window.position_monitor = monitor
        self.window.position_home_established = True
        # A display offset must never move the fixed board safety boundary.
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

        monitor.move_relative.assert_not_called()

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
