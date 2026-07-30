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
from hardware import MrMc240nPositionController, describe_mr_mc240n_api_error


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
    )


class PositionControllerConstantTests(unittest.TestCase):
    def test_axis_bit_constants_use_vendor_global_bit_ranges(self):
        expected = {
            "SSC_CMDBIT_AX_SON": 513,
            "SSC_STSBIT_AX_RDY": 769,
            "SSC_STSBIT_AX_INP": 770,
            "SSC_STSBIT_AX_SALM": 774,
            "SSC_STSBIT_AX_OP": 777,
            "SSC_STSBIT_AX_ZP": 780,
            "SSC_STSBIT_AX_OALM": 782,
            "SSC_STSBIT_AX_OPF": 783,
        }

        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(MrMc240nPositionController, name), value)

    def test_axis_status_reads_vendor_global_status_bit_numbers(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=1)
        controller.get_axis_status_bit = Mock(
            side_effect=[True, False, False, True, False, False, True]
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
                # Command has visibly entered operation.
                True, False, False, True, False, False, False,
                # Completion after OP was observed.
                True, True, False, False, False, False, True,
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
            side_effect=[True, True, False, False, False, False, True]
        )
        controller.read_feedback_position_counts = Mock(return_value=12_346)

        controller.read_axis_status()

        self.assertFalse(controller._motion_command_may_be_active)

    def test_ambiguous_dispatch_does_not_clear_from_position_change_alone(self):
        controller = MrMc240nPositionController(board_id=2, axis_number=4)
        controller._begin_motion_dispatch(
            start_position=12_345,
            motion_kind="relative",
        )
        controller.get_axis_status_bit = Mock(
            side_effect=[True, True, False, False, False, False, True]
        )
        controller.read_feedback_position_counts = Mock(return_value=12_346)

        controller.read_axis_status()

        self.assertTrue(controller._motion_command_may_be_active)


class PositionControllerApiSignatureTests(unittest.TestCase):
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


class PositionControllerOpenTests(unittest.TestCase):
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
