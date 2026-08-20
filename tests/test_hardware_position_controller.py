"""Regression tests for the MR-MC240N PCIe controller wrapper.

All vendor APIs are faked.  These tests must never open a real position board,
start SSCNET, or issue a motion command.
"""

import ctypes
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import hardware
from hardware import (
    MrMc240nPositionController,
    MrMc240nUsbController,
    calculate_soft_limit_stop_margin_mm,
    describe_mr_mc240n_api_error,
    load_mr_mc240n_project,
)


def fake_vendor_library():
    """Return a minimal in-memory substitute for the vendor DLL."""

    def write_ready_status(_board_id, _channel, status_pointer):
        status_pointer._obj.value = (
            MrMc240nPositionController.SSC_STS_CODE_READY_FIN
        )
        return 0

    return SimpleNamespace(
        sscOpen=Mock(name="sscOpen", return_value=0),
        sscClose=Mock(name="sscClose", return_value=0),
        sscGetLastError=Mock(name="sscGetLastError", return_value=0),
        sscSystemStart=Mock(name="sscSystemStart", return_value=0),
        sscReboot=Mock(name="sscReboot", return_value=0),
        sscResetAllParameter=Mock(
            name="sscResetAllParameter", return_value=0
        ),
        sscChangeParameterEx=Mock(
            name="sscChangeParameterEx", return_value=0
        ),
        sscChange2ParameterEx=Mock(
            name="sscChange2ParameterEx", return_value=0
        ),
        sscGetSystemStatusCode=Mock(
            name="sscGetSystemStatusCode",
            side_effect=write_ready_status,
        ),
        sscGetCurrentFbPositionFast=Mock(
            name="sscGetCurrentFbPositionFast",
            return_value=0,
        ),
        sscSetCommandBitSignalEx=Mock(
            name="sscSetCommandBitSignalEx",
            return_value=0,
        ),
        sscGetStatusBitSignalEx=Mock(
            name="sscGetStatusBitSignalEx",
            return_value=0,
        ),
        sscJogStart=Mock(name="sscJogStart", return_value=0),
        sscJogStop=Mock(name="sscJogStop", return_value=0),
        sscIncStart=Mock(name="sscIncStart", return_value=0),
        sscHomeReturnStart=Mock(name="sscHomeReturnStart", return_value=0),
        sscDriveStop=Mock(name="sscDriveStop", return_value=0),
        sscDriveRapidStop=Mock(name="sscDriveRapidStop", return_value=0),
        sscOperationAlarmReset=Mock(name="sscOperationAlarmReset", return_value=0),
        sscServoAlarmReset=Mock(name="sscServoAlarmReset", return_value=0),
    )


class PositionBoardProjectTests(unittest.TestCase):
    def test_ctr_project_selects_pcie_test_mode_board_zero_channel_one(self):
        project_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "CTR.pbp2"
        )

        project = load_mr_mc240n_project(project_path)

        self.assertEqual(project["board_type"], "MR-MC240N")
        self.assertEqual(project["board_id"], 0)
        self.assertEqual(project["channel"], 1)
        self.assertEqual(project["connection"], "PCIe control (API)")
        self.assertEqual(project["tool_mode"], 1)
        self.assertEqual(project["tool_mode_name"], "Test Mode")
        self.assertEqual(project["control_axes"], [1])
        self.assertTrue(project["parameter_file"].endswith("SampleData.prm2"))
        self.assertTrue(os.path.isfile(project["parameter_file"]))
        self.assertTrue(os.path.isfile(project["point_file"]))

        axis_mapping = {}
        with open(project["parameter_file"], encoding="ascii") as parameter_file:
            for line in parameter_file:
                fields = line.strip().split(",")
                if len(fields) == 3 and fields[1].upper() in (
                    "0X0200",
                    "0X0203",
                    "0X0219",
                    "0X0228",
                    "0X0229",
                    "0X022A",
                    "0X022B",
                    "0X0240",
                    "0X1100",
                    "0X1103",
                ):
                    axis = int(fields[0])
                    axis_mapping.setdefault(axis, {})[fields[1].upper()] = int(
                        fields[2], 16
                    )
        self.assertEqual(
            (
                axis_mapping[1]["0X0200"],
                axis_mapping[1]["0X0203"],
            ),
            (1, 1),
        )
        self.assertEqual(
            [
                (
                    axis_mapping[axis]["0X0200"],
                    axis_mapping[axis]["0X0203"],
                )
                for axis in range(2, 7)
            ],
            [(0, 0)] * 5,
        )
        self.assertEqual(
            [axis_mapping[axis]["0X0219"] for axis in range(1, 7)],
            [0x0303] * 6,
        )
        software_upper_limit = (
            axis_mapping[1]["0X0228"]
            | axis_mapping[1]["0X0229"] << 16
        )
        software_lower_limit = (
            axis_mapping[1]["0X022A"]
            | axis_mapping[1]["0X022B"] << 16
        )
        self.assertEqual(software_lower_limit, 0)
        self.assertEqual(software_upper_limit, 196_000)
        # Data-set home: the physical lower endpoint is explicitly made 0 mm.
        self.assertEqual(axis_mapping[1]["0X0240"] & 0xF, 0x2)
        self.assertEqual(axis_mapping[1]["0X1100"], 0x1000)
        # MR-J4 PA04.2=1: disable the amplifier EM2/EM1 forced-stop input.
        self.assertEqual(axis_mapping[1]["0X1103"], 0x2100)
        with open(project["parameter_file"], encoding="ascii") as parameter_file:
            system_parameters = {
                fields[1].upper(): int(fields[2], 16)
                for line in parameter_file
                if len(fields := line.strip().split(",")) == 3
                and fields[0] == "0"
            }
        self.assertEqual(system_parameters["0X000E"], 0x5AE1)


class PositionControllerConstantTests(unittest.TestCase):
    def test_alarm_reset_calls_vendor_operation_and_servo_reset(self):
        library = fake_vendor_library()
        controller = MrMc240nPositionController(board_id=2, axis_number=4)
        controller.library = library
        controller._is_open = True

        status = controller.reset_axis_alarms()

        library.sscOperationAlarmReset.assert_called_once_with(2, 1, 4)
        library.sscServoAlarmReset.assert_called_once_with(2, 1, 4)
        self.assertFalse(status["operation_alarm"])
        self.assertFalse(status["servo_alarm"])

    def test_alarm_reset_is_blocked_while_motion_may_be_active(self):
        library = fake_vendor_library()
        controller = MrMc240nPositionController(board_id=0, axis_number=1)
        controller.library = library
        controller._is_open = True
        controller._motion_command_may_be_active = True

        with self.assertRaisesRegex(RuntimeError, "motion may still be active"):
            controller.reset_axis_alarms()

        library.sscOperationAlarmReset.assert_not_called()
        library.sscServoAlarmReset.assert_not_called()

    def test_host_soft_limit_margin_covers_deceleration_and_poll_latency(self):
        self.assertAlmostEqual(
            calculate_soft_limit_stop_margin_mm(100, 500, 50),
            1.1,
        )
        self.assertAlmostEqual(
            calculate_soft_limit_stop_margin_mm(12_000, 500, 50),
            120.1,
        )

    def test_axis_bit_constants_use_vendor_global_bit_ranges(self):
        expected = {
            "SSC_CMDBIT_SYS_SEMI": 17,
            "SSC_STSBIT_SYS_EMIO": 273,
            "SSC_STSBIT_SYS_TSTO": 275,
            "SSC_STSBIT_SYS_EMID": 279,
            "SSC_CMDBIT_AX_SON": 513,
            "SSC_STSBIT_AX_RDY": 769,
            "SSC_STSBIT_AX_INP": 770,
            "SSC_STSBIT_AX_SALM": 774,
            "SSC_STSBIT_AX_OP": 777,
            "SSC_STSBIT_AX_ZP": 780,
            "SSC_STSBIT_AX_OALM": 782,
            "SSC_STSBIT_AX_OPF": 783,
            "SSC_STSBIT_AX_SO": 788,
            "SSC_STSBIT_AX_DSTO": 791,
            "SSC_STSBIT_AX_ISTP": 801,
            "SSC_STSBIT_AX_STO": 804,
        }

        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(MrMc240nPositionController, name), value)

    def test_system_status_reads_system_bits_with_axis_zero(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=1)
        controller.get_system_status_bit = Mock(
            side_effect=[True, False, True]
        )

        status = controller.read_system_status()

        self.assertEqual(
            controller.get_system_status_bit.call_args_list,
            [call(273), call(275), call(279)],
        )
        self.assertEqual(
            status,
            {
                "forced_stop_active": True,
                "test_mode_active": False,
                "external_forced_stop_disabled": True,
            },
        )

    def test_not_ready_reason_uses_real_vendor_bit_meanings(self):
        reasons = MrMc240nPositionController.axis_not_ready_reasons(
            {
                "servo_ready": False,
                "interlock_stop": True,
                # These operating modes must not be mislabeled as stop inputs.
                "incremental_feed_mode": True,
                "home_reset_mode": True,
                "startup_accepted": True,
            },
            {"forced_stop_active": True},
        )

        self.assertIn("forced stop is active (EMIO=ON)", reasons)
        self.assertIn("interlock stop active (ISTP=ON)", reasons)
        self.assertTrue(any("CN8 STO1/STO2" in reason for reason in reasons))
        self.assertFalse(any("drive stop" in reason for reason in reasons))

    def test_axis_status_reads_vendor_global_status_bit_numbers(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=1)
        controller.get_axis_status_bit = Mock(
            side_effect=[
                True, False, False, True, False, False, True,
                False, False, True, False, False, False,
            ]
        )
        controller.read_feedback_position_counts = Mock(return_value=12_345)

        status = controller.read_axis_status(axis_number=4)

        self.assertEqual(
            controller.get_axis_status_bit.call_args_list,
            [
                call(769, 4),
                call(770, 4),
                call(774, 4),
                call(777, 4),
                call(780, 4),
                call(782, 4),
                call(783, 4),
                call(775, 4),
                call(776, 4),
                call(788, 4),
                call(791, 4),
                call(801, 4),
                call(804, 4),
            ],
        )
        controller.read_feedback_position_counts.assert_called_once_with(4)
        self.assertEqual(status["axis"], 4)
        self.assertEqual(status["position"], 12_345)

    def test_stale_operation_complete_does_not_clear_motion_latch(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=4)
        controller._begin_motion_dispatch(
            start_position=12_345,
            motion_kind="relative",
        )
        controller._confirm_motion_dispatch()
        controller.get_axis_status_bit = Mock(
            side_effect=[
                # Stale pre-command status: OP=0, OPF=1.
                True, False, False, False, False, False, True,
                False, False, True, False, False, False,
                # Command has visibly entered operation.
                True, False, False, True, False, False, False,
                False, False, True, False, False, False,
                # Completion after OP was observed.
                True, True, False, False, False, False, True,
                False, False, True, False, False, False,
            ]
        )
        controller.read_feedback_position_counts = Mock(return_value=12_345)

        controller.read_axis_status()
        self.assertTrue(controller._motion_command_may_be_active)

        controller.read_axis_status()
        self.assertTrue(controller._motion_command_may_be_active)
        self.assertTrue(controller._motion_seen_operating)

        controller.read_axis_status()
        self.assertFalse(controller._motion_command_may_be_active)
        self.assertFalse(controller._motion_seen_operating)

    def test_short_completed_move_can_clear_from_confirmed_position_change(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=4)
        controller._begin_motion_dispatch(
            start_position=12_345,
            motion_kind="relative",
        )
        controller._confirm_motion_dispatch()
        controller.get_axis_status_bit = Mock(
            side_effect=[
                True, True, False, False, False, False, True,
                False, False, True, False, False, False,
            ]
        )
        controller.read_feedback_position_counts = Mock(return_value=12_346)

        controller.read_axis_status()

        self.assertFalse(controller._motion_command_may_be_active)

    def test_data_set_home_can_complete_without_position_change(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=4)
        controller._begin_motion_dispatch(
            start_position=0,
            motion_kind="home",
        )
        controller._confirm_motion_dispatch()
        controller.get_axis_status_bit = Mock(
            side_effect=[
                True, True, False, False, True, False, True,
                False, False, True, False, False, False,
            ]
        )
        controller.read_feedback_position_counts = Mock(return_value=0)

        controller.read_axis_status()

        self.assertFalse(controller._motion_command_may_be_active)

    def test_ambiguous_dispatch_does_not_clear_from_position_change_alone(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=4)
        controller._begin_motion_dispatch(
            start_position=12_345,
            motion_kind="relative",
        )
        controller.get_axis_status_bit = Mock(
            side_effect=[
                True, True, False, False, False, False, True,
                False, False, True, False, False, False,
            ]
        )
        controller.read_feedback_position_counts = Mock(return_value=12_346)

        controller.read_axis_status()

        self.assertTrue(controller._motion_command_may_be_active)


class PositionControllerApiSignatureTests(unittest.TestCase):
    def test_motion_is_not_dispatched_when_axis_alarm_is_active(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=6)
        library = fake_vendor_library()
        controller.library = library
        controller._is_open = True
        controller.get_axis_status_bit = Mock(side_effect=[True, False])

        with self.assertRaisesRegex(RuntimeError, "servo alarm"):
            controller.start_jog(0, 100, 10, 10)

        library.sscJogStart.assert_not_called()
        self.assertFalse(controller._motion_command_may_be_active)

    def test_drive_alarm_error_explains_that_dll_reconnect_will_not_clear_it(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=6)
        library = fake_vendor_library()
        library.sscJogStart.return_value = -1
        library.sscGetLastError.return_value = 0x00060030
        controller.library = library
        controller._is_open = True
        controller.get_axis_status_bit = Mock(side_effect=[False, False])
        controller.read_feedback_position_counts = Mock(return_value=0)

        with self.assertRaisesRegex(RuntimeError, "replacing the DLL will not clear"):
            controller.start_jog(0, 100, 10, 10)

    def test_jog_stop_binds_three_argument_vendor_signature(self):
        controller = MrMc240nPositionController(
            board_id=0,
            axis_number=1,
            dll_path=os.path.abspath("fake_mc2xxstd_x64.dll"),
        )
        library = fake_vendor_library()
        explicit_path = os.path.abspath(controller.dll_path)
        expected_arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"

        with (
            patch.object(
                MrMc240nPositionController,
                "is_supported_platform",
                return_value=True,
            ),
            patch(
                "hardware.os.path.isfile",
                side_effect=lambda path: os.path.abspath(path) == explicit_path,
            ),
            patch(
                "hardware.get_windows_pe_architecture",
                return_value=expected_arch,
            ),
            patch("hardware.ctypes.WinDLL", return_value=library) as load_library,
        ):
            controller._load_library()

        load_library.assert_called_once_with(explicit_path)
        self.assertEqual(
            library.sscJogStop.argtypes,
            [ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self.assertIs(library.sscJogStop.restype, ctypes.c_int)

    def test_stop_jog_passes_only_board_channel_and_axis(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=6)
        library = fake_vendor_library()
        controller.library = library
        controller._is_open = True
        controller._jog_active = True

        controller.stop_jog()

        library.sscJogStop.assert_called_once_with(2, 1, 6)
        self.assertFalse(controller._jog_active)

    def test_normal_stop_routes_active_jog_to_jog_stop(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=6)
        library = fake_vendor_library()
        controller.library = library
        controller._is_open = True
        controller._jog_active = True
        controller._motion_kind = "jog"

        result = controller.stop(rapid=False)

        library.sscJogStop.assert_called_once_with(2, 1, 6)
        library.sscDriveStop.assert_not_called()
        self.assertEqual(result["mode"], "jog stop")
        self.assertFalse(result["escalated"])

    def test_all_axis_rapid_stop_engages_and_confirms_semi_first(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=6)
        library = fake_vendor_library()

        def write_system_bits(
            _board_id, _channel, _axis, bit_number, status_pointer
        ):
            status_pointer._obj.value = int(
                bit_number == controller.SSC_STSBIT_SYS_EMIO
            )
            return 0

        library.sscGetStatusBitSignalEx.side_effect = write_system_bits
        controller.library = library
        controller._is_open = True
        controller._jog_active = True
        controller._motion_command_may_be_active = True

        result = controller.stop_all_axes()

        library.sscSetCommandBitSignalEx.assert_called_once_with(
            2, 1, 0, 17, 1
        )
        library.sscDriveRapidStop.assert_not_called()
        self.assertEqual(result["mode"], "software forced stop")
        self.assertFalse(controller._jog_active)
        self.assertFalse(controller._motion_command_may_be_active)

    def test_failed_motion_dispatch_remains_latched_until_stop_succeeds(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=6)
        library = fake_vendor_library()
        library.sscJogStart.return_value = -1
        library.sscGetLastError.return_value = 0x00010000
        controller.library = library
        controller._is_open = True
        controller.read_feedback_position_counts = Mock(return_value=0)

        with self.assertRaisesRegex(RuntimeError, "sscJogStart"):
            controller.start_jog(0, 100, 10, 10)

        self.assertTrue(controller._motion_command_may_be_active)
        controller.stop(rapid=True)
        self.assertFalse(controller._motion_command_may_be_active)

    def test_stop_all_axes_uses_vendor_abi_and_attempts_every_axis(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=6)
        library = fake_vendor_library()

        def stop_axis(_board_id, _channel, axis_number, _timeout_ms):
            return -1 if axis_number in (2, 5) else 0

        library.sscDriveRapidStop.side_effect = stop_axis
        library.sscGetLastError.return_value = 0x00010000
        controller.library = library
        controller._is_open = True
        controller._jog_active = True
        controller._motion_command_may_be_active = True

        with self.assertRaises(RuntimeError) as raised:
            controller.stop_all_axes()

        self.assertEqual(
            library.sscDriveRapidStop.call_args_list,
            [call(2, 1, axis, 3000) for axis in range(1, 7)],
        )
        self.assertIn("axis 2", str(raised.exception))
        self.assertIn("axis 5", str(raised.exception))
        self.assertFalse(controller._jog_active)
        self.assertFalse(controller._motion_command_may_be_active)


class UsbControllerStopTests(unittest.TestCase):
    def test_stop_all_axes_attempts_every_axis_before_clearing_latches(self):
        controller = MrMc240nUsbController(board_id=0, axis_number=1)
        dispatched = []

        def dispatch(command):
            self.assertTrue(controller._jog_active)
            self.assertTrue(controller._motion_command_may_be_active)
            dispatched.append(command)
            if command in ("RAPID_STOP 2", "RAPID_STOP 5"):
                raise RuntimeError(f"simulated failure for {command}")
            return {"ok": True}

        controller._request = Mock(side_effect=dispatch)
        controller._jog_active = True
        controller._motion_command_may_be_active = True

        with self.assertRaises(RuntimeError) as raised:
            controller.stop_all_axes()

        self.assertEqual(
            dispatched,
            [f"RAPID_STOP {axis}" for axis in range(1, 7)],
        )
        self.assertIn("axis 2", str(raised.exception))
        self.assertIn("axis 5", str(raised.exception))
        self.assertFalse(controller._jog_active)
        self.assertFalse(controller._motion_command_may_be_active)


class PositionControllerOpenTests(unittest.TestCase):
    def test_axis_unmounted_status_has_specific_recovery_instructions(self):
        controller = MrMc240nPositionController(
            board_id=1,
            axis_number=2,
            auto_start_system=True,
        )
        library = fake_vendor_library()

        def write_axis_unmounted(_board_id, _channel, status_pointer):
            status_pointer._obj.value = -7168  # signed c_short for 0xE400
            return 0

        library.sscGetSystemStatusCode.side_effect = write_axis_unmounted
        controller.library = library

        with self.assertRaisesRegex(RuntimeError, "AXIS UNMOUNTED"):
            controller.open()

        library.sscSystemStart.assert_not_called()

    def test_servo_on_is_blocked_until_system_is_running(self):
        controller = MrMc240nPositionController(
            board_id=1,
            axis_number=2,
            auto_start_system=False,
        )
        library = fake_vendor_library()
        controller.library = library
        controller._is_open = True

        with self.assertRaisesRegex(RuntimeError, "prepared but not running"):
            controller.set_servo_on(True)

        library.sscSetCommandBitSignalEx.assert_not_called()

    def test_servo_on_is_sent_when_system_is_running(self):
        controller = MrMc240nPositionController(board_id=1, axis_number=2)
        library = fake_vendor_library()

        def write_running_status(_board_id, _channel, status_pointer):
            status_pointer._obj.value = controller.SSC_STS_CODE_RUNNING
            return 0

        library.sscGetSystemStatusCode.side_effect = write_running_status
        controller.library = library
        controller._is_open = True

        controller.set_servo_on(True)

        self.assertEqual(
            library.sscSetCommandBitSignalEx.call_args_list,
            [
                call(1, 1, 0, 17, 0),
                call(1, 1, 2, 513, 1),
            ],
        )

    def test_servo_on_is_blocked_when_emio_remains_after_semi_release(self):
        controller = MrMc240nPositionController(board_id=1, axis_number=2)
        library = fake_vendor_library()

        def write_running_status(_board_id, _channel, status_pointer):
            status_pointer._obj.value = controller.SSC_STS_CODE_RUNNING
            return 0

        def write_system_bits(
            _board_id, _channel, _axis, bit_number, status_pointer
        ):
            status_pointer._obj.value = int(
                bit_number == controller.SSC_STSBIT_SYS_EMIO
            )
            return 0

        library.sscGetSystemStatusCode.side_effect = write_running_status
        library.sscGetStatusBitSignalEx.side_effect = write_system_bits
        controller.library = library
        controller._is_open = True

        with self.assertRaisesRegex(RuntimeError, "EMID=OFF"):
            controller.set_servo_on(True)

        library.sscSetCommandBitSignalEx.assert_called_once_with(
            1, 1, 0, 17, 0
        )

    def test_apply_parameter_file_uses_vendor_reset_write_start_flow(self):
        controller = MrMc240nPositionController(board_id=1, axis_number=2)
        library = fake_vendor_library()

        def write_applied_system_bits(
            _board_id, _channel, _axis, bit_number, status_pointer
        ):
            status_pointer._obj.value = int(
                bit_number == controller.SSC_STSBIT_SYS_EMID
            )
            return 0

        system_codes = iter(
            [controller.SSC_STS_CODE_READY_FIN, controller.SSC_STS_CODE_RUNNING]
        )

        def write_project_start_status(_board_id, _channel, status_pointer):
            status_pointer._obj.value = next(system_codes)
            return 0

        library.sscGetSystemStatusCode.side_effect = write_project_start_status
        library.sscGetStatusBitSignalEx.side_effect = write_applied_system_bits
        controller.library = library
        parameter_groups = {
            0: [(0x000E, 0x5AE1)],
            1: [(0x0200, 1), (0x0203, 1), (0x1103, 0x2100)],
        }

        with patch(
            "hardware.load_mr_mc240n_parameter_file",
            return_value=parameter_groups,
        ):
            result = controller.apply_parameter_file_and_start("CTR.prm2")

        library.sscReboot.assert_not_called()
        library.sscResetAllParameter.assert_called_once_with(1, 1, 0)
        library.sscChangeParameterEx.assert_called_once_with(
            1, 1, 0, 0x000E, 0x5AE1
        )
        self.assertEqual(library.sscChange2ParameterEx.call_count, 2)
        library.sscSystemStart.assert_called_once_with(1, 1, 0)
        self.assertEqual(result["parameter_count"], 4)
        self.assertEqual(result["target_count"], 2)
        self.assertEqual(
            result["system_status"], controller.SSC_STS_CODE_RUNNING
        )

    def test_open_auto_starts_system_once_when_requested(self):
        controller = MrMc240nPositionController(
            board_id=3,
            axis_number=2,
            auto_start_system=True,
        )
        library = fake_vendor_library()
        controller.library = library

        controller.open()
        controller.open()

        library.sscOpen.assert_called_once_with(3)
        library.sscSystemStart.assert_called_once_with(3, 1, 0)
        self.assertTrue(controller._is_open)
        self.assertTrue(controller._system_start_attempted)

    def test_open_does_not_start_system_without_opt_in(self):
        controller = MrMc240nPositionController(
            board_id=1,
            axis_number=2,
            auto_start_system=False,
        )
        library = fake_vendor_library()
        controller.library = library

        controller.open()

        library.sscOpen.assert_called_once_with(1)
        library.sscSystemStart.assert_not_called()
        library.sscGetSystemStatusCode.assert_not_called()
        self.assertTrue(controller._is_open)
        self.assertFalse(controller._system_start_attempted)

    def test_open_does_not_restart_an_already_running_system(self):
        controller = MrMc240nPositionController(
            board_id=1,
            axis_number=2,
            auto_start_system=True,
        )
        library = fake_vendor_library()

        def write_running_status(_board_id, _channel, status_pointer):
            status_pointer._obj.value = (
                MrMc240nPositionController.SSC_STS_CODE_RUNNING
            )
            return 0

        library.sscGetSystemStatusCode.side_effect = write_running_status
        controller.library = library

        controller.open()

        library.sscSystemStart.assert_not_called()
        self.assertEqual(
            controller.system_status_code,
            MrMc240nPositionController.SSC_STS_CODE_RUNNING,
        )
        self.assertTrue(controller._is_open)

    def test_auto_start_failure_closes_the_board_and_clears_open_state(self):
        controller = MrMc240nPositionController(
            board_id=1,
            axis_number=2,
            auto_start_system=True,
        )
        library = fake_vendor_library()
        library.sscSystemStart.return_value = -1
        library.sscGetLastError.return_value = 0x00030000
        controller.library = library

        with self.assertRaisesRegex(RuntimeError, "sscSystemStart"):
            controller.open()

        library.sscClose.assert_called_once_with(1)
        self.assertFalse(controller._is_open)
        self.assertFalse(controller._system_start_attempted)

    def test_auto_start_cleanup_failure_latches_controller_until_close(self):
        controller = MrMc240nPositionController(
            board_id=1,
            axis_number=2,
            auto_start_system=True,
        )
        library = fake_vendor_library()
        library.sscSystemStart.return_value = -1
        library.sscGetLastError.return_value = 0x00030000
        library.sscClose.return_value = -1
        controller.library = library

        with self.assertRaisesRegex(RuntimeError, "Cleanup also failed"):
            controller.open()

        self.assertTrue(controller._is_open)
        self.assertTrue(controller._open_cleanup_pending)
        with self.assertRaisesRegex(RuntimeError, "Cleanup also failed"):
            controller.read_feedback_position_counts()

        library.sscClose.return_value = 0
        controller.close()
        self.assertFalse(controller._is_open)
        self.assertFalse(controller._open_cleanup_pending)


class PositionControllerExplicitDllTests(unittest.TestCase):
    def test_registered_runtime_excludes_project_dll_fallbacks(self):
        controller = MrMc240nPositionController(board_id=0, axis_number=1)
        registered_directory = os.path.abspath("registered_api_library")

        with patch(
            "hardware.get_position_board_api_library_directory",
            return_value=registered_directory,
        ):
            candidates = controller._library_candidates()

        self.assertTrue(candidates)
        self.assertTrue(
            all(
                os.path.commonpath([registered_directory, candidate])
                == registered_directory
                for candidate in candidates
            )
        )

    def test_explicit_dll_load_failure_does_not_try_fallback_candidates(self):
        explicit_path = os.path.abspath("broken_explicit_mc2xxstd_x64.dll")
        controller = MrMc240nPositionController(
            board_id=0,
            axis_number=1,
            dll_path=explicit_path,
        )
        expected_arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"

        with (
            patch.object(
                MrMc240nPositionController,
                "is_supported_platform",
                return_value=True,
            ),
            patch(
                "hardware.os.path.isfile",
                side_effect=lambda path: os.path.abspath(path) == explicit_path,
            ),
            patch(
                "hardware.get_windows_pe_architecture",
                return_value=expected_arch,
            ),
            patch(
                "hardware.ctypes.WinDLL",
                side_effect=OSError("simulated explicit DLL load failure"),
            ) as load_library,
        ):
            with self.assertRaises(RuntimeError):
                controller._load_library()

        load_library.assert_called_once_with(explicit_path)
        self.assertIsNone(controller.library)

    def test_loaded_dll_missing_required_export_fails_without_fallback(self):
        explicit_path = os.path.abspath("incomplete_mc2xxstd_x64.dll")
        controller = MrMc240nPositionController(
            board_id=0,
            axis_number=1,
            dll_path=explicit_path,
        )
        library = fake_vendor_library()
        delattr(library, "sscJogStop")
        expected_arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"

        with (
            patch.object(
                MrMc240nPositionController,
                "is_supported_platform",
                return_value=True,
            ),
            patch("hardware.os.path.isfile", return_value=True),
            patch(
                "hardware.get_windows_pe_architecture",
                return_value=expected_arch,
            ),
            patch("hardware.ctypes.WinDLL", return_value=library) as load_library,
        ):
            with self.assertRaisesRegex(RuntimeError, "sscJogStop"):
                controller._load_library()
            with self.assertRaisesRegex(RuntimeError, "sscJogStop"):
                controller._load_library()

        load_library.assert_called_once_with(explicit_path)

    def test_explicit_architecture_mismatch_does_not_try_fallback_candidates(self):
        explicit_path = os.path.abspath("wrong_arch_mc2xxstd.dll")
        controller = MrMc240nPositionController(
            board_id=0,
            axis_number=1,
            dll_path=explicit_path,
        )
        wrong_arch = "x86" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x64"

        with (
            patch.object(
                MrMc240nPositionController,
                "is_supported_platform",
                return_value=True,
            ),
            patch(
                "hardware.os.path.isfile",
                side_effect=lambda path: os.path.abspath(path) == explicit_path,
            ),
            patch(
                "hardware.get_windows_pe_architecture",
                return_value=wrong_arch,
            ),
            patch("hardware.ctypes.WinDLL") as load_library,
        ):
            with self.assertRaisesRegex(RuntimeError, "Architecture mismatch"):
                controller._load_library()

        load_library.assert_not_called()
        self.assertIsNone(controller.library)


class ApiErrorFormattingTests(unittest.TestCase):
    def test_signed_ctypes_error_values_are_rendered_as_unsigned_32_bit_hex(self):
        cases = (
            (-1, "0xFFFFFFFF"),
            (-2_147_483_648, "0x80000000"),
        )

        for signed_value, expected_hex in cases:
            with self.subTest(signed_value=signed_value):
                rendered = describe_mr_mc240n_api_error(signed_value)
                self.assertTrue(rendered.startswith(f"{expected_hex}: "), rendered)
                self.assertNotIn("0x-", rendered)

    def test_known_positive_error_format_is_unchanged(self):
        self.assertEqual(
            describe_mr_mc240n_api_error(0x00021010),
            "0x00021010: position board not found "
            "(SSC_FUNC_ERR_NOT_FOUND_BOARD)",
        )


if __name__ == "__main__":
    unittest.main()
